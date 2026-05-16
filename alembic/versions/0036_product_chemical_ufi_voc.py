"""add product chemical UFI and VOC fields

Revision ID: 0036_product_chemical_ufi_voc
Revises: 0035_suva_limits
Create Date: 2026-05-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0036_product_chemical_ufi_voc"
down_revision = "0035_suva_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("ufi", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("voc_content_percent", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "voc_content_percent")
    op.drop_column("products", "ufi")
