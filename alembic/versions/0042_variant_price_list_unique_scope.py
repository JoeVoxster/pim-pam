"""include price lists in variant price uniqueness

Revision ID: 0042_variant_price_list_unique_scope
Revises: 0041_price_lists_and_variant_prices
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op


revision = "0042_variant_price_list_unique_scope"
down_revision = "0041_price_lists_and_variant_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_variant_price_tiers_variant_scope", "product_variant_price_tiers", type_="unique")
    op.create_index(
        "uq_variant_price_tiers_variant_scope",
        "product_variant_price_tiers",
        ["variant_id", "price_list_id", "price_type", "currency", "min_qty", "max_qty"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_variant_price_tiers_variant_scope", table_name="product_variant_price_tiers")
    op.create_unique_constraint(
        "uq_variant_price_tiers_variant_scope",
        "product_variant_price_tiers",
        ["variant_id", "price_type", "currency", "min_qty", "max_qty"],
    )
