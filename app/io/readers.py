from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
from urllib.parse import urljoin

import pandas as pd

from app.models import ProductInputRow

REQUIRED_LIKE_COLUMNS = {
    "supplier_sku",
    "supplier_name",
    "source_url",
    "title_raw",
    "description_raw",
    "brand",
    "ean",
}

FIELD_ALIASES = {
    "supplier_sku": {"supplier_sku", "sku", "artikel", "artikelnummer", "item", "itemno", "code", "productcode"},
    "supplier_name": {"supplier_name", "supplier", "lieferant", "vendor"},
    "source_url": {"source_url", "url", "link", "website", "webseite", "produkturl", "producturl"},
    "title_raw": {"title_raw", "title", "name", "productname", "bezeichnung", "produktname", "descrizionebreve"},
    "description_raw": {"description_raw", "description", "beschreibung", "descrizione", "details"},
    "brand": {"brand", "marke", "marca"},
    "ean": {"ean", "barcode", "gtin"},
}

PRICE_ALIASES = {
    "preis",
    "price",
    "netprice",
    "grossprice",
    "unitprice",
    "verkaufspreis",
    "einkaufspreis",
    "purchaseprice",
    "salesprice",
}

MAGENTO_IMPORT_KIND = "magento_1_export"


def read_products(
    input_path: str | Path,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
) -> list[ProductInputRow]:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    frame = _read_input_frame(path, sheet_name=sheet_name, sheet_index=sheet_index)
    frame = frame.where(pd.notna(frame), None)

    if _looks_like_magento_export(frame):
        return _read_magento_export_products(frame)

    rows: list[ProductInputRow] = []
    for index, record in enumerate(frame.to_dict(orient="records"), start=2):
        mapped_values, consumed_keys = _map_record_fields(record)
        supplier_sku = str(mapped_values.get("supplier_sku") or "").strip()
        if not supplier_sku:
            supplier_sku = f"row-{index}"
        extra_fields = {
            k: v
            for k, v in record.items()
            if k not in REQUIRED_LIKE_COLUMNS and k not in consumed_keys
        }
        extra_fields = _augment_extra_fields(extra_fields)
        rows.append(
            ProductInputRow(
                supplier_sku=supplier_sku,
                supplier_name=_clean_string(mapped_values.get("supplier_name")),
                source_url=_clean_string(mapped_values.get("source_url")),
                title_raw=_clean_string(mapped_values.get("title_raw")),
                description_raw=_clean_string(mapped_values.get("description_raw")),
                brand=_clean_string(mapped_values.get("brand")),
                ean=_clean_string(mapped_values.get("ean")),
                row_number=index,
                extra_fields=extra_fields,
            )
        )
    return rows


def list_excel_sheets(input_path: str | Path) -> list[str]:
    path = Path(input_path)
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return []
    workbook = pd.ExcelFile(path)
    return list(workbook.sheet_names)


def list_excel_sheet_items(input_path: str | Path) -> list[dict[str, object]]:
    return [{"index": index, "name": name} for index, name in enumerate(list_excel_sheets(input_path))]


def resolve_excel_sheet_name(input_path: str | Path, requested_sheet_name: str | None) -> str | None:
    cleaned_requested = _clean_string(requested_sheet_name)
    if not cleaned_requested:
        return None
    available_sheets = list_excel_sheets(input_path)
    if cleaned_requested in available_sheets:
        return cleaned_requested

    requested_trimmed = cleaned_requested.strip()
    for sheet_name in available_sheets:
        if sheet_name.strip() == requested_trimmed:
            return sheet_name

    requested_folded = requested_trimmed.casefold()
    for sheet_name in available_sheets:
        if sheet_name.strip().casefold() == requested_folded:
            return sheet_name

    raise ValueError(
        f"Worksheet named '{cleaned_requested}' not found. Available sheets: {', '.join(available_sheets)}"
    )


def resolve_excel_sheet_selector(
    input_path: str | Path,
    requested_sheet_name: str | None = None,
    requested_sheet_index: int | None = None,
) -> int | str:
    available_sheets = list_excel_sheets(input_path)
    if requested_sheet_index is not None:
        if 0 <= requested_sheet_index < len(available_sheets):
            return requested_sheet_index
        raise ValueError(
            f"Worksheet index {requested_sheet_index} is out of range. Available sheet count: {len(available_sheets)}"
        )
    resolved_name = resolve_excel_sheet_name(input_path, requested_sheet_name)
    return resolved_name or 0


def _read_input_frame(path: Path, sheet_name: str | None = None, sheet_index: int | None = None) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        resolved_sheet_selector = resolve_excel_sheet_selector(path, requested_sheet_name=sheet_name, requested_sheet_index=sheet_index)
        return pd.read_excel(path, sheet_name=resolved_sheet_selector)
    raise ValueError(f"Unsupported input format: {path.suffix}")


def _looks_like_magento_export(frame: pd.DataFrame) -> bool:
    raw_columns = {str(column).strip().lower() for column in frame.columns}
    if {"sku", "name", "description", "price", "url_key"}.issubset(raw_columns):
        return True
    if "import_kind" in frame.columns:
        values = {str(value).strip().lower() for value in frame["import_kind"].dropna().tolist() if str(value).strip()}
        if MAGENTO_IMPORT_KIND in values:
            return True
    return False


def _read_magento_export_products(frame: pd.DataFrame) -> list[ProductInputRow]:
    product_map: "OrderedDict[str, dict[str, object]]" = OrderedDict()
    last_sku: str | None = None

    for index, raw_record in enumerate(frame.to_dict(orient="records"), start=2):
        record = {str(key): value for key, value in raw_record.items()}
        sku = _clean_string(record.get("sku"))
        category_value = _clean_string(record.get("_category") or record.get("category"))

        if sku:
            aggregate = product_map.get(sku)
            if aggregate is None:
                aggregate = {
                    "row_number": index,
                    "supplier_sku": sku,
                    "supplier_name": _clean_string(record.get("supplier_name")) or "VOXSTER",
                    "title_raw": _clean_string(record.get("name")),
                    "description_raw": _clean_string(record.get("description")) or _clean_string(record.get("short_description")),
                    "brand": _clean_string(record.get("manufacturer")),
                    "ean": _clean_string(record.get("ean")) or _clean_string(record.get("rw_google_base_12_digit_sku")),
                    "base_website_url": _clean_string(record.get("base_website_url")),
                    "source_url_final": None,
                    "categories": [],
                    "image_urls": [],
                    "pdf_urls": [],
                    "tiers": [],
                    "extra_fields": {},
                }
                product_map[sku] = aggregate
            _merge_magento_record(aggregate, record)
            last_sku = sku
            continue

        if last_sku and last_sku in product_map:
            aggregate = product_map[last_sku]
            _merge_magento_record(aggregate, record)
            if category_value:
                normalized_category = _normalize_magento_category_path(category_value)
                if normalized_category and normalized_category not in aggregate["categories"]:
                    aggregate["categories"].append(normalized_category)

    output_rows: list[ProductInputRow] = []
    for aggregate in product_map.values():
        base_extra = dict(aggregate["extra_fields"])
        base_extra["import_kind"] = "sales_article_list"
        base_extra["source_system"] = MAGENTO_IMPORT_KIND
        base_extra.setdefault("sales_currency", "CHF")
        if aggregate.get("categories"):
            base_extra["category"] = "|".join(str(item) for item in aggregate["categories"] if item)
        if aggregate.get("image_urls"):
            base_extra["direct_image_urls"] = " | ".join(str(item) for item in aggregate["image_urls"] if item)
        if aggregate.get("pdf_urls"):
            base_extra["direct_pdf_urls"] = " | ".join(str(item) for item in aggregate["pdf_urls"] if item)
        if aggregate.get("source_url_final"):
            base_extra["source_url_final"] = aggregate["source_url_final"]

        tiers = aggregate.get("tiers") or []
        base_extra_for_base_row = dict(base_extra)
        if tiers:
            base_extra_for_base_row["is_base_price_row"] = True
        output_rows.append(
            ProductInputRow(
                supplier_sku=str(aggregate["supplier_sku"]),
                supplier_name=_clean_string(aggregate.get("supplier_name")),
                source_url=None,
                title_raw=_clean_string(aggregate.get("title_raw")),
                description_raw=_clean_string(aggregate.get("description_raw")),
                brand=_clean_string(aggregate.get("brand")),
                ean=_clean_string(aggregate.get("ean")),
                row_number=int(aggregate["row_number"]),
                extra_fields=base_extra_for_base_row,
            )
        )
        for tier in tiers:
            tier_extra = dict(base_extra)
            tier_extra["tier_price"] = tier.get("price")
            tier_extra["min_qty"] = tier.get("qty")
            tier_extra["Menge_min"] = tier.get("qty")
            output_rows.append(
                ProductInputRow(
                    supplier_sku=str(aggregate["supplier_sku"]),
                    supplier_name=_clean_string(aggregate.get("supplier_name")),
                    source_url=None,
                    title_raw=_clean_string(aggregate.get("title_raw")),
                    description_raw=_clean_string(aggregate.get("description_raw")),
                    brand=_clean_string(aggregate.get("brand")),
                    ean=_clean_string(aggregate.get("ean")),
                    row_number=int(aggregate["row_number"]),
                    extra_fields=tier_extra,
                )
            )
    return output_rows


def _merge_magento_record(aggregate: dict[str, object], record: dict[str, object]) -> None:
    for target_key, source_key in {
        "supplier_name": "supplier_name",
        "title_raw": "name",
        "description_raw": "description",
        "brand": "manufacturer",
        "ean": "ean",
        "base_website_url": "base_website_url",
    }.items():
        existing = _clean_string(aggregate.get(target_key))
        incoming = _clean_string(record.get(source_key))
        if not existing and incoming:
            aggregate[target_key] = incoming

    if not _clean_string(aggregate.get("description_raw")):
        aggregate["description_raw"] = _clean_string(record.get("short_description"))

    base_url = _clean_string(record.get("base_website_url")) or _clean_string(aggregate.get("base_website_url"))
    source_url_final = _build_magento_product_url(record, base_url)
    if source_url_final and not aggregate.get("source_url_final"):
        aggregate["source_url_final"] = source_url_final

    image_urls = aggregate["image_urls"]
    for image_value in _extract_magento_media_values(record):
        resolved = _resolve_magento_asset_url(image_value, base_url)
        if resolved and resolved not in image_urls:
            image_urls.append(resolved)

    pdf_urls = aggregate["pdf_urls"]
    for pdf_value in _extract_magento_pdf_values(record):
        resolved = _resolve_magento_asset_url(pdf_value, base_url)
        if resolved and resolved not in pdf_urls:
            pdf_urls.append(resolved)

    tier_qty = _clean_string(record.get("_tier_price_qty"))
    tier_price = _clean_string(record.get("_tier_price_price"))
    if tier_qty and tier_price:
        tier_key = (tier_qty, tier_price)
        existing_tiers = {(str(item.get("qty")), str(item.get("price"))) for item in aggregate["tiers"]}
        if tier_key not in existing_tiers:
            aggregate["tiers"].append({"qty": tier_qty, "price": tier_price})

    extra_fields: dict[str, object] = aggregate["extra_fields"]
    direct_mappings = {
        "price": "regular_price",
        "special_price": "special_price",
        "cost": "purchase_price",
        "qty": "stock_qty",
        "status": "magento_status",
        "visibility": "magento_visibility",
        "tax_class_id": "tax_class_id",
        "weight": "weight",
        "color": "color",
        "hydrofix_color_code": "hydrofix_color_code",
        "groesse_und_stueck": "groesse_und_stueck",
        "rollenbreite_model": "rollenbreite_model",
        "room": "room",
        "activation_information": "activation_information",
        "model": "model",
        "shape": "shape",
        "shirt_size": "shirt_size",
        "shoe_size": "shoe_size",
        "shoe_type": "shoe_type",
        "material_und_groesse": "material_und_groesse",
        "processor": "processor",
        "ram_size": "ram_size",
        "response_time": "response_time",
        "screensize": "screensize",
    }
    for source_key, target_key in direct_mappings.items():
        value = _clean_string(record.get(source_key))
        if value is not None and target_key not in extra_fields:
            extra_fields[target_key] = value

    price_value = _clean_string(record.get("special_price")) or _clean_string(record.get("price"))
    if price_value is not None:
        extra_fields["sales_price"] = price_value
    cost_value = _clean_string(record.get("cost"))
    if cost_value is not None:
        extra_fields["purchase_price"] = cost_value


def _extract_magento_media_values(record: dict[str, object]) -> list[str]:
    values: list[str] = []
    for field in ("image", "small_image", "thumbnail", "media_gallery", "_media_image"):
        value = _clean_string(record.get(field))
        if not value or value.lower() == "no_selection":
            continue
        values.extend(_split_magento_multi_value(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _extract_magento_pdf_values(record: dict[str, object]) -> list[str]:
    values: list[str] = []
    for key, raw_value in record.items():
        normalized_key = _normalize_column_name(key)
        if "pdf" not in normalized_key and "datasheet" not in normalized_key and "download" not in normalized_key:
            continue
        value = _clean_string(raw_value)
        if not value:
            continue
        values.extend(_split_magento_multi_value(value))
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _split_magento_multi_value(value: str) -> list[str]:
    parts = re.split(r"\s*[|,;]\s*", value)
    return [part.strip() for part in parts if part.strip()]


def _build_magento_product_url(record: dict[str, object], base_url: str | None) -> str | None:
    direct = _clean_string(record.get("url_path"))
    if direct:
        if direct.startswith(("http://", "https://")):
            return direct
        if base_url:
            return urljoin(base_url.rstrip("/") + "/", direct.lstrip("/"))
        return direct
    key = _clean_string(record.get("url_key"))
    if key and base_url:
        return urljoin(base_url.rstrip("/") + "/", f"{key}.html")
    return None


def _resolve_magento_asset_url(value: str | None, base_url: str | None) -> str | None:
    cleaned = _clean_string(value)
    if not cleaned:
        return None
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    if not base_url:
        return None
    normalized = cleaned if cleaned.startswith("/") else f"/{cleaned}"
    if "/media/" in normalized:
        return urljoin(base_url.rstrip("/") + "/", normalized.lstrip("/"))
    return urljoin(base_url.rstrip("/") + "/", f"media/catalog/product{normalized}")


def _normalize_magento_category_path(value: str) -> str:
    parts = [part.strip() for part in str(value).split("/") if part and part.strip()]
    return " > ".join(parts)


def _map_record_fields(record: dict[object, object]) -> tuple[dict[str, object], set[object]]:
    normalized_keys = {_normalize_column_name(key): key for key in record.keys()}
    mapped: dict[str, object] = {}
    consumed_keys: set[object] = set()
    for target_field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            original_key = normalized_keys.get(_normalize_column_name(alias))
            if original_key is None:
                continue
            mapped[target_field] = record.get(original_key)
            consumed_keys.add(original_key)
            break
    return mapped, consumed_keys


def _normalize_column_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _augment_extra_fields(extra_fields: dict[object, object]) -> dict[str, object]:
    normalized = {str(key): value for key, value in extra_fields.items()}
    import_kind = str(normalized.get("import_kind") or "").strip()
    purchase_currency = _clean_string(normalized.get("purchase_currency"))
    price_value = None
    for key, value in normalized.items():
        if _normalize_column_name(key) in PRICE_ALIASES:
            price_value = value
            break
    if import_kind == "supplier_price_list" and price_value is not None:
        normalized.setdefault("purchase_price", price_value)
        if purchase_currency:
            normalized.setdefault("purchase_currency", purchase_currency)
    elif import_kind == "sales_article_list" and price_value is not None:
        normalized.setdefault("sales_price", price_value)
    return normalized


def _clean_string(value: object) -> str | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    stringified = str(value).strip()
    if not stringified:
        return None
    if stringified.lower() in {"nan", "none", "null", "nat"}:
        return None
    return stringified
