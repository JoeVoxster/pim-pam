"""add variant status for archive workflow

Revision ID: 0027_variant_status_archive
Revises: 0026_product_duplicate_groups
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0027_variant_status_archive"
down_revision = "0026_product_duplicate_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("product_variants")}
    if "status" not in columns:
        op.add_column("product_variants", sa.Column("status", sa.String(length=50), nullable=False, server_default="active"))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("product_variants")}
    if "ix_product_variants_status" not in indexes:
        op.create_index("ix_product_variants_status", "product_variants", ["status"])


def downgrade() -> None:
    op.drop_index("ix_product_variants_status", table_name="product_variants")
    op.drop_column("product_variants", "status")
