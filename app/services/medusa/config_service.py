from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    MedusaConnectionConfig,
    MedusaLocaleMapping,
    MedusaPriceListMapping,
    MedusaSyncRun,
    MedusaSyncRunItem,
)

DEFAULT_MEDUSA_NAME = "default"
DEFAULT_ADMIN_PATH = "/admin"
DEFAULT_LOCALES = ["de-CH", "fr-CH", "it-CH", "en"]


def get_or_create_medusa_connection(session: Session, name: str = DEFAULT_MEDUSA_NAME) -> MedusaConnectionConfig:
    if name == DEFAULT_MEDUSA_NAME:
        active_config = session.scalar(
            select(MedusaConnectionConfig)
            .where(MedusaConnectionConfig.enabled.is_(True))
            .order_by(MedusaConnectionConfig.updated_at.desc(), MedusaConnectionConfig.id.asc())
            .limit(1)
        )
        if active_config is not None:
            return active_config
    config = session.scalar(select(MedusaConnectionConfig).where(MedusaConnectionConfig.name == name))
    if config is None:
        config = MedusaConnectionConfig(
            name=name,
            enabled=False,
            base_url=os.getenv("MEDUSA_BASE_URL") or "http://localhost:9000",
            admin_path=os.getenv("MEDUSA_ADMIN_PATH") or DEFAULT_ADMIN_PATH,
            auth_type="api_token",
            api_token_secret=os.getenv("MEDUSA_ADMIN_API_TOKEN") or None,
            default_currency_code=(os.getenv("MEDUSA_DEFAULT_CURRENCY") or "CHF").upper(),
            default_locale=os.getenv("MEDUSA_DEFAULT_LOCALE") or "de-CH",
            enabled_locales=DEFAULT_LOCALES,
            product_status_default=os.getenv("MEDUSA_PRODUCT_STATUS_DEFAULT") or "draft",
        )
        session.add(config)
        session.flush()
        _ensure_default_locale_mappings(session, config)
        session.flush()
    return config


def save_medusa_connection(session: Session, payload: dict[str, Any], name: str = DEFAULT_MEDUSA_NAME) -> MedusaConnectionConfig:
    config = get_or_create_medusa_connection(session, name=name)
    config.enabled = bool(payload.get("enabled"))
    config.name = str(payload.get("name") or name).strip() or DEFAULT_MEDUSA_NAME
    config.base_url = _clean(payload.get("base_url")).rstrip("/") or "http://localhost:9000"
    config.admin_path = _normalize_admin_path(_clean(payload.get("admin_path")) or DEFAULT_ADMIN_PATH)
    config.auth_type = _clean(payload.get("auth_type")) or "api_token"
    token = _clean(payload.get("api_token"))
    if token:
        _validate_secret_token(token, "Medusa API Token")
        config.api_token_secret = token
    jwt_password = _clean(payload.get("jwt_password"))
    if jwt_password:
        _validate_secret_token(jwt_password, "Medusa JWT Passwort")
        config.jwt_password_secret = jwt_password
    config.jwt_email = _clean(payload.get("jwt_email")) or None
    config.timeout_seconds = int(payload.get("timeout_seconds") or 30)
    config.verify_ssl = bool(payload.get("verify_ssl", True))
    config.retry_count = int(payload.get("retry_count") or 2)
    config.retry_backoff_seconds = int(payload.get("retry_backoff_seconds") or 2)
    config.batch_size = int(payload.get("batch_size") or 20)
    config.dry_run_default = bool(payload.get("dry_run_default", True))
    config.default_region_id = _clean(payload.get("default_region_id")) or None
    config.default_sales_channel_id = _clean(payload.get("default_sales_channel_id")) or None
    config.default_currency_code = (_clean(payload.get("default_currency_code")) or "CHF").upper()
    config.default_locale = _clean(payload.get("default_locale")) or "de-CH"
    config.enabled_locales = _parse_locales(payload.get("enabled_locales")) or DEFAULT_LOCALES
    config.public_asset_base_url = _clean(payload.get("public_asset_base_url")).rstrip("/") or None
    config.product_status_default = _clean(payload.get("product_status_default")) or "draft"
    config.product_match_policy = _clean(payload.get("product_match_policy")) or "id_handle_metadata"
    config.variant_match_policy = _clean(payload.get("variant_match_policy")) or "id_sku_metadata"
    config.conflict_policy = _clean(payload.get("conflict_policy")) or "skip_conflicts"
    config.pricing_strategy = _clean(payload.get("pricing_strategy")) or "default_and_price_lists"
    config.translation_strategy = _clean(payload.get("translation_strategy")) or "translation_module"
    for flag in (
        "export_products",
        "export_variants",
        "export_options",
        "export_categories",
        "export_collections",
        "export_tags",
        "export_types",
        "export_images",
        "export_seo",
        "export_metadata",
        "export_translations",
        "export_default_prices",
        "export_price_lists",
        "export_tiered_prices",
        "export_inventory",
        "pull_ids_after_export",
        "repair_mapping_before_export",
    ):
        if flag in payload:
            setattr(config, flag, bool(payload[flag]))
    _ensure_default_locale_mappings(session, config)
    session.flush()
    return config


def serialize_medusa_connection(config: MedusaConnectionConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "name": config.name,
        "enabled": bool(config.enabled),
        "base_url": config.base_url or "",
        "admin_path": config.admin_path or DEFAULT_ADMIN_PATH,
        "effective_admin_url": effective_admin_url(config),
        "auth_type": config.auth_type or "api_token",
        "api_token_configured": bool((config.api_token_secret or os.getenv("MEDUSA_ADMIN_API_TOKEN") or "").strip()),
        "jwt_email": config.jwt_email or "",
        "jwt_password_configured": bool((config.jwt_password_secret or "").strip()),
        "timeout_seconds": config.timeout_seconds,
        "verify_ssl": bool(config.verify_ssl),
        "retry_count": config.retry_count,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "batch_size": config.batch_size,
        "dry_run_default": bool(config.dry_run_default),
        "default_region_id": config.default_region_id or "",
        "default_sales_channel_id": config.default_sales_channel_id or "",
        "default_currency_code": config.default_currency_code or "CHF",
        "default_locale": config.default_locale or "de-CH",
        "enabled_locales": ", ".join(config.enabled_locales or DEFAULT_LOCALES),
        "public_asset_base_url": config.public_asset_base_url or "",
        "product_status_default": config.product_status_default or "draft",
        "product_match_policy": config.product_match_policy,
        "variant_match_policy": config.variant_match_policy,
        "conflict_policy": config.conflict_policy,
        "pricing_strategy": config.pricing_strategy,
        "translation_strategy": config.translation_strategy,
        "last_test_status": config.last_test_status or "noch nicht getestet",
        "last_test_at": config.last_test_at.isoformat() if config.last_test_at else "",
        "last_error_message": config.last_error_message or "",
        **{flag: bool(getattr(config, flag)) for flag in _EXPORT_FLAGS},
    }


def effective_admin_url(config: MedusaConnectionConfig) -> str:
    return f"{(config.base_url or '').rstrip('/')}{_normalize_admin_path(config.admin_path)}"


def list_medusa_runs(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.scalars(select(MedusaSyncRun).order_by(MedusaSyncRun.id.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "connection_id": row.connection_id,
            "mode": row.mode,
            "status": row.status,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "summary": row.summary,
        }
        for row in rows
    ]


def list_medusa_run_items(session: Session, run_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    stmt = select(MedusaSyncRunItem).order_by(MedusaSyncRunItem.id.desc()).limit(limit)
    if run_id:
        stmt = select(MedusaSyncRunItem).where(MedusaSyncRunItem.run_id == run_id).order_by(MedusaSyncRunItem.id.desc()).limit(limit)
    rows = session.scalars(stmt).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "entity_type": row.entity_type,
            "local_entity_id": row.local_entity_id,
            "medusa_id": row.medusa_id,
            "locale_code": row.locale_code,
            "price_list_code": row.price_list_code,
            "currency_code": row.currency_code,
            "action": row.action,
            "status": row.status,
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def mark_connection_test_result(config: MedusaConnectionConfig, *, ok: bool, error: str | None = None) -> None:
    config.last_test_status = "ok" if ok else "error"
    config.last_test_at = datetime.now(timezone.utc)
    config.last_error_message = None if ok else (error or "Unbekannter Fehler")


def _ensure_default_locale_mappings(session: Session, config: MedusaConnectionConfig) -> None:
    existing = {
        row.local_locale: row
        for row in session.scalars(select(MedusaLocaleMapping).where(MedusaLocaleMapping.connection_id == config.id))
    }
    locales = config.enabled_locales or DEFAULT_LOCALES
    for locale in locales:
        if locale not in existing:
            session.add(
                MedusaLocaleMapping(
                    connection_id=config.id,
                    local_locale=locale,
                    medusa_locale=locale,
                    enabled=True,
                    is_default=locale == (config.default_locale or "de-CH"),
                )
            )


def _clean(value: object) -> str:
    return str(value or "").strip()


def _normalize_admin_path(value: str | None) -> str:
    cleaned = (value or DEFAULT_ADMIN_PATH).strip()
    return "/" + cleaned.strip("/")


def _parse_locales(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value or "").replace("\n", ",").split(",") if part.strip()]


def _validate_secret_token(value: str, label: str) -> None:
    if not value.isascii():
        raise ValueError(f"{label} enthält ungültige Sonderzeichen. Bitte Token direkt aus Medusa neu kopieren, nicht aus der GUI oder formatiertem Text.")
    if any(char.isspace() for char in value):
        raise ValueError(f"{label} darf keine Leerzeichen oder Zeilenumbrüche enthalten.")
    if len(value) < 16:
        raise ValueError(f"{label} wirkt zu kurz.")


_EXPORT_FLAGS = (
    "export_products",
    "export_variants",
    "export_options",
    "export_categories",
    "export_collections",
    "export_tags",
    "export_types",
    "export_images",
    "export_seo",
    "export_metadata",
    "export_translations",
    "export_default_prices",
    "export_price_lists",
    "export_tiered_prices",
    "export_inventory",
    "pull_ids_after_export",
    "repair_mapping_before_export",
)
