"""add price lists and variant price list support

Revision ID: 0041_price_lists_and_variant_prices
Revises: 0040_performance_indexes
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0041_price_lists_and_variant_prices"
down_revision = "0040_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_lists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("price_list_type", sa.String(length=50), nullable=False, server_default="sale"),
        sa.Column("sales_channel_id", sa.Integer(), sa.ForeignKey("sales_channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_price_lists_code"),
    )
    op.create_index("ix_price_lists_sales_channel_id", "price_lists", ["sales_channel_id"], unique=False)
    op.create_index("ix_price_lists_status", "price_lists", ["status"], unique=False)

    op.add_column("product_variant_price_tiers", sa.Column("price_list_id", sa.Integer(), nullable=True))
    op.add_column("product_variant_price_tiers", sa.Column("status", sa.String(length=32), nullable=False, server_default="active"))
    op.create_foreign_key(
        "fk_variant_price_tiers_price_list_id",
        "product_variant_price_tiers",
        "price_lists",
        ["price_list_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_variant_price_tiers_price_list_id", "product_variant_price_tiers", ["price_list_id"], unique=False)
    op.create_index("ix_variant_price_tiers_status", "product_variant_price_tiers", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_variant_price_tiers_status", table_name="product_variant_price_tiers")
    op.drop_index("ix_variant_price_tiers_price_list_id", table_name="product_variant_price_tiers")
    op.drop_constraint("fk_variant_price_tiers_price_list_id", "product_variant_price_tiers", type_="foreignkey")
    op.drop_column("product_variant_price_tiers", "status")
    op.drop_column("product_variant_price_tiers", "price_list_id")

    op.drop_index("ix_price_lists_status", table_name="price_lists")
    op.drop_index("ix_price_lists_sales_channel_id", table_name="price_lists")
    op.drop_table("price_lists")
