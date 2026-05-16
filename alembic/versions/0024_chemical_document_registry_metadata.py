"""extend chemical document registry metadata

Revision ID: 0024_chemical_document_registry_metadata
Revises: 0023_wgk_storage_class_metadata
Create Date: 2026-04-30 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0024_chemical_document_registry_metadata"
down_revision = "0023_wgk_storage_class_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chemical_documents", sa.Column("source", sa.String(length=64), nullable=True, server_default="manual"))
    op.add_column("chemical_documents", sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chemical_documents", sa.Column("generation_log_json", sa.JSON(), nullable=True))
    op.add_column("chemical_documents", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column("chemical_documents", sa.Column("is_current", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("chemical_documents", sa.Column("filename", sa.String(length=255), nullable=True))
    op.add_column("chemical_documents", sa.Column("mime_type", sa.String(length=255), nullable=True))
    op.create_index("ix_chemical_documents_product_locale_current", "chemical_documents", ["product_id", "locale", "is_current"])
    op.create_index("ix_chemical_documents_generated_at", "chemical_documents", ["generated_at"])
    op.create_index("ix_chemical_documents_source", "chemical_documents", ["source"])


def downgrade() -> None:
    op.drop_index("ix_chemical_documents_source", table_name="chemical_documents")
    op.drop_index("ix_chemical_documents_generated_at", table_name="chemical_documents")
    op.drop_index("ix_chemical_documents_product_locale_current", table_name="chemical_documents")
    op.drop_column("chemical_documents", "mime_type")
    op.drop_column("chemical_documents", "filename")
    op.drop_column("chemical_documents", "is_current")
    op.drop_column("chemical_documents", "error_message")
    op.drop_column("chemical_documents", "generation_log_json")
    op.drop_column("chemical_documents", "generated_at")
    op.drop_column("chemical_documents", "source")
