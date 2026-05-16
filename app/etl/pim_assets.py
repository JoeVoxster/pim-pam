from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import mimetypes

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets.downloader import AssetDownloader
from app.db.models import Asset, Product, ProductVariant
from app.models import AssetReference, ProductOutputRow
from shutil import copy2

from app.services.asset_service import create_asset_record


@dataclass
class AssetCandidate:
    asset_type: str
    role: str | None
    path: str | None
    url: str | None


def sync_product_assets(
    session: Session,
    product: ProductOutputRow,
    db_product: Product,
    db_variant: ProductVariant | None,
    storage_root: Path,
    imported_asset_keys: set[tuple[int, str]] | None = None,
    timeout_seconds: int = 30,
) -> int:
    candidates = asset_candidates(product)
    if not candidates:
        return 0

    target_dir = storage_root / db_product.handle
    target_dir.mkdir(parents=True, exist_ok=True)
    attach_assets_to_variant = not bool(product.variant_option_1_name and product.variant_option_1_value)
    variant_id = db_variant.id if db_variant and attach_assets_to_variant else None
    downloader = AssetDownloader(timeout_seconds=timeout_seconds)
    created = 0

    existing_assets = session.scalars(
        select(Asset).where(Asset.product_id == db_product.id)
    ).all()
    existing_by_source_url = {asset.source_url: asset for asset in existing_assets if asset.source_url}
    existing_by_filename = {asset.filename: asset for asset in existing_assets}

    for index, candidate in enumerate(candidates, start=1):
        unique_value = candidate.path or candidate.url or f"{candidate.asset_type}:{index}"
        asset_key = (db_product.id, unique_value)
        if imported_asset_keys is not None and asset_key in imported_asset_keys:
            continue

        preferred_name = _preferred_filename(candidate)
        existing = None
        if candidate.url and candidate.url in existing_by_source_url:
            existing = existing_by_source_url[candidate.url]
        elif preferred_name and preferred_name in existing_by_filename:
            existing = existing_by_filename[preferred_name]

        if existing and Path(existing.storage_path).exists():
            if imported_asset_keys is not None:
                imported_asset_keys.add(asset_key)
            continue

        target_path = _materialize_candidate(
            candidate=candidate,
            product=product,
            db_product=db_product,
            target_dir=target_dir,
            downloader=downloader,
            index=index,
        )
        if target_path is None or not target_path.exists():
            continue

        if existing is not None:
            _update_asset_record(
                existing,
                target_path=target_path,
                product_id=db_product.id,
                variant_id=variant_id,
                alt_text=db_product.title,
                source_url=candidate.url,
            )
            created_asset = existing
        else:
            created_asset = create_asset_record(
                session,
                target_path,
                product_id=db_product.id,
                variant_id=variant_id,
                alt_text=db_product.title,
                source_url=candidate.url,
            )
        existing_by_filename[created_asset.filename] = created_asset
        if candidate.url:
            existing_by_source_url[candidate.url] = created_asset
        if imported_asset_keys is not None:
            imported_asset_keys.add(asset_key)
        created += 1

    return created


def asset_candidates(product: ProductOutputRow) -> list[AssetCandidate]:
    image_paths = _split_pipe_values(product.image_paths)
    image_urls = _split_pipe_values(product.image_urls)
    pdf_paths = _split_pipe_values(product.pdf_paths)
    pdf_urls = _split_pipe_values(product.pdf_urls)
    datasheet_urls = set(_split_pipe_values(product.datasheet_urls))
    sds_urls = set(_split_pipe_values(product.sds_urls))

    candidates: list[AssetCandidate] = []
    for index in range(max(len(image_paths), len(image_urls))):
        candidates.append(
            AssetCandidate(
                asset_type="image",
                role=None,
                path=image_paths[index] if index < len(image_paths) else None,
                url=image_urls[index] if index < len(image_urls) else None,
            )
        )

    for index in range(max(len(pdf_paths), len(pdf_urls))):
        url = pdf_urls[index] if index < len(pdf_urls) else None
        role = None
        if url in datasheet_urls:
            role = "datasheet"
        elif url in sds_urls:
            role = "sds"
        candidates.append(
            AssetCandidate(
                asset_type="pdf",
                role=role,
                path=pdf_paths[index] if index < len(pdf_paths) else None,
                url=url,
            )
        )

    deduped: list[AssetCandidate] = []
    seen: set[tuple[str | None, str | None, str]] = set()
    for candidate in candidates:
        key = (candidate.path, candidate.url, candidate.asset_type)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _materialize_candidate(
    candidate: AssetCandidate,
    product: ProductOutputRow,
    db_product: Product,
    target_dir: Path,
    downloader: AssetDownloader,
    index: int,
) -> Path | None:
    if candidate.path:
        source = Path(candidate.path)
        if source.exists() and source.is_file():
            target = target_dir / source.name
            if source.resolve() != target.resolve():
                copy2(source, target)
            else:
                target = source
            return target

    if not candidate.url:
        return None

    reference = AssetReference(url=candidate.url, asset_type=candidate.asset_type, role=candidate.role, page_url=product.source_url_final or product.source_url)
    supplier_sku = product.variant_sku or product.supplier_sku
    if candidate.asset_type == "image":
        downloaded = downloader.download_images(
            supplier_sku=supplier_sku,
            references=[reference],
            destination_dir=target_dir,
            product_name=product.product_name,
            product_title=product.product_title or product.title_raw,
            description=product.description or product.description_raw,
        )
    else:
        downloaded = downloader.download_pdfs(
            supplier_sku=supplier_sku,
            references=[reference],
            destination_dir=target_dir,
            product_name=product.product_name,
            product_title=product.product_title or product.title_raw,
        )
    if not downloaded:
        return None
    return Path(downloaded[0].local_path)


def _split_pipe_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split("|") if item and item.strip()]


def _preferred_filename(candidate: AssetCandidate) -> str | None:
    if candidate.path:
        return Path(candidate.path).name
    if candidate.url:
        name = Path(urlparse(candidate.url).path).name
        return name or None
    return None
