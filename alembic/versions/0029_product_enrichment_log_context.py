"""extend product enrichment log context

Revision ID: 0029_product_enrichment_log_context
Revises: 0028_product_enrichment_logs
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0029_product_enrichment_log_context"
down_revision = "0028_product_enrichment_logs"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns("product_enrichment_logs")}


def upgrade() -> None:
    columns = _columns()
    if "search_query" not in columns:
        op.add_column("product_enrichment_logs", sa.Column("search_query", sa.Text(), nullable=True))
    if "search_method" not in columns:
        op.add_column("product_enrichment_logs", sa.Column("search_method", sa.String(length=100), nullable=True))
    if "error_message" not in columns:
        op.add_column("product_enrichment_logs", sa.Column("error_message", sa.Text(), nullable=True))
    if "dry_run" not in columns:
        op.add_column(
            "product_enrichment_logs",
            sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "language_code" not in columns:
        op.add_column("product_enrichment_logs", sa.Column("language_code", sa.String(length=12), nullable=True))
    if "created_by" not in columns:
        op.add_column("product_enrichment_logs", sa.Column("created_by", sa.String(length=255), nullable=True))


def downgrade() -> None:
    columns = _columns()
    for column_name in ("created_by", "language_code", "dry_run", "error_message", "search_method", "search_query"):
        if column_name in columns:
            op.drop_column("product_enrichment_logs", column_name)
