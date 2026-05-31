from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable

import pandas as pd
from slugify import slugify

from app.models import DownloadedAsset, ErrorRecord, ProductOutputRow
from app.utils.variant_options import infer_variant_option_data

DEFAULT_EXPORTS = {
    "products_clean_csv",
    "products_clean_xlsx",
    "products_medusa_csv",
    "products_odoo_csv",
    "asset_mapping_csv",
    "errors_csv",
}
CHANNEL_EXPORT_COLUMNS = [
    "sales_channel_code",
    "product_id",
    "variant_id",
    "product_sku",
    "variant_sku",
    "variant_ean",
    "product_title",
    "short_description",
    "description",
    "slug",
    "variant_title",
    "external_category_id",
    "external_category_path",
    "publication_status",
    "price_enabled",
    "shippable",
    "hazardous_goods",
    "limited_quantity",
    "language_code",
]
APPENDED_PRODUCT_RE = re.compile(
    r"\s+[A-Z]\d{2,3}-[0-9A-Z]+(?:x\d+)?\s+-\s+",
)
REPEATED_COLUMN_PATTERNS = (
    re.compile(r"^Product Category \d+$"),
    re.compile(r"^Product Image \d+$"),
    re.compile(r"^Product Tag \d+$"),
    re.compile(r"^Product Sales Channel \d+$"),
    re.compile(r"^Variant Price [A-Z]{3}$"),
    re.compile(r"^Variant Price \[[^\]]+\] [A-Z]{3}$"),
    re.compile(r"^Variant Option \d+ Name$"),
    re.compile(r"^Variant Option \d+ Value$"),
)
ALLOWED_PRODUCT_COLUMNS = {
    "Product Id",
    "Product Handle",
    "Product Title",
    "Product Subtitle",
    "Product Status",
    "Product Description",
    "Product External Id",
    "Product Thumbnail",
    "Product Collection Id",
    "Product Type Id",
    "Product Discountable",
    "Product Height",
    "Product HS Code",
    "Product Length",
    "Product Material",
    "Product MID Code",
    "Product Origin Country",
    "Product Weight",
    "Product Width",
    "Product Metadata",
    "Shipping Profile Id",
    "Product Is Giftcard",
}
ALLOWED_VARIANT_COLUMNS = {
    "Variant Id",
    "Variant Title",
    "Variant SKU",
    "Variant UPC",
    "Variant EAN",
    "Variant HS Code",
    "Variant MID Code",
    "Variant Manage Inventory",
    "Variant Allow Backorder",
    "Variant Barcode",
    "Variant Height",
    "Variant Length",
    "Variant Material",
    "Variant Metadata",
    "Variant Origin Country",
    "Variant Variant Rank",
    "Variant Width",
    "Variant Weight",
}
ALLOWED_COLUMNS = ALLOWED_PRODUCT_COLUMNS | ALLOWED_VARIANT_COLUMNS
BOOLEAN_COLUMNS = {
    "Product Discountable",
    "Product Is Giftcard",
    "Variant Manage Inventory",
    "Variant Allow Backorder",
}
NUMERIC_COLUMNS = {
    "Product Height",
    "Product Length",
    "Product Weight",
    "Product Width",
    "Variant Height",
    "Variant Length",
    "Variant Weight",
    "Variant Width",
    "Variant Variant Rank",
}
JSON_COLUMNS = {
    "Product Metadata",
    "Variant Metadata",
}


def _frame_from_models(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns)


def ensure_output_dirs(output_dir: str | Path) -> dict[str, Path]:
    base = Path(output_dir)
    paths = {
        "base": base,
        "assets": base / "assets",
        "logs": base / "logs",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def write_products(output_dir: str | Path, products: Iterable[ProductOutputRow], export_types: set[str] | None = None) -> None:
    product_list = list(products)
    rows = [_serialize_product_row(product.model_dump()) for product in product_list]
    extra_field_columns = _collect_extra_field_columns(product_list)
    frame = _frame_from_models(rows, [*list(ProductOutputRow.model_fields.keys()), *extra_field_columns])
    selected_exports = export_types or DEFAULT_EXPORTS
    csv_path = Path(output_dir) / "products_clean.csv"
    xlsx_path = Path(output_dir) / "products_clean.xlsx"
    if "products_clean_csv" in selected_exports:
        frame.to_csv(csv_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    if "products_clean_xlsx" in selected_exports:
        frame.to_excel(xlsx_path, index=False)
    if "products_medusa_csv" in selected_exports:
        write_medusa_products(output_dir, product_list)
    if "products_odoo_csv" in selected_exports:
        write_odoo_products(output_dir, product_list)


def write_channel_export_rows(
    output_dir: str | Path,
    rows: Iterable[dict],
    sales_channel_code: str,
    language_code: str | None = None,
) -> Path:
    row_list = list(rows)
    frame = _frame_from_models(row_list, CHANNEL_EXPORT_COLUMNS)
    language_suffix = f"_{language_code}" if language_code else ""
    path = Path(output_dir) / f"channel_export_{sales_channel_code}{language_suffix}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    return path


def write_asset_mapping(output_dir: str | Path, assets: Iterable[DownloadedAsset], export_types: set[str] | None = None) -> Path | None:
    selected_exports = export_types or DEFAULT_EXPORTS
    if "asset_mapping_csv" not in selected_exports:
        return None
    rows = [asset.model_dump() for asset in assets]
    frame = _frame_from_models(rows, list(DownloadedAsset.model_fields.keys()))
    path = Path(output_dir) / "asset_mapping.csv"
    frame.to_csv(path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    return path


def write_errors(output_dir: str | Path, errors: Iterable[ErrorRecord], export_types: set[str] | None = None) -> Path | None:
    selected_exports = export_types or DEFAULT_EXPORTS
    if "errors_csv" not in selected_exports:
        return None
    rows = [error.model_dump() for error in errors]
    frame = _frame_from_models(rows, list(ErrorRecord.model_fields.keys()))
    path = Path(output_dir) / "errors.csv"
    frame.to_csv(path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    return path


def write_medusa_products(output_dir: str | Path, products: Iterable[ProductOutputRow]) -> Path:
    medusa_rows = _dedupe_medusa_rows([_to_medusa_row(product) for product in products])
    max_images = max((len(row.get("_images", [])) for row in medusa_rows), default=0)
    static_columns = {
        "Product Handle",
        "Product Title",
        "Product Status",
        "Product Description",
        "Product External Id",
        "Product Thumbnail",
        "Variant Title",
        "Variant SKU",
        "Variant EAN",
        "Variant Barcode",
        "Variant Manage Inventory",
        "Variant Allow Backorder",
        "Variant Metadata",
        "Variant Option 1 Name",
        "Variant Option 1 Value",
        "Variant Variant Rank",
    }
    dynamic_columns = _collect_dynamic_medusa_columns(medusa_rows, excluded_columns=static_columns)
    normalized_rows: list[dict[str, object]] = []
    image_owner_handles: set[str] = set()
    for row in medusa_rows:
        images = row.pop("_images", [])
        normalized = dict(row)
        handle = str(normalized.get("Product Handle") or "")
        write_product_images = handle not in image_owner_handles
        if write_product_images and handle:
            image_owner_handles.add(handle)
        elif not write_product_images:
            normalized["Product Thumbnail"] = None
        for index in range(max_images):
            normalized[f"Product Image {index + 1}"] = images[index] if write_product_images and index < len(images) else None
        normalized_rows.append(normalized)

    columns = [
        "Product Handle",
        "Product Title",
        "Product Status",
        "Product Description",
        "Product External Id",
        "Product Thumbnail",
        *[f"Product Image {index + 1}" for index in range(max_images)],
        *dynamic_columns,
        "Variant Title",
        "Variant SKU",
        "Variant EAN",
        "Variant Barcode",
        "Variant Manage Inventory",
        "Variant Allow Backorder",
        "Variant Metadata",
        "Variant Option 1 Name",
        "Variant Option 1 Value",
        "Variant Variant Rank",
    ]
    _validate_medusa_columns(columns)
    frame = _frame_from_models(normalized_rows, columns)
    _validate_medusa_rows(frame.to_dict(orient="records"), columns)
    path = Path(output_dir) / "products_medusa_import.csv"
    frame.to_csv(path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    return path


def write_odoo_products(output_dir: str | Path, products: Iterable[ProductOutputRow]) -> tuple[Path, Path]:
    product_list = list(products)
    templates = _odoo_template_rows(product_list)
    variants = _odoo_variant_rows(product_list)
    template_path = Path(output_dir) / "products_odoo_templates.csv"
    variant_path = Path(output_dir) / "products_odoo_variants.csv"
    _frame_from_models(
        templates,
        [
            "Template External ID",
            "Name",
            "Internal Reference",
            "Brand",
            "Description",
            "Sales Price",
            "Cost Price",
            "Currency",
            "Attribute 1 Name",
            "Attribute 1 Values",
            "Source URL",
        ],
    ).to_csv(template_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    _frame_from_models(
        variants,
        [
            "Template External ID",
            "Variant SKU",
            "Variant Name",
            "Attribute 1 Name",
            "Attribute 1 Value",
            "Barcode",
            "EAN",
            "Sales Price",
            "Cost Price",
            "Currency",
            "Source URL",
        ],
    ).to_csv(variant_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    return template_path, variant_path


def _to_medusa_row(product: ProductOutputRow) -> dict[str, object]:
    image_urls = _split_pipe_values(product.image_urls)
    variant_options = infer_variant_option_data(
        sku=product.variant_sku or product.supplier_sku,
        title=product.product_name or product.product_title or product.title_raw or product.supplier_sku,
        variant_title=product.variant_title,
        extra_fields=product.extra_fields,
        existing_option_name=product.variant_option_1_name,
        existing_option_value=product.variant_option_1_value,
    )
    product_title = _normalize_export_text(variant_options.product_title or product.product_name or product.product_title)
    variant_title = _normalize_export_text(product.variant_title or product.product_title or product.product_name)
    option_name = variant_options.option_name or product.variant_option_1_name
    option_value = variant_options.option_value or product.variant_option_1_value
    custom_fields = _map_custom_medusa_fields(product.extra_fields or {})
    metadata = {
        "source_url": product.source_url_final or product.source_url,
        "supplier_sku": product.supplier_sku,
        "variant_sku": product.variant_sku,
        "datasheet_urls": _split_pipe_values(product.datasheet_urls),
        "sds_urls": _split_pipe_values(product.sds_urls),
        "specifications": _split_pipe_values(product.specifications),
        "technical_features": _split_pipe_values(product.technical_features),
    }
    row = {
        "Product Handle": custom_fields.pop("Product Handle", None) or slugify(product_title or "product", separator="-"),
        "Product Title": product_title,
        "Product Status": "published" if product.status == "ok" else "draft",
        "Product Description": _sanitize_medusa_description(_normalize_export_text(product.description)),
        "Product External Id": product.source_url_final or product.source_url or product.supplier_sku,
        "Product Thumbnail": image_urls[0] if image_urls else None,
        "_images": image_urls,
        "Variant Title": variant_title,
        "Variant SKU": product.variant_sku or product.supplier_sku,
        "Variant EAN": product.ean,
        "Variant Barcode": product.barcode,
        "Variant Manage Inventory": "FALSE",
        "Variant Allow Backorder": "FALSE",
        "Variant Metadata": json.dumps({k: v for k, v in metadata.items() if v}, ensure_ascii=True),
        "Variant Option 1 Name": option_name,
        "Variant Option 1 Value": option_value,
        "Variant Variant Rank": 0,
    }
    row.update(custom_fields)
    return row


def _odoo_template_rows(products: list[ProductOutputRow]) -> list[dict[str, object]]:
    grouped: dict[str, list[ProductOutputRow]] = {}
    for product in products:
        variant_options = infer_variant_option_data(
            sku=product.variant_sku or product.supplier_sku,
            title=product.product_name or product.product_title or product.title_raw or product.supplier_sku,
            variant_title=product.variant_title,
            extra_fields=product.extra_fields,
            existing_option_name=product.variant_option_1_name,
            existing_option_value=product.variant_option_1_value,
        )
        external_id = product.supplier_sku
        grouped.setdefault(external_id, []).append(product)

    rows: list[dict[str, object]] = []
    for external_id, items in grouped.items():
        first = items[0]
        variant_options = infer_variant_option_data(
            sku=first.variant_sku or first.supplier_sku,
            title=first.product_name or first.product_title or first.title_raw or first.supplier_sku,
            variant_title=first.variant_title,
            extra_fields=first.extra_fields,
            existing_option_name=first.variant_option_1_name,
            existing_option_value=first.variant_option_1_value,
        )
        option_values = sorted(
            {
                infer_variant_option_data(
                    sku=item.variant_sku or item.supplier_sku,
                    title=item.product_name or item.product_title or item.title_raw or item.supplier_sku,
                    variant_title=item.variant_title,
                    extra_fields=item.extra_fields,
                    existing_option_name=item.variant_option_1_name,
                    existing_option_value=item.variant_option_1_value,
                ).option_value
                for item in items
            }
            - {None}
        )
        rows.append(
            {
                "Template External ID": external_id,
                "Name": variant_options.product_title or first.product_name or first.product_title,
                "Internal Reference": external_id,
                "Brand": first.brand or first.supplier_name,
                "Description": first.description,
                "Sales Price": _odoo_price(first, "sales"),
                "Cost Price": _odoo_price(first, "cost"),
                "Currency": _odoo_currency(first),
                "Attribute 1 Name": variant_options.option_name,
                "Attribute 1 Values": ", ".join(option_values) if option_values else None,
                "Source URL": first.source_url_final or first.source_url,
            }
        )
    return rows


def _odoo_variant_rows(products: list[ProductOutputRow]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for product in products:
        variant_options = infer_variant_option_data(
            sku=product.variant_sku or product.supplier_sku,
            title=product.product_name or product.product_title or product.title_raw or product.supplier_sku,
            variant_title=product.variant_title,
            extra_fields=product.extra_fields,
            existing_option_name=product.variant_option_1_name,
            existing_option_value=product.variant_option_1_value,
        )
        rows.append(
            {
                "Template External ID": product.supplier_sku,
                "Variant SKU": product.variant_sku or product.supplier_sku,
                "Variant Name": product.variant_title or product.product_title or product.product_name,
                "Attribute 1 Name": variant_options.option_name,
                "Attribute 1 Value": variant_options.option_value or product.variant_option_1_value,
                "Barcode": product.barcode,
                "EAN": product.ean,
                "Sales Price": _odoo_price(product, "sales"),
                "Cost Price": _odoo_price(product, "cost"),
                "Currency": _odoo_currency(product),
                "Source URL": product.source_url_final or product.source_url,
            }
        )
    return rows


def _split_pipe_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _odoo_price(product: ProductOutputRow, price_type: str) -> int | float | None:
    extra_fields = product.extra_fields or {}
    if price_type == "sales":
        value = extra_fields.get("sales_price") or extra_fields.get("price") or extra_fields.get("Preis")
    else:
        value = extra_fields.get("purchase_price") or extra_fields.get("cost_price")
    if value in {None, ""}:
        return None
    try:
        return _coerce_numeric(value)
    except (TypeError, ValueError):
        return None


def _odoo_currency(product: ProductOutputRow) -> str | None:
    extra_fields = product.extra_fields or {}
    value = extra_fields.get("sales_currency") or extra_fields.get("purchase_currency") or extra_fields.get("currency")
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _map_custom_medusa_fields(extra_fields: dict[str, object]) -> dict[str, object]:
    mapped: dict[str, object] = {}
    direct_map = {
        "medusa_product_collection_id": "Product Collection Id",
        "medusa_product_handle": "Product Handle",
        "medusa_product_type_id": "Product Type Id",
        "medusa_product_subtitle": "Product Subtitle",
        "medusa_product_discountable": "Product Discountable",
        "medusa_product_height": "Product Height",
        "medusa_product_hs_code": "Product HS Code",
        "medusa_product_length": "Product Length",
        "medusa_product_material": "Product Material",
        "medusa_product_mid_code": "Product MID Code",
        "medusa_product_origin_country": "Product Origin Country",
        "medusa_product_weight": "Product Weight",
        "medusa_product_width": "Product Width",
        "medusa_product_metadata": "Product Metadata",
        "medusa_product_is_giftcard": "Product Is Giftcard",
        "medusa_shipping_profile_id": "Shipping Profile Id",
        "medusa_variant_manage_inventory": "Variant Manage Inventory",
        "medusa_variant_allow_backorder": "Variant Allow Backorder",
        "medusa_variant_id": "Variant Id",
        "medusa_variant_upc": "Variant UPC",
        "medusa_variant_mid_code": "Variant MID Code",
        "medusa_variant_hs_code": "Variant HS Code",
        "medusa_variant_origin_country": "Variant Origin Country",
        "medusa_variant_weight": "Variant Weight",
        "medusa_variant_length": "Variant Length",
        "medusa_variant_width": "Variant Width",
        "medusa_variant_height": "Variant Height",
        "medusa_variant_material": "Variant Material",
        "medusa_variant_metadata": "Variant Metadata",
    }
    for source_key, target_key in direct_map.items():
        if source_key in extra_fields and extra_fields[source_key] is not None:
            mapped[target_key] = _normalize_medusa_value(target_key, extra_fields[source_key])

    mapped.update(_expand_list_fields(extra_fields, "medusa_product_category", "medusa_product_categories", "Product Category"))
    mapped.update(_expand_list_fields(extra_fields, "medusa_product_tag", "medusa_product_tags", "Product Tag"))
    mapped.update(
        _expand_list_fields(
            extra_fields,
            "medusa_product_sales_channel",
            "medusa_product_sales_channels",
            "Product Sales Channel",
        )
    )
    mapped.update(_expand_price_fields(extra_fields))
    return mapped


def _expand_list_fields(
    extra_fields: dict[str, object],
    key_prefix: str,
    plural_key: str,
    column_prefix: str,
) -> dict[str, object]:
    mapped: dict[str, object] = {}
    if plural_key in extra_fields and extra_fields[plural_key]:
        values = _split_pipe_values(str(extra_fields[plural_key]))
        for index, value in enumerate(values, start=1):
                mapped[f"{column_prefix} {index}"] = value
    for key, value in extra_fields.items():
        if not key.startswith(f"{key_prefix}_") or value is None:
            continue
        suffix = key.removeprefix(f"{key_prefix}_")
        if suffix.isdigit():
            mapped[f"{column_prefix} {suffix}"] = value
    return mapped


def _expand_price_fields(extra_fields: dict[str, object]) -> dict[str, object]:
    mapped: dict[str, object] = {}
    for key, value in extra_fields.items():
        if value is None:
            continue
        lower = key.lower()
        if lower.startswith("medusa_variant_price_"):
            currency = lower.removeprefix("medusa_variant_price_").upper()
            mapped[f"Variant Price {currency}"] = _coerce_numeric(value)
        elif lower.startswith("medusa_variant_price_region_"):
            region_data = str(value).split("|")
            region_key = lower.removeprefix("medusa_variant_price_region_").replace("_", " ").title()
            if len(region_data) == 2:
                currency, amount = region_data
                mapped[f"Variant Price {region_key} [{currency.strip().upper()}]"] = _coerce_numeric(amount.strip())
    fallback_price = extra_fields.get("sales_price")
    fallback_currency = (
        extra_fields.get("sales_currency")
        or extra_fields.get("currency")
        or extra_fields.get("purchase_currency")
    )
    if fallback_price is None:
        fallback_price = extra_fields.get("purchase_price") or extra_fields.get("price") or extra_fields.get("Preis")
        fallback_currency = fallback_currency or extra_fields.get("purchase_currency")
    if fallback_price is not None:
        currency = str(fallback_currency or "EUR").strip().upper()
        mapped.setdefault(f"Variant Price {currency}", _coerce_numeric(fallback_price))
    return mapped


def _collect_dynamic_medusa_columns(rows: list[dict[str, object]], excluded_columns: set[str] | None = None) -> list[str]:
    preferred_prefixes = (
        "Product Collection Id",
        "Product Type Id",
        "Product Subtitle",
        "Product Discountable",
        "Product Height",
        "Product HS Code",
        "Product Length",
        "Product Material",
        "Product MID Code",
        "Product Origin Country",
        "Product Weight",
        "Product Width",
        "Product Metadata",
        "Product Category ",
        "Product Tag ",
        "Product Sales Channel ",
        "Shipping Profile Id",
        "Product Is Giftcard",
        "Variant Price ",
        "Variant Id",
        "Variant UPC",
        "Variant HS Code",
        "Variant MID Code",
        "Variant Origin Country",
        "Variant Weight",
        "Variant Length",
        "Variant Width",
        "Variant Height",
        "Variant Material",
        "Variant Metadata",
        "Variant Option ",
    )
    seen: list[str] = []
    excluded = excluded_columns or set()
    for row in rows:
        for key in row.keys():
            if key.startswith("_") or key in seen or key in excluded:
                continue
            if key.startswith(preferred_prefixes):
                seen.append(key)
    return seen


def _serialize_product_row(row: dict[str, object]) -> dict[str, object]:
    serialized = dict(row)
    for key, value in list(serialized.items()):
        if isinstance(value, str):
            serialized[key] = _normalize_export_text(value)
    extra_fields = serialized.get("extra_fields")
    if isinstance(extra_fields, dict):
        for key, value in extra_fields.items():
            if key not in serialized:
                serialized[key] = _normalize_export_text(value) if isinstance(value, str) else value
        serialized["extra_fields"] = json.dumps(extra_fields, ensure_ascii=True)
    return serialized


def _collect_extra_field_columns(products: Iterable[ProductOutputRow]) -> list[str]:
    columns: list[str] = []
    seen: set[str] = set()
    reserved = set(ProductOutputRow.model_fields.keys())
    for product in products:
        for key in (product.extra_fields or {}).keys():
            if key in reserved or key in seen:
                continue
            seen.add(key)
            columns.append(key)
    return columns


def _dedupe_medusa_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    deduped: list[dict[str, object]] = []
    best_rows: dict[tuple[object, object], dict[str, object]] = {}
    for row in rows:
        key = (row.get("Product Handle"), row.get("Variant SKU"))
        existing = best_rows.get(key)
        if existing is None or _medusa_row_quality(row) > _medusa_row_quality(existing):
            best_rows[key] = row
    deduped.extend(best_rows.values())
    return deduped


def _normalize_export_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in ("Â", "Ã", "â")):
        return value
    try:
        fixed = value.encode("latin-1").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _sanitize_medusa_description(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = APPENDED_PRODUCT_RE.search(value)
    if match is None:
        return value
    return value[: match.start()].rstrip(" -")


def _medusa_row_quality(row: dict[str, object]) -> tuple[int, int, int]:
    description = str(row.get("Product Description") or "")
    price_present = int(any(str(key).startswith("Variant Price ") and row.get(key) not in {None, ""} for key in row.keys()))
    option_present = int(bool(row.get("Variant Option 1 Value")))
    price_value = _extract_highest_variant_price(row)
    return (price_present, option_present, price_value, -len(description))


def _extract_highest_variant_price(row: dict[str, object]) -> float:
    values: list[float] = []
    for key, value in row.items():
        if not str(key).startswith("Variant Price ") or value in {None, ""}:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return max(values, default=-1.0)


def _validate_medusa_columns(columns: list[str]) -> None:
    unknown = [column for column in columns if not _is_allowed_medusa_column(column)]
    if unknown:
        raise ValueError(f"Unknown Medusa columns: {', '.join(unknown)}")


def _validate_medusa_rows(rows: list[dict[str, object]], columns: list[str]) -> None:
    for index, row in enumerate(rows, start=2):
        if not row.get("Product Id") and not row.get("Product Handle"):
            raise ValueError(f"Row {index}: missing Product Id or Product Handle")

        for option_number in range(1, 10):
            name_key = f"Variant Option {option_number} Name"
            value_key = f"Variant Option {option_number} Value"
            if row.get(name_key) and not row.get(value_key):
                raise ValueError(f"Row {index}: {name_key} set without {value_key}")

        for column in columns:
            value = row.get(column)
            if value in {None, ""}:
                continue
            if column in BOOLEAN_COLUMNS and str(value).upper() not in {"TRUE", "FALSE"}:
                raise ValueError(f"Row {index}: {column} is not a valid boolean")
            if column in JSON_COLUMNS:
                try:
                    json.loads(str(value))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Row {index}: {column} is not valid JSON") from exc
            if column in NUMERIC_COLUMNS or column.startswith("Variant Price "):
                _coerce_numeric(value, row_index=index, column_name=column)


def _is_allowed_medusa_column(column: str) -> bool:
    if column in ALLOWED_COLUMNS:
        return True
    return any(pattern.match(column) for pattern in REPEATED_COLUMN_PATTERNS)


def _normalize_medusa_value(column: str, value: object) -> object:
    if value is None:
        return None
    if column in BOOLEAN_COLUMNS:
        return "TRUE" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "FALSE"
    if column in JSON_COLUMNS:
        if isinstance(value, str):
            try:
                json.loads(value)
                return value
            except json.JSONDecodeError:
                pass
        return json.dumps(value, ensure_ascii=True)
    if column in NUMERIC_COLUMNS:
        return _coerce_numeric(value)
    if isinstance(value, str):
        return _normalize_export_text(value)
    return value


def _coerce_numeric(value: object, row_index: int | None = None, column_name: str | None = None) -> int | float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        if row_index is not None and column_name is not None:
            raise ValueError(f"Row {row_index}: {column_name} is not numeric") from exc
        raise
    return int(numeric) if numeric.is_integer() else numeric
