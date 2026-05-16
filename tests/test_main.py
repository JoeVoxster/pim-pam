from __future__ import annotations

from pathlib import Path

from app.assets.downloader import AssetDownloader
from app.config import Settings
from app.main import process_row
from app.models import ProductInputRow


def test_process_row_without_source_url_is_not_an_error(tmp_path: Path) -> None:
    row = ProductInputRow(
        supplier_sku="SKU-1",
        supplier_name="Demo",
        title_raw="Produkt A",
        description_raw="Beschreibung",
    )

    products = process_row(
        row=row,
        browser=None,
        downloader=AssetDownloader(timeout_seconds=1),
        assets_root=tmp_path,
        asset_mapping=[],
        settings=Settings(),
    )

    assert len(products) == 1
    assert products[0].status == "ok"
    assert products[0].error_reason is None
