"""scope existing categories to voxster sales channel

Revision ID: 0015_categories_scoped_to_voxster
Revises: 0014_sales_channels_and_translations
Create Date: 2026-04-21 09:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_categories_scoped_to_voxster"
down_revision = "0014_sales_channels_and_translations"
branch_labels = None
depends_on = None


def _ensure_voxster_channel(bind) -> int:
    sales_channels = sa.table(
        "sales_channels",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    voxster_id = bind.execute(
        sa.select(sales_channels.c.id).where(sales_channels.c.code == "voxster")
    ).scalar()
    if voxster_id is not None:
        return int(voxster_id)
    result = bind.execute(
        sales_channels.insert().values(
            code="voxster",
            name="voxster.ch",
            is_active=True,
            sort_order=10,
        )
    )
    return int(result.inserted_primary_key[0])


def upgrade() -> None:
    bind = op.get_bind()
    voxster_id = _ensure_voxster_channel(bind)

    with op.batch_alter_table("categories") as batch_op:
        batch_op.add_column(sa.Column("sales_channel_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_categories_sales_channel_id", ["sales_channel_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_categories_sales_channel_id",
            "sales_channels",
            ["sales_channel_id"],
            ["id"],
            ondelete="CASCADE",
        )

    categories = sa.table(
        "categories",
        sa.column("sales_channel_id", sa.Integer()),
    )
    bind.execute(
        categories.update()
        .where(categories.c.sales_channel_id.is_(None))
        .values(sales_channel_id=voxster_id)
    )

    with op.batch_alter_table("categories") as batch_op:
        batch_op.alter_column("sales_channel_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("categories") as batch_op:
        batch_op.drop_constraint("fk_categories_sales_channel_id", type_="foreignkey")
        batch_op.drop_index("ix_categories_sales_channel_id")
        batch_op.drop_column("sales_channel_id")
