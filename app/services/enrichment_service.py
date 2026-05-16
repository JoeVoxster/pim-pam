from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import time
from urllib.parse import urljoin

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from app.assets.downloader import AssetDownloader
from app.config import load_settings
from app.db.models import Asset, ImportJob, ImportRow, Product, ProductVariant
from app.models import ProductInputRow, ProductVariant as ScrapedVariant, ScrapedData
from app.schemas.pim import EnrichmentJobOptions
from app.scraping.browser import BrowserClient
from app.services.asset_service import create_asset_record
from app.services.supplier_extraction_service import save_scraped_enrichment_candidates
from app.suppliers.base import get_supplier_extractor
from app.utils.pim_config import get_pim_settings


@dataclass(slots=True)
class MatchTarget:
    product: Product
    variant: ProductVariant | None


@dataclass(slots=True)
class ResolverCandidate:
    url: str
    title: str | None
    subtitle: str | None
    sku: str | None


@dataclass(slots=True)
class ApplyStats:
    direct_updates: int = 0
    text_candidates: int = 0
    asset_candidates: int = 0

    @property
    def total(self) -> int:
        return self.direct_updates + self.text_candidates + self.asset_candidates


def run_selected_website_enrichment(
    session: Session,
    options: EnrichmentJobOptions,
    product_ids: list[int] | None = None,
    variant_ids: list[int] | None = None,
) -> dict[str, object]:
    options.seed_url = options.seed_url.strip()
    if options.supplier_name:
        options.supplier_name = options.supplier_name.strip() or None
    if options.resolver_listing_url:
        options.resolver_listing_url = options.resolver_listing_url.strip() or None
    products = _load_products(session)
    selected_targets = _selected_targets(products, product_ids=product_ids, variant_ids=variant_ids)
    if not selected_targets:
        raise ValueError("Keine markierten Produkte oder Varianten gefunden.")

    selected_products = [target.product for target in selected_targets]
    selected_variants = [target.variant for target in selected_targets if target.variant is not None]
    direct_urls = _target_urls(selected_targets)
    if not direct_urls and not options.seed_url.strip():
        raise ValueError("Für die markierten Datensätze gibt es keine Source-URL. Bitte eine Fallback-Start-URL eintragen.")
    source_name = options.seed_url or "selected_records"
    job = ImportJob(
        source_name=source_name,
        job_type="website_enrichment_selection",
        status="running",
        summary_json={
            **options.model_dump(),
            "selected_product_ids": [product.id for product in selected_products],
            "selected_variant_ids": [variant.id for variant in selected_variants],
            "direct_url_count": len(direct_urls),
        },
    )
    _add_job_with_retry(session, job)
    summary = _run_enrichment_job(
        session=session,
        job=job,
        options=options,
        products=selected_products,
        selected_targets=selected_targets,
        direct_urls=direct_urls,
    )
    return summary


def run_website_enrichment(session: Session, options: EnrichmentJobOptions) -> dict[str, object]:
    options.seed_url = options.seed_url.strip()
    if options.supplier_name:
        options.supplier_name = options.supplier_name.strip() or None
    if options.resolver_listing_url:
        options.resolver_listing_url = options.resolver_listing_url.strip() or None
    if not options.seed_url.strip():
        raise ValueError("Start-URL für die Website-Anreicherung fehlt.")
    products = _load_products(session)
    job = ImportJob(
        source_name=options.seed_url,
        job_type="website_enrichment",
        status="running",
        summary_json=options.model_dump(),
    )
    _add_job_with_retry(session, job)
    return _run_enrichment_job(
        session=session,
        job=job,
        options=options,
        products=products,
        selected_targets=None,
        direct_urls=None,
    )


def _run_enrichment_job(
    session: Session,
    job: ImportJob,
    options: EnrichmentJobOptions,
    products: list[Product],
    selected_targets: list[MatchTarget] | None,
    direct_urls: list[str] | None,
) -> dict[str, object]:
    settings = load_settings(None)
    pim_settings = get_pim_settings()
    pim_settings.asset_storage_root.mkdir(parents=True, exist_ok=True)
    downloader = AssetDownloader(timeout_seconds=settings.request_timeout_seconds)
    extractor = get_supplier_extractor(options.supplier_name, options.seed_url)

    discovered = 0
    matched = 0
    updated = 0
    direct_updates = 0
    text_candidates_count = 0
    asset_candidates_count = 0
    errors = 0
    seen_variants: set[int] = set()

    with BrowserClient(settings) as browser:
        preselected_matches = None
        if options.resolver_mode == "tintolav_catalog":
            preselected_matches = _resolve_tintolav_targets(
                browser=browser,
                selected_targets=selected_targets,
                listing_url=options.resolver_listing_url or options.seed_url,
            )
            if selected_targets and not preselected_matches:
                raise ValueError(
                    "Tintolav-Katalogresolver hat keine passende Produktseite fuer die markierte Auswahl gefunden. "
                    "Pruefe Listing-URL, SKU-Mapping oder waehle den generischen Crawl."
                )
            urls = list(preselected_matches.keys())
        else:
            urls = direct_urls or browser.crawl_site_urls(
                options.seed_url,
                max_pages=options.max_pages,
                crawl_config=extractor.crawl_config(options.seed_url),
            )
        for index, url in enumerate(urls, start=1):
            discovered += 1
            row = ImportRow(job_id=job.id, external_id=url, row_index=index, status="pending")
            session.add(row)
            session.flush()
            page = None
            try:
                page = browser.open_page(url)
                scraped = extractor.extract(
                    page,
                    url,
                    ProductInputRow(
                        supplier_sku="crawl",
                        supplier_name=options.supplier_name,
                        source_url=url,
                    ),
                )
                if not extractor.classify_product_candidate(page.url, scraped):
                    row.status = "skipped"
                    row.message = "not_product_candidate"
                    row.raw_payload_json = scraped.model_dump()
                    continue
                matches = _match_scraped_to_products(
                    products,
                    scraped,
                    selected_targets=selected_targets,
                    forced_matches=_forced_matches_for_url(preselected_matches, page.url),
                )
                if not matches:
                    row.status = "unmatched"
                    row.message = scraped.supplier_sku or scraped.product_name or page.url
                    row.raw_payload_json = scraped.model_dump()
                    continue
                matched_targets: list[str] = []
                applied_total = 0
                applied_direct = 0
                applied_text_candidates = 0
                applied_asset_candidates = 0
                for match in matches:
                    if match.variant and match.variant.id in seen_variants:
                        continue
                    applied = _apply_scraped_data_detailed(
                        session=session,
                        target=match,
                        scraped=scraped,
                        options=options,
                        downloader=downloader,
                        storage_root=pim_settings.asset_storage_root,
                    )
                    if match.variant:
                        seen_variants.add(match.variant.id)
                    matched += 1
                    applied_total += applied.total
                    applied_direct += applied.direct_updates
                    applied_text_candidates += applied.text_candidates
                    applied_asset_candidates += applied.asset_candidates
                    direct_updates += applied.direct_updates
                    text_candidates_count += applied.text_candidates
                    asset_candidates_count += applied.asset_candidates
                    matched_targets.append(f"product_id={match.product.id}")
                if not matched_targets:
                    row.status = "duplicate_match"
                    row.message = "all_targets_already_processed"
                    row.raw_payload_json = scraped.model_dump()
                    continue
                updated += applied_total
                row.status = "enriched"
                row.message = (
                    f"{', '.join(matched_targets)} total_changes={applied_total} "
                    f"direct_updates={applied_direct} text_candidates={applied_text_candidates} asset_candidates={applied_asset_candidates}"
                )
                row.raw_payload_json = scraped.model_dump()
            except Exception as exc:
                errors += 1
                row.status = "error"
                row.message = str(exc)
            finally:
                if page is not None:
                    page.close()

    job.status = "completed" if errors == 0 else "completed_with_errors"
    job.summary_json = {
        "seed_url": options.seed_url,
        "supplier_name": options.supplier_name,
        "discovered_urls": discovered,
        "matched_products": matched,
        "updated_fields": updated,
        "direct_updated_fields": direct_updates,
        "candidate_fields": text_candidates_count + asset_candidates_count,
        "text_candidates": text_candidates_count,
        "asset_candidates": asset_candidates_count,
        "errors": errors,
    }
    job.finished_at = datetime.now(timezone.utc)
    job.error_log = f"{errors} page(s) failed" if errors else None
    session.flush()
    return job.summary_json or {}


def _add_job_with_retry(session: Session, job: ImportJob, retries: int = 8, delay_seconds: float = 0.35) -> None:
    for attempt in range(retries):
        try:
            session.add(job)
            session.flush()
            return
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == retries - 1:
                raise
            session.rollback()
            job.id = None
            time.sleep(delay_seconds * (attempt + 1))


def _load_products(session: Session) -> list[Product]:
    stmt = (
        select(Product)
        .options(joinedload(Product.variants), joinedload(Product.assets))
        .order_by(Product.id.asc())
    )
    return list(session.scalars(stmt).unique())


def _selected_targets(
    products: list[Product],
    product_ids: list[int] | None = None,
    variant_ids: list[int] | None = None,
) -> list[MatchTarget]:
    selected: list[MatchTarget] = []
    product_id_set = set(product_ids or [])
    variant_id_set = set(variant_ids or [])
    for product in products:
        if product.id in product_id_set:
            selected.append(MatchTarget(product=product, variant=product.variants[0] if product.variants else None))
        for variant in product.variants:
            if variant.id in variant_id_set:
                selected.append(MatchTarget(product=product, variant=variant))
    return selected


def _target_urls(targets: list[MatchTarget]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for target in targets:
        for candidate in (target.product.source_url_final, target.product.source_url):
            if candidate and candidate not in seen:
                seen.add(candidate)
                urls.append(candidate)
    return urls


def _match_scraped_to_products(
    products: list[Product],
    scraped: ScrapedData,
    selected_targets: list[MatchTarget] | None = None,
    forced_matches: list[MatchTarget] | None = None,
) -> list[MatchTarget]:
    if forced_matches:
        return _unique_targets(forced_matches)
    if selected_targets:
        matched = _match_within_targets(selected_targets, scraped)
        if matched is not None:
            return [matched]

    scraped_sku = (scraped.supplier_sku or "").strip().lower()
    for product in products:
        if product.sku.lower() == scraped_sku:
            return [MatchTarget(product=product, variant=product.variants[0] if product.variants else None)]
        for variant in product.variants:
            if variant.sku.lower() == scraped_sku:
                return [MatchTarget(product=product, variant=variant)]

    scraped_handle = slugify(scraped.product_name or scraped.product_title or "", separator="-")
    if scraped_handle:
        for product in products:
            if product.handle == scraped_handle:
                return [MatchTarget(product=product, variant=product.variants[0] if product.variants else None)]

    return []


def _match_scraped_to_product(
    products: list[Product],
    scraped: ScrapedData,
    selected_targets: list[MatchTarget] | None = None,
    forced_match: MatchTarget | None = None,
) -> MatchTarget | None:
    matches = _match_scraped_to_products(
        products,
        scraped,
        selected_targets=selected_targets,
        forced_matches=[forced_match] if forced_match is not None else None,
    )
    return matches[0] if matches else None


def _match_within_targets(targets: list[MatchTarget], scraped: ScrapedData) -> MatchTarget | None:
    scraped_sku = (scraped.supplier_sku or "").strip().lower()
    if scraped_sku:
        for target in targets:
            if target.variant and target.variant.sku.lower() == scraped_sku:
                return target
            if target.product.sku.lower() == scraped_sku:
                return target

    scraped_handle = slugify(scraped.product_name or scraped.product_title or "", separator="-")
    if scraped_handle:
        for target in targets:
            if target.product.handle == scraped_handle:
                return target
    return None


def _forced_matches_for_url(
    preselected_matches: dict[str, list[MatchTarget]] | None,
    page_url: str,
) -> list[MatchTarget]:
    if not preselected_matches:
        return []
    normalized = page_url.rstrip("/")
    for resolved_url, targets in preselected_matches.items():
        if resolved_url.rstrip("/") == normalized:
            return targets
    return []


def _resolve_tintolav_targets(
    browser: BrowserClient,
    selected_targets: list[MatchTarget] | None,
    listing_url: str | None,
) -> dict[str, list[MatchTarget]]:
    if not selected_targets:
        return {}
    catalog_url = (listing_url or "").strip() or "https://www.tintolav.com/en/products/tintolav/product/listing.html"
    candidates = _fetch_tintolav_catalog_candidates(browser, catalog_url)
    if not candidates:
        return {}
    resolved: dict[str, list[MatchTarget]] = {}
    for target in selected_targets:
        match = _find_tintolav_candidate_for_target(target, candidates)
        if match:
            resolved.setdefault(match.url, []).append(target)
    return resolved


def _unique_targets(targets: list[MatchTarget]) -> list[MatchTarget]:
    unique: list[MatchTarget] = []
    seen: set[tuple[int, int | None]] = set()
    for target in targets:
        key = (target.product.id, target.variant.id if target.variant else None)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique


def _fetch_tintolav_catalog_candidates(browser: BrowserClient, listing_url: str) -> list[ResolverCandidate]:
    seen: set[str] = set()
    candidates: list[ResolverCandidate] = []
    pending_urls = [listing_url]
    visited_urls: set[str] = set()

    while pending_urls:
        current_url = pending_urls.pop(0)
        normalized_current = current_url.rstrip("/")
        if normalized_current in visited_urls:
            continue
        visited_urls.add(normalized_current)

        page = browser.open_page(current_url)
        try:
            payload = page.evaluate(
                """() => {
                    const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
                    const productCards = Array.from(document.querySelectorAll(".dacshop_product, .dacshop_products_listing .dacshop_container"))
                        .map((card) => {
                            const link =
                                card.querySelector(".dacshop_product_name a[href]") ||
                                card.querySelector(".dacshop_product_code_list a[href]") ||
                                card.querySelector(".dacshop_product_image a[href]");
                            const href = link ? (link.href || link.getAttribute("href") || "") : "";
                            const title = normalize(card.querySelector(".dacshop_product_name")?.innerText || "");
                            const subtitle = normalize(card.querySelector(".dacshop_product_subtitle")?.innerText || "");
                            const sku = normalize(card.querySelector(".dacshop_product_code_list")?.innerText || "");
                            if (!href || !title) return null;
                            return {
                                url: href,
                                title,
                                subtitle: subtitle || null,
                                sku: sku || null,
                            };
                        })
                        .filter(Boolean);
                    const nextHref =
                        document.querySelector('link[rel="next"]')?.href ||
                        document.querySelector('a[rel="next"]')?.href ||
                        "";
                    return { productCards, nextHref };
                }"""
            )
        finally:
            page.close()

        entries = payload.get("productCards") or []
        for entry in entries:
            url = str(entry.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                ResolverCandidate(
                    url=url,
                    title=(entry.get("title") or None),
                    subtitle=(entry.get("subtitle") or None),
                    sku=(entry.get("sku") or None),
                )
            )

        next_url = str(payload.get("nextHref") or "").strip()
        if next_url and next_url.rstrip("/") not in visited_urls:
            pending_urls.append(next_url)
    return candidates


def _find_tintolav_candidate_for_target(target: MatchTarget, candidates: list[ResolverCandidate]) -> ResolverCandidate | None:
    target_skus = {
        _tintolav_sku_key(target.product.sku),
    }
    if target.variant is not None:
        target_skus.add(_tintolav_sku_key(target.variant.sku))
    target_skus.discard("")
    for candidate in candidates:
        if candidate.sku and _tintolav_sku_key(candidate.sku) in target_skus:
            return candidate

    product_title = slugify(target.product.title or "", separator="-")
    variant_title = slugify(target.variant.variant_title or "", separator="-") if target.variant else ""
    for candidate in candidates:
        candidate_title = slugify(candidate.title or "", separator="-")
        candidate_subtitle = slugify(candidate.subtitle or "", separator="-")
        candidate_combined = "-".join(part for part in (candidate_title, candidate_subtitle) if part)
        if candidate_title and (candidate_title in product_title or product_title in candidate_title):
            return candidate
        if candidate_combined and (candidate_combined in product_title or product_title in candidate_combined):
            return candidate
        if variant_title and candidate_title and (candidate_title in variant_title or variant_title in candidate_title):
            return candidate
        if variant_title and candidate_combined and (candidate_combined in variant_title or variant_title in candidate_combined):
            return candidate
    return None


def _tintolav_sku_key(value: str | None) -> str:
    if not value:
        return ""
    cleaned = str(value).strip().upper()
    match = re.search(r"[A-Z]{1,3}\d{2}-\d{3}", cleaned)
    return match.group(0) if match else cleaned


def _apply_scraped_data(
    session: Session,
    target: MatchTarget,
    scraped: ScrapedData,
    options: EnrichmentJobOptions,
    downloader: AssetDownloader,
    storage_root: Path,
) -> int:
    return _apply_scraped_data_detailed(
        session=session,
        target=target,
        scraped=scraped,
        options=options,
        downloader=downloader,
        storage_root=storage_root,
    ).total


def _apply_scraped_data_detailed(
    session: Session,
    target: MatchTarget,
    scraped: ScrapedData,
    options: EnrichmentJobOptions,
    downloader: AssetDownloader,
    storage_root: Path,
) -> ApplyStats:
    stats = ApplyStats()
    product = target.product
    variant = target.variant
    is_candidate_supplier = str((scraped.extra_fields or {}).get("supplier_key") or "").strip().lower() in {"tintolav"}

    if is_candidate_supplier:
        text_candidates, asset_candidates = save_scraped_enrichment_candidates(
            session,
            product,
            scraped,
            target_locales=_target_locales_for_product(product),
            translate=_auto_translate_supplier_candidates(),
        )
        stats.text_candidates += len(text_candidates)
        stats.asset_candidates += len(asset_candidates)

    if not is_candidate_supplier and options.update_description and scraped.description and (_empty(product.description) if options.only_empty_fields else True):
        product.description = scraped.description
        stats.direct_updates += 1
    if options.update_source_urls:
        if scraped.source_url_final and (_empty(product.source_url_final) if options.only_empty_fields else True):
            product.source_url_final = scraped.source_url_final
            stats.direct_updates += 1
        if options.seed_url and (_empty(product.source_url) if options.only_empty_fields else True):
            product.source_url = options.seed_url
            stats.direct_updates += 1
    if not is_candidate_supplier and options.update_specifications and scraped.specifications:
        spec_text = " | ".join(scraped.specifications)
        if spec_text and (_empty(product.specifications_text) if options.only_empty_fields else True):
            product.specifications_text = spec_text
            stats.direct_updates += 1
    if not is_candidate_supplier and options.update_technical_features and scraped.technical_features:
        feature_text = " | ".join(scraped.technical_features)
        if feature_text and (_empty(product.technical_features_text) if options.only_empty_fields else True):
            product.technical_features_text = feature_text
            stats.direct_updates += 1
    if not is_candidate_supplier and options.update_packaging and variant is not None:
        packaging = _select_packaging(scraped, variant)
        if packaging and (_empty(variant.packaging) if options.only_empty_fields else True):
            variant.packaging = packaging
            stats.direct_updates += 1

    if not is_candidate_supplier and options.update_assets:
        stats.direct_updates += _import_scraped_assets(
            session=session,
            product=product,
            variant=variant,
            scraped=scraped,
            downloader=downloader,
            storage_root=storage_root,
        )

    session.flush()
    return stats


def _target_locales_for_product(product: Product) -> list[str]:
    preferred = [product.source_language or "de-CH", "de-CH", "fr-CH", "it-CH"]
    result: list[str] = []
    for value in preferred:
        code = str(value or "").strip()
        if code and code not in result:
            result.append(code)
    return result


def _auto_translate_supplier_candidates() -> bool:
    return str(os.getenv("PIM_ENRICHMENT_AUTO_TRANSLATE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _import_scraped_assets(
    session: Session,
    product: Product,
    variant: ProductVariant | None,
    scraped: ScrapedData,
    downloader: AssetDownloader,
    storage_root: Path,
) -> int:
    if not scraped.asset_references:
        return 0
    existing_urls = {
        asset.source_url
        for asset in product.assets
        if asset.source_url
    }
    image_refs = [item for item in scraped.asset_references if item.asset_type == "image" and item.url not in existing_urls]
    pdf_refs = [item for item in scraped.asset_references if item.asset_type == "pdf" and item.url not in existing_urls]
    if not image_refs and not pdf_refs:
        return 0
    target_dir = storage_root / product.handle
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []
    if image_refs:
        downloaded.extend(
            downloader.download_images(
                supplier_sku=variant.sku if variant else product.sku,
                references=image_refs,
                destination_dir=target_dir,
                product_name=product.title,
                product_title=product.title,
                description=product.description,
            )
        )
    if pdf_refs:
        downloaded.extend(
            downloader.download_pdfs(
                supplier_sku=variant.sku if variant else product.sku,
                references=pdf_refs,
                destination_dir=target_dir,
                product_name=product.title,
                product_title=product.title,
            )
        )
    count = 0
    for item in downloaded:
        create_asset_record(
            session=session,
            file_path=item.local_path,
            product_id=product.id,
            variant_id=variant.id if variant else None,
            alt_text=product.title,
            source_url=item.source_url,
        )
        count += 1
    return count


def _select_packaging(scraped: ScrapedData, variant: ProductVariant) -> str | None:
    for item in scraped.variants:
        candidate = _scraped_variant_matches_db_variant(item, variant)
        if candidate and item.packaging:
            return item.packaging
    for item in scraped.variants:
        if item.packaging:
            return item.packaging
    return None


def _scraped_variant_matches_db_variant(scraped_variant: ScrapedVariant, variant: ProductVariant) -> bool:
    if scraped_variant.supplier_sku and scraped_variant.supplier_sku.lower() == variant.sku.lower():
        return True
    if scraped_variant.barcode and variant.barcode and scraped_variant.barcode == variant.barcode:
        return True
    if scraped_variant.title and variant.variant_title and slugify(scraped_variant.title, separator="-") == slugify(variant.variant_title, separator="-"):
        return True
    return False


def _empty(value: str | None) -> bool:
    return value is None or not str(value).strip()
