"""SUVA workplace limit reference data and checks.

Revision ID: 0035_suva_limits
Revises: 0034_sds_swiss_review
Create Date: 2026-05-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0035_suva_limits"
down_revision = "0034_sds_swiss_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suva_limit_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(length=255), nullable=False, server_default="SUVA Grenzwerte am Arbeitsplatz"),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("imported_by", sa.String(length=255), nullable=True),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("sha256", name="uq_suva_limit_source_sha256"),
    )
    op.create_index("ix_suva_limit_source_sha256", "suva_limit_source", ["sha256"])
    op.create_index("ix_suva_limit_source_imported_at", "suva_limit_source", ["imported_at"])

    op.create_table(
        "suva_limit_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("suva_limit_source.id", ondelete="CASCADE"), nullable=False),
        sa.Column("substance_name", sa.String(length=500), nullable=True),
        sa.Column("cas_number", sa.String(length=64), nullable=True),
        sa.Column("ec_number", sa.String(length=64), nullable=True),
        sa.Column("index_number", sa.String(length=64), nullable=True),
        sa.Column("synonyms", sa.JSON(), nullable=True),
        sa.Column("mak_ppm", sa.String(length=128), nullable=True),
        sa.Column("mak_mg_m3", sa.String(length=128), nullable=True),
        sa.Column("kzgw_ppm", sa.String(length=128), nullable=True),
        sa.Column("kzgw_mg_m3", sa.String(length=128), nullable=True),
        sa.Column("bat_value", sa.String(length=255), nullable=True),
        sa.Column("bat_matrix", sa.String(length=255), nullable=True),
        sa.Column("notations", sa.Text(), nullable=True),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column("raw_row_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_suva_limit_entry_source_id", "suva_limit_entry", ["source_id"])
    op.create_index("ix_suva_limit_entry_cas_number", "suva_limit_entry", ["cas_number"])
    op.create_index("ix_suva_limit_entry_ec_number", "suva_limit_entry", ["ec_number"])
    op.create_index("ix_suva_limit_entry_index_number", "suva_limit_entry", ["index_number"])
    op.create_index("ix_suva_limit_entry_substance_name", "suva_limit_entry", ["substance_name"])

    op.create_table(
        "suva_substance_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("suva_limit_entry.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(length=500), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_suva_substance_alias_entry_id", "suva_substance_alias", ["entry_id"])
    op.create_index("ix_suva_substance_alias_alias", "suva_substance_alias", ["alias"])

    op.create_table(
        "product_suva_check",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sds_id", sa.Integer(), sa.ForeignKey("chemical_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("suva_limit_source.id", ondelete="SET NULL"), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_by", sa.String(length=255), nullable=True),
        sa.Column("overall_status", sa.String(length=16), nullable=False, server_default="BLOCKER"),
        sa.Column("report_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_product_suva_check_product_id", "product_suva_check", ["product_id"])
    op.create_index("ix_product_suva_check_sds_id", "product_suva_check", ["sds_id"])
    op.create_index("ix_product_suva_check_source_id", "product_suva_check", ["source_id"])
    op.create_index("ix_product_suva_check_checked_at", "product_suva_check", ["checked_at"])
    op.create_index("ix_product_suva_check_overall_status", "product_suva_check", ["overall_status"])

    op.create_table(
        "product_suva_check_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("check_id", sa.Integer(), sa.ForeignKey("product_suva_check.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_name", sa.String(length=500), nullable=True),
        sa.Column("cas_number", sa.String(length=64), nullable=True),
        sa.Column("ec_number", sa.String(length=64), nullable=True),
        sa.Column("index_number", sa.String(length=64), nullable=True),
        sa.Column("concentration", sa.String(length=255), nullable=True),
        sa.Column("h_statements", sa.Text(), nullable=True),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("suva_entry_id", sa.Integer(), sa.ForeignKey("suva_limit_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mak_ppm", sa.String(length=128), nullable=True),
        sa.Column("mak_mg_m3", sa.String(length=128), nullable=True),
        sa.Column("kzgw_ppm", sa.String(length=128), nullable=True),
        sa.Column("kzgw_mg_m3", sa.String(length=128), nullable=True),
        sa.Column("bat_value", sa.String(length=255), nullable=True),
        sa.Column("bat_matrix", sa.String(length=255), nullable=True),
        sa.Column("notations", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="WARNING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_product_suva_check_item_check_id", "product_suva_check_item", ["check_id"])
    op.create_index("ix_product_suva_check_item_cas_number", "product_suva_check_item", ["cas_number"])
    op.create_index("ix_product_suva_check_item_match_status", "product_suva_check_item", ["match_status"])
    op.create_index("ix_product_suva_check_item_severity", "product_suva_check_item", ["severity"])


def downgrade() -> None:
    op.drop_table("product_suva_check_item")
    op.drop_table("product_suva_check")
    op.drop_table("suva_substance_alias")
    op.drop_table("suva_limit_entry")
    op.drop_table("suva_limit_source")
