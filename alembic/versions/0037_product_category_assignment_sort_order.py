"""add product category assignment sort order

Revision ID: 0037_product_category_assignment_sort_order
Revises: 0036_product_chemical_ufi_voc
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0037_product_category_assignment_sort_order"
down_revision = "0036_product_chemical_ufi_voc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_category_assignments",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("product_category_assignments", "sort_order")
