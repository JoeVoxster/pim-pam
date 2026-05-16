from __future__ import annotations

import argparse
import logging
import re
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import BrowserContext, sync_playwright

from app.config import Settings
from app.io.writers import ensure_output_dirs, write_errors, write_products
from app.models import ProductInputRow, ProductOutputRow, ProductVariant, ScrapedData
from app.suppliers.base import get_supplier_extractor
from app.transform.enrichment import build_error_record, set_status
from app.transform.normalizer import normalize_product
from app.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Tintolav products to Medusa-compatible CSV")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--start-url", default="https://tintolav.com/en/products.html", help="Tintolav products seed URL")
    parser.add_argument("--max-pages", type=int, default=800, help="Maximum category/listing pages to crawl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings()
    output_paths = ensure_output_dirs(args.output)
    log_path = configure_logging(output_paths["logs"], settings.log_level)
    LOGGER.info("Logging to %s", log_path)

    extractor = get_supplier_extractor("tintolav")
    errors: list[ProductOutputRow] = []
    products: list[ProductOutputRow] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(user_agent=settings.user_agent)
        try:
            product_urls = collect_product_urls(context, args.start_url, max_pages=args.max_pages)
            LOGGER.info("Discovered %s product detail URLs", len(product_urls))
            (Path(args.output) / "tintolav_product_urls.txt").write_text("\n".join(product_urls), encoding="utf-8")
            for index, url in enumerate(product_urls, start=1):
                row = ProductInputRow(
                    supplier_sku=f"tintolav-{index:04d}",
                    supplier_name="Tintolav",
                    source_url=url,
                    brand=_brand_from_url(url),
                )
                page = context.new_page()
                page.set_default_timeout(settings.browser_timeout_ms)
                row_errors: list[str] = []
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    scraped = extractor.extract(page, url, row)
                    if not scraped.is_product_candidate:
                        row_errors.append("page_not_detected_as_product")
                        product = normalize_product(row, scraped)
                        set_status(product, row_errors)
                        errors.append(product)
                        continue
                    products.extend(build_product_output(row, scraped, row_errors))
                except Exception as exc:
                    LOGGER.exception("Scraping failed for %s", url)
                    failed = normalize_product(row, ScrapedData())
                    set_status(failed, [f"scraping_failed: {exc}"])
                    errors.append(failed)
                finally:
                    page.close()
        finally:
            context.close()
            browser.close()

    combined = _dedupe_products(products) + _dedupe_products(errors)
    write_products(output_paths["base"], combined)
    write_errors(output_paths["base"], [error for product in combined if (error := build_error_record(product)) is not None])
    LOGGER.info("Wrote %s product rows and %s error rows", len(products), len(errors))
    return 0


def collect_product_urls(context: BrowserContext, start_url: str, max_pages: int) -> list[str]:
    seed = _normalize_url(start_url)
    parsed_seed = urlparse(seed)
    allowed_host = parsed_seed.netloc.lower()
    queue: deque[str] = deque([seed])
    seen: set[str] = {seed}
    product_urls: set[str] = set()
    visited = 0

    while queue and visited < max_pages:
        current = queue.popleft()
        visited += 1
        if visited % 25 == 0:
            LOGGER.info("Crawled %s listing pages, discovered %s product URLs so far", visited, len(product_urls))
        page = context.new_page()
        page.set_default_timeout(20000)
        try:
            page.goto(current, wait_until="domcontentloaded")
            for absolute in _extract_links_from_page(page, current, allowed_host):
                if "/product/" in urlparse(absolute).path:
                    product_urls.add(absolute)
                elif absolute not in seen:
                    seen.add(absolute)
                    queue.append(absolute)
        except Exception:
            LOGGER.warning("Failed to crawl %s", current, exc_info=True)
        finally:
            page.close()

    return sorted(product_urls)


def _extract_links_from_page(page, base_url: str, allowed_host: str) -> set[str]:
    links: set[str] = set()
    for href in page.eval_on_selector_all("a[href]", "els => els.map(el => el.getAttribute('href') || '')"):
        absolute = _normalize_url(urljoin(base_url, href.replace("&amp;", "&")))
        if not absolute:
            continue
        parsed = urlparse(absolute)
        if parsed.netloc.lower() != allowed_host:
            continue
        if "/en/products/" not in parsed.path:
            continue
        links.add(absolute)
    return links


def build_product_output(
    row: ProductInputRow,
    scraped: ScrapedData,
    inherited_errors: list[str],
) -> list[ProductOutputRow]:
    row_errors = list(inherited_errors)
    variants = scraped.variants or [ProductVariant(supplier_sku=scraped.supplier_sku, barcode=scraped.barcode, title=scraped.product_name)]
    products: list[ProductOutputRow] = []
    for variant in variants:
        product = normalize_product(row, scraped, variant)
        set_status(product, row_errors)
        products.append(product)
    return products


def _dedupe_products(products: list[ProductOutputRow]) -> list[ProductOutputRow]:
    deduped: list[ProductOutputRow] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for product in products:
        key = (
            product.source_url_final or product.source_url,
            product.variant_sku,
            product.product_name,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


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


def _normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return ""
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    cleaned = parsed._replace(path=path, params="", query="", fragment="")
    return urlunparse(cleaned)


if __name__ == "__main__":
    raise SystemExit(main())
