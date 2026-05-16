"""move product categories to channel-aware assignments

Revision ID: 0016_product_category_assignments
Revises: 0015_categories_scoped_to_voxster
Create Date: 2026-04-21 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_product_category_assignments"
down_revision = "0015_categories_scoped_to_voxster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_category_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("sales_channel_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sales_channel_id"], ["sales_channels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "category_id",
            "sales_channel_id",
            name="uq_product_category_assignments_scope",
        ),
    )
    op.create_index(
        "ix_product_category_assignments_product_id",
        "product_category_assignments",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_category_assignments_category_id",
        "product_category_assignments",
        ["category_id"],
        unique=False,
    )
    op.create_index(
        "ix_product_category_assignments_sales_channel_id",
        "product_category_assignments",
        ["sales_channel_id"],
        unique=False,
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO product_category_assignments (product_id, category_id, sales_channel_id)
            SELECT pc.product_id, pc.category_id, c.sales_channel_id
            FROM product_categories AS pc
            JOIN categories AS c ON c.id = pc.category_id
            """
        )
    )

    op.drop_table("product_categories")


def downgrade() -> None:
    op.create_table(
        "product_categories",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("product_id", "category_id"),
        sa.UniqueConstraint("product_id", "category_id", name="uq_product_categories_product_category"),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO product_categories (product_id, category_id)
            SELECT DISTINCT pca.product_id, pca.category_id
            FROM product_category_assignments AS pca
            JOIN sales_channels AS sc ON sc.id = pca.sales_channel_id
            WHERE sc.code = 'voxster'
            """
        )
    )

    op.drop_index("ix_product_category_assignments_sales_channel_id", table_name="product_category_assignments")
    op.drop_index("ix_product_category_assignments_category_id", table_name="product_category_assignments")
    op.drop_index("ix_product_category_assignments_product_id", table_name="product_category_assignments")
    op.drop_table("product_category_assignments")
