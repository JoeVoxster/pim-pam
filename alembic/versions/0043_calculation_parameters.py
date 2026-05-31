"""add calculation currency rates and surcharges

Revision ID: 0043_calculation_parameters
Revises: 0042_variant_price_list_unique_scope
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0043_calculation_parameters"
down_revision = "0042_variant_price_list_unique_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "currency_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_currency", sa.String(length=3), nullable=False),
        sa.Column("target_currency", sa.String(length=3), nullable=False),
        sa.Column("effective_rate", sa.Numeric(12, 6), nullable=False),
        sa.Column("markup_percent", sa.Numeric(7, 3), nullable=False, server_default="0"),
        sa.Column("used_rate", sa.Numeric(12, 6), nullable=False),
        sa.Column("rate_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_currency", "target_currency", name="uq_currency_rates_pair"),
    )
    op.create_index("ix_currency_rates_status", "currency_rates", ["status"], unique=False)

    op.create_table(
        "cost_surcharges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("surcharge_type", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=64), nullable=False, server_default="global"),
        sa.Column("scope_value", sa.String(length=255), nullable=True),
        sa.Column("percent", sa.Numeric(7, 3), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("code", name="uq_cost_surcharges_code"),
    )
    op.create_index("ix_cost_surcharges_scope", "cost_surcharges", ["scope_type", "scope_value"], unique=False)
    op.create_index("ix_cost_surcharges_type_status", "cost_surcharges", ["surcharge_type", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cost_surcharges_type_status", table_name="cost_surcharges")
    op.drop_index("ix_cost_surcharges_scope", table_name="cost_surcharges")
    op.drop_table("cost_surcharges")
    op.drop_index("ix_currency_rates_status", table_name="currency_rates")
    op.drop_table("currency_rates")
