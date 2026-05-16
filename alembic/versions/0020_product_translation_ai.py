"""add ai product translation metadata and prompts

Revision ID: 0020_product_translation_ai
Revises: 0019_variant_category_mappings
Create Date: 2026-04-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_product_translation_ai"
down_revision = "0019_variant_category_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "languages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_languages_code"),
    )
    op.create_table(
        "translation_prompts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("language_code", sa.String(length=12), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("language_code", name="uq_translation_prompts_language_code"),
    )
    with op.batch_alter_table("product_translations") as batch_op:
        batch_op.add_column(sa.Column("translation_status", sa.String(length=32), nullable=False, server_default="draft"))
        batch_op.add_column(sa.Column("source_language_code", sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column("provider", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("prompt_used", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True))

    languages = sa.table(
        "languages",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("is_default", sa.Boolean()),
    )
    op.bulk_insert(
        languages,
        [
            {"code": "de", "name": "Deutsch", "enabled": True, "is_default": True},
            {"code": "en", "name": "Englisch", "enabled": True, "is_default": False},
            {"code": "fr", "name": "Französisch", "enabled": True, "is_default": False},
            {"code": "it", "name": "Italienisch", "enabled": True, "is_default": False},
            {"code": "es", "name": "Spanisch", "enabled": True, "is_default": False},
        ],
    )


def downgrade() -> None:
    with op.batch_alter_table("product_translations") as batch_op:
        batch_op.drop_column("generated_at")
        batch_op.drop_column("prompt_used")
        batch_op.drop_column("model")
        batch_op.drop_column("provider")
        batch_op.drop_column("source_language_code")
        batch_op.drop_column("translation_status")
    op.drop_table("translation_prompts")
    op.drop_table("languages")
