"""add medusa sync connector tables

Revision ID: 0033_medusa_sync
Revises: 0032_r2_storage_config
Create Date: 2026-05-05 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0033_medusa_sync"
down_revision = "0032_r2_storage_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "medusa_connection_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default="default"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("base_url", sa.String(length=1000), nullable=True),
        sa.Column("admin_path", sa.String(length=255), nullable=False, server_default="/admin"),
        sa.Column("auth_type", sa.String(length=32), nullable=False, server_default="api_token"),
        sa.Column("api_token_secret", sa.Text(), nullable=True),
        sa.Column("jwt_email", sa.String(length=255), nullable=True),
        sa.Column("jwt_password_secret", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("rate_limit_per_second", sa.Integer(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("dry_run_default", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("default_region_id", sa.String(length=255), nullable=True),
        sa.Column("default_sales_channel_id", sa.String(length=255), nullable=True),
        sa.Column("default_currency_code", sa.String(length=3), nullable=False, server_default="CHF"),
        sa.Column("default_locale", sa.String(length=12), nullable=False, server_default="de-CH"),
        sa.Column("enabled_locales", sa.JSON(), nullable=True),
        sa.Column("public_asset_base_url", sa.String(length=1000), nullable=True),
        sa.Column("product_status_default", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("product_match_policy", sa.String(length=100), nullable=False, server_default="id_handle_metadata"),
        sa.Column("variant_match_policy", sa.String(length=100), nullable=False, server_default="id_sku_metadata"),
        sa.Column("conflict_policy", sa.String(length=100), nullable=False, server_default="skip_conflicts"),
        sa.Column("pricing_strategy", sa.String(length=100), nullable=False, server_default="default_and_price_lists"),
        sa.Column("translation_strategy", sa.String(length=100), nullable=False, server_default="translation_module"),
        sa.Column("export_products", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_variants", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_options", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_categories", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("export_collections", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("export_tags", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("export_types", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("export_images", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_seo", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_metadata", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_translations", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_default_prices", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_price_lists", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_tiered_prices", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("export_inventory", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("pull_ids_after_export", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("repair_mapping_before_export", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_test_status", sa.String(length=32), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_medusa_connection_configs_name"),
    )
    op.create_index("ix_medusa_connection_configs_enabled", "medusa_connection_configs", ["enabled"], unique=False)

    op.create_table(
        "medusa_sync_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("local_entity_id", sa.Integer(), nullable=False),
        sa.Column("local_parent_id", sa.Integer(), nullable=True),
        sa.Column("medusa_id", sa.String(length=255), nullable=True),
        sa.Column("medusa_parent_id", sa.String(length=255), nullable=True),
        sa.Column("medusa_handle", sa.String(length=500), nullable=True),
        sa.Column("medusa_sku", sa.String(length=255), nullable=True),
        sa.Column("medusa_external_id", sa.String(length=500), nullable=True),
        sa.Column("locale_code", sa.String(length=12), nullable=True),
        sa.Column("price_list_code", sa.String(length=255), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("min_quantity", sa.Integer(), nullable=True),
        sa.Column("max_quantity", sa.Integer(), nullable=True),
        sa.Column("local_hash", sa.String(length=128), nullable=True),
        sa.Column("medusa_hash", sa.String(length=128), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_in_medusa_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_direction", sa.String(length=32), nullable=False, server_default="pim_to_medusa"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["medusa_connection_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "entity_type",
            "local_entity_id",
            "locale_code",
            "price_list_code",
            "currency_code",
            "min_quantity",
            "max_quantity",
            name="uq_medusa_mapping_local_scope",
        ),
    )
    op.create_index("ix_medusa_sync_mappings_connection_entity", "medusa_sync_mappings", ["connection_id", "entity_type"], unique=False)
    op.create_index("ix_medusa_sync_mappings_local", "medusa_sync_mappings", ["entity_type", "local_entity_id"], unique=False)
    op.create_index("ix_medusa_sync_mappings_medusa_id", "medusa_sync_mappings", ["connection_id", "entity_type", "medusa_id"], unique=False)
    op.create_index("ix_medusa_sync_mappings_status", "medusa_sync_mappings", ["status"], unique=False)

    op.create_table(
        "medusa_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("selected_scope", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["connection_id"], ["medusa_connection_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_medusa_sync_runs_connection_id", "medusa_sync_runs", ["connection_id"], unique=False)
    op.create_index("ix_medusa_sync_runs_status", "medusa_sync_runs", ["status"], unique=False)
    op.create_index("ix_medusa_sync_runs_mode", "medusa_sync_runs", ["mode"], unique=False)

    op.create_table(
        "medusa_sync_run_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("local_entity_id", sa.Integer(), nullable=True),
        sa.Column("medusa_id", sa.String(length=255), nullable=True),
        sa.Column("locale_code", sa.String(length=12), nullable=True),
        sa.Column("price_list_code", sa.String(length=255), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("diff", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["medusa_sync_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_medusa_sync_run_items_run_id", "medusa_sync_run_items", ["run_id"], unique=False)
    op.create_index("ix_medusa_sync_run_items_entity", "medusa_sync_run_items", ["entity_type", "local_entity_id"], unique=False)
    op.create_index("ix_medusa_sync_run_items_status", "medusa_sync_run_items", ["status"], unique=False)

    op.create_table(
        "medusa_price_list_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("local_price_list_id", sa.Integer(), nullable=True),
        sa.Column("local_price_list_code", sa.String(length=255), nullable=False),
        sa.Column("local_price_list_name", sa.String(length=500), nullable=True),
        sa.Column("medusa_price_list_id", sa.String(length=255), nullable=True),
        sa.Column("medusa_price_list_type", sa.String(length=32), nullable=True),
        sa.Column("customer_group_id", sa.String(length=255), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["medusa_connection_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "local_price_list_code", "currency_code", name="uq_medusa_price_list_mapping_scope"),
    )
    op.create_index("ix_medusa_price_list_mappings_connection_id", "medusa_price_list_mappings", ["connection_id"], unique=False)

    op.create_table(
        "medusa_locale_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("local_locale", sa.String(length=12), nullable=False),
        sa.Column("medusa_locale", sa.String(length=12), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["medusa_connection_configs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", "local_locale", name="uq_medusa_locale_mapping_scope"),
    )
    op.create_index("ix_medusa_locale_mappings_connection_id", "medusa_locale_mappings", ["connection_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_medusa_locale_mappings_connection_id", table_name="medusa_locale_mappings")
    op.drop_table("medusa_locale_mappings")
    op.drop_index("ix_medusa_price_list_mappings_connection_id", table_name="medusa_price_list_mappings")
    op.drop_table("medusa_price_list_mappings")
    op.drop_index("ix_medusa_sync_run_items_status", table_name="medusa_sync_run_items")
    op.drop_index("ix_medusa_sync_run_items_entity", table_name="medusa_sync_run_items")
    op.drop_index("ix_medusa_sync_run_items_run_id", table_name="medusa_sync_run_items")
    op.drop_table("medusa_sync_run_items")
    op.drop_index("ix_medusa_sync_runs_mode", table_name="medusa_sync_runs")
    op.drop_index("ix_medusa_sync_runs_status", table_name="medusa_sync_runs")
    op.drop_index("ix_medusa_sync_runs_connection_id", table_name="medusa_sync_runs")
    op.drop_table("medusa_sync_runs")
    op.drop_index("ix_medusa_sync_mappings_status", table_name="medusa_sync_mappings")
    op.drop_index("ix_medusa_sync_mappings_medusa_id", table_name="medusa_sync_mappings")
    op.drop_index("ix_medusa_sync_mappings_local", table_name="medusa_sync_mappings")
    op.drop_index("ix_medusa_sync_mappings_connection_entity", table_name="medusa_sync_mappings")
    op.drop_table("medusa_sync_mappings")
    op.drop_index("ix_medusa_connection_configs_enabled", table_name="medusa_connection_configs")
    op.drop_table("medusa_connection_configs")
