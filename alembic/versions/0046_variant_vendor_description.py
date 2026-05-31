"""add variant vendor description

Revision ID: 0046_variant_vendor_description
Revises: 0045_variant_sales_unit_fields
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0046_variant_vendor_description"
down_revision = "0045_variant_sales_unit_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_variants", sa.Column("vendor_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("product_variants", "vendor_description")
