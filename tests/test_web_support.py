from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.config import Settings
from app.web_support import list_downloadable_outputs, load_table_preview, parse_job_options, prepare_input_with_website_url


def test_prepare_input_with_website_url_fills_missing_source_url_for_csv(tmp_path: Path) -> None:
    input_path = tmp_path / "products.csv"
    pd.DataFrame(
        [
            {"supplier_sku": "SKU-1", "supplier_name": "Demo", "source_url": "", "title_raw": "A"},
            {"supplier_sku": "SKU-2", "supplier_name": "Demo", "source_url": "https://existing.example", "title_raw": "B"},
        ]
    ).to_csv(input_path, index=False)

    prepared_path = prepare_input_with_website_url(input_path, "https://fallback.example")
    frame = pd.read_csv(prepared_path)

    assert frame.loc[0, "source_url"] == "https://fallback.example"
    assert frame.loc[1, "source_url"] == "https://existing.example"


def test_prepare_input_with_website_url_adds_missing_column_for_excel(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    pd.DataFrame([{"supplier_sku": "SKU-1", "supplier_name": "Demo"}]).to_excel(input_path, index=False)

    prepared_path = prepare_input_with_website_url(input_path, "https://shop.example")
    frame = pd.read_excel(prepared_path)

    assert "source_url" in frame.columns
    assert frame.loc[0, "source_url"] == "https://shop.example"


def test_prepare_input_with_website_url_can_force_crawl_and_overwrite(tmp_path: Path) -> None:
    input_path = tmp_path / "products.csv"
    pd.DataFrame([{"supplier_sku": "SKU-1", "source_url": "https://old.example"}]).to_csv(input_path, index=False)

    prepared_path = prepare_input_with_website_url(
        input_path,
        "https://new.example",
        source_url_mode="overwrite_all",
        force_crawl=True,
        import_kind="supplier_price_list",
        supplier_name="Tintolav",
        purchase_currency="EUR",
    )
    frame = pd.read_csv(prepared_path)

    assert frame.loc[0, "source_url"] == "https://new.example"
    assert bool(frame.loc[0, "crawl_site"]) is True
    assert frame.loc[0, "import_kind"] == "supplier_price_list"
    assert frame.loc[0, "supplier_name"] == "Tintolav"
    assert frame.loc[0, "purchase_currency"] == "EUR"


def test_prepare_input_with_website_url_preserves_selected_excel_sheet_content(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    with pd.ExcelWriter(input_path) as writer:
        pd.DataFrame([{"Artikel": "A-1", "Preis": 10}]).to_excel(writer, sheet_name="Uebersicht", index=False)
        pd.DataFrame([{"Artikel": "B-2", "Preis": 20}]).to_excel(writer, sheet_name="Preiszeilen", index=False)

    prepared_path = prepare_input_with_website_url(
        input_path,
        "https://example.com",
        import_kind="supplier_price_list",
        supplier_name="Tintolav",
        purchase_currency="EUR",
        sheet_name="Preiszeilen",
        sheet_index=1,
    )
    frame = pd.read_excel(prepared_path, sheet_name="Preiszeilen")

    assert frame.loc[0, "Artikel"] == "B-2"
    assert frame.loc[0, "source_url"] == "https://example.com"
    assert frame.loc[0, "supplier_name"] == "Tintolav"
    assert frame.loc[0, "purchase_currency"] == "EUR"


def test_load_table_preview_and_download_list(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True)
    (output_dir / "errors.csv").write_text(
        "supplier_sku,supplier_name,source_url,reason,status\nSKU-1,Demo,https://example.com,missing_source_url,partial\n",
        encoding="utf-8",
    )
    (output_dir / "products_clean.csv").write_text("id\n1\n", encoding="utf-8")
    (output_dir / "products_clean.xlsx").write_bytes(b"xlsx")
    (output_dir / "products_medusa_import.csv").write_text("id\n1\n", encoding="utf-8")
    (output_dir / "products_odoo_templates.csv").write_text("id\n1\n", encoding="utf-8")
    (output_dir / "products_odoo_variants.csv").write_text("id\n1\n", encoding="utf-8")
    (output_dir / "asset_mapping.csv").write_text("id\n1\n", encoding="utf-8")
    (logs_dir / "run.log").write_text("ok\n", encoding="utf-8")

    preview = load_table_preview(output_dir / "errors.csv")
    downloads = list_downloadable_outputs(output_dir)

    assert preview["total_rows"] == 1
    assert preview["columns"] == ["supplier_sku", "supplier_name", "source_url", "reason", "status"]
    assert preview["rows"][0]["supplier_sku"] == "SKU-1"
    assert {item["relative_path"] for item in downloads} == {
        "products_clean.csv",
        "products_clean.xlsx",
        "products_medusa_import.csv",
        "products_odoo_templates.csv",
        "products_odoo_variants.csv",
        "asset_mapping.csv",
        "errors.csv",
        "logs/run.log",
    }


def test_parse_job_options_uses_form_values() -> None:
    class DummyForm:
        def __init__(self) -> None:
            self.values = {
                "import_kind": "supplier_price_list",
                "supplier_name": "Tintolav",
                "purchase_currency": "EUR",
                "source_url_mode": "overwrite_all",
                "sheet_index": "1",
                "request_timeout_seconds": "45",
                "browser_timeout_ms": "60000",
                "user_agent": "AgentX",
                "max_images_per_product": "3",
                "max_pdfs_per_product": "4",
                "max_crawl_pages": "50",
                "log_level": "debug",
                "headless": "1",
                "force_crawl": "1",
            }
            self.lists = {"export_types": ["products_clean_csv", "products_medusa_csv", "products_odoo_csv"]}

        def get(self, key: str):
            return self.values.get(key)

        def getlist(self, key: str):
            return self.lists.get(key, [])

    options = parse_job_options(DummyForm(), Settings())

    assert options["source_url_mode"] == "overwrite_all"
    assert options["import_kind"] == "supplier_price_list"
    assert options["supplier_name"] == "Tintolav"
    assert options["purchase_currency"] == "EUR"
    assert options["sheet_index"] == 1
    assert options["force_crawl"] is True
    assert options["export_types"] == {"products_clean_csv", "products_medusa_csv", "products_odoo_csv"}
    assert options["settings"].request_timeout_seconds == 45
    assert options["settings"].browser_timeout_ms == 60000
    assert options["settings"].user_agent == "AgentX"
    assert options["settings"].max_images_per_product == 3
    assert options["settings"].max_pdfs_per_product == 4
    assert options["settings"].max_crawl_pages == 50
    assert options["settings"].log_level == "DEBUG"
