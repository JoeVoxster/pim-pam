"""add manufacturer sku

Revision ID: 0039_add_manufacturer_sku
Revises: 0038_product_category_mapping_position
Create Date: 2026-05-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0039_add_manufacturer_sku"
down_revision = "0038_product_category_mapping_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_variants", sa.Column("manufacturer_sku", sa.String(length=255), nullable=True))
    op.create_index("ix_product_variants_manufacturer_sku", "product_variants", ["manufacturer_sku"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_product_variants_manufacturer_sku", table_name="product_variants")
    op.drop_column("product_variants", "manufacturer_sku")
