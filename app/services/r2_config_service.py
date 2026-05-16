from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import R2StorageConfig
from app.services.r2_storage_service import BunnyStorage, CloudflareR2Storage, R2ConfigurationError, R2Settings


DEFAULT_R2_ENDPOINT = "https://c1c33248a1d708c368b3c2c9952d993d.r2.cloudflarestorage.com"
DEFAULT_R2_BUCKET = "voxster-media"
DEFAULT_R2_REGION = "auto"
DEFAULT_R2_PROVIDER = "cloudflare_r2"
BUNNY_PROVIDER = "bunny_storage"
DEFAULT_R2_PATH_PREFIX = "prod/assets"
DEFAULT_ALLOWED_FILE_TYPES = ".jpg,.jpeg,.png,.webp,.gif,.pdf,.csv,.xlsx,.xls,.txt,.json,.xml"


def get_or_create_r2_config(session: Session) -> R2StorageConfig:
    config = session.scalar(select(R2StorageConfig).order_by(R2StorageConfig.id.asc()).limit(1))
    if config is not None:
        return config
    config = R2StorageConfig(
        enabled=False,
        provider=DEFAULT_R2_PROVIDER,
        endpoint=os.getenv("R2_ENDPOINT") or DEFAULT_R2_ENDPOINT,
        bucket=os.getenv("R2_BUCKET") or DEFAULT_R2_BUCKET,
        region=os.getenv("R2_REGION") or DEFAULT_R2_REGION,
        public_base_url=os.getenv("R2_PUBLIC_BASE_URL") or None,
        path_prefix=DEFAULT_R2_PATH_PREFIX,
        max_upload_size_mb=int(os.getenv("MAX_ASSET_UPLOAD_SIZE_MB") or "50"),
        allowed_file_types=DEFAULT_ALLOWED_FILE_TYPES,
    )
    session.add(config)
    session.flush()
    return config


def serialize_r2_config(config: R2StorageConfig) -> dict[str, object]:
    return {
        "id": config.id,
        "enabled": bool(config.enabled),
        "provider": config.provider or DEFAULT_R2_PROVIDER,
        "endpoint": config.endpoint or DEFAULT_R2_ENDPOINT,
        "bucket": config.bucket or DEFAULT_R2_BUCKET,
        "region": config.region or DEFAULT_R2_REGION,
        "access_key_id_masked": mask_secret(config.access_key_id),
        "access_key_configured": bool((config.access_key_id or os.getenv("R2_ACCESS_KEY_ID") or "").strip()),
        "secret_configured": bool((config.secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()),
        "public_base_url": config.public_base_url or "",
        "path_prefix": config.path_prefix or DEFAULT_R2_PATH_PREFIX,
        "storage_class": config.storage_class or "",
        "max_upload_size_mb": config.max_upload_size_mb or 50,
        "allowed_file_types": config.allowed_file_types or DEFAULT_ALLOWED_FILE_TYPES,
        "notes": config.notes or "",
        "last_test_status": config.last_test_status or "",
        "last_test_at": config.last_test_at.isoformat() if config.last_test_at else "",
        "last_error_message": config.last_error_message or "",
    }


def save_r2_config(session: Session, payload: dict[str, object]) -> R2StorageConfig:
    config = get_or_create_r2_config(session)
    enabled = bool(payload.get("enabled"))
    provider = _clean(payload.get("provider")) or DEFAULT_R2_PROVIDER
    if provider not in {DEFAULT_R2_PROVIDER, BUNNY_PROVIDER}:
        raise ValueError("Storage Provider muss cloudflare_r2 oder bunny_storage sein.")
    endpoint = _clean(payload.get("endpoint")).rstrip("/") or DEFAULT_R2_ENDPOINT
    bucket = _clean(payload.get("bucket")) or DEFAULT_R2_BUCKET
    region = _clean(payload.get("region")) or DEFAULT_R2_REGION
    public_base_url = _clean(payload.get("public_base_url")).rstrip("/") or None
    max_upload_size_mb = int(payload.get("max_upload_size_mb") or 50)
    allowed_file_types = _clean(payload.get("allowed_file_types")) or DEFAULT_ALLOWED_FILE_TYPES
    path_prefix = _normalize_prefix(_clean(payload.get("path_prefix")) or DEFAULT_R2_PATH_PREFIX)

    if endpoint and not endpoint.startswith("https://"):
        raise ValueError("Endpoint muss mit https:// beginnen.")
    if " " in bucket:
        raise ValueError("Bucket darf keine Leerzeichen enthalten.")
    if max_upload_size_mb <= 0:
        raise ValueError("Upload max. Dateigrösse muss grösser als 0 sein.")
    if enabled and not bucket:
        raise ValueError("Bucket ist Pflicht, wenn R2 aktiviert ist.")

    config.enabled = enabled
    config.provider = provider
    config.endpoint = endpoint
    config.bucket = bucket
    config.region = region
    config.public_base_url = public_base_url
    config.path_prefix = path_prefix
    config.storage_class = _clean(payload.get("storage_class")) or None
    config.max_upload_size_mb = max_upload_size_mb
    config.allowed_file_types = allowed_file_types
    config.notes = _clean(payload.get("notes")) or None

    access_key_id = _clean(payload.get("access_key_id"))
    if access_key_id:
        config.access_key_id = access_key_id
    secret_access_key = _clean(payload.get("secret_access_key"))
    if secret_access_key:
        config.secret_access_key = secret_access_key

    session.flush()
    return config


def effective_r2_settings(session: Session | None = None) -> R2Settings:
    if session is not None:
        config = session.scalar(select(R2StorageConfig).order_by(R2StorageConfig.id.asc()).limit(1))
        if config is not None and config.enabled:
            endpoint = (config.endpoint or os.getenv("R2_ENDPOINT") or DEFAULT_R2_ENDPOINT).strip().rstrip("/")
            bucket = (config.bucket or os.getenv("R2_BUCKET") or DEFAULT_R2_BUCKET).strip()
            region = (config.region or os.getenv("R2_REGION") or DEFAULT_R2_REGION).strip()
            access_key_id = (config.access_key_id or os.getenv("R2_ACCESS_KEY_ID") or "").strip()
            secret_access_key = (config.secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
            public_base_url = (config.public_base_url or os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/") or None
            provider = (config.provider or DEFAULT_R2_PROVIDER).strip()
            _validate_effective(endpoint, bucket, access_key_id, secret_access_key, provider=provider)
            return R2Settings(
                endpoint=endpoint,
                bucket=bucket,
                region=region,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                public_base_url=public_base_url,
            )
    endpoint = (os.getenv("R2_ENDPOINT") or DEFAULT_R2_ENDPOINT).strip().rstrip("/")
    bucket = (os.getenv("R2_BUCKET") or DEFAULT_R2_BUCKET).strip()
    region = (os.getenv("R2_REGION") or DEFAULT_R2_REGION).strip()
    access_key_id = (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    public_base_url = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/") or None
    _validate_effective(endpoint, bucket, access_key_id, secret_access_key, provider=DEFAULT_R2_PROVIDER)
    return R2Settings(endpoint, bucket, region, access_key_id, secret_access_key, public_base_url)


def build_r2_storage(session: Session | None = None):
    provider = DEFAULT_R2_PROVIDER
    if session is not None:
        config = session.scalar(select(R2StorageConfig).order_by(R2StorageConfig.id.asc()).limit(1))
        if config is not None and config.enabled:
            provider = config.provider or DEFAULT_R2_PROVIDER
    settings = effective_r2_settings(session)
    if provider == BUNNY_PROVIDER:
        return BunnyStorage(settings)
    return CloudflareR2Storage(settings)


def get_r2_upload_options(session: Session) -> dict[str, object]:
    config = session.scalar(select(R2StorageConfig).order_by(R2StorageConfig.id.asc()).limit(1))
    if config is not None and config.enabled:
        return {
            "max_upload_size_mb": config.max_upload_size_mb or 50,
            "path_prefix": config.path_prefix or DEFAULT_R2_PATH_PREFIX,
            "allowed_file_types": config.allowed_file_types or DEFAULT_ALLOWED_FILE_TYPES,
        }
    return {
        "max_upload_size_mb": int(os.getenv("MAX_ASSET_UPLOAD_SIZE_MB") or "50"),
        "path_prefix": DEFAULT_R2_PATH_PREFIX,
        "allowed_file_types": DEFAULT_ALLOWED_FILE_TYPES,
    }


def get_r2_public_base_url(session: Session | None = None) -> str | None:
    if session is not None:
        config = session.scalar(select(R2StorageConfig).order_by(R2StorageConfig.id.asc()).limit(1))
        if config is not None and config.public_base_url:
            return config.public_base_url.strip().rstrip("/") or None
    return (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/") or None


def test_r2_connection(session: Session, *, test_upload: bool = True) -> dict[str, object]:
    config = get_or_create_r2_config(session)
    try:
        storage = build_r2_storage(session)
        storage.list_objects(max_keys=1)
        uploaded = False
        if test_upload:
            prefix = _normalize_prefix(config.path_prefix or DEFAULT_R2_PATH_PREFIX)
            object_key = f"{prefix}/diagnostics/r2-test-{uuid4().hex}.txt"
            storage.upload_fileobj(
                BytesIO(b"pim-pam-r2-connection-test"),
                object_key,
                content_type="text/plain",
                metadata={"purpose": "connection-test"},
            )
            uploaded = True
            storage.delete_object(object_key)
        _record_test(config, "ok", None)
        session.flush()
        return {
            "status": "ok",
            "message": f"Verbindung zu {config.provider or DEFAULT_R2_PROVIDER} erfolgreich.",
            "bucket": storage.settings.bucket,
            "test_upload": uploaded,
        }
    except R2ConfigurationError as exc:
        message = _sanitize_error(str(exc))
    except (BotoCoreError, ClientError, Exception) as exc:
        message = _sanitize_error(str(exc))
    _record_test(config, "error", message)
    session.flush()
    return {"status": "error", "message": _friendly_error(message)}


def mask_secret(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return f"{raw[:2]}***"
    return f"{raw[:4]}***{raw[-4:]}"


def _record_test(config: R2StorageConfig, status: str, error: str | None) -> None:
    config.last_test_status = status
    config.last_test_at = datetime.now(timezone.utc)
    config.last_error_message = error


def _validate_effective(endpoint: str, bucket: str, access_key_id: str, secret_access_key: str, *, provider: str) -> None:
    if not endpoint:
        raise R2ConfigurationError("R2_ENDPOINT ist nicht konfiguriert.")
    if not endpoint.startswith("https://"):
        raise R2ConfigurationError("R2_ENDPOINT muss mit https:// beginnen.")
    if not bucket:
        raise R2ConfigurationError("R2_BUCKET ist nicht konfiguriert.")
    if provider == BUNNY_PROVIDER:
        if not secret_access_key:
            raise R2ConfigurationError(
                "Bunny Storage ist nicht vollständig konfiguriert. Secret Access Key muss das Storage-Zone-Passwort sein."
            )
        return
    if not access_key_id or not secret_access_key:
        raise R2ConfigurationError(
            "Cloudflare R2 ist nicht vollständig konfiguriert. Bitte unter Assets -> R2 Speicher -> Konfiguration prüfen."
        )


def _friendly_error(message: str) -> str:
    lower = message.lower()
    if "accessdenied" in lower or "forbidden" in lower or "invalidaccesskeyid" in lower:
        return "Zugriff verweigert oder Credentials falsch."
    if "nosuchbucket" in lower or "not found" in lower:
        return "Bucket nicht gefunden."
    if "endpoint" in lower:
        return "Endpoint ist ungültig oder nicht erreichbar."
    return message


def _sanitize_error(message: str) -> str:
    sanitized = message
    for secret_name in ("R2_SECRET_ACCESS_KEY", "R2_ACCESS_KEY_ID"):
        secret = os.getenv(secret_name) or ""
        if secret:
            sanitized = sanitized.replace(secret, "***")
    return sanitized[:1000]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_prefix(value: str) -> str:
    return "/".join(part for part in value.strip().strip("/").split("/") if part) or DEFAULT_R2_PATH_PREFIX
