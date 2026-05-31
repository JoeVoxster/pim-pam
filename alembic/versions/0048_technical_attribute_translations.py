"""add technical attribute translations

Revision ID: 0048_technical_attribute_translations
Revises: 0047_variant_technical_attributes
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0048_technical_attribute_translations"
down_revision = "0047_variant_technical_attributes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technical_attribute_label_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attribute_code", sa.String(length=100), nullable=False),
        sa.Column("language_code", sa.String(length=12), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attribute_code", "language_code", name="uq_technical_attr_label_code_lang"),
    )
    op.create_index("ix_technical_attr_label_code", "technical_attribute_label_translations", ["attribute_code"])
    op.create_table(
        "variant_technical_attribute_value_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("technical_attribute_id", sa.Integer(), nullable=False),
        sa.Column("language_code", sa.String(length=12), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["technical_attribute_id"], ["variant_technical_attributes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("technical_attribute_id", "language_code", name="uq_variant_technical_attr_value_lang"),
    )
    op.create_index("ix_variant_technical_attr_value_attr_id", "variant_technical_attribute_value_translations", ["technical_attribute_id"])
    op.create_index("ix_variant_technical_attr_value_lang", "variant_technical_attribute_value_translations", ["language_code"])


def downgrade() -> None:
    op.drop_index("ix_variant_technical_attr_value_lang", table_name="variant_technical_attribute_value_translations")
    op.drop_index("ix_variant_technical_attr_value_attr_id", table_name="variant_technical_attribute_value_translations")
    op.drop_table("variant_technical_attribute_value_translations")
    op.drop_index("ix_technical_attr_label_code", table_name="technical_attribute_label_translations")
    op.drop_table("technical_attribute_label_translations")
