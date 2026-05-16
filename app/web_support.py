from __future__ import annotations

import csv
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd

from app.config import Settings, settings_with_overrides
from app.io.readers import resolve_excel_sheet_selector

DEFAULT_EXPORT_SELECTION = {
    "products_clean_csv",
    "products_clean_xlsx",
    "products_medusa_csv",
    "products_odoo_csv",
    "asset_mapping_csv",
    "errors_csv",
}

IMPORT_KINDS = {"supplier_price_list", "sales_article_list", "magento_1_export"}


def prepare_input_with_website_url(
    input_path: str | Path,
    website_url: str | None,
    source_url_mode: str = "fill_missing",
    force_crawl: bool = False,
    import_kind: str | None = None,
    supplier_name: str | None = None,
    purchase_currency: str | None = None,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
) -> Path:
    path = Path(input_path)
    normalized_url = _coerce_http_url((website_url or "").strip())
    normalized_supplier_name = (supplier_name or "").strip()
    normalized_purchase_currency = (purchase_currency or "").strip().upper()
    if not normalized_url and not force_crawl and not import_kind and not normalized_supplier_name and not normalized_purchase_currency:
        return path

    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
    elif suffix in {".xlsx", ".xls"}:
        resolved_sheet_selector = resolve_excel_sheet_selector(
            path,
            requested_sheet_name=sheet_name,
            requested_sheet_index=sheet_index,
        )
        frame = pd.read_excel(path, sheet_name=resolved_sheet_selector)
    else:
        raise ValueError(f"Unsupported input format: {path.suffix}")

    if normalized_url:
        if import_kind == "magento_1_export":
            frame["base_website_url"] = normalized_url
        else:
            if "source_url" not in frame.columns:
                frame["source_url"] = normalized_url
            else:
                source_values = frame["source_url"].fillna("").astype(str).str.strip()
                if source_url_mode == "overwrite_all":
                    frame["source_url"] = normalized_url
                else:
                    frame["source_url"] = source_values.replace({"": normalized_url, "nan": normalized_url, "None": normalized_url})

    if force_crawl and import_kind != "magento_1_export":
        frame["crawl_site"] = True

    if import_kind in IMPORT_KINDS:
        frame["import_kind"] = import_kind

    if normalized_supplier_name:
        if "supplier_name" not in frame.columns:
            frame["supplier_name"] = normalized_supplier_name
        else:
            supplier_values = frame["supplier_name"].fillna("").astype(str).str.strip()
            frame["supplier_name"] = supplier_values.replace({"": normalized_supplier_name, "nan": normalized_supplier_name, "None": normalized_supplier_name})

    if normalized_purchase_currency:
        frame["purchase_currency"] = normalized_purchase_currency

    temp_file = NamedTemporaryFile(delete=False, suffix=path.suffix)
    temp_path = Path(temp_file.name)
    temp_file.close()

    if suffix == ".csv":
        frame.to_csv(temp_path, index=False)
    else:
        target_sheet_name = sheet_name or "Import"
        frame.to_excel(temp_path, index=False, sheet_name=target_sheet_name)
    return temp_path


def _coerce_http_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://")):
        return cleaned
    return f"https://{cleaned.lstrip('/')}"


def load_table_preview(csv_path: str | Path, limit: int = 200) -> dict[str, Any]:
    path = Path(csv_path)
    if not path.exists():
        return {"columns": [], "rows": [], "total_rows": 0}

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: list[dict[str, str]] = []
        total_rows = 0
        columns = reader.fieldnames or []
        for row in reader:
            total_rows += 1
            if len(rows) < limit:
                rows.append({key: (value or "") for key, value in row.items()})
    return {"columns": columns, "rows": rows, "total_rows": total_rows}


def list_downloadable_outputs(output_dir: str | Path) -> list[dict[str, str]]:
    base = Path(output_dir)
    files = [
        base / "products_clean.csv",
        base / "products_clean.xlsx",
        base / "products_medusa_import.csv",
        base / "products_odoo_templates.csv",
        base / "products_odoo_variants.csv",
        base / "asset_mapping.csv",
        base / "errors.csv",
        base / "logs" / "run.log",
    ]
    return [
        {"name": file_path.name, "relative_path": str(file_path.relative_to(base))}
        for file_path in files
        if file_path.exists()
    ]


def parse_job_options(form: Any, base_settings: Settings) -> dict[str, Any]:
    source_url_mode = (form.get("source_url_mode") or "fill_missing").strip()
    import_kind = (form.get("import_kind") or "").strip()
    export_types = {
        value
        for value in form.getlist("export_types")
        if value in {
            "products_clean_csv",
            "products_clean_xlsx",
            "products_medusa_csv",
            "products_odoo_csv",
            "asset_mapping_csv",
            "errors_csv",
        }
    } or set(DEFAULT_EXPORT_SELECTION)

    overrides = {
        "request_timeout_seconds": _parse_int(form.get("request_timeout_seconds"), minimum=1, fallback=base_settings.request_timeout_seconds),
        "browser_timeout_ms": _parse_int(form.get("browser_timeout_ms"), minimum=1000, fallback=base_settings.browser_timeout_ms),
        "user_agent": (form.get("user_agent") or base_settings.user_agent).strip(),
        "max_images_per_product": _parse_int(form.get("max_images_per_product"), minimum=0, fallback=base_settings.max_images_per_product),
        "max_pdfs_per_product": _parse_int(form.get("max_pdfs_per_product"), minimum=0, fallback=base_settings.max_pdfs_per_product),
        "max_crawl_pages": _parse_int(form.get("max_crawl_pages"), minimum=1, fallback=base_settings.max_crawl_pages),
        "log_level": (form.get("log_level") or base_settings.log_level).strip().upper(),
        "headless": _parse_bool(form.get("headless"), fallback=base_settings.headless),
    }
    settings = settings_with_overrides(base_settings, overrides)
    return {
        "settings": settings,
        "import_kind": import_kind,
        "supplier_name": (form.get("supplier_name") or "").strip() or None,
        "purchase_currency": (form.get("purchase_currency") or "").strip().upper() or None,
        "export_types": export_types,
        "source_url_mode": source_url_mode if source_url_mode in {"fill_missing", "overwrite_all"} else "fill_missing",
        "force_crawl": _parse_bool(form.get("force_crawl"), fallback=False),
        "sheet_name": (form.get("sheet_name") or "").strip() or None,
        "sheet_index": _parse_optional_int(form.get("sheet_index")),
    }


def _parse_int(value: Any, minimum: int, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return max(minimum, parsed)


def _parse_bool(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
