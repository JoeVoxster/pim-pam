"""add product enrichment logs

Revision ID: 0028_product_enrichment_logs
Revises: 0027_variant_status_archive
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0028_product_enrichment_logs"
down_revision = "0027_variant_status_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "product_enrichment_logs" not in inspector.get_table_names():
        op.create_table(
            "product_enrichment_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("field_name", sa.String(length=100), nullable=False),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("source_url", sa.String(length=1000), nullable=True),
            sa.Column("source_domain", sa.String(length=255), nullable=True),
            sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="suggested"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("product_enrichment_logs")}
    if "ix_product_enrichment_logs_product_id" not in indexes:
        op.create_index("ix_product_enrichment_logs_product_id", "product_enrichment_logs", ["product_id"])
    if "ix_product_enrichment_logs_status" not in indexes:
        op.create_index("ix_product_enrichment_logs_status", "product_enrichment_logs", ["status"])
    if "ix_product_enrichment_logs_field_name" not in indexes:
        op.create_index("ix_product_enrichment_logs_field_name", "product_enrichment_logs", ["field_name"])


def downgrade() -> None:
    op.drop_index("ix_product_enrichment_logs_field_name", table_name="product_enrichment_logs")
    op.drop_index("ix_product_enrichment_logs_status", table_name="product_enrichment_logs")
    op.drop_index("ix_product_enrichment_logs_product_id", table_name="product_enrichment_logs")
    op.drop_table("product_enrichment_logs")
