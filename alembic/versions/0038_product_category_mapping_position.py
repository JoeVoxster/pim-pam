"""add channel category product position

Revision ID: 0038_product_category_mapping_position
Revises: 0037_product_category_assignment_sort_order
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0038_product_category_mapping_position"
down_revision = "0037_product_category_assignment_sort_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_category_mappings",
        sa.Column("position", sa.Integer(), nullable=False, server_default="9999"),
    )
    op.create_index("ix_product_category_mappings_channel_category_id", "product_category_mappings", ["channel_category_id"])
    op.create_index("ix_product_category_mappings_position", "product_category_mappings", ["position"])
    op.create_index(
        "ix_product_category_mappings_category_position",
        "product_category_mappings",
        ["channel_category_id", "position", "product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_category_mappings_category_position", table_name="product_category_mappings")
    op.drop_index("ix_product_category_mappings_position", table_name="product_category_mappings")
    op.drop_index("ix_product_category_mappings_channel_category_id", table_name="product_category_mappings")
    op.drop_column("product_category_mappings", "position")
