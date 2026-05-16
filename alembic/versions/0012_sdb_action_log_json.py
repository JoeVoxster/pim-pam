"""add action_log_json to product_sdb

Revision ID: 0012_sdb_action_log_json
Revises: 0011_sdb_contact_fields
Create Date: 2026-04-19 20:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0012_sdb_action_log_json"
down_revision = "0011_sdb_contact_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_sdb", sa.Column("action_log_json", sa.JSON(), nullable=True))

    bind = op.get_bind()
    product_sdb = sa.table(
        "product_sdb",
        sa.column("action_log_json", sa.JSON()),
    )
    bind.execute(product_sdb.update().values(action_log_json=[]))


def downgrade() -> None:
    op.drop_column("product_sdb", "action_log_json")
