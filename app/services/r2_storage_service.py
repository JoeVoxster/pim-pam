from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import boto3
import requests
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


class R2ConfigurationError(RuntimeError):
    pass


class R2UploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class R2Settings:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str | None = None


def get_r2_settings() -> R2Settings:
    endpoint = (os.getenv("R2_ENDPOINT") or default_r2_endpoint()).strip().rstrip("/")
    bucket = (os.getenv("R2_BUCKET") or "voxster-media").strip()
    region = (os.getenv("R2_REGION") or "auto").strip()
    access_key_id = (os.getenv("R2_ACCESS_KEY_ID") or "").strip()
    secret_access_key = (os.getenv("R2_SECRET_ACCESS_KEY") or "").strip()
    public_base_url = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/") or None
    if not endpoint:
        raise R2ConfigurationError("R2_ENDPOINT ist nicht konfiguriert.")
    if not bucket:
        raise R2ConfigurationError("R2_BUCKET ist nicht konfiguriert.")
    if not access_key_id or not secret_access_key:
        raise R2ConfigurationError("R2_ACCESS_KEY_ID und R2_SECRET_ACCESS_KEY müssen als ENV Variablen gesetzt sein.")
    return R2Settings(
        endpoint=endpoint,
        bucket=bucket,
        region=region,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        public_base_url=public_base_url,
    )


class CloudflareR2Storage:
    def __init__(self, settings: R2Settings | None = None) -> None:
        self.settings = settings or get_r2_settings()
        self.client = boto3.client(
            "s3",
            endpoint_url=self.settings.endpoint,
            region_name=self.settings.region,
            aws_access_key_id=self.settings.access_key_id,
            aws_secret_access_key=self.settings.secret_access_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def upload_fileobj(self, fileobj: BinaryIO, object_key: str, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
        try:
            self.client.upload_fileobj(
                fileobj,
                self.settings.bucket,
                object_key,
                ExtraArgs={
                    "ContentType": content_type,
                    "Metadata": metadata or {},
                },
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2UploadError(f"R2 Upload fehlgeschlagen: {exc}") from exc

    def generate_presigned_download_url(self, object_key: str, *, expires_in: int = 3600) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.settings.bucket, "Key": object_key},
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2UploadError(f"R2 Download-Link konnte nicht erzeugt werden: {exc}") from exc

    def delete_object(self, object_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.settings.bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise R2UploadError(f"R2 Objekt konnte nicht gelöscht werden: {exc}") from exc

    def list_objects(self, *, max_keys: int = 1) -> None:
        try:
            self.client.list_objects_v2(Bucket=self.settings.bucket, MaxKeys=max_keys)
        except (BotoCoreError, ClientError) as exc:
            raise R2UploadError(f"R2 Listing fehlgeschlagen: {exc}") from exc

    def public_url(self, object_key: str) -> str | None:
        if not self.settings.public_base_url:
            return None
        return f"{self.settings.public_base_url}/{object_key.lstrip('/')}"


class BunnyStorage:
    def __init__(self, settings: R2Settings) -> None:
        self.settings = settings
        self.base_url = _bunny_base_url(settings.endpoint, settings.bucket)

    def upload_fileobj(self, fileobj: BinaryIO, object_key: str, *, content_type: str, metadata: dict[str, str] | None = None) -> None:
        response = requests.put(
            f"{self.base_url}/{object_key.lstrip('/')}",
            data=fileobj,
            headers={
                "AccessKey": self.settings.secret_access_key,
                "Content-Type": content_type,
            },
            timeout=60,
        )
        if response.status_code >= 400:
            raise R2UploadError(f"Bunny Upload fehlgeschlagen ({response.status_code}): {response.text[:500]}")

    def generate_presigned_download_url(self, object_key: str, *, expires_in: int = 3600) -> str:
        public_url = self.public_url(object_key)
        if not public_url:
            raise R2UploadError("Für Bunny ist keine Public Base URL konfiguriert.")
        return public_url

    def delete_object(self, object_key: str) -> None:
        response = requests.delete(
            f"{self.base_url}/{object_key.lstrip('/')}",
            headers={"AccessKey": self.settings.secret_access_key},
            timeout=30,
        )
        if response.status_code >= 400 and response.status_code != 404:
            raise R2UploadError(f"Bunny Objekt konnte nicht gelöscht werden ({response.status_code}): {response.text[:500]}")

    def list_objects(self, *, max_keys: int = 1) -> None:
        response = requests.get(
            f"{self.base_url}/",
            headers={"AccessKey": self.settings.secret_access_key},
            timeout=30,
        )
        if response.status_code >= 400:
            raise R2UploadError(f"Bunny Listing fehlgeschlagen ({response.status_code}): {response.text[:500]}")

    def public_url(self, object_key: str) -> str | None:
        if not self.settings.public_base_url:
            return None
        return f"{self.settings.public_base_url.rstrip('/')}/{object_key.lstrip('/')}"


def safe_r2_public_url(object_key: str | None) -> str | None:
    public_base_url = (os.getenv("R2_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not public_base_url or not object_key:
        return None
    return f"{public_base_url}/{object_key.lstrip('/')}"


def default_r2_bucket() -> str:
    return (os.getenv("R2_BUCKET") or "voxster-media").strip()


def default_r2_endpoint() -> str:
    return (os.getenv("R2_ENDPOINT") or "https://c1c33248a1d708c368b3c2c9952d993d.r2.cloudflarestorage.com").strip()


def object_key_to_storage_path(object_key: str, bucket: str | None = None) -> str:
    return f"r2://{bucket or default_r2_bucket()}/{object_key}"


def remote_object_key_to_storage_path(object_key: str, bucket: str | None = None, provider: str = "cloudflare_r2") -> str:
    scheme = "bunny" if provider == "bunny_storage" else "r2"
    return f"{scheme}://{bucket or default_r2_bucket()}/{object_key}"


def object_key_from_storage_path(storage_path: str | None) -> str | None:
    value = str(storage_path or "")
    if not value.startswith(("r2://", "bunny://")):
        return None
    value = value.split("://", 1)[1]
    parts = value.split("/", 1)
    return parts[1] if len(parts) == 2 else None


def _bunny_base_url(endpoint: str, bucket: str) -> str:
    clean_endpoint = endpoint.strip().rstrip("/")
    clean_bucket = bucket.strip().strip("/")
    if clean_endpoint.endswith(f"/{clean_bucket}"):
        return clean_endpoint
    return f"{clean_endpoint}/{clean_bucket}"
