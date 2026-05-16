"""add product dedupe merge metadata

Revision ID: 0025_product_dedupe_merge
Revises: 0024_chemical_document_registry_metadata
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0025_product_dedupe_merge"
down_revision = "0024_chemical_document_registry_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    if "merged_into_product_id" not in product_columns:
        op.add_column("products", sa.Column("merged_into_product_id", sa.Integer(), nullable=True))
    if "dedupe_status" not in product_columns:
        op.add_column("products", sa.Column("dedupe_status", sa.String(length=50), nullable=True))
    if "dedupe_notes" not in product_columns:
        op.add_column("products", sa.Column("dedupe_notes", sa.Text(), nullable=True))
    if "source_refs_json" not in product_columns:
        op.add_column("products", sa.Column("source_refs_json", sa.JSON(), nullable=True))
    product_indexes = {index["name"] for index in inspector.get_indexes("products")}
    if "ix_products_merged_into_product_id" not in product_indexes:
        op.create_index("ix_products_merged_into_product_id", "products", ["merged_into_product_id"])
    if "ix_products_dedupe_status" not in product_indexes:
        op.create_index("ix_products_dedupe_status", "products", ["dedupe_status"])

    if "product_merge_logs" not in inspector.get_table_names():
        op.create_table(
            "product_merge_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_key", sa.String(length=255), nullable=False),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("master_product_id", sa.Integer(), nullable=False),
            sa.Column("duplicate_product_ids_json", sa.JSON(), nullable=True),
            sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="planned"),
            sa.Column("summary_json", sa.JSON(), nullable=True),
            sa.Column("conflicts_json", sa.JSON(), nullable=True),
            sa.Column("report_path", sa.String(length=1000), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["master_product_id"], ["products.id"], ondelete="CASCADE"),
        )
    log_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("product_merge_logs")}
    if "ix_product_merge_logs_group_key" not in log_indexes:
        op.create_index("ix_product_merge_logs_group_key", "product_merge_logs", ["group_key"])
    if "ix_product_merge_logs_master_product_id" not in log_indexes:
        op.create_index("ix_product_merge_logs_master_product_id", "product_merge_logs", ["master_product_id"])
    if "ix_product_merge_logs_status" not in log_indexes:
        op.create_index("ix_product_merge_logs_status", "product_merge_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_product_merge_logs_status", table_name="product_merge_logs")
    op.drop_index("ix_product_merge_logs_master_product_id", table_name="product_merge_logs")
    op.drop_index("ix_product_merge_logs_group_key", table_name="product_merge_logs")
    op.drop_table("product_merge_logs")
    op.drop_index("ix_products_dedupe_status", table_name="products")
    op.drop_index("ix_products_merged_into_product_id", table_name="products")
    op.drop_column("products", "source_refs_json")
    op.drop_column("products", "dedupe_notes")
    op.drop_column("products", "dedupe_status")
    op.drop_column("products", "merged_into_product_id")
