"""add product source language

Revision ID: 0006_product_source_language
Revises: 0005_product_families
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_product_source_language"
down_revision = "0005_product_families"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("source_language", sa.String(length=12), nullable=False, server_default="en"),
    )
    op.execute("UPDATE products SET source_language = 'en' WHERE source_language IS NULL OR source_language = ''")


def downgrade() -> None:
    op.drop_column("products", "source_language")
