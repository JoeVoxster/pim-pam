from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Category,
    ChannelCategory,
    MedusaConnectionConfig,
    MedusaSyncMapping,
    MedusaSyncRun,
    MedusaSyncRunItem,
    Product,
    ProductCategoryAssignment,
    ProductCategoryMapping,
    ProductTranslation,
    ProductVariant,
    VariantTechnicalAttribute,
    VariantTranslation,
)
from app.services.medusa.client import MedusaAdminApiClient, MedusaApiError, MedusaAuthError
from app.services.medusa.category_position_service import (
    POSITION_ENTITY_TYPE,
    build_medusa_category_product_position_payload,
    build_medusa_internal_category_product_position_payload,
)
from app.services.medusa.config_service import get_or_create_medusa_connection, mark_connection_test_result
from app.services.medusa.mappers import (
    MedusaAssetMapper,
    MedusaCategoryMapper,
    MedusaPricingMapper,
    MedusaProductMapper,
    MedusaTranslationMapper,
    MedusaVariantMapper,
    stable_hash,
)
from app.services.r2_config_service import get_r2_public_base_url


ClientFactory = Callable[[MedusaConnectionConfig], MedusaAdminApiClient]


class MedusaSyncService:
    def __init__(self, session: Session, *, client_factory: ClientFactory | None = None) -> None:
        self.session = session
        self.client_factory = client_factory or (lambda config: MedusaAdminApiClient(config))

    def test_connection(self, connection_name: str = "default") -> dict[str, Any]:
        config = get_or_create_medusa_connection(self.session, connection_name)
        run = self._start_run(config, "test_connection", {"connection": connection_name})
        try:
            response = self.client_factory(config).test_connection()
            mark_connection_test_result(config, ok=True)
            self._add_item(run, "connection", config.id, "test_connection", "success", response_payload=response)
            self._finish_run(run, "success", {"message": response.get("message")})
            return {"status": "success", "run_id": run.id, "message": response.get("message")}
        except MedusaAuthError as exc:
            mark_connection_test_result(config, ok=False, error=str(exc))
            self._add_item(run, "connection", config.id, "error", "auth_error", error_message=str(exc))
            self._finish_run(run, "failed", {"error": str(exc)})
            return {"status": "failed", "run_id": run.id, "message": str(exc)}
        except Exception as exc:
            mark_connection_test_result(config, ok=False, error=str(exc))
            self._add_item(run, "connection", config.id, "error", "error", error_message=str(exc))
            self._finish_run(run, "failed", {"error": str(exc)})
            return {"status": "failed", "run_id": run.id, "message": str(exc)}

    def dry_run_product(self, product_id: int, connection_name: str = "default", *, force: bool = False) -> dict[str, Any]:
        return self.export_product(product_id, connection_name=connection_name, dry_run=True, force=force)

    def resolve_product_ids(
        self,
        *,
        selection_mode: str,
        product_id: int | None = None,
        selected_product_ids: list[int] | None = None,
        connection_name: str = "default",
        limit: int | None = None,
    ) -> list[int]:
        mode = selection_mode or "single"
        max_items = max(1, int(limit or 20))
        if mode == "single":
            return [int(product_id)] if product_id else []
        if mode == "selected":
            return _unique_ints(selected_product_ids or [])
        if mode == "all_active":
            return list(
                self.session.scalars(
                    select(Product.id)
                    .where(Product.status != "archived")
                    .order_by(Product.id.asc())
                    .limit(max_items)
                )
            )
        if mode == "without_mapping":
            config = get_or_create_medusa_connection(self.session, connection_name)
            mapped_ids = (
                select(MedusaSyncMapping.local_entity_id)
                .where(
                    MedusaSyncMapping.connection_id == config.id,
                    MedusaSyncMapping.entity_type == "product",
                    MedusaSyncMapping.medusa_id.is_not(None),
                    MedusaSyncMapping.status == "active",
                )
                .subquery()
            )
            return list(
                self.session.scalars(
                    select(Product.id)
                    .where(Product.status != "archived")
                    .where(Product.id.not_in(select(mapped_ids.c.local_entity_id)))
                    .order_by(Product.id.asc())
                    .limit(max_items)
                )
            )
        return []

    def export_products(
        self,
        product_ids: list[int],
        connection_name: str = "default",
        *,
        dry_run: bool,
        force: bool = False,
    ) -> dict[str, Any]:
        ids = _unique_ints(product_ids)
        if not ids:
            return {"status": "failed", "message": "Keine Produkte ausgewählt.", "products": []}
        results = [
            self.export_product(product_id, connection_name=connection_name, dry_run=dry_run, force=force)
            for product_id in ids
        ]
        statuses = [str(result.get("status") or "failed") for result in results]
        if all(status == "success" for status in statuses):
            status = "success"
        elif any(status in {"success", "partial_success"} for status in statuses):
            status = "partial_success"
        else:
            status = "failed"
        return {
            "status": status,
            "product_count": len(ids),
            "product_ids": ids,
            "products": results,
            "run_ids": [result.get("run_id") for result in results if result.get("run_id")],
        }

    def export_product(
        self,
        product_id: int,
        connection_name: str = "default",
        *,
        dry_run: bool | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        config = get_or_create_medusa_connection(self.session, connection_name)
        dry_run = bool(config.dry_run_default if dry_run is None else dry_run)
        run = self._start_run(config, "dry_run" if dry_run else "export", {"product_id": product_id, "force": force})
        product = self._load_product(product_id)
        if product is None:
            self._add_item(run, "product", product_id, "error", "error", error_message="Produkt nicht gefunden")
            self._finish_run(run, "failed", {"error": "Produkt nicht gefunden"})
            return {"status": "failed", "run_id": run.id, "message": "Produkt nicht gefunden"}

        client = self.client_factory(config)
        mapper = MedusaProductMapper(config)
        variant_mapper = MedusaVariantMapper(config)
        asset_mapper = MedusaAssetMapper(config.public_asset_base_url or get_r2_public_base_url(self.session))
        translation_mapper = MedusaTranslationMapper()
        pricing_mapper = MedusaPricingMapper()
        default_translation = _translation_for(product, config.default_locale)
        product_payload = mapper.map_product(product, translation=default_translation, image_urls=asset_mapper.image_urls(product))
        self._attach_product_categories(config, product, product_payload.payload)
        product_payload = type(product_payload)(payload=product_payload.payload, hash=stable_hash(product_payload.payload), locale=product_payload.locale)
        product_mapping = self._mapping(config, "product", product.id)
        product_action = self._planned_action(product_mapping, product_payload.hash, force=force)

        medusa_product_id = product_mapping.medusa_id if product_mapping else None
        if dry_run:
            self._add_item(run, "product", product.id, f"would_{product_action}", "planned", request_payload=product_payload.payload, diff={"hash": product_payload.hash})
        else:
            try:
                medusa_product_id, product_action = self._upsert_product(client, config, product, product_payload, product_mapping, force=force)
                self._add_item(run, "product", product.id, product_action, "success", medusa_id=medusa_product_id, request_payload=product_payload.payload)
            except MedusaAuthError:
                raise
            except Exception as exc:
                self._add_item(run, "product", product.id, "error", "error", request_payload=product_payload.payload, error_message=str(exc))
                self._finish_run(run, "partial_success", self._summary(run))
                return {"status": "partial_success", "run_id": run.id, "message": str(exc)}

        if medusa_product_id or dry_run:
            self._sync_variants(
                run,
                client,
                config,
                product,
                variant_mapper,
                pricing_mapper,
                dry_run=dry_run,
                force=force,
                medusa_product_id=medusa_product_id,
            )
            self._sync_translations(run, client, config, product, translation_mapper, dry_run=dry_run)

        status = "success" if not any(item.status in {"error", "validation_error"} for item in run.items) else "partial_success"
        self._finish_run(run, status, self._summary(run))
        return {"status": status, "run_id": run.id, "summary": run.summary}

    def repair_mapping(self, connection_name: str = "default") -> dict[str, Any]:
        config = get_or_create_medusa_connection(self.session, connection_name)
        run = self._start_run(config, "mapping_repair", {"connection": connection_name})
        client = self.client_factory(config)
        try:
            products = client.list_products_for_mapping_repair()
            mapped = 0
            for medusa_product in products:
                metadata = medusa_product.get("metadata") or {}
                local_id = _safe_int(metadata.get("pim_product_id"))
                if local_id is None:
                    handle = medusa_product.get("handle")
                    if handle:
                        product = self.session.scalar(select(Product).where(Product.handle == handle))
                        local_id = product.id if product else None
                if local_id is None:
                    self._add_item(run, "product", None, "conflict", "orphaned_in_medusa", medusa_id=medusa_product.get("id"), response_payload=medusa_product)
                    continue
                mapping = self._get_or_create_mapping(config, "product", local_id)
                mapping.medusa_id = medusa_product.get("id")
                mapping.medusa_handle = medusa_product.get("handle")
                mapping.medusa_external_id = medusa_product.get("external_id")
                mapping.last_seen_in_medusa_at = datetime.now(timezone.utc)
                mapping.status = "active"
                mapped += 1
                self._add_item(run, "product", local_id, "map_existing", "success", medusa_id=mapping.medusa_id)
                for variant in medusa_product.get("variants") or client.list_variants_for_mapping_repair(str(mapping.medusa_id)):
                    variant_metadata = variant.get("metadata") or {}
                    local_variant_id = _safe_int(variant_metadata.get("pim_variant_id"))
                    if local_variant_id is None and variant.get("sku"):
                        local_variant = self.session.scalar(select(ProductVariant).where(ProductVariant.sku == variant.get("sku")))
                        local_variant_id = local_variant.id if local_variant else None
                    if local_variant_id:
                        variant_mapping = self._get_or_create_mapping(config, "variant", local_variant_id, local_parent_id=local_id)
                        variant_mapping.medusa_id = variant.get("id")
                        variant_mapping.medusa_parent_id = mapping.medusa_id
                        variant_mapping.medusa_sku = variant.get("sku")
                        variant_mapping.last_seen_in_medusa_at = datetime.now(timezone.utc)
                        variant_mapping.status = "active"
                        self._add_item(run, "variant", local_variant_id, "map_existing", "success", medusa_id=variant_mapping.medusa_id)
            self._finish_run(run, "success", {"mapped_products": mapped, "seen_products": len(products)})
            return {"status": "success", "run_id": run.id, "summary": run.summary}
        except Exception as exc:
            self._add_item(run, "mapping", None, "error", "error", error_message=str(exc))
            self._finish_run(run, "failed", {"error": str(exc)})
            return {"status": "failed", "run_id": run.id, "message": str(exc)}

    def export_channel_categories(
        self,
        *,
        sales_channel_id: int | None = None,
        channel_category_ids: list[int] | None = None,
        connection_name: str = "default",
        dry_run: bool,
        limit: int | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        config = get_or_create_medusa_connection(self.session, connection_name)
        category_ids = _unique_ints(channel_category_ids)
        if not category_ids:
            category_ids = self.resolve_pim_category_ids(sales_channel_id=sales_channel_id, limit=limit)
        if not category_ids:
            return {"status": "failed", "message": "Keine Kanal-Kategorien für Medusa-Export ausgewählt.", "category_count": 0}
        run = self._start_run(
            config,
            "category_dry_run" if dry_run else "category_export",
            {"channel_category_ids": category_ids, "sales_channel_id": sales_channel_id, "force": force},
        )
        client = self.client_factory(config)
        mapper = MedusaCategoryMapper()
        for category_id in self._sort_pim_category_ids_for_export(category_ids):
            category = self.session.get(Category, category_id)
            if category is None:
                self._add_item(run, "category", category_id, "error", "validation_error", error_message="Kategorie nicht gefunden.")
                continue
            mapping = self._mapping(config, "category", category.id)
            parent_mapping = self._parent_pim_category_mapping(config, category)
            category_payload = mapper.map_category(
                category,
                medusa_id=mapping.medusa_id if mapping else None,
                parent_category_id=parent_mapping.medusa_id if parent_mapping else None,
            )
            action = self._planned_action(mapping, category_payload.hash, force=force)
            if dry_run:
                self._add_item(run, "category", category.id, f"would_{action}", "planned", medusa_id=mapping.medusa_id if mapping else None, request_payload=category_payload.payload, diff={"hash": category_payload.hash})
                continue
            try:
                response = client.create_or_update_category(dict(category_payload.payload))
                medusa_category = response.get("product_category", response.get("category", response)) if isinstance(response, dict) else {}
                medusa_id = str(medusa_category.get("id") or category_payload.payload.get("id") or "")
                if not medusa_id:
                    raise MedusaApiError("Medusa-Antwort enthält keine Product Category ID.", response_payload=response)
                mapping = self._get_or_create_mapping(config, "category", category.id)
                mapping.medusa_id = medusa_id
                mapping.medusa_handle = category_payload.handle
                mapping.medusa_external_id = category.slug
                mapping.local_hash = category_payload.hash
                mapping.last_synced_at = datetime.now(timezone.utc)
                mapping.status = "active"
                self._add_item(run, "category", category.id, action, "success", medusa_id=medusa_id, request_payload=category_payload.payload, response_payload=response)
            except Exception as exc:
                self._add_item(run, "category", category.id, "error", "error", request_payload=category_payload.payload, error_message=str(exc))
        status = "success" if not any(item.status in {"error", "validation_error"} for item in run.items) else "partial_success"
        if all(item.status in {"error", "validation_error"} for item in run.items):
            status = "failed"
        self._finish_run(run, status, self._summary(run))
        return {"status": status, "run_id": run.id, "category_count": len(category_ids), "summary": run.summary}

    def resolve_channel_category_ids(
        self,
        *,
        channel_category_id: int | None = None,
        sales_channel_id: int | None = None,
        limit: int | None = None,
    ) -> list[int]:
        max_items = max(1, int(limit or 200))
        if channel_category_id:
            return [int(channel_category_id)]
        stmt = select(ChannelCategory.id).where(ChannelCategory.is_active.is_(True)).order_by(ChannelCategory.id.asc()).limit(max_items)
        if sales_channel_id:
            stmt = stmt.where(ChannelCategory.sales_channel_id == int(sales_channel_id))
        return list(self.session.scalars(stmt))

    def resolve_pim_category_ids(
        self,
        *,
        category_id: int | None = None,
        sales_channel_id: int | None = None,
        limit: int | None = None,
    ) -> list[int]:
        max_items = max(1, int(limit or 200))
        if category_id:
            return [int(category_id)]
        stmt = select(Category.id).order_by(Category.sort_order.asc(), Category.name.asc(), Category.id.asc()).limit(max_items)
        if sales_channel_id:
            stmt = stmt.where(Category.sales_channel_id == int(sales_channel_id))
        return list(self.session.scalars(stmt))

    def _sort_channel_category_ids_for_export(self, category_ids: list[int]) -> list[int]:
        rows = list(self.session.scalars(select(ChannelCategory).where(ChannelCategory.id.in_(category_ids))).unique())
        rows.sort(key=lambda row: (_path_depth(row.external_path), row.external_path or "", row.name or "", row.id))
        return [row.id for row in rows]

    def _sort_pim_category_ids_for_export(self, category_ids: list[int]) -> list[int]:
        rows = list(self.session.scalars(select(Category).where(Category.id.in_(category_ids))).unique())
        by_id = {row.id: row for row in rows}
        depth_cache: dict[int, int] = {}

        def depth(row: Category) -> int:
            if row.id in depth_cache:
                return depth_cache[row.id]
            parent = by_id.get(row.parent_id) if row.parent_id else None
            value = 1 + depth(parent) if parent else 0
            depth_cache[row.id] = value
            return value

        rows.sort(key=lambda row: (depth(row), row.sort_order, row.name or "", row.id))
        return [row.id for row in rows]

    def _parent_category_mapping(self, config: MedusaConnectionConfig, category: ChannelCategory) -> MedusaSyncMapping | None:
        parent_path = _parent_path(category.external_path)
        if not parent_path:
            return None
        parent = self.session.scalar(
            select(ChannelCategory).where(
                ChannelCategory.sales_channel_id == category.sales_channel_id,
                ChannelCategory.external_path == parent_path,
            )
        )
        if parent is None:
            return None
        return self._mapping(config, "channel_category", parent.id)

    def _parent_pim_category_mapping(self, config: MedusaConnectionConfig, category: Category) -> MedusaSyncMapping | None:
        if not category.parent_id:
            return None
        return self._mapping(config, "category", category.parent_id)

    def _attach_product_categories(self, config: MedusaConnectionConfig, product: Product, payload: dict[str, Any]) -> None:
        categories: list[dict[str, str]] = []
        metadata_categories: list[dict[str, Any]] = []
        seen: set[str] = set()
        for mapping in sorted(product.channel_category_mappings or [], key=lambda row: (row.sales_channel_id, row.position, row.channel_category_id)):
            category_mapping = self._mapping(config, "channel_category", mapping.channel_category_id)
            medusa_id = category_mapping.medusa_id if category_mapping else None
            if medusa_id and medusa_id not in seen:
                categories.append({"id": medusa_id})
                seen.add(medusa_id)
            metadata_categories.append(
                {
                    "pimpam_category_id": str(mapping.channel_category_id),
                    "medusa_category_id": medusa_id,
                    "sales_channel_id": mapping.sales_channel_id,
                    "position": mapping.position if mapping.position is not None else 9999,
                }
            )
        if categories:
            payload["categories"] = categories
        if metadata_categories:
            metadata = dict(payload.get("metadata") or {})
            metadata["pim_category_mappings"] = metadata_categories
            payload["metadata"] = metadata
        internal_categories: list[dict[str, Any]] = []
        for assignment in sorted(product.category_links or [], key=lambda row: (row.sales_channel_id, row.sort_order, row.category_id)):
            category_mapping = self._mapping(config, "category", assignment.category_id)
            medusa_id = category_mapping.medusa_id if category_mapping else None
            if medusa_id and medusa_id not in seen:
                categories.append({"id": medusa_id})
                seen.add(medusa_id)
            internal_categories.append(
                {
                    "pimpam_category_id": str(assignment.category_id),
                    "medusa_category_id": medusa_id,
                    "sales_channel_id": assignment.sales_channel_id,
                    "position": assignment.sort_order if assignment.sort_order is not None else 9999,
                }
            )
        if categories:
            payload["categories"] = categories
        if internal_categories:
            metadata = dict(payload.get("metadata") or {})
            metadata["pim_category_mappings"] = internal_categories
            payload["metadata"] = metadata

    def sync_category_product_positions(
        self,
        *,
        channel_category_ids: list[int],
        connection_name: str = "default",
        dry_run: bool,
        use_sales_channel: bool = True,
    ) -> dict[str, Any]:
        ids = _unique_ints(channel_category_ids)
        if not ids:
            return {"status": "failed", "message": "Keine Kanal-Kategorien für Positionssync ausgewählt.", "category_count": 0}
        config = get_or_create_medusa_connection(self.session, connection_name)
        run = self._start_run(
            config,
            "category_position_dry_run" if dry_run else "category_position_sync",
            {"channel_category_ids": ids, "use_sales_channel": use_sales_channel},
        )
        client = self.client_factory(config)
        for category_id in ids:
            try:
                if self.session.get(Category, category_id) is not None:
                    payload = build_medusa_internal_category_product_position_payload(
                        self.session,
                        config,
                        category_id=category_id,
                        use_sales_channel=use_sales_channel,
                    )
                else:
                    payload = build_medusa_category_product_position_payload(
                        self.session,
                        config,
                        channel_category_id=category_id,
                        use_sales_channel=use_sales_channel,
                    )
                if dry_run:
                    self._add_item(
                        run,
                        POSITION_ENTITY_TYPE,
                        category_id,
                        "would_sync_positions",
                        "planned",
                        medusa_id=payload.get("product_category_id"),
                        request_payload=payload,
                        diff={"items": len(payload.get("items") or [])},
                    )
                else:
                    response = client.sync_category_product_positions(payload)
                    status = "success" if bool(response.get("success", True)) else "error"
                    self._add_item(
                        run,
                        POSITION_ENTITY_TYPE,
                        category_id,
                        "sync_positions",
                        status,
                        medusa_id=payload.get("product_category_id"),
                        request_payload=payload,
                        response_payload=response,
                        diff={
                            "items": len(payload.get("items") or []),
                            "created": response.get("created"),
                            "updated": response.get("updated"),
                            "skipped": response.get("skipped"),
                        },
                        error_message="; ".join(map(str, response.get("errors") or [])) or None,
                    )
            except Exception as exc:
                self._add_item(
                    run,
                    POSITION_ENTITY_TYPE,
                    category_id,
                    "validate_positions" if dry_run else "sync_positions",
                    "validation_error",
                    error_message=str(exc),
                )
        status = "success" if not any(item.status in {"error", "validation_error"} for item in run.items) else "partial_success"
        if not dry_run and all(item.status in {"error", "validation_error"} for item in run.items):
            status = "failed"
        self._finish_run(run, status, self._summary(run))
        return {
            "status": status,
            "run_id": run.id,
            "category_count": len(ids),
            "summary": run.summary,
        }

    def _upsert_product(
        self,
        client: MedusaAdminApiClient,
        config: MedusaConnectionConfig,
        product: Product,
        product_payload: Any,
        mapping: MedusaSyncMapping | None,
        *,
        force: bool,
    ) -> tuple[str, str]:
        existing = None
        if mapping and mapping.medusa_id:
            try:
                existing = client.get_product(mapping.medusa_id)
            except MedusaApiError as exc:
                if exc.status_code != 404:
                    raise
                mapping.status = "missing_in_medusa"
        if existing is None:
            existing = client.find_product_by_handle(product_payload.payload["handle"])
        if existing is None:
            existing = client.find_product_by_external_id_or_metadata(product.id)
        if existing:
            medusa_product = existing.get("product", existing)
            medusa_id = str(medusa_product.get("id"))
            action = "skip" if mapping and mapping.local_hash == product_payload.hash and not force else "update"
            response = {"product": medusa_product} if action == "skip" else client.update_product(medusa_id, product_payload.payload)
        else:
            action = "create"
            response = client.create_product(product_payload.payload)
            medusa_product = response.get("product", response)
            medusa_id = str(medusa_product.get("id"))
        if not medusa_id or medusa_id == "None":
            raise MedusaApiError("Medusa-Antwort enthält keine Product ID.", response_payload=response)
        mapping = self._get_or_create_mapping(config, "product", product.id)
        mapping.medusa_id = medusa_id
        mapping.medusa_handle = product_payload.payload.get("handle")
        mapping.medusa_external_id = product_payload.payload.get("external_id")
        mapping.local_hash = product_payload.hash
        mapping.last_synced_at = datetime.now(timezone.utc)
        mapping.status = "active"
        return medusa_id, action

    def _sync_variants(
        self,
        run: MedusaSyncRun,
        client: MedusaAdminApiClient,
        config: MedusaConnectionConfig,
        product: Product,
        variant_mapper: MedusaVariantMapper,
        pricing_mapper: MedusaPricingMapper,
        *,
        dry_run: bool,
        force: bool,
        medusa_product_id: str | None,
    ) -> None:
        for variant in sorted(product.variants or [], key=lambda item: item.id):
            if str(variant.status or "").lower() == "archived":
                continue
            translation = _variant_translation_for(variant, config.default_locale)
            mapped = variant_mapper.map_variant(variant, translation=translation)
            try:
                prices = pricing_mapper.variant_prices(variant)
            except Exception as exc:
                self._add_item(run, "price", variant.id, "error", "validation_error", error_message=str(exc))
                prices = []
            if not prices:
                message = f"Variante {variant.id} nicht exportiert: Verkaufspreis fehlt."
                self._add_item(run, "variant", variant.id, "skip", "validation_error", request_payload=mapped.payload, error_message=message)
                continue
            mapped.payload["prices"] = prices
            mapped = type(mapped)(local_id=mapped.local_id, payload=mapped.payload, hash=stable_hash(mapped.payload), sku=mapped.sku)
            mapping = self._mapping(config, "variant", variant.id)
            action = self._planned_action(mapping, mapped.hash, force=force)
            if not variant.sku and "sku" in (config.variant_match_policy or ""):
                self._add_item(run, "variant", variant.id, "error", "validation_error", request_payload=mapped.payload, error_message="Variante ohne SKU kann nicht sicher gematcht werden.")
                continue
            medusa_variant_id = mapping.medusa_id if mapping else None
            if dry_run:
                self._add_item(run, "variant", variant.id, f"would_{action}", "planned", request_payload=mapped.payload, diff={"hash": mapped.hash})
            elif medusa_product_id:
                try:
                    medusa_variant_id, action = self._upsert_variant(client, config, variant, medusa_product_id, mapped, mapping, force=force)
                    self._add_item(run, "variant", variant.id, action, "success", medusa_id=medusa_variant_id, request_payload=mapped.payload)
                except Exception as exc:
                    self._add_item(run, "variant", variant.id, "error", "error", request_payload=mapped.payload, error_message=str(exc))
                    continue
            self._sync_prices(run, client, config, variant, pricing_mapper, dry_run=dry_run, medusa_product_id=medusa_product_id, medusa_variant_id=medusa_variant_id, prices=prices)

    def _upsert_variant(
        self,
        client: MedusaAdminApiClient,
        config: MedusaConnectionConfig,
        variant: ProductVariant,
        medusa_product_id: str,
        mapped: Any,
        mapping: MedusaSyncMapping | None,
        *,
        force: bool,
    ) -> tuple[str, str]:
        existing = None
        if mapping and mapping.medusa_id:
            try:
                existing = client.get_variant(mapping.medusa_id)
            except MedusaApiError as exc:
                if exc.status_code != 404:
                    raise
                mapping.status = "missing_in_medusa"
        if existing is None and variant.sku:
            existing = client.find_variant_by_sku(medusa_product_id, variant.sku)
        if existing:
            medusa_variant = existing.get("variant", existing)
            medusa_variant_id = str(medusa_variant.get("id"))
            action = "skip" if mapping and mapping.local_hash == mapped.hash and not force else "update"
            if action != "skip":
                client.update_variant(medusa_product_id, medusa_variant_id, mapped.payload)
        else:
            action = "create"
            response = client.create_variant(medusa_product_id, mapped.payload)
            medusa_variant = response.get("variant", response)
            medusa_variant_id = str(medusa_variant.get("id"))
        if not medusa_variant_id or medusa_variant_id == "None":
            raise MedusaApiError("Medusa-Antwort enthält keine Variant ID.")
        mapping = self._get_or_create_mapping(config, "variant", variant.id, local_parent_id=variant.product_id)
        mapping.medusa_id = medusa_variant_id
        mapping.medusa_parent_id = medusa_product_id
        mapping.medusa_sku = variant.sku
        mapping.local_hash = mapped.hash
        mapping.last_synced_at = datetime.now(timezone.utc)
        mapping.status = "active"
        return medusa_variant_id, action

    def _sync_prices(
        self,
        run: MedusaSyncRun,
        client: MedusaAdminApiClient,
        config: MedusaConnectionConfig,
        variant: ProductVariant,
        pricing_mapper: MedusaPricingMapper,
        *,
        dry_run: bool,
        medusa_product_id: str | None,
        medusa_variant_id: str | None,
        prices: list[dict[str, Any]] | None = None,
    ) -> None:
        if not config.export_default_prices and not config.export_tiered_prices:
            return
        if prices is None:
            try:
                prices = pricing_mapper.variant_prices(variant)
            except Exception as exc:
                self._add_item(run, "price", variant.id, "error", "validation_error", error_message=str(exc))
                return
        payload = {"prices": prices}
        payload_hash = stable_hash(payload)
        mapping = self._mapping(config, "price", variant.id, currency_code=(variant.currency or config.default_currency_code or "CHF").upper())
        action = self._planned_action(mapping, payload_hash, force=False)
        if dry_run:
            self._add_item(run, "price", variant.id, f"would_{action}", "planned", request_payload=payload)
            return
        if medusa_product_id and medusa_variant_id and prices:
            try:
                response = client.upsert_variant_prices(medusa_product_id, medusa_variant_id, _medusa_variant_prices_payload(prices))
                mapping = self._get_or_create_mapping(config, "price", variant.id, local_parent_id=variant.id, currency_code=(variant.currency or config.default_currency_code or "CHF").upper())
                mapping.medusa_parent_id = medusa_variant_id
                mapping.local_hash = payload_hash
                mapping.last_synced_at = datetime.now(timezone.utc)
                mapping.status = "active"
                self._add_item(run, "price", variant.id, action, "success", medusa_id=medusa_variant_id, request_payload=payload, response_payload=response)
            except Exception as exc:
                self._add_item(run, "price", variant.id, "error", "error", request_payload=payload, error_message=str(exc))

    def _sync_translations(
        self,
        run: MedusaSyncRun,
        client: MedusaAdminApiClient,
        config: MedusaConnectionConfig,
        product: Product,
        translation_mapper: MedusaTranslationMapper,
        *,
        dry_run: bool,
    ) -> None:
        if not config.export_translations:
            return
        default_locale = config.default_locale or "de-CH"
        product_mapping = self._mapping(config, "product", product.id)
        for translation in product.translations or []:
            if translation.language_code == default_locale:
                continue
            payload = translation_mapper.product_translation_payload(translation)
            if not payload:
                continue
            payload_hash = stable_hash(payload)
            mapping = self._mapping(config, "translation", translation.id, locale_code=translation.language_code)
            action = self._planned_action(mapping, payload_hash, force=False)
            if dry_run:
                self._add_item(run, "translation", translation.id, f"would_{action}", "planned", locale_code=translation.language_code, request_payload=payload)
                continue
            if product_mapping and product_mapping.medusa_id:
                try:
                    response = client.upsert_translation("product", product_mapping.medusa_id, translation.language_code, payload)
                    mapping = self._get_or_create_mapping(config, "translation", translation.id, local_parent_id=product.id, locale_code=translation.language_code)
                    mapping.medusa_parent_id = product_mapping.medusa_id
                    mapping.local_hash = payload_hash
                    mapping.medusa_id = response.get("translation", {}).get("id") if isinstance(response, dict) else None
                    mapping.last_synced_at = datetime.now(timezone.utc)
                    mapping.status = "active"
                    self._add_item(run, "translation", translation.id, action, "success", medusa_id=mapping.medusa_id, locale_code=translation.language_code, request_payload=payload)
                except MedusaApiError as exc:
                    if exc.status_code == 404:
                        try:
                            fallback_payload = _translation_metadata_payload(client, "product", product_mapping.medusa_id, translation.language_code, payload)
                            client.update_product(product_mapping.medusa_id, fallback_payload)
                            mapping = self._get_or_create_mapping(config, "translation", translation.id, local_parent_id=product.id, locale_code=translation.language_code)
                            mapping.medusa_parent_id = product_mapping.medusa_id
                            mapping.local_hash = payload_hash
                            mapping.last_synced_at = datetime.now(timezone.utc)
                            mapping.status = "active"
                            self._add_item(
                                run,
                                "translation",
                                translation.id,
                                "fallback_metadata",
                                "success",
                                medusa_id=product_mapping.medusa_id,
                                locale_code=translation.language_code,
                                request_payload=fallback_payload,
                                diff={"reason": "Custom Translation Admin Route /admin/pim-sync/translations nicht vorhanden."},
                            )
                        except Exception as fallback_exc:
                            self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(fallback_exc))
                    else:
                        self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(exc))
                except Exception as exc:
                    self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(exc))
        for variant in product.variants or []:
            variant_mapping = self._mapping(config, "variant", variant.id)
            for translation in variant.translations or []:
                if translation.language_code == default_locale:
                    continue
                payload = translation_mapper.variant_translation_payload(translation)
                if not payload:
                    continue
                payload_hash = stable_hash(payload)
                mapping = self._mapping(config, "translation", translation.id, locale_code=translation.language_code)
                action = self._planned_action(mapping, payload_hash, force=False)
                if dry_run:
                    self._add_item(run, "translation", translation.id, f"would_{action}", "planned", locale_code=translation.language_code, request_payload=payload)
                elif variant_mapping and variant_mapping.medusa_id:
                    try:
                        response = client.upsert_translation("product_variant", variant_mapping.medusa_id, translation.language_code, payload)
                        mapping = self._get_or_create_mapping(config, "translation", translation.id, local_parent_id=variant.id, locale_code=translation.language_code)
                        mapping.medusa_parent_id = variant_mapping.medusa_id
                        mapping.local_hash = payload_hash
                        mapping.medusa_id = response.get("translation", {}).get("id") if isinstance(response, dict) else None
                        mapping.last_synced_at = datetime.now(timezone.utc)
                        mapping.status = "active"
                        self._add_item(run, "translation", translation.id, action, "success", medusa_id=mapping.medusa_id, locale_code=translation.language_code, request_payload=payload)
                    except MedusaApiError as exc:
                        if exc.status_code == 404:
                            try:
                                fallback_payload = _translation_metadata_payload(client, "variant", variant_mapping.medusa_id, translation.language_code, payload, product_id=product_mapping.medusa_id)
                                product_mapping = self._mapping(config, "product", product.id)
                                if not product_mapping or not product_mapping.medusa_id:
                                    raise MedusaApiError("Produkt-Mapping für Varianten-Translation fehlt.")
                                client.update_variant(product_mapping.medusa_id, variant_mapping.medusa_id, fallback_payload)
                                mapping = self._get_or_create_mapping(config, "translation", translation.id, local_parent_id=variant.id, locale_code=translation.language_code)
                                mapping.medusa_parent_id = variant_mapping.medusa_id
                                mapping.local_hash = payload_hash
                                mapping.last_synced_at = datetime.now(timezone.utc)
                                mapping.status = "active"
                                self._add_item(
                                    run,
                                    "translation",
                                    translation.id,
                                    "fallback_metadata",
                                    "success",
                                    medusa_id=variant_mapping.medusa_id,
                                    locale_code=translation.language_code,
                                    request_payload=fallback_payload,
                                    diff={"reason": "Custom Translation Admin Route /admin/pim-sync/translations nicht vorhanden."},
                                )
                            except Exception as fallback_exc:
                                self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(fallback_exc))
                        else:
                            self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(exc))
                    except Exception as exc:
                        self._add_item(run, "translation", translation.id, "error", "error", locale_code=translation.language_code, request_payload=payload, error_message=str(exc))

    def _load_product(self, product_id: int) -> Product | None:
        return self.session.scalar(
            select(Product)
            .options(
                joinedload(Product.brand),
                joinedload(Product.translations),
                joinedload(Product.assets),
                joinedload(Product.channel_category_mappings).joinedload(ProductCategoryMapping.sales_channel),
                joinedload(Product.channel_category_mappings).joinedload(ProductCategoryMapping.channel_category),
                joinedload(Product.variants).joinedload(ProductVariant.translations),
                joinedload(Product.variants).joinedload(ProductVariant.assets),
                joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
                joinedload(Product.variants).joinedload(ProductVariant.technical_attributes),
                joinedload(Product.variants).joinedload(ProductVariant.technical_attributes).joinedload(VariantTechnicalAttribute.translations),
            )
            .where(Product.id == product_id)
        )

    def _start_run(self, config: MedusaConnectionConfig, mode: str, scope: dict[str, Any]) -> MedusaSyncRun:
        run = MedusaSyncRun(connection_id=config.id, mode=mode, status="running", selected_scope=scope)
        self.session.add(run)
        self.session.flush()
        return run

    def _finish_run(self, run: MedusaSyncRun, status: str, summary: dict[str, Any]) -> None:
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        run.summary = summary
        self.session.flush()

    def _add_item(
        self,
        run: MedusaSyncRun,
        entity_type: str,
        local_entity_id: int | None,
        action: str,
        status: str,
        *,
        medusa_id: str | None = None,
        locale_code: str | None = None,
        price_list_code: str | None = None,
        currency_code: str | None = None,
        request_payload: dict[str, Any] | None = None,
        response_payload: dict[str, Any] | None = None,
        diff: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> MedusaSyncRunItem:
        item = MedusaSyncRunItem(
            run_id=run.id,
            entity_type=entity_type,
            local_entity_id=local_entity_id,
            medusa_id=medusa_id,
            locale_code=locale_code,
            price_list_code=price_list_code,
            currency_code=currency_code,
            action=action,
            status=status,
            request_payload=request_payload,
            response_payload=response_payload,
            diff=diff,
            error_message=error_message,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def _mapping(
        self,
        config: MedusaConnectionConfig,
        entity_type: str,
        local_entity_id: int,
        *,
        locale_code: str | None = None,
        currency_code: str | None = None,
    ) -> MedusaSyncMapping | None:
        return self.session.scalar(
            select(MedusaSyncMapping).where(
                MedusaSyncMapping.connection_id == config.id,
                MedusaSyncMapping.entity_type == entity_type,
                MedusaSyncMapping.local_entity_id == local_entity_id,
                MedusaSyncMapping.locale_code.is_(None) if locale_code is None else MedusaSyncMapping.locale_code == locale_code,
                MedusaSyncMapping.currency_code.is_(None) if currency_code is None else MedusaSyncMapping.currency_code == currency_code,
            )
        )

    def _get_or_create_mapping(
        self,
        config: MedusaConnectionConfig,
        entity_type: str,
        local_entity_id: int,
        *,
        local_parent_id: int | None = None,
        locale_code: str | None = None,
        currency_code: str | None = None,
    ) -> MedusaSyncMapping:
        mapping = self._mapping(config, entity_type, local_entity_id, locale_code=locale_code, currency_code=currency_code)
        if mapping is None:
            mapping = MedusaSyncMapping(
                connection_id=config.id,
                entity_type=entity_type,
                local_entity_id=local_entity_id,
                local_parent_id=local_parent_id,
                locale_code=locale_code,
                currency_code=currency_code,
                status="active",
            )
            self.session.add(mapping)
            self.session.flush()
        return mapping

    def _planned_action(self, mapping: MedusaSyncMapping | None, payload_hash: str, *, force: bool) -> str:
        if mapping and mapping.medusa_id:
            return "skip" if mapping.local_hash == payload_hash and not force else "update"
        return "create"

    def _summary(self, run: MedusaSyncRun) -> dict[str, Any]:
        counter = Counter(f"{item.entity_type}:{item.action}:{item.status}" for item in run.items)
        return {"items": len(run.items), "counts": dict(counter)}


def _translation_for(product: Product, locale: str | None) -> ProductTranslation | None:
    return next((row for row in product.translations or [] if row.language_code == locale), None)


def _variant_translation_for(variant: ProductVariant, locale: str | None) -> VariantTranslation | None:
    return next((row for row in variant.translations or [] if row.language_code == locale), None)


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _unique_ints(values: list[int] | None) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values or []:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _path_depth(value: str | None) -> int:
    return len([part for part in str(value or "").split(">") if part.strip()])


def _parent_path(value: str | None) -> str | None:
    parts = [part.strip() for part in str(value or "").split(">") if part.strip()]
    if len(parts) <= 1:
        return None
    return " > ".join(parts[:-1])


def _medusa_variant_prices_payload(prices: list[dict[str, Any]]) -> dict[str, Any]:
    clean_prices: list[dict[str, Any]] = []
    for price in prices:
        clean = {
            "currency_code": price.get("currency_code"),
            "amount": price.get("amount"),
        }
        if price.get("min_quantity") is not None:
            clean["min_quantity"] = price["min_quantity"]
        if price.get("max_quantity") is not None:
            clean["max_quantity"] = price["max_quantity"]
        rules = price.get("rules")
        if rules:
            clean["rules"] = rules
        clean_prices.append(clean)
    return {"prices": clean_prices}


def _translation_metadata_payload(
    client: MedusaAdminApiClient,
    reference: str,
    reference_id: str,
    locale_code: str,
    payload: dict[str, Any],
    *,
    product_id: str | None = None,
) -> dict[str, Any]:
    existing_metadata: dict[str, Any] = {}
    try:
        if reference == "product":
            product_payload = client.get_product(reference_id)
            product = product_payload.get("product", product_payload) if isinstance(product_payload, dict) else {}
            existing_metadata = dict(product.get("metadata") or {})
        elif reference == "variant" and product_id:
            for variant in client.list_product_variants(product_id):
                if variant.get("id") == reference_id:
                    existing_metadata = dict(variant.get("metadata") or {})
                    break
    except Exception:
        existing_metadata = {}
    translations = dict(existing_metadata.get("translations") or {})
    translations[locale_code] = payload
    return {
        "metadata": {
            **existing_metadata,
            "translations": {
                **translations,
            },
            "translation_strategy": "metadata_fallback",
            "translation_source": "pim-pam",
        }
    }
