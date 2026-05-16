from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AssetReference(BaseModel):
    url: str
    asset_type: str
    role: str | None = None
    label: str | None = None
    context_text: str | None = None
    page_url: str | None = None
    packaging: str | None = None
    supplier_sku: str | None = None


class ProductVariant(BaseModel):
    packaging: str | None = None
    supplier_sku: str | None = None
    barcode: str | None = None
    title: str | None = None
    is_standalone_product: bool = False
    option_name: str | None = None
    option_value: str | None = None
    price: str | None = None
    currency: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class ProductInputRow(BaseModel):
    supplier_sku: str
    supplier_name: str | None = None
    source_url: str | None = None
    title_raw: str | None = None
    description_raw: str | None = None
    brand: str | None = None
    ean: str | None = None
    row_number: int | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class ScrapedData(BaseModel):
    source_url_final: str | None = None
    supplier_sku: str | None = None
    barcode: str | None = None
    product_name: str | None = None
    product_title: str | None = None
    description: str | None = None
    specifications: list[str] = Field(default_factory=list)
    variants: list[ProductVariant] = Field(default_factory=list)
    technical_features: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)
    pdf_urls: list[str] = Field(default_factory=list)
    datasheet_urls: list[str] = Field(default_factory=list)
    sds_urls: list[str] = Field(default_factory=list)
    asset_references: list[AssetReference] = Field(default_factory=list)
    page_title: str | None = None
    is_product_candidate: bool = False
    has_product_view: bool = False
    has_add_to_cart: bool = False
    has_price_box: bool = False
    has_category_products: bool = False
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class DownloadedAsset(BaseModel):
    supplier_sku: str
    asset_type: str
    role: str | None = None
    source_url: str
    page_url: str | None = None
    local_path: str
    file_name: str
    label: str | None = None
    context_text: str | None = None
    product_name: str | None = None
    product_title: str | None = None
    extracted_text: str | None = None


class ProductOutputRow(BaseModel):
    supplier_sku: str
    variant_sku: str | None = None
    supplier_name: str | None = None
    brand: str | None = None
    ean: str | None = None
    barcode: str | None = None
    variant_title: str | None = None
    variant_option_1_name: str | None = None
    variant_option_1_value: str | None = None
    source_url: str | None = None
    source_url_final: str | None = None
    title_raw: str | None = None
    description_raw: str | None = None
    product_name: str | None = None
    product_title: str | None = None
    description: str | None = None
    specifications: str | None = None
    technical_features: str | None = None
    image_urls: str | None = None
    image_paths: str | None = None
    pdf_urls: str | None = None
    pdf_paths: str | None = None
    datasheet_urls: str | None = None
    datasheet_paths: str | None = None
    sds_urls: str | None = None
    sds_paths: str | None = None
    pdf_texts: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    status: str
    error_reason: str | None = None


class ErrorRecord(BaseModel):
    supplier_sku: str
    supplier_name: str | None = None
    source_url: str | None = None
    reason: str
    status: str
