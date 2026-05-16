"""add product duplicate group workflow tables

Revision ID: 0026_product_duplicate_groups
Revises: 0025_product_dedupe_merge
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_product_duplicate_groups"
down_revision = "0025_product_dedupe_merge"
branch_labels = None
depends_on = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    if table_name not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "product_duplicate_groups" not in tables:
        op.create_table(
            "product_duplicate_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_key", sa.String(length=255), nullable=False),
            sa.Column("master_product_id", sa.Integer(), nullable=False),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
            sa.Column("source", sa.String(length=50), nullable=False, server_default="rule"),
            sa.Column("conflict_summary", sa.Text(), nullable=True),
            sa.Column("merge_log_json", sa.JSON(), nullable=True),
            sa.Column("ignore_reason", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("reviewed_by", sa.String(length=255), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ignored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["master_product_id"], ["products.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("group_key", name="uq_product_duplicate_groups_group_key"),
        )
    group_indexes = _index_names(sa.inspect(bind), "product_duplicate_groups")
    if "ix_product_duplicate_groups_master_product_id" not in group_indexes:
        op.create_index("ix_product_duplicate_groups_master_product_id", "product_duplicate_groups", ["master_product_id"])
    if "ix_product_duplicate_groups_status" not in group_indexes:
        op.create_index("ix_product_duplicate_groups_status", "product_duplicate_groups", ["status"])
    if "ix_product_duplicate_groups_confidence_score" not in group_indexes:
        op.create_index("ix_product_duplicate_groups_confidence_score", "product_duplicate_groups", ["confidence_score"])

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "product_duplicate_group_items" not in tables:
        op.create_table(
            "product_duplicate_group_items",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="duplicate"),
            sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
            sa.Column("match_reasons_json", sa.JSON(), nullable=True),
            sa.Column("conflict_details_json", sa.JSON(), nullable=True),
            sa.Column("selected_for_merge", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["group_id"], ["product_duplicate_groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("group_id", "product_id", name="uq_product_duplicate_group_items_group_product"),
        )
    item_indexes = _index_names(sa.inspect(bind), "product_duplicate_group_items")
    if "ix_product_duplicate_group_items_group_id" not in item_indexes:
        op.create_index("ix_product_duplicate_group_items_group_id", "product_duplicate_group_items", ["group_id"])
    if "ix_product_duplicate_group_items_product_id" not in item_indexes:
        op.create_index("ix_product_duplicate_group_items_product_id", "product_duplicate_group_items", ["product_id"])

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "product_merge_previews" not in tables:
        op.create_table(
            "product_merge_previews",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("preview_json", sa.JSON(), nullable=True),
            sa.Column("conflict_json", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["group_id"], ["product_duplicate_groups.id"], ondelete="CASCADE"),
        )
    preview_indexes = _index_names(sa.inspect(bind), "product_merge_previews")
    if "ix_product_merge_previews_group_id" not in preview_indexes:
        op.create_index("ix_product_merge_previews_group_id", "product_merge_previews", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_product_merge_previews_group_id", table_name="product_merge_previews")
    op.drop_table("product_merge_previews")
    op.drop_index("ix_product_duplicate_group_items_product_id", table_name="product_duplicate_group_items")
    op.drop_index("ix_product_duplicate_group_items_group_id", table_name="product_duplicate_group_items")
    op.drop_table("product_duplicate_group_items")
    op.drop_index("ix_product_duplicate_groups_confidence_score", table_name="product_duplicate_groups")
    op.drop_index("ix_product_duplicate_groups_status", table_name="product_duplicate_groups")
    op.drop_index("ix_product_duplicate_groups_master_product_id", table_name="product_duplicate_groups")
    op.drop_table("product_duplicate_groups")
