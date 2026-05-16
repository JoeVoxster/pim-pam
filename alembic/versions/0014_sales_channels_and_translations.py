"""add sales channels, channel listings, and translation extensions

Revision ID: 0014_sales_channels_and_translations
Revises: 0013_product_sdb_document_title
Create Date: 2026-04-19 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_sales_channels_and_translations"
down_revision = "0013_product_sdb_document_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("product_translations") as batch_op:
        batch_op.add_column(sa.Column("short_description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("seo_title", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("seo_description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("slug", sa.String(length=500), nullable=True))

    op.create_table(
        "variant_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("language_code", sa.String(length=12), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("option_label_override", sa.String(length=255), nullable=True),
        sa.Column("package_label", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variant_id", "language_code", name="uq_variant_translations_variant_lang"),
    )

    op.create_table(
        "sales_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_channels_code"),
        sa.UniqueConstraint("name", name="uq_sales_channels_name"),
    )
    op.create_index("ix_sales_channels_code", "sales_channels", ["code"], unique=False)

    op.create_table(
        "product_channel_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publication_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "sales_channel_id", name="uq_product_channel_listings_scope"),
    )
    op.create_index("ix_product_channel_listings_product_id", "product_channel_listings", ["product_id"], unique=False)
    op.create_index("ix_product_channel_listings_sales_channel_id", "product_channel_listings", ["sales_channel_id"], unique=False)

    op.create_table(
        "variant_channel_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("publication_status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("price_enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("shippable", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("hazardous_goods", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("limited_quantity", sa.String(length=255), nullable=True),
        sa.Column("channel_sku", sa.String(length=255), nullable=True),
        sa.Column("channel_ean", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variant_id", "sales_channel_id", name="uq_variant_channel_listings_scope"),
    )
    op.create_index("ix_variant_channel_listings_variant_id", "variant_channel_listings", ["variant_id"], unique=False)
    op.create_index("ix_variant_channel_listings_sales_channel_id", "variant_channel_listings", ["sales_channel_id"], unique=False)

    op.create_table(
        "channel_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("external_category_id", sa.String(length=255), nullable=False),
        sa.Column("external_path", sa.String(length=1000), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("required_attributes_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sales_channel_id", "external_category_id", name="uq_channel_categories_external_id"),
    )
    op.create_index("ix_channel_categories_sales_channel_id", "channel_categories", ["sales_channel_id"], unique=False)

    op.create_table(
        "product_category_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("channel_category_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_category_id"], ["channel_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "sales_channel_id", "channel_category_id", name="uq_product_category_mappings_scope"),
    )
    op.create_index("ix_product_category_mappings_product_id", "product_category_mappings", ["product_id"], unique=False)
    op.create_index("ix_product_category_mappings_sales_channel_id", "product_category_mappings", ["sales_channel_id"], unique=False)

    sales_channels = sa.table(
        "sales_channels",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        sales_channels,
        [
            {"code": "voxster", "name": "voxster.ch", "is_active": True, "sort_order": 10},
            {"code": "pos", "name": "POS", "is_active": True, "sort_order": 20},
            {"code": "chemie_shop", "name": "Chemie Shop", "is_active": True, "sort_order": 30},
            {"code": "otto", "name": "OTTO", "is_active": False, "sort_order": 40},
            {"code": "ebay", "name": "eBay", "is_active": False, "sort_order": 50},
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_product_category_mappings_sales_channel_id", table_name="product_category_mappings")
    op.drop_index("ix_product_category_mappings_product_id", table_name="product_category_mappings")
    op.drop_table("product_category_mappings")

    op.drop_index("ix_channel_categories_sales_channel_id", table_name="channel_categories")
    op.drop_table("channel_categories")

    op.drop_index("ix_variant_channel_listings_sales_channel_id", table_name="variant_channel_listings")
    op.drop_index("ix_variant_channel_listings_variant_id", table_name="variant_channel_listings")
    op.drop_table("variant_channel_listings")

    op.drop_index("ix_product_channel_listings_sales_channel_id", table_name="product_channel_listings")
    op.drop_index("ix_product_channel_listings_product_id", table_name="product_channel_listings")
    op.drop_table("product_channel_listings")

    op.drop_index("ix_sales_channels_code", table_name="sales_channels")
    op.drop_table("sales_channels")

    op.drop_table("variant_translations")

    with op.batch_alter_table("product_translations") as batch_op:
        batch_op.drop_column("slug")
        batch_op.drop_column("seo_description")
        batch_op.drop_column("seo_title")
        batch_op.drop_column("short_description")
