"""add category language code

Revision ID: 0007_category_language_code
Revises: 0006_product_source_language
Create Date: 2026-04-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_category_language_code"
down_revision = "0006_product_source_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("language_code", sa.String(length=12), nullable=False, server_default="de"),
    )
    op.execute("UPDATE categories SET language_code = 'de' WHERE language_code IS NULL OR language_code = ''")


def downgrade() -> None:
    op.drop_column("categories", "language_code")
