"""add chemical product fields

Revision ID: 0008_chemical_product_fields
Revises: 0007_category_language_code
Create Date: 2026-04-18 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from slugify import slugify


revision = "0008_chemical_product_fields"
down_revision = "0007_category_language_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("is_chemical", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("products", sa.Column("chemical_type", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("cas_number", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("ec_number", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("un_number", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("hazard_class", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("packing_group", sa.String(length=32), nullable=True))
    op.add_column("products", sa.Column("adr_relevant", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("products", sa.Column("ghs_pictograms", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("signal_word", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("hazard_statements", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("precautionary_statements", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("wgk", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("storage_class", sa.String(length=64), nullable=True))
    op.add_column("products", sa.Column("sds_available", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("products", sa.Column("sds_url", sa.String(length=1000), nullable=True))
    op.add_column("products", sa.Column("sds_asset_id", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("density", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("color", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("odor", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("ph_value", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("flash_point", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("boiling_point", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("viscosity", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("solubility", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("business_only", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("products", sa.Column("age_check_required", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("products", sa.Column("shippable", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("products", sa.Column("limited_quantity", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("hazard_shipping_note", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("shop_active", sa.Boolean(), nullable=False, server_default="1"))
    op.create_index("ix_products_is_chemical", "products", ["is_chemical"], unique=False)
    op.create_index("ix_products_cas_number", "products", ["cas_number"], unique=False)
    op.create_index("ix_products_un_number", "products", ["un_number"], unique=False)

    bind = op.get_bind()
    brand_table = sa.table(
        "brands",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
    )
    products_table = sa.table(
        "products",
        sa.column("id", sa.Integer()),
        sa.column("sku", sa.String()),
        sa.column("handle", sa.String()),
        sa.column("source_language", sa.String()),
        sa.column("title", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("brand_id", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("is_chemical", sa.Boolean()),
        sa.column("chemical_type", sa.String()),
        sa.column("cas_number", sa.String()),
        sa.column("ec_number", sa.String()),
        sa.column("un_number", sa.String()),
        sa.column("hazard_class", sa.String()),
        sa.column("packing_group", sa.String()),
        sa.column("adr_relevant", sa.Boolean()),
        sa.column("ghs_pictograms", sa.Text()),
        sa.column("signal_word", sa.String()),
        sa.column("hazard_statements", sa.Text()),
        sa.column("precautionary_statements", sa.Text()),
        sa.column("wgk", sa.String()),
        sa.column("storage_class", sa.String()),
        sa.column("sds_available", sa.Boolean()),
        sa.column("sds_url", sa.String()),
        sa.column("density", sa.String()),
        sa.column("color", sa.String()),
        sa.column("odor", sa.String()),
        sa.column("ph_value", sa.String()),
        sa.column("flash_point", sa.String()),
        sa.column("boiling_point", sa.String()),
        sa.column("viscosity", sa.String()),
        sa.column("solubility", sa.String()),
        sa.column("business_only", sa.Boolean()),
        sa.column("age_check_required", sa.Boolean()),
        sa.column("shippable", sa.Boolean()),
        sa.column("limited_quantity", sa.String()),
        sa.column("hazard_shipping_note", sa.Text()),
        sa.column("shop_active", sa.Boolean()),
    )
    variants_table = sa.table(
        "product_variants",
        sa.column("id", sa.Integer()),
        sa.column("product_id", sa.Integer()),
        sa.column("sku", sa.String()),
        sa.column("variant_title", sa.String()),
        sa.column("option_name", sa.String()),
        sa.column("option_value", sa.String()),
        sa.column("packaging", sa.String()),
        sa.column("price", sa.Numeric(12, 2)),
        sa.column("currency", sa.String()),
        sa.column("cost_price", sa.Numeric(12, 2)),
        sa.column("cost_currency", sa.String()),
        sa.column("stock_qty", sa.Integer()),
        sa.column("barcode", sa.String()),
    )

    brand_id = bind.scalar(sa.select(brand_table.c.id).where(brand_table.c.slug == "demo-chem"))
    if brand_id is None:
        bind.execute(brand_table.insert().values(name="Demo Chem", slug="demo-chem"))
        brand_id = bind.scalar(sa.select(brand_table.c.id).where(brand_table.c.slug == "demo-chem"))

    demo_sku = "CHEM-DEMO-001"
    existing_product_id = bind.scalar(sa.select(products_table.c.id).where(products_table.c.sku == demo_sku))
    if existing_product_id is None and brand_id is not None:
        bind.execute(
            products_table.insert().values(
                sku=demo_sku,
                handle=slugify("Demo Chemieprodukt Natriumhypochlorit 14", separator="-"),
                source_language="de-CH",
                title="Demo Chemieprodukt Natriumhypochlorit 14%",
                description="Demo-Datensatz für die Chemiepflege im PIM/PAM Admin.",
                brand_id=brand_id,
                status="draft",
                is_chemical=True,
                chemical_type="Oxidationsmittel",
                cas_number="7681-52-9",
                ec_number="231-668-3",
                un_number="1791",
                hazard_class="8",
                packing_group="II",
                adr_relevant=True,
                ghs_pictograms="GHS05|GHS09",
                signal_word="GEFAHR",
                hazard_statements="Verursacht schwere Verätzungen der Haut und schwere Augenschäden. Sehr giftig für Wasserorganismen.",
                precautionary_statements="Schutzhandschuhe, Schutzkleidung, Augenschutz und Gesichtsschutz tragen.",
                wgk="2",
                storage_class="8B",
                sds_available=True,
                sds_url="https://www.chemstore.swiss/de/javelle-konzentrat-14-inhalt-5-l",
                density="1.235 g/cm3",
                color="gelblich",
                odor="chlorähnlich",
                ph_value="alkalisch",
                flash_point="nicht anwendbar",
                boiling_point="nicht angegeben",
                viscosity="nicht angegeben",
                solubility="vollständig in Wasser löslich",
                business_only=True,
                age_check_required=True,
                shippable=True,
                limited_quantity="1 L",
                hazard_shipping_note="ADR: UN1791 HYPOCHLORITLÖSUNG, 8, II, (E)",
                shop_active=True,
            )
        )
        existing_product_id = bind.scalar(sa.select(products_table.c.id).where(products_table.c.sku == demo_sku))

    if existing_product_id is not None:
        existing_variant_id = bind.scalar(sa.select(variants_table.c.id).where(variants_table.c.sku == demo_sku))
        if existing_variant_id is None:
            bind.execute(
                variants_table.insert().values(
                    product_id=existing_product_id,
                    sku=demo_sku,
                    variant_title="5 L",
                    option_name="Packaging",
                    option_value="5 L",
                    packaging="5 L",
                    price=57.30,
                    currency="CHF",
                    cost_price=53.01,
                    cost_currency="CHF",
                    stock_qty=0,
                    barcode=None,
                )
            )


def downgrade() -> None:
    op.drop_index("ix_products_un_number", table_name="products")
    op.drop_index("ix_products_cas_number", table_name="products")
    op.drop_index("ix_products_is_chemical", table_name="products")
    op.drop_column("products", "shop_active")
    op.drop_column("products", "hazard_shipping_note")
    op.drop_column("products", "limited_quantity")
    op.drop_column("products", "shippable")
    op.drop_column("products", "age_check_required")
    op.drop_column("products", "business_only")
    op.drop_column("products", "solubility")
    op.drop_column("products", "viscosity")
    op.drop_column("products", "boiling_point")
    op.drop_column("products", "flash_point")
    op.drop_column("products", "ph_value")
    op.drop_column("products", "odor")
    op.drop_column("products", "color")
    op.drop_column("products", "density")
    op.drop_column("products", "sds_asset_id")
    op.drop_column("products", "sds_url")
    op.drop_column("products", "sds_available")
    op.drop_column("products", "storage_class")
    op.drop_column("products", "wgk")
    op.drop_column("products", "precautionary_statements")
    op.drop_column("products", "hazard_statements")
    op.drop_column("products", "signal_word")
    op.drop_column("products", "ghs_pictograms")
    op.drop_column("products", "adr_relevant")
    op.drop_column("products", "packing_group")
    op.drop_column("products", "hazard_class")
    op.drop_column("products", "un_number")
    op.drop_column("products", "ec_number")
    op.drop_column("products", "cas_number")
    op.drop_column("products", "chemical_type")
    op.drop_column("products", "is_chemical")
