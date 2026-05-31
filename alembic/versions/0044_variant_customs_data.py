"""add variant customs data

Revision ID: 0044_variant_customs_data
Revises: 0043_calculation_parameters
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0044_variant_customs_data"
down_revision = "0043_calculation_parameters"
branch_labels = None
depends_on = None


VARIANT_COLUMNS = [
    sa.Column("customs_description_de", sa.Text(), nullable=True),
    sa.Column("customs_description_en", sa.Text(), nullable=True),
    sa.Column("origin_country", sa.String(length=2), nullable=True),
    sa.Column("material_composition", sa.Text(), nullable=True),
    sa.Column("ch_tariff_code", sa.String(length=32), nullable=True),
    sa.Column("ch_statistical_key", sa.String(length=32), nullable=True),
    sa.Column("ch_customs_unit_code", sa.String(length=32), nullable=True),
    sa.Column("ch_customs_quantity_per_unit", sa.Numeric(12, 3), nullable=True),
    sa.Column("ch_net_mass_kg", sa.Numeric(12, 3), nullable=True),
    sa.Column("ch_gross_mass_kg", sa.Numeric(12, 3), nullable=True),
    sa.Column("ch_preference_possible", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("ch_origin_proof_required", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("ch_nze_required", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("ch_nze_code", sa.String(length=64), nullable=True),
    sa.Column("ch_voc_relevant", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("eu_cn_code", sa.String(length=32), nullable=True),
    sa.Column("eu_taric_code", sa.String(length=32), nullable=True),
    sa.Column("de_import_code", sa.String(length=32), nullable=True),
    sa.Column("de_customs_unit_code", sa.String(length=32), nullable=True),
    sa.Column("de_customs_quantity_per_unit", sa.Numeric(12, 3), nullable=True),
    sa.Column("eu_export_control_required", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("dual_use_required", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("reach_relevant", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("antidumping_relevant", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("customs_notes", sa.Text(), nullable=True),
]


def upgrade() -> None:
    for column in VARIANT_COLUMNS:
        op.add_column("product_variants", column)
    op.create_index("ix_product_variants_origin_country", "product_variants", ["origin_country"], unique=False)
    op.create_index("ix_product_variants_ch_tariff_code", "product_variants", ["ch_tariff_code"], unique=False)
    op.create_index("ix_product_variants_eu_cn_code", "product_variants", ["eu_cn_code"], unique=False)
    op.create_index("ix_product_variants_eu_taric_code", "product_variants", ["eu_taric_code"], unique=False)
    op.create_index("ix_product_variants_de_import_code", "product_variants", ["de_import_code"], unique=False)

    op.create_table(
        "variant_customs_additional_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jurisdiction", sa.String(length=16), nullable=False),
        sa.Column("flow", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("variant_id", "jurisdiction", "flow", "code", name="uq_variant_customs_codes_scope"),
    )
    op.create_index("ix_variant_customs_codes_variant_id", "variant_customs_additional_codes", ["variant_id"], unique=False)
    op.create_index("ix_variant_customs_codes_scope", "variant_customs_additional_codes", ["jurisdiction", "flow", "code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_variant_customs_codes_scope", table_name="variant_customs_additional_codes")
    op.drop_index("ix_variant_customs_codes_variant_id", table_name="variant_customs_additional_codes")
    op.drop_table("variant_customs_additional_codes")
    op.drop_index("ix_product_variants_de_import_code", table_name="product_variants")
    op.drop_index("ix_product_variants_eu_taric_code", table_name="product_variants")
    op.drop_index("ix_product_variants_eu_cn_code", table_name="product_variants")
    op.drop_index("ix_product_variants_ch_tariff_code", table_name="product_variants")
    op.drop_index("ix_product_variants_origin_country", table_name="product_variants")
    for column in reversed(VARIANT_COLUMNS):
        op.drop_column("product_variants", column.name)
