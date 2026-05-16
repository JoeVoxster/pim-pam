"""add swiss sds review metadata and issues

Revision ID: 0034_sds_swiss_review
Revises: 0033_medusa_sync
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0034_sds_swiss_review"
down_revision = "0033_medusa_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chemical_documents", sa.Column("swiss_review_status", sa.String(length=32), nullable=False, server_default="draft"))
    op.add_column("chemical_documents", sa.Column("compliance_score", sa.Integer(), nullable=True))
    op.add_column("chemical_documents", sa.Column("source_issue_date", sa.String(length=32), nullable=True))
    op.add_column("chemical_documents", sa.Column("source_revision", sa.String(length=64), nullable=True))
    op.add_column("chemical_documents", sa.Column("ufi", sa.String(length=64), nullable=True))
    op.add_column("chemical_documents", sa.Column("rpc_status", sa.String(length=32), nullable=False, server_default="unknown"))
    op.add_column("chemical_documents", sa.Column("waste_code_ch", sa.String(length=64), nullable=True))
    op.add_column("chemical_documents", sa.Column("transport_review_status", sa.String(length=32), nullable=True))
    op.add_column("chemical_documents", sa.Column("last_ch_review_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "sds_review_issues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sds_version_id", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("issue_key", sa.String(length=100), nullable=False),
        sa.Column("current_text", sa.Text(), nullable=True),
        sa.Column("suggested_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("auto_fixable", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sds_version_id"], ["chemical_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sds_review_issues_product_id", "sds_review_issues", ["product_id"], unique=False)
    op.create_index("ix_sds_review_issues_sds_version_id", "sds_review_issues", ["sds_version_id"], unique=False)
    op.create_index("ix_sds_review_issues_severity", "sds_review_issues", ["severity"], unique=False)
    op.create_index("ix_sds_review_issues_status", "sds_review_issues", ["status"], unique=False)
    op.create_index("ix_sds_review_issues_issue_key", "sds_review_issues", ["issue_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sds_review_issues_issue_key", table_name="sds_review_issues")
    op.drop_index("ix_sds_review_issues_status", table_name="sds_review_issues")
    op.drop_index("ix_sds_review_issues_severity", table_name="sds_review_issues")
    op.drop_index("ix_sds_review_issues_sds_version_id", table_name="sds_review_issues")
    op.drop_index("ix_sds_review_issues_product_id", table_name="sds_review_issues")
    op.drop_table("sds_review_issues")

    op.drop_column("chemical_documents", "last_ch_review_at")
    op.drop_column("chemical_documents", "transport_review_status")
    op.drop_column("chemical_documents", "waste_code_ch")
    op.drop_column("chemical_documents", "rpc_status")
    op.drop_column("chemical_documents", "ufi")
    op.drop_column("chemical_documents", "source_revision")
    op.drop_column("chemical_documents", "source_issue_date")
    op.drop_column("chemical_documents", "compliance_score")
    op.drop_column("chemical_documents", "swiss_review_status")
