from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable

from app.assets.downloader import AssetDownloader
from app.assets.naming import ensure_supplier_asset_dir
from app.config import Settings, load_settings
from app.io.readers import read_products
from app.io.writers import ensure_output_dirs, write_asset_mapping, write_errors, write_products
from app.models import AssetReference, DownloadedAsset, ProductOutputRow, ProductVariant, ScrapedData
from app.scraping.browser import BrowserClient
from app.suppliers.base import get_supplier_extractor
from app.transform.enrichment import apply_assets_to_product, build_error_record, set_status
from app.transform.normalizer import normalize_product
from app.utils.logging import configure_logging
from app.models import ProductInputRow

LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, object]], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supplier product ETL and scraping pipeline")
    parser.add_argument("--input", required=True, help="Path to products CSV/XLSX")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--config", default=None, help="Optional .env or config.yaml path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings(args.config)
    run_pipeline(args.input, args.output, settings)
    return 0


def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    settings: Settings,
    progress_callback: ProgressCallback | None = None,
    export_types: set[str] | None = None,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
) -> dict[str, object]:
    output_paths = ensure_output_dirs(output_dir)
    log_path = configure_logging(output_paths["logs"], settings.log_level)
    LOGGER.info("Logging to %s", log_path)

    _emit_progress(progress_callback, stage="reading_input", current=0, total=0, message="Lese Eingabedatei")
    rows = read_products(input_path, sheet_name=sheet_name, sheet_index=sheet_index)
    total_rows = len(rows)
    LOGGER.info("Loaded %s product rows", total_rows)
    _emit_progress(progress_callback, stage="processing", current=0, total=total_rows, message="Import gestartet")

    downloader = AssetDownloader(timeout_seconds=settings.request_timeout_seconds)
    cleaned_products: list[ProductOutputRow] = []
    asset_mapping: list[DownloadedAsset] = []
    use_browser = any(row.source_url for row in rows)
    browser_manager = BrowserClient(settings) if use_browser else None

    if browser_manager:
        with browser_manager as browser:
            for index, row in enumerate(rows, start=1):
                cleaned_products.extend(process_row(row, browser, downloader, output_paths["assets"], asset_mapping, settings))
                _emit_progress(
                    progress_callback,
                    stage="processing",
                    current=index,
                    total=total_rows,
                    message=f"Verarbeite Datensatz {index} von {total_rows}",
                    supplier_sku=row.supplier_sku,
                )
    else:
        for index, row in enumerate(rows, start=1):
            cleaned_products.extend(process_row(row, None, downloader, output_paths["assets"], asset_mapping, settings))
            _emit_progress(
                progress_callback,
                stage="processing",
                current=index,
                total=total_rows,
                message=f"Verarbeite Datensatz {index} von {total_rows}",
                supplier_sku=row.supplier_sku,
            )

    error_records = [error for product in cleaned_products if (error := build_error_record(product)) is not None]
    partial_count = sum(1 for product in cleaned_products if product.status == "partial")
    error_count = sum(1 for product in cleaned_products if product.status == "error")

    _emit_progress(progress_callback, stage="writing_output", current=total_rows, total=total_rows, message="Schreibe Exportdateien")
    write_products(output_paths["base"], cleaned_products, export_types=export_types)
    write_asset_mapping(output_paths["base"], asset_mapping, export_types=export_types)
    write_errors(output_paths["base"], error_records, export_types=export_types)
    LOGGER.info(
        "Finished run with %s products, %s partials, and %s errors",
        len(cleaned_products),
        partial_count,
        error_count,
    )
    summary = {
        "products": len(cleaned_products),
        "partials": partial_count,
        "errors": error_count,
        "rows": total_rows,
        "output_dir": str(output_paths["base"]),
        "log_path": str(log_path),
        "sheet_name": sheet_name,
        "sheet_index": sheet_index,
    }
    _emit_progress(progress_callback, stage="completed", current=total_rows, total=total_rows, message="Import abgeschlossen", summary=summary)
    return summary


def process_row(
    row: ProductInputRow,
    browser: BrowserClient | None,
    downloader: AssetDownloader,
    assets_root: Path,
    asset_mapping: list[DownloadedAsset],
    settings: Settings,
) -> list[ProductOutputRow]:
    row_errors: list[str] = []
    scraped_items: list[ScrapedData] = []
    target_urls: list[str] = []
    product_candidate_count = 0

    if row.source_url and browser is not None:
        extractor = get_supplier_extractor(row.supplier_name, row.source_url)
        try:
            target_urls = resolve_target_urls(row, browser, extractor)
            LOGGER.info(
                "Resolved %s target URL(s) for %s (crawl_site=%s, source_url=%s)",
                len(target_urls),
                row.supplier_sku,
                _should_crawl_site(row),
                row.source_url,
            )
            for target_url in target_urls:
                page = None
                try:
                    LOGGER.info("Visiting %s for %s", target_url, row.supplier_sku)
                    page = browser.open_page(target_url)
                    scraped = extractor.extract(page, target_url, row)
                    scraped.is_product_candidate = extractor.classify_product_candidate(page.url, scraped)
                    if scraped.is_product_candidate:
                        product_candidate_count += 1
                        LOGGER.info(
                            "Accepted %s for %s as product candidate (product_name=%s, sku=%s)",
                            page.url,
                            row.supplier_sku,
                            scraped.product_name,
                            scraped.supplier_sku,
                        )
                    else:
                        LOGGER.info(
                            "Rejected %s for %s as non-product page (product_name=%s, sku=%s)",
                            page.url,
                            row.supplier_sku,
                            scraped.product_name,
                            scraped.supplier_sku,
                        )
                    if scraped.is_product_candidate or not _should_crawl_site(row):
                        scraped_items.append(scraped)
                except Exception as exc:
                    LOGGER.exception("Scraping failed for %s at %s", row.supplier_sku, target_url)
                    row_errors.append(f"scraping_failed[{target_url}]: {exc}")
                finally:
                    if page is not None:
                        page.close()
            LOGGER.info(
                "Scrape summary for %s: %s accepted item(s), %s product candidate(s)",
                row.supplier_sku,
                len(scraped_items),
                product_candidate_count,
            )
        except Exception as exc:
            LOGGER.exception("Website crawl failed for %s", row.supplier_sku)
            row_errors.append(f"crawl_failed: {exc}")
    if scraped_items:
        products: list[ProductOutputRow] = []
        for scraped in _dedupe_scraped_items(scraped_items):
            products.extend(build_product_output(row, scraped, downloader, assets_root, asset_mapping, row_errors, settings))
        return products

    preloaded_scraped = build_preloaded_scraped(row)
    if preloaded_scraped is not None:
        return build_product_output(row, preloaded_scraped, downloader, assets_root, asset_mapping, row_errors, settings)

    product = normalize_product(row, ScrapedData())
    if _should_crawl_site(row):
        if not target_urls:
            row_errors.append("crawl_no_target_urls")
        elif product_candidate_count == 0:
            row_errors.append(f"crawl_no_product_candidates[{len(target_urls)}_visited]")
        else:
            row_errors.append("crawl_no_accepted_results")
    set_status(product, row_errors)
    return [product]


def build_product_output(
    row: ProductInputRow,
    scraped: ScrapedData,
    downloader: AssetDownloader,
    assets_root: Path,
    asset_mapping: list[DownloadedAsset],
    inherited_errors: list[str],
    settings: Settings,
) -> list[ProductOutputRow]:
    row_errors = list(inherited_errors)
    variants = scraped.variants or [ProductVariant(supplier_sku=scraped.supplier_sku, barcode=scraped.barcode, title=scraped.product_name)]
    products: list[ProductOutputRow] = []
    shared_pdf_refs = [asset for asset in scraped.asset_references if asset.asset_type == "pdf"]

    for variant in variants:
        product = normalize_product(row, scraped, variant)
        product_assets: list[DownloadedAsset] = []
        asset_dir = ensure_supplier_asset_dir(
            assets_root,
            product.product_name or product.product_title or product.supplier_sku,
        )
        image_refs = _filter_variant_image_refs(scraped, variant)[: settings.max_images_per_product]

        if image_refs:
            try:
                image_assets = downloader.download_images(
                    supplier_sku=product.variant_sku or product.supplier_sku,
                    references=image_refs,
                    destination_dir=asset_dir,
                    product_name=product.product_name,
                    product_title=product.variant_title or product.product_title,
                    description=product.description,
                )
                product_assets.extend(image_assets)
            except Exception as exc:
                LOGGER.exception("Image download failed for %s", product.variant_sku or product.supplier_sku)
                row_errors.append(f"image_download_failed: {exc}")

        if shared_pdf_refs:
            try:
                pdf_assets = downloader.download_pdfs(
                    supplier_sku=product.variant_sku or product.supplier_sku,
                    references=shared_pdf_refs[: settings.max_pdfs_per_product],
                    destination_dir=asset_dir,
                    product_name=product.product_name,
                    product_title=product.variant_title or product.product_title,
                )
                product_assets.extend(pdf_assets)
            except Exception as exc:
                LOGGER.exception("PDF download failed for %s", product.variant_sku or product.supplier_sku)
                row_errors.append(f"pdf_download_failed: {exc}")

        apply_assets_to_product(product, product_assets)
        set_status(product, row_errors)
        asset_mapping.extend(product_assets)
        products.append(product)

    return products


def resolve_target_urls(row: ProductInputRow, browser: BrowserClient, extractor) -> list[str]:
    if not row.source_url:
        return []
    if _should_crawl_site(row):
        return browser.crawl_site_urls(row.source_url, crawl_config=extractor.crawl_config(row.source_url))
    return [row.source_url]


def _should_crawl_site(row: ProductInputRow) -> bool:
    explicit_flag = row.extra_fields.get("crawl_site") or row.extra_fields.get("crawl_full_site")
    if isinstance(explicit_flag, str) and explicit_flag.strip().lower() in {"1", "true", "yes", "y"}:
        return True
    if explicit_flag is True:
        return True
    return False


def _dedupe_scraped_items(items: list[ScrapedData]) -> list[ScrapedData]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    output: list[ScrapedData] = []
    for item in items:
        key = (item.source_url_final, item.supplier_sku, item.product_name)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _filter_variant_image_refs(scraped: ScrapedData, variant: ProductVariant) -> list:
    image_refs = [asset for asset in scraped.asset_references if asset.asset_type == "image"]
    if not variant.packaging:
        return image_refs
    filtered = [
        asset
        for asset in image_refs
        if asset.packaging == variant.packaging
        or variant.packaging.lower().replace(" ", "") in (asset.url or "").lower().replace(" ", "")
        or variant.packaging.lower().replace(" ", "") in (asset.context_text or "").lower().replace(" ", "")
    ]
    return filtered or image_refs


def _emit_progress(progress_callback: ProgressCallback | None, **payload: object) -> None:
    if progress_callback is None:
        return
    progress_callback(payload)


def build_preloaded_scraped(row: ProductInputRow) -> ScrapedData | None:
    extra_fields = row.extra_fields or {}
    source_url_final = _string_or_none(extra_fields.get("source_url_final")) or _string_or_none(extra_fields.get("product_url"))
    image_urls = _split_extra_url_list(extra_fields.get("direct_image_urls"))
    pdf_urls = _split_extra_url_list(extra_fields.get("direct_pdf_urls"))
    if not source_url_final and not image_urls and not pdf_urls:
        return None
    references: list[AssetReference] = []
    references.extend(
        AssetReference(url=url, asset_type="image", page_url=source_url_final, supplier_sku=row.supplier_sku)
        for url in image_urls
    )
    references.extend(
        AssetReference(url=url, asset_type="pdf", role="pdf", page_url=source_url_final, supplier_sku=row.supplier_sku)
        for url in pdf_urls
    )
    return ScrapedData(
        source_url_final=source_url_final,
        supplier_sku=row.supplier_sku,
        product_name=row.title_raw,
        product_title=row.title_raw,
        description=row.description_raw,
        image_urls=image_urls,
        pdf_urls=pdf_urls,
        asset_references=references,
        is_product_candidate=True,
    )


def _split_extra_url_list(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value)
    parts = [part.strip() for part in text.split("|") if part.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in seen:
            seen.add(part)
            deduped.append(part)
    return deduped


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def collect_local_pdf_paths(row: ProductInputRow) -> list[str]:
    candidates: list[str] = []
    for key in ["local_pdf_path", "local_pdf_paths", "pdf_local_path", "pdf_local_paths"]:
        value = row.extra_fields.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            parts = [part.strip() for part in value.split("|") if part.strip()]
            candidates.extend(parts)
        elif isinstance(value, list):
            candidates.extend(str(item).strip() for item in value if str(item).strip())
    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)
    return unique_candidates


if __name__ == "__main__":
    raise SystemExit(main())
