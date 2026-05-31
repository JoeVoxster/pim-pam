from __future__ import annotations

import json
from decimal import Decimal

from slugify import slugify

from app.models import ProductOutputRow
from app.schemas.pim import ImportMappingConfig
from app.utils.variant_options import infer_variant_option_data


def category_values(product: ProductOutputRow, config: ImportMappingConfig) -> list[str]:
    values: list[str] = []
    for column in config.category_columns:
        value = product.extra_fields.get(column) if product.extra_fields else None
        if value:
            if isinstance(value, str):
                values.extend([part.strip() for part in value.split("|") if part.strip()])
            else:
                values.append(str(value))
    return values


def product_payload(product: ProductOutputRow) -> dict[str, object]:
    title = product.product_name or product.product_title or product.title_raw or product.supplier_sku
    variant_options = infer_variant_option_data(
        sku=product.supplier_sku,
        title=title,
        variant_title=product.variant_title,
        extra_fields=product.extra_fields,
        existing_option_name=product.variant_option_1_name,
        existing_option_value=product.variant_option_1_value,
    )
    product_sku = product.supplier_sku
    product_title = variant_options.product_title or title
    payload = {
        "sku": product_sku,
        "handle": slugify(product_title, separator="-"),
        "source_language": _infer_source_language(product),
        "title": product_title,
        "description": product.description or product.description_raw,
        "source_url": product.source_url,
        "source_url_final": product.source_url_final,
        "specifications_text": product.specifications,
        "technical_features_text": product.technical_features,
        "brand_name": product.brand or product.supplier_name,
        "status": "active" if product.status == "ok" else "draft",
    }
    payload.update(_chemical_fields(product))
    return payload


def product_short_description(product: ProductOutputRow) -> str | None:
    extra_fields = product.extra_fields or {}
    explicit = _string_from_candidates(
        extra_fields,
        [
            "short_description",
            "shortDescription",
            "short_desc",
            "kurzbeschreibung",
            "kurz_beschreibung",
            "meta_description",
            "seo_description",
        ],
    )
    if explicit:
        return explicit
    description = _string_or_none(product.description) or _string_or_none(product.description_raw)
    if description and len(description) <= 250:
        return description
    return None


def variant_payload(product: ProductOutputRow, config: ImportMappingConfig) -> dict[str, object]:
    extra_fields = product.extra_fields or {}
    variant_options = infer_variant_option_data(
        sku=product.variant_sku or product.supplier_sku,
        title=product.product_name or product.product_title or product.title_raw or product.supplier_sku,
        variant_title=product.variant_title,
        extra_fields=extra_fields,
        existing_option_name=product.variant_option_1_name,
        existing_option_value=product.variant_option_1_value,
    )
    import_kind = _string_or_none(extra_fields.get("import_kind"))
    sale_price = _decimal_from_candidates(extra_fields, ["sales_price", "price", "Preis"])
    purchase_price = _decimal_from_candidates(extra_fields, ["purchase_price", "cost_price", "Einkaufspreis"])
    price = sale_price
    if price is None and import_kind not in {"sales_price_list", "supplier_price_list"}:
        price = _decimal_from_candidates(extra_fields, config.price_column_candidates)
    currency = (
        _string_or_none(extra_fields.get("sales_currency"))
        or _string_or_none(extra_fields.get("purchase_currency"))
        or config.default_currency
    )
    cost_currency = (
        _string_or_none(extra_fields.get("purchase_currency"))
        or _string_or_none(extra_fields.get("cost_currency"))
        or currency
    )
    stock_qty = _int_or_zero(extra_fields.get(config.stock_column)) if config.stock_column else 0
    return {
        "sku": product.variant_sku or product.supplier_sku,
        "variant_title": product.variant_title or product.product_title or product.product_name,
        "option_name": variant_options.option_name or product.variant_option_1_name,
        "option_value": variant_options.option_value or product.variant_option_1_value,
        "packaging": variant_options.option_value if variant_options.option_name == "Packaging" else product.variant_option_1_value,
        "price": price,
        "currency": currency,
        "cost_price": purchase_price,
        "cost_currency": cost_currency,
        "stock_qty": stock_qty,
        "barcode": product.barcode or product.ean,
    }


def price_tier_payload(product: ProductOutputRow, config: ImportMappingConfig) -> dict[str, object] | None:
    extra_fields = product.extra_fields or {}
    min_qty = _int_or_none(extra_fields.get("Menge_min")) or _int_or_none(extra_fields.get("min_qty")) or 1
    max_qty = _int_or_none(extra_fields.get("Menge_max")) or _int_or_none(extra_fields.get("max_qty"))
    tier_price = _decimal_from_candidates(extra_fields, ["tier_price", "sales_price", "purchase_price", "price", "Preis"])
    if tier_price is None:
        return None
    import_kind = _string_or_none(extra_fields.get("import_kind"))
    price_type = "purchase" if import_kind == "supplier_price_list" else "sale"
    currency = (
        _string_or_none(extra_fields.get("sales_currency"))
        or _string_or_none(extra_fields.get("purchase_currency"))
        or config.default_currency
    )
    return {
        "min_qty": min_qty,
        "max_qty": max_qty,
        "price": tier_price,
        "currency": currency or config.default_currency,
        "price_type": price_type,
    }


def asset_paths(product: ProductOutputRow) -> list[str]:
    values: list[str] = []
    for field in [product.image_paths, product.pdf_paths, product.datasheet_paths, product.sds_paths]:
        if not field:
            continue
        values.extend([item.strip() for item in str(field).split("|") if item.strip()])
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def raw_payload(product: ProductOutputRow) -> dict:
    payload = product.model_dump()
    if isinstance(payload.get("extra_fields"), dict):
        return payload
    if isinstance(payload.get("extra_fields"), str):
        try:
            payload["extra_fields"] = json.loads(payload["extra_fields"])
        except json.JSONDecodeError:
            payload["extra_fields"] = {}
    return payload


def _decimal_from_candidates(extra_fields: dict[str, object], candidates: list[str]) -> Decimal | None:
    for key in candidates:
        value = extra_fields.get(key)
        if value in {None, ""}:
            continue
        try:
            decimal_value = Decimal(str(value))
            if not decimal_value.is_finite():
                continue
            return decimal_value
        except Exception:
            continue
    return None


def _int_or_zero(value: object) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return 0


def _int_or_none(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(float(str(value)))
    except Exception:
        return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_source_language(product: ProductOutputRow) -> str:
    extra_fields = product.extra_fields or {}
    for key in ("source_language", "language_code", "language", "lang", "original_language"):
        value = _string_or_none(extra_fields.get(key))
        if value:
            return value
    return "en"


def _chemical_fields(product: ProductOutputRow) -> dict[str, object]:
    extra_fields = product.extra_fields or {}
    categories = " | ".join(category for category in [extra_fields.get("category"), extra_fields.get("categories"), extra_fields.get("_category")] if category)
    sds_url = _string_from_candidates(extra_fields, ["sds_url", "safety_data_sheet_url", "sicherheitsdatenblatt_url"])
    sds_available = _bool_from_candidates(extra_fields, ["sds_available", "sdb_vorhanden"])
    if sds_available is None:
        sds_available = bool(sds_url or product.sds_paths or product.datasheet_paths)
    hazard_class = _string_from_candidates(extra_fields, ["hazard_class", "gefahrgutklasse", "adr_class"])
    un_number = _string_from_candidates(extra_fields, ["un_number", "un", "un_nummer", "UN-Nummer"])
    adr_relevant = _bool_from_candidates(extra_fields, ["adr_relevant", "adr"])
    if adr_relevant is None:
        adr_relevant = bool(hazard_class or un_number or _string_from_candidates(extra_fields, ["limited_quantity", "lq", "ADR"]))
    is_chemical = _bool_from_candidates(extra_fields, ["is_chemical", "chemical_product", "chemieprodukt"])
    if is_chemical is None:
        is_chemical = bool(
            _string_from_candidates(extra_fields, ["cas_number", "cas", "CAS Nummer", "ec_number", "eg_number", "EG-Nummer", "signalwort", "signal_word"])
            or "chem" in categories.lower()
            or "laugen" in categories.lower()
        )
    payload = {
        "is_chemical": is_chemical,
        "chemical_type": _string_from_candidates(extra_fields, ["chemical_type", "chem_type", "stoffgruppe", "substance_group"]),
        "cas_number": _string_from_candidates(extra_fields, ["cas_number", "cas", "CAS Nummer"]),
        "ec_number": _string_from_candidates(extra_fields, ["ec_number", "eg_number", "EG-Nummer"]),
        "un_number": un_number,
        "hazard_class": hazard_class,
        "packing_group": _string_from_candidates(extra_fields, ["packing_group", "verpackungsgruppe"]),
        "adr_relevant": adr_relevant,
        "ghs_pictograms": _string_from_candidates(extra_fields, ["ghs_pictograms", "ghs", "ghs_symbols"]),
        "signal_word": _string_from_candidates(extra_fields, ["signal_word", "signalwort"]),
        "hazard_statements": _string_from_candidates(extra_fields, ["hazard_statements", "h_statements", "Gefahrenhinweise"]),
        "precautionary_statements": _string_from_candidates(extra_fields, ["precautionary_statements", "p_statements", "Sicherheitshinweise"]),
        "wgk": _string_from_candidates(extra_fields, ["wgk"]),
        "storage_class": _string_from_candidates(extra_fields, ["storage_class", "lagerklasse"]),
        "sds_available": sds_available,
        "sds_url": sds_url,
        "chemical_reference_url": _string_from_candidates(extra_fields, ["chemical_reference_url", "reference_url", "referenz_url"]),
        "chemical_enrichment_status": _string_from_candidates(extra_fields, ["chemical_enrichment_status", "enrichment_status"]),
        "chemical_enrichment_error": _string_from_candidates(extra_fields, ["chemical_enrichment_error", "enrichment_error"]),
        "density": _string_from_candidates(extra_fields, ["density", "dichte"]),
        "color": _string_from_candidates(extra_fields, ["color", "farbe"]),
        "odor": _string_from_candidates(extra_fields, ["odor", "geruch"]),
        "ph_value": _string_from_candidates(extra_fields, ["ph_value", "ph"]),
        "flash_point": _string_from_candidates(extra_fields, ["flash_point", "flammpunkt"]),
        "boiling_point": _string_from_candidates(extra_fields, ["boiling_point", "siedepunkt", "siedebereich"]),
        "viscosity": _string_from_candidates(extra_fields, ["viscosity", "viskositaet", "viskosität"]),
        "solubility": _string_from_candidates(extra_fields, ["solubility", "löslichkeit", "loeslichkeit"]),
        "business_only": _bool_from_candidates(extra_fields, ["business_only", "nur_gewerbe", "professional_use_only"]),
        "age_check_required": _bool_from_candidates(extra_fields, ["age_check_required", "alterspruefung", "altersprüfung"]),
        "shippable": _bool_from_candidates(extra_fields, ["shippable", "versandfaehig", "versandfähig"]),
        "limited_quantity": _string_from_candidates(extra_fields, ["limited_quantity", "lq", "begrenzte_mengen"]),
        "hazard_shipping_note": _string_from_candidates(extra_fields, ["hazard_shipping_note", "gefahrgutversand_hinweis", "ADR"]),
        "shop_active": _bool_from_candidates(extra_fields, ["shop_active", "im_shop_aktiv"]),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _string_from_candidates(extra_fields: dict[str, object], candidates: list[str]) -> str | None:
    for key in candidates:
        value = _string_or_none(extra_fields.get(key))
        if value:
            return value
    return None


def _bool_from_candidates(extra_fields: dict[str, object], candidates: list[str]) -> bool | None:
    truthy = {"1", "true", "yes", "ja", "y", "x"}
    falsy = {"0", "false", "no", "nein", "n"}
    for key in candidates:
        raw_value = extra_fields.get(key)
        if raw_value in {None, ""}:
            continue
        if isinstance(raw_value, bool):
            return raw_value
        text = str(raw_value).strip().lower()
        if text in truthy:
            return True
        if text in falsy:
            return False
    return None
