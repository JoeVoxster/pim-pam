"""add variant technical attributes

Revision ID: 0047_variant_technical_attributes
Revises: 0046_variant_vendor_description
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0047_variant_technical_attributes"
down_revision = "0046_variant_vendor_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variant_technical_attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("attribute_code", sa.String(length=100), nullable=False),
        sa.Column("attribute_name", sa.String(length=255), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("variant_id", "attribute_code", name="uq_variant_technical_attributes_variant_code"),
    )
    op.create_index("ix_variant_technical_attributes_variant_id", "variant_technical_attributes", ["variant_id"])
    op.create_index("ix_variant_technical_attributes_code", "variant_technical_attributes", ["attribute_code"])


def downgrade() -> None:
    op.drop_index("ix_variant_technical_attributes_code", table_name="variant_technical_attributes")
    op.drop_index("ix_variant_technical_attributes_variant_id", table_name="variant_technical_attributes")
    op.drop_table("variant_technical_attributes")
