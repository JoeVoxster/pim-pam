"""add performance indexes

Revision ID: 0040_performance_indexes
Revises: 0039_add_manufacturer_sku
Create Date: 2026-05-17
"""

from __future__ import annotations

from alembic import op


revision = "0040_performance_indexes"
down_revision = "0039_add_manufacturer_sku"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_products_status_updated_at", "products", ["status", "updated_at"], unique=False)
    op.create_index("ix_products_updated_at", "products", ["updated_at"], unique=False)
    op.create_index("ix_product_variants_status_updated_at", "product_variants", ["status", "updated_at"], unique=False)
    op.create_index("ix_product_variants_updated_at", "product_variants", ["updated_at"], unique=False)
    op.create_index("ix_assets_sort_created", "assets", ["sort_order", "created_at"], unique=False)
    op.create_index("ix_assets_created_at", "assets", ["created_at"], unique=False)
    op.create_index("ix_import_jobs_started_at", "import_jobs", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_import_jobs_started_at", table_name="import_jobs")
    op.drop_index("ix_assets_created_at", table_name="assets")
    op.drop_index("ix_assets_sort_created", table_name="assets")
    op.drop_index("ix_product_variants_updated_at", table_name="product_variants")
    op.drop_index("ix_product_variants_status_updated_at", table_name="product_variants")
    op.drop_index("ix_products_updated_at", table_name="products")
    op.drop_index("ix_products_status_updated_at", table_name="products")
