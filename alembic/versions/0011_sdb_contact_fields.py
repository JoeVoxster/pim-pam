"""add issuer phone and email to product_sdb

Revision ID: 0011_sdb_contact_fields
Revises: 0010_sdb_llm_runs_and_metadata
Create Date: 2026-04-18 18:40:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0011_sdb_contact_fields"
down_revision = "0010_sdb_llm_runs_and_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_sdb", sa.Column("issuer_phone", sa.String(length=64), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_email", sa.String(length=255), nullable=True))

    bind = op.get_bind()
    product_sdb = sa.table(
        "product_sdb",
        sa.column("issuer_phone", sa.String()),
        sa.column("issuer_email", sa.String()),
    )
    bind.execute(
        product_sdb.update().values(
            issuer_phone="+41 52 502 67 23",
            issuer_email="info@voxster.ch",
        )
    )


def downgrade() -> None:
    op.drop_column("product_sdb", "issuer_email")
    op.drop_column("product_sdb", "issuer_phone")
