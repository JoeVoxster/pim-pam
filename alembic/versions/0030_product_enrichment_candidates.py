"""product enrichment candidates

Revision ID: 0030_product_enrichment_candidates
Revises: 0029_product_enrichment_log_context
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0030_product_enrichment_candidates"
down_revision = "0029_product_enrichment_log_context"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "product_enrichment_candidates" not in tables:
        op.create_table(
            "product_enrichment_candidates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("supplier_key", sa.String(length=100), nullable=True),
            sa.Column("source_url", sa.String(length=1000), nullable=True),
            sa.Column("source_domain", sa.String(length=255), nullable=True),
            sa.Column("source_language", sa.String(length=12), nullable=True),
            sa.Column("source_locale", sa.String(length=12), nullable=True),
            sa.Column("target_locale", sa.String(length=12), nullable=True),
            sa.Column("field_name", sa.String(length=100), nullable=False),
            sa.Column("section_name", sa.String(length=255), nullable=True),
            sa.Column("source_value", sa.Text(), nullable=True),
            sa.Column("suggested_value", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="new", nullable=False),
            sa.Column("warning", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_product_enrichment_candidates_product_id", "product_enrichment_candidates", ["product_id"])
        op.create_index("ix_product_enrichment_candidates_status", "product_enrichment_candidates", ["status"])
        op.create_index("ix_product_enrichment_candidates_field_name", "product_enrichment_candidates", ["field_name"])
    if "product_asset_candidates" not in tables:
        op.create_table(
            "product_asset_candidates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("supplier_key", sa.String(length=100), nullable=True),
            sa.Column("source_url", sa.String(length=1000), nullable=True),
            sa.Column("asset_url", sa.String(length=1000), nullable=False),
            sa.Column("asset_type", sa.String(length=64), server_default="unknown", nullable=False),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("filename", sa.String(length=500), nullable=True),
            sa.Column("language", sa.String(length=12), nullable=True),
            sa.Column("region", sa.String(length=32), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="new", nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_product_asset_candidates_product_id", "product_asset_candidates", ["product_id"])
        op.create_index("ix_product_asset_candidates_status", "product_asset_candidates", ["status"])
        op.create_index("ix_product_asset_candidates_asset_type", "product_asset_candidates", ["asset_type"])


def downgrade() -> None:
    tables = _tables()
    if "product_asset_candidates" in tables:
        op.drop_index("ix_product_asset_candidates_asset_type", table_name="product_asset_candidates")
        op.drop_index("ix_product_asset_candidates_status", table_name="product_asset_candidates")
        op.drop_index("ix_product_asset_candidates_product_id", table_name="product_asset_candidates")
        op.drop_table("product_asset_candidates")
    if "product_enrichment_candidates" in tables:
        op.drop_index("ix_product_enrichment_candidates_field_name", table_name="product_enrichment_candidates")
        op.drop_index("ix_product_enrichment_candidates_status", table_name="product_enrichment_candidates")
        op.drop_index("ix_product_enrichment_candidates_product_id", table_name="product_enrichment_candidates")
        op.drop_table("product_enrichment_candidates")

