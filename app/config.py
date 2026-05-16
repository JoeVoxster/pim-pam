from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Settings(BaseModel):
    request_timeout_seconds: int = 30
    browser_timeout_ms: int = 30000
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
    max_images_per_product: int = 20
    max_pdfs_per_product: int = 10
    max_crawl_pages: int = 150
    log_level: str = "INFO"
    headless: bool = True


def settings_with_overrides(base: Settings, overrides: dict[str, Any] | None = None) -> Settings:
    if not overrides:
        return base
    payload = base.model_dump()
    payload.update({key: value for key, value in overrides.items() if value is not None})
    return Settings(**payload)


def load_settings(config_path: str | None = None) -> Settings:
    config_data: dict[str, Any] = {}
    if config_path:
        path = Path(config_path)
    else:
        path = Path(".env")

    if path.exists():
        if path.suffix in {".yaml", ".yml"}:
            config_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                config_data[key.strip()] = value.strip()

    normalized = {
        "request_timeout_seconds": int(config_data.get("REQUEST_TIMEOUT_SECONDS", config_data.get("request_timeout_seconds", 30))),
        "browser_timeout_ms": int(config_data.get("BROWSER_TIMEOUT_MS", config_data.get("browser_timeout_ms", 30000))),
        "user_agent": config_data.get("USER_AGENT", config_data.get("user_agent")),
        "max_images_per_product": int(config_data.get("MAX_IMAGES_PER_PRODUCT", config_data.get("max_images_per_product", 20))),
        "max_pdfs_per_product": int(config_data.get("MAX_PDFS_PER_PRODUCT", config_data.get("max_pdfs_per_product", 10))),
        "max_crawl_pages": int(config_data.get("MAX_CRAWL_PAGES", config_data.get("max_crawl_pages", 150))),
        "log_level": config_data.get("LOG_LEVEL", config_data.get("log_level", "INFO")),
        "headless": str(config_data.get("HEADLESS", config_data.get("headless", True))).lower() not in {"0", "false", "no"},
    }
    return Settings(**{k: v for k, v in normalized.items() if v is not None})
