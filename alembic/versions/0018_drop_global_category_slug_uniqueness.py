"""drop legacy global uniqueness on category slug

Revision ID: 0018_drop_global_category_slug_uniqueness
Revises: 0017_categories_slug_per_channel
Create Date: 2026-04-21 20:20:00.000000
"""

from alembic import op


revision = "0018_drop_global_category_slug_uniqueness"
down_revision = "0017_categories_slug_per_channel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("categories_slug_key", "categories", type_="unique")
        return

    index_rows = bind.exec_driver_sql("PRAGMA index_list('categories')").fetchall()
    for row in index_rows:
        index_name = row[1]
        if index_name == "ix_categories_slug":
            op.drop_index("ix_categories_slug", table_name="categories")
            op.create_index("ix_categories_slug", "categories", ["slug"], unique=False)
            return
    op.create_index("ix_categories_slug", "categories", ["slug"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_unique_constraint("categories_slug_key", "categories", ["slug"])
        return

    index_rows = bind.exec_driver_sql("PRAGMA index_list('categories')").fetchall()
    for row in index_rows:
        index_name = row[1]
        if index_name == "ix_categories_slug":
            op.drop_index("ix_categories_slug", table_name="categories")
            break
    op.create_index("ix_categories_slug", "categories", ["slug"], unique=True)
