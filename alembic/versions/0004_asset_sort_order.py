"""add sort order to assets"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_asset_sort_order"
down_revision = "0003_enrichment_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("assets", "sort_order")
