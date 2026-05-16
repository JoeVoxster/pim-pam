from __future__ import annotations

from app.models import ProductInputRow, ProductOutputRow, ProductVariant, ScrapedData


def normalize_product(
    row: ProductInputRow,
    scraped: ScrapedData | None = None,
    variant: ProductVariant | None = None,
) -> ProductOutputRow:
    scraped = scraped or ScrapedData()
    variant = variant or ProductVariant()
    extra_fields = dict(row.extra_fields)
    extra_fields.update(scraped.extra_fields)
    extra_fields.update(variant.extra_fields)
    is_standalone_product = variant.is_standalone_product
    product_name = (
        variant.title if is_standalone_product and variant.title else scraped.product_name or row.title_raw
    )
    description = (
        str(variant.extra_fields.get("description") or "").strip()
        if is_standalone_product and variant.extra_fields.get("description")
        else scraped.description or row.description_raw
    )
    supplier_sku = (
        variant.supplier_sku if is_standalone_product and variant.supplier_sku else scraped.supplier_sku or row.supplier_sku
    )
    variant_sku = (
        supplier_sku
        if is_standalone_product
        else variant.supplier_sku or scraped.supplier_sku or row.supplier_sku
    )
    fallback_source_url = _string_or_none(extra_fields.get("source_url"))
    fallback_source_url_final = _string_or_none(extra_fields.get("source_url_final")) or _string_or_none(extra_fields.get("product_url"))
    return ProductOutputRow(
        supplier_sku=supplier_sku,
        variant_sku=variant_sku,
        supplier_name=row.supplier_name,
        brand=row.brand,
        ean=variant.barcode or scraped.barcode or row.ean,
        barcode=variant.barcode or scraped.barcode or row.ean,
        variant_title=None if is_standalone_product else variant.title,
        variant_option_1_name=None if is_standalone_product else (variant.option_name or ("Pack Size" if variant.packaging else None)),
        variant_option_1_value=None if is_standalone_product else (variant.option_value or variant.packaging),
        source_url=row.source_url or fallback_source_url,
        source_url_final=scraped.source_url_final or fallback_source_url_final,
        title_raw=row.title_raw,
        description_raw=row.description_raw,
        product_name=product_name,
        product_title=(variant.title if is_standalone_product and variant.title else scraped.product_title or scraped.page_title or product_name),
        description=description,
        specifications=" | ".join(scraped.specifications) if scraped.specifications else None,
        technical_features=" | ".join(scraped.technical_features) if scraped.technical_features else None,
        image_urls=None if is_standalone_product else (" | ".join(scraped.image_urls) if scraped.image_urls else None),
        pdf_urls=None if is_standalone_product else (" | ".join(scraped.pdf_urls) if scraped.pdf_urls else None),
        datasheet_urls=None if is_standalone_product else (" | ".join(scraped.datasheet_urls) if scraped.datasheet_urls else None),
        sds_urls=None if is_standalone_product else (" | ".join(scraped.sds_urls) if scraped.sds_urls else None),
        extra_fields=extra_fields,
        status="ok",
    )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text
