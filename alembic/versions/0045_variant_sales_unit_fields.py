"""add variant sales unit fields

Revision ID: 0045_variant_sales_unit_fields
Revises: 0044_variant_customs_data
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0045_variant_sales_unit_fields"
down_revision = "0044_variant_customs_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_variants", sa.Column("sales_unit", sa.String(length=64), nullable=True))
    op.add_column("product_variants", sa.Column("pack_quantity", sa.Numeric(12, 3), nullable=True))
    op.add_column("product_variants", sa.Column("pack_unit", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("product_variants", "pack_unit")
    op.drop_column("product_variants", "pack_quantity")
    op.drop_column("product_variants", "sales_unit")
