from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from app.assets.downloader import AssetDownloader
from app.assets.naming import ensure_supplier_asset_dir
from app.io.writers import write_asset_mapping
from app.models import AssetReference, DownloadedAsset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill local assets from products_clean.csv")
    parser.add_argument("--output", required=True, help="Export output directory containing products_clean.csv")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout for asset downloads")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output)
    products_path = output_dir / "products_clean.csv"
    if not products_path.exists():
        raise FileNotFoundError(f"Missing input file: {products_path}")

    frame = pd.read_csv(products_path).where(pd.notna, None)
    for column in ["image_paths", "pdf_paths", "datasheet_paths", "sds_paths", "pdf_texts"]:
        if column not in frame.columns:
            frame[column] = None
        frame[column] = frame[column].astype("object")
    assets_root = output_dir / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    downloader = AssetDownloader(timeout_seconds=args.timeout)
    asset_mapping: list[DownloadedAsset] = []

    for index, row in frame.iterrows():
        supplier_sku = _text(row.get("variant_sku")) or _text(row.get("supplier_sku")) or f"row-{index + 2}"
        product_name = _text(row.get("product_name")) or _text(row.get("product_title")) or supplier_sku
        product_title = _text(row.get("variant_title")) or _text(row.get("product_title")) or product_name
        description = _text(row.get("description"))
        asset_dir = ensure_supplier_asset_dir(assets_root, product_name)

        image_refs = [
            AssetReference(url=url, asset_type="image", page_url=_text(row.get("source_url_final")) or _text(row.get("source_url")))
            for url in _split_pipe_values(_text(row.get("image_urls")))
        ]
        pdf_refs = _build_pdf_references(row)

        downloaded_images: list[DownloadedAsset] = []
        downloaded_pdfs: list[DownloadedAsset] = []

        if image_refs:
            downloaded_images = downloader.download_images(
                supplier_sku=supplier_sku,
                references=image_refs,
                destination_dir=asset_dir,
                product_name=product_name,
                product_title=product_title,
                description=description,
            )
        if pdf_refs:
            downloaded_pdfs = downloader.download_pdfs(
                supplier_sku=supplier_sku,
                references=pdf_refs,
                destination_dir=asset_dir,
                product_name=product_name,
                product_title=product_title,
            )

        frame.at[index, "image_paths"] = " | ".join(asset.local_path for asset in downloaded_images) or None
        frame.at[index, "pdf_paths"] = " | ".join(asset.local_path for asset in downloaded_pdfs) or None
        frame.at[index, "datasheet_paths"] = " | ".join(asset.local_path for asset in downloaded_pdfs if asset.role == "datasheet") or None
        frame.at[index, "sds_paths"] = " | ".join(asset.local_path for asset in downloaded_pdfs if asset.role == "sds") or None
        frame.at[index, "pdf_texts"] = "\n\n".join(asset.extracted_text or "" for asset in downloaded_pdfs if asset.extracted_text) or None
        asset_mapping.extend(downloaded_images)
        asset_mapping.extend(downloaded_pdfs)

    frame.to_csv(products_path, index=False)
    frame.to_excel(output_dir / "products_clean.xlsx", index=False)
    write_asset_mapping(output_dir, asset_mapping)
    return 0


def _build_pdf_references(row: pd.Series) -> list[AssetReference]:
    page_url = _text(row.get("source_url_final")) or _text(row.get("source_url"))
    datasheet_urls = set(_split_pipe_values(_text(row.get("datasheet_urls"))))
    sds_urls = set(_split_pipe_values(_text(row.get("sds_urls"))))
    refs: list[AssetReference] = []
    for url in _split_pipe_values(_text(row.get("pdf_urls"))):
        role = None
        if url in datasheet_urls:
            role = "datasheet"
        elif url in sds_urls:
            role = "sds"
        refs.append(AssetReference(url=url, asset_type="pdf", role=role, page_url=page_url))
    return refs


def _split_pipe_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split("|") if item.strip()]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


if __name__ == "__main__":
    raise SystemExit(main())
