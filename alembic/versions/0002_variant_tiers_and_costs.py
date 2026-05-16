"""add variant price tiers and cost prices"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_variant_tiers_and_costs"
down_revision = "0001_initial_pim"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_variants", sa.Column("cost_price", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("product_variants", sa.Column("cost_currency", sa.String(length=3), nullable=True))
    op.create_table(
        "product_variant_price_tiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("price_type", sa.String(length=20), server_default="sale", nullable=False),
        sa.Column("min_qty", sa.Integer(), server_default="1", nullable=False),
        sa.Column("max_qty", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "variant_id",
            "price_type",
            "currency",
            "min_qty",
            "max_qty",
            name="uq_variant_price_tiers_variant_scope",
        ),
    )
    op.create_index("ix_variant_price_tiers_variant_id", "product_variant_price_tiers", ["variant_id"], unique=False)
    op.create_index("ix_variant_price_tiers_price_type", "product_variant_price_tiers", ["price_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_variant_price_tiers_price_type", table_name="product_variant_price_tiers")
    op.drop_index("ix_variant_price_tiers_variant_id", table_name="product_variant_price_tiers")
    op.drop_table("product_variant_price_tiers")
    op.drop_column("product_variants", "cost_currency")
    op.drop_column("product_variants", "cost_price")
