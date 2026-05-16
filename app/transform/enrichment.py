from __future__ import annotations

from app.models import DownloadedAsset, ErrorRecord, ProductOutputRow


def apply_assets_to_product(product: ProductOutputRow, assets: list[DownloadedAsset]) -> ProductOutputRow:
    image_assets = [asset for asset in assets if asset.asset_type == "image"]
    pdf_assets = [asset for asset in assets if asset.asset_type == "pdf"]
    datasheet_assets = [asset for asset in pdf_assets if asset.role == "datasheet"]
    sds_assets = [asset for asset in pdf_assets if asset.role == "sds"]
    product.image_paths = " | ".join(asset.local_path for asset in image_assets) or None
    product.pdf_paths = " | ".join(asset.local_path for asset in pdf_assets) or None
    product.datasheet_paths = " | ".join(asset.local_path for asset in datasheet_assets) or None
    product.sds_paths = " | ".join(asset.local_path for asset in sds_assets) or None
    product.pdf_texts = "\n\n".join(asset.extracted_text or "" for asset in pdf_assets if asset.extracted_text) or None
    return product


def set_status(product: ProductOutputRow, errors: list[str]) -> ProductOutputRow:
    if not errors:
        product.status = "ok"
        product.error_reason = None
    elif product.product_name or product.description or product.image_paths or product.pdf_paths:
        product.status = "partial"
        product.error_reason = " | ".join(errors)
    else:
        product.status = "error"
        product.error_reason = " | ".join(errors)
    return product


def build_error_record(product: ProductOutputRow) -> ErrorRecord | None:
    if not product.error_reason:
        return None
    return ErrorRecord(
        supplier_sku=product.supplier_sku,
        supplier_name=product.supplier_name,
        source_url=product.source_url,
        reason=product.error_reason,
        status=product.status,
    )
