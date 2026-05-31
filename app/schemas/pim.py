from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    sku: str | None = None
    handle: str | None = None
    source_language: str = "en"
    title: str
    description: str | None = None
    brand_name: str | None = None
    status: str = "draft"
    is_chemical: bool = False
    chemical_type: str | None = None
    ufi: str | None = None
    voc_content_percent: str | None = None
    cas_number: str | None = None
    ec_number: str | None = None
    un_number: str | None = None
    hazard_class: str | None = None
    packing_group: str | None = None
    adr_relevant: bool = False
    ghs_pictograms: str | None = None
    signal_word: str | None = None
    chemical_safety_json: dict | None = None
    hazard_statements: str | None = None
    precautionary_statements: str | None = None
    wgk: str | None = None
    wgk_label: str | None = None
    wgk_source_section: str | None = None
    wgk_source_url: str | None = None
    wgk_source_asset_id: int | None = None
    wgk_confidence: float | None = None
    storage_class: str | None = None
    storage_class_label: str | None = None
    storage_class_source_section: str | None = None
    storage_class_source_url: str | None = None
    storage_class_source_asset_id: int | None = None
    storage_class_confidence: float | None = None
    sds_available: bool = False
    sds_url: str | None = None
    sds_asset_id: int | None = None
    chemical_reference_url: str | None = None
    chemical_enrichment_status: str | None = None
    chemical_enrichment_error: str | None = None
    density: str | None = None
    color: str | None = None
    odor: str | None = None
    ph_value: str | None = None
    flash_point: str | None = None
    boiling_point: str | None = None
    viscosity: str | None = None
    solubility: str | None = None
    business_only: bool = False
    age_check_required: bool = False
    shippable: bool = True
    limited_quantity: str | None = None
    hazard_shipping_note: str | None = None
    shop_active: bool = True


class ProductUpdate(BaseModel):
    sku: str | None = None
    source_language: str = "en"
    title: str
    description: str | None = None
    source_url: str | None = None
    source_url_final: str | None = None
    brand_name: str | None = None
    status: str
    category_channel_code: str | None = None
    category_ids: list[int] = Field(default_factory=list)
    is_chemical: bool | None = None
    chemical_type: str | None = None
    ufi: str | None = None
    voc_content_percent: str | None = None
    cas_number: str | None = None
    ec_number: str | None = None
    un_number: str | None = None
    hazard_class: str | None = None
    packing_group: str | None = None
    adr_relevant: bool | None = None
    ghs_pictograms: str | None = None
    signal_word: str | None = None
    chemical_safety_json: dict | None = None
    hazard_statements: str | None = None
    precautionary_statements: str | None = None
    wgk: str | None = None
    wgk_label: str | None = None
    wgk_source_section: str | None = None
    wgk_source_url: str | None = None
    wgk_source_asset_id: int | None = None
    wgk_confidence: float | None = None
    storage_class: str | None = None
    storage_class_label: str | None = None
    storage_class_source_section: str | None = None
    storage_class_source_url: str | None = None
    storage_class_source_asset_id: int | None = None
    storage_class_confidence: float | None = None
    sds_available: bool | None = None
    sds_url: str | None = None
    sds_asset_id: int | None = None
    chemical_reference_url: str | None = None
    chemical_enrichment_status: str | None = None
    chemical_enrichment_error: str | None = None
    density: str | None = None
    color: str | None = None
    odor: str | None = None
    ph_value: str | None = None
    flash_point: str | None = None
    boiling_point: str | None = None
    viscosity: str | None = None
    solubility: str | None = None
    business_only: bool | None = None
    age_check_required: bool | None = None
    shippable: bool | None = None
    limited_quantity: str | None = None
    hazard_shipping_note: str | None = None
    shop_active: bool | None = None


class VariantCreate(BaseModel):
    sku: str | None = None
    manufacturer_sku: str | None = None
    vendor_description: str | None = None
    variant_title: str | None = None
    option_name: str | None = None
    option_value: str | None = None
    sales_unit: str | None = None
    pack_quantity: Decimal | None = None
    pack_unit: str | None = None
    packaging: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    cost_price: Decimal | None = None
    cost_currency: str | None = None
    stock_qty: int = 0
    barcode: str | None = None
    status: str = "active"
    customs_description_de: str | None = None
    customs_description_en: str | None = None
    origin_country: str | None = None
    material_composition: str | None = None
    ch_tariff_code: str | None = None
    ch_statistical_key: str | None = None
    ch_customs_unit_code: str | None = None
    ch_customs_quantity_per_unit: Decimal | None = None
    ch_net_mass_kg: Decimal | None = None
    ch_gross_mass_kg: Decimal | None = None
    ch_preference_possible: bool = False
    ch_origin_proof_required: bool = False
    ch_nze_required: bool = False
    ch_nze_code: str | None = None
    ch_voc_relevant: bool = False
    eu_cn_code: str | None = None
    eu_taric_code: str | None = None
    de_import_code: str | None = None
    de_customs_unit_code: str | None = None
    de_customs_quantity_per_unit: Decimal | None = None
    eu_export_control_required: bool = False
    dual_use_required: bool = False
    reach_relevant: bool = False
    antidumping_relevant: bool = False
    customs_notes: str | None = None


class VariantUpdate(BaseModel):
    sku: str | None = None
    manufacturer_sku: str | None = None
    vendor_description: str | None = None
    variant_title: str | None = None
    option_name: str | None = None
    option_value: str | None = None
    sales_unit: str | None = None
    pack_quantity: Decimal | None = None
    pack_unit: str | None = None
    packaging: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    cost_price: Decimal | None = None
    cost_currency: str | None = None
    stock_qty: int = 0
    barcode: str | None = None
    status: str | None = None
    customs_description_de: str | None = None
    customs_description_en: str | None = None
    origin_country: str | None = None
    material_composition: str | None = None
    ch_tariff_code: str | None = None
    ch_statistical_key: str | None = None
    ch_customs_unit_code: str | None = None
    ch_customs_quantity_per_unit: Decimal | None = None
    ch_net_mass_kg: Decimal | None = None
    ch_gross_mass_kg: Decimal | None = None
    ch_preference_possible: bool | None = None
    ch_origin_proof_required: bool | None = None
    ch_nze_required: bool | None = None
    ch_nze_code: str | None = None
    ch_voc_relevant: bool | None = None
    eu_cn_code: str | None = None
    eu_taric_code: str | None = None
    de_import_code: str | None = None
    de_customs_unit_code: str | None = None
    de_customs_quantity_per_unit: Decimal | None = None
    eu_export_control_required: bool | None = None
    dual_use_required: bool | None = None
    reach_relevant: bool | None = None
    antidumping_relevant: bool | None = None
    customs_notes: str | None = None


class VariantCustomsAdditionalCodeUpsert(BaseModel):
    id: int | None = None
    variant_id: int
    jurisdiction: str
    flow: str
    code: str
    description: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    status: str = "active"
    source: str | None = None
    notes: str | None = None


class VariantTechnicalAttributeUpsert(BaseModel):
    id: int | None = None
    variant_id: int
    attribute_code: str | None = None
    attribute_name: str
    value_text: str | None = None
    value_number: Decimal | None = None
    unit: str | None = None
    sort_order: int = 0


class TechnicalAttributeLabelTranslationUpsert(BaseModel):
    id: int | None = None
    attribute_code: str
    language_code: str
    label: str


class VariantTechnicalAttributeValueTranslationUpsert(BaseModel):
    id: int | None = None
    technical_attribute_id: int
    language_code: str
    value_text: str


class VariantPriceTierCreate(BaseModel):
    variant_id: int
    price_list_id: int | None = None
    price_type: str = "sale"
    min_qty: int = 1
    max_qty: int | None = None
    price: Decimal
    currency: str
    status: str = "active"


class PriceListUpsert(BaseModel):
    id: int | None = None
    code: str
    name: str
    price_list_type: str = "sale"
    sales_channel_id: int | None = None
    currency: str = "CHF"
    valid_from: str | None = None
    valid_to: str | None = None
    status: str = "active"


class CurrencyRateUpsert(BaseModel):
    id: int | None = None
    source_currency: str
    target_currency: str = "CHF"
    effective_rate: Decimal
    markup_percent: Decimal = Decimal("0")
    used_rate: Decimal
    rate_date: str | None = None
    status: str = "active"


class CostSurchargeUpsert(BaseModel):
    id: int | None = None
    code: str
    name: str
    surcharge_type: str
    scope_type: str = "global"
    scope_value: str | None = None
    percent: Decimal
    status: str = "active"


class CategoryCreate(BaseModel):
    name: str
    slug: str
    parent_id: int | None = None
    language_code: str = "de"
    sort_order: int = 0


class AssetCreate(BaseModel):
    product_id: int | None = None
    variant_id: int | None = None
    filename: str
    original_filename: str
    mime_type: str
    file_size: int
    width: int | None = None
    height: int | None = None
    storage_path: str
    source_url: str | None = None
    checksum: str | None = None
    alt_text: str | None = None
    sort_order: int = 0


class ProductTranslationCreate(BaseModel):
    product_id: int
    language_code: str
    title: str
    short_description: str | None = None
    description: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    slug: str | None = None
    translation_status: str = "draft"
    source_language_code: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_used: str | None = None


class VariantTranslationCreate(BaseModel):
    variant_id: int
    language_code: str
    title: str
    option_label_override: str | None = None
    package_label: str | None = None


class SalesChannelCreate(BaseModel):
    code: str
    name: str
    is_active: bool = True
    sort_order: int = 0


class SalesChannelUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class ProductChannelListingUpdate(BaseModel):
    product_id: int
    sales_channel_id: int
    allowed: bool = False
    is_active: bool = False
    active_from: str | None = None
    active_until: str | None = None
    publication_status: str = "draft"


class VariantChannelListingUpdate(BaseModel):
    variant_id: int
    sales_channel_id: int
    allowed: bool = False
    is_active: bool = False
    publication_status: str = "draft"
    price_enabled: bool = True
    shippable: bool = True
    hazardous_goods: bool = False
    limited_quantity: str | None = None
    channel_sku: str | None = None
    channel_ean: str | None = None


class ChannelCategoryUpsert(BaseModel):
    sales_channel_id: int
    external_category_id: str
    external_path: str | None = None
    name: str
    required_attributes_json: list | dict | None = None
    is_active: bool = True


class ProductCategoryMappingUpsert(BaseModel):
    product_id: int
    sales_channel_id: int
    channel_category_id: int
    position: int | None = None
    is_primary: bool = False


class VariantCategoryMappingUpsert(BaseModel):
    variant_id: int
    sales_channel_id: int
    channel_category_id: int
    is_primary: bool = False


class ImportMappingConfig(BaseModel):
    supplier_name: str | None = None
    sales_channel_code: str = "voxster"
    default_currency: str = "EUR"
    category_separator: str = ">"
    category_columns: list[str] = Field(default_factory=lambda: ["category", "categories"])
    stock_column: str | None = "stock_qty"
    price_column_candidates: list[str] = Field(default_factory=lambda: ["sales_price", "purchase_price", "price", "Preis"])


class EnrichmentJobOptions(BaseModel):
    seed_url: str
    supplier_name: str | None = None
    resolver_mode: str = "generic_crawl"
    resolver_listing_url: str | None = None
    max_pages: int = 200
    only_empty_fields: bool = True
    update_description: bool = True
    update_assets: bool = True
    update_packaging: bool = True
    update_specifications: bool = True
    update_technical_features: bool = True
    update_source_urls: bool = True


class ProductSDBUpdate(BaseModel):
    source_url: str | None = None
    pdf_url: str | None = None
    source_asset_id: int | None = None
    parser_status: str | None = None
    review_status: str | None = None
    version_label: str | None = None
    effective_date: str | None = None
    document_title: str | None = None
    issuer_name: str | None = None
    issuer_address_line1: str | None = None
    issuer_address_line2: str | None = None
    issuer_postal_code: str | None = None
    issuer_city: str | None = None
    issuer_country_code: str | None = None
    issuer_phone: str | None = None
    issuer_email: str | None = None
    action_log_json: list[dict[str, object]] | None = None
    raw_text: str | None = None
    sections_json: dict[str, object] = Field(default_factory=dict)
    generated_pdf_path: str | None = None
