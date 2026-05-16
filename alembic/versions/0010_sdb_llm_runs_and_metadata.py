"""add sdb llm runs and metadata

Revision ID: 0010_sdb_llm_runs_and_metadata
Revises: 0009_chemical_enrichment_and_sdb
Create Date: 2026-04-18 14:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0010_sdb_llm_runs_and_metadata"
down_revision = "0009_chemical_enrichment_and_sdb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_sdb", sa.Column("review_status", sa.String(length=64), nullable=True))
    op.add_column("product_sdb", sa.Column("version_label", sa.String(length=64), nullable=True))
    op.add_column("product_sdb", sa.Column("effective_date", sa.String(length=32), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_name", sa.String(length=255), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_address_line1", sa.String(length=255), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_address_line2", sa.String(length=255), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_postal_code", sa.String(length=32), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_city", sa.String(length=255), nullable=True))
    op.add_column("product_sdb", sa.Column("issuer_country_code", sa.String(length=16), nullable=True))

    op.create_table(
        "product_sdb_llm_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_sdb_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("user_prompt", sa.Text(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("raw_response_text", sa.Text(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("error_log", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_sdb_id"], ["product_sdb.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_sdb_llm_runs_product_sdb_id", "product_sdb_llm_runs", ["product_sdb_id"], unique=False)
    op.create_index("ix_product_sdb_llm_runs_status", "product_sdb_llm_runs", ["status"], unique=False)

    bind = op.get_bind()
    product_sdb = sa.table(
        "product_sdb",
        sa.column("issuer_name", sa.String()),
        sa.column("issuer_address_line1", sa.String()),
        sa.column("issuer_postal_code", sa.String()),
        sa.column("issuer_city", sa.String()),
        sa.column("issuer_country_code", sa.String()),
        sa.column("review_status", sa.String()),
        sa.column("version_label", sa.String()),
    )
    bind.execute(
        product_sdb.update().values(
            issuer_name="VOXSTER GmbH",
            issuer_address_line1="Obere Ifangstrasse 10",
            issuer_postal_code="8215",
            issuer_city="Hallau",
            issuer_country_code="CH",
            review_status="review_required",
            version_label="Entwurf 1.0",
        )
    )


def downgrade() -> None:
    op.drop_index("ix_product_sdb_llm_runs_status", table_name="product_sdb_llm_runs")
    op.drop_index("ix_product_sdb_llm_runs_product_sdb_id", table_name="product_sdb_llm_runs")
    op.drop_table("product_sdb_llm_runs")
    op.drop_column("product_sdb", "issuer_country_code")
    op.drop_column("product_sdb", "issuer_city")
    op.drop_column("product_sdb", "issuer_postal_code")
    op.drop_column("product_sdb", "issuer_address_line2")
    op.drop_column("product_sdb", "issuer_address_line1")
    op.drop_column("product_sdb", "issuer_name")
    op.drop_column("product_sdb", "effective_date")
    op.drop_column("product_sdb", "version_label")
    op.drop_column("product_sdb", "review_status")
