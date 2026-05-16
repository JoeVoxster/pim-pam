"""make category slugs unique per sales channel

Revision ID: 0017_categories_slug_per_channel
Revises: 0016_product_category_assignments
Create Date: 2026-04-21 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_categories_slug_per_channel"
down_revision = "0016_product_category_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            """
            SELECT sales_channel_id, slug, COUNT(*) AS row_count
            FROM categories
            GROUP BY sales_channel_id, slug
            HAVING COUNT(*) > 1
            ORDER BY sales_channel_id ASC, slug ASC
            LIMIT 10
            """
        )
    ).fetchall()
    if duplicates:
        sample = ", ".join(
            f"(channel={row[0]}, slug={row[1]}, count={row[2]})"
            for row in duplicates
        )
        raise RuntimeError(f"Kategorieslugs sind innerhalb eines Kanals nicht eindeutig: {sample}")

    op.create_index(
        "uq_categories_sales_channel_slug",
        "categories",
        ["sales_channel_id", "slug"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_categories_sales_channel_slug", table_name="categories")
