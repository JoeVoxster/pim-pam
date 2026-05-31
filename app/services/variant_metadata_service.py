from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.db.models import ProductVariant, VariantTechnicalAttribute


def build_medusa_variant_metadata(variant: ProductVariant, *, include_sync_fields: bool = False) -> dict[str, object]:
    metadata: dict[str, object] = {
        "pim_variant_id": variant.id,
        "pim_product_id": variant.product_id,
        "variant_sku": variant.sku,
        "product_sku": variant.product.sku if variant.product else None,
        "manufacturer_sku": variant.manufacturer_sku,
        "vendor_sku": variant.manufacturer_sku,
        "vendor_description": variant.vendor_description,
        "option_name": variant.option_name,
        "option_value": variant.option_value,
        "source": "pim-pam",
        "cost_price": str(variant.cost_price) if variant.cost_price is not None else None,
        "cost_currency": variant.cost_currency,
        "sales_unit": variant.sales_unit,
        "pack_quantity": str(variant.pack_quantity) if variant.pack_quantity is not None else None,
        "pack_unit": variant.pack_unit,
        "packaging": variant.packaging,
        "unit_price": str((variant.price / variant.pack_quantity).quantize(Decimal("0.0001"))) if variant.price is not None and variant.pack_quantity else None,
        "unit_price_unit": variant.pack_unit,
        "unit_cost": str((variant.cost_price / variant.pack_quantity).quantize(Decimal("0.0001"))) if variant.cost_price is not None and variant.pack_quantity else None,
        "origin_country": variant.origin_country,
        "hs_code": _base_hs_code(variant),
        "ch_tariff_code": variant.ch_tariff_code,
        "eu_cn_code": variant.eu_cn_code,
        "eu_taric_code": variant.eu_taric_code,
        "de_import_code": variant.de_import_code,
        "material": variant.material_composition,
        "customs_description_de": variant.customs_description_de,
        "technical_attributes": technical_attributes_metadata(variant),
        "technical_attributes_flat": technical_attributes_flat_metadata(variant),
        "technical_attribute_units": technical_attribute_units_metadata(variant),
    }
    if include_sync_fields:
        metadata["pim_updated_at"] = variant.updated_at.isoformat() if variant.updated_at else None
        metadata["pim_hash"] = None
    return _prune_metadata(metadata)


def technical_attributes_metadata(variant: ProductVariant) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for row in variant.technical_attributes or []:
        value = _technical_attribute_value(row)
        if value in (None, ""):
            continue
        entry: dict[str, object] = {
            "name": row.attribute_name,
            "value": value,
            "display_value": _technical_attribute_display_value(value, row.unit),
            "sort_order": row.sort_order,
        }
        if row.value_text not in (None, ""):
            entry["value_text"] = row.value_text
        if row.value_number is not None:
            entry["value_number"] = value
        if row.unit:
            entry["unit"] = row.unit
        translations = {
            translation.language_code: translation.value_text
            for translation in row.translations or []
            if translation.language_code and translation.value_text
        }
        if translations:
            entry["translations"] = translations
        metadata[row.attribute_code] = entry
    return metadata


def technical_attributes_flat_metadata(variant: ProductVariant) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for row in variant.technical_attributes or []:
        value = _technical_attribute_value(row)
        if value in (None, ""):
            continue
        metadata[row.attribute_code] = value
    return metadata


def technical_attribute_units_metadata(variant: ProductVariant) -> dict[str, str]:
    return {
        row.attribute_code: row.unit
        for row in variant.technical_attributes or []
        if row.unit and _technical_attribute_value(row) not in (None, "")
    }


def _base_hs_code(variant: ProductVariant) -> str | None:
    for value in [variant.ch_tariff_code, variant.eu_cn_code, variant.eu_taric_code, variant.de_import_code]:
        digits = re.sub(r"\D+", "", value or "")
        if len(digits) >= 6:
            return digits[:6]
    return None


def _technical_attribute_value(row: VariantTechnicalAttribute) -> object | None:
    value: object | None = row.value_number if row.value_number is not None else row.value_text
    if isinstance(value, Decimal):
        value = float(value)
    return value


def _technical_attribute_display_value(value: object, unit: str | None) -> str:
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    return f"{text} {unit}".strip() if unit else text


def _prune_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}
