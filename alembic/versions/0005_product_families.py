"""add product family and variant option fields"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_product_families"
down_revision = "0004_asset_sort_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("family_key", sa.String(length=255), nullable=True))
    op.create_index("ix_products_family_key", "products", ["family_key"], unique=False)
    op.add_column("product_variants", sa.Column("option_name", sa.String(length=100), nullable=True))
    op.add_column("product_variants", sa.Column("option_value", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("product_variants", "option_value")
    op.drop_column("product_variants", "option_name")
    op.drop_index("ix_products_family_key", table_name="products")
    op.drop_column("products", "family_key")
