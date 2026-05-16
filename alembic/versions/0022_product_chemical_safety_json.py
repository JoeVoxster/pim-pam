"""add structured chemical safety metadata

Revision ID: 0022_product_chemical_safety_json
Revises: 0021_sdb_translation_documents
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_product_chemical_safety_json"
down_revision = "0021_sdb_translation_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("chemical_safety_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "chemical_safety_json")
