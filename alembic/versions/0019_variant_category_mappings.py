"""add variant category mappings

Revision ID: 0019_variant_category_mappings
Revises: 0018_drop_global_category_slug_uniqueness
Create Date: 2026-04-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_variant_category_mappings"
down_revision = "0018_drop_global_category_slug_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variant_category_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("channel_category_id", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_category_id"], ["channel_categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "variant_id",
            "sales_channel_id",
            "channel_category_id",
            name="uq_variant_category_mappings_scope",
        ),
    )
    op.create_index("ix_variant_category_mappings_variant_id", "variant_category_mappings", ["variant_id"], unique=False)
    op.create_index(
        "ix_variant_category_mappings_sales_channel_id",
        "variant_category_mappings",
        ["sales_channel_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_variant_category_mappings_sales_channel_id", table_name="variant_category_mappings")
    op.drop_index("ix_variant_category_mappings_variant_id", table_name="variant_category_mappings")
    op.drop_table("variant_category_mappings")
