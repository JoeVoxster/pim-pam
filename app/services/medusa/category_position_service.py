from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Category, ChannelCategory, MedusaConnectionConfig, MedusaSyncMapping, ProductCategoryAssignment, ProductCategoryMapping


SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "pim-category-product-position-sync.schema.json"
POSITION_SYNC_SOURCE = "pim_pam"
POSITION_ENTITY_TYPE = "category_product_positions"
DEFAULT_POSITION = 9999


class MedusaPositionPayloadError(ValueError):
    pass


def load_category_product_position_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def normalize_position(value: object) -> int:
    if value is None or value == "":
        return DEFAULT_POSITION
    if isinstance(value, bool):
        raise MedusaPositionPayloadError("Position ist ungültig.")
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise MedusaPositionPayloadError("Position ist ungültig.") from exc
    if not numeric.is_integer():
        raise MedusaPositionPayloadError("Position darf keine Kommazahl sein.")
    position = int(numeric)
    if position < 0:
        raise MedusaPositionPayloadError("Position darf nicht negativ sein.")
    return position


def build_medusa_category_product_position_payload(
    session: Session,
    config: MedusaConnectionConfig,
    *,
    channel_category_id: int,
    use_sales_channel: bool = True,
    allow_handle_fallback: bool = True,
) -> dict[str, Any]:
    category = session.get(ChannelCategory, int(channel_category_id))
    if category is None:
        raise MedusaPositionPayloadError("Kategorie nicht gefunden.")

    category_mapping = _mapping(session, config, "channel_category", category.id) or _mapping(session, config, "category", category.id)
    sales_channel_mapping = _mapping(session, config, "sales_channel", category.sales_channel_id)

    payload: dict[str, Any] = {"source": POSITION_SYNC_SOURCE}
    category_medusa_id = _clean(category_mapping.medusa_id if category_mapping else None)
    category_handle = _clean((category_mapping.medusa_handle if category_mapping else None) or category.external_category_id)
    if category_medusa_id:
        payload["product_category_id"] = category_medusa_id
    elif allow_handle_fallback and category_handle:
        payload["category_handle"] = category_handle
    else:
        raise MedusaPositionPayloadError("Kategorie hat keine Medusa-ID.")

    if use_sales_channel:
        sales_channel = category.sales_channel
        sales_channel_id = _clean(sales_channel_mapping.medusa_id if sales_channel_mapping else None)
        sales_channel_handle = _clean((sales_channel_mapping.medusa_handle if sales_channel_mapping else None) or (sales_channel.name if sales_channel else None) or (sales_channel.code if sales_channel else None))
        if sales_channel_id:
            payload["sales_channel_id"] = sales_channel_id
        elif allow_handle_fallback and sales_channel_handle:
            payload["sales_channel_handle"] = sales_channel_handle

    rows = list(
        session.scalars(
            select(ProductCategoryMapping)
            .options(
                joinedload(ProductCategoryMapping.product),
                joinedload(ProductCategoryMapping.sales_channel),
                joinedload(ProductCategoryMapping.channel_category),
            )
            .where(
                ProductCategoryMapping.channel_category_id == category.id,
                ProductCategoryMapping.sales_channel_id == category.sales_channel_id,
            )
        )
        .unique()
    )
    if not rows:
        raise MedusaPositionPayloadError("Keine Produkte für Kategorie gefunden.")

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            normalize_position(row.position),
            (row.product.title if row.product else "") or "",
            row.product_id,
        ),
    )
    seen_products: set[int] = set()
    items: list[dict[str, Any]] = []
    for row in sorted_rows:
        if row.product_id in seen_products:
            raise MedusaPositionPayloadError("Produkt kommt doppelt im Positions-Payload vor.")
        seen_products.add(row.product_id)
        product = row.product
        product_mapping = _mapping(session, config, "product", row.product_id)
        product_medusa_id = _clean(product_mapping.medusa_id if product_mapping else None)
        product_handle = _clean((product_mapping.medusa_handle if product_mapping else None) or (product.handle if product else None))
        item = {"position": normalize_position(row.position)}
        if product_medusa_id:
            item["product_id"] = product_medusa_id
        elif allow_handle_fallback and product_handle:
            item["product_handle"] = product_handle
        else:
            raise MedusaPositionPayloadError(f"Produkt {row.product_id} hat keine Medusa-ID.")
        items.append(item)

    payload["items"] = items
    validate_medusa_category_product_position_payload(payload)
    validate_no_duplicate_products(payload)
    return payload


def build_medusa_internal_category_product_position_payload(
    session: Session,
    config: MedusaConnectionConfig,
    *,
    category_id: int,
    use_sales_channel: bool = True,
    allow_handle_fallback: bool = True,
) -> dict[str, Any]:
    category = session.get(Category, int(category_id))
    if category is None:
        raise MedusaPositionPayloadError("Kategorie nicht gefunden.")

    category_mapping = _mapping(session, config, "category", category.id)
    sales_channel_mapping = _mapping(session, config, "sales_channel", category.sales_channel_id)
    payload: dict[str, Any] = {"source": POSITION_SYNC_SOURCE}

    category_medusa_id = _clean(category_mapping.medusa_id if category_mapping else None)
    category_handle = _clean((category_mapping.medusa_handle if category_mapping else None) or category.slug)
    if category_medusa_id:
        payload["product_category_id"] = category_medusa_id
    elif allow_handle_fallback and category_handle:
        payload["category_handle"] = category_handle
    else:
        raise MedusaPositionPayloadError("Kategorie hat keine Medusa-ID.")

    if use_sales_channel:
        sales_channel = category.sales_channel
        sales_channel_id = _clean(sales_channel_mapping.medusa_id if sales_channel_mapping else None)
        sales_channel_handle = _clean((sales_channel_mapping.medusa_handle if sales_channel_mapping else None) or (sales_channel.name if sales_channel else None) or (sales_channel.code if sales_channel else None))
        if sales_channel_id:
            payload["sales_channel_id"] = sales_channel_id
        elif allow_handle_fallback and sales_channel_handle:
            payload["sales_channel_handle"] = sales_channel_handle

    rows = list(
        session.scalars(
            select(ProductCategoryAssignment)
            .options(
                joinedload(ProductCategoryAssignment.product),
                joinedload(ProductCategoryAssignment.sales_channel),
                joinedload(ProductCategoryAssignment.category),
            )
            .where(
                ProductCategoryAssignment.category_id == category.id,
                ProductCategoryAssignment.sales_channel_id == category.sales_channel_id,
            )
        )
        .unique()
    )
    if not rows:
        raise MedusaPositionPayloadError("Keine Produkte für Kategorie gefunden.")

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            normalize_position(row.sort_order),
            (row.product.title if row.product else "") or "",
            row.product_id,
        ),
    )
    seen_products: set[int] = set()
    items: list[dict[str, Any]] = []
    for row in sorted_rows:
        if row.product_id in seen_products:
            raise MedusaPositionPayloadError("Produkt kommt doppelt im Positions-Payload vor.")
        seen_products.add(row.product_id)
        product = row.product
        product_mapping = _mapping(session, config, "product", row.product_id)
        product_medusa_id = _clean(product_mapping.medusa_id if product_mapping else None)
        product_handle = _clean((product_mapping.medusa_handle if product_mapping else None) or (product.handle if product else None))
        item = {"position": normalize_position(row.sort_order)}
        if product_medusa_id:
            item["product_id"] = product_medusa_id
        elif allow_handle_fallback and product_handle:
            item["product_handle"] = product_handle
        else:
            raise MedusaPositionPayloadError(f"Produkt {row.product_id} hat keine Medusa-ID.")
        items.append(item)

    payload["items"] = items
    validate_medusa_category_product_position_payload(payload)
    validate_no_duplicate_products(payload)
    return payload


def validate_medusa_category_product_position_payload(payload: dict[str, Any]) -> None:
    schema = load_category_product_position_schema()
    allowed = set(schema["properties"].keys())
    extra_keys = set(payload.keys()) - allowed
    if extra_keys:
        raise MedusaPositionPayloadError(f"Positions-Payload enthält unerlaubte Felder: {', '.join(sorted(extra_keys))}.")
    if "items" not in payload:
        raise MedusaPositionPayloadError("Positions-Payload enthält keine items.")
    if not payload.get("product_category_id") and not payload.get("category_handle"):
        raise MedusaPositionPayloadError("Positions-Payload enthält keine Kategorie-ID und keinen Kategorie-Handle.")
    _validate_nullable_string(payload, "sales_channel_id")
    _validate_nullable_string(payload, "sales_channel_handle")
    _validate_nullable_string(payload, "product_category_id")
    _validate_nullable_string(payload, "category_handle")
    _validate_nullable_string(payload, "source")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise MedusaPositionPayloadError("Positions-Payload items muss eine nicht leere Liste sein.")
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise MedusaPositionPayloadError(f"Positions-Payload item {index} ist kein Objekt.")
        item_extra = set(item.keys()) - {"product_id", "product_handle", "position"}
        if item_extra:
            raise MedusaPositionPayloadError(f"Positions-Payload item {index} enthält unerlaubte Felder: {', '.join(sorted(item_extra))}.")
        if "position" not in item:
            raise MedusaPositionPayloadError(f"Positions-Payload item {index} enthält keine Position.")
        if not item.get("product_id") and not item.get("product_handle"):
            raise MedusaPositionPayloadError(f"Positions-Payload item {index} enthält keine Produkt-ID und keinen Produkt-Handle.")
        _validate_nullable_string(item, "product_id")
        _validate_nullable_string(item, "product_handle")
        normalize_position(item.get("position"))


def validate_no_duplicate_products(payload: dict[str, Any]) -> None:
    seen: set[str] = set()
    for item in payload.get("items") or []:
        identity = str(item.get("product_id") or item.get("product_handle") or "")
        if identity in seen:
            raise MedusaPositionPayloadError("Produkt kommt doppelt im Positions-Payload vor.")
        seen.add(identity)


def _mapping(session: Session, config: MedusaConnectionConfig, entity_type: str, local_entity_id: int) -> MedusaSyncMapping | None:
    return session.scalar(
        select(MedusaSyncMapping).where(
            MedusaSyncMapping.connection_id == config.id,
            MedusaSyncMapping.entity_type == entity_type,
            MedusaSyncMapping.local_entity_id == int(local_entity_id),
            MedusaSyncMapping.locale_code.is_(None),
            MedusaSyncMapping.currency_code.is_(None),
            MedusaSyncMapping.status == "active",
        )
    )


def _validate_nullable_string(payload: dict[str, Any], key: str) -> None:
    if key not in payload or payload[key] is None:
        return
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise MedusaPositionPayloadError(f"{key} muss ein nicht leerer String oder null sein.")


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
