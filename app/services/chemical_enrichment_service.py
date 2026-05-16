from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from app.assets.downloader import AssetDownloader
from app.config import load_settings
from app.db.models import Asset, Product, ProductChemicalEnrichment, ProductSDB
from app.models import AssetReference, ProductInputRow
from app.pdf.parser import extract_pdf_text
from app.schemas.pim import ProductSDBUpdate
from app.scraping.browser import BrowserClient
from app.scraping.extractors import extract_generic_product_data
from app.services.asset_service import create_asset_record
from app.services.chemical_classification_service import (
    STORAGE_CLASS_LABELS,
    WGK_LABELS,
    extract_wgk_storage_from_sdb,
    normalize_storage_class,
    normalize_wgk,
    storage_class_label,
    wgk_label,
)
from app.services.r2_config_service import build_r2_storage
from app.services.r2_storage_service import object_key_from_storage_path
from app.services.chemical_enrichment_adapters import pick_chemical_adapter
from app.services.pim_service import CHEMICAL_FIELD_NAMES, get_product_sdb, upsert_product_sdb
from app.services.sdb_support import SDB_SECTION_TITLES, merge_sdb_sections
from app.utils.pim_config import get_pim_settings


LOGGER = logging.getLogger(__name__)

def list_product_chemical_enrichment_runs(session: Session, product_id: int) -> list[dict]:
    stmt = (
        select(ProductChemicalEnrichment)
        .where(ProductChemicalEnrichment.product_id == product_id)
        .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
    )
    rows = []
    for enrichment in session.scalars(stmt):
        rows.append(
            {
                "id": enrichment.id,
                "reference_url": enrichment.reference_url,
                "source_kind": enrichment.source_kind,
                "status": enrichment.status,
                "normalized_payload_json": enrichment.normalized_payload_json or {},
                "document_links_json": enrichment.document_links_json or [],
                "warnings_json": enrichment.warnings_json or [],
                "error_log": enrichment.error_log,
                "extracted_at": enrichment.extracted_at.isoformat() if enrichment.extracted_at else None,
                "applied_at": enrichment.applied_at.isoformat() if enrichment.applied_at else None,
            }
        )
    return rows


def _enrichment_has_suggestions(enrichment: ProductChemicalEnrichment) -> bool:
    review = ((enrichment.normalized_payload_json or {}).get("enrichment") or {})
    return bool(review.get("suggestions"))


def _latest_enrichment_with_suggestions(session: Session, product_id: int) -> ProductChemicalEnrichment | None:
    stmt = (
        select(ProductChemicalEnrichment)
        .where(ProductChemicalEnrichment.product_id == product_id)
        .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
        .limit(20)
    )
    for enrichment in session.scalars(stmt):
        if _enrichment_has_suggestions(enrichment):
            return enrichment
    return None


def get_latest_product_chemical_enrichment(session: Session, product_id: int) -> dict | None:
    stmt = (
        select(ProductChemicalEnrichment)
        .where(ProductChemicalEnrichment.product_id == product_id)
        .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
    )
    enrichment = session.scalar(stmt)
    if enrichment is not None and not _enrichment_has_suggestions(enrichment):
        suggested = _latest_enrichment_with_suggestions(session, product_id)
        if suggested is not None:
            enrichment = suggested
    if enrichment is None:
        return None
    return {
        "id": enrichment.id,
        "reference_url": enrichment.reference_url,
        "source_kind": enrichment.source_kind,
        "status": enrichment.status,
        "raw_payload_json": enrichment.raw_payload_json or {},
        "normalized_payload_json": enrichment.normalized_payload_json or {},
        "document_links_json": enrichment.document_links_json or [],
        "warnings_json": enrichment.warnings_json or [],
        "error_log": enrichment.error_log,
        "extracted_at": enrichment.extracted_at.isoformat() if enrichment.extracted_at else None,
        "applied_at": enrichment.applied_at.isoformat() if enrichment.applied_at else None,
    }


def run_product_chemical_enrichment(
    session: Session,
    product_id: int,
    reference_urls: list[str],
) -> dict[str, object]:
    product = session.scalar(
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.assets), joinedload(Product.sdb_record))
        .where(Product.id == product_id)
    )
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")
    urls = _normalize_reference_urls(reference_urls)
    if not urls:
        urls = _normalize_reference_urls(
            [
                product.sds_url,
                product.chemical_reference_url,
                product.source_url_final,
                product.source_url,
            ]
        )
    if not urls:
        raise ValueError("Keine Referenz-URL vorhanden")

    product.chemical_reference_url = "\n".join(urls)
    product.chemical_enrichment_status = "running"
    product.chemical_enrichment_error = None
    session.flush()

    settings = load_settings()
    per_source_results: list[dict[str, object]] = []
    all_documents: list[dict[str, object]] = []
    all_warnings: list[str] = []
    pdf_assets: list[dict[str, object]] = []

    try:
        with BrowserClient(settings) as browser:
            for reference_url in urls:
                if _looks_like_pdf_url(reference_url):
                    document = {"url": reference_url, "role": "sds", "label": "SDB / SDS", "source": "reference_url"}
                    per_source_results.append(
                        {
                            "reference_url": reference_url,
                            "status": "ok",
                            "source_kind": "sds_pdf",
                            "fields": {"sds_url": reference_url, "sds_available": True},
                            "documents": [document],
                            "warnings": [],
                            "raw": {},
                        }
                    )
                    all_documents.append(document)
                    continue
                try:
                    page = browser.open_page(reference_url)
                except Exception as exc:
                    LOGGER.warning("Chemical enrichment failed to open %s: %s", reference_url, exc)
                    per_source_results.append(
                        {
                            "reference_url": reference_url,
                            "status": "error",
                            "error": str(exc),
                            "source_kind": "unreachable",
                        }
                    )
                    all_warnings.append(f"{reference_url}: {exc}")
                    continue
                try:
                    html = page.content()
                    text = page.evaluate("() => (document.body && (document.body.innerText || document.body.textContent)) || ''")
                    links = _page_links(page)
                    generic_data = extract_generic_product_data(
                        page,
                        page.url,
                        ProductInputRow(
                            supplier_sku=product.sku,
                            source_url=reference_url,
                            title_raw=product.title,
                            description_raw=product.description,
                            brand=product.brand.name if product.brand else None,
                        ),
                    )
                    adapter = pick_chemical_adapter(page.url, html, text)
                    extracted = adapter.extract(url=page.url, html=html, text=text, links=links, generic_data=generic_data)
                    per_source_results.append(
                        {
                            "reference_url": page.url,
                            "status": "ok",
                            "source_kind": extracted.source_kind,
                            "fields": extracted.fields,
                            "documents": extracted.documents,
                            "warnings": extracted.warnings,
                            "raw": extracted.raw,
                        }
                    )
                    all_documents.extend(extracted.documents)
                    all_warnings.extend(extracted.warnings)
                finally:
                    page.close()

        aggregated = _aggregate_source_results(per_source_results)
        enrichment = ProductChemicalEnrichment(
            product_id=product.id,
            reference_url=urls[0],
            source_kind=aggregated.get("source_kind"),
            status="completed" if aggregated.get("fields") else "partial",
            raw_payload_json={"sources": per_source_results},
            normalized_payload_json=aggregated,
            document_links_json=_unique_documents(all_documents),
            warnings_json=_unique_strings(all_warnings),
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(enrichment)
        session.flush()

        existing_sdb = get_product_sdb(session, product.id) or {}
        downloaded = _download_document_assets(session, product, _unique_documents(all_documents), settings.request_timeout_seconds)
        pdf_assets = downloaded["assets"]
        parsed_has_content = bool(str(downloaded.get("sdb_text") or "").strip()) or any(
            str((section or {}).get("content") or "").strip()
            for section in (downloaded.get("sdb_sections") or {}).values()
        )
        existing_has_content = bool(str(existing_sdb.get("raw_text") or "").strip()) or any(
            str((section or {}).get("content") or "").strip()
            for section in (existing_sdb.get("sections_json") or {}).values()
        )
        if parsed_has_content or ((downloaded["sdb_url"] or downloaded["sdb_asset_id"]) and not existing_has_content):
            sdb_payload = ProductSDBUpdate(
                source_url=urls[0],
                pdf_url=downloaded["sdb_url"],
                source_asset_id=downloaded["sdb_asset_id"],
                parser_status="parsed" if downloaded["sdb_sections"] else "raw",
                raw_text=downloaded["sdb_text"],
                sections_json=downloaded["sdb_sections"],
            )
            upsert_product_sdb(session, product.id, sdb_payload)
            if not product.sds_url:
                product.sds_url = downloaded["sdb_url"]
            if not product.sds_asset_id:
                product.sds_asset_id = downloaded["sdb_asset_id"]
            if downloaded["sdb_url"] or downloaded["sdb_asset_id"]:
                product.sds_available = True

        sdb_data = get_product_sdb(session, product.id) or {}
        review_payload = _build_chemical_enrichment_review(
            product=product,
            aggregated=aggregated,
            documents=_unique_documents(all_documents),
            warnings=_unique_strings(all_warnings),
            sdb_data=sdb_data,
        )
        aggregated = {**aggregated, "enrichment": review_payload}
        if review_payload["suggestions"]:
            enrichment.status = "needs_review"
        elif not review_payload["sources"]:
            enrichment.status = "no_source_found"
        enrichment.normalized_payload_json = aggregated
        flag_modified(enrichment, "normalized_payload_json")

        product.chemical_last_enriched_at = datetime.now(timezone.utc)
        product.chemical_enrichment_status = enrichment.status
        product.chemical_enrichment_error = None
        session.flush()

        return {
            "status": enrichment.status,
            "enrichment_id": enrichment.id,
            "reference_url": urls[0],
            "source_kind": enrichment.source_kind,
            "warnings": _unique_strings(all_warnings),
            "documents": _unique_documents(all_documents),
            "pdf_assets": pdf_assets,
            "normalized": aggregated,
        }
    except Exception as exc:
        LOGGER.exception("Chemical enrichment failed for product %s", product_id)
        product.chemical_last_enriched_at = datetime.now(timezone.utc)
        product.chemical_enrichment_status = "failed"
        product.chemical_enrichment_error = str(exc)
        session.flush()
        failed = ProductChemicalEnrichment(
            product_id=product.id,
            reference_url=urls[0] if urls else None,
            source_kind="error",
            status="failed",
            raw_payload_json={"sources": per_source_results},
            normalized_payload_json={},
            document_links_json=_unique_documents(all_documents),
            warnings_json=_unique_strings(all_warnings),
            error_log=str(exc),
            extracted_at=datetime.now(timezone.utc),
        )
        session.add(failed)
        session.flush()
        raise


def apply_product_chemical_enrichment(
    session: Session,
    product_id: int,
    enrichment_id: int | None = None,
    overwrite_existing: bool = False,
) -> dict[str, object]:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")
    if enrichment_id is None:
        stmt = (
            select(ProductChemicalEnrichment)
            .where(ProductChemicalEnrichment.product_id == product_id)
            .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
        )
    else:
        stmt = select(ProductChemicalEnrichment).where(
            ProductChemicalEnrichment.product_id == product_id,
            ProductChemicalEnrichment.id == enrichment_id,
        )
    enrichment = session.scalar(stmt)
    if enrichment is not None and enrichment_id is None and not _enrichment_has_suggestions(enrichment):
        suggested = _latest_enrichment_with_suggestions(session, product_id)
        if suggested is not None:
            enrichment = suggested
    if enrichment is None:
        raise ValueError("Keine Anreicherung vorhanden")
    payload = enrichment.normalized_payload_json or {}
    field_entries = payload.get("fields") or {}
    applied_fields: list[str] = []
    for field_name in CHEMICAL_FIELD_NAMES:
        entry = field_entries.get(field_name)
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not _should_apply_value(getattr(product, field_name), value, overwrite_existing):
            continue
        if field_name == "ghs_pictograms" and isinstance(value, list):
            value = "|".join(value)
        setattr(product, field_name, value)
        applied_fields.append(field_name)
    product.chemical_reference_url = product.chemical_reference_url or enrichment.reference_url
    product.chemical_last_enriched_at = enrichment.extracted_at or datetime.now(timezone.utc)
    product.chemical_enrichment_status = "applied"
    product.chemical_enrichment_error = None
    enrichment.applied_at = datetime.now(timezone.utc)
    session.flush()
    return {"status": "applied", "enrichment_id": enrichment.id, "applied_fields": applied_fields}


def apply_product_chemical_enrichment_suggestions(
    session: Session,
    product_id: int,
    enrichment_id: int | None = None,
    selected_fields: list[str] | None = None,
    *,
    overwrite_existing: bool = True,
) -> dict[str, object]:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")
    if enrichment_id is None:
        stmt = (
            select(ProductChemicalEnrichment)
            .where(ProductChemicalEnrichment.product_id == product_id)
            .order_by(ProductChemicalEnrichment.extracted_at.desc(), ProductChemicalEnrichment.id.desc())
        )
    else:
        stmt = select(ProductChemicalEnrichment).where(
            ProductChemicalEnrichment.product_id == product_id,
            ProductChemicalEnrichment.id == enrichment_id,
        )
    enrichment = session.scalar(stmt)
    if enrichment is not None and enrichment_id is None and not _enrichment_has_suggestions(enrichment):
        suggested = _latest_enrichment_with_suggestions(session, product_id)
        if suggested is not None:
            enrichment = suggested
    if enrichment is None:
        raise ValueError("Keine Anreicherung vorhanden")

    review = ((enrichment.normalized_payload_json or {}).get("enrichment") or {})
    suggestions = review.get("suggestions") or []
    if not isinstance(suggestions, list) or not suggestions:
        raise ValueError("Keine Vorschläge zur Übernahme vorhanden")

    selected = set(selected_fields or [])
    if not selected:
        selected = {str(item.get("field") or "") for item in suggestions if item.get("status") == "suggested"}

    chem_safety = dict(product.chemical_safety_json or {})
    applied_fields: list[str] = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        field = str(suggestion.get("field") or "")
        if field not in selected or suggestion.get("status") not in {"suggested", "needs_review"}:
            continue
        value = suggestion.get("suggested_value")
        if _is_blank_value(value):
            continue
        current_value = _current_product_value(product, field)
        bool_fill = isinstance(value, bool) and value is True and current_value in {None, False, ""}
        additive_list = isinstance(value, list) and set(_value_as_codes(current_value)).issubset(set(_value_as_codes(value)))
        if not overwrite_existing and not bool_fill and not additive_list and not _is_blank_value(current_value) and _json_stable(current_value) != _json_stable(value):
            continue
        _apply_enrichment_suggestion(product, chem_safety, field, value)
        applied_fields.append(field)

    if applied_fields:
        product.chemical_safety_json = chem_safety
        product.chemical_reference_url = product.chemical_reference_url or enrichment.reference_url
        product.chemical_last_enriched_at = enrichment.extracted_at or datetime.now(timezone.utc)
        product.chemical_enrichment_status = "applied"
        product.chemical_enrichment_error = None
        enrichment.applied_at = datetime.now(timezone.utc)
    session.flush()
    return {"status": "applied", "enrichment_id": enrichment.id, "applied_fields": applied_fields}


def parse_sdb_sections(text: str | None) -> dict[str, dict[str, str]]:
    normalized = (text or "").replace("\r", "\n")
    result = {f"section_{number}": {"title": title, "content": ""} for number, title in SDB_SECTION_TITLES.items()}
    if not normalized.strip():
        return result

    explicit_heading_pattern = re.compile(
        r"(?im)^\s*(?:ABSCHNITT|SECTION)\s*(1[0-6]|[1-9])\s*[:.)-]?\s*(.+?)\s*$"
    )
    plain_heading_pattern = re.compile(
        r"(?im)^\s*(1[0-6]|[1-9])(?:\s+|[:)-]|\.(?!\d))\s*(.+?)\s*$"
    )

    matches = list(explicit_heading_pattern.finditer(normalized))
    if not matches:
        matches = [
            match
            for match in plain_heading_pattern.finditer(normalized)
            if _looks_like_sdb_section_heading(int(match.group(1)), match.group(2))
        ]
    if not matches:
        result["section_1"]["content"] = normalized.strip()
        return result

    for index, match in enumerate(matches):
        section_number = int(match.group(1))
        if section_number not in SDB_SECTION_TITLES:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        title = re.sub(r"\s+", " ", match.group(2)).strip(" :-") or SDB_SECTION_TITLES[section_number]
        content = normalized[start:end].strip()
        result[f"section_{section_number}"] = {"title": title, "content": content}
    return result


def _looks_like_sdb_section_heading(section_number: int, title: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(title or "").strip()).casefold()
    if not normalized:
        return False
    known = SDB_SECTION_TITLES.get(section_number, "").casefold()
    if known and _heading_overlap(normalized, known):
        return True
    english_markers = {
        1: ("identification",),
        2: ("hazards identification",),
        3: ("composition", "ingredients"),
        4: ("first aid",),
        5: ("firefighting", "fire-fighting"),
        6: ("accidental release",),
        7: ("handling", "storage"),
        8: ("exposure controls", "personal protection"),
        9: ("physical", "chemical properties"),
        10: ("stability", "reactivity"),
        11: ("toxicological",),
        12: ("ecological",),
        13: ("disposal",),
        14: ("transport",),
        15: ("regulatory",),
        16: ("other information",),
    }
    return any(marker in normalized for marker in english_markers.get(section_number, ()))


def _heading_overlap(candidate: str, known: str) -> bool:
    candidate_words = {word for word in re.findall(r"[a-zäöüß]{4,}", candidate) if word not in {"oder", "und", "sowie"}}
    known_words = {word for word in re.findall(r"[a-zäöüß]{4,}", known) if word not in {"oder", "und", "sowie"}}
    if not candidate_words or not known_words:
        return False
    return len(candidate_words & known_words) >= min(2, len(known_words))


def ingest_product_sdb_pdf(session: Session, product_id: int, pdf_url: str, *, force_download: bool = False) -> dict[str, object]:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")
    normalized_url = (pdf_url or "").strip()
    if not normalized_url:
        raise ValueError("PDF-/SDB-URL fehlt")

    existing_asset = session.scalar(
        select(Asset).where(
            Asset.product_id == product_id,
            Asset.source_url == normalized_url,
        ).order_by(Asset.id.desc())
    )
    target_path: Path
    asset_id: int | None = None
    if not force_download and existing_asset is not None and Path(existing_asset.storage_path).exists():
        target_path = Path(existing_asset.storage_path)
        asset_id = existing_asset.id
    else:
        settings = load_settings()
        destination_dir = _resolve_sdb_download_dir(product_id)
        filename = Path(urlparse(normalized_url).path).name or f"{product.sku.lower()}-sdb.pdf"
        target_path = _unique_destination_path(destination_dir / filename)
        _download_pdf_to_path(
            normalized_url,
            target_path,
            timeout_seconds=settings.request_timeout_seconds,
            user_agent=settings.user_agent,
        )
        asset = create_asset_record(
            session,
            target_path,
            product_id=product_id,
            source_url=normalized_url,
        )
        asset_id = asset.id

    raw_text = extract_pdf_text(target_path)
    sections = parse_sdb_sections(raw_text)
    return {
        "pdf_url": normalized_url,
        "source_asset_id": asset_id,
        "raw_text": raw_text,
        "sections_json": sections,
        "parser_status": "parsed" if any(section.get("content") for section in sections.values()) else "raw",
    }


def ingest_product_sdb_asset(session: Session, product_id: int, asset_id: int) -> dict[str, object]:
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")
    asset = session.get(Asset, int(asset_id))
    if asset is None or asset.product_id != int(product_id):
        raise ValueError("SDB-Asset nicht gefunden oder nicht mit dem Produkt verknüpft.")
    if "pdf" not in str(asset.mime_type or "").lower() and not str(asset.filename or "").lower().endswith(".pdf"):
        raise ValueError("Das gewählte Asset ist kein PDF.")

    temp_path: Path | None = None
    local_path = Path(asset.storage_path)
    target_path = local_path if local_path.exists() else None
    if target_path is None:
        fd, temp_name = tempfile.mkstemp(prefix=f"sdb-asset-{asset.id}-", suffix=".pdf")
        os.close(fd)
        temp_path = Path(temp_name)
        _download_asset_pdf_to_path(session, asset, temp_path)
        target_path = temp_path

    try:
        raw_text = extract_pdf_text(target_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    sections = parse_sdb_sections(raw_text)
    return {
        "pdf_url": f"/asset-file/{asset.id}",
        "source_asset_id": asset.id,
        "raw_text": raw_text,
        "sections_json": sections,
        "parser_status": "parsed" if any(section.get("content") for section in sections.values()) else "raw",
    }


def _download_asset_pdf_to_path(session: Session, asset: Asset, destination: Path) -> None:
    object_key = asset.object_key or object_key_from_storage_path(asset.storage_path)
    url: str | None = None
    if asset.storage_provider in {"cloudflare_r2", "bunny_storage"} and object_key:
        url = build_r2_storage(session).generate_presigned_download_url(object_key)
    else:
        url = asset.public_url or asset.source_url
    if not url:
        raise ValueError("Für das Asset ist keine lesbare Datei oder Download-URL vorhanden.")
    response = requests.get(url, timeout=60, headers={"Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"})
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        content_type = response.headers.get("Content-Type", "")
        raise ValueError(f"Asset-Download ist kein PDF. Content-Type: {content_type or '-'}")
    destination.write_bytes(response.content)


def _resolve_sdb_download_dir(product_id: int) -> Path:
    asset_root = get_pim_settings().asset_storage_root
    preferred = asset_root / f"product-{product_id}"
    preferred.mkdir(parents=True, exist_ok=True)
    if os.access(preferred, os.W_OK | os.X_OK):
        return preferred

    fallback = asset_root / "_imports" / f"product-{product_id}"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _download_pdf_to_path(url: str, destination: Path, *, timeout_seconds: int, user_agent: str) -> None:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    }
    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers=headers,
        )
        response.raise_for_status()
        destination.write_bytes(response.content)
        return
    except Exception as exc:
        LOGGER.warning("requests download failed for %s, falling back to curl: %s", url, exc)

    command = [
        "curl",
        "-fsSL",
        "--retry",
        "2",
        "--connect-timeout",
        str(timeout_seconds),
        "--max-time",
        str(max(timeout_seconds * 2, 60)),
        "-A",
        user_agent,
        "-H",
        "Accept: application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "-H",
        "Accept-Language: de-CH,de;q=0.9,en;q=0.8",
        "-o",
        str(destination),
        url,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"PDF-Download fehlgeschlagen: {stderr or exc}") from exc


def _page_links(page) -> list[dict[str, str]]:
    raw_links: list[dict[str, str]] = page.evaluate(
        """() => Array.from(document.querySelectorAll("a[href]")).map((node) => ({
            href: node.getAttribute("href") || "",
            text: (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim()
        }))"""
    )
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_links:
        href = (item.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(page.url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        output.append({"url": absolute, "label": (item.get("text") or "").strip()})
    return output


def _aggregate_source_results(results: list[dict[str, object]]) -> dict[str, object]:
    field_values: dict[str, dict[str, object]] = {}
    source_kind = None
    for result in results:
        if result.get("status") != "ok":
            continue
        if source_kind is None:
            source_kind = result.get("source_kind")
        reference_url = result.get("reference_url")
        fields = result.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for field_name, value in fields.items():
            if _is_blank_value(value):
                continue
            entry = field_values.setdefault(field_name, {"value": None, "sources": [], "conflicts": []})
            source_entry = {"url": reference_url, "source_kind": result.get("source_kind"), "value": value}
            current_value = entry.get("value")
            entry["sources"].append(source_entry)
            if _is_blank_value(current_value):
                entry["value"] = value
            elif _json_stable(current_value) != _json_stable(value):
                entry["conflicts"].append(source_entry)
    return {"source_kind": source_kind or "generic", "fields": field_values}


def _download_document_assets(
    session: Session,
    product: Product,
    documents: list[dict[str, object]],
    timeout_seconds: int,
) -> dict[str, object]:
    pdf_documents = [item for item in documents if str(item.get("role") or "").lower() in {"sds", "datasheet", "pdf"}]
    if not pdf_documents:
        return {"assets": [], "sdb_url": None, "sdb_asset_id": None, "sdb_text": None, "sdb_sections": {}}
    destination_dir = get_pim_settings().asset_storage_root / f"product-{product.id}"
    destination_dir.mkdir(parents=True, exist_ok=True)
    references = [
        AssetReference(
            url=str(item.get("url")),
            asset_type="pdf",
            role=str(item.get("role") or "pdf"),
            label=str(item.get("label") or item.get("role") or "PDF"),
            context_text=str(item.get("label") or item.get("role") or "PDF"),
            page_url=product.chemical_reference_url or product.source_url_final or product.source_url,
        )
        for item in pdf_documents[:5]
        if item.get("url")
    ]
    downloader = AssetDownloader(timeout_seconds)
    downloaded_assets = []
    for reference in references:
        try:
            downloaded_assets.extend(
                downloader.download_pdfs(
                    product.sku,
                    [reference],
                    destination_dir,
                    product.title,
                    product.title,
                )
            )
        except Exception as exc:
            LOGGER.warning("Skipping chemical document download %s for product %s: %s", reference.url, product.id, exc)

    created_assets: list[dict[str, object]] = []
    sdb_url = None
    sdb_asset_id = None
    sdb_text = None
    sdb_sections: dict[str, dict[str, str]] = {}

    sds_reference = next((item for item in pdf_documents if str(item.get("role") or "").lower() == "sds"), None)
    if sds_reference:
        sdb_url = str(sds_reference.get("url") or "") or None

    for downloaded in downloaded_assets:
        existing = session.scalar(
            select(Asset).where(
                Asset.product_id == product.id,
                Asset.source_url == downloaded.source_url,
            )
        )
        asset = existing
        if asset is None:
            asset = create_asset_record(
                session,
                downloaded.local_path,
                product_id=product.id,
                source_url=downloaded.source_url,
            )
        created_assets.append(
            {
                "id": asset.id,
                "filename": asset.filename,
                "source_url": asset.source_url,
                "mime_type": asset.mime_type,
            }
        )
        if downloaded.role == "sds" and sdb_asset_id is None:
            sdb_asset_id = asset.id
            sdb_url = downloaded.source_url
            sdb_text = downloaded.extracted_text or extract_pdf_text(downloaded.local_path)
            sdb_sections = parse_sdb_sections(sdb_text)
    if sdb_asset_id is None and downloaded_assets:
        first_pdf = downloaded_assets[0]
        asset = session.scalar(
            select(Asset).where(
                Asset.product_id == product.id,
                Asset.source_url == first_pdf.source_url,
            )
        )
        if asset is not None:
            sdb_asset_id = asset.id
            sdb_url = first_pdf.source_url
            sdb_text = first_pdf.extracted_text or extract_pdf_text(first_pdf.local_path)
            sdb_sections = parse_sdb_sections(sdb_text)

    return {
        "assets": created_assets,
        "sdb_url": sdb_url,
        "sdb_asset_id": sdb_asset_id,
        "sdb_text": sdb_text,
        "sdb_sections": sdb_sections,
    }


def _build_chemical_enrichment_review(
    *,
    product: Product,
    aggregated: dict[str, object],
    documents: list[dict[str, object]],
    warnings: list[str],
    sdb_data: dict | None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    sources = _enrichment_sources(documents, sdb_data)
    log: list[dict[str, str]] = [
        {"level": "info", "message": f"Produktdaten geladen: {product.id} · {product.title or product.sku or '-'}"},
        {"level": "info", "message": f"Quellen geprüft: {len(sources)}"},
    ]
    for warning in warnings:
        log.append({"level": "warning", "message": str(warning)})

    sdb_sections = merge_sdb_sections((sdb_data or {}).get("sections_json"))
    section_2 = _section_content(sdb_sections, 2)
    section_7 = _section_content(sdb_sections, 7)
    section_12 = _section_content(sdb_sections, 12)
    section_14 = _section_content(sdb_sections, 14)
    section_15 = _section_content(sdb_sections, 15)
    section_16 = _section_content(sdb_sections, 16)
    raw_text = str((sdb_data or {}).get("raw_text") or "")
    if section_14:
        log.append({"level": "info", "message": "SDB Abschnitt 14 gefunden und ausgewertet."})
    else:
        log.append({"level": "warning", "message": "SDB Abschnitt 14 nicht gefunden."})

    suggestions: list[dict[str, object]] = []
    found_values: dict[str, object] = {}
    not_found: list[str] = []
    fields = aggregated.get("fields") or {}
    field_candidates = _general_field_candidates(fields, documents, raw_text, sdb_sections)
    for field_name, candidate in field_candidates.items():
        value = candidate.get("value")
        if _is_blank_value(value):
            continue
        suggestions.append(
            _suggestion(
                product,
                field_name,
                value,
                str(candidate.get("confidence") or "medium"),
                str(candidate.get("source_section") or ""),
                str(candidate.get("evidence") or ""),
                force_review=bool(candidate.get("force_review")),
            )
        )

    ghs_codes = _unique_codes(
        _value_as_codes(_field_value(fields, "ghs_pictograms"))
        + _extract_codes(f"{section_2}\n{raw_text}", r"\bGHS0[1-9]\b")
    )
    if ghs_codes:
        found_values["chem_safety.ghs_pictograms"] = ghs_codes
        suggestions.append(_suggestion(product, "chem_safety.ghs_pictograms", ghs_codes, "high", "2", "GHS-Piktogramme aus Abschnitt 2/Quelle erkannt."))
    else:
        not_found.append("ghs_pictograms")

    signal_word = _normalize_signal_word(_field_value(fields, "signal_word") or _find_signal_word(section_2))
    if signal_word:
        found_values["chem_safety.signal_word"] = signal_word
        suggestions.append(_suggestion(product, "chem_safety.signal_word", signal_word, "medium", "2", f"Signalwort erkannt: {signal_word}"))
    else:
        not_found.append("signal_word")

    h_statements = _unique_codes(_value_as_codes(_field_value(fields, "hazard_statements")) + _extract_codes(f"{section_2}\n{section_16}", r"\bH\d{3}[A-Z]?\b"))
    if h_statements:
        suggestions.append(_suggestion(product, "chem_safety.hazard_statements", h_statements, "medium", "2/16", ", ".join(h_statements[:12])))
    else:
        not_found.append("hazard_statements")

    p_statements = _unique_codes(_value_as_codes(_field_value(fields, "precautionary_statements")) + _extract_codes(section_2, r"\bP\d{3}(?:\+P\d{3})*[A-Z]?\b"))
    if p_statements:
        suggestions.append(_suggestion(product, "chem_safety.precautionary_statements", p_statements, "medium", "2", ", ".join(p_statements[:12])))
    else:
        not_found.append("precautionary_statements")

    euh_statements = _unique_codes(_extract_codes(f"{section_2}\n{section_16}", r"\bEUH\d{3}[A-Z]?\b"))
    if euh_statements:
        suggestions.append(_suggestion(product, "chem_safety.euh_statements", euh_statements, "medium", "2/16", ", ".join(euh_statements[:8])))

    adr_class = _normalize_adr_class(_field_value(fields, "hazard_class") or _extract_adr_class(section_14))
    if adr_class:
        suggestions.append(_suggestion(product, "chem_safety.adr_class", adr_class, "high" if section_14 else "medium", "14", f"Transportklasse erkannt: {adr_class}"))
        if adr_class == "8":
            suggestions.append(_suggestion(product, "chem_safety.adr_pictograms", _merge_list_value(_current_product_value(product, "chem_safety.adr_pictograms"), ["ADR_8"]), "high", "14", "ADR Klasse 8 erkannt."))
    else:
        not_found.append("adr_class")

    un_number = _normalize_un(_field_value(fields, "un_number") or _extract_un_number_from_section_14(section_14))
    if un_number:
        suggestions.append(_suggestion(product, "un_number", un_number, "high" if section_14 else "medium", "14", f"UN-Nummer erkannt: {un_number}"))
    else:
        not_found.append("un_number")

    packing_group = _normalize_packing_group(_field_value(fields, "packing_group") or _find_regex(section_14, r"(?:Verpackungsgruppe|Packing group)\s*:?\s*(I{1,3}|II|III)\b"))
    if packing_group:
        suggestions.append(_suggestion(product, "packing_group", packing_group, "high" if section_14 else "medium", "14", f"Verpackungsgruppe erkannt: {packing_group}"))
    else:
        not_found.append("packing_group")

    lq = _field_value(fields, "limited_quantity") or _find_regex(section_14, r"(?:Begrenzte Menge|Begrenzte Mengen|Limited Quantity|LQ)\s*:?\s*([^\n;]+)")
    if lq:
        suggestions.append(_suggestion(product, "limited_quantity", str(lq).strip(), "medium", "14", f"LQ erkannt: {str(lq).strip()}"))

    environmental = _extract_environmentally_hazardous(section_14)
    if environmental:
        suggestions.append(_suggestion(product, "chem_safety.environmentally_hazardous", True, "high", "14", environmental))
        suggestions.append(_suggestion(product, "chem_safety.adr_pictograms", _merge_list_value(_current_product_value(product, "chem_safety.adr_pictograms"), ["ADR_pollution"]), "high", "14", environmental))
    elif "GHS09" in ghs_codes:
        suggestions.append(
            _suggestion(
                product,
                "chem_safety.environmentally_hazardous",
                True,
                "review",
                "2/14",
                "GHS09 gefunden. Bitte prüfen, ob ADR umweltgefährdend ebenfalls gesetzt werden muss.",
                force_review=True,
            )
        )
    else:
        not_found.append("environmentally_hazardous")

    classification = extract_wgk_storage_from_sdb(sdb_data, existing_wgk=product.wgk, existing_storage_class=product.storage_class)
    wgk_proposal = classification.get("wgk")
    if isinstance(wgk_proposal, dict):
        suggestions.append(_suggestion(product, "chem_safety.wgk", wgk_proposal.get("value"), "high", str(wgk_proposal.get("source_section") or "15"), str(wgk_proposal.get("excerpt") or "")))
    else:
        not_found.append("wgk")
    storage_proposal = classification.get("storage_class")
    if isinstance(storage_proposal, dict):
        suggestions.append(_suggestion(product, "chem_safety.storage_class", storage_proposal.get("value"), "high", str(storage_proposal.get("source_section") or "7.2"), str(storage_proposal.get("excerpt") or "")))
    else:
        not_found.append("storage_class")

    if section_7 or section_12 or section_15:
        log.append({"level": "info", "message": "SDB Abschnitte 7/12/15 für Lagerung, Umwelt und nationale Vorschriften geprüft."})
    status = "needs_review" if any(item.get("status") in {"suggested", "needs_review"} for item in suggestions) else ("found" if suggestions else "no_source_found")
    return {
        "last_run_at": now,
        "status": status,
        "sources": sources,
        "found_values": found_values,
        "suggestions": suggestions,
        "not_found": not_found,
        "warnings": warnings,
        "log": log,
    }


def _enrichment_sources(documents: list[dict[str, object]], sdb_data: dict | None) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    if sdb_data:
        if sdb_data.get("pdf_url") or sdb_data.get("source_asset_id"):
            sources.append(
                {
                    "type": "sds_pdf",
                    "url": sdb_data.get("pdf_url") or sdb_data.get("source_url"),
                    "asset_id": sdb_data.get("source_asset_id"),
                    "status": sdb_data.get("parser_status") or "parsed",
                    "language": sdb_data.get("sdb_language") or "unknown",
                    "region": sdb_data.get("sdb_region") or "unknown",
                }
            )
    for document in documents:
        sources.append(
            {
                "type": document.get("role") or "document",
                "url": document.get("url"),
                "status": "found",
                "label": document.get("label"),
            }
        )
    return sources


def _general_field_candidates(
    fields: object,
    documents: list[dict[str, object]],
    raw_text: str,
    sections: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    field_map = fields if isinstance(fields, dict) else {}
    candidates: dict[str, dict[str, object]] = {}

    def add(field_name: str, value: object, source_section: str, evidence: str, confidence: str = "medium", *, force_review: bool = False) -> None:
        if _is_blank_value(value) or field_name in candidates:
            return
        candidates[field_name] = {
            "value": value,
            "source_section": source_section,
            "evidence": evidence,
            "confidence": confidence,
            "force_review": force_review,
        }

    for field_name in (
        "chemical_type",
        "ufi",
        "voc_content_percent",
        "cas_number",
        "ec_number",
        "density",
        "color",
        "odor",
        "ph_value",
        "flash_point",
        "boiling_point",
        "viscosity",
        "solubility",
        "business_only",
        "age_check_required",
        "shippable",
        "sds_available",
        "sds_url",
    ):
        value = _field_value(field_map, field_name)
        if not _is_blank_value(value):
            add(field_name, value, "Internet", f"{field_name}: {value}", "medium")

    section_9 = _section_content(sections, 9)
    section_1 = _section_content(sections, 1)
    section_2 = _section_content(sections, 2)
    add("ufi", _extract_ufi(f"{section_1}\n{section_2}\n{raw_text}"), "1/2", "UFI aus SDB erkannt.", "high")
    add("voc_content_percent", _extract_voc_content(section_9 or raw_text), "9", "VOC-Gehalt aus Abschnitt 9 erkannt.", "medium")
    for field_name, labels in {
        "density": ("Dichte", "Relative Dichte", "Dichte und/oder relative Dichte", "Density"),
        "color": ("Farbe", "Colour", "Color"),
        "odor": ("Geruch", "Odour", "Odor"),
        "ph_value": ("pH-Wert", "pH"),
        "flash_point": ("Flammpunkt", "Flash point"),
        "boiling_point": ("Siedebeginn und Siedebereich", "Siedepunkt", "Boiling point"),
        "viscosity": ("Viskosität", "Viscosity"),
        "solubility": ("Löslichkeit", "Wasserlöslichkeit", "Solubility"),
    }.items():
        value = _extract_labeled_value(section_9, labels)
        add(field_name, value, "9", f"{labels[0]} aus Abschnitt 9 erkannt.", "medium")

    sds_document = next((item for item in documents if str(item.get("role") or "").lower() == "sds" and item.get("url")), None)
    if sds_document:
        add("sds_available", True, "Internet", "SDB/SDS-Link gefunden.", "high")
        add("sds_url", sds_document.get("url"), "Internet", str(sds_document.get("label") or "SDB/SDS-Link gefunden."), "high")
    return candidates


def _section_content(sections: dict[str, dict[str, object]], section_number: int) -> str:
    return str((sections.get(f"section_{section_number}") or {}).get("content") or "")


def _field_value(fields: object, field_name: str) -> object:
    if not isinstance(fields, dict):
        return None
    entry = fields.get(field_name)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _suggestion(
    product: Product,
    field: str,
    suggested_value: object,
    confidence: str,
    source_section: str,
    evidence: str,
    *,
    force_review: bool = False,
) -> dict[str, object]:
    current_value = _current_product_value(product, field)
    status = "needs_review" if force_review else "suggested"
    if not force_review and _json_stable(current_value) == _json_stable(suggested_value):
        status = "found"
    return {
        "field": field,
        "current_value": current_value,
        "found_value": suggested_value,
        "suggested_value": suggested_value,
        "confidence": confidence,
        "source": "Internet/SDB" if not source_section or source_section == "Internet" else f"SDB Abschnitt {source_section}",
        "source_section": source_section,
        "evidence": _compact_excerpt(evidence),
        "status": status,
    }


def _current_product_value(product: Product, field: str) -> object:
    chem_safety = product.chemical_safety_json or {}
    if field.startswith("chem_safety."):
        key = field.split(".", 1)[1]
        if key == "ghs_pictograms":
            return chem_safety.get(key) or _value_as_codes(product.ghs_pictograms)
        if key == "signal_word":
            return chem_safety.get(key) or _normalize_signal_word(product.signal_word)
        if key == "adr_class":
            return chem_safety.get(key) or product.hazard_class
        if key == "wgk":
            return chem_safety.get(key) or product.wgk
        if key == "storage_class":
            return chem_safety.get(key) or product.storage_class
        return chem_safety.get(key)
    return getattr(product, field, None)


def _apply_enrichment_suggestion(product: Product, chem_safety: dict, field: str, value: object) -> None:
    if field.startswith("chem_safety."):
        key = field.split(".", 1)[1]
        if key == "ghs_pictograms":
            codes = _unique_codes(_value_as_codes(value))
            chem_safety[key] = codes
            product.ghs_pictograms = "|".join(codes) or None
        elif key == "signal_word":
            normalized = _normalize_signal_word(value)
            chem_safety[key] = normalized
            product.signal_word = {"danger": "GEFAHR", "warning": "ACHTUNG", "none": None}.get(normalized, str(value or "").strip() or None)
        elif key == "adr_pictograms":
            chem_safety[key] = _unique_codes(_value_as_codes(value))
            if "ADR_pollution" in chem_safety[key]:
                chem_safety["environmentally_hazardous"] = True
        elif key == "environmentally_hazardous":
            chem_safety[key] = bool(value)
        elif key == "adr_class":
            chem_safety[key] = str(value).strip()
            product.hazard_class = str(value).strip()
            product.adr_relevant = True
        elif key == "hazard_statements":
            chem_safety[key] = _unique_codes(_value_as_codes(value))
            product.hazard_statements = ", ".join(chem_safety[key]) or product.hazard_statements
        elif key == "precautionary_statements":
            chem_safety[key] = _unique_codes(_value_as_codes(value))
            product.precautionary_statements = ", ".join(chem_safety[key]) or product.precautionary_statements
        elif key == "euh_statements":
            chem_safety[key] = _unique_codes(_value_as_codes(value))
        elif key == "wgk":
            normalized = normalize_wgk(str(value)) if value else None
            product.wgk = normalized
            product.wgk_label = wgk_label(normalized)
            chem_safety[key] = normalized
            chem_safety["wgk_label"] = WGK_LABELS.get(normalized or "")
        elif key == "storage_class":
            normalized = normalize_storage_class(str(value)) if value else None
            product.storage_class = normalized
            product.storage_class_label = storage_class_label(normalized)
            chem_safety[key] = normalized
            chem_safety["storage_class_label"] = STORAGE_CLASS_LABELS.get(normalized or "")
        else:
            chem_safety[key] = value
        return
    if field in set(CHEMICAL_FIELD_NAMES):
        if field in {"adr_relevant", "sds_available", "business_only", "age_check_required", "shippable", "shop_active"}:
            setattr(product, field, bool(value))
            return
        setattr(product, field, str(value).strip() or None)
        return


def _extract_environmentally_hazardous(section_14: str) -> str | None:
    text = re.sub(r"\s+", " ", section_14 or "").strip()
    if not text:
        return None
    patterns = [
        r"\bUmweltgef[aä]hrdend\b\s*:?\s*(?:ja|yes|true)\b",
        r"\bEnvironmentally hazardous\b\s*:?\s*(?:yes|ja|true)\b",
        r"\bMarine pollutant\b\s*:?\s*(?:yes|ja|true)\b",
        r"\bMeeresschadstoff\b\s*:?\s*(?:ja|yes|true)\b",
        r"\bADR\b.{0,80}\bUmweltgef[aä]hrdend\b",
        r"\bUmweltgefahren\b\s*:?\s*umweltgef[aä]hrdend\b",
        r"\bFisch\b.{0,40}\bBaum\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return _compact_excerpt(match.group(0))
    return None


def _extract_adr_class(section_14: str) -> str | None:
    return _find_regex(section_14, r"(?:Transportgefahrenklassen|Gefahrzettel|Klasse|Class)\s*:?\s*([1-9](?:\.[1-9])?)\b")


def _extract_un_number_from_section_14(section_14: str) -> str | None:
    text = section_14 or ""
    for pattern in (
        r"\bUN\s*[-:]?\s*([0-9]{4})\b",
        r"(?:UN-Nummer|UN Nummer|UN number|UN-No\.?|UN No\.?)\s*:?\s*(?:UN\s*)?([0-9]{4})\b",
    ):
        value = _find_regex(text, pattern, group=1)
        if value:
            return value
    return None


def _normalize_adr_class(value: object) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b([1-9](?:\.[1-9])?)\b", str(value))
    return match.group(1) if match else None


def _normalize_un(value: object) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b(?:UN\s*)?([0-9]{4})\b", str(value), flags=re.I)
    return match.group(1) if match else None


def _extract_ufi(text: str) -> str | None:
    match = re.search(r"\bUFI\s*:?\s*([A-Z0-9]{4}(?:-[A-Z0-9]{4}){3})\b", text or "", flags=re.I)
    return match.group(1).upper() if match else None


def _extract_voc_content(text: str) -> str | None:
    match = re.search(r"\bVOC(?:[- ]?Gehalt| content)?\s*:?\s*([0-9]+(?:[.,][0-9]+)?)\s*%?", text or "", flags=re.I)
    return match.group(1).replace(",", ".") if match else None


def _extract_labeled_value(text: str, labels: tuple[str, ...]) -> str | None:
    normalized = (text or "").replace("\r", "\n")
    for label in labels:
        pattern = rf"(?im)^\s*[-•]?\s*{re.escape(label)}\s*:?\s+(.+?)\s*$"
        match = re.search(pattern, normalized)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
            if value and value.lower() not in {"nicht verfügbar", "not available", "keine daten verfügbar"}:
                return value
    return None


def _normalize_packing_group(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    match = re.search(r"\b(I{1,3}|II|III)\b", text)
    return match.group(1) if match else None


def _normalize_signal_word(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"danger", "gefahr", "gefährlich"}:
        return "danger"
    if text in {"warning", "achtung"}:
        return "warning"
    if text in {"none", "kein", "keines"}:
        return "none"
    return None


def _find_signal_word(text: str) -> str | None:
    match = re.search(r"(?:Signalwort|Signal word)\s*:?\s*(Gefahr|Achtung|Danger|Warning)\b", text or "", flags=re.I)
    return match.group(1) if match else None


def _extract_codes(text: str, pattern: str) -> list[str]:
    return _unique_codes(re.findall(pattern, text or "", flags=re.I))


def _find_regex(text: str, pattern: str, group: int = 0) -> str | None:
    match = re.search(pattern, text or "", flags=re.I)
    if not match:
        return None
    return str(match.group(group)).strip()


def _value_as_codes(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        parts = value
    else:
        parts = re.split(r"[\s,;|]+", str(value))
    return [str(part).strip().upper() for part in parts if str(part).strip()]


def _unique_codes(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _normalize_code(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def _normalize_code(value: object) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    upper = cleaned.upper()
    if upper in {"ADR_POLLUTION", "ADR-POLLUTION", "ADR_POLLUTANT", "ADR_ENVIRONMENT"}:
        return "ADR_pollution"
    if upper.startswith("ADR_"):
        return upper
    return upper


def _merge_list_value(current: object, additions: list[str]) -> list[str]:
    return _unique_codes(_value_as_codes(current) + additions)


def _compact_excerpt(text: object, limit: int = 300) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:limit]


def _should_apply_value(current: object, new_value: object, overwrite_existing: bool) -> bool:
    if _is_blank_value(new_value):
        return False
    if overwrite_existing:
        return True
    if isinstance(new_value, bool):
        return current in {None, False, ""} and new_value is True
    return _is_blank_value(current)


def _normalize_reference_urls(values: list[str | None]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if raw is None:
            continue
        for part in re.split(r"[\n,]+", str(raw)):
            candidate = part.strip()
            if not candidate:
                continue
            if not candidate.startswith(("http://", "https://")):
                candidate = f"https://{candidate.lstrip('/')}"
            if candidate in seen:
                continue
            seen.add(candidate)
            output.append(candidate)
    return output


def _looks_like_pdf_url(url: str | None) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.path.lower().endswith(".pdf")


def _unique_documents(values: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        url = str(item.get("url") or "").strip()
        role = str(item.get("role") or "").strip()
        if not url:
            continue
        key = (url, role)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def _json_stable(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _is_blank_value(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _unique_destination_path(path: Path) -> Path:
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate
