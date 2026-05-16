"""add r2 storage configuration

Revision ID: 0032_r2_storage_config
Revises: 0031_asset_r2_metadata
Create Date: 2026-05-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0032_r2_storage_config"
down_revision = "0031_asset_r2_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "r2_storage_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="cloudflare_r2"),
        sa.Column("endpoint", sa.String(length=1000), nullable=True),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=False, server_default="auto"),
        sa.Column("access_key_id", sa.String(length=255), nullable=True),
        sa.Column("secret_access_key", sa.Text(), nullable=True),
        sa.Column("public_base_url", sa.String(length=1000), nullable=True),
        sa.Column("path_prefix", sa.String(length=255), nullable=True),
        sa.Column("storage_class", sa.String(length=64), nullable=True),
        sa.Column("max_upload_size_mb", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("allowed_file_types", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("r2_storage_configs")
