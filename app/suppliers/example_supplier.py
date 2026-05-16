from __future__ import annotations

from playwright.sync_api import Page

from app.models import ProductInputRow, ScrapedData
from app.scraping.extractors import extract_generic_product_data
from app.suppliers.base import BaseSupplierExtractor


class SupplierExtractor(BaseSupplierExtractor):
    supplier_key = "example_supplier"

    def extract(self, page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
        data = extract_generic_product_data(page, source_url or row.source_url or page.url, row)
        if not data.product_name:
            headline = page.locator("h1, .product-title").first
            if headline.count():
                data.product_name = headline.inner_text().strip()
        return data
