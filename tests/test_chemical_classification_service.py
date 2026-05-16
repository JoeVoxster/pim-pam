from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.db.base import Base
from app.schemas.pim import ProductCreate, ProductSDBUpdate, ProductUpdate, VariantCreate
from app.services.chemical_classification_service import (
    extract_wgk_storage_from_sdb,
    normalize_storage_class,
    normalize_wgk,
)
from app.services.pim_service import create_product, get_product_detail, update_product, upsert_product_sdb


def test_allowed_wgk_values_are_normalized() -> None:
    assert normalize_wgk("nwg") == "nwg"
    assert normalize_wgk("awg") == "awg"
    assert normalize_wgk("WGK2") == "WGK 2"
    assert normalize_wgk("WGK 3") == "WGK 3"


def test_allowed_storage_classes_are_normalized_and_lgk9_rejected() -> None:
    assert normalize_storage_class("LGK8B") == "8B"
    assert normalize_storage_class("Lagerklasse 8A") == "8A"
    assert normalize_storage_class("5.1B") == "5.1B"
    with pytest.raises(ValueError):
        normalize_storage_class("LGK 9")


def test_product_keeps_wgk_storage_and_adr_separate(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="CLASS-SEP", title="Class Sep", brand_name="VOXSTER", status="draft", is_chemical=True),
            VariantCreate(sku="CLASS-SEP-A", variant_title="Default"),
        )
        update_product(
            session,
            product.id,
            ProductUpdate(
                sku=product.sku,
                title=product.title,
                brand_name="VOXSTER",
                status="ready",
                source_language="de-CH",
                wgk="WGK2",
                storage_class="LGK 8B",
                hazard_class="8",
                adr_relevant=True,
                chemical_safety_json={"adr_class": "8", "adr_pictograms": ["ADR_8"]},
            ),
        )
        detail = get_product_detail(session, product.id)

    assert detail["wgk"] == "WGK 2"
    assert detail["storage_class"] == "8B"
    assert detail["hazard_class"] == "8"
    assert detail["chemical_safety_json"]["wgk"] == "WGK 2"
    assert detail["chemical_safety_json"]["storage_class"] == "8B"
    assert detail["chemical_safety_json"].get("adr_class") != detail["storage_class"]


def test_sdb_parsing_detects_wgk_and_storage_class_without_applying(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-CLASS", title="SDB Class", brand_name="VOXSTER", status="draft", is_chemical=True),
            VariantCreate(sku="SDB-CLASS-A", variant_title="Default"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                source_url="https://example.com/sdb.pdf",
                raw_text="",
                sections_json={
                    "section_7": {"title": "Handhabung und Lagerung", "content": "Lagerklasse nach TRGS 510: 8B"},
                    "section_15": {"title": "Rechtsvorschriften", "content": "Wassergefährdungsklasse WGK 2"},
                },
            ),
        )
        sdb = get_product_detail(session, product.id)["sdb"]
        proposals = extract_wgk_storage_from_sdb(sdb, existing_wgk="WGK 1", existing_storage_class=None)
        detail = get_product_detail(session, product.id)

    assert proposals["wgk"]["value"] == "WGK 2"
    assert proposals["wgk"]["source_section"] == "15.1"
    assert proposals["wgk"]["would_overwrite"] is True
    assert proposals["storage_class"]["value"] == "8B"
    assert proposals["storage_class"]["source_section"] == "7.2"
    assert detail["wgk"] is None
    assert detail["storage_class"] is None
