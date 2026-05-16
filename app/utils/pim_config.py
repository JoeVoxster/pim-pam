from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(slots=True)
class PimSettings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "postgresql+psycopg://pim:pim@localhost:5432/pimdb"))
    asset_storage_path: str = field(default_factory=lambda: os.getenv("ASSET_STORAGE_PATH", "./data/assets"))
    app_host: str = field(default_factory=lambda: os.getenv("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: int(os.getenv("APP_PORT", "8050")))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"})
    r2_endpoint: str = field(default_factory=lambda: os.getenv("R2_ENDPOINT", "https://c1c33248a1d708c368b3c2c9952d993d.r2.cloudflarestorage.com"))
    r2_bucket: str = field(default_factory=lambda: os.getenv("R2_BUCKET", "voxster-media"))
    r2_region: str = field(default_factory=lambda: os.getenv("R2_REGION", "auto"))
    r2_public_base_url: str = field(default_factory=lambda: os.getenv("R2_PUBLIC_BASE_URL", ""))
    max_asset_upload_size_mb: int = field(default_factory=lambda: int(os.getenv("MAX_ASSET_UPLOAD_SIZE_MB", "50")))

    @property
    def asset_storage_root(self) -> Path:
        return Path(self.asset_storage_path).resolve()


def get_pim_settings() -> PimSettings:
    return PimSettings()
