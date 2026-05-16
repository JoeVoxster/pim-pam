"""add enrichment fields for source urls and packaging"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_enrichment_fields"
down_revision = "0002_variant_tiers_and_costs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("source_url", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("source_url_final", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("specifications_text", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("technical_features_text", sa.Text(), nullable=True))
    op.add_column("product_variants", sa.Column("packaging", sa.String(length=255), nullable=True))
    op.add_column("assets", sa.Column("source_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "source_url")
    op.drop_column("product_variants", "packaging")
    op.drop_column("products", "technical_features_text")
    op.drop_column("products", "specifications_text")
    op.drop_column("products", "source_url_final")
    op.drop_column("products", "source_url")
