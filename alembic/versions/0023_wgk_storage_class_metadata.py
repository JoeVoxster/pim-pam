"""add wgk and storage class metadata

Revision ID: 0023_wgk_storage_class_metadata
Revises: 0022_product_chemical_safety_json
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0023_wgk_storage_class_metadata"
down_revision = "0022_product_chemical_safety_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("wgk_label", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("wgk_source_section", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("wgk_source_url", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("wgk_source_asset_id", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("wgk_confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("products", sa.Column("wgk_last_enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("products", sa.Column("storage_class_label", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("storage_class_source_section", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("storage_class_source_url", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("storage_class_source_asset_id", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("storage_class_confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("products", sa.Column("storage_class_last_enriched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "storage_class_last_enriched_at")
    op.drop_column("products", "storage_class_confidence")
    op.drop_column("products", "storage_class_source_asset_id")
    op.drop_column("products", "storage_class_source_url")
    op.drop_column("products", "storage_class_source_section")
    op.drop_column("products", "storage_class_label")
    op.drop_column("products", "wgk_last_enriched_at")
    op.drop_column("products", "wgk_confidence")
    op.drop_column("products", "wgk_source_asset_id")
    op.drop_column("products", "wgk_source_url")
    op.drop_column("products", "wgk_source_section")
    op.drop_column("products", "wgk_label")
