"""add chemical enrichment and sdb tables

Revision ID: 0009_chemical_enrichment_and_sdb
Revises: 0008_chemical_product_fields
Create Date: 2026-04-18 00:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_chemical_enrichment_and_sdb"
down_revision = "0008_chemical_product_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("chemical_reference_url", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("chemical_last_enriched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("products", sa.Column("chemical_enrichment_status", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("chemical_enrichment_error", sa.Text(), nullable=True))

    op.create_table(
        "product_chemical_enrichments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("reference_url", sa.String(length=1000), nullable=True),
        sa.Column("source_kind", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
        sa.Column("raw_payload_json", sa.JSON(), nullable=True),
        sa.Column("normalized_payload_json", sa.JSON(), nullable=True),
        sa.Column("document_links_json", sa.JSON(), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=True),
        sa.Column("error_log", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_chemical_enrichments_product_id",
        "product_chemical_enrichments",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_chemical_enrichments_status",
        "product_chemical_enrichments",
        ["status"],
        unique=False,
    )

    op.create_table(
        "product_sdb",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("pdf_url", sa.String(length=1000), nullable=True),
        sa.Column("source_asset_id", sa.Integer(), nullable=True),
        sa.Column("parser_status", sa.String(length=64), nullable=True),
        sa.Column("parser_warnings_json", sa.JSON(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("sections_json", sa.JSON(), nullable=True),
        sa.Column("generated_pdf_path", sa.String(length=1000), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_product_sdb_product_id"),
    )
    op.create_index("ix_product_sdb_product_id", "product_sdb", ["product_id"], unique=False)

    bind = op.get_bind()
    products_table = sa.table(
        "products",
        sa.column("id", sa.Integer()),
        sa.column("sku", sa.String()),
        sa.column("chemical_reference_url", sa.String()),
        sa.column("chemical_enrichment_status", sa.String()),
    )
    demo_product_id = bind.scalar(sa.select(products_table.c.id).where(products_table.c.sku == "CHEM-DEMO-001"))
    if demo_product_id is not None:
        bind.execute(
            products_table.update()
            .where(products_table.c.id == demo_product_id)
            .values(
                chemical_reference_url="https://www.chemstore.swiss/de/javelle-konzentrat-14-inhalt-5-l",
                chemical_enrichment_status="seeded",
            )
        )


def downgrade() -> None:
    op.drop_index("ix_product_sdb_product_id", table_name="product_sdb")
    op.drop_table("product_sdb")
    op.drop_index("ix_product_chemical_enrichments_status", table_name="product_chemical_enrichments")
    op.drop_index("ix_product_chemical_enrichments_product_id", table_name="product_chemical_enrichments")
    op.drop_table("product_chemical_enrichments")
    op.drop_column("products", "chemical_enrichment_error")
    op.drop_column("products", "chemical_enrichment_status")
    op.drop_column("products", "chemical_last_enriched_at")
    op.drop_column("products", "chemical_reference_url")
