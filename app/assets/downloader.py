from __future__ import annotations

from pathlib import Path
from shutil import copy2
from urllib.parse import urlparse

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from app.assets.naming import build_asset_filename, build_descriptive_image_label, build_descriptive_pdf_label, guess_pdf_label
from app.models import AssetReference, DownloadedAsset
from app.pdf.parser import extract_pdf_text


class AssetDownloader:
    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self._downloaded_urls: set[str] = set()

    def download_images(
        self,
        supplier_sku: str,
        references: list[AssetReference],
        destination_dir: Path,
        product_name: str | None,
        product_title: str | None,
        description: str | None,
    ) -> list[DownloadedAsset]:
        assets: list[DownloadedAsset] = []
        for index, reference in enumerate(self._unique_references(references), start=1):
            url = reference.url
            extension = _infer_extension(url, fallback=".jpg")
            descriptive_label = build_descriptive_image_label(
                product_name,
                product_title,
                description,
                url,
                reference.context_text or reference.label,
                index,
            )
            file_name = build_asset_filename(
                supplier_sku=supplier_sku,
                asset_type="image",
                index=index,
                extension=extension,
                descriptive_label=descriptive_label,
            )
            target = destination_dir / file_name
            self._download_to_path(url, target)
            assets.append(
                DownloadedAsset(
                    supplier_sku=supplier_sku,
                    asset_type="image",
                    role=reference.role,
                    source_url=url,
                    page_url=reference.page_url,
                    local_path=str(target),
                    file_name=file_name,
                    label=descriptive_label,
                    context_text=reference.context_text,
                    product_name=product_name,
                    product_title=product_title,
                )
            )
        return assets

    def download_pdfs(
        self,
        supplier_sku: str,
        references: list[AssetReference],
        destination_dir: Path,
        product_name: str | None,
        product_title: str | None,
    ) -> list[DownloadedAsset]:
        assets: list[DownloadedAsset] = []
        for index, reference in enumerate(self._unique_references(references), start=1):
            url = reference.url
            label = reference.label or reference.role or guess_pdf_label(url)
            descriptive_label = build_descriptive_pdf_label(
                product_name,
                product_title,
                label,
                url,
                reference.context_text,
            )
            file_name = build_asset_filename(
                supplier_sku=supplier_sku,
                asset_type="pdf",
                index=index,
                extension=".pdf",
                descriptive_label=descriptive_label or label,
            )
            target = destination_dir / file_name
            self._download_to_path(url, target)
            assets.append(
                DownloadedAsset(
                    supplier_sku=supplier_sku,
                    asset_type="pdf",
                    role=reference.role or guess_pdf_label(url),
                    source_url=url,
                    page_url=reference.page_url,
                    local_path=str(target),
                    file_name=file_name,
                    label=label,
                    context_text=reference.context_text,
                    product_name=product_name,
                    product_title=product_title,
                    extracted_text=extract_pdf_text(target),
                )
            )
        return assets

    def import_local_pdfs(self, supplier_sku: str, paths: list[str], destination_dir: Path) -> list[DownloadedAsset]:
        assets: list[DownloadedAsset] = []
        for index, path_string in enumerate(paths, start=1):
            source = Path(path_string)
            if not source.exists() or not source.is_file():
                raise FileNotFoundError(f"Local PDF not found: {source}")
            label = guess_pdf_label(source.name)
            file_name = build_asset_filename(
                supplier_sku=supplier_sku,
                asset_type="pdf",
                index=index,
                extension=".pdf",
                descriptive_label=label,
            )
            target = destination_dir / file_name
            copy2(source, target)
            assets.append(
                DownloadedAsset(
                    supplier_sku=supplier_sku,
                    asset_type="pdf",
                    role=guess_pdf_label(source.name),
                    source_url=str(source),
                    local_path=str(target),
                    file_name=file_name,
                    label=label,
                    extracted_text=extract_pdf_text(target),
                )
            )
        return assets

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3), reraise=True)
    def _download_to_path(self, url: str, target: Path) -> None:
        if url in self._downloaded_urls and target.exists():
            return
        response = requests.get(url, timeout=self.timeout_seconds, stream=True)
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
        self._downloaded_urls.add(url)

    @staticmethod
    def _unique_references(references: list[AssetReference]) -> list[AssetReference]:
        seen: set[tuple[str, str | None]] = set()
        output: list[AssetReference] = []
        for reference in references:
            key = (reference.url, reference.role)
            if key not in seen:
                seen.add(key)
                output.append(reference)
        return output


def _infer_extension(url: str, fallback: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".pdf"}:
        return suffix
    return fallback
