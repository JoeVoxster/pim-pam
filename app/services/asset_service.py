from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from shutil import copy2
from uuid import uuid4

import requests
from PIL import Image
from slugify import slugify
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Asset
from app.schemas.pim import AssetCreate
from app.services.r2_storage_service import BunnyStorage, CloudflareR2Storage, remote_object_key_to_storage_path


ALLOWED_ASSET_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".pdf",
    ".csv",
    ".xlsx",
    ".xls",
    ".txt",
    ".json",
    ".xml",
}
BLOCKED_ASSET_EXTENSIONS = {".exe", ".bat", ".cmd", ".sh", ".php", ".js", ".html", ".htm"}
ASSET_TYPE_FOLDERS = {
    "product_image": "images",
    "product_gallery": "images",
    "safety_data_sheet": "sdb",
    "technical_data_sheet": "datasheets",
    "manual": "manuals",
    "invoice_pdf": "invoices",
    "import_file": "imports",
    "other": "other",
}


def import_asset_file(
    session: Session,
    source_path: str | Path,
    storage_root: str | Path,
    product_id: int | None = None,
    variant_id: int | None = None,
    alt_text: str | None = None,
    source_url: str | None = None,
) -> Asset:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(source)
    target_root = Path(storage_root)
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / source.name
    if source.resolve() != target.resolve():
        copy2(source, target)
    return create_asset_record(
        session,
        target,
        product_id=product_id,
        variant_id=variant_id,
        alt_text=alt_text,
        source_url=source_url,
    )


def create_asset_record(
    session: Session,
    file_path: str | Path,
    product_id: int | None = None,
    variant_id: int | None = None,
    alt_text: str | None = None,
    source_url: str | None = None,
) -> Asset:
    path = Path(file_path)
    stat = path.stat()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    width = None
    height = None
    if mime_type.startswith("image/"):
        try:
            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            width = None
            height = None
    sort_scope_column = Asset.product_id if product_id is not None else Asset.variant_id
    sort_scope_value = product_id if product_id is not None else variant_id
    sort_order = 0
    if sort_scope_value is not None:
        sort_order = (
            session.scalar(select(func.coalesce(func.max(Asset.sort_order), -1)).where(sort_scope_column == sort_scope_value)) or -1
        ) + 1
    payload = AssetCreate(
        product_id=product_id,
        variant_id=variant_id,
        filename=path.name,
        original_filename=path.name,
        mime_type=mime_type,
        file_size=stat.st_size,
        width=width,
        height=height,
        storage_path=str(path),
        source_url=source_url,
        checksum=_sha256(path),
        alt_text=alt_text,
        sort_order=sort_order,
    )
    asset = Asset(**payload.model_dump())
    asset.stored_filename = path.name
    asset.file_extension = path.suffix.lower()
    asset.storage_provider = "local"
    asset.status = "active"
    asset.uploaded_at = datetime.now(timezone.utc)
    session.add(asset)
    session.flush()
    return asset


def upload_r2_asset_from_bytes(
    session: Session,
    payload: bytes,
    filename: str,
    *,
    asset_type: str = "other",
    product_id: int | None = None,
    language_code: str | None = None,
    title: str | None = None,
    description: str | None = None,
    source: str = "manual",
    storage: CloudflareR2Storage | None = None,
    max_upload_size_mb: int | None = None,
    path_prefix: str = "prod/assets",
    allowed_file_types: str | None = None,
) -> tuple[Asset, list[dict[str, str]]]:
    log: list[dict[str, str]] = []
    original_filename = Path(filename or "asset").name
    log.append({"level": "info", "message": f"Upload gestartet: {original_filename}"})
    extension = Path(original_filename).suffix.lower()
    _validate_upload(payload, original_filename, extension, max_upload_size_mb=max_upload_size_mb, allowed_file_types=allowed_file_types)
    log.append({"level": "info", "message": "Datei validiert."})
    mime_type = mimetypes.guess_type(original_filename)[0] or "application/octet-stream"
    checksum = hashlib.sha256(payload).hexdigest()
    object_key = build_r2_object_key(
        original_filename,
        asset_type=asset_type,
        product_id=product_id,
        language_code=language_code,
        path_prefix=path_prefix,
    )
    log.append({"level": "info", "message": f"Object Key erzeugt: {object_key}"})
    r2 = storage or CloudflareR2Storage()
    provider = _storage_provider_name(r2)
    log.append({"level": "info", "message": f"Upload zu {provider} gestartet."})
    r2.upload_fileobj(
        BytesIO(payload),
        object_key,
        content_type=mime_type,
        metadata={
            "original_filename": _metadata_value(original_filename),
            "asset_type": _metadata_value(asset_type),
            "source": _metadata_value(source),
            "checksum_sha256": checksum,
        },
    )
    log.append({"level": "info", "message": f"Upload zu {provider} erfolgreich."})
    width, height = _image_size_from_bytes(payload, mime_type)
    now = datetime.now(timezone.utc)
    asset = Asset(
        product_id=product_id,
        variant_id=None,
        filename=Path(object_key).name,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=len(payload),
        width=width,
        height=height,
        storage_path=remote_object_key_to_storage_path(object_key, r2.settings.bucket, provider=provider),
        source_url=None,
        checksum=checksum,
        alt_text=title,
        sort_order=_next_asset_sort_order(session, product_id=product_id),
        stored_filename=Path(object_key).name,
        object_key=object_key,
        bucket=r2.settings.bucket,
        storage_provider=provider,
        file_extension=extension,
        asset_type=asset_type,
        title=title,
        description=description,
        language_code=language_code,
        public_url=r2.public_url(object_key),
        status="uploaded",
        uploaded_at=now,
    )
    session.add(asset)
    session.flush()
    log.append({"level": "info", "message": f"Asset-Metadaten gespeichert: Asset {asset.id}"})
    return asset, log


def upload_selected_assets_to_r2(
    session: Session,
    asset_ids: list[int],
    *,
    storage: CloudflareR2Storage,
    max_upload_size_mb: int | None = None,
    path_prefix: str = "prod/assets",
    allowed_file_types: str | None = None,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    for raw_asset_id in asset_ids:
        try:
            asset_id = int(raw_asset_id)
        except Exception:
            results.append({"asset_id": raw_asset_id, "status": "error", "message": "Ungültige Asset-ID."})
            continue
        if asset_id in seen_ids:
            results.append({"asset_id": asset_id, "status": "skipped", "message": "Doppelte Auswahl übersprungen."})
            continue
        seen_ids.add(asset_id)
        asset = session.get(
            Asset,
            asset_id,
            options=[joinedload(Asset.product), joinedload(Asset.variant)],
        )
        results.append(
            _upload_existing_asset_to_r2(
                session,
                asset,
                storage=storage,
                max_upload_size_mb=max_upload_size_mb,
                path_prefix=path_prefix,
                allowed_file_types=allowed_file_types,
            )
        )
    return {
        "total": len(results),
        "uploaded_count": sum(1 for item in results if item.get("status") == "uploaded"),
        "skipped_count": sum(1 for item in results if item.get("status") == "skipped"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
        "items": results,
    }


def _upload_existing_asset_to_r2(
    session: Session,
    asset: Asset | None,
    *,
    storage: CloudflareR2Storage,
    max_upload_size_mb: int | None,
    path_prefix: str,
    allowed_file_types: str | None,
) -> dict[str, object]:
    if asset is None:
        return {"asset_id": None, "status": "error", "message": "Asset existiert nicht."}
    product_id = asset.product_id or (asset.variant.product_id if asset.variant else None)
    base = _asset_result_base(asset, product_id)
    skip_reason = _asset_r2_upload_skip_reason(asset)
    if skip_reason:
        return {**base, "status": "skipped", "message": skip_reason}
    provider = _storage_provider_name(storage)
    if asset.storage_provider == provider:
        return {**base, "status": "skipped", "message": f"Asset ist bereits in {provider} gespeichert.", "target_asset_id": asset.id}
    duplicate = _find_existing_remote_duplicate(session, asset, product_id, provider)
    if duplicate is not None:
        return {
            **base,
            "status": "skipped",
            "message": f"{provider}-Dublette existiert bereits: Asset {duplicate.id}.",
            "target_asset_id": duplicate.id,
        }
    try:
        payload = _asset_payload_for_upload(asset)
        new_asset, _log = upload_r2_asset_from_bytes(
            session,
            payload,
            asset.original_filename or asset.filename,
            asset_type=asset.asset_type or _asset_type_from_mime(asset.mime_type),
            product_id=product_id,
            language_code=asset.language_code,
            title=asset.title or asset.alt_text,
            description=asset.description,
            source="product_grid_assets",
            storage=storage,
            max_upload_size_mb=max_upload_size_mb,
            path_prefix=path_prefix,
            allowed_file_types=allowed_file_types,
        )
        return {
            **base,
            "status": "uploaded",
            "message": f"Asset wurde in {provider} übernommen: Asset {new_asset.id}.",
            "target_asset_id": new_asset.id,
            "object_key": new_asset.object_key,
        }
    except Exception as exc:
        return {**base, "status": "error", "message": str(exc)}


def _asset_payload_for_upload(asset: Asset) -> bytes:
    storage_path = str(asset.storage_path or "")
    if asset.storage_provider in {"cloudflare_r2", "bunny_storage"} or storage_path.startswith(("r2://", "bunny://")):
        remote_url = asset.public_url or asset.source_url
        if not remote_url or not str(remote_url).startswith(("http://", "https://")):
            raise ValueError(f"Remote-Asset kann nicht ohne öffentliche URL migriert werden: {asset.storage_path}")
        response = requests.get(str(remote_url), timeout=60)
        response.raise_for_status()
        return response.content
    source_path = Path(storage_path)
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"Quelldatei nicht gefunden: {asset.storage_path}")
    return source_path.read_bytes()


def _find_existing_remote_duplicate(session: Session, asset: Asset, product_id: int | None, provider: str) -> Asset | None:
    if not asset.checksum:
        return None
    stmt = (
        select(Asset)
        .where(
            Asset.storage_provider == provider,
            Asset.checksum == asset.checksum,
            Asset.product_id == product_id,
        )
        .order_by(Asset.id.asc())
    )
    return session.scalars(stmt).first()


def _storage_provider_name(storage: object) -> str:
    return "bunny_storage" if isinstance(storage, BunnyStorage) else "cloudflare_r2"


def _asset_result_base(asset: Asset, product_id: int | None) -> dict[str, object]:
    return {
        "asset_id": asset.id,
        "filename": asset.original_filename or asset.filename,
        "asset_type": asset.asset_type,
        "product_id": product_id,
        "product_title": asset.product.title if asset.product else None,
        "storage_provider": asset.storage_provider,
    }


def _asset_r2_upload_skip_reason(asset: Asset) -> str | None:
    if str(asset.status or "").lower() == "archived":
        return "Archiviertes Asset wird nicht nach R2 hochgeladen."
    if not str(asset.mime_type or "").lower().startswith("image/"):
        return None
    source = f"{asset.source_url or ''} {asset.storage_path or ''} {asset.filename or ''}".lower()
    if "/thumbnail/" in source:
        return "Thumbnail-Bild wird nicht nach R2 hochgeladen."
    if any(token in source for token in ("/skin/", "/frontend/", "/static/", "/theme/", "/template/")):
        return "Layout-/Theme-Bild wird nicht nach R2 hochgeladen."
    if any(token in source for token in ("sprite", "logo", "icon", "placeholder", "blank", "menu")):
        return "Nicht-produktbezogenes Layoutbild wird nicht nach R2 hochgeladen."
    if asset.width is not None and asset.height is not None and (asset.width < 300 or asset.height < 300):
        return f"Bild zu klein für R2-Produktbild ({asset.width}x{asset.height}px)."
    return None


def _asset_type_from_mime(mime_type: str | None) -> str:
    value = str(mime_type or "")
    if value.startswith("image/"):
        return "product_image"
    if value == "application/pdf":
        return "technical_data_sheet"
    return "other"


def build_r2_object_key(
    filename: str,
    *,
    asset_type: str,
    product_id: int | None = None,
    language_code: str | None = None,
    now: datetime | None = None,
    path_prefix: str = "prod/assets",
) -> str:
    current = now or datetime.now(timezone.utc)
    base_prefix = _normalize_object_prefix(path_prefix)
    safe_filename = _safe_filename(filename)
    prefix = f"{uuid4().hex}-{safe_filename}"
    normalized_type = asset_type if asset_type in ASSET_TYPE_FOLDERS else "other"
    folder = ASSET_TYPE_FOLDERS.get(normalized_type, "other")
    if product_id:
        if normalized_type == "safety_data_sheet":
            language_part = slugify(language_code or "unknown", separator="-") or "unknown"
            return f"{base_prefix}/products/{product_id}/sdb/{language_part}/{prefix}"
        return f"{base_prefix}/products/{product_id}/{folder}/{prefix}"
    if normalized_type == "import_file":
        return f"{base_prefix}/imports/{current:%Y}/{current:%m}/{prefix}"
    return f"{base_prefix}/general/{normalized_type}/{current:%Y}/{current:%m}/{prefix}"


def _validate_upload(
    payload: bytes,
    filename: str,
    extension: str,
    *,
    max_upload_size_mb: int | None = None,
    allowed_file_types: str | None = None,
) -> None:
    if not payload:
        raise ValueError("Datei ist leer.")
    max_size_mb = int(max_upload_size_mb or os.getenv("MAX_ASSET_UPLOAD_SIZE_MB") or "50")
    if len(payload) > max_size_mb * 1024 * 1024:
        raise ValueError(f"Datei ist zu gross. Maximal erlaubt: {max_size_mb} MB.")
    allowed_extensions = _allowed_extensions_from_config(allowed_file_types)
    if extension in BLOCKED_ASSET_EXTENSIONS:
        raise ValueError(f"Dateityp {extension} ist nicht erlaubt.")
    if extension not in allowed_extensions:
        raise ValueError(f"Dateityp {extension or '(ohne Endung)'} ist nicht erlaubt.")
    guessed_type = mimetypes.guess_type(filename)[0] or ""
    if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"} and not guessed_type.startswith("image/"):
        raise ValueError("Bilddatei hat keinen gültigen Bild-MIME-Type.")
    if extension == ".pdf" and guessed_type != "application/pdf":
        raise ValueError("PDF-Datei hat keinen gültigen MIME-Type.")


def _safe_filename(filename: str) -> str:
    path = Path(filename or "asset")
    extension = path.suffix.lower()
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", slugify(path.stem or "asset", separator="-")).strip("-")
    return f"{stem or 'asset'}{extension}"


def _normalize_object_prefix(value: str | None) -> str:
    parts = [part for part in str(value or "prod/assets").strip().strip("/").split("/") if part]
    return "/".join(parts) or "prod/assets"


def _allowed_extensions_from_config(value: str | None) -> set[str]:
    if not value:
        return ALLOWED_ASSET_EXTENSIONS
    extensions = set()
    for item in re.split(r"[,;\s]+", value):
        cleaned = item.strip().lower()
        if not cleaned:
            continue
        extensions.add(cleaned if cleaned.startswith(".") else f".{cleaned}")
    return extensions or ALLOWED_ASSET_EXTENSIONS


def _metadata_value(value: str | None) -> str:
    return str(value or "")[:1024]


def _image_size_from_bytes(payload: bytes, mime_type: str) -> tuple[int | None, int | None]:
    if not mime_type.startswith("image/"):
        return None, None
    try:
        with Image.open(BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None, None


def _next_asset_sort_order(session: Session, *, product_id: int | None = None, variant_id: int | None = None) -> int:
    sort_scope_column = Asset.product_id if product_id is not None else Asset.variant_id
    sort_scope_value = product_id if product_id is not None else variant_id
    if sort_scope_value is None:
        return 0
    return (session.scalar(select(func.coalesce(func.max(Asset.sort_order), -1)).where(sort_scope_column == sort_scope_value)) or -1) + 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8192):
            digest.update(chunk)
    return digest.hexdigest()
