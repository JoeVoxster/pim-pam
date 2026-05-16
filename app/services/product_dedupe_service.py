from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Asset,
    ChemicalDocument,
    Product,
    ProductCategoryAssignment,
    ProductChannelListing,
    ProductDuplicateGroup,
    ProductDuplicateGroupItem,
    ProductMergeLog,
    ProductMergePreview,
    ProductSDB,
    ProductTranslation,
    ProductVariant,
    ProductVariantPriceTier,
    VariantChannelListing,
)


CONFIDENCE_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
CONFIDENCE_SCORE = {"HIGH": Decimal("95.00"), "MEDIUM": Decimal("82.00"), "LOW": Decimal("61.00")}
REPORT_COLUMNS = [
    "duplicate_group_id",
    "confidence",
    "master_product_id",
    "duplicate_product_ids",
    "sku",
    "title",
    "merged_assets_count",
    "merged_prices_count",
    "merged_variants_count",
    "conflicts_count",
    "status",
]


@dataclass(frozen=True)
class DuplicateGroup:
    group_id: str
    confidence: str
    reason: str
    product_ids: tuple[int, ...]
    master_product_id: int


def analyze_product_duplicates(
    session: Session,
    *,
    supplier: str | None = None,
    product_id: int | None = None,
    min_confidence: str = "LOW",
) -> list[dict[str, Any]]:
    products = _load_products(session, product_id=product_id, supplier=supplier)
    groups = _detect_duplicate_groups(products)
    min_rank = CONFIDENCE_ORDER.get((min_confidence or "LOW").upper(), 1)
    return [
        _preview_group(session, group)
        for group in groups
        if CONFIDENCE_ORDER.get(group.confidence, 0) >= min_rank
    ]


def scan_duplicate_groups(
    session: Session,
    *,
    supplier: str | None = None,
    product_id: int | None = None,
    min_confidence: str = "LOW",
    created_by: str | None = None,
) -> dict[str, Any]:
    previews = analyze_product_duplicates(session, supplier=supplier, product_id=product_id, min_confidence=min_confidence)
    created = 0
    updated = 0
    for preview in previews:
        group = _upsert_duplicate_group(session, preview, created_by=created_by)
        if group.created_at == group.updated_at:
            created += 1
        else:
            updated += 1
    session.flush()
    return {"groups_count": len(previews), "created_count": created, "updated_count": updated, "groups": list_duplicate_groups(session)}


def list_duplicate_groups(
    session: Session,
    *,
    status: str | None = None,
    min_score: int | None = None,
    query: str | None = None,
    source: str | None = None,
    only_open: bool = False,
    conflicts_only: bool = False,
    safe_only: bool = False,
) -> list[dict[str, Any]]:
    stmt = (
        select(ProductDuplicateGroup)
        .options(
            joinedload(ProductDuplicateGroup.master_product).joinedload(Product.brand),
            joinedload(ProductDuplicateGroup.items).joinedload(ProductDuplicateGroupItem.product).joinedload(Product.brand),
            joinedload(ProductDuplicateGroup.previews),
        )
        .order_by(ProductDuplicateGroup.updated_at.desc(), ProductDuplicateGroup.confidence_score.desc())
    )
    groups = list(session.scalars(stmt).unique())
    if status:
        groups = [group for group in groups if group.status == status]
    if only_open:
        groups = [group for group in groups if group.status in {"open", "reviewed", "conflict"}]
    if source:
        needle = source.lower().strip()
        groups = [group for group in groups if needle in (group.source or "").lower()]
    if min_score is not None:
        groups = [group for group in groups if float(group.confidence_score or 0) >= float(min_score)]
    if safe_only:
        groups = [group for group in groups if float(group.confidence_score or 0) >= 90]
    if conflicts_only:
        groups = [group for group in groups if group.conflict_summary]
    if query:
        needle = query.lower().strip()
        groups = [group for group in groups if needle in _group_search_text(group)]
    return [_serialize_duplicate_group(group) for group in groups]


def get_duplicate_group_detail(session: Session, group_id: int) -> dict[str, Any] | None:
    group = _load_duplicate_group(session, group_id)
    if group is None:
        return None
    latest_preview = group.previews[0] if group.previews else None
    preview_payload = latest_preview.preview_json if latest_preview else None
    return {
        **_serialize_duplicate_group(group),
        "master": _serialize_product_for_merge(group.master_product),
        "items": [_serialize_group_item(item) for item in group.items],
        "conflicts": _serialize_conflicts(group, preview_payload),
        "latest_preview": preview_payload,
        "latest_preview_id": latest_preview.id if latest_preview else None,
        "merge_log": group.merge_log_json,
    }


def create_duplicate_group_preview(session: Session, group_id: int, *, created_by: str | None = None) -> dict[str, Any]:
    group = _load_duplicate_group(session, group_id)
    if group is None:
        raise ValueError("Dublettengruppe nicht gefunden.")
    preview = _preview_persisted_group(session, group)
    preview_row = ProductMergePreview(
        group_id=group.id,
        preview_json=preview,
        conflict_json=preview.get("conflicts") or [],
        created_by=created_by or "pim_gui",
    )
    group.status = "conflict" if preview.get("conflicts_count", 0) else "reviewed"
    group.conflict_summary = _conflict_summary(preview.get("conflicts") or [])
    session.add(preview_row)
    _record_merge_log(session, preview, dry_run=True, status="planned", created_by=created_by or "pim_gui")
    session.flush()
    return preview


def set_duplicate_group_master(session: Session, group_id: int, product_id: int, *, reviewed_by: str | None = None) -> dict[str, Any]:
    group = _load_duplicate_group(session, group_id)
    if group is None:
        raise ValueError("Dublettengruppe nicht gefunden.")
    product_ids = {item.product_id for item in group.items}
    if product_id not in product_ids:
        raise ValueError("Der neue Master muss Teil der Dublettengruppe sein.")
    group.master_product_id = product_id
    group.reviewed_by = reviewed_by or "pim_gui"
    group.reviewed_at = datetime.now(timezone.utc)
    for item in group.items:
        item.role = "master" if item.product_id == product_id else "duplicate"
        item.selected_for_merge = item.product_id != product_id
    preview = _preview_persisted_group(session, group)
    group.status = "conflict" if preview.get("conflicts_count", 0) else "reviewed"
    group.conflict_summary = _conflict_summary(preview.get("conflicts") or [])
    session.flush()
    return get_duplicate_group_detail(session, group_id) or {}


def ignore_duplicate_group(session: Session, group_id: int, *, reason: str | None = None, reviewed_by: str | None = None) -> dict[str, Any]:
    group = _load_duplicate_group(session, group_id)
    if group is None:
        raise ValueError("Dublettengruppe nicht gefunden.")
    group.status = "ignored"
    group.ignore_reason = reason
    group.reviewed_by = reviewed_by or "pim_gui"
    group.ignored_at = datetime.now(timezone.utc)
    session.flush()
    return _serialize_duplicate_group(group)


def merge_duplicate_group(session: Session, group_id: int, *, yes: bool = False, created_by: str | None = None) -> dict[str, Any]:
    if not yes:
        raise ValueError("Merge erfordert explizite Bestätigung.")
    group = _load_duplicate_group(session, group_id)
    if group is None:
        raise ValueError("Dublettengruppe nicht gefunden.")
    if group.status == "merged":
        raise ValueError("Diese Dublettengruppe wurde bereits gemerged.")
    if group.status == "ignored":
        raise ValueError("Ignorierte Dublettengruppen können nicht gemerged werden.")
    preview = _preview_persisted_group(session, group)
    if preview.get("confidence") == "LOW":
        raise ValueError("LOW-Confidence-Gruppen dürfen nicht automatisch gemerged werden.")
    result = _apply_group_merge(session, preview, created_by=created_by or "pim_gui")
    group.status = "merged"
    group.merged_at = datetime.now(timezone.utc)
    group.merge_log_json = result
    group.conflict_summary = _conflict_summary(result.get("conflicts") or [])
    session.flush()
    return result


def merge_product_duplicates(
    session: Session,
    *,
    confidence: str = "HIGH",
    supplier: str | None = None,
    product_id: int | None = None,
    apply: bool = False,
    yes: bool = False,
    output_dir: str | Path | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    target_confidence = (confidence or "HIGH").upper()
    if target_confidence == "LOW" and apply:
        raise ValueError("LOW-Confidence-Gruppen dürfen nicht automatisch gemerged werden.")
    if apply and not yes:
        raise ValueError("Apply erfordert explizite Bestätigung mit --yes.")

    previews = analyze_product_duplicates(session, supplier=supplier, product_id=product_id, min_confidence=target_confidence)
    applied: list[dict[str, Any]] = []
    for preview in previews:
        if preview["confidence"] == "LOW":
            continue
        if apply:
            applied.append(_apply_group_merge(session, preview, created_by=created_by))
        else:
            applied.append(preview)
            _record_merge_log(session, preview, dry_run=True, status="planned", created_by=created_by)
    report_path = _write_reports(applied, output_dir=output_dir, dry_run=not apply)
    return {
        "dry_run": not apply,
        "groups_count": len(applied),
        "affected_products_count": sum(len(row["duplicate_product_ids"]) + 1 for row in applied),
        "report_path": str(report_path) if report_path else None,
        "groups": applied,
    }


def _upsert_duplicate_group(session: Session, preview: dict[str, Any], *, created_by: str | None) -> ProductDuplicateGroup:
    group = session.scalar(select(ProductDuplicateGroup).where(ProductDuplicateGroup.group_key == str(preview["duplicate_group_id"])))
    confidence = str(preview.get("confidence") or "LOW").upper()
    status = "conflict" if preview.get("conflicts_count", 0) else "open"
    if group is None:
        group = ProductDuplicateGroup(
            group_key=str(preview["duplicate_group_id"]),
            master_product_id=int(preview["master_product_id"]),
            confidence=confidence,
            confidence_score=CONFIDENCE_SCORE.get(confidence, Decimal("0.00")),
            status=status,
            source="rule",
            conflict_summary=_conflict_summary(preview.get("conflicts") or []),
            created_by=created_by or "pim_gui",
        )
        session.add(group)
        session.flush()
    elif group.status not in {"merged", "ignored"}:
        group.master_product_id = int(preview["master_product_id"])
        group.confidence = confidence
        group.confidence_score = CONFIDENCE_SCORE.get(confidence, Decimal("0.00"))
        group.status = status if group.status in {"open", "reviewed", "conflict"} else group.status
        group.conflict_summary = _conflict_summary(preview.get("conflicts") or [])

    current_items = {item.product_id: item for item in group.items}
    all_product_ids = [int(preview["master_product_id"]), *[int(product_id) for product_id in preview.get("duplicate_product_ids", [])]]
    for product_id in all_product_ids:
        role = "master" if product_id == int(preview["master_product_id"]) else "duplicate"
        item = current_items.get(product_id)
        product_conflicts = [row for row in preview.get("conflicts") or [] if int(row.get("product_id") or 0) == product_id]
        if item is None:
            item = ProductDuplicateGroupItem(
                group_id=group.id,
                product_id=product_id,
                role=role,
                confidence_score=CONFIDENCE_SCORE.get(confidence, Decimal("0.00")),
                match_reasons_json=[preview.get("reason")],
                conflict_details_json=product_conflicts,
                selected_for_merge=role != "master",
            )
            session.add(item)
        else:
            item.role = role
            item.confidence_score = CONFIDENCE_SCORE.get(confidence, Decimal("0.00"))
            item.match_reasons_json = [preview.get("reason")]
            item.conflict_details_json = product_conflicts
            item.selected_for_merge = role != "master"
    return group


def _load_duplicate_group(session: Session, group_id: int) -> ProductDuplicateGroup | None:
    result = session.execute(
        select(ProductDuplicateGroup)
        .options(
            joinedload(ProductDuplicateGroup.master_product).joinedload(Product.brand),
            joinedload(ProductDuplicateGroup.master_product).joinedload(Product.assets),
            joinedload(ProductDuplicateGroup.master_product).joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
            joinedload(ProductDuplicateGroup.items).joinedload(ProductDuplicateGroupItem.product).joinedload(Product.brand),
            joinedload(ProductDuplicateGroup.items).joinedload(ProductDuplicateGroupItem.product).joinedload(Product.assets),
            joinedload(ProductDuplicateGroup.items).joinedload(ProductDuplicateGroupItem.product).joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
            joinedload(ProductDuplicateGroup.previews),
        )
        .where(ProductDuplicateGroup.id == int(group_id))
    )
    return result.unique().scalar_one_or_none()


def _preview_persisted_group(session: Session, group: ProductDuplicateGroup) -> dict[str, Any]:
    product_ids = tuple(sorted(item.product_id for item in group.items if item.product_id))
    selected_duplicates = [item.product_id for item in group.items if item.product_id != group.master_product_id and item.selected_for_merge]
    if len(product_ids) < 2 or not selected_duplicates:
        raise ValueError("Dublettengruppe enthält keine ausgewählten Dubletten.")
    preview_ids = tuple(sorted({group.master_product_id, *selected_duplicates}))
    duplicate_group = DuplicateGroup(
        group_id=group.group_key,
        confidence=group.confidence,
        reason=_first_match_reason(group),
        product_ids=preview_ids,
        master_product_id=group.master_product_id,
    )
    return _preview_group(session, duplicate_group)


def _first_match_reason(group: ProductDuplicateGroup) -> str:
    for item in group.items:
        reasons = item.match_reasons_json
        if isinstance(reasons, list) and reasons:
            return str(reasons[0])
    return "manual_review"


def _serialize_duplicate_group(group: ProductDuplicateGroup) -> dict[str, Any]:
    duplicate_count = sum(1 for item in group.items if item.role != "master")
    latest_preview = group.previews[0] if group.previews else None
    master = group.master_product
    return {
        "id": group.id,
        "group_key": group.group_key,
        "master_product_id": group.master_product_id,
        "master_product": _product_label(master),
        "duplicate_count": duplicate_count,
        "confidence": group.confidence,
        "confidence_score": float(group.confidence_score or 0),
        "confidence_display": f"{float(group.confidence_score or 0):.0f} %",
        "confidence_level": _confidence_level(group.confidence_score),
        "status": group.status,
        "source": group.source,
        "conflict_summary": group.conflict_summary or "",
        "has_conflicts": bool(group.conflict_summary),
        "latest_preview_id": latest_preview.id if latest_preview else None,
        "created_at": _format_dt(group.created_at),
        "updated_at": _format_dt(group.updated_at),
        "reviewed_at": _format_dt(group.reviewed_at),
        "merged_at": _format_dt(group.merged_at),
        "ignored_at": _format_dt(group.ignored_at),
    }


def _serialize_group_item(item: ProductDuplicateGroupItem) -> dict[str, Any]:
    product = item.product
    payload = _serialize_product_for_merge(product)
    payload.update(
        {
            "item_id": item.id,
            "role": item.role,
            "confidence_score": float(item.confidence_score or 0),
            "confidence_display": f"{float(item.confidence_score or 0):.0f} %",
            "match_reasons": ", ".join(str(row) for row in (item.match_reasons_json or [])),
            "conflicts": len(item.conflict_details_json or []),
            "selected_for_merge": bool(item.selected_for_merge),
        }
    )
    return payload


def _serialize_product_for_merge(product: Product | None) -> dict[str, Any]:
    if product is None:
        return {}
    variants = list(product.variants or [])
    sale_prices = sum(1 for variant in variants if variant.price is not None)
    cost_prices = sum(1 for variant in variants if variant.cost_price is not None)
    tier_count = sum(len(variant.price_tiers or []) for variant in variants)
    return {
        "product_id": product.id,
        "title": product.title,
        "handle": product.handle,
        "sku": product.sku,
        "status": product.status,
        "brand": product.brand.name if product.brand else "",
        "family_key": product.family_key or "",
        "variant_count": len(variants),
        "asset_count": len(product.assets or []),
        "sale_price_count": sale_prices,
        "cost_price_count": cost_prices,
        "price_tier_count": tier_count,
        "source": _source_haystack(product),
        "created_at": _format_dt(product.created_at),
        "updated_at": _format_dt(product.updated_at),
    }


def _serialize_conflicts(group: ProductDuplicateGroup, preview_payload: dict[str, Any] | list | None) -> list[dict[str, Any]]:
    conflicts = preview_payload.get("conflicts") if isinstance(preview_payload, dict) else None
    if conflicts is None:
        conflicts = []
        for item in group.items:
            conflicts.extend(item.conflict_details_json or [])
    rows = []
    for index, conflict in enumerate(conflicts or [], start=1):
        rows.append(
            {
                "id": index,
                "product_id": conflict.get("product_id") or "",
                "variant_id": conflict.get("variant_id") or "",
                "field": conflict.get("field") or "",
                "master": conflict.get("master") or "",
                "duplicate": conflict.get("duplicate") or "",
                "strategy": _conflict_strategy(conflict),
                "suggestion": _conflict_suggestion(conflict),
            }
        )
    return rows


def _group_search_text(group: ProductDuplicateGroup) -> str:
    parts = [group.group_key, group.status, group.source, _product_label(group.master_product)]
    for item in group.items:
        parts.append(_product_label(item.product))
        parts.append(item.product.family_key if item.product else "")
    return " ".join(str(part or "").lower() for part in parts)


def _product_label(product: Product | None) -> str:
    if product is None:
        return ""
    return f"{product.id} · {product.sku} · {product.title}"


def _confidence_level(score: Decimal | float | None) -> str:
    value = float(score or 0)
    if value >= 90:
        return "hoch"
    if value >= 70:
        return "mittel"
    return "niedrig"


def _conflict_summary(conflicts: list[dict[str, Any]]) -> str:
    if not conflicts:
        return ""
    fields: list[str] = []
    for conflict in conflicts:
        field = str(conflict.get("field") or "Konflikt")
        if field not in fields:
            fields.append(field)
    return ", ".join(fields[:5])


def _conflict_strategy(conflict: dict[str, Any]) -> str:
    field = str(conflict.get("field") or "")
    if field in {"sale_price", "cost_price"}:
        return "preserve_master_and_append_missing"
    return "preserve_master_and_fill_empty_only"


def _conflict_suggestion(conflict: dict[str, Any]) -> str:
    field = str(conflict.get("field") or "")
    if field == "sale_price":
        return "Master-Verkaufspreis behalten; Dublettenpreis nur als Konflikt dokumentieren."
    if field == "cost_price":
        return "Master-Einkaufspreis behalten; fehlende Einkaufsdaten ergänzen, Konflikt manuell prüfen."
    return "Master-Wert behalten; Dublettenwert nur übernehmen, wenn Master leer ist."


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d.%m.%Y %H:%M")


def _load_products(session: Session, *, product_id: int | None, supplier: str | None) -> list[Product]:
    stmt = (
        select(Product)
        .options(
            joinedload(Product.brand),
            joinedload(Product.assets),
            joinedload(Product.translations),
            joinedload(Product.variants).joinedload(ProductVariant.assets),
            joinedload(Product.variants).joinedload(ProductVariant.price_tiers),
            joinedload(Product.variants).joinedload(ProductVariant.channel_listings),
            joinedload(Product.channel_listings),
            joinedload(Product.category_links),
            joinedload(Product.sdb_record),
            joinedload(Product.chemical_documents),
        )
        .where(Product.status != "archived")
    )
    if product_id:
        product = session.get(Product, int(product_id))
        if product is None:
            return []
        keys = {_normalized_sku(product.sku), _normalized_title(product.title)}
        candidates = list(session.scalars(stmt).unique())
        return [row for row in candidates if _normalized_sku(row.sku) in keys or _normalized_title(row.title) in keys or row.id == product.id]
    products = list(session.scalars(stmt).unique())
    if supplier:
        needle = supplier.lower()
        products = [
            row
            for row in products
            if needle in _source_haystack(row).lower()
            or any(needle in _source_haystack(variant).lower() for variant in row.variants)
        ]
    return products


def _detect_duplicate_groups(products: list[Product]) -> list[DuplicateGroup]:
    buckets: dict[str, tuple[str, str, list[Product]]] = {}
    for product in products:
        keys = _product_match_keys(product)
        for confidence, reason, key in keys:
            buckets.setdefault(key, (confidence, reason, []) )[2].append(product)
    seen_sets: set[tuple[int, ...]] = set()
    groups: list[DuplicateGroup] = []
    for key, (confidence, reason, rows) in buckets.items():
        ids = tuple(sorted({row.id for row in rows}))
        if len(ids) < 2 or ids in seen_sets:
            continue
        seen_sets.add(ids)
        ranked_rows = [row for row in rows if row.id in ids]
        master = _choose_master(ranked_rows)
        groups.append(DuplicateGroup(group_id=key, confidence=confidence, reason=reason, product_ids=ids, master_product_id=master.id))
    groups.sort(key=lambda group: (-CONFIDENCE_ORDER[group.confidence], group.group_id))
    return groups


def _product_match_keys(product: Product) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    sku = _normalized_sku(product.sku)
    if sku:
        keys.append(("HIGH", "same_product_sku", f"sku:{sku}"))
    for variant in product.variants:
        variant_sku = _normalized_sku(variant.sku)
        if variant_sku:
            keys.append(("HIGH", "same_variant_sku", f"variant_sku:{variant_sku}"))
        barcode = _normalized_barcode(variant.barcode)
        if barcode:
            keys.append(("HIGH", "same_ean_barcode", f"barcode:{barcode}"))
    title = _normalized_title(product.title)
    brand = _normalized_title(product.brand.name if product.brand else "")
    if brand and title:
        keys.append(("MEDIUM", "same_brand_normalized_title", f"brand_title:{brand}:{title}"))
    if title:
        keys.append(("LOW", "same_normalized_title", f"title:{title}"))
    return keys


def _choose_master(products: list[Product]) -> Product:
    def score(product: Product) -> tuple[int, int, int, int, int, int]:
        source = _source_haystack(product).lower()
        voxster_score = 3 if ("voxster" in source or "voxer" in source or "voxster.ch" in source) else 0
        text_score = int(bool(product.description)) + int(bool(product.source_url_final or product.source_url))
        seo_score = len(product.translations)
        asset_score = len(product.assets)
        sale_price_score = sum(1 for variant in product.variants if variant.price is not None)
        return (voxster_score, sale_price_score, text_score, seo_score, asset_score, -product.id)

    return sorted(products, key=score, reverse=True)[0]


def _preview_group(session: Session, group: DuplicateGroup) -> dict[str, Any]:
    products = [session.get(Product, product_id) for product_id in group.product_ids]
    products = [product for product in products if product is not None]
    master = session.get(Product, group.master_product_id)
    duplicates = [product for product in products if product.id != group.master_product_id]
    conflicts = _detect_conflicts(master, duplicates) if master else []
    return {
        "duplicate_group_id": group.group_id,
        "confidence": group.confidence,
        "reason": group.reason,
        "master_product_id": group.master_product_id,
        "duplicate_product_ids": [product.id for product in duplicates],
        "sku": master.sku if master else None,
        "title": master.title if master else None,
        "merged_assets_count": sum(_count_new_assets(master, product) for product in duplicates) if master else 0,
        "merged_prices_count": sum(_count_price_merges(master, product) for product in duplicates) if master else 0,
        "merged_variants_count": sum(_count_variant_merges(master, product) for product in duplicates) if master else 0,
        "conflicts_count": len(conflicts),
        "conflicts": conflicts,
        "status": "planned",
    }


def _apply_group_merge(session: Session, preview: dict[str, Any], *, created_by: str | None) -> dict[str, Any]:
    master = session.get(Product, int(preview["master_product_id"]))
    if master is None:
        raise ValueError("Master-Produkt nicht gefunden.")
    summary = {
        "fields": [],
        "assets": [],
        "variants": [],
        "prices": [],
        "translations": [],
        "relations": [],
        "archived_products": [],
    }
    for duplicate_id in preview["duplicate_product_ids"]:
        duplicate = session.get(Product, int(duplicate_id))
        if duplicate is None or duplicate.id == master.id:
            continue
        _merge_product_fields(master, duplicate, summary)
        _merge_translations(session, master, duplicate, summary)
        _merge_assets(master, duplicate, summary)
        _merge_variants(session, master, duplicate, summary)
        _merge_relations(master, duplicate, summary)
        duplicate.status = "archived"
        duplicate.dedupe_status = "merged"
        duplicate.merged_into_product_id = master.id
        duplicate.dedupe_notes = f"Merged into product {master.id} at {datetime.now(timezone.utc).isoformat()}"
        summary["archived_products"].append(duplicate.id)
    master.dedupe_status = "master"
    master.source_refs_json = _merged_source_refs(master, preview)
    session.flush()
    result = {**preview, **{k + "_detail": v for k, v in summary.items()}, "status": "merged"}
    _record_merge_log(session, result, dry_run=False, status="merged", created_by=created_by)
    return result


def _merge_product_fields(master: Product, duplicate: Product, summary: dict[str, list]) -> None:
    fallback_fields = [
        "family_key",
        "description",
        "source_url",
        "source_url_final",
        "specifications_text",
        "technical_features_text",
        "cas_number",
        "ec_number",
        "un_number",
        "hazard_class",
        "packing_group",
        "limited_quantity",
        "hazard_shipping_note",
    ]
    for field in fallback_fields:
        if _blank(getattr(master, field)) and not _blank(getattr(duplicate, field)):
            setattr(master, field, getattr(duplicate, field))
            summary["fields"].append({"field": field, "from_product_id": duplicate.id})
    if master.brand is None and duplicate.brand is not None:
        master.brand = duplicate.brand
        summary["fields"].append({"field": "brand", "from_product_id": duplicate.id})
    if duplicate.is_chemical and not master.is_chemical:
        master.is_chemical = True
        summary["fields"].append({"field": "is_chemical", "from_product_id": duplicate.id})


def _merge_translations(session: Session, master: Product, duplicate: Product, summary: dict[str, list]) -> None:
    existing = {row.language_code: row for row in master.translations}
    for translation in list(duplicate.translations):
        master_translation = existing.get(translation.language_code)
        if master_translation is None:
            translation.product_id = master.id
            existing[translation.language_code] = translation
            summary["translations"].append({"language_code": translation.language_code, "action": "moved"})
            continue
        for field in ("short_description", "description", "seo_title", "seo_description", "slug"):
            if _blank(getattr(master_translation, field)) and not _blank(getattr(translation, field)):
                setattr(master_translation, field, getattr(translation, field))
                summary["translations"].append({"language_code": translation.language_code, "field": field, "action": "filled"})


def _merge_assets(master: Product, duplicate: Product, summary: dict[str, list]) -> None:
    existing_keys = {_asset_key(asset) for asset in master.assets}
    next_sort = (max([asset.sort_order for asset in master.assets] or [-1]) + 1)
    for asset in list(duplicate.assets):
        key = _asset_key(asset)
        if key in existing_keys:
            summary["assets"].append({"asset_id": asset.id, "action": "skipped_duplicate", "from_product_id": duplicate.id})
            continue
        asset.product_id = master.id
        asset.sort_order = next_sort
        next_sort += 1
        existing_keys.add(key)
        summary["assets"].append({"asset_id": asset.id, "action": "moved", "from_product_id": duplicate.id})


def _merge_variants(session: Session, master: Product, duplicate: Product, summary: dict[str, list]) -> None:
    master_variants = list(master.variants)
    for source_variant in list(duplicate.variants):
        target_variant = _find_matching_variant(master_variants, source_variant)
        if target_variant is None:
            source_variant.product_id = master.id
            master_variants.append(source_variant)
            summary["variants"].append({"variant_id": source_variant.id, "action": "moved", "sku": source_variant.sku})
            continue
        _merge_variant_fields(target_variant, source_variant, summary)
        _merge_variant_prices(session, target_variant, source_variant, summary)
        _merge_variant_assets(target_variant, source_variant, summary)


def _merge_variant_fields(target: ProductVariant, source: ProductVariant, summary: dict[str, list]) -> None:
    for field in ("variant_title", "option_name", "option_value", "packaging", "currency", "cost_currency", "barcode"):
        if _blank(getattr(target, field)) and not _blank(getattr(source, field)):
            setattr(target, field, getattr(source, field))
            summary["variants"].append({"variant_id": target.id, "source_variant_id": source.id, "field": field, "action": "filled"})
    if target.cost_price is None and source.cost_price is not None:
        target.cost_price = source.cost_price
        target.cost_currency = source.cost_currency or target.cost_currency
        summary["prices"].append({"variant_id": target.id, "field": "cost_price", "source_variant_id": source.id})
    if target.price is None and source.price is not None:
        target.price = source.price
        target.currency = source.currency or target.currency
        summary["prices"].append({"variant_id": target.id, "field": "sale_price_fallback", "source_variant_id": source.id})


def _merge_variant_prices(session: Session, target: ProductVariant, source: ProductVariant, summary: dict[str, list]) -> None:
    existing = {(tier.price_type, tier.currency, tier.min_qty, tier.max_qty) for tier in target.price_tiers}
    for tier in source.price_tiers:
        key = (tier.price_type, tier.currency, tier.min_qty, tier.max_qty)
        if key in existing:
            continue
        session.add(
            ProductVariantPriceTier(
                variant_id=target.id,
                price_type=tier.price_type,
                min_qty=tier.min_qty,
                max_qty=tier.max_qty,
                price=tier.price,
                currency=tier.currency,
            )
        )
        existing.add(key)
        summary["prices"].append({"variant_id": target.id, "tier": list(key), "price": str(tier.price)})


def _merge_variant_assets(target: ProductVariant, source: ProductVariant, summary: dict[str, list]) -> None:
    existing = {_asset_key(asset) for asset in target.assets}
    for asset in list(source.assets):
        key = _asset_key(asset)
        if key in existing:
            continue
        asset.product_id = target.product_id
        asset.variant_id = target.id
        existing.add(key)
        summary["assets"].append({"asset_id": asset.id, "action": "moved_variant_asset", "variant_id": target.id})


def _merge_relations(master: Product, duplicate: Product, summary: dict[str, list]) -> None:
    _move_unique_relations(master.category_links, duplicate.category_links, ("category_id", "sales_channel_id"), "product_id", master.id, summary, "category")
    _move_unique_relations(master.channel_listings, duplicate.channel_listings, ("sales_channel_id",), "product_id", master.id, summary, "product_listing")
    for doc in list(duplicate.chemical_documents):
        doc.product_id = master.id
        summary["relations"].append({"type": "chemical_document", "id": doc.id})
    if master.sdb_record is None and duplicate.sdb_record is not None:
        duplicate.sdb_record.product_id = master.id
        summary["relations"].append({"type": "sdb_record", "id": duplicate.sdb_record.id})


def _move_unique_relations(master_rows: list, duplicate_rows: list, key_fields: tuple[str, ...], product_field: str, master_id: int, summary: dict[str, list], relation_type: str) -> None:
    existing = {tuple(getattr(row, field) for field in key_fields) for row in master_rows}
    for row in list(duplicate_rows):
        key = tuple(getattr(row, field) for field in key_fields)
        if key in existing:
            continue
        setattr(row, product_field, master_id)
        existing.add(key)
        summary["relations"].append({"type": relation_type, "id": row.id})


def _find_matching_variant(candidates: list[ProductVariant], source: ProductVariant) -> ProductVariant | None:
    source_sku = _normalized_sku(source.sku)
    source_barcode = _normalized_barcode(source.barcode)
    source_values = _variant_value_key(source)
    for candidate in candidates:
        if source_sku and source_sku == _normalized_sku(candidate.sku):
            return candidate
        if source_barcode and source_barcode == _normalized_barcode(candidate.barcode):
            return candidate
        if source_values and source_values == _variant_value_key(candidate):
            return candidate
    return None


def _detect_conflicts(master: Product | None, duplicates: list[Product]) -> list[dict[str, Any]]:
    if master is None:
        return [{"field": "master", "message": "Master fehlt"}]
    conflicts: list[dict[str, Any]] = []
    for duplicate in duplicates:
        for field in ("title", "description", "family_key"):
            master_value = getattr(master, field)
            duplicate_value = getattr(duplicate, field)
            if not _blank(master_value) and not _blank(duplicate_value) and str(master_value).strip() != str(duplicate_value).strip():
                conflicts.append({"product_id": duplicate.id, "field": field, "master": str(master_value)[:120], "duplicate": str(duplicate_value)[:120]})
        for source_variant in duplicate.variants:
            target = _find_matching_variant(list(master.variants), source_variant)
            if target and target.price is not None and source_variant.price is not None and Decimal(target.price) != Decimal(source_variant.price):
                conflicts.append({"variant_id": source_variant.id, "field": "sale_price", "master": str(target.price), "duplicate": str(source_variant.price)})
            if target and target.cost_price is not None and source_variant.cost_price is not None and Decimal(target.cost_price) != Decimal(source_variant.cost_price):
                conflicts.append({"variant_id": source_variant.id, "field": "cost_price", "master": str(target.cost_price), "duplicate": str(source_variant.cost_price)})
    return conflicts


def _record_merge_log(session: Session, preview: dict[str, Any], *, dry_run: bool, status: str, created_by: str | None) -> ProductMergeLog:
    log = ProductMergeLog(
        group_key=str(preview["duplicate_group_id"]),
        confidence=str(preview["confidence"]),
        master_product_id=int(preview["master_product_id"]),
        duplicate_product_ids_json=preview["duplicate_product_ids"],
        dry_run=bool(dry_run),
        status=status,
        summary_json={k: v for k, v in preview.items() if k != "conflicts"},
        conflicts_json=preview.get("conflicts") or [],
        created_by=created_by or "product_dedupe_cli",
    )
    session.add(log)
    session.flush()
    return log


def _write_reports(rows: list[dict[str, Any]], *, output_dir: str | Path | None, dry_run: bool) -> Path | None:
    if output_dir is None:
        output_dir = Path("/opt/output/product_dedupe")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = root / f"product-dedupe-{'dry-run' if dry_run else 'apply'}-{stamp}"
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in REPORT_COLUMNS})
    return json_path


def _count_new_assets(master: Product, duplicate: Product) -> int:
    existing = {_asset_key(asset) for asset in master.assets}
    return sum(1 for asset in duplicate.assets if _asset_key(asset) not in existing)


def _count_price_merges(master: Product, duplicate: Product) -> int:
    count = 0
    master_variants = list(master.variants)
    for variant in duplicate.variants:
        target = _find_matching_variant(master_variants, variant)
        if not target:
            count += len(variant.price_tiers) + int(variant.price is not None) + int(variant.cost_price is not None)
            continue
        if target.cost_price is None and variant.cost_price is not None:
            count += 1
        existing = {(tier.price_type, tier.currency, tier.min_qty, tier.max_qty) for tier in target.price_tiers}
        count += sum(1 for tier in variant.price_tiers if (tier.price_type, tier.currency, tier.min_qty, tier.max_qty) not in existing)
    return count


def _count_variant_merges(master: Product, duplicate: Product) -> int:
    master_variants = list(master.variants)
    return sum(1 for variant in duplicate.variants if _find_matching_variant(master_variants, variant) is None)


def _merged_source_refs(master: Product, preview: dict[str, Any]) -> list[dict[str, Any]]:
    refs = master.source_refs_json if isinstance(master.source_refs_json, list) else []
    refs.append({"at": datetime.now(timezone.utc).isoformat(), "type": "dedupe_merge", "group": preview["duplicate_group_id"], "duplicates": preview["duplicate_product_ids"]})
    return refs


def _asset_key(asset: Asset) -> tuple[str, str]:
    if asset.checksum:
        return ("checksum", asset.checksum)
    if asset.source_url:
        return ("source_url", asset.source_url.strip().lower())
    return ("filename", (asset.filename or asset.original_filename or "").strip().lower())


def _variant_value_key(variant: ProductVariant) -> tuple[str, str, str] | None:
    parts = (_normalized_title(variant.option_name), _normalized_title(variant.option_value), _normalized_title(variant.packaging))
    return parts if any(parts) else None


def _normalized_sku(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _normalized_barcode(value: str | None) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalized_title(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _source_haystack(value: Product | ProductVariant) -> str:
    if isinstance(value, Product):
        return " ".join(str(item or "") for item in [value.sku, value.title, value.source_url, value.source_url_final, value.brand.name if value.brand else None])
    return " ".join(str(item or "") for item in [value.sku, value.variant_title, value.barcode])


def _blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def _csv_value(value: object) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return "" if value is None else str(value)
