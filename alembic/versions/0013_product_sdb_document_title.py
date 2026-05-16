"""add product_sdb document_title

Revision ID: 0013_product_sdb_document_title
Revises: 0012_sdb_action_log_json
Create Date: 2026-04-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_product_sdb_document_title"
down_revision = "0012_sdb_action_log_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_sdb", sa.Column("document_title", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("product_sdb", "document_title")
