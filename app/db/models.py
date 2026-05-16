from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    products: Mapped[list["Product"]] = relationship(back_populates="brand")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("sales_channel_id", "slug", name="uq_categories_sales_channel_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    language_code: Mapped[str] = mapped_column(String(12), nullable=False, default="de", server_default="de")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    sales_channel: Mapped["SalesChannel"] = relationship(back_populates="categories")
    parent: Mapped["Category | None"] = relationship(remote_side=[id], backref="children")
    product_links: Mapped[list["ProductCategoryAssignment"]] = relationship(back_populates="category", cascade="all, delete-orphan")


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_products_sku"),
        UniqueConstraint("handle", name="uq_products_handle"),
        Index("ix_products_brand_id", "brand_id"),
        Index("ix_products_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    family_key: Mapped[str | None] = mapped_column(String(255), index=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_language: Mapped[str] = mapped_column(String(12), nullable=False, default="en", server_default="en")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_url_final: Mapped[str | None] = mapped_column(String(1000))
    specifications_text: Mapped[str | None] = mapped_column(Text)
    technical_features_text: Mapped[str | None] = mapped_column(Text)
    is_chemical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    chemical_type: Mapped[str | None] = mapped_column(String(255))
    ufi: Mapped[str | None] = mapped_column(String(64))
    voc_content_percent: Mapped[str | None] = mapped_column(String(64))
    cas_number: Mapped[str | None] = mapped_column(String(64), index=True)
    ec_number: Mapped[str | None] = mapped_column(String(64))
    un_number: Mapped[str | None] = mapped_column(String(32), index=True)
    hazard_class: Mapped[str | None] = mapped_column(String(64))
    packing_group: Mapped[str | None] = mapped_column(String(32))
    adr_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    ghs_pictograms: Mapped[str | None] = mapped_column(Text)
    signal_word: Mapped[str | None] = mapped_column(String(64))
    chemical_safety_json: Mapped[dict | None] = mapped_column(JSON)
    hazard_statements: Mapped[str | None] = mapped_column(Text)
    precautionary_statements: Mapped[str | None] = mapped_column(Text)
    wgk: Mapped[str | None] = mapped_column(String(64))
    wgk_label: Mapped[str | None] = mapped_column(String(255))
    wgk_source_section: Mapped[str | None] = mapped_column(String(32))
    wgk_source_url: Mapped[str | None] = mapped_column(String(1000))
    wgk_source_asset_id: Mapped[int | None] = mapped_column(Integer)
    wgk_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    wgk_last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_class: Mapped[str | None] = mapped_column(String(64))
    storage_class_label: Mapped[str | None] = mapped_column(String(255))
    storage_class_source_section: Mapped[str | None] = mapped_column(String(32))
    storage_class_source_url: Mapped[str | None] = mapped_column(String(1000))
    storage_class_source_asset_id: Mapped[int | None] = mapped_column(Integer)
    storage_class_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    storage_class_last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sds_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sds_url: Mapped[str | None] = mapped_column(String(1000))
    sds_asset_id: Mapped[int | None] = mapped_column(Integer)
    chemical_reference_url: Mapped[str | None] = mapped_column(String(1000))
    chemical_last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chemical_enrichment_status: Mapped[str | None] = mapped_column(String(64))
    chemical_enrichment_error: Mapped[str | None] = mapped_column(Text)
    density: Mapped[str | None] = mapped_column(String(255))
    color: Mapped[str | None] = mapped_column(String(255))
    odor: Mapped[str | None] = mapped_column(String(255))
    ph_value: Mapped[str | None] = mapped_column(String(255))
    flash_point: Mapped[str | None] = mapped_column(String(255))
    boiling_point: Mapped[str | None] = mapped_column(String(255))
    viscosity: Mapped[str | None] = mapped_column(String(255))
    solubility: Mapped[str | None] = mapped_column(String(255))
    business_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    age_check_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    shippable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    limited_quantity: Mapped[str | None] = mapped_column(String(255))
    hazard_shipping_note: Mapped[str | None] = mapped_column(Text)
    shop_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft", server_default="draft")
    merged_into_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    dedupe_status: Mapped[str | None] = mapped_column(String(50), index=True)
    dedupe_notes: Mapped[str | None] = mapped_column(Text)
    source_refs_json: Mapped[list | dict | None] = mapped_column(JSON)

    brand: Mapped[Brand | None] = relationship(back_populates="products")
    merged_into_product: Mapped["Product | None"] = relationship(remote_side=[id])
    variants: Mapped[list["ProductVariant"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    category_links: Mapped[list["ProductCategoryAssignment"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    assets: Mapped[list["Asset"]] = relationship(back_populates="product")
    translations: Mapped[list["ProductTranslation"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    channel_listings: Mapped[list["ProductChannelListing"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    channel_category_mappings: Mapped[list["ProductCategoryMapping"]] = relationship(back_populates="product", cascade="all, delete-orphan")
    chemical_enrichments: Mapped[list["ProductChemicalEnrichment"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductChemicalEnrichment.extracted_at.desc()",
    )
    sdb_record: Mapped["ProductSDB | None"] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        uselist=False,
    )
    chemical_documents: Mapped[list["ChemicalDocument"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ChemicalDocument.updated_at.desc()",
    )
    suva_checks: Mapped[list["ProductSuvaCheck"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductSuvaCheck.checked_at.desc()",
    )
    enrichment_candidates: Mapped[list["ProductEnrichmentCandidate"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductEnrichmentCandidate.updated_at.desc()",
    )
    asset_candidates: Mapped[list["ProductAssetCandidate"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductAssetCandidate.updated_at.desc()",
    )
    merge_logs_as_master: Mapped[list["ProductMergeLog"]] = relationship(back_populates="master_product")


class ProductVariant(TimestampMixin, Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint("sku", name="uq_product_variants_sku"),
        Index("ix_product_variants_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sku: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    variant_title: Mapped[str | None] = mapped_column(String(500))
    option_name: Mapped[str | None] = mapped_column(String(100))
    option_value: Mapped[str | None] = mapped_column(String(255))
    packaging: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost_currency: Mapped[str | None] = mapped_column(String(3))
    stock_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    barcode: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", server_default="active", index=True)

    product: Mapped[Product] = relationship(back_populates="variants")
    assets: Mapped[list["Asset"]] = relationship(back_populates="variant")
    channel_listings: Mapped[list["VariantChannelListing"]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    channel_category_mappings: Mapped[list["VariantCategoryMapping"]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    translations: Mapped[list["VariantTranslation"]] = relationship(back_populates="variant", cascade="all, delete-orphan")
    price_tiers: Mapped[list["ProductVariantPriceTier"]] = relationship(
        back_populates="variant",
        cascade="all, delete-orphan",
        order_by="ProductVariantPriceTier.min_qty.asc()",
    )


class ProductVariantPriceTier(TimestampMixin, Base):
    __tablename__ = "product_variant_price_tiers"
    __table_args__ = (
        UniqueConstraint(
            "variant_id",
            "price_type",
            "currency",
            "min_qty",
            "max_qty",
            name="uq_variant_price_tiers_variant_scope",
        ),
        Index("ix_variant_price_tiers_variant_id", "variant_id"),
        Index("ix_variant_price_tiers_price_type", "price_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    price_type: Mapped[str] = mapped_column(String(20), nullable=False, default="sale", server_default="sale")
    min_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    max_qty: Mapped[int | None] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    variant: Mapped[ProductVariant] = relationship(back_populates="price_tiers")


class ProductCategoryAssignment(TimestampMixin, Base):
    __tablename__ = "product_category_assignments"
    __table_args__ = (
        UniqueConstraint("product_id", "category_id", "sales_channel_id", name="uq_product_category_assignments_scope"),
        Index("ix_product_category_assignments_product_id", "product_id"),
        Index("ix_product_category_assignments_category_id", "category_id"),
        Index("ix_product_category_assignments_sales_channel_id", "sales_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), nullable=False)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    product: Mapped[Product] = relationship(back_populates="category_links")
    category: Mapped[Category] = relationship(back_populates="product_links")
    sales_channel: Mapped["SalesChannel"] = relationship()


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_product_id", "product_id"),
        Index("ix_assets_variant_id", "variant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("product_variants.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    checksum: Mapped[str | None] = mapped_column(String(128))
    alt_text: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stored_filename: Mapped[str | None] = mapped_column(String(255))
    object_key: Mapped[str | None] = mapped_column(String(1000), index=True)
    bucket: Mapped[str | None] = mapped_column(String(255))
    storage_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="local", server_default="local", index=True)
    file_extension: Mapped[str | None] = mapped_column(String(32))
    asset_type: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(String(12), index=True)
    public_url: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active", index=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product | None] = relationship(back_populates="assets")
    variant: Mapped[ProductVariant | None] = relationship(back_populates="assets")


class R2StorageConfig(TimestampMixin, Base):
    __tablename__ = "r2_storage_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="cloudflare_r2", server_default="cloudflare_r2")
    endpoint: Mapped[str | None] = mapped_column(String(1000))
    bucket: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str] = mapped_column(String(64), nullable=False, default="auto", server_default="auto")
    access_key_id: Mapped[str | None] = mapped_column(String(255))
    secret_access_key: Mapped[str | None] = mapped_column(Text)
    public_base_url: Mapped[str | None] = mapped_column(String(1000))
    path_prefix: Mapped[str | None] = mapped_column(String(255))
    storage_class: Mapped[str | None] = mapped_column(String(64))
    max_upload_size_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=50, server_default="50")
    allowed_file_types: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_message: Mapped[str | None] = mapped_column(Text)


class MedusaConnectionConfig(TimestampMixin, Base):
    __tablename__ = "medusa_connection_configs"
    __table_args__ = (
        UniqueConstraint("name", name="uq_medusa_connection_configs_name"),
        Index("ix_medusa_connection_configs_enabled", "enabled"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default", server_default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    base_url: Mapped[str | None] = mapped_column(String(1000))
    admin_path: Mapped[str] = mapped_column(String(255), nullable=False, default="/admin", server_default="/admin")
    auth_type: Mapped[str] = mapped_column(String(32), nullable=False, default="api_token", server_default="api_token")
    api_token_secret: Mapped[str | None] = mapped_column(Text)
    jwt_email: Mapped[str | None] = mapped_column(String(255))
    jwt_password_secret: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30, server_default="30")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    rate_limit_per_second: Mapped[int | None] = mapped_column(Integer)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    dry_run_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    default_region_id: Mapped[str | None] = mapped_column(String(255))
    default_sales_channel_id: Mapped[str | None] = mapped_column(String(255))
    default_currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="CHF", server_default="CHF")
    default_locale: Mapped[str] = mapped_column(String(12), nullable=False, default="de-CH", server_default="de-CH")
    enabled_locales: Mapped[list | dict | None] = mapped_column(JSON)
    public_asset_base_url: Mapped[str | None] = mapped_column(String(1000))
    product_status_default: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    product_match_policy: Mapped[str] = mapped_column(String(100), nullable=False, default="id_handle_metadata", server_default="id_handle_metadata")
    variant_match_policy: Mapped[str] = mapped_column(String(100), nullable=False, default="id_sku_metadata", server_default="id_sku_metadata")
    conflict_policy: Mapped[str] = mapped_column(String(100), nullable=False, default="skip_conflicts", server_default="skip_conflicts")
    pricing_strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="default_and_price_lists", server_default="default_and_price_lists")
    translation_strategy: Mapped[str] = mapped_column(String(100), nullable=False, default="translation_module", server_default="translation_module")
    export_products: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_variants: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_options: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_categories: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    export_collections: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    export_tags: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    export_types: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    export_images: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_seo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_metadata: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_translations: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_default_prices: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_price_lists: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_tiered_prices: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    export_inventory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    pull_ids_after_export: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    repair_mapping_before_export: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    last_test_status: Mapped[str | None] = mapped_column(String(32))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_message: Mapped[str | None] = mapped_column(Text)

    mappings: Mapped[list["MedusaSyncMapping"]] = relationship(back_populates="connection", cascade="all, delete-orphan")
    runs: Mapped[list["MedusaSyncRun"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class MedusaSyncMapping(TimestampMixin, Base):
    __tablename__ = "medusa_sync_mappings"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "entity_type",
            "local_entity_id",
            "locale_code",
            "price_list_code",
            "currency_code",
            "min_quantity",
            "max_quantity",
            name="uq_medusa_mapping_local_scope",
        ),
        Index("ix_medusa_sync_mappings_connection_entity", "connection_id", "entity_type"),
        Index("ix_medusa_sync_mappings_local", "entity_type", "local_entity_id"),
        Index("ix_medusa_sync_mappings_medusa_id", "connection_id", "entity_type", "medusa_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("medusa_connection_configs.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    local_parent_id: Mapped[int | None] = mapped_column(Integer)
    medusa_id: Mapped[str | None] = mapped_column(String(255))
    medusa_parent_id: Mapped[str | None] = mapped_column(String(255))
    medusa_handle: Mapped[str | None] = mapped_column(String(500))
    medusa_sku: Mapped[str | None] = mapped_column(String(255))
    medusa_external_id: Mapped[str | None] = mapped_column(String(500))
    locale_code: Mapped[str | None] = mapped_column(String(12))
    price_list_code: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    min_quantity: Mapped[int | None] = mapped_column(Integer)
    max_quantity: Mapped[int | None] = mapped_column(Integer)
    local_hash: Mapped[str | None] = mapped_column(String(128))
    medusa_hash: Mapped[str | None] = mapped_column(String(128))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_in_medusa_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_direction: Mapped[str] = mapped_column(String(32), nullable=False, default="pim_to_medusa", server_default="pim_to_medusa")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active", index=True)

    connection: Mapped[MedusaConnectionConfig] = relationship(back_populates="mappings")


class MedusaSyncRun(Base):
    __tablename__ = "medusa_sync_runs"
    __table_args__ = (
        Index("ix_medusa_sync_runs_connection_id", "connection_id"),
        Index("ix_medusa_sync_runs_status", "status"),
        Index("ix_medusa_sync_runs_mode", "mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("medusa_connection_configs.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default="running")
    selected_scope: Mapped[list | dict | None] = mapped_column(JSON)
    summary: Mapped[list | dict | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(255))

    connection: Mapped[MedusaConnectionConfig] = relationship(back_populates="runs")
    items: Mapped[list["MedusaSyncRunItem"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class MedusaSyncRunItem(Base):
    __tablename__ = "medusa_sync_run_items"
    __table_args__ = (
        Index("ix_medusa_sync_run_items_run_id", "run_id"),
        Index("ix_medusa_sync_run_items_entity", "entity_type", "local_entity_id"),
        Index("ix_medusa_sync_run_items_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("medusa_sync_runs.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    local_entity_id: Mapped[int | None] = mapped_column(Integer)
    medusa_id: Mapped[str | None] = mapped_column(String(255))
    locale_code: Mapped[str | None] = mapped_column(String(12))
    price_list_code: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_payload: Mapped[list | dict | None] = mapped_column(JSON)
    response_payload: Mapped[list | dict | None] = mapped_column(JSON)
    diff: Mapped[list | dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run: Mapped[MedusaSyncRun] = relationship(back_populates="items")


class MedusaPriceListMapping(TimestampMixin, Base):
    __tablename__ = "medusa_price_list_mappings"
    __table_args__ = (
        UniqueConstraint("connection_id", "local_price_list_code", "currency_code", name="uq_medusa_price_list_mapping_scope"),
        Index("ix_medusa_price_list_mappings_connection_id", "connection_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("medusa_connection_configs.id", ondelete="CASCADE"), nullable=False)
    local_price_list_id: Mapped[int | None] = mapped_column(Integer)
    local_price_list_code: Mapped[str] = mapped_column(String(255), nullable=False)
    local_price_list_name: Mapped[str | None] = mapped_column(String(500))
    medusa_price_list_id: Mapped[str | None] = mapped_column(String(255))
    medusa_price_list_type: Mapped[str | None] = mapped_column(String(32))
    customer_group_id: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")


class MedusaLocaleMapping(TimestampMixin, Base):
    __tablename__ = "medusa_locale_mappings"
    __table_args__ = (
        UniqueConstraint("connection_id", "local_locale", name="uq_medusa_locale_mapping_scope"),
        Index("ix_medusa_locale_mappings_connection_id", "connection_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("medusa_connection_configs.id", ondelete="CASCADE"), nullable=False)
    local_locale: Mapped[str] = mapped_column(String(12), nullable=False)
    medusa_locale: Mapped[str] = mapped_column(String(12), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class ProductTranslation(TimestampMixin, Base):
    __tablename__ = "product_translations"
    __table_args__ = (
        UniqueConstraint("product_id", "language_code", name="uq_product_translations_product_lang"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    language_code: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    short_description: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    seo_title: Mapped[str | None] = mapped_column(String(500))
    seo_description: Mapped[str | None] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(String(500))
    translation_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    source_language_code: Mapped[str | None] = mapped_column(String(12))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_used: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="translations")


class Language(TimestampMixin, Base):
    __tablename__ = "languages"
    __table_args__ = (
        UniqueConstraint("code", name="uq_languages_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")


class TranslationPrompt(TimestampMixin, Base):
    __tablename__ = "translation_prompts"
    __table_args__ = (
        UniqueConstraint("language_code", name="uq_translation_prompts_language_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    language_code: Mapped[str] = mapped_column(String(12), nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text)


class VariantTranslation(TimestampMixin, Base):
    __tablename__ = "variant_translations"
    __table_args__ = (
        UniqueConstraint("variant_id", "language_code", name="uq_variant_translations_variant_lang"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    language_code: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    option_label_override: Mapped[str | None] = mapped_column(String(255))
    package_label: Mapped[str | None] = mapped_column(String(255))

    variant: Mapped[ProductVariant] = relationship(back_populates="translations")


class SalesChannel(TimestampMixin, Base):
    __tablename__ = "sales_channels"
    __table_args__ = (
        UniqueConstraint("code", name="uq_sales_channels_code"),
        UniqueConstraint("name", name="uq_sales_channels_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    categories: Mapped[list["Category"]] = relationship(back_populates="sales_channel")
    product_listings: Mapped[list["ProductChannelListing"]] = relationship(back_populates="sales_channel", cascade="all, delete-orphan")
    variant_listings: Mapped[list["VariantChannelListing"]] = relationship(back_populates="sales_channel", cascade="all, delete-orphan")
    channel_categories: Mapped[list["ChannelCategory"]] = relationship(back_populates="sales_channel", cascade="all, delete-orphan")
    product_category_mappings: Mapped[list["ProductCategoryMapping"]] = relationship(back_populates="sales_channel", cascade="all, delete-orphan")
    variant_category_mappings: Mapped[list["VariantCategoryMapping"]] = relationship(back_populates="sales_channel", cascade="all, delete-orphan")


class ProductChannelListing(TimestampMixin, Base):
    __tablename__ = "product_channel_listings"
    __table_args__ = (
        UniqueConstraint("product_id", "sales_channel_id", name="uq_product_channel_listings_scope"),
        Index("ix_product_channel_listings_product_id", "product_id"),
        Index("ix_product_channel_listings_sales_channel_id", "sales_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    active_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")

    product: Mapped[Product] = relationship(back_populates="channel_listings")
    sales_channel: Mapped[SalesChannel] = relationship(back_populates="product_listings")


class VariantChannelListing(TimestampMixin, Base):
    __tablename__ = "variant_channel_listings"
    __table_args__ = (
        UniqueConstraint("variant_id", "sales_channel_id", name="uq_variant_channel_listings_scope"),
        Index("ix_variant_channel_listings_variant_id", "variant_id"),
        Index("ix_variant_channel_listings_sales_channel_id", "sales_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    price_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    shippable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    hazardous_goods: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    limited_quantity: Mapped[str | None] = mapped_column(String(255))
    channel_sku: Mapped[str | None] = mapped_column(String(255))
    channel_ean: Mapped[str | None] = mapped_column(String(255))

    variant: Mapped[ProductVariant] = relationship(back_populates="channel_listings")
    sales_channel: Mapped[SalesChannel] = relationship(back_populates="variant_listings")


class ChannelCategory(TimestampMixin, Base):
    __tablename__ = "channel_categories"
    __table_args__ = (
        UniqueConstraint("sales_channel_id", "external_category_id", name="uq_channel_categories_external_id"),
        Index("ix_channel_categories_sales_channel_id", "sales_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    external_category_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_path: Mapped[str | None] = mapped_column(String(1000))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    required_attributes_json: Mapped[list | dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    sales_channel: Mapped[SalesChannel] = relationship(back_populates="channel_categories")
    product_mappings: Mapped[list["ProductCategoryMapping"]] = relationship(back_populates="channel_category")
    variant_mappings: Mapped[list["VariantCategoryMapping"]] = relationship(back_populates="channel_category")


class ProductCategoryMapping(TimestampMixin, Base):
    __tablename__ = "product_category_mappings"
    __table_args__ = (
        UniqueConstraint("product_id", "sales_channel_id", "channel_category_id", name="uq_product_category_mappings_scope"),
        Index("ix_product_category_mappings_product_id", "product_id"),
        Index("ix_product_category_mappings_sales_channel_id", "sales_channel_id"),
        Index("ix_product_category_mappings_channel_category_id", "channel_category_id"),
        Index("ix_product_category_mappings_position", "position"),
        Index("ix_product_category_mappings_category_position", "channel_category_id", "position", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    channel_category_id: Mapped[int] = mapped_column(ForeignKey("channel_categories.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=9999, server_default="9999")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    product: Mapped[Product] = relationship(back_populates="channel_category_mappings")
    sales_channel: Mapped[SalesChannel] = relationship(back_populates="product_category_mappings")
    channel_category: Mapped[ChannelCategory] = relationship(back_populates="product_mappings")


class VariantCategoryMapping(TimestampMixin, Base):
    __tablename__ = "variant_category_mappings"
    __table_args__ = (
        UniqueConstraint("variant_id", "sales_channel_id", "channel_category_id", name="uq_variant_category_mappings_scope"),
        Index("ix_variant_category_mappings_variant_id", "variant_id"),
        Index("ix_variant_category_mappings_sales_channel_id", "sales_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False)
    sales_channel_id: Mapped[int] = mapped_column(ForeignKey("sales_channels.id", ondelete="CASCADE"), nullable=False)
    channel_category_id: Mapped[int] = mapped_column(ForeignKey("channel_categories.id", ondelete="CASCADE"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    variant: Mapped[ProductVariant] = relationship(back_populates="channel_category_mappings")
    sales_channel: Mapped[SalesChannel] = relationship(back_populates="variant_category_mappings")
    channel_category: Mapped[ChannelCategory] = relationship(back_populates="variant_mappings")


class ProductChemicalEnrichment(TimestampMixin, Base):
    __tablename__ = "product_chemical_enrichments"
    __table_args__ = (
        Index("ix_product_chemical_enrichments_product_id", "product_id"),
        Index("ix_product_chemical_enrichments_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    reference_url: Mapped[str | None] = mapped_column(String(1000))
    source_kind: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", server_default="pending")
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON)
    normalized_payload_json: Mapped[dict | None] = mapped_column(JSON)
    document_links_json: Mapped[list | dict | None] = mapped_column(JSON)
    warnings_json: Mapped[list | dict | None] = mapped_column(JSON)
    error_log: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="chemical_enrichments")


class ProductEnrichmentLog(TimestampMixin, Base):
    __tablename__ = "product_enrichment_logs"
    __table_args__ = (
        Index("ix_product_enrichment_logs_product_id", "product_id"),
        Index("ix_product_enrichment_logs_status", "status"),
        Index("ix_product_enrichment_logs_field_name", "field_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_domain: Mapped[str | None] = mapped_column(String(255))
    search_query: Mapped[str | None] = mapped_column(Text)
    search_method: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="suggested", server_default="suggested")
    error_message: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    language_code: Mapped[str | None] = mapped_column(String(12))
    created_by: Mapped[str | None] = mapped_column(String(255))

    product: Mapped[Product] = relationship()


class ProductEnrichmentCandidate(TimestampMixin, Base):
    __tablename__ = "product_enrichment_candidates"
    __table_args__ = (
        Index("ix_product_enrichment_candidates_product_id", "product_id"),
        Index("ix_product_enrichment_candidates_status", "status"),
        Index("ix_product_enrichment_candidates_field_name", "field_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    supplier_key: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_domain: Mapped[str | None] = mapped_column(String(255))
    source_language: Mapped[str | None] = mapped_column(String(12))
    source_locale: Mapped[str | None] = mapped_column(String(12))
    target_locale: Mapped[str | None] = mapped_column(String(12))
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    section_name: Mapped[str | None] = mapped_column(String(255))
    source_value: Mapped[str | None] = mapped_column(Text)
    suggested_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", server_default="new")
    warning: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(back_populates="enrichment_candidates")


class ProductAssetCandidate(TimestampMixin, Base):
    __tablename__ = "product_asset_candidates"
    __table_args__ = (
        Index("ix_product_asset_candidates_product_id", "product_id"),
        Index("ix_product_asset_candidates_status", "status"),
        Index("ix_product_asset_candidates_asset_type", "asset_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    supplier_key: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    asset_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown", server_default="unknown")
    title: Mapped[str | None] = mapped_column(String(500))
    filename: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(12))
    region: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", server_default="new")
    error_message: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(back_populates="asset_candidates")


class ProductSDB(TimestampMixin, Base):
    __tablename__ = "product_sdb"
    __table_args__ = (
        UniqueConstraint("product_id", name="uq_product_sdb_product_id"),
        Index("ix_product_sdb_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000))
    pdf_url: Mapped[str | None] = mapped_column(String(1000))
    source_asset_id: Mapped[int | None] = mapped_column(Integer)
    parser_status: Mapped[str | None] = mapped_column(String(64))
    parser_warnings_json: Mapped[list | dict | None] = mapped_column(JSON)
    review_status: Mapped[str | None] = mapped_column(String(64))
    version_label: Mapped[str | None] = mapped_column(String(255))
    effective_date: Mapped[str | None] = mapped_column(String(32))
    document_title: Mapped[str | None] = mapped_column(String(500))
    issuer_name: Mapped[str | None] = mapped_column(String(255))
    issuer_address_line1: Mapped[str | None] = mapped_column(String(255))
    issuer_address_line2: Mapped[str | None] = mapped_column(String(255))
    issuer_postal_code: Mapped[str | None] = mapped_column(String(32))
    issuer_city: Mapped[str | None] = mapped_column(String(255))
    issuer_country_code: Mapped[str | None] = mapped_column(String(16))
    issuer_phone: Mapped[str | None] = mapped_column(String(64))
    issuer_email: Mapped[str | None] = mapped_column(String(255))
    action_log_json: Mapped[list | dict | None] = mapped_column(JSON)
    raw_text: Mapped[str | None] = mapped_column(Text)
    sections_json: Mapped[dict | None] = mapped_column(JSON)
    generated_pdf_path: Mapped[str | None] = mapped_column(String(1000))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="sdb_record")
    llm_runs: Mapped[list["ProductSDBLLMRun"]] = relationship(
        back_populates="product_sdb",
        cascade="all, delete-orphan",
        order_by="ProductSDBLLMRun.created_at.desc()",
    )


class ProductSDBLLMRun(TimestampMixin, Base):
    __tablename__ = "product_sdb_llm_runs"
    __table_args__ = (
        Index("ix_product_sdb_llm_runs_product_sdb_id", "product_sdb_id"),
        Index("ix_product_sdb_llm_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_sdb_id: Mapped[int] = mapped_column(ForeignKey("product_sdb.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", server_default="pending")
    system_prompt: Mapped[str | None] = mapped_column(Text)
    user_prompt: Mapped[str | None] = mapped_column(Text)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    raw_response_text: Mapped[str | None] = mapped_column(Text)
    warnings_json: Mapped[list | dict | None] = mapped_column(JSON)
    error_log: Mapped[str | None] = mapped_column(Text)

    product_sdb: Mapped[ProductSDB] = relationship(back_populates="llm_runs")


class SDBTranslationPrompt(TimestampMixin, Base):
    __tablename__ = "sdb_translation_prompts"
    __table_args__ = (
        Index("ix_sdb_translation_prompts_scope", "document_type", "source_locale", "target_locale", "target_region"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, default="sds", server_default="sds")
    source_locale: Mapped[str | None] = mapped_column(String(16))
    target_locale: Mapped[str | None] = mapped_column(String(16))
    target_region: Mapped[str | None] = mapped_column(String(16))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")


class ChemicalDocument(TimestampMixin, Base):
    __tablename__ = "chemical_documents"
    __table_args__ = (
        Index("ix_chemical_documents_product_id", "product_id"),
        Index("ix_chemical_documents_source_document_id", "source_document_id"),
        Index("ix_chemical_documents_status", "status"),
        Index("ix_chemical_documents_locale_region", "locale", "region_code"),
        Index("ix_chemical_documents_product_locale_current", "product_id", "locale", "is_current"),
        Index("ix_chemical_documents_generated_at", "generated_at"),
        Index("ix_chemical_documents_source", "source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, default="sds", server_default="sds")
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("chemical_documents.id", ondelete="SET NULL"))
    locale: Mapped[str | None] = mapped_column(String(16))
    language_code: Mapped[str | None] = mapped_column(String(16))
    region_code: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(String(500))
    file_url: Mapped[str | None] = mapped_column(String(1000))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(255))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    generated_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="draft", server_default="draft")
    source: Mapped[str | None] = mapped_column(String(64), default="manual", server_default="manual")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation_log_json: Mapped[list | dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    version: Mapped[str | None] = mapped_column(String(255))
    valid_from: Mapped[str | None] = mapped_column(String(32))
    created_by_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    ai_provider: Mapped[str | None] = mapped_column(String(64))
    ai_model: Mapped[str | None] = mapped_column(String(128))
    ai_prompt_id: Mapped[int | None] = mapped_column(ForeignKey("sdb_translation_prompts.id", ondelete="SET NULL"))
    review_note: Mapped[str | None] = mapped_column(Text)
    swiss_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    compliance_score: Mapped[int | None] = mapped_column(Integer)
    source_issue_date: Mapped[str | None] = mapped_column(String(32))
    source_revision: Mapped[str | None] = mapped_column(String(64))
    ufi: Mapped[str | None] = mapped_column(String(64))
    rpc_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown", server_default="unknown")
    waste_code_ch: Mapped[str | None] = mapped_column(String(64))
    transport_review_status: Mapped[str | None] = mapped_column(String(32))
    last_ch_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="chemical_documents")
    source_document: Mapped["ChemicalDocument | None"] = relationship(remote_side=[id])
    asset: Mapped[Asset | None] = relationship()
    ai_prompt: Mapped[SDBTranslationPrompt | None] = relationship()
    sds_review_issues: Mapped[list["SDSReviewIssue"]] = relationship(back_populates="sds_version", cascade="all, delete-orphan")


class SDSReviewIssue(TimestampMixin, Base):
    __tablename__ = "sds_review_issues"
    __table_args__ = (
        Index("ix_sds_review_issues_product_id", "product_id"),
        Index("ix_sds_review_issues_sds_version_id", "sds_version_id"),
        Index("ix_sds_review_issues_severity", "severity"),
        Index("ix_sds_review_issues_status", "status"),
        Index("ix_sds_review_issues_issue_key", "issue_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sds_version_id: Mapped[int] = mapped_column(ForeignKey("chemical_documents.id", ondelete="CASCADE"), nullable=False)
    section: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    issue_key: Mapped[str] = mapped_column(String(100), nullable=False)
    current_text: Mapped[str | None] = mapped_column(Text)
    suggested_text: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    auto_fixable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", server_default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship()
    sds_version: Mapped[ChemicalDocument] = relationship(back_populates="sds_review_issues")


class SuvaLimitSource(TimestampMixin, Base):
    __tablename__ = "suva_limit_source"
    __table_args__ = (
        UniqueConstraint("sha256", name="uq_suva_limit_source_sha256"),
        Index("ix_suva_limit_source_imported_at", "imported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False, default="SUVA Grenzwerte am Arbeitsplatz", server_default="SUVA Grenzwerte am Arbeitsplatz")
    source_url: Mapped[str | None] = mapped_column(String(1000))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    imported_by: Mapped[str | None] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)

    entries: Mapped[list["SuvaLimitEntry"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    checks: Mapped[list["ProductSuvaCheck"]] = relationship(back_populates="source")


class SuvaLimitEntry(TimestampMixin, Base):
    __tablename__ = "suva_limit_entry"
    __table_args__ = (
        Index("ix_suva_limit_entry_source_id", "source_id"),
        Index("ix_suva_limit_entry_cas_number", "cas_number"),
        Index("ix_suva_limit_entry_ec_number", "ec_number"),
        Index("ix_suva_limit_entry_index_number", "index_number"),
        Index("ix_suva_limit_entry_substance_name", "substance_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("suva_limit_source.id", ondelete="CASCADE"), nullable=False)
    substance_name: Mapped[str | None] = mapped_column(String(500))
    cas_number: Mapped[str | None] = mapped_column(String(64))
    ec_number: Mapped[str | None] = mapped_column(String(64))
    index_number: Mapped[str | None] = mapped_column(String(64))
    synonyms: Mapped[list | dict | None] = mapped_column(JSON)
    mak_ppm: Mapped[str | None] = mapped_column(String(128))
    mak_mg_m3: Mapped[str | None] = mapped_column(String(128))
    kzgw_ppm: Mapped[str | None] = mapped_column(String(128))
    kzgw_mg_m3: Mapped[str | None] = mapped_column(String(128))
    bat_value: Mapped[str | None] = mapped_column(String(255))
    bat_matrix: Mapped[str | None] = mapped_column(String(255))
    notations: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    raw_row_json: Mapped[dict | list | None] = mapped_column(JSON)

    source: Mapped[SuvaLimitSource] = relationship(back_populates="entries")
    aliases: Mapped[list["SuvaSubstanceAlias"]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class SuvaSubstanceAlias(TimestampMixin, Base):
    __tablename__ = "suva_substance_alias"
    __table_args__ = (
        Index("ix_suva_substance_alias_entry_id", "entry_id"),
        Index("ix_suva_substance_alias_alias", "alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("suva_limit_entry.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(500), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")

    entry: Mapped[SuvaLimitEntry] = relationship(back_populates="aliases")


class ProductSuvaCheck(TimestampMixin, Base):
    __tablename__ = "product_suva_check"
    __table_args__ = (
        Index("ix_product_suva_check_product_id", "product_id"),
        Index("ix_product_suva_check_sds_id", "sds_id"),
        Index("ix_product_suva_check_source_id", "source_id"),
        Index("ix_product_suva_check_checked_at", "checked_at"),
        Index("ix_product_suva_check_overall_status", "overall_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    sds_id: Mapped[int | None] = mapped_column(ForeignKey("chemical_documents.id", ondelete="SET NULL"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("suva_limit_source.id", ondelete="SET NULL"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    checked_by: Mapped[str | None] = mapped_column(String(255))
    overall_status: Mapped[str] = mapped_column(String(16), nullable=False, default="BLOCKER", server_default="BLOCKER")
    report_json: Mapped[dict | list | None] = mapped_column(JSON)

    product: Mapped[Product] = relationship(back_populates="suva_checks")
    sds: Mapped[ChemicalDocument | None] = relationship()
    source: Mapped[SuvaLimitSource | None] = relationship(back_populates="checks")
    items: Mapped[list["ProductSuvaCheckItem"]] = relationship(back_populates="check", cascade="all, delete-orphan")


class ProductSuvaCheckItem(TimestampMixin, Base):
    __tablename__ = "product_suva_check_item"
    __table_args__ = (
        Index("ix_product_suva_check_item_check_id", "check_id"),
        Index("ix_product_suva_check_item_cas_number", "cas_number"),
        Index("ix_product_suva_check_item_match_status", "match_status"),
        Index("ix_product_suva_check_item_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("product_suva_check.id", ondelete="CASCADE"), nullable=False)
    ingredient_name: Mapped[str | None] = mapped_column(String(500))
    cas_number: Mapped[str | None] = mapped_column(String(64))
    ec_number: Mapped[str | None] = mapped_column(String(64))
    index_number: Mapped[str | None] = mapped_column(String(64))
    concentration: Mapped[str | None] = mapped_column(String(255))
    h_statements: Mapped[str | None] = mapped_column(Text)
    match_status: Mapped[str] = mapped_column(String(32), nullable=False)
    suva_entry_id: Mapped[int | None] = mapped_column(ForeignKey("suva_limit_entry.id", ondelete="SET NULL"))
    mak_ppm: Mapped[str | None] = mapped_column(String(128))
    mak_mg_m3: Mapped[str | None] = mapped_column(String(128))
    kzgw_ppm: Mapped[str | None] = mapped_column(String(128))
    kzgw_mg_m3: Mapped[str | None] = mapped_column(String(128))
    bat_value: Mapped[str | None] = mapped_column(String(255))
    bat_matrix: Mapped[str | None] = mapped_column(String(255))
    notations: Mapped[str | None] = mapped_column(Text)
    review_note: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="WARNING", server_default="WARNING")

    check: Mapped[ProductSuvaCheck] = relationship(back_populates="items")
    suva_entry: Mapped[SuvaLimitEntry | None] = relationship()


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        Index("ix_import_jobs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", server_default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_log: Mapped[str | None] = mapped_column(Text)

    rows: Mapped[list["ImportRow"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        Index("ix_import_rows_job_id", "job_id"),
        Index("ix_import_rows_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    row_index: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    raw_payload_json: Mapped[dict | None] = mapped_column(JSON)

    job: Mapped[ImportJob] = relationship(back_populates="rows")


class ProductMergeLog(Base):
    __tablename__ = "product_merge_logs"
    __table_args__ = (
        Index("ix_product_merge_logs_group_key", "group_key"),
        Index("ix_product_merge_logs_master_product_id", "master_product_id"),
        Index("ix_product_merge_logs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_key: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    master_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    duplicate_product_ids_json: Mapped[list | dict | None] = mapped_column(JSON)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="planned", server_default="planned")
    summary_json: Mapped[dict | list | None] = mapped_column(JSON)
    conflicts_json: Mapped[dict | list | None] = mapped_column(JSON)
    report_path: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    master_product: Mapped[Product] = relationship(back_populates="merge_logs_as_master")


class ProductDuplicateGroup(TimestampMixin, Base):
    __tablename__ = "product_duplicate_groups"
    __table_args__ = (
        UniqueConstraint("group_key", name="uq_product_duplicate_groups_group_key"),
        Index("ix_product_duplicate_groups_master_product_id", "master_product_id"),
        Index("ix_product_duplicate_groups_status", "status"),
        Index("ix_product_duplicate_groups_confidence_score", "confidence_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_key: Mapped[str] = mapped_column(String(255), nullable=False)
    master_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open", server_default="open")
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="rule", server_default="rule")
    conflict_summary: Mapped[str | None] = mapped_column(Text)
    merge_log_json: Mapped[dict | list | None] = mapped_column(JSON)
    ignore_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ignored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    master_product: Mapped[Product] = relationship(foreign_keys=[master_product_id])
    items: Mapped[list["ProductDuplicateGroupItem"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ProductDuplicateGroupItem.role.desc(), ProductDuplicateGroupItem.product_id.asc()",
    )
    previews: Mapped[list["ProductMergePreview"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="ProductMergePreview.created_at.desc()",
    )


class ProductDuplicateGroupItem(TimestampMixin, Base):
    __tablename__ = "product_duplicate_group_items"
    __table_args__ = (
        UniqueConstraint("group_id", "product_id", name="uq_product_duplicate_group_items_group_product"),
        Index("ix_product_duplicate_group_items_group_id", "group_id"),
        Index("ix_product_duplicate_group_items_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("product_duplicate_groups.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="duplicate", server_default="duplicate")
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    match_reasons_json: Mapped[dict | list | None] = mapped_column(JSON)
    conflict_details_json: Mapped[dict | list | None] = mapped_column(JSON)
    selected_for_merge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    group: Mapped[ProductDuplicateGroup] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class ProductMergePreview(TimestampMixin, Base):
    __tablename__ = "product_merge_previews"
    __table_args__ = (
        Index("ix_product_merge_previews_group_id", "group_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("product_duplicate_groups.id", ondelete="CASCADE"), nullable=False)
    preview_json: Mapped[dict | list | None] = mapped_column(JSON)
    conflict_json: Mapped[dict | list | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(255))

    group: Mapped[ProductDuplicateGroup] = relationship(back_populates="previews")
