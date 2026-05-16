from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.io.writers import write_errors, write_products
from app.models import ProductInputRow, ProductOutputRow, ProductVariant, ScrapedData
from app.suppliers.base import get_supplier_extractor
from app.transform.enrichment import build_error_record, set_status
from app.transform.normalizer import normalize_product


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add specific product URLs to an existing export")
    parser.add_argument("--output", required=True, help="Existing export directory")
    parser.add_argument("--url", action="append", required=True, help="Product URL to extract")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output)
    clean_path = output_dir / "products_clean.csv"
    frame = pd.read_csv(clean_path).where(pd.notna, None)
    existing = [ProductOutputRow(**_normalize_existing_row(row)) for row in frame.to_dict(orient="records")]

    extracted: list[ProductOutputRow] = []
    settings = Settings()
    extractor = get_supplier_extractor("Tintolav")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=settings.user_agent)
        try:
            for index, url in enumerate(args.url, start=1):
                row = ProductInputRow(
                    supplier_sku=f"manual-{index:04d}",
                    supplier_name="Tintolav",
                    source_url=url,
                    brand=_brand_from_url(url),
                )
                page = context.new_page()
                page.set_default_timeout(settings.browser_timeout_ms)
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    scraped = extractor.extract(page, url, row)
                    extracted.extend(_build_product_output(row, scraped))
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()

    merged = _dedupe_products(existing + extracted)
    write_products(output_dir, merged)
    write_errors(output_dir, [error for product in merged if (error := build_error_record(product)) is not None])
    return 0


def _build_product_output(row: ProductInputRow, scraped: ScrapedData) -> list[ProductOutputRow]:
    variants = scraped.variants or [ProductVariant(supplier_sku=scraped.supplier_sku, barcode=scraped.barcode, title=scraped.product_name)]
    products: list[ProductOutputRow] = []
    for variant in variants:
        product = normalize_product(row, scraped, variant)
        set_status(product, [])
        products.append(product)
    return products


def _dedupe_products(products: list[ProductOutputRow]) -> list[ProductOutputRow]:
    deduped: list[ProductOutputRow] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for product in products:
        key = (product.source_url_final or product.source_url, product.variant_sku, product.product_name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def _normalize_existing_row(row: dict) -> dict:
    string_fields = {
        "supplier_sku",
        "variant_sku",
        "supplier_name",
        "brand",
        "ean",
        "barcode",
        "variant_title",
        "variant_option_1_name",
        "variant_option_1_value",
        "source_url",
        "source_url_final",
        "title_raw",
        "description_raw",
        "product_name",
        "product_title",
        "description",
        "specifications",
        "technical_features",
        "image_urls",
        "image_paths",
        "pdf_urls",
        "pdf_paths",
        "datasheet_urls",
        "datasheet_paths",
        "sds_urls",
        "sds_paths",
        "pdf_texts",
        "status",
        "error_reason",
    }
    normalized = {}
    for key, value in row.items():
        if value is None:
            normalized[key] = None
            continue
        if isinstance(value, float) and pd.isna(value):
            normalized[key] = None
            continue
        if key == "extra_fields":
            if isinstance(value, dict):
                normalized[key] = value
            else:
                normalized[key] = json.loads(value) if value else {}
            continue
        if key in string_fields:
            if isinstance(value, float) and value.is_integer():
                normalized[key] = str(int(value))
            else:
                normalized[key] = str(value)
            continue
        normalized[key] = value
    return normalized


def _brand_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if "/products/hygienfresh/" in path:
        return "HygienFresh"
    if "/products/hypnosense/" in path:
        return "Hypnosense"
    if "/products/odorblok" in path:
        return "OdorBlok"
    if "/products/bioxelle" in path:
        return "Bioxelle"
    return "Tintolav"


if __name__ == "__main__":
    raise SystemExit(main())
