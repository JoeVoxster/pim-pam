from __future__ import annotations

import hashlib
import csv
import mimetypes
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from PIL import Image
import requests
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.models import Asset, Product, ProductAssetCandidate
from app.services.asset_service import create_asset_record, detect_asset_language
from app.suppliers.base import SupplierAssetCandidate
from app.suppliers.tintolav import SupplierExtractor as TintolavExtractor


SUPPORTED_LANGUAGES = {
    "de-ch": "de-CH",
    "de-de": "de-DE",
    "de": "de",
    "fr-ch": "fr-CH",
    "fr-fr": "fr-FR",
    "fr": "fr",
    "it-ch": "it-CH",
    "it-it": "it-IT",
    "it": "it",
    "en-gb": "en-GB",
    "en-us": "en-US",
    "en": "en",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf"}
MAX_ASSET_DOWNLOAD_BYTES = 50 * 1024 * 1024
MIN_AUTOMATIC_IMAGE_WIDTH = 300
MIN_AUTOMATIC_IMAGE_HEIGHT = 300
REQUEST_HEADERS = {"User-Agent": "PIM-PAM Asset Enrichment/1.0"}
LOCAL_SUPPLIER_ASSET_MAPPING_PATHS = (Path("/opt/output/tintolav_export/asset_mapping.csv"),)


@dataclass(slots=True)
class AssetDiscovery:
    product_id: int
    asset_url: str
    source_url: str | None
    asset_type: str
    title: str | None = None
    filename: str | None = None
    language_code: str | None = None
    confidence: float = 0.6


def enrich_missing_product_assets(
    session: Session,
    product_ids: list[int],
    *,
    storage_root: str | Path,
    timeout_seconds: int = 20,
    max_download_bytes: int = MAX_ASSET_DOWNLOAD_BYTES,
) -> dict[str, object]:
    unique_product_ids = _unique_ints(product_ids)
    if not unique_product_ids:
        return {"products_checked": 0, "saved_count": 0, "skipped_count": 0, "error_count": 0, "items": [], "logs": []}
    products = session.scalars(
        select(Product)
        .where(Product.id.in_(unique_product_ids))
        .options(joinedload(Product.assets), joinedload(Product.brand), selectinload(Product.variants))
    ).unique().all()
    root = Path(storage_root)
    items: list[dict[str, object]] = []
    logs: list[str] = []
    for product in products:
        search_terms = build_asset_search_terms(product)
        logs.extend(f"{product.id} · {product.sku}: Suchhinweis: {term}" for term in search_terms[:8])
        discoveries = discover_product_asset_candidates(product, timeout_seconds=timeout_seconds)
        if not discoveries:
            logs.append(f"{product.id} · {product.sku}: Keine Asset-Kandidaten gefunden.")
            continue
        for discovery in discoveries:
            result = _save_discovery(
                session,
                product,
                discovery,
                storage_root=root,
                timeout_seconds=timeout_seconds,
                max_download_bytes=max_download_bytes,
            )
            items.append(result)
            logs.append(f"{product.id} · {product.sku}: {result['status']}: {result.get('message') or result.get('filename') or discovery.asset_url}")
    return {
        "products_checked": len(products),
        "saved_count": sum(1 for item in items if item.get("status") == "saved"),
        "skipped_count": sum(1 for item in items if item.get("status") == "skipped"),
        "error_count": sum(1 for item in items if item.get("status") == "error"),
        "items": items,
        "logs": logs,
    }


def build_asset_search_terms(product: Product) -> list[str]:
    brand = product.brand.name if product.brand else ""
    base = " ".join(part for part in [brand, product.title, product.sku] if part).strip()
    terms = []
    for suffix in (
        "SDB",
        "Sicherheitsdatenblatt",
        "SDS",
        "Safety Data Sheet",
        "Produktdatenblatt",
        "Product Data Sheet",
        "Technical Data Sheet",
        "TDS",
        "PDF",
        "Produktbild",
        "image",
    ):
        terms.append(f"{base} {suffix}".strip())
    return terms


def discover_product_asset_candidates(product: Product, *, timeout_seconds: int = 20) -> list[AssetDiscovery]:
    discoveries: list[AssetDiscovery] = []
    for source_url in _product_source_urls(product):
        if _is_direct_asset_url(source_url):
            discoveries.append(_discovery_from_url(product, source_url, source_url, None, None, 0.74))
            continue
        try:
            body = _http_get_text(source_url, timeout_seconds)
        except Exception:
            continue
        if _is_tintolav_url(source_url):
            discoveries.extend(_discover_tintolav_assets(product, source_url, body))
        discoveries.extend(_discover_generic_assets(product, source_url, body))
    discoveries.extend(_discover_local_supplier_asset_mapping(product))
    return _dedupe_discoveries(discoveries)


def build_asset_filename(product: Product, asset_type: str, source_url: str, *, language_code: str | None = None, extension: str | None = None) -> str:
    stem = _product_asset_stem(product)
    ext = _safe_extension(extension or Path(urlparse(source_url).path).suffix, ".pdf" if _is_document_type(asset_type) else ".jpg")
    if _is_document_type(asset_type):
        language = canonical_pdf_language(language_code or detect_pdf_language(source_url))
        return f"{stem}-{_document_suffix(asset_type)}-{language}{ext}"
    return f"{stem}-{_image_suffix(asset_type)}{ext}"


def detect_pdf_language(*hints: str | None) -> str:
    haystack = " ".join(str(hint or "") for hint in hints)
    normalized = haystack.lower()
    normalized = re.sub(r"[_./\\]+", "-", normalized)
    for key, value in sorted(SUPPORTED_LANGUAGES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(^|[^a-z]){re.escape(key)}([^a-z]|$)", normalized):
            return value
    language_words = (
        ("de", ("deutsch", "german", "sicherheitsdatenblatt")),
        ("fr", ("francais", "français", "french", "fiche-de-donnees", "fiche de donnees")),
        ("it", ("italiano", "italian", "scheda-di-sicurezza", "scheda di sicurezza")),
        ("en", ("english", "safety-data-sheet", "safety data sheet")),
    )
    for code, tokens in language_words:
        if any(token in normalized for token in tokens):
            return code
    return "unknown"


def canonical_pdf_language(value: str | None) -> str:
    if not value:
        return "unknown"
    return SUPPORTED_LANGUAGES.get(value.lower().replace("_", "-"), value if value in SUPPORTED_LANGUAGES.values() else "unknown")


def classify_asset_type(url: str, *, mime_type: str | None = None, title: str | None = None, context: str | None = None) -> str:
    text = f"{url} {mime_type or ''} {title or ''} {context or ''}".lower()
    if "pdf" in (mime_type or "").lower() or re.search(r"\.pdf(?:[?#]|$)", url, flags=re.I):
        if any(token in text for token in ("sdb", "sicherheitsdatenblatt")):
            return "sdb"
        if any(token in text for token in ("sds", "safety data sheet", "security sheet")):
            return "sds"
        if any(token in text for token in ("tds", "technical data sheet", "technical_datasheet", "technical-sheet")):
            return "technical_datasheet"
        if any(token in text for token in ("datasheet", "data sheet", "datenblatt", "produktdatenblatt")):
            return "datasheet"
        return "pdf"
    if "image/" in (mime_type or "").lower() or re.search(r"\.(?:jpe?g|png|webp|gif)(?:[?#]|$)", url, flags=re.I):
        if any(token in text for token in ("packaging", "package", "gebinde", "verpackung")):
            return "packaging_image"
        if any(token in text for token in ("application", "anwendung", "use")):
            return "application_image"
        return "product_image"
    return "unknown"


def _save_discovery(
    session: Session,
    product: Product,
    discovery: AssetDiscovery,
    *,
    storage_root: Path,
    timeout_seconds: int,
    max_download_bytes: int,
) -> dict[str, object]:
    candidate = _upsert_asset_candidate(session, discovery)
    if _existing_asset_by_url(session, product.id, discovery.asset_url) is not None:
        candidate.status = "skipped"
        return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "skipped", "message": "Quelle bereits als Asset vorhanden."}
    try:
        payload, mime_type = _http_get_binary(discovery.asset_url, timeout_seconds, max_download_bytes)
    except Exception as exc:
        candidate.status = "error"
        candidate.error_message = str(exc)
        return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "error", "message": str(exc)}
    unsupported_reason = _unsupported_download_reason(discovery.asset_url, mime_type, payload)
    if unsupported_reason:
        candidate.status = "skipped"
        candidate.error_message = unsupported_reason
        return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "skipped", "message": unsupported_reason}
    checksum = hashlib.sha256(payload).hexdigest()
    if _existing_asset_by_checksum(session, product.id, checksum) is not None:
        candidate.status = "skipped"
        return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "skipped", "message": "Datei mit gleicher Prüfsumme bereits vorhanden."}
    asset_type = classify_asset_type(discovery.asset_url, mime_type=mime_type, title=discovery.title, context=discovery.filename)
    if asset_type == "unknown":
        asset_type = discovery.asset_type or "unknown"
    if _is_image_type(asset_type, mime_type):
        rejected_url_reason = _rejected_image_url_reason(discovery.asset_url)
        if rejected_url_reason:
            candidate.status = "skipped"
            candidate.error_message = rejected_url_reason
            return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "skipped", "message": rejected_url_reason}
        quality_issue = _image_quality_issue(payload, mime_type)
        if quality_issue:
            candidate.status = "skipped"
            candidate.error_message = quality_issue
            return {"product_id": product.id, "asset_url": discovery.asset_url, "status": "skipped", "message": quality_issue}
    extension = _extension_from_mime_or_url(mime_type, discovery.asset_url, asset_type)
    filename = build_asset_filename(
        product,
        asset_type,
        discovery.asset_url,
        language_code=discovery.language_code,
        extension=extension,
    )
    target_dir = _resolve_asset_download_dir(
        storage_root,
        product.id,
        "documents" if _is_document_type(asset_type) else "images",
    )
    target = _unique_path(target_dir / filename)
    target.write_bytes(payload)
    asset = create_asset_record(session, target, product_id=product.id, alt_text=discovery.title or product.title, source_url=discovery.asset_url)
    asset.asset_type = asset_type
    asset.title = discovery.title or _title_for_asset_type(asset_type)
    asset.description = f"Automatisch über Produkt-Asset-Anreicherung gefunden. Quelle: {discovery.source_url or discovery.asset_url}"
    asset.language_code = (
        detect_asset_language(
            target,
            mime_type=mime_type,
            filename=target.name,
            source_url=discovery.asset_url,
            title=discovery.title,
        )
        or canonical_pdf_language(discovery.language_code or detect_pdf_language(discovery.asset_url, discovery.filename, discovery.title))
        if _is_document_type(asset_type)
        else None
    )
    asset.checksum = checksum
    candidate.status = "downloaded"
    candidate.filename = target.name
    candidate.language = asset.language_code
    session.flush()
    return {
        "product_id": product.id,
        "asset_id": asset.id,
        "asset_url": discovery.asset_url,
        "asset_type": asset_type,
        "language_code": asset.language_code,
        "filename": target.name,
        "status": "saved",
    }


def _discover_tintolav_assets(product: Product, source_url: str, body: str) -> list[AssetDiscovery]:
    result = TintolavExtractor().extract_from_html(source_url, body)
    discoveries: list[AssetDiscovery] = []
    for item in result.pdfs:
        discoveries.append(_discovery_from_supplier_item(product, source_url, item, result.confidence))
    for item in result.images:
        discoveries.append(_discovery_from_supplier_item(product, source_url, item, result.confidence))
    return discoveries


def _discover_generic_assets(product: Product, source_url: str, body: str) -> list[AssetDiscovery]:
    discoveries: list[AssetDiscovery] = []
    for href, text in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", body, flags=re.I | re.S):
        url = urljoin(source_url, href.strip())
        label = _clean_html_text(text)
        if not _is_direct_asset_url(url) and not _asset_link_context(label, url):
            continue
        discoveries.append(_discovery_from_url(product, url, source_url, label, Path(urlparse(url).path).name, 0.62))
    for match in re.finditer(r"<img[^>]+(?:src|data-src)=[\"']([^\"']+)[\"'][^>]*>", body, flags=re.I | re.S):
        tag = match.group(0)
        src = match.group(1)
        alt = _clean_html_text(_first_match(tag, r"alt=[\"']([^\"']*)[\"']"))
        url = urljoin(source_url, src.strip())
        if re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:[?#]|$)", url, flags=re.I) and not _rejected_image_url_reason(url):
            discoveries.append(_discovery_from_url(product, url, source_url, alt, Path(urlparse(url).path).name, 0.58))
    return discoveries


def _discover_local_supplier_asset_mapping(product: Product) -> list[AssetDiscovery]:
    product_tokens = _product_match_tokens(product)
    if not product_tokens:
        return []
    discoveries: list[AssetDiscovery] = []
    for mapping_path in LOCAL_SUPPLIER_ASSET_MAPPING_PATHS:
        if not mapping_path.exists():
            continue
        with mapping_path.open(newline="", encoding="utf-8", errors="ignore") as handle:
            for row in csv.DictReader(handle):
                row_text = " ".join(str(value or "") for value in row.values()).lower()
                if not any(token in row_text for token in product_tokens):
                    continue
                source_url = (row.get("source_url") or "").strip()
                if not source_url.startswith(("http://", "https://")):
                    continue
                role = (row.get("role") or row.get("asset_type") or "").strip()
                asset_type = _normalize_supplier_asset_type(role, source_url, row.get("label"))
                discoveries.append(
                    AssetDiscovery(
                        product_id=product.id,
                        asset_url=source_url,
                        source_url=(row.get("page_url") or "").strip() or None,
                        asset_type=asset_type,
                        title=(row.get("label") or row.get("product_title") or row.get("product_name") or "").strip() or None,
                        filename=(row.get("file_name") or "").strip() or None,
                        language_code=detect_pdf_language(row.get("extracted_text"), row.get("label"), row.get("file_name"), source_url) if _is_document_type(asset_type) else None,
                        confidence=0.86,
                    )
                )
    return discoveries


def _discovery_from_supplier_item(product: Product, source_url: str, item: SupplierAssetCandidate, confidence: float) -> AssetDiscovery:
    asset_url = _preferred_asset_url(item.asset_url)
    asset_type = _normalize_supplier_asset_type(item.asset_type or item.role or "unknown", asset_url, item.title)
    language = item.language if _is_document_type(asset_type) else None
    return AssetDiscovery(
        product_id=product.id,
        asset_url=asset_url,
        source_url=source_url,
        asset_type=asset_type,
        title=item.title,
        filename=item.filename or Path(urlparse(asset_url).path).name,
        language_code=language,
        confidence=confidence,
    )


def _discovery_from_url(product: Product, asset_url: str, source_url: str | None, title: str | None, filename: str | None, confidence: float) -> AssetDiscovery:
    asset_url = _preferred_asset_url(asset_url)
    asset_type = classify_asset_type(asset_url, title=title, context=filename)
    return AssetDiscovery(
        product_id=product.id,
        asset_url=asset_url,
        source_url=source_url,
        asset_type=asset_type,
        title=title,
        filename=filename or Path(urlparse(asset_url).path).name,
        language_code=detect_pdf_language(asset_url, filename, title) if _is_document_type(asset_type) else None,
        confidence=confidence,
    )


def _upsert_asset_candidate(session: Session, discovery: AssetDiscovery) -> ProductAssetCandidate:
    row = session.scalar(
        select(ProductAssetCandidate).where(
            ProductAssetCandidate.product_id == discovery.product_id,
            ProductAssetCandidate.asset_url == discovery.asset_url,
        )
    )
    if row is None:
        row = ProductAssetCandidate(product_id=discovery.product_id, asset_url=discovery.asset_url)
        session.add(row)
    row.source_url = discovery.source_url
    row.asset_type = discovery.asset_type or "unknown"
    row.title = discovery.title
    row.filename = discovery.filename
    row.language = discovery.language_code
    row.status = "new"
    row.error_message = None
    session.flush()
    return row


def _product_source_urls(product: Product) -> list[str]:
    values: list[str] = []
    for raw in (product.source_url_final, product.source_url):
        if not raw:
            continue
        values.extend(re.split(r"[\n,]+", raw))
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        url = value.strip()
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            output.append(url)
    return output


def _resolve_asset_download_dir(storage_root: Path, product_id: int, folder: str) -> Path:
    preferred = storage_root / "products" / str(product_id) / folder
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        if os.access(preferred, os.W_OK | os.X_OK):
            return preferred
    except PermissionError:
        pass

    fallback = storage_root / "_imports" / f"product-{product_id}" / folder
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _product_match_tokens(product: Product) -> set[str]:
    tokens: set[str] = set()
    for value in [product.sku, *(variant.sku for variant in product.variants or [])]:
        raw = (value or "").strip().lower()
        if not raw:
            continue
        tokens.add(raw)
        tokens.add(raw.replace("xx", ""))
        tokens.add(raw.replace("-", ""))
        tokens.add(raw.replace("-", "").replace("xx", ""))
    return {token for token in tokens if len(token) >= 5}


def _product_asset_stem(product: Product) -> str:
    brand = product.brand.name if product.brand else ""
    return slugify(" ".join(part for part in [brand, product.title, product.sku] if part), separator="-") or f"product-{product.id}"


def _document_suffix(asset_type: str) -> str:
    return {
        "sdb": "sdb",
        "sds": "sds",
        "technical_datasheet": "tds",
        "datasheet": "datasheet",
        "pdf": "pdf",
    }.get(asset_type, "document")


def _image_suffix(asset_type: str) -> str:
    return {
        "packaging_image": "packaging-image",
        "application_image": "application-image",
        "product_image": "product-image",
    }.get(asset_type, "product-image")


def _normalize_supplier_asset_type(value: str, url: str, title: str | None) -> str:
    normalized = (value or "").lower()
    if normalized in {"sds", "sdb", "technical_datasheet", "datasheet", "product_image", "packaging_image", "application_image"}:
        return normalized
    if normalized in {"technical data sheet", "tds"}:
        return "technical_datasheet"
    if normalized in {"product_sheet", "sheet"}:
        return "datasheet"
    return classify_asset_type(url, title=title)


def _is_document_type(asset_type: str | None) -> bool:
    return (asset_type or "") in {"pdf", "sdb", "sds", "datasheet", "technical_datasheet"} and not str(asset_type or "").endswith("_image")


def _is_image_type(asset_type: str | None, mime_type: str | None = None) -> bool:
    return str(asset_type or "").endswith("_image") or str(mime_type or "").lower().startswith("image/")


def _rejected_image_url_reason(url: str) -> str | None:
    normalized = (url or "").lower()
    path = urlparse(url or "").path.lower()
    filename = Path(path).name
    if "/thumbnail/" in normalized or "/thumbnails/" in normalized or re.search(r"/thumbnails?/\d+x(?:/|$)", normalized):
        return "Thumbnail-Bild übersprungen."
    if any(token in normalized for token in ("/skin/", "/frontend/", "/static/", "/theme/", "/template/")):
        return "Layout-/Theme-Bild übersprungen."
    if re.search(r"(^|[^a-z0-9])(sprite|logo|icon|placeholder|blank|menu)([^a-z0-9]|$)", filename):
        return "Nicht-produktbezogenes Layoutbild übersprungen."
    return None


def _image_quality_issue(payload: bytes, mime_type: str | None) -> str | None:
    if not str(mime_type or "").lower().startswith("image/"):
        return None
    try:
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
    except Exception:
        return "Bild konnte nicht validiert werden."
    if width < MIN_AUTOMATIC_IMAGE_WIDTH or height < MIN_AUTOMATIC_IMAGE_HEIGHT:
        return f"Bild zu klein für Produktbild ({width}x{height}px)."
    return None


def _is_tintolav_url(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return any(domain in host for domain in TintolavExtractor.supported_domains)


def _is_direct_asset_url(url: str) -> bool:
    return bool(re.search(r"\.(?:pdf|jpe?g|png|webp|gif)(?:[?#]|$)", url, flags=re.I))


def _asset_link_context(label: str | None, url: str) -> bool:
    text = f"{label or ''} {url}".lower()
    return any(token in text for token in ("sdb", "sds", "safety data sheet", "sicherheitsdatenblatt", "datasheet", "data sheet", "tds", "pdf", "download"))


def _http_get_text(url: str, timeout_seconds: int) -> str:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout_seconds)
    response.raise_for_status()
    return response.text


def _http_get_binary(url: str, timeout_seconds: int, max_bytes: int) -> tuple[bytes, str]:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout_seconds, stream=True)
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    payload = bytearray()
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise ValueError("Download ist grösser als erlaubt.")
    return bytes(payload), content_type or mimetypes.guess_type(urlparse(url).path)[0] or "application/octet-stream"


def _unsupported_download_reason(url: str, mime_type: str | None, payload: bytes) -> str | None:
    normalized_mime = str(mime_type or "").lower()
    is_pdf_payload = payload.lstrip().startswith(b"%PDF")
    is_image_mime = normalized_mime.startswith("image/")
    is_pdf_mime = "pdf" in normalized_mime
    if is_image_mime or is_pdf_mime or is_pdf_payload:
        return None
    if payload.lstrip().lower().startswith((b"<!doctype html", b"<html")) or "html" in normalized_mime:
        return "HTML-/Downloadseite übersprungen; kein direktes Asset."
    if _is_direct_asset_url(url):
        return None
    return f"Download übersprungen; nicht unterstützter Content-Type: {mime_type or 'unbekannt'}."


def _existing_asset_by_url(session: Session, product_id: int, source_url: str) -> Asset | None:
    return session.scalar(
        select(Asset).where(
            Asset.product_id == product_id,
            Asset.source_url == source_url,
        )
    )


def _existing_asset_by_checksum(session: Session, product_id: int, checksum: str) -> Asset | None:
    return session.scalar(
        select(Asset).where(
            Asset.product_id == product_id,
            Asset.checksum == checksum,
        )
    )


def _extension_from_mime_or_url(mime_type: str, url: str, asset_type: str) -> str:
    if "pdf" in (mime_type or "").lower() or _is_document_type(asset_type):
        return ".pdf"
    guessed = mimetypes.guess_extension(mime_type or "") or Path(urlparse(url).path).suffix
    return _safe_extension(guessed, ".jpg")


def _safe_extension(value: str | None, fallback: str) -> str:
    extension = (value or "").lower().strip()
    if not extension.startswith("."):
        extension = f".{extension}" if extension else ""
    if extension in DOCUMENT_EXTENSIONS or extension in IMAGE_EXTENSIONS:
        return extension
    return fallback


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _dedupe_discoveries(discoveries: list[AssetDiscovery]) -> list[AssetDiscovery]:
    best_by_key: dict[str, AssetDiscovery] = {}
    order: list[str] = []
    for discovery in discoveries:
        normalized = _preferred_asset_url(discovery.asset_url.strip())
        if not normalized:
            continue
        if normalized != discovery.asset_url:
            discovery = AssetDiscovery(
                product_id=discovery.product_id,
                asset_url=normalized,
                source_url=discovery.source_url,
                asset_type=classify_asset_type(normalized, title=discovery.title, context=discovery.filename) or discovery.asset_type,
                title=discovery.title,
                filename=discovery.filename or Path(urlparse(normalized).path).name,
                language_code=discovery.language_code,
                confidence=discovery.confidence,
            )
        key = _asset_candidate_key(discovery)
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = discovery
            order.append(key)
            continue
        if _asset_candidate_score(discovery) > _asset_candidate_score(current):
            best_by_key[key] = discovery
    return [best_by_key[key] for key in order]


def _preferred_asset_url(url: str) -> str:
    asset_type = classify_asset_type(url)
    if _is_image_type(asset_type):
        return _preferred_image_url(url)
    return url


def _preferred_image_url(url: str) -> str:
    parsed = urlparse(url or "")
    path = parsed.path
    if not path:
        return url
    preferred_path = re.sub(
        r"(?i)(/images/dacshop/upload)/thumbnails/[^/]+/([^/]+)$",
        r"\1/\2",
        path,
    )
    if preferred_path == path:
        return url
    return parsed._replace(path=preferred_path, query="", fragment="").geturl()


def _asset_candidate_key(discovery: AssetDiscovery) -> str:
    url = _preferred_asset_url(discovery.asset_url)
    asset_type = discovery.asset_type or classify_asset_type(url)
    parsed = urlparse(url)
    path = unquote(parsed.path or "").lower()
    if _is_image_type(asset_type):
        basename = Path(path).name
        if basename:
            return f"image:{parsed.netloc.lower()}:{basename}"
    return f"{asset_type}:{parsed.netloc.lower()}:{path}"


def _asset_candidate_score(discovery: AssetDiscovery) -> float:
    url = _preferred_asset_url(discovery.asset_url)
    asset_type = discovery.asset_type or classify_asset_type(url)
    score = float(discovery.confidence or 0) * 100
    if not _is_image_type(asset_type):
        return score
    normalized = url.lower()
    if "/thumbnails/" in normalized or "/thumbnail/" in normalized:
        score -= 500
    if "/images/dacshop/upload/" in normalized and "/thumbnails/" not in normalized:
        score += 300
    dimensions = _dimensions_from_url(url)
    if dimensions:
        width, height = dimensions
        score += min(width * height / 1000, 1000)
    return score


def _dimensions_from_url(url: str) -> tuple[int, int] | None:
    match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?:[a-z])?(?!\d)", url, flags=re.I)
    if match:
        try:
            return int(match.group(1)), int(match.group(2))
        except Exception:
            return None
    match = re.search(r"(?<!\d)(\d{2,5})x(?:[a-z])?(?=/|_|-|$)", url, flags=re.I)
    if not match:
        return None
    try:
        value = int(match.group(1))
        return value, value
    except Exception:
        return None


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        try:
            item = int(value)
        except Exception:
            continue
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _clean_html_text(value: str | None) -> str | None:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unquote(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _first_match(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value or "", flags=re.I | re.S)
    return match.group(1) if match else None


def _title_for_asset_type(asset_type: str) -> str:
    return {
        "sdb": "Sicherheitsdatenblatt",
        "sds": "Safety Data Sheet",
        "datasheet": "Produktdatenblatt",
        "technical_datasheet": "Technisches Datenblatt",
        "product_image": "Produktbild",
        "packaging_image": "Verpackungsbild",
        "application_image": "Anwendungsbild",
    }.get(asset_type, "Produkt-Asset")
