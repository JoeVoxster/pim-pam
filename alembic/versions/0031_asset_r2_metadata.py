"""asset r2 metadata

Revision ID: 0031_asset_r2_metadata
Revises: 0030_product_enrichment_candidates
Create Date: 2026-05-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0031_asset_r2_metadata"
down_revision = "0030_product_enrichment_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("stored_filename", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("object_key", sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column("bucket", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("storage_provider", sa.String(length=64), nullable=False, server_default="local"))
        batch.add_column(sa.Column("file_extension", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("asset_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("title", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch.add_column(sa.Column("language_code", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("public_url", sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column("status", sa.String(length=32), nullable=False, server_default="active"))
        batch.add_column(sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_assets_object_key", "assets", ["object_key"])
    op.create_index("ix_assets_storage_provider", "assets", ["storage_provider"])
    op.create_index("ix_assets_asset_type", "assets", ["asset_type"])
    op.create_index("ix_assets_language_code", "assets", ["language_code"])
    op.create_index("ix_assets_status", "assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_index("ix_assets_language_code", table_name="assets")
    op.drop_index("ix_assets_asset_type", table_name="assets")
    op.drop_index("ix_assets_storage_provider", table_name="assets")
    op.drop_index("ix_assets_object_key", table_name="assets")
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("uploaded_at")
        batch.drop_column("status")
        batch.drop_column("public_url")
        batch.drop_column("language_code")
        batch.drop_column("description")
        batch.drop_column("title")
        batch.drop_column("asset_type")
        batch.drop_column("file_extension")
        batch.drop_column("storage_provider")
        batch.drop_column("bucket")
        batch.drop_column("object_key")
        batch.drop_column("stored_filename")
