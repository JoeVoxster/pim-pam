from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Asset,
    Brand,
    Category,
    ChannelCategory,
    ChemicalDocument,
    ImportJob,
    MedusaSyncMapping,
    Product,
    ProductCategoryAssignment,
    ProductCategoryMapping,
    ProductChemicalEnrichment,
    ProductChannelListing,
    ProductSDB,
    ProductSDBLLMRun,
    ProductTranslation,
    ProductVariant,
    ProductVariantPriceTier,
    SalesChannel,
    VariantCategoryMapping,
    VariantChannelListing,
    VariantTranslation,
)
from app.schemas.pim import (
    ChannelCategoryUpsert,
    ProductCategoryMappingUpsert,
    ProductChannelListingUpdate,
    ProductCreate,
    ProductSDBUpdate,
    ProductTranslationCreate,
    ProductUpdate,
    SalesChannelCreate,
    SalesChannelUpdate,
    VariantCategoryMappingUpsert,
    VariantChannelListingUpdate,
    VariantCreate,
    VariantPriceTierCreate,
    VariantTranslationCreate,
    VariantUpdate,
)
from app.io.writers import write_channel_export_rows, write_medusa_products
from app.models import ProductOutputRow
from app.services.chemical_classification_service import (
    build_chem_safety_payload,
    normalize_storage_class,
    normalize_wgk,
    storage_class_label,
    wgk_label,
)
from app.services.sdb_support import default_sdb_sections, merge_sdb_sections
from app.services.r2_config_service import get_r2_public_base_url

PUBLICATION_STATUS_OPTIONS = ["imported", "draft", "ready", "published", "inactive", "archived"]
DEFAULT_SALES_CHANNELS = [
    {"code": "voxster", "name": "voxster.ch", "sort_order": 10, "is_active": True},
    {"code": "pos", "name": "POS", "sort_order": 20, "is_active": True},
    {"code": "chemie_shop", "name": "Chemie Shop", "sort_order": 30, "is_active": True},
    {"code": "otto", "name": "OTTO", "sort_order": 40, "is_active": False},
    {"code": "ebay", "name": "eBay", "sort_order": 50, "is_active": False},
]
DEFAULT_CATEGORY_CHANNEL_CODE = "voxster"
DEFAULT_CATEGORY_CHANNEL_NAME = "voxster.ch"


CHEMICAL_FIELD_NAMES = [
    "is_chemical",
    "chemical_type",
    "ufi",
    "voc_content_percent",
    "cas_number",
    "ec_number",
    "un_number",
    "hazard_class",
    "packing_group",
    "adr_relevant",
    "ghs_pictograms",
    "signal_word",
    "chemical_safety_json",
    "hazard_statements",
    "precautionary_statements",
    "wgk",
    "wgk_label",
    "wgk_source_section",
    "wgk_source_url",
    "wgk_source_asset_id",
    "wgk_confidence",
    "wgk_last_enriched_at",
    "storage_class",
    "storage_class_label",
    "storage_class_source_section",
    "storage_class_source_url",
    "storage_class_source_asset_id",
    "storage_class_confidence",
    "storage_class_last_enriched_at",
    "sds_available",
    "sds_url",
    "sds_asset_id",
    "density",
    "color",
    "odor",
    "ph_value",
    "flash_point",
    "boiling_point",
    "viscosity",
    "solubility",
    "business_only",
    "age_check_required",
    "shippable",
    "limited_quantity",
    "hazard_shipping_note",
    "shop_active",
]

CHEMICAL_ENRICHMENT_FIELD_NAMES = [
    "chemical_reference_url",
    "chemical_last_enriched_at",
    "chemical_enrichment_status",
    "chemical_enrichment_error",
]


def _serialize_chemical_fields(product: Product) -> dict[str, object]:
    return {field_name: getattr(product, field_name) for field_name in CHEMICAL_FIELD_NAMES}


def _is_sdb_asset(asset: Asset) -> bool:
    asset_type = str(asset.asset_type or "").lower()
    if asset_type in {"sds", "sdb", "safety_data_sheet"}:
        return True
    haystack = " ".join([asset.filename or "", asset.original_filename or "", asset.source_url or ""]).lower()
    return any(token in haystack for token in ("sds", "sdb", "sicherheitsdatenblatt", "safety-data-sheet", "safety_data_sheet", "safety data sheet"))


def _inferred_sds_asset(assets: Iterable[Asset]) -> Asset | None:
    candidates = [asset for asset in assets if _is_sdb_asset(asset)]
    return min(candidates, key=lambda asset: (asset.sort_order, asset.id), default=None)


def _serialize_import_chemical_payload(payload: dict[str, object]) -> dict[str, object]:
    return {field_name: payload[field_name] for field_name in CHEMICAL_FIELD_NAMES if field_name in payload}


def _serialize_chemical_enrichment_fields(product: Product) -> dict[str, object]:
    payload = {field_name: getattr(product, field_name) for field_name in CHEMICAL_ENRICHMENT_FIELD_NAMES}
    if isinstance(payload.get("chemical_last_enriched_at"), datetime):
        payload["chemical_last_enriched_at"] = payload["chemical_last_enriched_at"].isoformat()
    return payload


def get_brand_by_name(session: Session, name: str | None) -> Brand | None:
    if not name:
        return None
    return session.scalar(select(Brand).where(Brand.name == name.strip()))


def get_or_create_brand(session: Session, name: str | None) -> Brand | None:
    if not name:
        return None
    normalized_name = name.strip()
    existing = get_brand_by_name(session, normalized_name)
    if existing:
        return existing
    slug = slugify(normalized_name, separator="-")
    existing_by_slug = session.scalar(select(Brand).where(Brand.slug == slug))
    if existing_by_slug:
        if existing_by_slug.name != normalized_name:
            existing_by_slug.name = normalized_name
        return existing_by_slug
    brand = Brand(name=normalized_name, slug=slug)
    session.add(brand)
    session.flush()
    return brand


def ensure_default_sales_channels(session: Session) -> list[SalesChannel]:
    rows: list[SalesChannel] = []
    for item in DEFAULT_SALES_CHANNELS:
        channel = session.scalar(select(SalesChannel).where(SalesChannel.code == item["code"]))
        if channel is None:
            channel = SalesChannel(
                code=item["code"],
                name=item["name"],
                is_active=bool(item["is_active"]),
                sort_order=int(item["sort_order"]),
            )
            session.add(channel)
            session.flush()
        else:
            channel.name = item["name"]
            channel.sort_order = int(item["sort_order"])
            if channel.is_active is None:
                channel.is_active = bool(item["is_active"])
        rows.append(channel)
    return rows


def get_default_category_sales_channel(session: Session) -> SalesChannel:
    ensure_default_sales_channels(session)
    channel = session.scalar(select(SalesChannel).where(SalesChannel.code == DEFAULT_CATEGORY_CHANNEL_CODE))
    if channel is None:
        raise ValueError(f"Kategorien-Kanal {DEFAULT_CATEGORY_CHANNEL_NAME} nicht gefunden")
    return channel


def get_sales_channel_by_code(session: Session, sales_channel_code: str | None) -> SalesChannel:
    ensure_default_sales_channels(session)
    code = (sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE).strip() or DEFAULT_CATEGORY_CHANNEL_CODE
    channel = session.scalar(select(SalesChannel).where(SalesChannel.code == code))
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    return channel


def _normalize_publication_status(value: str | None, default: str = "draft") -> str:
    normalized = (value or default).strip().lower()
    return normalized if normalized in PUBLICATION_STATUS_OPTIONS else default


def _normalize_variant_status(value: str | None) -> str:
    normalized = (value or "active").strip().lower()
    if normalized in {"archived", "archiviert"}:
        return "archived"
    if normalized in {"inactive", "inaktiv"}:
        return "inactive"
    return "active"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _unique_handle(session: Session, desired: str, product_id: int | None = None) -> str:
    base_handle = slugify(desired, separator="-") or "product"
    handle = base_handle
    suffix = 2
    while True:
        stmt = select(Product).where(Product.handle == handle)
        existing = session.scalar(stmt)
        if existing is None or existing.id == product_id:
            return handle
        handle = f"{base_handle}-{suffix}"
        suffix += 1


def get_or_create_categories(
    session: Session,
    category_paths: Iterable[str],
    separator: str = ">",
    sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE,
) -> list[Category]:
    categories: list[Category] = []
    channel = get_sales_channel_by_code(session, sales_channel_code)
    for raw_path in category_paths:
        if not raw_path:
            continue
        parent: Category | None = None
        for part in [segment.strip() for segment in raw_path.split(separator) if segment.strip()]:
            slug = slugify(part, separator="-")
            stmt = select(Category).where(
                Category.sales_channel_id == channel.id,
                Category.slug == slug,
            )
            category = session.scalar(stmt)
            if category is None:
                category = Category(name=part, slug=slug, parent=parent, sales_channel_id=channel.id)
                session.add(category)
                session.flush()
            parent = category
        if parent is not None:
            categories.append(parent)
    return categories


def _serialize_sales_channel(channel: SalesChannel) -> dict:
    return {
        "id": channel.id,
        "code": channel.code,
        "name": channel.name,
        "is_active": bool(channel.is_active),
        "sort_order": channel.sort_order,
        "created_at": channel.created_at.isoformat() if channel.created_at else None,
        "updated_at": channel.updated_at.isoformat() if channel.updated_at else None,
    }


def list_sales_channels(session: Session) -> list[dict]:
    ensure_default_sales_channels(session)
    stmt = select(SalesChannel).order_by(SalesChannel.sort_order.asc(), SalesChannel.name.asc())
    return [_serialize_sales_channel(channel) for channel in session.scalars(stmt)]


def create_or_update_sales_channel(session: Session, payload: SalesChannelCreate | SalesChannelUpdate, channel_id: int | None = None, code: str | None = None) -> SalesChannel:
    channel = None
    if channel_id is not None:
        channel = session.get(SalesChannel, int(channel_id))
    elif code:
        channel = session.scalar(select(SalesChannel).where(SalesChannel.code == code.strip()))
    if channel is None:
        if not isinstance(payload, SalesChannelCreate):
            raise ValueError("Neuer Vertriebskanal benötigt code und name")
        normalized_code = slugify(payload.code, separator="_")
        if not normalized_code:
            raise ValueError("Code ist Pflicht")
        existing = session.scalar(select(SalesChannel).where(SalesChannel.code == normalized_code))
        if existing is not None:
            raise ValueError("Vertriebskanal-Code bereits vergeben")
        channel = SalesChannel(
            code=normalized_code,
            name=payload.name.strip(),
            is_active=bool(payload.is_active),
            sort_order=int(payload.sort_order or 0),
        )
        session.add(channel)
        session.flush()
        return channel
    if isinstance(payload, SalesChannelCreate):
        channel.name = payload.name.strip()
        channel.is_active = bool(payload.is_active)
        channel.sort_order = int(payload.sort_order or 0)
    else:
        if payload.name is not None:
            channel.name = payload.name.strip()
        if payload.is_active is not None:
            channel.is_active = bool(payload.is_active)
        if payload.sort_order is not None:
            channel.sort_order = int(payload.sort_order)
    session.flush()
    return channel


def _serialize_channel_category(row: ChannelCategory) -> dict:
    return {
        "id": row.id,
        "sales_channel_id": row.sales_channel_id,
        "sales_channel_code": row.sales_channel.code if row.sales_channel else None,
        "sales_channel_name": row.sales_channel.name if row.sales_channel else None,
        "external_category_id": row.external_category_id,
        "external_path": row.external_path,
        "name": row.name,
        "required_attributes_json": row.required_attributes_json or [],
        "is_active": bool(row.is_active),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_channel_categories(session: Session) -> list[dict]:
    ensure_default_sales_channels(session)
    stmt = select(ChannelCategory).options(joinedload(ChannelCategory.sales_channel)).order_by(ChannelCategory.sales_channel_id.asc(), ChannelCategory.name.asc())
    return [_serialize_channel_category(row) for row in session.scalars(stmt).unique()]


def _channel_category_path_parts(row: ChannelCategory | dict) -> list[str]:
    if isinstance(row, dict):
        raw_path = row.get("external_path")
        name = row.get("name")
    else:
        raw_path = row.external_path
        name = row.name
    text = (raw_path or name or "").strip()
    if not text:
        return []
    normalized = text.replace("/", ">").replace("|", ">")
    return [part.strip() for part in normalized.split(">") if part.strip()]


def build_channel_category_tree_rows(rows: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    path_index: dict[tuple[str, ...], int] = {}
    for row in rows:
        parts = _channel_category_path_parts(row) or [str(row.get("name") or row.get("external_category_id") or row.get("id"))]
        key = tuple(part.casefold() for part in parts)
        prepared_row = {
            **row,
            "path_parts": parts,
            "path_key": key,
            "tree_level": max(len(parts) - 1, 0),
            "breadcrumb": " > ".join(parts),
        }
        prepared.append(prepared_row)
        if row.get("id") is not None:
            path_index[key] = int(row["id"])

    for row in prepared:
        parent_key = row["path_key"][:-1]
        row["parent_id"] = path_index.get(parent_key) if parent_key else None

    children_by_parent: dict[int | None, list[dict]] = {None: []}
    for row in prepared:
        parent_id = row.get("parent_id")
        children_by_parent.setdefault(parent_id, []).append(row)
        if row.get("id") is not None:
            children_by_parent.setdefault(int(row["id"]), [])

    for row in prepared:
        row["has_children"] = bool(children_by_parent.get(row.get("id")))

    def sort_key(item: dict) -> tuple[str, str, int]:
        return (str(item.get("breadcrumb") or "").casefold(), str(item.get("name") or "").casefold(), int(item.get("id") or 0))

    for siblings in children_by_parent.values():
        siblings.sort(key=sort_key)

    result: list[dict] = []

    def walk(parent_id: int | None) -> None:
        for row in children_by_parent.get(parent_id, []):
            result.append(row)
            if row.get("id") is not None:
                walk(int(row["id"]))

    walk(None)
    return result


def get_channel_category_tree(session: Session, channel_id: int) -> list[dict]:
    stmt = (
        select(ChannelCategory)
        .options(joinedload(ChannelCategory.sales_channel))
        .where(ChannelCategory.sales_channel_id == int(channel_id))
        .order_by(ChannelCategory.external_path.asc(), ChannelCategory.name.asc(), ChannelCategory.id.asc())
    )
    product_counts = dict(
        session.execute(
            select(ProductCategoryMapping.channel_category_id, func.count(func.distinct(ProductCategoryMapping.product_id)))
            .where(ProductCategoryMapping.sales_channel_id == int(channel_id))
            .group_by(ProductCategoryMapping.channel_category_id)
        ).all()
    )
    rows = []
    for row in session.scalars(stmt).unique():
        serialized = _serialize_channel_category(row)
        serialized["product_count"] = int(product_counts.get(row.id, 0))
        rows.append(serialized)
    return build_channel_category_tree_rows(rows)


def get_products_for_channel_category(session: Session, category_id: int, include_variants: bool = False) -> list[dict]:
    category = session.get(ChannelCategory, int(category_id))
    if category is None:
        return []
    mapping_rows = session.execute(
        select(ProductCategoryMapping.product_id, ProductCategoryMapping.position)
        .select_from(ProductCategoryMapping)
        .join(Product, Product.id == ProductCategoryMapping.product_id)
        .where(
            ProductCategoryMapping.channel_category_id == int(category_id),
            ProductCategoryMapping.sales_channel_id == category.sales_channel_id,
        )
        .order_by(ProductCategoryMapping.position.asc(), Product.title.asc(), Product.id.asc())
    ).all()
    product_order = {int(row.product_id): index for index, row in enumerate(mapping_rows)}
    positions = {int(row.product_id): int(row.position if row.position is not None else 9999) for row in mapping_rows}
    if not product_order:
        return []
    stmt = (
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.variants), joinedload(Product.assets))
        .where(Product.id.in_(product_order.keys()))
    )
    rows: list[dict] = []
    for product in sorted(session.scalars(stmt).unique(), key=lambda item: product_order.get(int(item.id), 0)):
        primary_asset = _primary_image_asset(product)
        variant_rows = [
            {
                "id": variant.id,
                "sku": variant.sku,
                "variant_title": variant.variant_title,
                "stock_qty": variant.stock_qty,
            }
            for variant in sorted(product.variants, key=lambda item: (item.sku or "", item.id))
        ]
        rows.append(
            {
                "id": product.id,
                "position": positions.get(int(product.id), 9999),
                "sort_order": positions.get(int(product.id), 9999),
                "sku": product.sku,
                "title": product.title,
                "photo_asset_id": primary_asset.id if primary_asset else None,
                "photo_url": f"/asset-file/{primary_asset.id}" if primary_asset else None,
                "photo_thumb_url": f"/asset-thumb/{primary_asset.id}" if primary_asset else None,
                "photo_filename": primary_asset.filename if primary_asset else None,
                "photo_mime_type": primary_asset.mime_type if primary_asset else None,
                "brand": product.brand.name if product.brand else None,
                "status": product.status,
                "variant_count": len(product.variants),
                "sales_channel_id": category.sales_channel_id,
                "sales_channel_code": category.sales_channel.code if category.sales_channel else None,
                "sales_channel_name": category.sales_channel.name if category.sales_channel else None,
                "variants": variant_rows if include_variants else [],
            }
        )
    return rows


def upsert_channel_category(session: Session, payload: ChannelCategoryUpsert, category_id: int | None = None) -> ChannelCategory:
    ensure_default_sales_channels(session)
    sales_channel = session.get(SalesChannel, payload.sales_channel_id)
    if sales_channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    row = session.get(ChannelCategory, category_id) if category_id else session.scalar(
        select(ChannelCategory).where(
            ChannelCategory.sales_channel_id == payload.sales_channel_id,
            ChannelCategory.external_category_id == payload.external_category_id.strip(),
        )
    )
    if row is None:
        row = ChannelCategory(
            sales_channel_id=payload.sales_channel_id,
            external_category_id=payload.external_category_id.strip(),
        )
        session.add(row)
    row.external_path = (payload.external_path or "").strip() or None
    row.name = payload.name.strip()
    row.required_attributes_json = payload.required_attributes_json or []
    row.is_active = bool(payload.is_active)
    session.flush()
    return row


def _serialize_product_channel_listing(product_id: int, row: ProductChannelListing | None, channel: SalesChannel, category_mapping: ProductCategoryMapping | None) -> dict:
    return {
        "id": row.id if row else None,
        "product_id": product_id,
        "sales_channel_id": channel.id,
        "sales_channel_code": channel.code,
        "sales_channel_name": channel.name,
        "allowed": bool(row.allowed) if row else False,
        "is_active": bool(row.is_active) if row else False,
        "active_from": row.active_from.isoformat() if row and row.active_from else None,
        "active_until": row.active_until.isoformat() if row and row.active_until else None,
        "publication_status": row.publication_status if row else "draft",
        "channel_category_id": category_mapping.channel_category_id if category_mapping else None,
        "channel_category_name": category_mapping.channel_category.name if category_mapping and category_mapping.channel_category else None,
        "channel_external_category_id": category_mapping.channel_category.external_category_id if category_mapping and category_mapping.channel_category else None,
        "channel_category_position": int(category_mapping.position if category_mapping and category_mapping.position is not None else 9999),
        "is_primary_category": bool(category_mapping.is_primary) if category_mapping else False,
    }


def list_product_channel_listings(session: Session, product_id: int) -> list[dict]:
    channels = ensure_default_sales_channels(session)
    listing_rows = {
        row.sales_channel_id: row
        for row in session.scalars(select(ProductChannelListing).where(ProductChannelListing.product_id == product_id))
    }
    mapping_rows = {
        row.sales_channel_id: row
        for row in session.scalars(
            select(ProductCategoryMapping)
            .options(joinedload(ProductCategoryMapping.channel_category))
            .where(ProductCategoryMapping.product_id == product_id)
        ).unique()
    }
    return [_serialize_product_channel_listing(product_id, listing_rows.get(channel.id), channel, mapping_rows.get(channel.id)) for channel in channels]


def upsert_product_channel_listing(session: Session, payload: ProductChannelListingUpdate) -> ProductChannelListing:
    product = session.get(Product, payload.product_id)
    if product is None:
        raise ValueError("Produkt nicht gefunden")
    channel = session.get(SalesChannel, payload.sales_channel_id)
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    row = session.scalar(
        select(ProductChannelListing).where(
            ProductChannelListing.product_id == payload.product_id,
            ProductChannelListing.sales_channel_id == payload.sales_channel_id,
        )
    )
    if row is None:
        row = ProductChannelListing(product_id=payload.product_id, sales_channel_id=payload.sales_channel_id)
        session.add(row)
    row.allowed = bool(payload.allowed)
    row.is_active = bool(payload.is_active)
    row.active_from = _parse_iso_datetime(payload.active_from)
    row.active_until = _parse_iso_datetime(payload.active_until)
    row.publication_status = _normalize_publication_status(payload.publication_status)
    session.flush()
    return row


def _serialize_variant_channel_listing(row: VariantChannelListing | None, channel: SalesChannel, variant: ProductVariant) -> dict:
    return {
        "id": row.id if row else None,
        "variant_id": variant.id,
        "variant_sku": variant.sku,
        "sales_channel_id": channel.id,
        "sales_channel_code": channel.code,
        "sales_channel_name": channel.name,
        "allowed": bool(row.allowed) if row else False,
        "is_active": bool(row.is_active) if row else False,
        "publication_status": row.publication_status if row else "draft",
        "price_enabled": bool(row.price_enabled) if row else True,
        "shippable": bool(row.shippable) if row else bool(variant.product.shippable if variant.product else True),
        "hazardous_goods": bool(row.hazardous_goods) if row else bool(variant.product.adr_relevant if variant.product else False),
        "limited_quantity": row.limited_quantity if row else (variant.product.limited_quantity if variant.product else None),
        "channel_sku": row.channel_sku if row else variant.sku,
        "channel_ean": row.channel_ean if row else variant.barcode,
    }


def list_variant_channel_listings(session: Session, product_id: int) -> list[dict]:
    channels = ensure_default_sales_channels(session)
    stmt = select(ProductVariant).options(joinedload(ProductVariant.product)).where(ProductVariant.product_id == product_id).order_by(ProductVariant.id.asc())
    variants = list(session.scalars(stmt).unique())
    listing_rows = {
        (row.variant_id, row.sales_channel_id): row
        for row in session.scalars(
            select(VariantChannelListing).join(ProductVariant).where(ProductVariant.product_id == product_id)
        )
    }
    rows: list[dict] = []
    for variant in variants:
        for channel in channels:
            rows.append(_serialize_variant_channel_listing(listing_rows.get((variant.id, channel.id)), channel, variant))
    return rows


def upsert_variant_channel_listing(session: Session, payload: VariantChannelListingUpdate) -> VariantChannelListing:
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is None:
        raise ValueError("Variante nicht gefunden")
    channel = session.get(SalesChannel, payload.sales_channel_id)
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    row = session.scalar(
        select(VariantChannelListing).where(
            VariantChannelListing.variant_id == payload.variant_id,
            VariantChannelListing.sales_channel_id == payload.sales_channel_id,
        )
    )
    if row is None:
        row = VariantChannelListing(variant_id=payload.variant_id, sales_channel_id=payload.sales_channel_id)
        session.add(row)
    row.allowed = bool(payload.allowed)
    row.is_active = bool(payload.is_active)
    row.publication_status = _normalize_publication_status(payload.publication_status)
    row.price_enabled = bool(payload.price_enabled)
    row.shippable = bool(payload.shippable)
    row.hazardous_goods = bool(payload.hazardous_goods)
    row.limited_quantity = (payload.limited_quantity or "").strip() or None
    row.channel_sku = (payload.channel_sku or "").strip() or None
    row.channel_ean = (payload.channel_ean or "").strip() or None
    session.flush()
    return row


def list_product_category_mappings(session: Session, product_id: int) -> list[dict]:
    stmt = (
        select(ProductCategoryMapping)
        .options(joinedload(ProductCategoryMapping.sales_channel), joinedload(ProductCategoryMapping.channel_category))
        .where(ProductCategoryMapping.product_id == product_id)
        .order_by(ProductCategoryMapping.sales_channel_id.asc())
    )
    return [
        {
            "id": row.id,
            "product_id": row.product_id,
            "sales_channel_id": row.sales_channel_id,
            "sales_channel_code": row.sales_channel.code if row.sales_channel else None,
            "sales_channel_name": row.sales_channel.name if row.sales_channel else None,
            "channel_category_id": row.channel_category_id,
            "channel_category_name": row.channel_category.name if row.channel_category else None,
            "external_category_id": row.channel_category.external_category_id if row.channel_category else None,
            "external_path": row.channel_category.external_path if row.channel_category else None,
            "position": int(row.position if row.position is not None else 9999),
            "is_primary": bool(row.is_primary),
        }
        for row in session.scalars(stmt).unique()
    ]


def upsert_product_category_mapping(session: Session, payload: ProductCategoryMappingUpsert) -> ProductCategoryMapping:
    product = session.get(Product, payload.product_id)
    if product is None:
        raise ValueError("Produkt nicht gefunden")
    channel = session.get(SalesChannel, payload.sales_channel_id)
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    channel_category = session.get(ChannelCategory, payload.channel_category_id)
    if channel_category is None:
        raise ValueError("Kanal-Kategorie nicht gefunden")
    if channel_category.sales_channel_id != payload.sales_channel_id:
        raise ValueError("Kanal-Kategorie gehört nicht zum gewählten Vertriebskanal")
    row = session.scalar(
        select(ProductCategoryMapping).where(
            ProductCategoryMapping.product_id == payload.product_id,
            ProductCategoryMapping.sales_channel_id == payload.sales_channel_id,
            ProductCategoryMapping.channel_category_id == payload.channel_category_id,
        )
    )
    if row is None:
        row = ProductCategoryMapping(
            product_id=payload.product_id,
            sales_channel_id=payload.sales_channel_id,
            channel_category_id=payload.channel_category_id,
        )
        session.add(row)
    if payload.position is not None:
        row.position = _normalize_category_position(payload.position)
    row.is_primary = bool(payload.is_primary)
    if row.is_primary:
        for sibling in session.scalars(
            select(ProductCategoryMapping).where(
                ProductCategoryMapping.product_id == payload.product_id,
                ProductCategoryMapping.sales_channel_id == payload.sales_channel_id,
                ProductCategoryMapping.id != row.id if row.id is not None else True,
            )
        ):
            sibling.is_primary = False
    session.flush()
    return row


def _normalize_category_position(value: object) -> int:
    if value in {None, ""}:
        return 9999
    try:
        position = int(float(str(value)))
    except (TypeError, ValueError):
        raise ValueError("Position muss numerisch sein")
    if position < 0:
        raise ValueError("Position darf nicht negativ sein")
    return position


def update_product_category_position(
    session: Session,
    category_id: int,
    product_id: int,
    position: object,
    *,
    sales_channel_id: int | None = None,
) -> ProductCategoryAssignment:
    category = session.get(Category, int(category_id))
    if category is None:
        raise ValueError("Kategorie nicht gefunden")
    channel_id = int(sales_channel_id or category.sales_channel_id)
    row = session.scalar(
        select(ProductCategoryAssignment).where(
            ProductCategoryAssignment.category_id == int(category_id),
            ProductCategoryAssignment.sales_channel_id == channel_id,
            ProductCategoryAssignment.product_id == int(product_id),
        )
    )
    if row is None:
        raise ValueError("Produkt ist nicht in dieser Kategorie")
    row.sort_order = _normalize_category_position(position)
    session.flush()
    return row


def bulk_update_category_product_positions(
    session: Session,
    category_id: int,
    rows: Iterable[dict],
    *,
    sales_channel_id: int | None = None,
) -> int:
    count = 0
    for row in rows:
        product_id = row.get("product_id") or row.get("id")
        if product_id in {None, ""}:
            continue
        update_product_category_position(
            session,
            int(category_id),
            int(product_id),
            row.get("position", row.get("sort_order")),
            sales_channel_id=sales_channel_id,
        )
        count += 1
    return count


def update_product_channel_category_position(
    session: Session,
    channel_category_id: int,
    product_id: int,
    position: object,
) -> ProductCategoryMapping:
    category = session.get(ChannelCategory, int(channel_category_id))
    if category is None:
        raise ValueError("Kanal-Kategorie nicht gefunden")
    row = session.scalar(
        select(ProductCategoryMapping).where(
            ProductCategoryMapping.channel_category_id == int(channel_category_id),
            ProductCategoryMapping.sales_channel_id == category.sales_channel_id,
            ProductCategoryMapping.product_id == int(product_id),
        )
    )
    if row is None:
        raise ValueError("Produkt ist nicht in dieser Kanal-Kategorie")
    row.position = _normalize_category_position(position)
    session.flush()
    return row


def bulk_update_channel_category_product_positions(
    session: Session,
    channel_category_id: int,
    rows: Iterable[dict],
) -> int:
    count = 0
    for row in rows:
        product_id = row.get("product_id") or row.get("id")
        if product_id in {None, ""}:
            continue
        update_product_channel_category_position(
            session,
            int(channel_category_id),
            int(product_id),
            row.get("position", row.get("sort_order")),
        )
        count += 1
    return count


def list_variant_category_mappings(session: Session, product_id: int) -> list[dict]:
    stmt = (
        select(VariantCategoryMapping)
        .join(ProductVariant, ProductVariant.id == VariantCategoryMapping.variant_id)
        .options(
            joinedload(VariantCategoryMapping.variant),
            joinedload(VariantCategoryMapping.sales_channel),
            joinedload(VariantCategoryMapping.channel_category),
        )
        .where(ProductVariant.product_id == product_id)
        .order_by(ProductVariant.sku.asc(), VariantCategoryMapping.sales_channel_id.asc())
    )
    return [
        {
            "id": row.id,
            "variant_id": row.variant_id,
            "variant_sku": row.variant.sku if row.variant else None,
            "variant_title": row.variant.variant_title if row.variant else None,
            "sales_channel_id": row.sales_channel_id,
            "sales_channel_code": row.sales_channel.code if row.sales_channel else None,
            "sales_channel_name": row.sales_channel.name if row.sales_channel else None,
            "channel_category_id": row.channel_category_id,
            "channel_category_name": row.channel_category.name if row.channel_category else None,
            "external_category_id": row.channel_category.external_category_id if row.channel_category else None,
            "external_path": row.channel_category.external_path if row.channel_category else None,
            "is_primary": bool(row.is_primary),
        }
        for row in session.scalars(stmt).unique()
    ]


def upsert_variant_category_mapping(session: Session, payload: VariantCategoryMappingUpsert) -> VariantCategoryMapping:
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is None:
        raise ValueError("Variante nicht gefunden")
    channel = session.get(SalesChannel, payload.sales_channel_id)
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    channel_category = session.get(ChannelCategory, payload.channel_category_id)
    if channel_category is None:
        raise ValueError("Kanal-Kategorie nicht gefunden")
    if channel_category.sales_channel_id != payload.sales_channel_id:
        raise ValueError("Kanal-Kategorie gehört nicht zum gewählten Vertriebskanal")
    row = session.scalar(
        select(VariantCategoryMapping).where(
            VariantCategoryMapping.variant_id == payload.variant_id,
            VariantCategoryMapping.sales_channel_id == payload.sales_channel_id,
            VariantCategoryMapping.channel_category_id == payload.channel_category_id,
        )
    )
    if row is None:
        row = VariantCategoryMapping(
            variant_id=payload.variant_id,
            sales_channel_id=payload.sales_channel_id,
            channel_category_id=payload.channel_category_id,
        )
        session.add(row)
    row.is_primary = bool(payload.is_primary)
    if row.is_primary:
        for sibling in session.scalars(
            select(VariantCategoryMapping).where(
                VariantCategoryMapping.variant_id == payload.variant_id,
                VariantCategoryMapping.sales_channel_id == payload.sales_channel_id,
                VariantCategoryMapping.id != row.id if row.id is not None else True,
            )
        ):
            sibling.is_primary = False
    session.flush()
    return row


def _unique_ints(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        normalized = int(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def variant_ids_for_products(session: Session, product_ids: Iterable[int]) -> list[int]:
    product_id_list = _unique_ints(product_ids)
    if not product_id_list:
        return []
    return list(
        session.scalars(
            select(ProductVariant.id)
            .where(ProductVariant.product_id.in_(product_id_list))
            .order_by(ProductVariant.product_id.asc(), ProductVariant.id.asc())
        )
    )


def product_ids_for_variants(session: Session, variant_ids: Iterable[int]) -> list[int]:
    variant_id_list = _unique_ints(variant_ids)
    if not variant_id_list:
        return []
    return _unique_ints(
        session.scalars(
            select(ProductVariant.product_id)
            .where(ProductVariant.id.in_(variant_id_list))
            .order_by(ProductVariant.product_id.asc())
        )
    )


def bulk_upsert_product_channel_listings(
    session: Session,
    product_ids: Iterable[int],
    sales_channel_id: int,
    *,
    allowed: bool,
    is_active: bool,
    publication_status: str,
    active_from: str | None = None,
    active_until: str | None = None,
) -> int:
    count = 0
    for product_id in _unique_ints(product_ids):
        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=product_id,
                sales_channel_id=int(sales_channel_id),
                allowed=allowed,
                is_active=is_active,
                active_from=active_from,
                active_until=active_until,
                publication_status=publication_status,
            ),
        )
        count += 1
    return count


def bulk_upsert_variant_channel_listings(
    session: Session,
    variant_ids: Iterable[int],
    sales_channel_id: int,
    *,
    allowed: bool,
    is_active: bool,
    publication_status: str,
    price_enabled: bool = True,
    shippable: bool = True,
    hazardous_goods: bool = False,
    limited_quantity: str | None = None,
) -> int:
    count = 0
    for variant_id in _unique_ints(variant_ids):
        variant = session.get(ProductVariant, variant_id)
        if variant is None:
            raise ValueError("Variante nicht gefunden")
        upsert_variant_channel_listing(
            session,
            VariantChannelListingUpdate(
                variant_id=variant_id,
                sales_channel_id=int(sales_channel_id),
                allowed=allowed,
                is_active=is_active,
                publication_status=publication_status,
                price_enabled=price_enabled,
                shippable=shippable,
                hazardous_goods=hazardous_goods,
                limited_quantity=limited_quantity,
                channel_sku=variant.sku,
                channel_ean=variant.barcode,
            ),
        )
        count += 1
    return count


def bulk_upsert_product_category_mappings(
    session: Session,
    product_ids: Iterable[int],
    sales_channel_id: int,
    channel_category_id: int,
    *,
    is_primary: bool = True,
) -> int:
    count = 0
    for product_id in _unique_ints(product_ids):
        upsert_product_category_mapping(
            session,
            ProductCategoryMappingUpsert(
                product_id=product_id,
                sales_channel_id=int(sales_channel_id),
                channel_category_id=int(channel_category_id),
                is_primary=is_primary,
            ),
        )
        count += 1
    return count


def bulk_upsert_variant_category_mappings(
    session: Session,
    variant_ids: Iterable[int],
    sales_channel_id: int,
    channel_category_id: int,
    *,
    is_primary: bool = True,
) -> int:
    count = 0
    for variant_id in _unique_ints(variant_ids):
        upsert_variant_category_mapping(
            session,
            VariantCategoryMappingUpsert(
                variant_id=variant_id,
                sales_channel_id=int(sales_channel_id),
                channel_category_id=int(channel_category_id),
                is_primary=is_primary,
            ),
        )
        count += 1
    return count


def bulk_update_products(
    session: Session,
    product_ids: Iterable[int],
    updates: dict[str, object],
    *,
    apply: bool = False,
    only_empty: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, object]:
    product_id_list = _unique_ints(product_ids)
    allowed_fields = {"source_language", "brand_name", "status", "is_chemical"}
    clean_updates = {key: value for key, value in updates.items() if key in allowed_fields and value not in {None, ""}}
    if not product_id_list:
        return {"status": "failed", "updated": 0, "skipped": 0, "errors": 0, "rows": [], "backup_path": None, "message": "Keine Produkte ausgewählt."}
    if not clean_updates:
        return {"status": "failed", "updated": 0, "skipped": 0, "errors": 0, "rows": [], "backup_path": None, "message": "Keine Produktfelder zum Ändern ausgewählt."}

    rows: list[dict[str, object]] = []
    changed_products: list[Product] = []
    brand_cache: dict[str, Brand | None] = {}
    products = list(session.scalars(select(Product).where(Product.id.in_(product_id_list)).order_by(Product.id.asc())))
    products_by_id = {product.id: product for product in products}
    for product_id in product_id_list:
        product = products_by_id.get(product_id)
        if product is None:
            rows.append({"entity_type": "product", "id": product_id, "sku": None, "title": None, "field": "-", "old_value": None, "new_value": None, "status": "error", "message": "Produkt nicht gefunden"})
            continue
        product_changed = False
        for field, new_value in clean_updates.items():
            target_field = "brand" if field == "brand_name" else field
            old_value = product.brand.name if field == "brand_name" and product.brand else getattr(product, target_field, None)
            if only_empty and not _bulk_value_is_empty(old_value):
                rows.append(_bulk_edit_row("product", product, field, old_value, new_value, "skipped", "Bestehender Wert bleibt erhalten."))
                continue
            if str(old_value) == str(new_value):
                rows.append(_bulk_edit_row("product", product, field, old_value, new_value, "skipped", "Wert ist bereits gesetzt."))
                continue
            rows.append(_bulk_edit_row("product", product, field, old_value, new_value, "would_update" if not apply else "updated", ""))
            product_changed = True
        if product_changed:
            changed_products.append(product)

    backup_path = _write_bulk_product_backup(changed_products, backup_dir) if apply and changed_products else None
    if apply:
        for product in changed_products:
            for field, new_value in clean_updates.items():
                old_value = product.brand.name if field == "brand_name" and product.brand else getattr(product, field, None)
                if only_empty and not _bulk_value_is_empty(old_value):
                    continue
                if field == "brand_name":
                    brand_name = str(new_value).strip()
                    if brand_name not in brand_cache:
                        brand_cache[brand_name] = get_or_create_brand(session, brand_name)
                    product.brand = brand_cache[brand_name]
                elif field == "is_chemical":
                    product.is_chemical = bool(new_value)
                else:
                    setattr(product, field, str(new_value).strip())
        session.flush()

    return _bulk_edit_result(rows, backup_path=backup_path)


def bulk_update_variants(
    session: Session,
    variant_ids: Iterable[int],
    updates: dict[str, object],
    *,
    apply: bool = False,
    only_empty: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, object]:
    variant_id_list = _unique_ints(variant_ids)
    allowed_fields = {"status", "currency", "cost_currency", "price", "cost_price", "stock_qty", "barcode", "option_name", "option_value", "packaging"}
    clean_updates = {key: value for key, value in updates.items() if key in allowed_fields and value not in {None, ""}}
    if not variant_id_list:
        return {"status": "failed", "updated": 0, "skipped": 0, "errors": 0, "rows": [], "backup_path": None, "message": "Keine Varianten ausgewählt."}
    if not clean_updates:
        return {"status": "failed", "updated": 0, "skipped": 0, "errors": 0, "rows": [], "backup_path": None, "message": "Keine Variantenfelder zum Ändern ausgewählt."}

    rows: list[dict[str, object]] = []
    changed_variants: list[ProductVariant] = []
    variants = list(session.scalars(select(ProductVariant).where(ProductVariant.id.in_(variant_id_list)).order_by(ProductVariant.id.asc())))
    variants_by_id = {variant.id: variant for variant in variants}
    normalized_updates = _normalize_variant_bulk_updates(clean_updates)
    for variant_id in variant_id_list:
        variant = variants_by_id.get(variant_id)
        if variant is None:
            rows.append({"entity_type": "variant", "id": variant_id, "sku": None, "title": None, "field": "-", "old_value": None, "new_value": None, "status": "error", "message": "Variante nicht gefunden"})
            continue
        variant_changed = False
        for field, new_value in normalized_updates.items():
            old_value = getattr(variant, field, None)
            if only_empty and not _bulk_value_is_empty(old_value):
                rows.append(_bulk_edit_row("variant", variant, field, old_value, new_value, "skipped", "Bestehender Wert bleibt erhalten."))
                continue
            if str(old_value) == str(new_value):
                rows.append(_bulk_edit_row("variant", variant, field, old_value, new_value, "skipped", "Wert ist bereits gesetzt."))
                continue
            rows.append(_bulk_edit_row("variant", variant, field, old_value, new_value, "would_update" if not apply else "updated", ""))
            variant_changed = True
        if variant_changed:
            changed_variants.append(variant)

    backup_path = _write_bulk_variant_backup(changed_variants, backup_dir) if apply and changed_variants else None
    if apply:
        for variant in changed_variants:
            for field, new_value in normalized_updates.items():
                old_value = getattr(variant, field, None)
                if only_empty and not _bulk_value_is_empty(old_value):
                    continue
                setattr(variant, field, new_value)
        session.flush()

    return _bulk_edit_result(rows, backup_path=backup_path)


def _bulk_value_is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _bulk_edit_row(entity_type: str, entity: Product | ProductVariant, field: str, old_value: object, new_value: object, status: str, message: str) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "id": entity.id,
        "sku": entity.sku,
        "title": entity.title if isinstance(entity, Product) else entity.variant_title,
        "field": field,
        "old_value": _bulk_json_value(old_value),
        "new_value": _bulk_json_value(new_value),
        "status": status,
        "message": message,
    }


def _bulk_json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _bulk_edit_result(rows: list[dict[str, object]], *, backup_path: Path | None) -> dict[str, object]:
    updated = sum(1 for row in rows if row.get("status") in {"updated", "would_update"})
    skipped = sum(1 for row in rows if row.get("status") == "skipped")
    errors = sum(1 for row in rows if row.get("status") == "error")
    status = "success" if errors == 0 else "partial_success"
    return {
        "status": status,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "rows": rows,
        "backup_path": str(backup_path) if backup_path else None,
    }


def _normalize_variant_bulk_updates(updates: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for field, value in updates.items():
        if field in {"price", "cost_price"}:
            result[field] = Decimal(str(value))
        elif field == "stock_qty":
            result[field] = int(float(str(value)))
        elif field in {"currency", "cost_currency"}:
            result[field] = str(value).strip().upper()
        else:
            result[field] = str(value).strip()
    return result


def _write_bulk_product_backup(products: list[Product], backup_dir: Path | None = None) -> Path:
    root = backup_dir or Path("/opt/output/bulk_edit_backups")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"product_bulk_edit_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    payload = [
        {
            "id": product.id,
            "sku": product.sku,
            "title": product.title,
            "source_language": product.source_language,
            "brand": product.brand.name if product.brand else None,
            "status": product.status,
            "is_chemical": product.is_chemical,
            "updated_at": product.updated_at.isoformat() if product.updated_at else None,
        }
        for product in products
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_bulk_variant_backup(variants: list[ProductVariant], backup_dir: Path | None = None) -> Path:
    root = backup_dir or Path("/opt/output/bulk_edit_backups")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"variant_bulk_edit_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    payload = [
        {
            "id": variant.id,
            "product_id": variant.product_id,
            "sku": variant.sku,
            "variant_title": variant.variant_title,
            "option_name": variant.option_name,
            "option_value": variant.option_value,
            "packaging": variant.packaging,
            "price": str(variant.price) if variant.price is not None else None,
            "currency": variant.currency,
            "cost_price": str(variant.cost_price) if variant.cost_price is not None else None,
            "cost_currency": variant.cost_currency,
            "stock_qty": variant.stock_qty,
            "barcode": variant.barcode,
            "status": variant.status,
            "updated_at": variant.updated_at.isoformat() if variant.updated_at else None,
        }
        for variant in variants
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def upsert_product_with_variant(
    session: Session,
    sku: str,
    family_key: str | None,
    source_language: str,
    title: str,
    description: str | None,
    source_url: str | None,
    source_url_final: str | None,
    specifications_text: str | None,
    technical_features_text: str | None,
    brand_name: str | None,
    status: str,
    variant_sku: str,
    variant_title: str | None,
    option_name: str | None,
    option_value: str | None,
    packaging: str | None,
    price: Decimal | None,
    currency: str | None,
    cost_price: Decimal | None,
    cost_currency: str | None,
    barcode: str | None,
    stock_qty: int = 0,
    **chemical_fields: object,
) -> tuple[Product, ProductVariant]:
    brand = get_or_create_brand(session, brand_name)
    product = None
    if family_key:
        product = session.scalar(select(Product).where(Product.sku == family_key))
        if product is None:
            family_products = list(session.scalars(select(Product).where(Product.family_key == family_key)))
            if len(family_products) == 1:
                product = family_products[0]
    if product is None:
        product = session.scalar(select(Product).where(Product.sku == sku))
    handle = _unique_handle(session, title or sku, product_id=product.id if product else None)
    if product is None:
        product = Product(
            sku=sku,
            family_key=family_key,
            handle=handle,
            source_language=source_language or "en",
            title=title or sku,
            description=description,
            source_url=source_url,
            source_url_final=source_url_final,
            specifications_text=specifications_text,
            technical_features_text=technical_features_text,
            brand=brand,
            status=status,
            **_serialize_import_chemical_payload(chemical_fields),
            **{field_name: chemical_fields[field_name] for field_name in CHEMICAL_ENRICHMENT_FIELD_NAMES if field_name in chemical_fields},
        )
        session.add(product)
        session.flush()
    else:
        product.sku = sku
        product.family_key = family_key or product.family_key
        product.handle = handle
        product.source_language = source_language or product.source_language or "en"
        product.title = title or sku
        product.description = description
        product.source_url = source_url or product.source_url
        product.source_url_final = source_url_final or product.source_url_final
        product.specifications_text = specifications_text or product.specifications_text
        product.technical_features_text = technical_features_text or product.technical_features_text
        product.brand = brand
        product.status = status
        for field_name, value in _serialize_import_chemical_payload(chemical_fields).items():
            setattr(product, field_name, value)
        for field_name in CHEMICAL_ENRICHMENT_FIELD_NAMES:
            if field_name in chemical_fields:
                setattr(product, field_name, chemical_fields[field_name])

    variant = session.scalar(select(ProductVariant).where(ProductVariant.sku == variant_sku))
    if variant is None:
        variant = ProductVariant(
            product=product,
            sku=variant_sku,
            variant_title=variant_title,
            option_name=option_name,
            option_value=option_value,
            packaging=packaging,
            price=price,
            currency=currency,
            cost_price=cost_price,
            cost_currency=cost_currency,
            barcode=barcode,
            stock_qty=stock_qty,
            status="active",
        )
        session.add(variant)
        session.flush()
    else:
        variant.product = product
        variant.variant_title = variant_title
        if option_name:
            variant.option_name = option_name
        if option_value:
            variant.option_value = option_value
        if packaging:
            variant.packaging = packaging
        if price is not None:
            variant.price = price
        if currency:
            variant.currency = currency
        if cost_price is not None:
            variant.cost_price = cost_price
        if cost_currency:
            variant.cost_currency = cost_currency
        variant.barcode = barcode
        variant.stock_qty = stock_qty

    return product, variant


def create_product(session: Session, payload: ProductCreate, variant_payload: VariantCreate | None = None) -> tuple[Product, ProductVariant | None]:
    brand = get_or_create_brand(session, payload.brand_name)
    handle = _unique_handle(session, payload.handle or payload.title or payload.sku)
    product = Product(
        sku=payload.sku,
        family_key=payload.family_key,
        source_language=payload.source_language or "en",
        handle=handle,
        title=payload.title,
        description=payload.description,
        brand=brand,
        status=payload.status,
        is_chemical=payload.is_chemical,
        chemical_type=payload.chemical_type,
        ufi=payload.ufi,
        voc_content_percent=payload.voc_content_percent,
        cas_number=payload.cas_number,
        ec_number=payload.ec_number,
        un_number=payload.un_number,
        hazard_class=payload.hazard_class,
        packing_group=payload.packing_group,
        adr_relevant=payload.adr_relevant,
        ghs_pictograms=payload.ghs_pictograms,
        signal_word=payload.signal_word,
        chemical_safety_json=payload.chemical_safety_json,
        hazard_statements=payload.hazard_statements,
        precautionary_statements=payload.precautionary_statements,
        wgk=normalize_wgk(payload.wgk) if payload.wgk else None,
        wgk_label=payload.wgk_label or wgk_label(payload.wgk),
        wgk_source_section=payload.wgk_source_section,
        wgk_source_url=payload.wgk_source_url,
        wgk_source_asset_id=payload.wgk_source_asset_id,
        wgk_confidence=payload.wgk_confidence,
        storage_class=normalize_storage_class(payload.storage_class) if payload.storage_class else None,
        storage_class_label=payload.storage_class_label or storage_class_label(payload.storage_class),
        storage_class_source_section=payload.storage_class_source_section,
        storage_class_source_url=payload.storage_class_source_url,
        storage_class_source_asset_id=payload.storage_class_source_asset_id,
        storage_class_confidence=payload.storage_class_confidence,
        sds_available=payload.sds_available,
        sds_url=payload.sds_url,
        sds_asset_id=payload.sds_asset_id,
        chemical_reference_url=payload.chemical_reference_url,
        chemical_enrichment_status=payload.chemical_enrichment_status,
        chemical_enrichment_error=payload.chemical_enrichment_error,
        density=payload.density,
        color=payload.color,
        odor=payload.odor,
        ph_value=payload.ph_value,
        flash_point=payload.flash_point,
        boiling_point=payload.boiling_point,
        viscosity=payload.viscosity,
        solubility=payload.solubility,
        business_only=payload.business_only,
        age_check_required=payload.age_check_required,
        shippable=payload.shippable,
        limited_quantity=payload.limited_quantity,
        hazard_shipping_note=payload.hazard_shipping_note,
        shop_active=payload.shop_active,
    )
    session.add(product)
    session.flush()

    variant = None
    if variant_payload is not None:
        variant = ProductVariant(
            product_id=product.id,
            sku=variant_payload.sku,
            variant_title=variant_payload.variant_title,
            option_name=variant_payload.option_name,
            option_value=variant_payload.option_value,
            packaging=variant_payload.packaging,
            price=variant_payload.price,
            currency=variant_payload.currency,
            cost_price=variant_payload.cost_price,
            cost_currency=variant_payload.cost_currency,
            stock_qty=variant_payload.stock_qty,
            barcode=variant_payload.barcode,
            status=variant_payload.status or "active",
        )
        session.add(variant)
        session.flush()
    return product, variant


def set_product_categories(session: Session, product: Product, category_ids: list[int]) -> None:
    set_product_categories_for_channel(
        session,
        product,
        category_ids,
        sales_channel_code=DEFAULT_CATEGORY_CHANNEL_CODE,
    )


def set_product_categories_for_channel(
    session: Session,
    product: Product,
    category_ids: list[int],
    sales_channel_code: str | None = None,
) -> None:
    channel = get_sales_channel_by_code(session, sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)
    target_channel_id = int(channel.id)
    normalized_category_ids: list[int] = []
    seen_category_ids: set[int] = set()
    for raw_category_id in category_ids:
        category_id = int(raw_category_id)
        if category_id in seen_category_ids:
            continue
        seen_category_ids.add(category_id)
        normalized_category_ids.append(category_id)
    existing_rows = list(
        session.scalars(
            select(ProductCategoryAssignment).where(
                ProductCategoryAssignment.product_id == product.id,
                ProductCategoryAssignment.sales_channel_id == target_channel_id,
            )
        )
    )
    for row in existing_rows:
        session.delete(row)
    session.flush()
    if not normalized_category_ids:
        return
    for index, category_id in enumerate(normalized_category_ids):
        category = session.get(Category, int(category_id))
        if category is None:
            raise ValueError("Kategorie nicht gefunden")
        if int(category.sales_channel_id) != target_channel_id:
            raise ValueError("Kategorie gehört nicht zum gewählten Kanal")
        session.add(
            ProductCategoryAssignment(
                product_id=product.id,
                category_id=category.id,
                sales_channel_id=target_channel_id,
                sort_order=index,
            )
        )
    session.flush()


def list_product_category_assignments(session: Session, product_id: int) -> list[dict]:
    stmt = (
        select(ProductCategoryAssignment)
        .options(
            joinedload(ProductCategoryAssignment.category).joinedload(Category.sales_channel),
            joinedload(ProductCategoryAssignment.sales_channel),
        )
        .where(ProductCategoryAssignment.product_id == product_id)
        .order_by(ProductCategoryAssignment.sales_channel_id.asc(), ProductCategoryAssignment.id.asc())
    )
    rows = list(session.scalars(stmt).unique())
    grouped: dict[int, dict] = {}
    for row in rows:
        channel = row.sales_channel or (row.category.sales_channel if row.category else None)
        if channel is None:
            continue
        bucket = grouped.setdefault(
            int(channel.id),
            {
                "sales_channel_id": channel.id,
                "sales_channel_code": channel.code,
                "sales_channel_name": channel.name,
                "category_ids": [],
                "categories": [],
            },
        )
        bucket["category_ids"].append(row.category_id)
        if row.category:
            bucket["categories"].append(row.category.name)
    return list(grouped.values())


def get_product_category_assignment_for_channel(
    session: Session,
    product_id: int,
    sales_channel_code: str | None,
) -> dict:
    channel = get_sales_channel_by_code(session, sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)
    assignments = list_product_category_assignments(session, product_id)
    row = next((item for item in assignments if item["sales_channel_id"] == channel.id), None)
    if row:
        return row
    return {
        "sales_channel_id": channel.id,
        "sales_channel_code": channel.code,
        "sales_channel_name": channel.name,
        "category_ids": [],
        "categories": [],
    }


def update_product(session: Session, product_id: int, payload: ProductUpdate) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Product not found")
    if payload.sku is not None:
        normalized_sku = payload.sku.strip()
        if not normalized_sku:
            raise ValueError("SKU darf nicht leer sein")
        existing = session.scalar(select(Product).where(Product.sku == normalized_sku, Product.id != product_id))
        if existing is not None:
            raise ValueError("SKU ist bereits vergeben")
        product.sku = normalized_sku
    product.title = payload.title
    product.handle = _unique_handle(session, payload.title or product.sku, product_id=product.id)
    product.source_language = payload.source_language or product.source_language or "en"
    product.description = payload.description
    product.source_url = payload.source_url
    product.source_url_final = payload.source_url_final
    product.status = payload.status
    product.brand = get_or_create_brand(session, payload.brand_name)
    set_product_categories_for_channel(
        session,
        product,
        payload.category_ids,
        sales_channel_code=payload.category_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
    )
    provided_fields = getattr(payload, "model_fields_set", set())
    if "wgk" in provided_fields:
        payload.wgk = normalize_wgk(payload.wgk) if payload.wgk else None
        payload.wgk_label = payload.wgk_label or wgk_label(payload.wgk)
    if "storage_class" in provided_fields:
        payload.storage_class = normalize_storage_class(payload.storage_class) if payload.storage_class else None
        payload.storage_class_label = payload.storage_class_label or storage_class_label(payload.storage_class)
    if "chemical_safety_json" in provided_fields or "wgk" in provided_fields or "storage_class" in provided_fields:
        payload.chemical_safety_json = build_chem_safety_payload(
            payload.chemical_safety_json if "chemical_safety_json" in provided_fields else product.chemical_safety_json,
            wgk=payload.wgk if "wgk" in provided_fields else product.wgk,
            storage_class=payload.storage_class if "storage_class" in provided_fields else product.storage_class,
        )
        provided_fields = set(provided_fields)
        provided_fields.add("chemical_safety_json")
    for field_name in CHEMICAL_FIELD_NAMES:
        if field_name in provided_fields:
            setattr(product, field_name, getattr(payload, field_name))
    for field_name in CHEMICAL_ENRICHMENT_FIELD_NAMES:
        if field_name in provided_fields:
            setattr(product, field_name, getattr(payload, field_name))
    session.flush()
    return product


def update_variant(session: Session, variant_id: int, payload: VariantUpdate) -> ProductVariant:
    variant = session.get(ProductVariant, variant_id)
    if variant is None:
        raise ValueError("Variant not found")
    variant.variant_title = payload.variant_title
    variant.option_name = payload.option_name
    variant.option_value = payload.option_value
    variant.packaging = payload.packaging
    variant.price = payload.price
    variant.currency = payload.currency
    variant.cost_price = payload.cost_price
    variant.cost_currency = payload.cost_currency
    variant.stock_qty = payload.stock_qty
    variant.barcode = payload.barcode
    if payload.status:
        variant.status = _normalize_variant_status(payload.status)
    _sync_base_tier_from_variant(session, variant, "sale")
    _sync_base_tier_from_variant(session, variant, "purchase")
    session.flush()
    return variant


def archive_variant(session: Session, variant_id: int) -> ProductVariant:
    variant = session.get(ProductVariant, variant_id)
    if variant is None:
        raise ValueError("Variant not found")
    variant.status = "archived"
    session.flush()
    return variant


def archive_variants(session: Session, variant_ids: list[int]) -> int:
    count = 0
    for variant_id in variant_ids:
        archive_variant(session, int(variant_id))
        count += 1
    return count


def delete_or_archive_variant(session: Session, variant_id: int) -> tuple[str, ProductVariant | None]:
    variant = (
        session.query(ProductVariant)
        .options(
            joinedload(ProductVariant.assets),
            joinedload(ProductVariant.price_tiers),
            joinedload(ProductVariant.channel_listings),
            joinedload(ProductVariant.channel_category_mappings),
            joinedload(ProductVariant.translations),
        )
        .filter(ProductVariant.id == int(variant_id))
        .one_or_none()
    )
    if variant is None:
        raise ValueError("Variant not found")
    has_relations = any(
        [
            variant.price is not None,
            variant.cost_price is not None,
            bool(variant.assets),
            bool(variant.price_tiers),
            bool(variant.channel_listings),
            bool(variant.channel_category_mappings),
            bool(variant.translations),
        ]
    )
    if has_relations:
        variant.status = "archived"
        session.flush()
        return "archived_due_to_relations", variant
    session.delete(variant)
    session.flush()
    return "deleted", None


def delete_or_archive_variants(session: Session, variant_ids: list[int]) -> dict[str, int]:
    result = {"deleted": 0, "archived_due_to_relations": 0}
    for variant_id in variant_ids:
        action, _variant = delete_or_archive_variant(session, int(variant_id))
        result[action] = result.get(action, 0) + 1
    return result


def upsert_variant_price_tier(session: Session, payload: VariantPriceTierCreate) -> ProductVariantPriceTier:
    tier = session.scalar(
        select(ProductVariantPriceTier).where(
            ProductVariantPriceTier.variant_id == payload.variant_id,
            ProductVariantPriceTier.price_type == payload.price_type,
            ProductVariantPriceTier.currency == payload.currency,
            ProductVariantPriceTier.min_qty == payload.min_qty,
            ProductVariantPriceTier.max_qty == payload.max_qty,
        )
    )
    if tier is None:
        tier = ProductVariantPriceTier(**payload.model_dump())
        session.add(tier)
    else:
        tier.price = payload.price
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is not None:
        _sync_variant_from_base_tier(variant, tier)
    session.flush()
    return tier


def update_variant_price_tier(session: Session, tier_id: int, payload: VariantPriceTierCreate) -> ProductVariantPriceTier:
    tier = session.get(ProductVariantPriceTier, tier_id)
    if tier is None:
        raise ValueError("Price tier not found")
    tier.variant_id = payload.variant_id
    tier.price_type = payload.price_type
    tier.min_qty = payload.min_qty
    tier.max_qty = payload.max_qty
    tier.price = payload.price
    tier.currency = payload.currency
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is not None:
        _sync_variant_from_base_tier(variant, tier)
    session.flush()
    return tier


def delete_variant_price_tier(session: Session, tier_id: int) -> None:
    tier = session.get(ProductVariantPriceTier, tier_id)
    if tier is None:
        raise ValueError("Price tier not found")
    variant = tier.variant
    session.delete(tier)
    if variant is not None:
        _rebuild_variant_base_price_from_tiers(variant)
    session.flush()


def _base_tier(variant: ProductVariant, price_type: str, currency: str | None) -> ProductVariantPriceTier | None:
    for tier in variant.price_tiers:
        if tier.price_type != price_type:
            continue
        if tier.min_qty != 1:
            continue
        if tier.max_qty is not None:
            continue
        if currency and tier.currency != currency:
            continue
        return tier
    return None


def _sync_base_tier_from_variant(session: Session, variant: ProductVariant, price_type: str) -> None:
    if price_type == "sale":
        amount = variant.price
        currency = variant.currency
    else:
        amount = variant.cost_price
        currency = variant.cost_currency
    if amount is None or not currency:
        return
    tier = _base_tier(variant, price_type, currency)
    if tier is None:
        tier = ProductVariantPriceTier(
            variant_id=variant.id,
            price_type=price_type,
            min_qty=1,
            max_qty=None,
            price=amount,
            currency=currency,
        )
        session.add(tier)
    else:
        tier.price = amount
        tier.currency = currency


def _sync_variant_from_base_tier(variant: ProductVariant, tier: ProductVariantPriceTier) -> None:
    if tier.min_qty != 1:
        return
    if tier.max_qty is not None:
        return
    if tier.price_type == "sale":
        variant.price = tier.price
        variant.currency = tier.currency
    elif tier.price_type == "purchase":
        variant.cost_price = tier.price
        variant.cost_currency = tier.currency


def _rebuild_variant_base_price_from_tiers(variant: ProductVariant) -> None:
    sale_candidates = [tier for tier in variant.price_tiers if tier.price_type == "sale" and tier.min_qty == 1 and tier.max_qty is None]
    purchase_candidates = [tier for tier in variant.price_tiers if tier.price_type == "purchase" and tier.min_qty == 1 and tier.max_qty is None]
    if sale_candidates:
        sale_candidates.sort(key=lambda tier: tier.id)
        variant.price = sale_candidates[0].price
        variant.currency = sale_candidates[0].currency
    if purchase_candidates:
        purchase_candidates.sort(key=lambda tier: tier.id)
        variant.cost_price = purchase_candidates[0].price
        variant.cost_currency = purchase_candidates[0].currency


def margin_metrics(variant: ProductVariant) -> tuple[float | None, float | None]:
    if variant.price is None or variant.cost_price is None:
        return None, None
    sale = Decimal(variant.price)
    cost = Decimal(variant.cost_price)
    margin_amount = sale - cost
    if sale == 0:
        return float(margin_amount), None
    return float(margin_amount), float(round((margin_amount / sale) * Decimal("100"), 2))


def _matching_purchase_price(variant: ProductVariant, sale_tier: ProductVariantPriceTier) -> Decimal | None:
    purchase_tiers = [tier for tier in variant.price_tiers if tier.price_type == "purchase" and tier.currency == sale_tier.currency]
    matching = [
        tier
        for tier in purchase_tiers
        if tier.min_qty <= sale_tier.min_qty and (tier.max_qty is None or tier.max_qty >= sale_tier.min_qty)
    ]
    if matching:
        matching.sort(key=lambda item: item.min_qty, reverse=True)
        return Decimal(matching[0].price)
    if variant.cost_price is not None and (variant.cost_currency or sale_tier.currency) == sale_tier.currency:
        return Decimal(variant.cost_price)
    return None


def tier_margin_metrics(variant: ProductVariant, tier: ProductVariantPriceTier) -> tuple[float | None, float | None]:
    if tier.price_type != "sale":
        return None, None
    cost = _matching_purchase_price(variant, tier)
    if cost is None:
        return None, None
    sale = Decimal(tier.price)
    margin_amount = sale - cost
    if sale == 0:
        return float(margin_amount), None
    return float(margin_amount), float(round((margin_amount / sale) * Decimal("100"), 2))


def _serialize_price_tier(variant: ProductVariant, tier: ProductVariantPriceTier) -> dict:
    margin_amount, margin_percent = tier_margin_metrics(variant, tier)
    total_margin_amount = None
    if margin_amount is not None:
        total_margin_amount = round(margin_amount * tier.min_qty, 2)
    return {
        "id": tier.id,
        "price_type": tier.price_type,
        "min_qty": tier.min_qty,
        "max_qty": tier.max_qty,
        "price": float(tier.price),
        "currency": tier.currency,
        "margin_amount": margin_amount,
        "total_margin_amount": total_margin_amount,
        "margin_percent": margin_percent,
    }


def _sorted_price_tiers(variant: ProductVariant) -> list[ProductVariantPriceTier]:
    return sorted(
        list(variant.price_tiers),
        key=lambda tier: (
            0 if tier.price_type == "sale" else 1,
            tier.min_qty,
            0 if tier.max_qty is None else 1,
            tier.max_qty if tier.max_qty is not None else 0,
            tier.id,
        ),
    )


def create_or_update_translation(session: Session, payload: ProductTranslationCreate) -> ProductTranslation:
    translation = session.scalar(
        select(ProductTranslation).where(
            ProductTranslation.product_id == payload.product_id,
            ProductTranslation.language_code == payload.language_code,
        )
    )
    if translation is None:
        translation = ProductTranslation(**_clean_translation_payload(payload.model_dump()))
        session.add(translation)
    else:
        cleaned = _clean_translation_payload(payload.model_dump())
        translation.title = cleaned["title"]
        for field in ("short_description", "description", "seo_title", "seo_description", "slug"):
            value = cleaned.get(field)
            if value is not None:
                setattr(translation, field, value)
    translation.translation_status = payload.translation_status or translation.translation_status or "draft"
    if payload.source_language_code is not None:
        translation.source_language_code = payload.source_language_code
    if payload.provider is not None:
        translation.provider = payload.provider
    if payload.model is not None:
        translation.model = payload.model
    if payload.prompt_used is not None:
        translation.prompt_used = payload.prompt_used
    if payload.translation_status == "generated":
        translation.generated_at = datetime.now(timezone.utc)
    session.flush()
    return translation


def _clean_translation_payload(payload: dict[str, object]) -> dict[str, object]:
    cleaned = dict(payload)
    for field in ("language_code", "title", "short_description", "description", "seo_title", "seo_description", "slug"):
        if field in cleaned and isinstance(cleaned[field], str):
            cleaned[field] = cleaned[field].strip()
    # Empty optional fields mean "not provided" in the manual editor. This avoids
    # wiping existing translations when a field is not part of the current edit.
    for field in ("short_description", "description", "seo_title", "seo_description", "slug"):
        if cleaned.get(field) == "":
            cleaned[field] = None
    return cleaned


def set_product_translation_short_description(
    session: Session,
    product_id: int,
    language_code: str,
    title: str,
    short_description: str | None,
) -> ProductTranslation:
    normalized_language = (language_code or "en").strip()
    translation = session.scalar(
        select(ProductTranslation).where(
            ProductTranslation.product_id == product_id,
            ProductTranslation.language_code == normalized_language,
        )
    )
    if translation is None:
        translation = ProductTranslation(
            product_id=product_id,
            language_code=normalized_language,
            title=title,
            short_description=short_description,
        )
        session.add(translation)
    else:
        if title and not translation.title:
            translation.title = title
        translation.short_description = short_description
    session.flush()
    return translation


def create_or_update_variant_translation(session: Session, payload: VariantTranslationCreate) -> VariantTranslation:
    cleaned = _clean_variant_translation_payload(payload.model_dump())
    translation = session.scalar(
        select(VariantTranslation).where(
            VariantTranslation.variant_id == cleaned["variant_id"],
            VariantTranslation.language_code == cleaned["language_code"],
        )
    )
    if translation is None:
        translation = VariantTranslation(**cleaned)
        session.add(translation)
    else:
        translation.title = cleaned["title"]
        for field in ("option_label_override", "package_label"):
            value = cleaned.get(field)
            if value is not None:
                setattr(translation, field, value)
    session.flush()
    return translation


def update_variant_translation_by_id(
    session: Session,
    translation_id: int,
    payload: VariantTranslationCreate,
) -> VariantTranslation:
    translation = session.get(VariantTranslation, int(translation_id))
    if translation is None:
        raise ValueError(f"Varianten-Übersetzung {translation_id} nicht gefunden.")
    cleaned = _clean_variant_translation_payload(payload.model_dump())
    translation.title = cleaned["title"]
    for field in ("option_label_override", "package_label"):
        value = cleaned.get(field)
        if value is not None:
            setattr(translation, field, value)
    session.flush()
    return translation


def _clean_variant_translation_payload(payload: dict[str, object]) -> dict[str, object]:
    cleaned = dict(payload)
    for field in ("language_code", "title", "option_label_override", "package_label"):
        if field in cleaned and isinstance(cleaned[field], str):
            cleaned[field] = cleaned[field].strip()
    for field in ("option_label_override", "package_label"):
        if cleaned.get(field) == "":
            cleaned[field] = None
    return cleaned


def dashboard_counts(session: Session) -> dict[str, int]:
    return {
        "products": session.scalar(select(func.count()).select_from(Product)) or 0,
        "variants": session.scalar(select(func.count()).select_from(ProductVariant)) or 0,
        "assets": session.scalar(select(func.count()).select_from(Asset)) or 0,
        "import_jobs": session.scalar(select(func.count()).select_from(ImportJob)) or 0,
    }


def get_product_detail(session: Session, product_id: int) -> dict | None:
    stmt = (
        select(Product)
        .options(
            joinedload(Product.brand),
            joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
            joinedload(Product.variants).joinedload(ProductVariant.channel_listings).joinedload(VariantChannelListing.sales_channel),
            joinedload(Product.variants).joinedload(ProductVariant.channel_category_mappings).joinedload(VariantCategoryMapping.sales_channel),
            joinedload(Product.variants).joinedload(ProductVariant.channel_category_mappings).joinedload(VariantCategoryMapping.channel_category),
            joinedload(Product.variants).joinedload(ProductVariant.translations),
            joinedload(Product.assets).joinedload(Asset.variant),
            joinedload(Product.translations),
            joinedload(Product.category_links).joinedload(ProductCategoryAssignment.category).joinedload(Category.sales_channel),
            joinedload(Product.category_links).joinedload(ProductCategoryAssignment.sales_channel),
            joinedload(Product.channel_listings).joinedload(ProductChannelListing.sales_channel),
            joinedload(Product.channel_category_mappings).joinedload(ProductCategoryMapping.sales_channel),
            joinedload(Product.channel_category_mappings).joinedload(ProductCategoryMapping.channel_category),
            joinedload(Product.chemical_enrichments),
            joinedload(Product.chemical_documents),
            joinedload(Product.sdb_record),
        )
        .execution_options(populate_existing=True)
        .where(Product.id == product_id)
    )
    product = session.scalars(stmt).unique().first()
    if product is None:
        return None
    ordered_assets = sorted(product.assets, key=lambda asset: (asset.sort_order, asset.id))
    inferred_sds_asset = _inferred_sds_asset(ordered_assets)
    chemical_fields = _serialize_chemical_fields(product)
    if not chemical_fields.get("sds_asset_id") and inferred_sds_asset is not None:
        chemical_fields["sds_asset_id"] = inferred_sds_asset.id
    if inferred_sds_asset is not None:
        chemical_fields["sds_available"] = bool(chemical_fields.get("sds_available") or True)
    sdb_by_asset_id = {
        document.asset_id: _serialize_asset_sdb_document(document)
        for document in product.chemical_documents
        if document.asset_id
    }
    default_category_assignment = get_product_category_assignment_for_channel(session, product.id, DEFAULT_CATEGORY_CHANNEL_CODE)
    category_assignments = list_product_category_assignments(session, product.id)
    variant_ids = [variant.id for variant in product.variants if variant.id is not None]
    medusa_mappings = _medusa_mappings_for_product(session, product.id, variant_ids)
    product_medusa_mapping = medusa_mappings.get(("product", product.id), {})
    return {
        "id": product.id,
        "sku": product.sku,
        "family_key": product.family_key,
        "handle": product.handle,
        "source_language": product.source_language,
        "title": product.title,
        "description": product.description,
        "source_url": product.source_url,
        "source_url_final": product.source_url_final,
        "specifications_text": product.specifications_text,
        "technical_features_text": product.technical_features_text,
        "brand_name": product.brand.name if product.brand else None,
        "status": product.status,
        "category_ids": default_category_assignment["category_ids"],
        "categories": default_category_assignment["categories"],
        "category_channel_id": default_category_assignment["sales_channel_id"],
        "category_channel_code": default_category_assignment["sales_channel_code"],
        "category_channel_name": default_category_assignment["sales_channel_name"],
        "category_assignments": category_assignments,
        **_serialize_medusa_mapping(product_medusa_mapping, prefix="medusa"),
        **chemical_fields,
        **_serialize_chemical_enrichment_fields(product),
        "chemical_enrichments": [
            _serialize_chemical_enrichment(enrichment)
            for enrichment in product.chemical_enrichments
        ],
        "sdb": _serialize_product_sdb(product.sdb_record),
        "variants": [
            {
                "id": variant.id,
                "sku": variant.sku,
                "variant_title": variant.variant_title,
                "option_name": variant.option_name,
                "option_value": variant.option_value,
                "packaging": variant.packaging,
                "price": float(variant.price) if variant.price is not None else None,
                "cost_price": float(variant.cost_price) if variant.cost_price is not None else None,
                "currency": variant.currency,
                "cost_currency": variant.cost_currency,
                "margin_amount": margin_metrics(variant)[0],
                "margin_percent": margin_metrics(variant)[1],
                "stock_qty": variant.stock_qty,
                "barcode": variant.barcode,
                "status": variant.status,
                **_serialize_medusa_mapping(medusa_mappings.get(("variant", variant.id), {}), prefix="medusa"),
                "price_tiers": [_serialize_price_tier(variant, tier) for tier in _sorted_price_tiers(variant)],
                "channel_listings": [
                    _serialize_variant_channel_listing(listing, listing.sales_channel, variant)
                    for listing in sorted(variant.channel_listings, key=lambda item: item.sales_channel.sort_order if item.sales_channel else 0)
                    if listing.sales_channel is not None
                ],
                "translations": [
                    {
                        "id": translation.id,
                        "language_code": translation.language_code,
                        "title": translation.title,
                        "option_label_override": translation.option_label_override,
                        "package_label": translation.package_label,
                    }
                    for translation in variant.translations
                ],
            }
            for variant in product.variants
        ],
        "assets": [
            {
                "id": asset.id,
                "product_id": product.id,
                "product_sku": product.sku,
                "product_title": product.title,
                "variant_id": asset.variant_id,
                "variant_sku": asset.variant.sku if asset.variant else None,
                "variant_title": asset.variant.variant_title if asset.variant else None,
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "source_url": asset.source_url,
                "storage_path": asset.storage_path,
                "file_size": asset.file_size,
                "width": asset.width,
                "height": asset.height,
                "sort_order": asset.sort_order,
                **_asset_sdb_fields(sdb_by_asset_id.get(asset.id)),
            }
            for asset in ordered_assets
        ],
        "translations": [
            {
                "id": translation.id,
                "language_code": translation.language_code,
                "title": translation.title,
                "short_description": translation.short_description,
                "description": translation.description,
                "seo_title": translation.seo_title,
                "seo_description": translation.seo_description,
                "slug": translation.slug,
                "translation_status": translation.translation_status,
                "source_language_code": translation.source_language_code,
                "provider": translation.provider,
                "model": translation.model,
                "prompt_used": translation.prompt_used,
                "generated_at": translation.generated_at.isoformat() if translation.generated_at else None,
            }
            for translation in product.translations
        ],
        "channel_listings": list_product_channel_listings(session, product.id),
        "channel_category_mappings": list_product_category_mappings(session, product.id),
        "variant_channel_listings": list_variant_channel_listings(session, product.id),
        "variant_category_mappings": list_variant_category_mappings(session, product.id),
    }


def _medusa_mappings_for_product(session: Session, product_id: int, variant_ids: list[int]) -> dict[tuple[str, int], dict]:
    ids_by_type: dict[str, list[int]] = {"product": [product_id]}
    if variant_ids:
        ids_by_type["variant"] = variant_ids
    rows = []
    for entity_type, local_ids in ids_by_type.items():
        rows.extend(
            session.scalars(
                select(MedusaSyncMapping)
                .where(
                    MedusaSyncMapping.entity_type == entity_type,
                    MedusaSyncMapping.local_entity_id.in_(local_ids),
                )
                .order_by(MedusaSyncMapping.connection_id.asc(), MedusaSyncMapping.id.desc())
            ).all()
        )
    mappings: dict[tuple[str, int], dict] = {}
    for row in rows:
        key = (row.entity_type, row.local_entity_id)
        if key in mappings and mappings[key].get("status") == "active":
            continue
        mappings[key] = {
            "id": row.id,
            "connection_id": row.connection_id,
            "medusa_id": row.medusa_id,
            "medusa_parent_id": row.medusa_parent_id,
            "medusa_handle": row.medusa_handle,
            "medusa_sku": row.medusa_sku,
            "medusa_external_id": row.medusa_external_id,
            "status": row.status,
            "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
            "last_seen_in_medusa_at": row.last_seen_in_medusa_at.isoformat() if row.last_seen_in_medusa_at else None,
        }
    return mappings


def _serialize_medusa_mapping(mapping: dict, *, prefix: str) -> dict:
    return {
        f"{prefix}_mapping_id": mapping.get("id"),
        f"{prefix}_connection_id": mapping.get("connection_id"),
        f"{prefix}_id": mapping.get("medusa_id"),
        f"{prefix}_parent_id": mapping.get("medusa_parent_id"),
        f"{prefix}_handle": mapping.get("medusa_handle"),
        f"{prefix}_sku": mapping.get("medusa_sku"),
        f"{prefix}_external_id": mapping.get("medusa_external_id"),
        f"{prefix}_mapping_status": mapping.get("status") or "not_mapped",
        f"{prefix}_last_synced_at": mapping.get("last_synced_at"),
        f"{prefix}_last_seen_in_medusa_at": mapping.get("last_seen_in_medusa_at"),
    }


def list_products(session: Session, archive_filter: str = "active") -> list[dict]:
    stmt = (
        select(Product)
        .options(
            joinedload(Product.brand),
            joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
            joinedload(Product.category_links),
            joinedload(Product.assets),
        )
        .order_by(Product.updated_at.desc())
    )
    normalized_filter = (archive_filter or "active").strip().lower()
    if normalized_filter in {"active", "aktive", "non_archived"}:
        stmt = stmt.where(func.lower(Product.status).notin_(["archived", "archiviert"]))
    elif normalized_filter in {"archived", "archiviert"}:
        stmt = stmt.where(func.lower(Product.status).in_(["archived", "archiviert"]))
    rows = []
    for product in session.scalars(stmt).unique():
        variants = list(product.variants)
        image_assets = [asset for asset in product.assets if (asset.mime_type or "").startswith("image/")]
        primary_asset = min(image_assets, key=lambda asset: (asset.sort_order, asset.id), default=None)
        normal_variants = [variant for variant in variants if variant.price is not None]
        cheapest_variant = min(normal_variants, key=lambda variant: variant.price) if normal_variants else None
        sale_tier_candidates = [
            tier
            for variant in variants
            for tier in variant.price_tiers
            if tier.price_type == "sale" and tier.price is not None
        ]
        cheapest_sale_tier = min(sale_tier_candidates, key=lambda tier: tier.price) if sale_tier_candidates else None
        color_values = sorted({variant.option_value for variant in product.variants if variant.option_value})
        rows.append(
            {
                "id": product.id,
                "variant_nav": "V",
                "sku": product.sku,
                "handle": product.handle,
                "family_key": product.family_key,
                "source_language": product.source_language,
                "colors": ", ".join(color_values) if color_values else None,
                "title": product.title,
                "photo_asset_id": primary_asset.id if primary_asset else None,
                "photo_url": f"/asset-file/{primary_asset.id}" if primary_asset else None,
                "photo_thumb_url": f"/asset-thumb/{primary_asset.id}" if primary_asset else None,
                "photo_filename": primary_asset.filename if primary_asset else None,
                "photo_mime_type": primary_asset.mime_type if primary_asset else None,
                "brand": product.brand.name if product.brand else None,
                "status": product.status,
                "variant_count": len(variants),
                "source_url": product.source_url_final or product.source_url,
                "is_chemical": product.is_chemical,
                "price_from": float(cheapest_sale_tier.price) if cheapest_sale_tier else (float(cheapest_variant.price) if cheapest_variant and cheapest_variant.price is not None else None),
                "price": float(cheapest_variant.price) if cheapest_variant and cheapest_variant.price is not None else None,
                "cost_price": float(cheapest_variant.cost_price) if cheapest_variant and cheapest_variant.cost_price is not None else None,
                "margin_amount": margin_metrics(cheapest_variant)[0] if cheapest_variant else None,
                "margin_percent": margin_metrics(cheapest_variant)[1] if cheapest_variant else None,
                "currency": cheapest_sale_tier.currency if cheapest_sale_tier else (cheapest_variant.currency if cheapest_variant else None),
                "updated_at": product.updated_at.isoformat(),
            }
        )
    return rows


def list_chemical_products(session: Session) -> list[dict]:
    stmt = (
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.assets))
        .where(Product.is_chemical.is_(True))
        .order_by(Product.updated_at.desc())
    )
    rows: list[dict] = []
    for product in session.scalars(stmt).unique():
        sds_available = bool(product.sds_available or product.sds_url or product.sds_asset_id or _inferred_sds_asset(product.assets))
        rows.append(
            {
                "id": product.id,
                "sku": product.sku,
                "title": product.title,
                "brand": product.brand.name if product.brand else None,
                "cas_number": product.cas_number,
                "un_number": product.un_number,
                "adr_relevant": product.adr_relevant,
                "adr_label": "Ja" if product.adr_relevant else "Nein",
                "sds_available": sds_available,
                "sds_label": "Ja" if sds_available else "Nein",
                "business_only": product.business_only,
                "business_only_label": "Ja" if product.business_only else "Nein",
                "status": product.status,
                "shop_active": product.shop_active,
                "shop_active_label": "Ja" if product.shop_active else "Nein",
                "chemical_type": product.chemical_type,
                "chemical_reference_url": product.chemical_reference_url,
                "chemical_last_enriched_at": product.chemical_last_enriched_at.isoformat() if product.chemical_last_enriched_at else None,
                "chemical_enrichment_status": product.chemical_enrichment_status,
                "chemical_enrichment_error": product.chemical_enrichment_error,
                "updated_at": product.updated_at.isoformat(),
            }
        )
    return rows


def list_variants(session: Session, archive_filter: str = "active") -> list[dict]:
    stmt = (
        select(ProductVariant)
        .options(joinedload(ProductVariant.product), joinedload(ProductVariant.price_tiers))
        .order_by(ProductVariant.updated_at.desc())
    )
    normalized_filter = (archive_filter or "active").strip().lower()
    if normalized_filter in {"active", "aktive", "non_archived"}:
        stmt = stmt.where(func.lower(ProductVariant.status).notin_(["archived", "archiviert"]))
    elif normalized_filter in {"archived", "archiviert"}:
        stmt = stmt.where(func.lower(ProductVariant.status).in_(["archived", "archiviert"]))
    return [
        {
            "id": variant.id,
            "product_id": variant.product_id,
            "product_sku": variant.product.sku if variant.product else None,
            "product_title": variant.product.title if variant.product else None,
            "sku": variant.sku,
            "variant_title": variant.variant_title,
            "option_name": variant.option_name,
            "option_value": variant.option_value,
            "packaging": variant.packaging,
            "price": float(variant.price) if variant.price is not None else None,
            "cost_price": float(variant.cost_price) if variant.cost_price is not None else None,
            "currency": variant.currency,
            "cost_currency": variant.cost_currency,
            "margin_amount": margin_metrics(variant)[0],
            "margin_percent": margin_metrics(variant)[1],
            "stock_qty": variant.stock_qty,
            "barcode": variant.barcode,
            "status": variant.status,
            "price_tiers": [_serialize_price_tier(variant, tier) for tier in _sorted_price_tiers(variant)],
            "updated_at": variant.updated_at.isoformat(),
        }
        for variant in session.scalars(stmt).unique()
    ]


def _serialize_chemical_enrichment(enrichment: ProductChemicalEnrichment | None) -> dict | None:
    if enrichment is None:
        return None
    return {
        "id": enrichment.id,
        "reference_url": enrichment.reference_url,
        "source_kind": enrichment.source_kind,
        "status": enrichment.status,
        "raw_payload_json": enrichment.raw_payload_json,
        "normalized_payload_json": enrichment.normalized_payload_json,
        "document_links_json": enrichment.document_links_json,
        "warnings_json": enrichment.warnings_json,
        "error_log": enrichment.error_log,
        "extracted_at": enrichment.extracted_at.isoformat() if enrichment.extracted_at else None,
        "applied_at": enrichment.applied_at.isoformat() if enrichment.applied_at else None,
    }


def _default_sdb_metadata() -> dict[str, object]:
    return {
        "review_status": "review_required",
        "version_label": "Entwurf 1.0",
        "effective_date": None,
        "issuer_name": "VOXSTER GmbH",
        "issuer_address_line1": "Obere Ifangstrasse 10",
        "issuer_address_line2": None,
        "issuer_postal_code": "8215",
        "issuer_city": "Hallau",
        "issuer_country_code": "CH",
        "issuer_phone": "+41 52 502 67 23",
        "issuer_email": "info@voxster.ch",
    }


def _serialize_sdb_llm_run(run: ProductSDBLLMRun) -> dict:
    return {
        "id": run.id,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "system_prompt": run.system_prompt,
        "user_prompt": run.user_prompt,
        "response_json": run.response_json or {},
        "raw_response_text": run.raw_response_text,
        "warnings_json": run.warnings_json or [],
        "error_log": run.error_log,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


def _serialize_product_sdb(record: ProductSDB | None) -> dict:
    defaults = _default_sdb_metadata()
    if record is None:
        return {
            "id": None,
            "source_url": None,
            "pdf_url": None,
            "source_asset_id": None,
            "parser_status": None,
            "parser_warnings_json": [],
            **defaults,
            "document_title": None,
            "action_log_json": [],
            "raw_text": None,
            "sections_json": default_sdb_sections(),
            "generated_pdf_path": None,
            "generated_at": None,
            "llm_runs": [],
        }
    return {
        "id": record.id,
        "source_url": record.source_url,
        "pdf_url": record.pdf_url,
        "source_asset_id": record.source_asset_id,
        "parser_status": record.parser_status,
        "parser_warnings_json": record.parser_warnings_json or [],
        "review_status": record.review_status or defaults["review_status"],
        "version_label": record.version_label or defaults["version_label"],
        "effective_date": record.effective_date,
        "document_title": record.document_title,
        "issuer_name": record.issuer_name or defaults["issuer_name"],
        "issuer_address_line1": record.issuer_address_line1 or defaults["issuer_address_line1"],
        "issuer_address_line2": record.issuer_address_line2,
        "issuer_postal_code": record.issuer_postal_code or defaults["issuer_postal_code"],
        "issuer_city": record.issuer_city or defaults["issuer_city"],
        "issuer_country_code": record.issuer_country_code or defaults["issuer_country_code"],
        "issuer_phone": record.issuer_phone or defaults["issuer_phone"],
        "issuer_email": record.issuer_email or defaults["issuer_email"],
        "action_log_json": record.action_log_json or [],
        "raw_text": record.raw_text,
        "sections_json": merge_sdb_sections(record.sections_json),
        "generated_pdf_path": record.generated_pdf_path,
        "generated_at": record.generated_at.isoformat() if record.generated_at else None,
        "llm_runs": [_serialize_sdb_llm_run(run) for run in record.llm_runs],
    }


def list_product_chemical_enrichments(session: Session, product_id: int) -> list[dict]:
    stmt = (
        select(ProductChemicalEnrichment)
        .where(ProductChemicalEnrichment.product_id == product_id)
        .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
    )
    return [_serialize_chemical_enrichment(row) for row in session.scalars(stmt)]


def get_product_sdb(session: Session, product_id: int) -> dict:
    stmt = select(ProductSDB).where(ProductSDB.product_id == product_id)
    return _serialize_product_sdb(session.scalar(stmt))


def upsert_product_sdb(session: Session, product_id: int, payload: ProductSDBUpdate) -> ProductSDB:
    record = session.scalar(select(ProductSDB).where(ProductSDB.product_id == product_id))
    if record is None:
        record = ProductSDB(product_id=product_id)
        session.add(record)
    provided_fields = getattr(payload, "model_fields_set", set())
    defaults = _default_sdb_metadata()
    record.source_url = payload.source_url
    record.pdf_url = payload.pdf_url
    record.source_asset_id = payload.source_asset_id
    record.parser_status = payload.parser_status
    if "review_status" in provided_fields or record.review_status is None:
        record.review_status = payload.review_status or defaults["review_status"]
    if "version_label" in provided_fields or record.version_label is None:
        record.version_label = payload.version_label or defaults["version_label"]
    if "effective_date" in provided_fields:
        record.effective_date = payload.effective_date
    if "document_title" in provided_fields:
        record.document_title = payload.document_title
    if "issuer_name" in provided_fields or record.issuer_name is None:
        record.issuer_name = payload.issuer_name or defaults["issuer_name"]
    if "issuer_address_line1" in provided_fields or record.issuer_address_line1 is None:
        record.issuer_address_line1 = payload.issuer_address_line1 or defaults["issuer_address_line1"]
    if "issuer_address_line2" in provided_fields:
        record.issuer_address_line2 = payload.issuer_address_line2
    if "issuer_postal_code" in provided_fields or record.issuer_postal_code is None:
        record.issuer_postal_code = payload.issuer_postal_code or defaults["issuer_postal_code"]
    if "issuer_city" in provided_fields or record.issuer_city is None:
        record.issuer_city = payload.issuer_city or defaults["issuer_city"]
    if "issuer_country_code" in provided_fields or record.issuer_country_code is None:
        record.issuer_country_code = payload.issuer_country_code or defaults["issuer_country_code"]
    if "issuer_phone" in provided_fields or record.issuer_phone is None:
        record.issuer_phone = payload.issuer_phone or defaults["issuer_phone"]
    if "issuer_email" in provided_fields or record.issuer_email is None:
        record.issuer_email = payload.issuer_email or defaults["issuer_email"]
    if "action_log_json" in provided_fields:
        record.action_log_json = payload.action_log_json or []
    record.raw_text = payload.raw_text
    record.sections_json = merge_sdb_sections(payload.sections_json)
    record.generated_pdf_path = payload.generated_pdf_path
    if payload.generated_pdf_path:
        record.generated_at = datetime.now(timezone.utc)
    session.flush()
    return record


def list_categories(session: Session, sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE) -> list[dict]:
    include_all = str(sales_channel_code or "").strip() in {"*", "__all__"}
    channel = None
    if not include_all:
        try:
            channel = get_sales_channel_by_code(session, sales_channel_code)
        except ValueError:
            return []
    stmt = select(Category).options(joinedload(Category.sales_channel))
    if channel is not None:
        stmt = stmt.where(Category.sales_channel_id == channel.id)
    stmt = stmt.order_by(Category.sales_channel_id.asc(), Category.sort_order.asc(), Category.name.asc())
    return [
        {
            "id": category.id,
            "sales_channel_id": category.sales_channel_id,
            "sales_channel_code": category.sales_channel.code if category.sales_channel else (channel.code if channel else None),
            "sales_channel_name": category.sales_channel.name if category.sales_channel else (channel.name if channel else None),
            "parent_id": category.parent_id,
            "language_code": category.language_code,
            "name": category.name,
            "slug": category.slug,
            "sort_order": category.sort_order,
        }
        for category in session.scalars(stmt)
    ]


def get_category_detail(session: Session, category_id: int, sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE) -> dict | None:
    try:
        channel = get_sales_channel_by_code(session, sales_channel_code)
    except ValueError:
        return None
    stmt = (
        select(Category)
        .options(joinedload(Category.parent), joinedload(Category.sales_channel))
        .where(Category.id == category_id, Category.sales_channel_id == channel.id)
    )
    category = session.scalars(stmt).unique().first()
    if category is None:
        return None
    child_count = session.scalar(
        select(func.count(Category.id)).where(Category.parent_id == category.id, Category.sales_channel_id == channel.id)
    ) or 0
    product_count = session.scalar(
        select(func.count(ProductCategoryAssignment.product_id)).where(ProductCategoryAssignment.category_id == category.id)
    ) or 0
    return {
        "id": category.id,
        "sales_channel_id": category.sales_channel_id,
        "sales_channel_code": category.sales_channel.code if category.sales_channel else channel.code,
        "sales_channel_name": category.sales_channel.name if category.sales_channel else channel.name,
        "parent_id": category.parent_id,
        "parent_name": category.parent.name if category.parent else None,
        "language_code": category.language_code,
        "name": category.name,
        "slug": category.slug,
        "sort_order": category.sort_order,
        "child_count": int(child_count),
        "product_count": int(product_count),
    }


def _category_descendant_ids(session: Session, category: Category) -> list[int]:
    ids: list[int] = [int(category.id)]
    queue: list[int] = [int(category.id)]
    seen: set[int] = set(queue)
    while queue:
        parent_id = queue.pop(0)
        child_ids = [
            int(row)
            for row in session.scalars(
                select(Category.id).where(
                    Category.parent_id == parent_id,
                    Category.sales_channel_id == category.sales_channel_id,
                )
            )
        ]
        for child_id in child_ids:
            if child_id in seen:
                continue
            seen.add(child_id)
            ids.append(child_id)
            queue.append(child_id)
    return ids


def _primary_image_asset(product: Product) -> Asset | None:
    image_assets = [asset for asset in product.assets if (asset.mime_type or "").startswith("image/")]
    return min(image_assets, key=lambda asset: (asset.sort_order, asset.id), default=None)


def get_products_for_category(session: Session, category_id: int, include_variants: bool = False, include_descendants: bool = True) -> list[dict]:
    category = session.get(Category, int(category_id))
    if category is None:
        return []
    category_ids = _category_descendant_ids(session, category) if include_descendants else [int(category_id)]
    assignment_rows = session.execute(
        select(
            ProductCategoryAssignment.product_id,
            func.min(ProductCategoryAssignment.sort_order).label("sort_order"),
        )
        .select_from(ProductCategoryAssignment)
        .join(Product, Product.id == ProductCategoryAssignment.product_id)
        .where(
            ProductCategoryAssignment.category_id.in_(category_ids),
            ProductCategoryAssignment.sales_channel_id == category.sales_channel_id,
        )
        .group_by(ProductCategoryAssignment.product_id)
        .order_by(func.min(ProductCategoryAssignment.sort_order).asc(), ProductCategoryAssignment.product_id.asc())
    ).all()
    product_order = {int(row.product_id): index for index, row in enumerate(assignment_rows)}
    sort_orders = {int(row.product_id): int(row.sort_order or 0) for row in assignment_rows}
    if not product_order:
        return []
    stmt = (
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.variants), joinedload(Product.assets))
        .where(Product.id.in_(product_order.keys()))
    )
    rows: list[dict] = []
    for product in sorted(session.scalars(stmt).unique(), key=lambda item: product_order.get(int(item.id), 0)):
        primary_asset = _primary_image_asset(product)
        variant_rows = [
            {
                "id": variant.id,
                "sku": variant.sku,
                "variant_title": variant.variant_title,
                "stock_qty": variant.stock_qty,
            }
            for variant in sorted(product.variants, key=lambda item: (item.sku or "", item.id))
        ]
        rows.append(
            {
                "id": product.id,
                "position": sort_orders.get(int(product.id), 9999),
                "sort_order": sort_orders.get(int(product.id), 0),
                "sku": product.sku,
                "title": product.title,
                "photo_asset_id": primary_asset.id if primary_asset else None,
                "photo_url": f"/asset-file/{primary_asset.id}" if primary_asset else None,
                "photo_thumb_url": f"/asset-thumb/{primary_asset.id}" if primary_asset else None,
                "photo_filename": primary_asset.filename if primary_asset else None,
                "photo_mime_type": primary_asset.mime_type if primary_asset else None,
                "brand": product.brand.name if product.brand else None,
                "status": product.status,
                "variant_count": len(product.variants),
                "sales_channel_id": category.sales_channel_id,
                "sales_channel_code": category.sales_channel.code if category.sales_channel else None,
                "sales_channel_name": category.sales_channel.name if category.sales_channel else None,
                "variants": variant_rows if include_variants else [],
            }
        )
    return rows


def move_products_to_category(
    session: Session,
    product_ids: Iterable[int],
    target_category_id: int,
    *,
    source_category_id: int | None = None,
    ordered_product_ids: Iterable[int] | None = None,
) -> dict[str, object]:
    target = session.get(Category, int(target_category_id))
    if target is None:
        raise ValueError("Ziel-Kategorie nicht gefunden")
    same_category = source_category_id is not None and int(source_category_id) == int(target_category_id)

    source_category_ids: list[int] = []
    if source_category_id is not None:
        source = session.get(Category, int(source_category_id))
        if source is None:
            raise ValueError("Quell-Kategorie nicht gefunden")
        if int(source.sales_channel_id) != int(target.sales_channel_id):
            raise ValueError("Quell- und Ziel-Kategorie gehören nicht zum selben Kanal")
        source_category_ids = _category_descendant_ids(session, source)

    moved = 0
    skipped = 0
    for product_id in _unique_ints(product_ids):
        product = session.get(Product, int(product_id))
        if product is None:
            skipped += 1
            continue
        if source_category_ids and not same_category:
            existing_source_rows = list(
                session.scalars(
                    select(ProductCategoryAssignment).where(
                        ProductCategoryAssignment.product_id == product.id,
                        ProductCategoryAssignment.sales_channel_id == target.sales_channel_id,
                        ProductCategoryAssignment.category_id.in_(source_category_ids),
                    )
                )
            )
            for row in existing_source_rows:
                session.delete(row)
        existing_target = session.scalar(
            select(ProductCategoryAssignment).where(
                ProductCategoryAssignment.product_id == product.id,
                ProductCategoryAssignment.sales_channel_id == target.sales_channel_id,
                ProductCategoryAssignment.category_id == target.id,
            )
        )
        if existing_target is None:
            existing_target = ProductCategoryAssignment(
                product_id=product.id,
                category_id=target.id,
                sales_channel_id=target.sales_channel_id,
            )
            session.add(existing_target)
        moved += 1
    ordered_ids = _unique_ints(ordered_product_ids or [])
    if same_category and ordered_ids:
        order_rows = list(
            session.scalars(
                select(ProductCategoryAssignment).where(
                    ProductCategoryAssignment.category_id == target.id,
                    ProductCategoryAssignment.sales_channel_id == target.sales_channel_id,
                )
            )
        )
        order_index = {product_id: index for index, product_id in enumerate(ordered_ids)}
        next_order = len(order_index)
        for row in sorted(order_rows, key=lambda item: order_index.get(int(item.product_id), next_order + int(item.id))):
            row.sort_order = order_index.get(int(row.product_id), next_order)
            if int(row.product_id) not in order_index:
                next_order += 1
    session.flush()
    return {"moved": moved, "skipped": skipped, "target_category_id": target.id, "target_category_name": target.name}


def create_category(
    session: Session,
    name: str,
    parent_id: int | None,
    language_code: str,
    sort_order: int,
    slug: str | None = None,
    sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE,
) -> Category:
    channel = get_sales_channel_by_code(session, sales_channel_code)
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("Kategorie-Name fehlt")
    normalized_slug = ((slug or "").strip() or slugify(normalized_name, separator="-"))
    parent = session.get(Category, parent_id) if parent_id else None
    if parent_id and parent is None:
        raise ValueError("Parent-Kategorie nicht gefunden")
    if parent and parent.sales_channel_id != channel.id:
        raise ValueError("Parent-Kategorie gehört zu einem anderen Kanal")
    existing = session.scalar(
        select(Category).where(
            Category.sales_channel_id == channel.id,
            Category.slug == normalized_slug,
        )
    )
    if existing is not None:
        raise ValueError("Slug ist in diesem Kanal bereits vergeben")
    category = Category(
        sales_channel_id=channel.id,
        name=normalized_name,
        parent_id=parent_id,
        language_code=(language_code or "de").strip(),
        sort_order=int(sort_order or 0),
        slug=normalized_slug,
    )
    session.add(category)
    session.flush()
    return category


def update_category(
    session: Session,
    category_id: int,
    name: str,
    parent_id: int | None,
    language_code: str,
    sort_order: int,
    slug: str | None = None,
    sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE,
) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise ValueError("Kategorie nicht gefunden")
    channel = get_sales_channel_by_code(session, sales_channel_code)
    if category.sales_channel_id != channel.id:
        raise ValueError("Kategorie gehört nicht zum gewählten Kanal")
    if parent_id == category_id:
        raise ValueError("Eine Kategorie kann nicht ihr eigener Parent sein")
    parent = session.get(Category, parent_id) if parent_id else None
    if parent_id and parent is None:
        raise ValueError("Parent-Kategorie nicht gefunden")
    if parent and parent.sales_channel_id != category.sales_channel_id:
        raise ValueError("Parent-Kategorie gehört zu einem anderen Kanal")
    normalized_name = (name or category.name or "").strip()
    if not normalized_name:
        raise ValueError("Kategorie-Name fehlt")
    normalized_slug = ((slug or "").strip() or slugify(normalized_name, separator="-"))
    existing = session.scalar(
        select(Category).where(
            Category.sales_channel_id == category.sales_channel_id,
            Category.slug == normalized_slug,
            Category.id != category.id,
        )
    )
    if existing is not None:
        raise ValueError("Slug ist in diesem Kanal bereits vergeben")
    category.name = normalized_name
    category.parent_id = parent_id
    category.language_code = (language_code or category.language_code or "de").strip()
    category.sort_order = int(sort_order or 0)
    category.slug = normalized_slug
    session.flush()
    return category


def delete_category(session: Session, category_id: int, sales_channel_code: str = DEFAULT_CATEGORY_CHANNEL_CODE) -> None:
    category = session.get(Category, category_id)
    if category is None:
        raise ValueError("Kategorie nicht gefunden")
    channel = get_sales_channel_by_code(session, sales_channel_code)
    if category.sales_channel_id != channel.id:
        raise ValueError("Kategorie gehört nicht zum gewählten Kanal")
    child_count = session.scalar(
        select(func.count(Category.id)).where(Category.parent_id == category.id, Category.sales_channel_id == category.sales_channel_id)
    ) or 0
    if child_count:
        raise ValueError("Kategorie kann nicht gelöscht werden: es gibt Unterkategorien")
    product_count = session.scalar(
        select(func.count(ProductCategoryAssignment.product_id)).where(ProductCategoryAssignment.category_id == category.id)
    ) or 0
    if product_count:
        raise ValueError("Kategorie kann nicht gelöscht werden: sie ist Produkten zugeordnet")
    session.delete(category)
    session.flush()


def list_brands(session: Session) -> list[dict]:
    stmt = select(Brand).order_by(Brand.name.asc())
    return [{"id": brand.id, "name": brand.name, "slug": brand.slug} for brand in session.scalars(stmt)]


def list_assets(session: Session) -> list[dict]:
    stmt = (
        select(Asset)
        .options(joinedload(Asset.product), joinedload(Asset.variant))
        .order_by(Asset.sort_order.asc(), Asset.created_at.desc())
    )
    documents_by_asset_id = {
        row.asset_id: _serialize_asset_sdb_document(row)
        for row in session.scalars(
            select(ChemicalDocument).where(ChemicalDocument.asset_id.is_not(None), ChemicalDocument.document_type == "sds")
        )
        if row.asset_id
    }
    return [
        {
            "id": asset.id,
            "product_id": asset.product_id,
            "product_sku": asset.product.sku if asset.product else None,
            "product_title": asset.product.title if asset.product else None,
            "variant_id": asset.variant_id,
            "variant_sku": asset.variant.sku if asset.variant else None,
            "variant_title": asset.variant.variant_title if asset.variant else None,
            "filename": asset.filename,
            "original_filename": asset.original_filename,
            "mime_type": asset.mime_type,
            "file_size": asset.file_size,
            "file_extension": asset.file_extension,
            "width": asset.width,
            "height": asset.height,
            "source_url": asset.source_url,
            "storage_path": asset.storage_path,
            "stored_filename": asset.stored_filename,
            "object_key": asset.object_key,
            "bucket": asset.bucket,
            "storage_provider": asset.storage_provider,
            "asset_type": asset.asset_type,
            "title": asset.title,
            "description": asset.description,
            "language_code": asset.language_code,
            "public_url": asset.public_url,
            "status": asset.status,
            "uploaded_at": asset.uploaded_at.isoformat() if asset.uploaded_at else None,
            "alt_text": asset.alt_text,
            "sort_order": asset.sort_order,
            "created_at": asset.created_at.isoformat(),
            **_asset_sdb_fields(documents_by_asset_id.get(asset.id)),
        }
        for asset in session.scalars(stmt)
    ]


def _serialize_asset_sdb_document(document: ChemicalDocument) -> dict[str, object]:
    generated_at = document.generated_at or document.created_at
    return {
        "id": document.id,
        "document_type": "SDB" if str(document.document_type or "").lower() in {"sds", "sdb"} else str(document.document_type or "").upper(),
        "language_code": document.locale or document.language_code,
        "region_code": document.region_code,
        "status": document.status,
        "source": document.source or "manual",
        "generated_at": generated_at.isoformat() if generated_at else None,
        "generated_at_display": _format_asset_datetime(generated_at),
        "file_url": document.file_url,
        "is_current": bool(document.is_current),
    }


def _asset_sdb_fields(document: dict[str, object] | None) -> dict[str, object]:
    if not document:
        return {
            "sdb_document_id": None,
            "sdb_document_type": None,
            "sdb_language_code": None,
            "sdb_region_code": None,
            "sdb_generated_at": None,
            "sdb_generated_at_display": None,
            "sdb_status": None,
            "sdb_source": None,
            "sdb_file_url": None,
            "sdb_is_current": None,
        }
    return {
        "sdb_document_id": document.get("id"),
        "sdb_document_type": document.get("document_type"),
        "sdb_language_code": document.get("language_code"),
        "sdb_region_code": document.get("region_code"),
        "sdb_generated_at": document.get("generated_at"),
        "sdb_generated_at_display": document.get("generated_at_display"),
        "sdb_status": document.get("status"),
        "sdb_source": document.get("source"),
        "sdb_file_url": document.get("file_url"),
        "sdb_is_current": document.get("is_current"),
    }


def _format_asset_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%d.%m.%Y %H:%M")


def delete_asset(session: Session, asset_id: int) -> None:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ValueError("Asset not found")
    file_path = Path(asset.storage_path)
    if asset.storage_provider in {"cloudflare_r2", "bunny_storage"} and asset.object_key:
        try:
            from app.services.r2_config_service import build_r2_storage

            build_r2_storage(session).delete_object(asset.object_key)
        except Exception:
            # The DB record can still be removed if R2 is temporarily unavailable.
            pass
    group_assets = _asset_group(session, asset)
    session.delete(asset)
    session.flush()
    remaining_assets = [row for row in group_assets if row.id != asset_id]
    _normalize_asset_sort_order(remaining_assets)
    session.flush()
    if asset.storage_provider not in {"cloudflare_r2", "bunny_storage"} and file_path.exists():
        file_path.unlink()


def delete_assets(session: Session, asset_ids: Iterable[int]) -> dict[str, object]:
    deleted_ids: list[int] = []
    errors: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for raw_id in asset_ids:
        try:
            asset_id = int(raw_id)
        except Exception:
            errors.append({"asset_id": raw_id, "message": "Ungültige Asset-ID"})
            continue
        if asset_id in seen_ids:
            continue
        seen_ids.add(asset_id)
        try:
            delete_asset(session, asset_id)
            deleted_ids.append(asset_id)
        except Exception as exc:
            errors.append({"asset_id": asset_id, "message": str(exc)})
    return {
        "deleted_ids": deleted_ids,
        "deleted_count": len(deleted_ids),
        "errors": errors,
        "error_count": len(errors),
    }


def move_asset(session: Session, asset_id: int, direction: str) -> None:
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise ValueError("Asset not found")
    group_assets = _asset_group(session, asset)
    ordered = sorted(group_assets, key=lambda row: (row.sort_order, row.id))
    current_index = next((index for index, row in enumerate(ordered) if row.id == asset_id), None)
    if current_index is None:
        raise ValueError("Asset not found in order scope")
    if direction == "up" and current_index > 0:
        ordered[current_index - 1], ordered[current_index] = ordered[current_index], ordered[current_index - 1]
    elif direction == "down" and current_index < len(ordered) - 1:
        ordered[current_index + 1], ordered[current_index] = ordered[current_index], ordered[current_index + 1]
    _normalize_asset_sort_order(ordered)
    session.flush()


def _asset_group(session: Session, asset: Asset) -> list[Asset]:
    if asset.product_id is not None:
        stmt = select(Asset).where(Asset.product_id == asset.product_id)
    else:
        stmt = select(Asset).where(Asset.variant_id == asset.variant_id)
    return list(session.scalars(stmt))


def _normalize_asset_sort_order(assets: list[Asset]) -> None:
    ordered = sorted(assets, key=lambda row: (row.sort_order, row.id))
    for index, row in enumerate(ordered):
        row.sort_order = index


def list_import_jobs(session: Session, limit: int = 25) -> list[dict]:
    stmt = select(ImportJob).order_by(ImportJob.started_at.desc()).limit(limit)
    rows = []
    for job in session.scalars(stmt):
        summary = job.summary_json or {}
        sales_channel_code = summary.get("sales_channel_code")
        if not sales_channel_code and job.job_type == "pim_import":
            sales_channel_code = DEFAULT_CATEGORY_CHANNEL_CODE
        rows.append(
            {
                "id": job.id,
                "source_name": job.source_name,
                "job_type": job.job_type,
                "status": job.status,
                "sales_channel_code": sales_channel_code,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "summary_json": summary,
                "error_log": job.error_log,
            }
        )
    return rows


def list_attribute_overview(session: Session) -> list[dict]:
    rows = list(session.scalars(select(ProductVariant).order_by(ProductVariant.option_name.asc(), ProductVariant.option_value.asc())))
    grouped: dict[str, dict[str, object]] = {}
    for variant in rows:
        attribute_name = (variant.option_name or ("Packaging" if variant.packaging else None))
        attribute_value = variant.option_value or variant.packaging
        if not attribute_name or not attribute_value:
            continue
        entry = grouped.setdefault(
            attribute_name,
            {
                "attribute_name": attribute_name,
                "variant_count": 0,
                "values": set(),
            },
        )
        entry["variant_count"] = int(entry["variant_count"]) + 1
        cast_values = entry["values"]
        assert isinstance(cast_values, set)
        cast_values.add(attribute_value)
    return [
        {
            "attribute_name": key,
            "variant_count": value["variant_count"],
            "value_count": len(value["values"]),
            "example_values": ", ".join(sorted(value["values"])[:8]),
        }
        for key, value in sorted(grouped.items())
    ]


def list_family_overview(session: Session) -> list[dict]:
    stmt = (
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.variants))
        .order_by(Product.updated_at.desc())
    )
    rows: list[dict] = []
    for product in session.scalars(stmt).unique():
        if not product.family_key and len(product.variants) <= 1:
            continue
        attribute_names = sorted({variant.option_name for variant in product.variants if variant.option_name})
        attribute_values = sorted({variant.option_value or variant.packaging for variant in product.variants if (variant.option_value or variant.packaging)})
        rows.append(
            {
                "id": product.id,
                "family_key": product.family_key or product.sku,
                "sku": product.sku,
                "title": product.title,
                "brand": product.brand.name if product.brand else None,
                "status": product.status,
                "variant_count": len(product.variants),
                "attributes": ", ".join(attribute_names) if attribute_names else None,
                "values": ", ".join(attribute_values[:10]) if attribute_values else None,
                "updated_at": product.updated_at.isoformat(),
            }
        )
    return rows


def list_translation_overview(session: Session) -> list[dict]:
    stmt = (
        select(ProductTranslation)
        .options(joinedload(ProductTranslation.product))
        .order_by(ProductTranslation.language_code.asc(), ProductTranslation.id.desc())
    )
    return [
        {
            "id": translation.id,
            "product_id": translation.product_id,
            "product_sku": translation.product.sku if translation.product else None,
            "product_title": translation.product.title if translation.product else None,
            "language_code": translation.language_code,
            "title": translation.title,
            "short_description": translation.short_description,
            "description": translation.description,
            "seo_title": translation.seo_title,
            "seo_description": translation.seo_description,
            "slug": translation.slug,
            "translation_status": translation.translation_status,
            "source_language_code": translation.source_language_code,
            "provider": translation.provider,
            "model": translation.model,
            "generated_at": translation.generated_at.isoformat() if translation.generated_at else None,
        }
        for translation in session.scalars(stmt).unique()
    ]


def list_variant_translation_overview(session: Session) -> list[dict]:
    stmt = (
        select(VariantTranslation)
        .options(joinedload(VariantTranslation.variant).joinedload(ProductVariant.product))
        .order_by(VariantTranslation.language_code.asc(), VariantTranslation.id.desc())
    )
    return [
        {
            "id": translation.id,
            "variant_id": translation.variant_id,
            "variant_sku": translation.variant.sku if translation.variant else None,
            "product_id": translation.variant.product_id if translation.variant else None,
            "product_sku": translation.variant.product.sku if translation.variant and translation.variant.product else None,
            "language_code": translation.language_code,
            "title": translation.title,
            "option_label_override": translation.option_label_override,
            "package_label": translation.package_label,
        }
        for translation in session.scalars(stmt).unique()
    ]


def _first_primary_channel_mapping(product: Product, sales_channel_id: int) -> ProductCategoryMapping | None:
    candidates = [row for row in product.channel_category_mappings if row.sales_channel_id == sales_channel_id]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (0 if item.is_primary else 1, item.position, item.id))
    return candidates[0]


def _matching_product_translation(product: Product, language_code: str | None) -> ProductTranslation | None:
    if not language_code:
        return None
    for translation in product.translations:
        if translation.language_code == language_code:
            return translation
    return None


def _matching_variant_translation(variant: ProductVariant, language_code: str | None) -> VariantTranslation | None:
    if not language_code:
        return None
    for translation in variant.translations:
        if translation.language_code == language_code:
            return translation
    return None


def _is_listing_within_active_window(
    active_from: datetime | None,
    active_until: datetime | None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)

    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start = _as_utc(active_from)
    end = _as_utc(active_until)
    if start and now < start:
        return False
    if end and now > end:
        return False
    return True


def list_channel_export_rows(session: Session, sales_channel_code: str, language_code: str | None = None) -> list[dict]:
    channel = session.scalar(select(SalesChannel).where(SalesChannel.code == sales_channel_code))
    if channel is None:
        raise ValueError("Vertriebskanal nicht gefunden")
    if not channel.is_active:
        raise ValueError("Vertriebskanal ist nicht aktiv")
    stmt = (
        select(Product)
        .options(
            joinedload(Product.brand),
            joinedload(Product.translations),
            joinedload(Product.channel_listings),
            joinedload(Product.channel_category_mappings).joinedload(ProductCategoryMapping.channel_category),
            joinedload(Product.variants).joinedload(ProductVariant.channel_listings),
            joinedload(Product.variants).joinedload(ProductVariant.translations),
        )
        .order_by(Product.id.asc())
    )
    rows: list[dict] = []
    now = datetime.now(timezone.utc)
    for product in session.scalars(stmt).unique():
        product_listing = next((row for row in product.channel_listings if row.sales_channel_id == channel.id), None)
        if product_listing is None or not product_listing.allowed or not product_listing.is_active:
            continue
        if _normalize_publication_status(product_listing.publication_status) != "published":
            continue
        if not _is_listing_within_active_window(product_listing.active_from, product_listing.active_until, now):
            continue
        mapping = _first_primary_channel_mapping(product, channel.id)
        product_translation = _matching_product_translation(product, language_code)
        for variant in product.variants:
            variant_listing = next((row for row in variant.channel_listings if row.sales_channel_id == channel.id), None)
            if variant_listing is None or not variant_listing.allowed or not variant_listing.is_active:
                continue
            if _normalize_publication_status(variant_listing.publication_status) != "published":
                continue
            variant_translation = _matching_variant_translation(variant, language_code)
            rows.append(
                {
                    "sales_channel_code": channel.code,
                    "product_id": product.id,
                    "variant_id": variant.id,
                    "product_sku": product.sku,
                    "variant_sku": variant_listing.channel_sku or variant.sku,
                    "variant_ean": variant_listing.channel_ean or variant.barcode,
                    "product_title": (
                        product_translation.title
                        if product_translation and product_translation.title
                        else product.title
                    ),
                    "short_description": (
                        product_translation.short_description
                        if product_translation and product_translation.short_description
                        else None
                    ),
                    "description": (
                        product_translation.description
                        if product_translation and product_translation.description
                        else product.description
                    ),
                    "slug": (
                        product_translation.slug
                        if product_translation and product_translation.slug
                        else product.handle
                    ),
                    "variant_title": (
                        variant_translation.title
                        if variant_translation and variant_translation.title
                        else (variant.variant_title or variant.option_value or variant.packaging)
                    ),
                    "external_category_id": mapping.channel_category.external_category_id if mapping and mapping.channel_category else None,
                    "external_category_path": mapping.channel_category.external_path if mapping and mapping.channel_category else None,
                    "publication_status": "published",
                    "price_enabled": bool(variant_listing.price_enabled),
                    "shippable": bool(variant_listing.shippable),
                    "hazardous_goods": bool(variant_listing.hazardous_goods),
                    "limited_quantity": variant_listing.limited_quantity,
                    "language_code": language_code or product.source_language,
                }
            )
    return rows


def export_channel_rows(
    session: Session,
    sales_channel_code: str,
    language_code: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, object]:
    rows = list_channel_export_rows(session, sales_channel_code, language_code=language_code)
    if not rows:
        raise ValueError("Keine freigegebenen und aktiven Kanal-Listings für den Export gefunden")
    export_root = Path(output_dir) if output_dir is not None else Path.cwd()
    path = write_channel_export_rows(export_root, rows, sales_channel_code=sales_channel_code, language_code=language_code)
    return {
        "path": str(path),
        "filename": path.name,
        "row_count": len(rows),
        "sales_channel_code": sales_channel_code,
        "language_code": language_code,
    }


def export_medusa_products(
    session: Session,
    product_ids: list[int],
    *,
    language_code: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, object]:
    normalized_ids = _unique_ints(product_ids)
    if not normalized_ids:
        raise ValueError("Keine Produkte ausgewählt")
    products = list(
        session.scalars(
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
            )
            .where(Product.id.in_(normalized_ids))
            .order_by(Product.id.asc())
        ).unique()
    )
    if not products:
        raise ValueError("Keine exportierbaren Produkte gefunden")
    rows = _medusa_product_output_rows(products, language_code=language_code, r2_public_base_url=get_r2_public_base_url(session))
    if not rows:
        raise ValueError("Keine aktiven Varianten für den Medusa-Export gefunden")
    export_root = Path(output_dir) if output_dir is not None else Path.cwd()
    run_dir = export_root / f"medusa_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    path = write_medusa_products(run_dir, rows)
    return {
        "path": str(path),
        "filename": str(path.relative_to(export_root)),
        "row_count": len(rows),
        "product_count": len(products),
        "language_code": language_code,
    }


def _medusa_product_output_rows(products: list[Product], *, language_code: str | None, r2_public_base_url: str | None = None) -> list[ProductOutputRow]:
    rows: list[ProductOutputRow] = []
    for product in products:
        if _normalize_variant_status(product.status) == "archived":
            continue
        translation = _matching_product_translation(product, language_code)
        title = translation.title if translation and translation.title else product.title
        description = translation.description if translation and translation.description else product.description
        handle = translation.slug if translation and translation.slug else product.handle
        product_image_urls = _asset_urls(product.assets, r2_public_base_url=r2_public_base_url)
        product_sds_urls = _document_asset_urls(product.assets, r2_public_base_url=r2_public_base_url)
        active_variants = [variant for variant in product.variants if _normalize_variant_status(variant.status) != "archived"]
        if not active_variants:
            active_variants = []
        for rank, variant in enumerate(sorted(active_variants, key=lambda item: (item.id or 0)), start=1):
            variant_translation = _matching_variant_translation(variant, language_code)
            variant_title = (
                variant_translation.title
                if variant_translation and variant_translation.title
                else (variant.variant_title or variant.option_value or variant.packaging or title)
            )
            image_urls = _asset_urls([*(product.assets or []), *(variant.assets or [])], r2_public_base_url=r2_public_base_url)
            extra_fields = _medusa_extra_fields(product, variant, handle, rank)
            rows.append(
                ProductOutputRow(
                    supplier_sku=product.sku,
                    variant_sku=variant.sku,
                    supplier_name=_source_name(product),
                    brand=product.brand.name if product.brand else None,
                    barcode=variant.barcode,
                    variant_title=variant_title,
                    variant_option_1_name=variant.option_name or "Variante",
                    variant_option_1_value=variant.option_value or variant.packaging or variant_title,
                    source_url=product.source_url,
                    source_url_final=product.source_url_final,
                    title_raw=product.title,
                    product_name=title,
                    product_title=title,
                    description=description,
                    specifications=product.specifications_text,
                    technical_features=product.technical_features_text,
                    image_urls="|".join(image_urls) if image_urls else None,
                    pdf_urls="|".join(product_sds_urls) if product_sds_urls else None,
                    sds_urls=product.sds_url,
                    extra_fields=extra_fields,
                    status="ok" if _medusa_product_is_publishable(product) else "draft",
                )
            )
    return rows


def _medusa_extra_fields(product: Product, variant: ProductVariant, handle: str | None, rank: int) -> dict[str, object]:
    category_sort_positions = _medusa_category_sort_positions(product)
    extra_fields: dict[str, object] = {
        "medusa_product_handle": handle,
        "medusa_product_metadata": {
            "pim_product_id": product.id,
            "pim_sku": product.sku,
            "source_language": product.source_language,
            "family_key": product.family_key,
            "is_chemical": bool(product.is_chemical),
            "pim_category_sort_positions": category_sort_positions,
        },
        "medusa_variant_metadata": {
            "pim_variant_id": variant.id,
            "pim_product_id": product.id,
            "cost_price": str(variant.cost_price) if variant.cost_price is not None else None,
            "cost_currency": variant.cost_currency,
        },
    }
    if variant.price is not None and variant.currency:
        extra_fields[f"medusa_variant_price_{variant.currency.lower()}"] = variant.price
    extra_fields["medusa_variant_manage_inventory"] = "TRUE"
    extra_fields["medusa_variant_allow_backorder"] = "FALSE"
    return {key: value for key, value in extra_fields.items() if value not in (None, "", {}, [])}


def _medusa_category_sort_positions(product: Product) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for mapping in sorted(product.channel_category_mappings or [], key=lambda item: (item.sales_channel_id, item.position, item.channel_category_id)):
        if not mapping.channel_category:
            continue
        rows.append(
            {
                "sales_channel": mapping.sales_channel.name if mapping.sales_channel else str(mapping.sales_channel_id),
                "sales_channel_code": mapping.sales_channel.code if mapping.sales_channel else None,
                "channel_category_id": mapping.channel_category_id,
                "external_category_id": mapping.channel_category.external_category_id,
                "category_handle": mapping.channel_category.external_category_id,
                "category_path": mapping.channel_category.external_path,
                "position": int(mapping.position if mapping.position is not None else 9999),
            }
        )
    return rows


def _asset_urls(assets: list[Asset], *, r2_public_base_url: str | None = None) -> list[str]:
    urls: list[str] = []
    for asset in _preferred_export_assets(
        [asset for asset in assets if str(asset.mime_type or "").startswith("image/")]
    ):
        url = _asset_export_url(asset, r2_public_base_url=r2_public_base_url)
        if url and url not in urls:
            urls.append(url)
    return urls


def _document_asset_urls(assets: list[Asset], *, r2_public_base_url: str | None = None) -> list[str]:
    urls: list[str] = []
    for asset in _preferred_export_assets(
        [asset for asset in assets if not str(asset.mime_type or "").startswith("image/")]
    ):
        url = _asset_export_url(asset, r2_public_base_url=r2_public_base_url)
        if url and url not in urls:
            urls.append(url)
    return urls


def _preferred_export_assets(assets: list[Asset]) -> list[Asset]:
    grouped: dict[str, list[Asset]] = {}
    for asset in assets:
        key = asset.checksum or asset.source_url or asset.object_key or f"asset:{asset.id}"
        grouped.setdefault(key, []).append(asset)
    preferred: list[Asset] = []
    for group in grouped.values():
        ordered = sorted(
            group,
            key=lambda item: (
                0 if item.storage_provider in {"cloudflare_r2", "bunny_storage"} and item.object_key else 1,
                item.sort_order,
                item.id,
            ),
        )
        preferred.append(ordered[0])
    return sorted(preferred, key=lambda item: (item.sort_order, item.id))


def _asset_export_url(asset: Asset, *, r2_public_base_url: str | None = None) -> str | None:
    if asset.storage_provider in {"cloudflare_r2", "bunny_storage"} and asset.object_key:
        if r2_public_base_url:
            return f"{r2_public_base_url.rstrip('/')}/{asset.object_key.lstrip('/')}"
        if asset.public_url:
            return asset.public_url
    if asset.source_url:
        return asset.source_url
    public_base_url = (os.getenv("PIM_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base_url:
        return f"{public_base_url}/asset-file/{asset.id}"
    return None


def _source_name(product: Product) -> str | None:
    refs = product.source_refs_json or {}
    if isinstance(refs, dict):
        return str(refs.get("source") or refs.get("supplier") or "").strip() or None
    return None


def _medusa_product_is_publishable(product: Product) -> bool:
    status = (product.status or "").strip().lower()
    if status in {"archived", "archiviert", "inactive"}:
        return False
    return bool(product.shop_active)


def list_rule_overview(session: Session) -> list[dict]:
    variants = list(session.scalars(select(ProductVariant).options(joinedload(ProductVariant.price_tiers))).unique())
    import_jobs = list(session.scalars(select(ImportJob)))
    variants_with_sale_price = sum(1 for variant in variants if variant.price is not None)
    variants_with_cost_price = sum(1 for variant in variants if variant.cost_price is not None)
    variants_with_tiers = sum(1 for variant in variants if variant.price_tiers)
    negative_margin_count = sum(1 for variant in variants if (margin_metrics(variant)[0] or 0) < 0)
    return [
        {
            "rule_type": "Anreicherung",
            "name": "Website-Resolver",
            "scope": "Produkt / Variante",
            "details": "Generischer Crawl und Tintolav-Katalogresolver verfügbar",
        },
        {
            "rule_type": "Anreicherung",
            "name": "Importjobs",
            "scope": "System",
            "details": f"{len(import_jobs)} Jobs protokolliert",
        },
        {
            "rule_type": "Preisregel",
            "name": "Verkaufspreise",
            "scope": "Varianten",
            "details": f"{variants_with_sale_price} Varianten mit Verkaufspreis",
        },
        {
            "rule_type": "Preisregel",
            "name": "Einkaufspreise",
            "scope": "Varianten",
            "details": f"{variants_with_cost_price} Varianten mit Einkaufspreis",
        },
        {
            "rule_type": "Preisregel",
            "name": "Preisstaffeln",
            "scope": "Varianten",
            "details": f"{variants_with_tiers} Varianten mit Staffelpreisen",
        },
        {
            "rule_type": "Preisregel",
            "name": "Negative Marge",
            "scope": "Varianten",
            "details": f"{negative_margin_count} Varianten mit negativer Marge",
        },
    ]


def archive_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Product not found")
    product.status = "archived"
    session.flush()
    return product
