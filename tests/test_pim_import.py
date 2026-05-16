import json
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Asset, Category, ImportJob, ImportRow, Product, ProductCategoryAssignment, ProductTranslation, ProductVariant, ProductVariantPriceTier
from app.etl.pim_import import run_pim_import
from app.schemas.pim import ImportMappingConfig
from app.services.pim_service import list_import_jobs


def test_run_pim_import_loads_clean_file_into_database(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    local_asset = tmp_path / "demo.txt"
    local_asset.write_text("asset", encoding="utf-8")
    clean_file = tmp_path / "products_clean.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "SKU-100",
                "variant_sku": "SKU-100",
                "supplier_name": "Tintolav",
                "brand": "Demo Brand",
                "product_name": "Demo Product",
                "product_title": "Demo Product",
                "description": "Clean description",
                "image_paths": str(local_asset),
                "extra_fields": json.dumps({"price": 11.5, "category": "Chemie > Reiniger"}),
                "status": "ok",
            }
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        summary = run_pim_import(
            session=session,
            source_name="demo.csv",
            mapping_config=ImportMappingConfig(price_column_candidates=["price"], category_columns=["category"]),
            clean_file=clean_file,
        )
        session.commit()

        assert summary["rows"] == 1

    with Session(engine, expire_on_commit=False) as session:
        products = session.scalars(select(Product)).all()
        translations = session.scalars(select(ProductTranslation)).all()
        variants = session.scalars(select(ProductVariant)).all()
        jobs = session.scalars(select(ImportJob)).all()
        rows = session.scalars(select(ImportRow)).all()
        assets = session.scalars(select(Asset)).all()
        assignments = session.scalars(select(ProductCategoryAssignment)).all()
        categories = session.scalars(select(Category)).all()

    assert len(products) == 1
    assert len(variants) == 1
    assert len(jobs) == 1
    assert len(rows) == 1
    assert len(assets) == 1
    assert len(assignments) == 1
    assert {category.sales_channel_id for category in categories} == {1}
    assert products[0].title == "Demo Product"
    assert translations[0].language_code == products[0].source_language
    assert translations[0].short_description == "Clean description"
    assert variants[0].currency == "EUR"


def test_run_pim_import_can_target_non_default_sales_channel_for_categories(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "products_pos.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "SKU-200",
                "variant_sku": "SKU-200",
                "supplier_name": "Tintolav",
                "brand": "Demo Brand",
                "product_name": "POS Product",
                "product_title": "POS Product",
                "description": "POS description",
                "extra_fields": json.dumps({"category": "POS Root > POS Child"}),
                "status": "ok",
            }
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        summary = run_pim_import(
            session=session,
            source_name="products_pos.csv",
            mapping_config=ImportMappingConfig(category_columns=["category"], sales_channel_code="pos"),
            clean_file=clean_file,
        )
        session.commit()

        categories = session.scalars(select(Category).order_by(Category.id.asc())).all()
        assignments = session.scalars(select(ProductCategoryAssignment).order_by(ProductCategoryAssignment.id.asc())).all()

    assert summary["sales_channel_code"] == "pos"
    assert {category.sales_channel_id for category in categories} == {2}
    assert {assignment.sales_channel_id for assignment in assignments} == {2}


def test_run_pim_import_uses_explicit_short_description_from_extra_fields(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "short_description.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "SKU-SHORT",
                "variant_sku": "SKU-SHORT",
                "supplier_name": "VOXSTER",
                "brand": "VOXSTER",
                "product_name": "Short Product",
                "product_title": "Short Product",
                "description": "This is a long product text that should stay the product description.",
                "extra_fields": json.dumps({"short_description": "Explicit short text.", "source_language": "de-CH"}),
                "status": "ok",
            }
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        run_pim_import(
            session=session,
            source_name="short_description.csv",
            mapping_config=ImportMappingConfig(),
            clean_file=clean_file,
        )
        session.commit()
        translation = session.scalars(select(ProductTranslation)).one()

    assert translation.language_code == "de-CH"
    assert translation.short_description == "Explicit short text."


def test_list_import_jobs_exposes_sales_channel_code(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "products_pos.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "SKU-201",
                "variant_sku": "SKU-201",
                "supplier_name": "Tintolav",
                "brand": "Demo Brand",
                "product_name": "POS Product 2",
                "product_title": "POS Product 2",
                "description": "POS description",
                "extra_fields": json.dumps({"category": "POS Root > POS Child"}),
                "status": "ok",
            }
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        run_pim_import(
            session=session,
            source_name="products_pos.csv",
            mapping_config=ImportMappingConfig(category_columns=["category"], sales_channel_code="pos"),
            clean_file=clean_file,
        )
        session.commit()
        jobs = list_import_jobs(session)

    assert jobs
    assert jobs[0]["sales_channel_code"] == "pos"


def test_run_pim_import_creates_purchase_tiers_and_cost_prices(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "tiers.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "A10-025Q",
                "variant_sku": "A10-025Q",
                "supplier_name": "Tintolav",
                "product_name": "Tonsil 25 kg.",
                "product_title": "Tonsil 25 kg.",
                "description": "Active bleaching earth",
                "extra_fields": json.dumps(
                    {
                        "import_kind": "supplier_price_list",
                        "purchase_price": 19.5,
                        "purchase_currency": "EUR",
                        "Menge_min": 1,
                        "Menge_max": 12,
                        "category": "Chemie > Pulver",
                    }
                ),
                "status": "ok",
            },
            {
                "supplier_sku": "A10-025Q",
                "variant_sku": "A10-025Q",
                "supplier_name": "Tintolav",
                "product_name": "Tonsil 25 kg.",
                "product_title": "Tonsil 25 kg.",
                "description": "Active bleaching earth",
                "extra_fields": json.dumps(
                    {
                        "import_kind": "supplier_price_list",
                        "purchase_price": 17.0,
                        "purchase_currency": "EUR",
                        "Menge_min": 13,
                        "Menge_max": 999,
                        "category": "Chemie > Pulver",
                    }
                ),
                "status": "ok",
            },
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        run_pim_import(
            session=session,
            source_name="tiers.csv",
            mapping_config=ImportMappingConfig(category_columns=["category"]),
            clean_file=clean_file,
        )
        session.commit()

    with Session(engine, expire_on_commit=False) as session:
        variant = session.scalars(select(ProductVariant)).one()
        tiers = session.scalars(select(ProductVariantPriceTier).order_by(ProductVariantPriceTier.min_qty.asc())).all()

    assert variant.cost_price == 17.0
    assert variant.cost_currency == "EUR"
    assert len(tiers) == 2
    assert tiers[0].price_type == "purchase"
    assert tiers[0].min_qty == 1
    assert tiers[1].min_qty == 13


def test_run_pim_import_groups_color_family_into_one_product(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "family.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "B01-045AR",
                "variant_sku": "B01-045AR",
                "supplier_name": "Tintolav",
                "product_name": "Marking Tape Rolls 24 mm. Orange 6 pz.",
                "product_title": "Marking Tape Rolls 24 mm. Orange 6 pz.",
                "variant_title": "Marking Tape Rolls 24 mm. Orange 6 pz.",
                "status": "ok",
                "extra_fields": json.dumps({"price": 5.5}),
            },
            {
                "supplier_sku": "B01-045BL",
                "variant_sku": "B01-045BL",
                "supplier_name": "Tintolav",
                "product_name": "Marking Tape Rolls 24 mm. Blue 6 pz.",
                "product_title": "Marking Tape Rolls 24 mm. Blue 6 pz.",
                "variant_title": "Marking Tape Rolls 24 mm. Blue 6 pz.",
                "status": "ok",
                "extra_fields": json.dumps({"price": 5.5}),
            },
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        run_pim_import(
            session=session,
            source_name="family.csv",
            mapping_config=ImportMappingConfig(price_column_candidates=["price"]),
            clean_file=clean_file,
        )
        session.commit()

    with Session(engine, expire_on_commit=False) as session:
        products = session.scalars(select(Product)).all()
        variants = session.scalars(select(ProductVariant).order_by(ProductVariant.sku.asc())).all()

    assert len(products) == 1
    assert products[0].sku == "B01-045XX"
    assert products[0].family_key == "B01-045XX"
    assert len(variants) == 2
    assert variants[0].option_name == "Color"
    assert variants[0].option_value in {"Blue", "Orange"}


def test_run_pim_import_skips_nan_price_tier_without_error(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "pim.db"
    assets_path = tmp_path / "assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(assets_path))

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    clean_file = tmp_path / "nan_tier.csv"
    pd.DataFrame(
        [
            {
                "supplier_sku": "A13-000M",
                "variant_sku": "A13-000M",
                "supplier_name": "Tintolav",
                "product_name": "P3 Pure Power Perc Box (2",
                "product_title": "P3 Pure Power Perc Box (2",
                "status": "ok",
                "extra_fields": json.dumps(
                    {
                        "import_kind": "supplier_price_list",
                        "purchase_price": "NaN",
                        "purchase_currency": "EUR",
                        "Menge_min": 1,
                        "Menge_max": 10,
                    }
                ),
            }
        ]
    ).to_csv(clean_file, index=False)

    with Session(engine, expire_on_commit=False) as session:
        summary = run_pim_import(
            session=session,
            source_name="nan_tier.csv",
            mapping_config=ImportMappingConfig(),
            clean_file=clean_file,
        )
        session.commit()

    with Session(engine, expire_on_commit=False) as session:
        variants = session.scalars(select(ProductVariant)).all()
        tiers = session.scalars(select(ProductVariantPriceTier)).all()

    assert summary["errors"] == 0
    assert len(variants) == 1
    assert len(tiers) == 0
