from decimal import Decimal

import fitz
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Asset, ChemicalDocument
from app.services.asset_service import create_asset_record
from app.schemas.pim import (
    ChannelCategoryUpsert,
    ProductCategoryMappingUpsert,
    ProductChannelListingUpdate,
    ProductCreate,
    ProductSDBUpdate,
    ProductTranslationCreate,
    ProductUpdate,
    VariantCategoryMappingUpsert,
    VariantChannelListingUpdate,
    VariantCreate,
    VariantPriceTierCreate,
    VariantTranslationCreate,
    VariantUpdate,
)
from app.services.pim_service import (
    bulk_update_products,
    bulk_update_variants,
    bulk_upsert_product_category_mappings,
    bulk_upsert_product_channel_listings,
    bulk_upsert_variant_category_mappings,
    bulk_upsert_variant_channel_listings,
    create_category,
    create_or_update_translation,
    create_or_update_variant_translation,
    create_product,
    delete_assets,
    delete_category,
    ensure_default_sales_channels,
    export_channel_rows,
    export_medusa_products,
    get_channel_category_tree,
    get_or_create_categories,
    get_product_detail,
    get_products_for_category,
    get_products_for_channel_category,
    get_product_sdb,
    get_category_detail,
    archive_variants,
    delete_or_archive_variants,
    list_categories,
    list_channel_categories,
    list_channel_export_rows,
    list_chemical_products,
    list_product_category_assignments,
    list_products,
    list_variants,
    list_variant_category_mappings,
    list_sales_channels,
    product_ids_for_variants,
    set_product_translation_short_description,
    set_product_categories,
    set_product_categories_for_channel,
    update_category,
    update_product,
    update_variant,
    update_variant_translation_by_id,
    upsert_channel_category,
    upsert_product_category_mapping,
    upsert_product_channel_listing,
    upsert_variant_category_mapping,
    upsert_product_sdb,
    upsert_product_with_variant,
    upsert_variant_channel_listing,
    upsert_variant_price_tier,
    variant_ids_for_products,
)
from app.services import product_translation_service
from app.services import sdb_translation_service
from app.services.product_translation_service import (
    _call_openai_variant_translation_json,
    _call_openai_translation_json,
    generate_product_translations,
    list_languages,
    list_translation_prompts,
    save_translation_prompt,
)
from app.services.sdb_translation_service import (
    _validate_sdb_translation_payload,
    backfill_sdb_documents_from_assets,
    delete_chemical_document,
    generate_sdb_translation_draft,
    list_sdb_documents_for_product,
    list_sdb_translation_prompts,
    save_sdb_translation_prompt,
    sync_product_sdb_working_document,
)
from app.services.chemical_enrichment_service import ingest_product_sdb_asset
from app.ui.dash_app import CHEMICAL_DOCUMENT_COLUMNS
from app.services.product_dedupe_service import (
    analyze_product_duplicates,
    create_duplicate_group_preview,
    get_duplicate_group_detail,
    ignore_duplicate_group,
    merge_duplicate_group,
    merge_product_duplicates,
    scan_duplicate_groups,
    set_duplicate_group_master,
)
from app.services import product_data_enrichment_service
from app.services.product_data_enrichment_service import (
    apply_product_data_enrichment,
    preview_product_data_enrichment,
)
from app.services.product_text_enrichment_service import (
    TextEnrichmentOptions,
    apply_product_text_enrichment,
    format_markdown_description,
    preview_product_text_enrichment,
)
from app.services.r2_config_service import save_r2_config
from app.db.models import ProductAssetCandidate, ProductEnrichmentCandidate, ProductEnrichmentLog, ProductTranslation


def test_pim_service_create_and_update_product(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(sku="SKU-1", title="Demo Product", brand_name="Brand A", status="draft"),
            VariantCreate(sku="SKU-1", variant_title="Default Variant"),
        )
        session.commit()

        update_product(
            session,
            product.id,
            ProductUpdate(
                title="Demo Product Updated",
                brand_name="Brand B",
                status="active",
                category_ids=[],
                is_chemical=True,
                chemical_type="Lauge",
                cas_number="7681-52-9",
                un_number="1791",
                adr_relevant=True,
                sds_available=True,
                business_only=True,
            ),
        )
        update_variant(
            session,
            variant.id,
            VariantUpdate(
                variant_title="Updated Variant",
                price="19.99",
                cost_price="9.99",
                currency="EUR",
                cost_currency="EUR",
                stock_qty=5,
                barcode="123",
            ),
        )
        upsert_variant_price_tier(
            session,
            VariantPriceTierCreate(variant_id=variant.id, min_qty=1, max_qty=9, price="8.50", currency="EUR", price_type="purchase"),
        )
        upsert_variant_price_tier(
            session,
            VariantPriceTierCreate(variant_id=variant.id, min_qty=10, max_qty=19, price="17.99", currency="EUR", price_type="sale"),
        )
        upsert_variant_price_tier(
            session,
            VariantPriceTierCreate(variant_id=variant.id, min_qty=10, max_qty=19, price="7.50", currency="EUR", price_type="purchase"),
        )
        session.commit()

        detail = get_product_detail(session, product.id)
        chemistry_rows = list_chemical_products(session)

    assert detail is not None
    assert detail["title"] == "Demo Product Updated"
    assert detail["brand_name"] == "Brand B"
    assert detail["is_chemical"] is True
    assert detail["chemical_type"] == "Lauge"
    assert detail["cas_number"] == "7681-52-9"
    assert detail["variants"][0]["variant_title"] == "Updated Variant"
    assert detail["variants"][0]["status"] == "active"
    assert detail["variants"][0]["currency"] == "EUR"
    assert detail["variants"][0]["cost_price"] == 9.99
    assert detail["variants"][0]["margin_percent"] == 50.03
    assert detail["variants"][0]["price_tiers"][1]["min_qty"] == 10
    assert detail["variants"][0]["price_tiers"][1]["margin_amount"] == 10.49
    assert detail["variants"][0]["price_tiers"][1]["margin_percent"] == 58.31
    assert len(chemistry_rows) == 1
    assert chemistry_rows[0]["cas_number"] == "7681-52-9"


def test_set_product_translation_short_description_preserves_existing_translation_fields(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SHORT-1", title="Kurzbeschreibung Produkt", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SHORT-1-A", variant_title="Default"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Bestehender Titel",
                description="Bestehende Langbeschreibung",
                seo_title="Bestehender SEO-Titel",
                slug="bestehender-slug",
            ),
        )
        set_product_translation_short_description(session, product.id, "de-CH", product.title, "Neue Kurzbeschreibung")
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "de-CH")
    assert translation["short_description"] == "Neue Kurzbeschreibung"
    assert translation["description"] == "Bestehende Langbeschreibung"
    assert translation["seo_title"] == "Bestehender SEO-Titel"
    assert translation["slug"] == "bestehender-slug"


def test_create_or_update_translation_persists_seo_description_change(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SEO-DESC-1", title="SEO Produkt", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SEO-DESC-1-A", variant_title="Default"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="SEO Produkt",
                short_description="Kurz",
                description="Lang",
                seo_title="SEO Titel",
                seo_description="Alte SEO Beschreibung",
                slug="seo-produkt",
            ),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="SEO Produkt",
                short_description="Kurz",
                description="Lang",
                seo_title="SEO Titel",
                seo_description="Neue SEO Beschreibung",
                slug="seo-produkt",
            ),
        )
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "de-CH")
    assert translation["seo_description"] == "Neue SEO Beschreibung"


def test_create_or_update_translation_empty_optional_fields_do_not_clear_existing_values(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SEO-DESC-2", title="SEO Produkt 2", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SEO-DESC-2-A", variant_title="Default"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Bestehender Titel",
                short_description="Bestehende Kurzbeschreibung",
                description="Bestehende Langbeschreibung",
                seo_title="Bestehender SEO-Titel",
                seo_description="Bestehende SEO-Beschreibung",
                slug="bestehender-slug",
            ),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Neuer Titel",
                short_description="",
                description="",
                seo_title="",
                seo_description="",
                slug="",
            ),
        )
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "de-CH")
    assert translation["title"] == "Neuer Titel"
    assert translation["short_description"] == "Bestehende Kurzbeschreibung"
    assert translation["description"] == "Bestehende Langbeschreibung"
    assert translation["seo_title"] == "Bestehender SEO-Titel"
    assert translation["seo_description"] == "Bestehende SEO-Beschreibung"
    assert translation["slug"] == "bestehender-slug"


def test_create_or_update_translation_does_not_touch_other_locales(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SEO-DESC-3", title="SEO Produkt 3", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SEO-DESC-3-A", variant_title="Default"),
        )
        create_or_update_translation(session, ProductTranslationCreate(product_id=product.id, language_code="de-CH", title="Deutsch", seo_description="Deutsch SEO"))
        create_or_update_translation(session, ProductTranslationCreate(product_id=product.id, language_code="en", title="English", seo_description="English SEO"))
        create_or_update_translation(session, ProductTranslationCreate(product_id=product.id, language_code="de-CH", title="Deutsch neu", seo_description="Deutsch SEO neu"))
        session.commit()
        detail = get_product_detail(session, product.id)

    translations = {row["language_code"]: row for row in detail["translations"]}
    assert translations["de-CH"]["seo_description"] == "Deutsch SEO neu"
    assert translations["en"]["seo_description"] == "English SEO"


def test_create_or_update_variant_translation_updates_existing_and_reloads(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(sku="VT-1", title="Variant Translation Product", source_language="de-CH", status="ready"),
            VariantCreate(sku="VT-1-A", variant_title="Original Variant"),
        )
        existing = create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Alter Variantentitel",
                option_label_override="Altes Optionslabel",
                package_label="Altes Gebindelabel",
            ),
        )
        session.commit()

        updated = create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Neuer Variantentitel",
                option_label_override="Neues Optionslabel",
                package_label="Neues Gebindelabel",
            ),
        )
        session.commit()
        detail = get_product_detail(session, product.id)

    assert updated.id == existing.id
    variant_detail = next(row for row in detail["variants"] if row["id"] == variant.id)
    translation = next(row for row in variant_detail["translations"] if row["language_code"] == "de-CH")
    assert translation["id"] == existing.id
    assert translation["title"] == "Neuer Variantentitel"
    assert translation["option_label_override"] == "Neues Optionslabel"
    assert translation["package_label"] == "Neues Gebindelabel"


def test_create_or_update_variant_translation_empty_optional_fields_do_not_clear_existing_values(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(sku="VT-2", title="Variant Translation Product 2", source_language="de-CH", status="ready"),
            VariantCreate(sku="VT-2-A", variant_title="Original Variant"),
        )
        create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Alter Variantentitel",
                option_label_override="Bestehendes Optionslabel",
                package_label="Bestehendes Gebindelabel",
            ),
        )
        create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Neuer Variantentitel",
                option_label_override="",
                package_label="",
            ),
        )
        session.commit()
        detail = get_product_detail(session, product.id)

    variant_detail = next(row for row in detail["variants"] if row["id"] == variant.id)
    translation = next(row for row in variant_detail["translations"] if row["language_code"] == "de-CH")
    assert translation["title"] == "Neuer Variantentitel"
    assert translation["option_label_override"] == "Bestehendes Optionslabel"
    assert translation["package_label"] == "Bestehendes Gebindelabel"


def test_create_or_update_variant_translation_does_not_touch_other_locales_and_creates_new(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(sku="VT-3", title="Variant Translation Product 3", source_language="de-CH", status="ready"),
            VariantCreate(sku="VT-3-A", variant_title="Original Variant"),
        )
        create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant.id, language_code="de-CH", title="Deutsch", package_label="Deutsch Packung"))
        create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant.id, language_code="en", title="English", package_label="English package"))
        create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant.id, language_code="de-CH", title="Deutsch neu", package_label="Deutsch Packung neu"))
        create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant.id, language_code="fr-CH", title="Francais", option_label_override="Conditionnement"))
        session.commit()
        detail = get_product_detail(session, product.id)

    variant_detail = next(row for row in detail["variants"] if row["id"] == variant.id)
    translations = {row["language_code"]: row for row in variant_detail["translations"]}
    assert translations["de-CH"]["title"] == "Deutsch neu"
    assert translations["de-CH"]["package_label"] == "Deutsch Packung neu"
    assert translations["en"]["title"] == "English"
    assert translations["en"]["package_label"] == "English package"
    assert translations["fr-CH"]["title"] == "Francais"
    assert translations["fr-CH"]["option_label_override"] == "Conditionnement"


def test_update_variant_translation_by_id_updates_exact_row(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product_a, variant_a = create_product(
            session,
            ProductCreate(sku="VT-4", title="Variant Translation Product 4", source_language="de-CH", status="ready"),
            VariantCreate(sku="VT-4-A", variant_title="Variant A"),
        )
        product_b, variant_b = create_product(
            session,
            ProductCreate(sku="VT-4-B-P", title="Variant Translation Product 4B", source_language="de-CH", status="ready"),
            VariantCreate(sku="VT-4-B", variant_title="Variant B"),
        )
        row_a = create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant_a.id, language_code="de-CH", title="Variante A"))
        row_b = create_or_update_variant_translation(session, VariantTranslationCreate(variant_id=variant_b.id, language_code="de-CH", title="Variante B"))

        update_variant_translation_by_id(
            session,
            row_b.id,
            VariantTranslationCreate(
                variant_id=variant_a.id,
                language_code="de-CH",
                title="Variante B neu",
                option_label_override="Gebinde",
                package_label="20 kg",
            ),
        )
        session.commit()
        detail_a = get_product_detail(session, product_a.id)
        detail_b = get_product_detail(session, product_b.id)

    rows = {
        translation["id"]: translation
        for detail in (detail_a, detail_b)
        for variant in detail["variants"]
        for translation in variant["translations"]
    }
    assert rows[row_a.id]["title"] == "Variante A"
    assert rows[row_b.id]["title"] == "Variante B neu"
    assert rows[row_b.id]["option_label_override"] == "Gebinde"
    assert rows[row_b.id]["package_label"] == "20 kg"


def test_voxster_categories_are_scoped_to_sales_channel_and_keep_tree(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        voxster = next(channel for channel in channels if channel.code == "voxster")
        categories = get_or_create_categories(session, ["Chemie > Laugen > Natriumhypochlorit"])
        session.commit()

        rows = list_categories(session)
        detail = get_category_detail(session, categories[-1].id)

    assert [row["sales_channel_code"] for row in rows] == ["voxster", "voxster", "voxster"]
    assert all(row["sales_channel_id"] == voxster.id for row in rows)
    assert detail["sales_channel_code"] == "voxster"
    assert detail["parent_name"] == "Laugen"


def test_voxster_category_crud_and_product_assignment_continue_to_work(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        root = create_category(session, "Voxster Root", None, "de", 10)
        child = create_category(session, "Unterkategorie", root.id, "de", 20)
        product, _variant = create_product(
            session,
            ProductCreate(sku="CAT-1", title="Kategorieprodukt", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="CAT-1-V1", variant_title="Default"),
        )
        set_product_categories(session, product, [child.id])
        session.commit()

        update_category(session, child.id, "Unterkategorie Neu", root.id, "de", 25)
        session.commit()

        detail = get_product_detail(session, product.id)
        category_detail = get_category_detail(session, child.id)

        try:
            delete_category(session, root.id)
        except ValueError as exc:
            delete_error = str(exc)
        else:
            delete_error = ""

    assert detail["categories"] == ["Unterkategorie Neu"]
    assert detail["category_ids"] == [child.id]
    assert category_detail["parent_name"] == "Voxster Root"
    assert delete_error


def test_categories_can_be_filtered_by_channel_and_cross_channel_parent_is_blocked(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        voxster_root = create_category(session, "Voxster Root", None, "de", 0, sales_channel_code="voxster")
        pos_root = create_category(session, "POS Root", None, "de", 0, sales_channel_code="pos")
        session.commit()

        voxster_rows = list_categories(session, sales_channel_code="voxster")
        pos_rows = list_categories(session, sales_channel_code="pos")
        all_rows = list_categories(session, sales_channel_code="*")

        try:
            create_category(session, "Invalid Child", voxster_root.id, "de", 0, sales_channel_code="pos")
        except ValueError as exc:
            error_text = str(exc)
        else:
            error_text = ""

    assert [row["name"] for row in voxster_rows] == ["Voxster Root"]
    assert [row["name"] for row in pos_rows] == ["POS Root"]
    assert {row["sales_channel_code"] for row in all_rows} == {"voxster", "pos"}
    assert "anderen Kanal" in error_text


def test_category_slug_is_unique_per_channel_but_can_repeat_across_channels(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        create_category(session, "Reiniger", None, "de", 0, slug="reiniger", sales_channel_code="voxster")
        create_category(session, "Reiniger", None, "de", 0, slug="reiniger", sales_channel_code="pos")
        session.commit()

        try:
            create_category(session, "Reiniger 2", None, "de", 0, slug="reiniger", sales_channel_code="voxster")
        except ValueError as exc:
            error_text = str(exc)
        else:
            error_text = ""

        voxster_rows = list_categories(session, sales_channel_code="voxster")
        pos_rows = list_categories(session, sales_channel_code="pos")

    assert [row["slug"] for row in voxster_rows] == ["reiniger"]
    assert [row["slug"] for row in pos_rows] == ["reiniger"]
    assert "bereits vergeben" in error_text


def test_export_medusa_products_uses_selected_products_and_translation(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(
                sku="MED-1",
                title="English Title",
                handle="english-handle",
                description="English description",
                brand_name="Demo Brand",
                status="active",
                source_language="en",
            ),
            VariantCreate(sku="MED-1-A", variant_title="Default", price=Decimal("12.90"), currency="CHF", barcode="761000000001"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Deutscher Titel",
                description="Deutsche Beschreibung",
                slug="deutscher-titel",
            ),
        )
        result = export_medusa_products(session, [product.id], language_code="de-CH", output_dir=tmp_path / "medusa_exports")
        session.commit()

    path = tmp_path / "medusa_exports" / str(result["filename"])
    frame = pd.read_csv(path)

    assert result["product_count"] == 1
    assert result["row_count"] == 1
    assert frame.loc[0, "Product Handle"] == "deutscher-titel"
    assert frame.loc[0, "Product Title"] == "Deutscher Titel"
    assert frame.loc[0, "Product Description"] == "Deutsche Beschreibung"
    assert frame.loc[0, "Variant SKU"] == variant.sku
    assert str(frame.loc[0, "Variant Barcode"]) == "761000000001"
    assert frame.loc[0, "Variant Price CHF"] == 12.9


def test_export_medusa_products_builds_r2_image_url_from_current_public_base_url(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="R2-1", title="R2 Product", handle="r2-product", status="active"),
            VariantCreate(sku="R2-1-A", variant_title="Default"),
        )
        source = tmp_path / "image.png"
        source.write_bytes(b"not-a-real-png")
        asset = create_asset_record(session, source, product_id=product.id, alt_text="R2 Product")
        asset.storage_provider = "cloudflare_r2"
        asset.object_key = "prod/assets/products/1/images/example.png"
        asset.public_url = "https://old.example.test/prod/assets/products/1/images/example.png"
        asset.source_url = "https://source.example.test/image.png"
        save_r2_config(
            session,
            {
                "enabled": True,
                "endpoint": "https://example.r2.cloudflarestorage.com",
                "bucket": "voxster-media",
                "region": "auto",
                "public_base_url": "https://media.voxster.ch",
                "max_upload_size_mb": 50,
            },
        )
        result = export_medusa_products(session, [product.id], output_dir=tmp_path / "medusa_exports")
        session.commit()

    frame = pd.read_csv(tmp_path / "medusa_exports" / str(result["filename"]))

    expected = "https://media.voxster.ch/prod/assets/products/1/images/example.png"
    assert frame.loc[0, "Product Thumbnail"] == expected
    assert frame.loc[0, "Product Image 1"] == expected


def test_export_medusa_products_prefers_r2_duplicate_over_local_asset(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="R2-DUP", title="R2 Duplicate", handle="r2-duplicate", status="active"),
            VariantCreate(sku="R2-DUP-A", variant_title="Default"),
        )
        source = tmp_path / "duplicate.png"
        source.write_bytes(b"same-bytes")
        local_asset = create_asset_record(session, source, product_id=product.id, alt_text="R2 Duplicate")
        r2_asset = create_asset_record(session, source, product_id=product.id, alt_text="R2 Duplicate")
        r2_asset.storage_provider = "cloudflare_r2"
        r2_asset.object_key = "prod/assets/products/1/images/duplicate.png"
        save_r2_config(
            session,
            {
                "enabled": True,
                "endpoint": "https://example.r2.cloudflarestorage.com",
                "bucket": "voxster-media",
                "region": "auto",
                "public_base_url": "https://media.voxster.ch",
                "max_upload_size_mb": 50,
            },
        )
        result = export_medusa_products(session, [product.id], output_dir=tmp_path / "medusa_exports")
        session.commit()

    frame = pd.read_csv(tmp_path / "medusa_exports" / str(result["filename"]))

    expected = "https://media.voxster.ch/prod/assets/products/1/images/duplicate.png"
    assert frame.loc[0, "Product Thumbnail"] == expected
    assert frame.loc[0, "Product Image 1"] == expected
    assert "/asset-file/" not in str(frame.loc[0].to_dict())
    assert local_asset.checksum == r2_asset.checksum


def test_export_medusa_products_does_not_export_relative_local_asset_urls(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="LOCAL-IMG", title="Local Image", handle="local-image", status="active"),
            VariantCreate(sku="LOCAL-IMG-A", variant_title="Default"),
        )
        source = tmp_path / "local.png"
        source.write_bytes(b"local-only")
        create_asset_record(session, source, product_id=product.id, alt_text="Local Image")
        result = export_medusa_products(session, [product.id], output_dir=tmp_path / "medusa_exports")
        session.commit()

    frame = pd.read_csv(tmp_path / "medusa_exports" / str(result["filename"]))

    assert pd.isna(frame.loc[0, "Product Thumbnail"])
    assert "/asset-file/" not in str(frame.loc[0].to_dict())


def test_product_category_assignments_are_stored_independently_per_channel(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        voxster_root = create_category(session, "Voxster Root", None, "de", 0, sales_channel_code="voxster")
        voxster_child = create_category(session, "Voxster Child", voxster_root.id, "de", 0, sales_channel_code="voxster")
        pos_root = create_category(session, "POS Root", None, "de", 0, sales_channel_code="pos")
        pos_child = create_category(session, "POS Child", pos_root.id, "de", 0, sales_channel_code="pos")
        product, _variant = create_product(
            session,
            ProductCreate(sku="MCH-1", title="Mehrkanal", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="MCH-1-V1", variant_title="Default"),
        )

        set_product_categories_for_channel(session, product, [voxster_child.id], sales_channel_code="voxster")
        set_product_categories_for_channel(session, product, [pos_child.id], sales_channel_code="pos")
        session.commit()

        detail = get_product_detail(session, product.id)
        assignments = list_product_category_assignments(session, product.id)

    assert detail["category_channel_code"] == "voxster"
    assert detail["category_ids"] == [voxster_child.id]
    assert detail["categories"] == ["Voxster Child"]
    assert {row["sales_channel_code"] for row in assignments} == {"voxster", "pos"}
    assert next(row for row in assignments if row["sales_channel_code"] == "voxster")["category_ids"] == [voxster_child.id]
    assert next(row for row in assignments if row["sales_channel_code"] == "pos")["category_ids"] == [pos_child.id]


def test_product_category_save_replaces_only_the_target_channel(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        voxster_root = create_category(session, "Voxster Root", None, "de", 0, sales_channel_code="voxster")
        voxster_child_a = create_category(session, "Voxster A", voxster_root.id, "de", 0, sales_channel_code="voxster")
        voxster_child_b = create_category(session, "Voxster B", voxster_root.id, "de", 0, sales_channel_code="voxster")
        pos_root = create_category(session, "POS Root", None, "de", 0, sales_channel_code="pos")
        pos_child = create_category(session, "POS Child", pos_root.id, "de", 0, sales_channel_code="pos")
        product, _variant = create_product(
            session,
            ProductCreate(sku="MCH-2", title="Mehrkanal 2", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="MCH-2-V1", variant_title="Default"),
        )
        set_product_categories_for_channel(session, product, [voxster_child_a.id], sales_channel_code="voxster")
        set_product_categories_for_channel(session, product, [pos_child.id], sales_channel_code="pos")
        session.commit()

        update_product(
            session,
            product.id,
            ProductUpdate(
                title="Mehrkanal 2",
                brand_name="VOXSTER",
                status="draft",
                category_channel_code="voxster",
                category_ids=[voxster_child_b.id],
            ),
        )
        session.commit()

        assignments = list_product_category_assignments(session, product.id)

        update_product(
            session,
            product.id,
            ProductUpdate(
                title="Mehrkanal 2",
                brand_name="VOXSTER",
                status="draft",
                category_channel_code="pos",
                category_ids=[],
            ),
        )
        session.commit()

        assignments_after_empty = list_product_category_assignments(session, product.id)

    assert next(row for row in assignments if row["sales_channel_code"] == "voxster")["category_ids"] == [voxster_child_b.id]
    assert next(row for row in assignments if row["sales_channel_code"] == "pos")["category_ids"] == [pos_child.id]
    assert next(row for row in assignments_after_empty if row["sales_channel_code"] == "voxster")["category_ids"] == [voxster_child_b.id]
    assert not any(row["sales_channel_code"] == "pos" for row in assignments_after_empty)


def test_product_category_assignments_reject_cross_channel_categories(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        voxster_root = create_category(session, "Voxster Root", None, "de", 0, sales_channel_code="voxster")
        pos_root = create_category(session, "POS Root", None, "de", 0, sales_channel_code="pos")
        product, _variant = create_product(
            session,
            ProductCreate(sku="MCH-3", title="Mehrkanal 3", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="MCH-3-V1", variant_title="Default"),
        )

        try:
            set_product_categories_for_channel(
                session,
                product,
                [voxster_root.id, pos_root.id],
                sales_channel_code="voxster",
            )
        except ValueError as exc:
            error_text = str(exc)
        else:
            error_text = ""

    assert "gewählten Kanal" in error_text


def test_product_sdb_defaults_include_review_and_issuer_metadata(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(sku="CHEM-1", title="Chem Demo", brand_name="Brand A", status="draft", is_chemical=True),
            VariantCreate(sku="CHEM-1", variant_title="Default Variant"),
        )
        upsert_product_sdb(session, product.id, ProductSDBUpdate())
        session.commit()

        sdb = get_product_sdb(session, product.id)

    assert sdb["review_status"] == "review_required"
    assert sdb["version_label"] == "Entwurf 1.0"
    assert sdb["issuer_name"] == "VOXSTER GmbH"
    assert sdb["issuer_address_line1"] == "Obere Ifangstrasse 10"
    assert sdb["issuer_postal_code"] == "8215"
    assert sdb["issuer_city"] == "Hallau"
    assert sdb["issuer_country_code"] == "CH"
    assert sdb["issuer_phone"] == "+41 52 502 67 23"
    assert sdb["issuer_email"] == "info@voxster.ch"
    assert sdb["action_log_json"] == []


def test_product_sdb_persists_action_log_json(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _ = create_product(
            session,
            ProductCreate(sku="CHEM-2", title="Chem Demo 2", brand_name="Brand A", status="draft", is_chemical=True),
            VariantCreate(sku="CHEM-2", variant_title="Default Variant"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                action_log_json=[
                    {
                        "timestamp": "2026-04-19 20:00:00 UTC",
                        "step": "Deterministischer Import",
                        "outcome": "ok",
                        "details": "Quelle übernommen.",
                    }
                ]
            ),
        )
        session.commit()

        sdb = get_product_sdb(session, product.id)

    assert len(sdb["action_log_json"]) == 1
    assert sdb["action_log_json"][0]["step"] == "Deterministischer Import"


def test_sales_channels_and_channel_export_use_only_published_active_listings(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        product, variant = create_product(
            session,
            ProductCreate(sku="CHEM-CH-1", title="Natriumhypochlorit 14 %", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="CHEM-CH-1-25KG", variant_title="25 kg", barcode="7610000000001"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Natriumhypochlorit 14 % 25 kg",
                short_description="Kurztext",
                description="Ausführliche Beschreibung",
                slug="natriumhypochlorit-14-25kg",
            ),
        )
        create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Gebinde 25 kg",
                package_label="25 kg Kanister",
            ),
        )
        voxster = next(channel for channel in channels if channel.code == "voxster")
        chemie_shop = next(channel for channel in channels if channel.code == "chemie_shop")
        channel_category = upsert_channel_category(
            session,
            ChannelCategoryUpsert(
                sales_channel_id=voxster.id,
                external_category_id="chem-001",
                external_path="Chemie > Laugen",
                name="Laugen",
                required_attributes_json=["cas_number"],
                is_active=True,
            ),
        )
        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=product.id,
                sales_channel_id=voxster.id,
                allowed=True,
                is_active=True,
                publication_status="published",
            ),
        )
        upsert_variant_channel_listing(
            session,
            VariantChannelListingUpdate(
                variant_id=variant.id,
                sales_channel_id=voxster.id,
                allowed=True,
                is_active=True,
                publication_status="published",
                channel_sku="VX-25KG",
                channel_ean="7610000000999",
            ),
        )
        upsert_product_category_mapping(
            session,
            ProductCategoryMappingUpsert(
                product_id=product.id,
                sales_channel_id=voxster.id,
                channel_category_id=channel_category.id,
                is_primary=True,
            ),
        )
        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=product.id,
                sales_channel_id=chemie_shop.id,
                allowed=True,
                is_active=True,
                publication_status="draft",
            ),
        )
        upsert_variant_channel_listing(
            session,
            VariantChannelListingUpdate(
                variant_id=variant.id,
                sales_channel_id=chemie_shop.id,
                allowed=True,
                is_active=True,
                publication_status="draft",
            ),
        )
        session.commit()

        sales_channels = list_sales_channels(session)
        channel_categories = list_channel_categories(session)
        detail = get_product_detail(session, product.id)
        export_rows = list_channel_export_rows(session, "voxster", language_code="de-CH")
        chemie_rows = list_channel_export_rows(session, "chemie_shop", language_code="de-CH")

    assert [row["code"] for row in sales_channels[:3]] == ["voxster", "pos", "chemie_shop"]
    assert channel_categories[0]["external_category_id"] == "chem-001"
    assert len(detail["channel_listings"]) == len(sales_channels)
    assert any(row["sales_channel_code"] == "voxster" and row["publication_status"] == "published" for row in detail["channel_listings"])
    assert len(export_rows) == 1
    assert export_rows[0]["product_title"] == "Natriumhypochlorit 14 % 25 kg"
    assert export_rows[0]["variant_title"] == "Gebinde 25 kg"
    assert export_rows[0]["variant_sku"] == "VX-25KG"
    assert export_rows[0]["variant_ean"] == "7610000000999"
    assert export_rows[0]["external_category_id"] == "chem-001"
    assert export_rows[0]["slug"] == "natriumhypochlorit-14-25kg"
    assert chemie_rows == []


def test_bulk_channel_actions_are_idempotent_and_resolve_related_variants(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        voxster = next(channel for channel in channels if channel.code == "voxster")
        product, variant_a = create_product(
            session,
            ProductCreate(sku="BULK-1", title="Bulk Product", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="BULK-1-A", variant_title="A", barcode="111"),
        )
        _product, variant_b = upsert_product_with_variant(
            session,
            sku="BULK-1",
            family_key=None,
            source_language="en",
            title="Bulk Product",
            description=None,
            source_url=None,
            source_url_final=None,
            specifications_text=None,
            technical_features_text=None,
            brand_name="VOXSTER",
            status="ready",
            variant_sku="BULK-1-B",
            variant_title="B",
            option_name=None,
            option_value=None,
            packaging=None,
            price=None,
            currency=None,
            cost_price=None,
            cost_currency=None,
            barcode="222",
            stock_qty=0,
        )
        channel_category = upsert_channel_category(
            session,
            ChannelCategoryUpsert(
                sales_channel_id=voxster.id,
                external_category_id="bulk-cat",
                external_path="Bulk",
                name="Bulk",
            ),
        )

        related_variant_ids = variant_ids_for_products(session, [product.id])
        related_product_ids = product_ids_for_variants(session, [variant_a.id, variant_b.id])
        product_rows = list_products(session)
        product_count_a = bulk_upsert_product_channel_listings(
            session,
            [product.id, product.id],
            voxster.id,
            allowed=True,
            is_active=True,
            publication_status="published",
        )
        product_count_b = bulk_upsert_product_channel_listings(
            session,
            [product.id],
            voxster.id,
            allowed=True,
            is_active=True,
            publication_status="published",
        )
        variant_count = bulk_upsert_variant_channel_listings(
            session,
            related_variant_ids,
            voxster.id,
            allowed=True,
            is_active=True,
            publication_status="published",
        )
        mapping_count_a = bulk_upsert_product_category_mappings(
            session,
            [product.id],
            voxster.id,
            channel_category.id,
            is_primary=True,
        )
        mapping_count_b = bulk_upsert_product_category_mappings(
            session,
            [product.id],
            voxster.id,
            channel_category.id,
            is_primary=True,
        )
        variant_mapping_count_a = bulk_upsert_variant_category_mappings(
            session,
            related_variant_ids,
            voxster.id,
            channel_category.id,
            is_primary=True,
        )
        variant_mapping_count_b = bulk_upsert_variant_category_mappings(
            session,
            related_variant_ids,
            voxster.id,
            channel_category.id,
            is_primary=True,
        )
        session.commit()

        detail = get_product_detail(session, product.id)
        variant_category_mappings = list_variant_category_mappings(session, product.id)

    assert related_variant_ids == [variant_a.id, variant_b.id]
    assert related_product_ids == [product.id]
    assert next(row for row in product_rows if row["id"] == product.id)["variant_count"] == 2
    assert product_count_a == 1
    assert product_count_b == 1
    assert variant_count == 2
    assert mapping_count_a == 1
    assert mapping_count_b == 1
    assert variant_mapping_count_a == 2
    assert variant_mapping_count_b == 2
    assert sum(1 for row in detail["channel_listings"] if row["sales_channel_code"] == "voxster" and row["is_active"]) == 1
    assert len([row for row in detail["variant_channel_listings"] if row["sales_channel_code"] == "voxster" and row["is_active"]]) == 2
    assert len(detail["channel_category_mappings"]) == 1
    assert len(detail["variant_category_mappings"]) == 2
    assert len(variant_category_mappings) == 2


def test_channel_category_tree_builds_multiple_levels_and_products_are_scoped(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        voxster = next(channel for channel in channels if channel.code == "voxster")
        pos = next(channel for channel in channels if channel.code == "pos")
        root = upsert_channel_category(
            session,
            ChannelCategoryUpsert(sales_channel_id=voxster.id, external_category_id="root", external_path="Root", name="Root"),
        )
        child = upsert_channel_category(
            session,
            ChannelCategoryUpsert(sales_channel_id=voxster.id, external_category_id="child", external_path="Root > Child", name="Child"),
        )
        leaf = upsert_channel_category(
            session,
            ChannelCategoryUpsert(sales_channel_id=voxster.id, external_category_id="leaf", external_path="Root > Child > Leaf", name="Leaf"),
        )
        pos_category = upsert_channel_category(
            session,
            ChannelCategoryUpsert(sales_channel_id=pos.id, external_category_id="pos-root", external_path="POS", name="POS"),
        )
        product, _variant_a = create_product(
            session,
            ProductCreate(sku="TREE-1", title="Tree Product", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TREE-1-A", variant_title="A"),
        )
        _product, _variant_b = upsert_product_with_variant(
            session,
            sku="TREE-1",
            family_key=None,
            source_language="en",
            title="Tree Product",
            description=None,
            source_url=None,
            source_url_final=None,
            specifications_text=None,
            technical_features_text=None,
            brand_name="VOXSTER",
            status="ready",
            variant_sku="TREE-1-B",
            variant_title="B",
            option_name=None,
            option_value=None,
            packaging=None,
            price=None,
            currency=None,
            cost_price=None,
            cost_currency=None,
            barcode=None,
            stock_qty=0,
        )
        upsert_product_category_mapping(
            session,
            ProductCategoryMappingUpsert(
                product_id=product.id,
                sales_channel_id=voxster.id,
                channel_category_id=leaf.id,
                is_primary=True,
            ),
        )
        session.commit()

        voxster_tree = get_channel_category_tree(session, voxster.id)
        pos_tree = get_channel_category_tree(session, pos.id)
        products = get_products_for_channel_category(session, leaf.id)
        empty_products = get_products_for_channel_category(session, child.id)

    assert [row["id"] for row in voxster_tree] == [root.id, child.id, leaf.id]
    assert [row["tree_level"] for row in voxster_tree] == [0, 1, 2]
    assert voxster_tree[0]["has_children"] is True
    assert voxster_tree[1]["parent_id"] == root.id
    assert voxster_tree[2]["parent_id"] == child.id
    assert [row["id"] for row in pos_tree] == [pos_category.id]
    assert len(products) == 1
    assert products[0]["sku"] == "TREE-1"
    assert products[0]["variant_count"] == 2
    assert empty_products == []


def test_internal_channel_category_products_are_scoped_by_channel(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        product, _variant = create_product(
            session,
            ProductCreate(sku="INT-CAT-1", title="Internal Category Product", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="INT-CAT-1-A", variant_title="A"),
        )
        voxster_category = create_category(
            session,
            name="Interne Kategorie",
            parent_id=None,
            language_code="de",
            sort_order=1,
            sales_channel_code="voxster",
        )
        pos_category = create_category(
            session,
            name="POS Kategorie",
            parent_id=None,
            language_code="de",
            sort_order=1,
            sales_channel_code="pos",
        )
        set_product_categories_for_channel(session, product, [voxster_category.id], "voxster")
        session.commit()

        voxster_products = get_products_for_category(session, voxster_category.id)
        pos_products = get_products_for_category(session, pos_category.id)

    assert [row["id"] for row in voxster_products] == [product.id]
    assert voxster_products[0]["sales_channel_code"] == "voxster"
    assert pos_products == []


def test_internal_channel_category_products_include_descendant_assignments(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        product, _variant = create_product(
            session,
            ProductCreate(sku="INT-CAT-TREE-1", title="Internal Category Tree Product", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="INT-CAT-TREE-1-A", variant_title="A"),
        )
        root = create_category(
            session,
            name="Interne Root",
            parent_id=None,
            language_code="de",
            sort_order=1,
            sales_channel_code="voxster",
        )
        child = create_category(
            session,
            name="Interne Child",
            parent_id=root.id,
            language_code="de",
            sort_order=1,
            sales_channel_code="voxster",
        )
        set_product_categories_for_channel(session, product, [child.id], "voxster")
        session.commit()

        root_products = get_products_for_category(session, root.id)
        root_direct_products = get_products_for_category(session, root.id, include_descendants=False)

    assert [row["id"] for row in root_products] == [product.id]
    assert root_direct_products == []


def test_bulk_update_products_supports_preview_apply_and_backup(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product_a, _variant_a = create_product(
            session,
            ProductCreate(sku="BULK-P-1", title="Bulk Produkt 1", source_language="en", brand_name="Alt", status="draft"),
            VariantCreate(sku="BULK-P-1-A", variant_title="A"),
        )
        product_b, _variant_b = create_product(
            session,
            ProductCreate(sku="BULK-P-2", title="Bulk Produkt 2", source_language="en", status="draft"),
            VariantCreate(sku="BULK-P-2-A", variant_title="A"),
        )
        preview = bulk_update_products(
            session,
            [product_a.id, product_b.id],
            {"source_language": "de-CH", "brand_name": "Tintolav", "status": "active", "is_chemical": True},
            apply=False,
            backup_dir=tmp_path,
        )
        assert preview["updated"] == 8
        assert session.get(type(product_a), product_a.id).source_language == "en"

        applied = bulk_update_products(
            session,
            [product_a.id, product_b.id],
            {"source_language": "de-CH", "brand_name": "Tintolav", "status": "active", "is_chemical": True},
            apply=True,
            backup_dir=tmp_path,
        )
        session.commit()
        updated_a = session.get(type(product_a), product_a.id)
        updated_b = session.get(type(product_b), product_b.id)
        product_values = {
            "a_source_language": updated_a.source_language,
            "a_brand": updated_a.brand.name if updated_a.brand else None,
            "a_status": updated_a.status,
            "a_is_chemical": updated_a.is_chemical,
            "b_brand": updated_b.brand.name if updated_b.brand else None,
        }

    assert applied["updated"] == 8
    assert applied["backup_path"]
    assert (tmp_path / str(applied["backup_path"]).split("/")[-1]).exists()
    assert product_values == {
        "a_source_language": "de-CH",
        "a_brand": "Tintolav",
        "a_status": "active",
        "a_is_chemical": True,
        "b_brand": "Tintolav",
    }


def test_bulk_update_variants_supports_preview_apply_and_only_empty(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        _product, variant = create_product(
            session,
            ProductCreate(sku="BULK-V-P", title="Bulk Varianten Produkt", source_language="de-CH", status="ready"),
            VariantCreate(sku="BULK-V-1", variant_title="Alt", price=Decimal("1.00"), currency="EUR", stock_qty=0),
        )
        preview = bulk_update_variants(
            session,
            [variant.id],
            {"status": "active", "price": "9.50", "currency": "CHF", "option_name": "Packaging", "option_value": "10 kg", "packaging": "10 kg Kanister"},
            apply=False,
            backup_dir=tmp_path,
        )
        assert preview["updated"] == 5
        assert session.get(type(variant), variant.id).currency == "EUR"

        applied = bulk_update_variants(
            session,
            [variant.id],
            {"status": "active", "price": "9.50", "currency": "CHF", "option_name": "Packaging", "option_value": "10 kg", "packaging": "10 kg Kanister"},
            apply=True,
            backup_dir=tmp_path,
        )
        only_empty = bulk_update_variants(
            session,
            [variant.id],
            {"currency": "EUR"},
            apply=True,
            only_empty=True,
            backup_dir=tmp_path,
        )
        session.commit()
        updated = session.get(type(variant), variant.id)
        variant_values = {
            "price": updated.price,
            "currency": updated.currency,
            "option_name": updated.option_name,
            "option_value": updated.option_value,
            "packaging": updated.packaging,
        }

    assert applied["backup_path"]
    assert only_empty["skipped"] == 1
    assert variant_values == {
        "price": Decimal("9.50"),
        "currency": "CHF",
        "option_name": "Packaging",
        "option_value": "10 kg",
        "packaging": "10 kg Kanister",
    }


def test_variant_category_mapping_rejects_wrong_channel_category(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        voxster = next(channel for channel in channels if channel.code == "voxster")
        pos = next(channel for channel in channels if channel.code == "pos")
        _product, variant = create_product(
            session,
            ProductCreate(sku="VAR-MAP-1", title="Variant Mapping", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="VAR-MAP-1-A", variant_title="A"),
        )
        pos_category = upsert_channel_category(
            session,
            ChannelCategoryUpsert(
                sales_channel_id=pos.id,
                external_category_id="pos-cat",
                external_path="POS",
                name="POS",
            ),
        )

        try:
            upsert_variant_category_mapping(
                session,
                VariantCategoryMappingUpsert(
                    variant_id=variant.id,
                    sales_channel_id=voxster.id,
                    channel_category_id=pos_category.id,
                    is_primary=True,
                ),
            )
        except ValueError as exc:
            assert "Kanal-Kategorie" in str(exc)
        else:
            raise AssertionError("Expected ValueError for cross-channel variant category mapping")


def test_channel_export_respects_active_window_and_writes_csv(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        channels = ensure_default_sales_channels(session)
        voxster = next(channel for channel in channels if channel.code == "voxster")
        product, variant = create_product(
            session,
            ProductCreate(sku="CHEM-2", title="Lauge", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="CHEM-2-1", variant_title="1 kg"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="de-CH",
                title="Lauge 1 kg",
                short_description="Kurz",
                description="Lang",
                seo_title="SEO",
                seo_description="SEO Lang",
                slug="lauge-1kg",
            ),
        )
        create_or_update_variant_translation(
            session,
            VariantTranslationCreate(
                variant_id=variant.id,
                language_code="de-CH",
                title="Gebinde 1 kg",
                option_label_override="Gebinde",
                package_label="1 kg Flasche",
            ),
        )
        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=product.id,
                sales_channel_id=voxster.id,
                allowed=True,
                is_active=True,
                active_from="2099-01-01T00:00:00+00:00",
                publication_status="published",
            ),
        )
        upsert_variant_channel_listing(
            session,
            VariantChannelListingUpdate(
                variant_id=variant.id,
                sales_channel_id=voxster.id,
                allowed=True,
                is_active=True,
                publication_status="published",
            ),
        )
        session.commit()

        assert list_channel_export_rows(session, "voxster", language_code="de-CH") == []

        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=product.id,
                sales_channel_id=voxster.id,
                allowed=True,
                is_active=True,
                active_from="2020-01-01T00:00:00+00:00",
                active_until="2099-01-01T00:00:00+00:00",
                publication_status="published",
            ),
        )
        session.commit()

        export_result = export_channel_rows(session, "voxster", language_code="de-CH", output_dir=tmp_path)

    assert export_result["row_count"] == 1
    assert export_result["filename"] == "channel_export_voxster_de-CH.csv"
    export_frame = pd.read_csv(tmp_path / export_result["filename"])
    assert export_frame.loc[0, "product_title"] == "Lauge 1 kg"
    assert export_frame.loc[0, "variant_title"] == "Gebinde 1 kg"


def test_delete_assets_handles_multiple_ids_and_reports_errors(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    asset_a = asset_dir / "a.txt"
    asset_b = asset_dir / "b.txt"
    asset_a.write_text("a", encoding="utf-8")
    asset_b.write_text("b", encoding="utf-8")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="ASSET-1", title="Asset Product", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="ASSET-1", variant_title="Default Variant"),
        )
        first = create_asset_record(session, asset_a, product_id=product.id)
        second = create_asset_record(session, asset_b, product_id=product.id)
        session.commit()

        result = delete_assets(session, [first.id, 999999, second.id, first.id])
        session.commit()

    assert result["deleted_count"] == 2
    assert sorted(result["deleted_ids"]) == sorted([first.id, second.id])
    assert result["error_count"] == 1
    assert result["errors"][0]["asset_id"] == 999999
    assert not asset_a.exists()
    assert not asset_b.exists()


def test_list_products_includes_primary_photo_asset(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    document_path = asset_dir / "manual.pdf"
    image_path = asset_dir / "product.jpg"
    document_path.write_bytes(b"%PDF-1.4\n")
    image_path.write_bytes(
        bytes.fromhex(
            "ffd8ffe000104a46494600010101006000600000ffdb004300"
            "0302020302020303030304030304050805050404050a07070608"
            "0c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b10161011131415"
            "15150c0f171816141812141514ffdb0043010304040504050905"
            "0509140d0b0d1414141414141414141414141414141414141414"
            "1414141414141414141414141414141414141414141414141414"
            "141414141414ffc00011080001000103012200021101031101ff"
            "c400140001000000000000000000000000000000000000ffc400"
            "141001000000000000000000000000000000000000ffda000c03"
            "010002110311003f00d2cf20ffd9"
        )
    )

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="PHOTO-1", title="Photo Product", brand_name="VOXSTER", status="draft"),
            VariantCreate(sku="PHOTO-1", variant_title="Default Variant"),
        )
        document_asset = create_asset_record(session, document_path, product_id=product.id)
        image_asset = create_asset_record(session, image_path, product_id=product.id)
        session.commit()

        rows = list_products(session)

    row = next(item for item in rows if item["id"] == product.id)
    assert row["photo_asset_id"] == image_asset.id
    assert row["photo_url"] == f"/asset-file/{image_asset.id}"
    assert row["photo_filename"] == "product.jpg"
    assert row["photo_mime_type"] == "image/jpeg"
    assert row["photo_asset_id"] != document_asset.id


def test_ai_product_translation_batch_uses_prompt_and_stores_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PIM_TRANSLATION_MODEL", "gpt-5-mini")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_call(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, str]:
        assert "Translate now" in user_prompt
        assert model == "gpt-5-mini"
        assert api_key == "test-key"
        return {
            "title": "Translated title",
            "shortDescription": "Short",
            "description": "Long",
            "seoTitle": "SEO title",
            "seoDescription": "SEO description",
            "slug": "translated-title",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TR-1", title="Original title", description="Original description", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TR-1-A", variant_title="Default"),
        )
        languages = list_languages(session)
        prompts = list_translation_prompts(session)
        save_translation_prompt(session, "fr", "Translate now {{title}} from {{sourceLanguage}} to {{targetLanguage}}", "System")
        result = generate_product_translations(session, [product.id], ["fr"], source_language_code="de")
        session.commit()

        detail = get_product_detail(session, product.id)

    assert any(row["code"] == "fr" for row in languages)
    assert any(row["language_code"] == "fr" for row in prompts)
    assert result["generated"] == 1
    translation = next(row for row in detail["translations"] if row["language_code"] == "fr")
    assert translation["title"] == "Translated title"
    assert translation["translation_status"] == "generated"
    assert translation["source_language_code"] == "de"
    assert translation["provider"] == "openai"
    assert translation["model"] == "gpt-5-mini"
    assert translation["short_description"] == "Short"
    assert translation["slug"] == "translated-title"


def test_ai_product_translation_skips_existing_without_overwrite(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TR-2", title="Original title", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TR-2-A", variant_title="Default"),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(product_id=product.id, language_code="it", title="Existing"),
        )
        result = generate_product_translations(session, [product.id], ["it"], source_language_code="de", overwrite_existing=False)

    assert result["generated"] == 0
    assert result["skipped"] == 1


def test_ai_product_translation_reports_openai_error_message(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_call(*_args, **_kwargs) -> dict[str, str]:
        raise RuntimeError("OpenAI API Fehler 400: Unsupported model")

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TR-ERR", title="Original title", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TR-ERR-A", variant_title="Default"),
        )
        result = generate_product_translations(session, [product.id], ["en"], source_language_code="de")

    assert result["failed"] == 1
    assert result["results"][0]["status"] == "failed"
    assert "Unsupported model" in result["results"][0]["message"]


def test_openai_translation_request_omits_temperature_for_gpt5(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"T","shortDescription":"","description":"","seoTitle":"","seoDescription":""}'
                        }
                    }
                ]
            }

    def fake_post(*_args, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr(product_translation_service.requests, "post", fake_post)

    payload = _call_openai_translation_json("System", "User", "gpt-5-mini", "test-key")

    assert payload["title"] == "T"
    assert payload["slug"] == ""
    assert "temperature" not in captured


def test_openai_translation_request_keeps_temperature_for_non_gpt5(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"T","shortDescription":"","description":"","seoTitle":"","seoDescription":""}'
                        }
                    }
                ]
            }

    def fake_post(*_args, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse()

    monkeypatch.setattr(product_translation_service.requests, "post", fake_post)

    _call_openai_translation_json("System", "User", "gpt-4o-mini", "test-key")

    assert captured["temperature"] == pytest.approx(0.2)


def test_ai_product_translation_fills_short_description_and_slug_fallbacks(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_call(*_args, **_kwargs) -> dict[str, str]:
        return {
            "title": "Hand Disinfectant",
            "shortDescription": "",
            "description": "Effective hand disinfectant for professional hygiene.",
            "seoTitle": "Hand Disinfectant",
            "seoDescription": "Professional hand disinfectant.",
            "slug": "",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TR-FALLBACK", title="Hand Desinfektionsmittel", handle="hand-desinfektionsmittel", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TR-FALLBACK-A", variant_title="Default"),
        )
        result = generate_product_translations(session, [product.id], ["en"], source_language_code="de-CH")
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "en")
    assert result["generated"] == 1
    assert translation["short_description"] == "Effective hand disinfectant for professional hygiene."
    assert translation["slug"] == "hand-disinfectant"


def test_ai_product_translation_uses_selected_source_translation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    captured: dict[str, str] = {}

    def fake_call(*_args, **kwargs) -> dict[str, str]:
        user_prompt = str(kwargs["user_prompt"])
        captured["prompt"] = user_prompt
        assert "D1 Sweat A15-030A" in user_prompt
        assert "Specific stain remover for sweat" in user_prompt
        assert "D1 Schweiß Fleckenentferner" not in user_prompt
        return {
            "title": "D1 Schweiss Fleckenentferner",
            "shortDescription": "D1 Schweiss ist ein Fleckenentferner für Schweiss-, Lebensmittel-, Saucen- und Schokoladenflecken.",
            "description": "D1 Schweiss ist ein spezifischer Fleckenentferner für alle Wascharten.",
            "seoTitle": "D1 Schweiss Fleckenentferner",
            "seoDescription": "Fleckenentferner für Schweiss, Lebensmittel, Saucen und Schokolade.",
            "slug": "d1-schweiss-fleckenentferner",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="A15-030",
                title="D1 Schweiß Fleckenentferner",
                description="Alte deutsche Beschreibung",
                source_language="de-CH",
                status="ready",
            ),
            VariantCreate(sku="A15-030A", variant_title="D1 Schweiß Fleckenentferner 500 ml"),
        )
        session.add(
            ProductTranslation(
                product_id=product.id,
                language_code="en",
                title="D1 Sweat A15-030A",
                short_description="Specific stain remover for sweat, food, sauce and chocolate stains.",
                description="Specific stain remover for sweat, food, sauce and chocolate stains for all kinds of wash.",
                seo_title="D1 Sweat A15-030A",
                seo_description="Specific stain remover for sweat.",
                slug="d1-sweat",
            )
        )
        result = generate_product_translations(
            session,
            [product.id],
            ["de-CH"],
            source_language_code="en",
            overwrite_existing=True,
            allow_original_overwrite=True,
        )
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "de-CH")
    assert result["generated"] == 1
    assert "D1 Sweat A15-030A" in captured["prompt"]
    assert detail["description"] == "D1 Schweiss ist ein spezifischer Fleckenentferner für alle Wascharten."
    assert translation["short_description"].startswith("D1 Schweiss ist ein Fleckenentferner")


def test_ai_product_translation_preserves_markdown_description(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source_description = (
        "Tintosoft ist ein parfümierter Weichspüler sowohl für das Händewaschen als auch für das Waschen in der Maschine und für alle Stoffarten geeignet.\n\n"
        "### Hinweis\n\n"
        "- Preis pro Kanister"
    )

    def fake_call(system_prompt: str, user_prompt: str, *_args, **_kwargs) -> dict[str, str]:
        assert "Erhalte alle Markdown-Elemente exakt" in user_prompt
        assert "### Hinweis" in user_prompt
        return {
            "title": "Tintosoft adoucissant",
            "shortDescription": "Tintosoft est un adoucissant parfumé adapté au lavage à la main et en machine pour tous les textiles.",
            "description": (
                "Tintosoft est un adoucissant parfumé adapté au lavage à la main ainsi qu’au lavage en machine et convient à tous les types de textiles.\n\n"
                "### Remarque\n\n"
                "- Prix par bidon"
            ),
            "seoTitle": "Tintosoft adoucissant",
            "seoDescription": "Adoucissant parfumé pour lavage à la main et en machine.",
            "slug": "tintosoft-adoucissant",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TINTO-MD", title="Tintosoft", description=source_description, source_language="de-CH", status="ready"),
            VariantCreate(sku="TINTO-MD-1", variant_title="Kanister"),
        )
        result = generate_product_translations(session, [product.id], ["fr-CH"], source_language_code="de-CH")
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "fr-CH")
    assert result["generated"] == 1
    assert "### Remarque" in translation["description"]
    assert "- Prix par bidon" in translation["description"]
    assert "<h3>" not in translation["description"]
    assert not translation["short_description"].startswith("-")


def test_ai_product_translation_rejects_broken_markdown(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_call(*_args, **_kwargs) -> dict[str, str]:
        return {
            "title": "Tintosoft",
            "shortDescription": "Court",
            "description": "Tintosoft est un adoucissant.\n\nRemarque:\n\nPrix par bidon",
            "seoTitle": "Tintosoft",
            "seoDescription": "SEO",
            "slug": "tintosoft",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="TINTO-BROKEN", title="Tintosoft", description="Text.\n\n### Hinweis\n\n- Preis pro Kanister", source_language="de-CH", status="ready"),
            VariantCreate(sku="TINTO-BROKEN-1", variant_title="Kanister"),
        )
        result = generate_product_translations(session, [product.id], ["fr-CH"], source_language_code="de-CH")

    assert result["failed"] == 1
    assert "Markdown" in result["results"][0]["message"]


def test_ai_product_translation_retries_when_markdown_structure_is_broken(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    calls: list[str] = []

    def fake_call(system_prompt: str, user_prompt: str, *_args, **_kwargs) -> dict[str, str]:
        assert system_prompt
        calls.append(user_prompt)
        if len(calls) == 1:
            return {
                "title": "Fresh Laundry detergent",
                "shortDescription": "Multipurpose detergent for all fabrics.",
                "description": "Fresh Laundry is a multipurpose detergent.\n\nProperties:\n\nGood washing effect",
                "seoTitle": "Fresh Laundry detergent",
                "seoDescription": "Multipurpose detergent.",
                "slug": "fresh-laundry-detergent",
            }
        return {
            "title": "Fresh Laundry detergent",
            "shortDescription": "Multipurpose detergent for all fabrics.",
            "description": "Fresh Laundry detergent is a multipurpose detergent for all fabrics.\n\n### Properties\n\n- Good washing effect\n\n### Use / Note\n\n- Price per canister",
            "seoTitle": "Fresh Laundry detergent",
            "seoDescription": "Multipurpose detergent.",
            "slug": "fresh-laundry-detergent",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="MD-RETRY",
                title="Fresh Laundry Waschmittel",
                description="Fresh Laundry Waschmittel.\n\n### Eigenschaften\n\n- Gute Waschwirkung\n\n### Anwendung / Hinweis\n\n- Preis pro Kanister",
                source_language="de-CH",
                status="ready",
            ),
            VariantCreate(sku="MD-RETRY-1", variant_title="Kanister"),
        )
        result = generate_product_translations(session, [product.id], ["en"], source_language_code="de-CH")
        session.commit()
        detail = get_product_detail(session, product.id)

    translation = next(row for row in detail["translations"] if row["language_code"] == "en")
    assert result["generated"] == 1
    assert len(calls) == 2
    assert "Markdown-Strukturschutz" in calls[0]
    assert "vorherige Antwort" in calls[1]
    assert "### Properties" in translation["description"]
    assert "- Good washing effect" in translation["description"]


def test_ai_product_translation_can_include_variant_translations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_product_call(*_args, **_kwargs) -> dict[str, str]:
        return {
            "title": "Disinfectant",
            "shortDescription": "Short",
            "description": "Long",
            "seoTitle": "SEO",
            "seoDescription": "SEO desc",
            "slug": "disinfectant",
        }

    def fake_variant_call(*_args, **_kwargs) -> dict[str, str]:
        return {
            "title": "25 kg canister",
            "optionLabelOverride": "Packaging size",
            "packageLabel": "25 kg canister",
        }

    monkeypatch.setattr(product_translation_service, "_call_openai_translation_json", fake_product_call)
    monkeypatch.setattr(product_translation_service, "_call_openai_variant_translation_json", fake_variant_call)

    with SessionLocal() as session:
        product, variant = create_product(
            session,
            ProductCreate(sku="TR-VAR", title="Desinfektionsmittel", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="TR-VAR-25", variant_title="25 kg Kanister", option_name="Gebinde", option_value="25 kg", packaging="Kanister"),
        )
        result = generate_product_translations(session, [product.id], ["en"], source_language_code="de-CH", include_variants=True)
        session.commit()
        detail = get_product_detail(session, product.id)

    variant_detail = next(row for row in detail["variants"] if row["id"] == variant.id)
    variant_translation = next(row for row in variant_detail["translations"] if row["language_code"] == "en")
    assert result["generated"] == 2
    assert variant_translation["title"] == "25 kg canister"
    assert variant_translation["option_label_override"] == "Packaging size"
    assert variant_translation["package_label"] == "25 kg canister"


def test_ai_variant_translation_json_validation(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"5 kg bucket","optionLabelOverride":"Package","packageLabel":"5 kg bucket"}'
                        }
                    }
                ]
            }

    monkeypatch.setattr(product_translation_service.requests, "post", lambda *_args, **_kwargs: FakeResponse())

    payload = _call_openai_variant_translation_json("System", "User", "gpt-5-mini", "test-key")

    assert payload["title"] == "5 kg bucket"
    assert payload["optionLabelOverride"] == "Package"
    assert payload["packageLabel"] == "5 kg bucket"


def test_sdb_translation_documents_seed_source_from_product_sdb(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-DOC-1", title="Natriumhypochlorit", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-DOC-1-A", variant_title="Default"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(raw_text="1. Bezeichnung\nOriginal SDB", review_status="review_required", version_label="1", document_title="SDB Original"),
        )
        documents = list_sdb_documents_for_product(session, product.id)

    assert len(documents) == 1
    assert documents[0]["title"] == "SDB Original"
    assert documents[0]["locale"] == "de-CH"
    assert documents[0]["status"] == "review_required"
    assert documents[0]["has_text"] is True
    assert documents[0]["source"] == "manual"
    assert documents[0]["generated_at_display"] is not None
    assert documents[0]["is_current"] is True
    assert documents[0]["pdf_url"] is None


def test_generated_working_sdb_pdf_is_visible_as_own_document_version(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    generated_pdf = tmp_path / "product-1404-sdb.pdf"
    generated_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-WORK-1", title="Arbeitsversion Produkt", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-WORK-1-A", variant_title="Default"),
        )
        imported_asset = create_asset_record(session, generated_pdf, product_id=product.id)
        imported_asset.filename = "supplier-original-sdb.pdf"
        imported_asset.asset_type = "sds"
        imported = ChemicalDocument(
            product_id=product.id,
            asset_id=imported_asset.id,
            document_type="sds",
            locale="en",
            language_code="en",
            region_code=None,
            title="Supplier Original",
            file_url=f"/asset-file/{imported_asset.id}",
            filename=imported_asset.filename,
            mime_type="application/pdf",
            source="imported",
            status="draft",
            is_current=True,
        )
        session.add(imported)
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung\nArbeitsversion",
                review_status="review_required",
                version_label="Arbeitsversion",
                document_title="SDB Arbeitsversion",
                generated_pdf_path=str(generated_pdf),
            ),
        )
        working = sync_product_sdb_working_document(session, product.id)
        documents = list_sdb_documents_for_product(session, product.id)

    assert working is not None
    assert working["id"] != imported.id
    assert working["source"] == "working_version"
    assert working["title"] == "SDB Arbeitsversion (Arbeitsversion)"
    assert working["pdf_url"] is not None
    imported_row = next(row for row in documents if row["id"] == imported.id)
    working_row = next(row for row in documents if row["id"] == working["id"])
    assert imported_row["source"] == "imported"
    assert imported_row["title"] == "Supplier Original"
    assert working_row["source"] == "working_version"
    assert working_row["has_pdf"] is True


def test_sdb_documents_with_missing_asset_are_not_current_or_openable(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-MISSING-ASSET-1", title="Chemie", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-MISSING-ASSET-1-A", variant_title="Default"),
        )
        document = ChemicalDocument(
            product_id=product.id,
            document_type="sds",
            locale="en",
            language_code="en",
            region_code="EN",
            title="Kaputter SDB-Link",
            file_url="/asset-file/999999",
            asset_id=999999,
            filename="missing.pdf",
            mime_type="application/pdf",
            source="imported",
            status="draft",
            is_current=True,
        )
        session.add(document)
        session.flush()
        document_id = document.id
        documents = list_sdb_documents_for_product(session, product.id)

    row = next(item for item in documents if item["id"] == document_id)
    assert row["status"] == "error"
    assert row["is_current"] is False
    assert row["pdf_url"] is None
    assert row["error_message"] == "Verknüpftes Asset fehlt; PDF-Link wurde deaktiviert."


def test_delete_working_sdb_document_clears_generated_pdf_reference(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    generated_pdf = tmp_path / "product-sdb.pdf"
    generated_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-DELETE-WORK", title="Arbeitsversion löschen", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-DELETE-WORK-A", variant_title="Default"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung\nArbeitsversion",
                review_status="review_required",
                version_label="Arbeitsversion",
                document_title="SDB Arbeitsversion",
                generated_pdf_path=str(generated_pdf),
            ),
        )
        working = sync_product_sdb_working_document(session, product.id)
        result = delete_chemical_document(session, int(working["id"]))
        documents = list_sdb_documents_for_product(session, product.id)
        sdb = get_product_sdb(session, product.id)

    assert result["delete_mode"] == "deleted"
    assert all(row["id"] != working["id"] for row in documents)
    assert sdb["generated_pdf_path"] is None


def test_working_sdb_sync_keeps_manually_set_document_status(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    generated_pdf = tmp_path / "product-sdb.pdf"
    generated_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-STATUS-WORK", title="Arbeitsversion Status", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-STATUS-WORK-A", variant_title="Default"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung\nArbeitsversion",
                review_status="review_required",
                version_label="Arbeitsversion",
                document_title="SDB Arbeitsversion",
                generated_pdf_path=str(generated_pdf),
            ),
        )
        working = sync_product_sdb_working_document(session, product.id)
        sdb_translation_service.update_chemical_document_status(session, int(working["id"]), "approved")
        synced = sync_product_sdb_working_document(session, product.id)

    assert synced["status"] == "approved"


def test_delete_imported_asset_sdb_document_archives_instead_of_recreating(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    source_pdf = tmp_path / "supplier-sdb.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-DELETE-ASSET", title="Asset SDB", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-DELETE-ASSET-A", variant_title="Default"),
        )
        asset = create_asset_record(session, source_pdf, product_id=product.id)
        asset.asset_type = "sds"
        document = ChemicalDocument(
            product_id=product.id,
            asset_id=asset.id,
            document_type="sds",
            locale="de-CH",
            language_code="de",
            region_code="CH",
            title="Lieferanten-SDB",
            file_url=f"/asset-file/{asset.id}",
            filename=asset.filename,
            mime_type=asset.mime_type,
            source="imported",
            status="draft",
            is_current=True,
        )
        session.add(document)
        session.flush()
        document_id = document.id
        result = delete_chemical_document(session, document_id)
        documents = list_sdb_documents_for_product(session, product.id)
        row = next(item for item in documents if item["id"] == document_id)

    assert result["delete_mode"] == "archived_asset_source"
    assert row["status"] == "archived"
    assert row["is_current"] is False


def test_sds_asset_is_visible_in_chemistry_and_document_registry(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A15-030", title="D1 Schweiss Fleckenentferner", source_language="de-CH", brand_name="Tintolav", status="ready", is_chemical=True),
            VariantCreate(sku="A15-030A", variant_title="D1"),
        )
        asset = Asset(
            product_id=product.id,
            filename="tintolav-d1-sweat-a15-030-sds-en.pdf",
            original_filename="tintolav-d1-sweat-a15-030-sds-en.pdf",
            mime_type="application/pdf",
            file_size=123,
            storage_path="/tmp/tintolav-d1-sweat-a15-030-sds-en.pdf",
            asset_type="sds",
            language_code="en",
            storage_provider="bunny_storage",
            object_key="prod/assets/products/1404/other/tintolav-d1-sweat-a15-030-sds-en.pdf",
            status="uploaded",
        )
        session.add(asset)
        session.commit()

        detail = get_product_detail(session, product.id)
        chemistry_rows = list_chemical_products(session)
        documents = list_sdb_documents_for_product(session, product.id)

    assert detail["sds_available"] is True
    assert detail["sds_asset_id"] == asset.id
    assert next(row for row in chemistry_rows if row["id"] == product.id)["sds_label"] == "Ja"
    assert len(documents) == 1
    assert documents[0]["asset_id"] == asset.id
    assert documents[0]["document_type"] == "sds"
    assert documents[0]["locale"] == "en"


def test_ingest_product_sdb_asset_fills_raw_text_from_pdf(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    pdf_path = tmp_path / "d1-sds-en.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "SECTION 1 Identification")
    page.insert_text((72, 92), "D1 Sweat safety data sheet")
    document.save(pdf_path)
    document.close()

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A15-030", title="D1 Schweiss Fleckenentferner", source_language="de-CH", brand_name="Tintolav", status="ready", is_chemical=True),
            VariantCreate(sku="A15-030A", variant_title="D1"),
        )
        asset = create_asset_record(session, pdf_path, product_id=product.id)
        asset.asset_type = "sds"
        asset.language_code = "en"
        session.commit()

        parsed = ingest_product_sdb_asset(session, product.id, asset.id)

    assert "D1 Sweat safety data sheet" in parsed["raw_text"]
    assert parsed["source_asset_id"] == asset.id
    assert parsed["parser_status"] == "parsed"


def test_sdb_translation_payload_accepts_structured_generated_text() -> None:
    payload = _validate_sdb_translation_payload(
        {
            "title": "SDS Draft",
            "generatedText": {
                "sections": [
                    {"number": 1, "title": "Identification", "content": "D1 Sweat"},
                    {"number": 2, "title": "Hazards identification", "content": "Review required"},
                ]
            },
            "reviewNotes": "Check Swiss emergency contact.",
        }
    )

    assert "1. Identification" in payload["generatedText"]
    assert "D1 Sweat" in payload["generatedText"]
    assert payload["reviewNotes"] == ["Check Swiss emergency contact."]


def test_sdb_translation_draft_is_always_review_required(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_SDB_MODEL", "gpt-5-mini")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    def fake_call(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, object]:
        assert "Erfinde keine Daten" in user_prompt
        assert model == "gpt-5-mini"
        assert api_key == "test-key"
        return {
            "title": "SDS Draft EN",
            "generatedText": "1. Identification\n[PRÜFEN: regionale Notrufnummer fehlt]",
            "reviewNotes": ["Abschnitt 15 prüfen"],
        }

    monkeypatch.setattr(sdb_translation_service, "_call_openai_sdb_translation_json", fake_call)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-AI-1", title="Natriumhypochlorit", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-AI-1-A", variant_title="Default"),
        )
        upsert_product_sdb(session, product.id, ProductSDBUpdate(raw_text="1. Bezeichnung\nOriginal SDB", review_status="review_required"))
        source_document = list_sdb_documents_for_product(session, product.id)[0]
        prompt = save_sdb_translation_prompt(session, name="Test SDB Prompt")
        result = generate_sdb_translation_draft(
            session,
            product_id=product.id,
            source_document_id=source_document["id"],
            source_locale="de-CH",
            target_locale="en-GB",
            target_region="GB",
            prompt_id=prompt.id,
        )
        documents = list_sdb_documents_for_product(session, product.id)

    draft = next(row for row in documents if row["created_by_ai"])
    assert result["status"] == "review_required"
    assert draft["status"] == "review_required"
    assert draft["locale"] == "en-GB"
    assert draft["region_code"] == "GB"
    assert draft["ai_model"] == "gpt-5-mini"
    assert "KI-ENTWURF" in draft["review_note"]
    assert draft["source"] == "generated"
    assert draft["generated_at_display"] is not None
    assert draft["filename"].endswith(".txt")


def test_sdb_document_asset_metadata_is_visible_on_product_assets(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    sdb_pdf = asset_dir / "SDB_DE.pdf"
    sdb_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-ASSET-1", title="Chemie", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-ASSET-1-A", variant_title="Default"),
        )
        asset = create_asset_record(session, sdb_pdf, product_id=product.id)
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung\nOriginal SDB",
                review_status="approved",
                document_title="SDB Original",
                source_asset_id=asset.id,
            ),
        )
        documents = list_sdb_documents_for_product(session, product.id)
        detail = get_product_detail(session, product.id)

    assert documents[0]["asset_id"] == asset.id
    assert documents[0]["filename"] == "SDB_DE.pdf"
    assert documents[0]["pdf_url"] == f"/asset-file/{asset.id}"
    asset_row = next(row for row in detail["assets"] if row["id"] == asset.id)
    assert asset_row["sdb_document_id"] == documents[0]["id"]
    assert asset_row["sdb_document_type"] == "SDB"
    assert asset_row["sdb_language_code"] == "de-CH"
    assert asset_row["sdb_status"] == "approved"


def test_backfill_sdb_documents_from_assets_is_explicit_and_non_destructive(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    sdb_pdf = asset_dir / "safety-data-sheet_de-CH.pdf"
    sdb_pdf.write_bytes(b"%PDF-1.4\n")

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-BACKFILL-1", title="Chemie", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-BACKFILL-1-A", variant_title="Default"),
        )
        asset = create_asset_record(session, sdb_pdf, product_id=product.id)
        dry_run = backfill_sdb_documents_from_assets(session, product.id, commit=False)
        committed = backfill_sdb_documents_from_assets(session, product.id, commit=True)
        documents = list_sdb_documents_for_product(session, product.id)

    assert dry_run["created_count"] == 1
    assert dry_run["committed"] is False
    assert committed["created_count"] == 1
    assert documents[0]["asset_id"] == asset.id
    assert documents[0]["locale"] == "de-CH"
    assert documents[0]["source"] == "imported"


def test_sdb_document_title_link_uses_existing_external_pdf_url(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    external_pdf = "https://example.com/sdb/product-1420-de-ch.pdf"

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-LINK-1", title="<SDB & Produkt>", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-LINK-1-A", variant_title="Default"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                raw_text="1. Bezeichnung\nOriginal SDB",
                pdf_url=external_pdf,
                review_status="approved",
                document_title="<Sicherheitsdatenblatt & Test>",
            ),
        )
        documents = list_sdb_documents_for_product(session, product.id)

    assert documents[0]["title"] == "<Sicherheitsdatenblatt & Test>"
    assert documents[0]["pdf_url"] == external_pdf


def test_sdb_title_column_uses_link_renderer_without_breaking_title_sorting() -> None:
    title_column = next(column for column in CHEMICAL_DOCUMENT_COLUMNS if column["field"] == "title")

    assert title_column["cellRenderer"] == "SdbTitleLinkCell"
    assert title_column["field"] == "title"
    assert title_column.get("sortable", True) is True


def test_sdb_document_section_parser_does_not_treat_subsections_as_main_sections() -> None:
    text = """
ABSCHNITT 7: Handhabung und Lagerung
7.2 Bedingungen zur sicheren Lagerung
Lagerklasse 8B

ABSCHNITT 8: Begrenzung und Überwachung der Exposition
DNEL-Werte

ABSCHNITT 14: Angaben zum Transport
14.3 Transportgefahrenklassen
8 / 8 / 8
Gefahrzettel: [PRÜFEN: Gefahrzettel im Original nicht dargestellt]
14.5 Umweltgefahren
ADR/RID: UMWELTGEFÄHRDEND

ABSCHNITT 15: Rechtsvorschriften
WGK 2
"""
    sections = sdb_translation_service._sections_from_document_text(text)

    assert sections["section_7"]["title"] == "Handhabung und Lagerung"
    assert sections["section_8"]["title"] == "Begrenzung und Überwachung der Exposition"
    assert sections["section_14"]["title"] == "Angaben zum Transport"
    assert "14.5 Umweltgefahren" in sections["section_14"]["content"]
    assert "8 / 8 / 8" in sections["section_14"]["content"]
    assert "Gefahrzettel" in sections["section_14"]["content"]


def test_sdb_translation_requires_source_document(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SDB-NOSOURCE", title="Chemie", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="SDB-NOSOURCE-A", variant_title="Default"),
        )
        result = generate_sdb_translation_draft(
            session,
            product_id=product.id,
            source_document_id=0,
            target_locale="fr-CH",
            target_region="CH",
        )
        prompts = list_sdb_translation_prompts(session)

    assert result["status"] == "failed"
    assert "Ausgangs-SDB" in result["message"]
    assert prompts


def test_product_stores_structured_chemical_safety_json(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="CHEM-SAFETY", title="Chem Safety", brand_name="VOXSTER", status="draft", is_chemical=True),
            VariantCreate(sku="CHEM-SAFETY-A", variant_title="Default"),
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
                ghs_pictograms="GHS05|GHS09",
                signal_word="GEFAHR",
                hazard_class="8",
                ufi="0A80-10U4-F00M-UJ45",
                voc_content_percent="1.12",
                adr_relevant=True,
                chemical_safety_json={
                    "ghs_pictograms": ["GHS05", "GHS09"],
                    "signal_word": "danger",
                    "adr_pictograms": ["ADR_8", "ADR_pollution"],
                    "adr_class": "8",
                    "environmentally_hazardous": True,
                },
            ),
        )
        detail = get_product_detail(session, product.id)

    assert detail["chemical_safety_json"]["ghs_pictograms"] == ["GHS05", "GHS09"]
    assert detail["chemical_safety_json"]["adr_pictograms"] == ["ADR_8", "ADR_pollution"]
    assert detail["chemical_safety_json"]["signal_word"] == "danger"
    assert detail["chemical_safety_json"]["environmentally_hazardous"] is True
    assert detail["ufi"] == "0A80-10U4-F00M-UJ45"
    assert detail["voc_content_percent"] == "1.12"


def test_product_dedupe_dry_run_does_not_change_data(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        master, _ = create_product(
            session,
            ProductCreate(sku="VOX-1", title="TintoLove Red Shirt", brand_name="TintoLove", status="ready"),
            VariantCreate(sku="VOX-1-RED", variant_title="Red", barcode="7610000000011", price="19.90", currency="CHF"),
        )
        duplicate, _ = create_product(
            session,
            ProductCreate(sku="TINTO-1", title="TintoLove Red Shirt", brand_name="TintoLove", status="ready"),
            VariantCreate(sku="TINTO-1-RED", variant_title="Red", barcode="7610000000011", cost_price="8.50", cost_currency="EUR"),
        )
        result = merge_product_duplicates(session, confidence="HIGH", apply=False)
        unchanged_duplicate = session.get(type(duplicate), duplicate.id)
        unchanged_master = session.get(type(master), master.id)
        session.commit()

    assert result["dry_run"] is True
    assert result["groups_count"] >= 1
    assert unchanged_duplicate.status == "ready"
    assert unchanged_duplicate.merged_into_product_id is None
    assert unchanged_master.dedupe_status is None


def test_product_dedupe_merges_assets_prices_family_and_archives_duplicate(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    master_image = asset_dir / "master.jpg"
    duplicate_pdf = asset_dir / "manual.pdf"
    duplicate_image_same = asset_dir / "master-copy.jpg"
    master_image.write_bytes(b"same-image")
    duplicate_image_same.write_bytes(b"same-image")
    duplicate_pdf.write_bytes(b"%PDF-1.4\nmanual")

    with SessionLocal() as session:
        master, master_variant = create_product(
            session,
            ProductCreate(sku="VOX-DEDUP", title="TintoLove Blue Shirt", brand_name="TintoLove", status="ready", source_url="https://voxster.ch/p/blue"),
            VariantCreate(sku="VOX-DEDUP-BLUE", variant_title="Blue", option_name="Color", option_value="Blue", barcode="7610000000022", price="29.90", currency="CHF"),
        )
        duplicate, duplicate_variant = create_product(
            session,
            ProductCreate(sku="TINTO-DEDUP", title="TintoLove Blue Shirt", brand_name="TintoLove", status="ready", family_key="TL-BLUE", source_url="https://tintolove.example/blue"),
            VariantCreate(sku="TINTO-DEDUP-BLUE", variant_title="Blue supplier", option_name="Color", option_value="Blue", barcode="7610000000022", cost_price="11.10", cost_currency="EUR"),
        )
        create_asset_record(session, master_image, product_id=master.id)
        create_asset_record(session, duplicate_image_same, product_id=duplicate.id)
        pdf_asset = create_asset_record(session, duplicate_pdf, product_id=duplicate.id)
        upsert_variant_price_tier(session, VariantPriceTierCreate(variant_id=duplicate_variant.id, price_type="purchase", min_qty=1, price="10.00", currency="EUR"))

        result = merge_product_duplicates(session, confidence="HIGH", apply=True, yes=True)
        session.commit()
        session.refresh(master)
        session.refresh(master_variant)
        session.refresh(duplicate)
        has_purchase_tier = any(tier.price_type == "purchase" and tier.price == Decimal("10.00") for tier in master_variant.price_tiers)
        master_asset_count = len([asset for asset in master.assets if asset.checksum])

    assert result["dry_run"] is False
    assert duplicate.status == "archived"
    assert duplicate.merged_into_product_id == master.id
    assert master.dedupe_status == "master"
    assert master.family_key == "TL-BLUE"
    assert master_variant.price == Decimal("29.90")
    assert master_variant.cost_price == Decimal("10.00")
    assert has_purchase_tier
    assert pdf_asset.product_id == master.id
    assert master_asset_count == 2


def test_product_dedupe_low_confidence_is_preview_only(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        create_product(
            session,
            ProductCreate(sku="LOW-1", title="Nearly Same Product", brand_name="A", status="ready"),
            VariantCreate(sku="LOW-1-A", variant_title="Default"),
        )
        create_product(
            session,
            ProductCreate(sku="LOW-2", title="Nearly Same Product", brand_name="B", status="ready"),
            VariantCreate(sku="LOW-2-A", variant_title="Default"),
        )
        previews = analyze_product_duplicates(session, min_confidence="LOW")

    assert any(row["confidence"] == "LOW" for row in previews)


def test_product_duplicate_group_workflow_preview_master_ignore_and_merge(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        master, master_variant = create_product(
            session,
            ProductCreate(sku="GUI-MASTER", title="TintoLove Merge Product", brand_name="TintoLove", status="ready"),
            VariantCreate(sku="GUI-MASTER-RED", variant_title="Red", barcode="7610000000999", price="19.90", currency="CHF"),
        )
        duplicate, duplicate_variant = create_product(
            session,
            ProductCreate(sku="GUI-DUP", title="TintoLove Merge Product", brand_name="TintoLove", status="ready", family_key="TL-MERGE"),
            VariantCreate(sku="GUI-DUP-RED", variant_title="Red", barcode="7610000000999", cost_price="7.50", cost_currency="EUR"),
        )

        scan = scan_duplicate_groups(session, min_confidence="HIGH")
        group_id = scan["groups"][0]["id"]
        detail = get_duplicate_group_detail(session, group_id)
        assert detail is not None
        assert detail["duplicate_count"] == 1

        changed = set_duplicate_group_master(session, group_id, duplicate.id)
        assert changed["master_product_id"] == duplicate.id

        set_duplicate_group_master(session, group_id, master.id)
        preview = create_duplicate_group_preview(session, group_id)
        assert preview["master_product_id"] == master.id
        assert preview["duplicate_product_ids"] == [duplicate.id]
        assert preview["merged_prices_count"] >= 1

        ignored = ignore_duplicate_group(session, group_id, reason="test")
        assert ignored["status"] == "ignored"

        master2, master_variant2 = create_product(
            session,
            ProductCreate(sku="GUI-MASTER-2", title="TintoLove Merge Product 2", brand_name="TintoLove", status="ready"),
            VariantCreate(sku="GUI-MASTER-2-RED", variant_title="Red", barcode="7610000000888", price="29.90", currency="CHF"),
        )
        duplicate2, _ = create_product(
            session,
            ProductCreate(sku="GUI-DUP-2", title="TintoLove Merge Product 2", brand_name="TintoLove", status="ready", family_key="TL-MERGE-2"),
            VariantCreate(sku="GUI-DUP-2-RED", variant_title="Red", barcode="7610000000888", cost_price="9.50", cost_currency="EUR"),
        )
        scan = scan_duplicate_groups(session, min_confidence="HIGH")
        merge_group_id = next(row["id"] for row in scan["groups"] if row["status"] != "ignored" and row["master_product_id"] == master2.id)
        result = merge_duplicate_group(session, merge_group_id, yes=True)
        session.commit()
        duplicate_after = session.get(type(duplicate2), duplicate2.id)
        master_after = session.get(type(master2), master2.id)
        session.refresh(master_variant2)

    assert result["status"] == "merged"
    assert duplicate_after.status == "archived"
    assert duplicate_after.merged_into_product_id == master_after.id
    assert master_after.family_key == "TL-MERGE-2"
    assert master_variant2.price == Decimal("29.90")
    assert master_variant2.cost_price == Decimal("9.50")


def test_list_products_hides_archived_by_default_and_can_filter(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        active, _ = create_product(
            session,
            ProductCreate(sku="ACTIVE-PRODUCT", title="Active Product", status="active"),
            VariantCreate(sku="ACTIVE-PRODUCT-1", variant_title="Default"),
        )
        archived, _ = create_product(
            session,
            ProductCreate(sku="ARCHIVED-PRODUCT", title="Archived Product", status="archived"),
            VariantCreate(sku="ARCHIVED-PRODUCT-1", variant_title="Default"),
        )

        default_rows = list_products(session)
        archived_rows = list_products(session, archive_filter="archived")
        all_rows = list_products(session, archive_filter="all")

    assert active.id in {row["id"] for row in default_rows}
    assert archived.id not in {row["id"] for row in default_rows}
    assert {row["id"] for row in archived_rows} == {archived.id}
    assert {active.id, archived.id}.issubset({row["id"] for row in all_rows})


def test_variant_archive_filter_and_safe_delete(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        _product, active_variant = create_product(
            session,
            ProductCreate(sku="VARIANT-ACTIVE", title="Variant Active", status="active"),
            VariantCreate(sku="VARIANT-ACTIVE-1", variant_title="Active"),
        )
        _product2, archived_variant = create_product(
            session,
            ProductCreate(sku="VARIANT-ARCHIVED", title="Variant Archived", status="active"),
            VariantCreate(sku="VARIANT-ARCHIVED-1", variant_title="Archived"),
        )
        archive_variants(session, [archived_variant.id])

        default_rows = list_variants(session)
        archived_rows = list_variants(session, archive_filter="archived")
        all_rows = list_variants(session, archive_filter="all")

    assert active_variant.id in {row["id"] for row in default_rows}
    assert archived_variant.id not in {row["id"] for row in default_rows}
    assert {row["id"] for row in archived_rows} == {archived_variant.id}
    assert {active_variant.id, archived_variant.id}.issubset({row["id"] for row in all_rows})


def test_variant_delete_archives_when_relations_exist(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, variant_with_price = create_product(
            session,
            ProductCreate(sku="VARIANT-DELETE-ARCHIVE", title="Variant Delete Archive", status="active"),
            VariantCreate(sku="VARIANT-DELETE-ARCHIVE-1", variant_title="Archive", price="12.50", currency="CHF"),
        )
        _product2, variant_without_relations = create_product(
            session,
            ProductCreate(sku="VARIANT-DELETE-HARD", title="Variant Delete Hard", status="active"),
            VariantCreate(sku="VARIANT-DELETE-HARD-1", variant_title="Delete"),
        )

        result = delete_or_archive_variants(session, [variant_with_price.id, variant_without_relations.id])
        session.commit()
        archived_variant = session.get(type(variant_with_price), variant_with_price.id)
        deleted_variant = session.get(type(variant_without_relations), variant_without_relations.id)

    assert result["archived_due_to_relations"] == 1
    assert result["deleted"] == 1
    assert archived_variant.status == "archived"
    assert archived_variant.product_id == product.id
    assert deleted_variant is None


def test_product_data_enrichment_preview_and_apply_missing_fields(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html; charset=utf-8"}
        text = """
        <html>
          <head>
            <title>Demo Produkt SEO Titel</title>
            <meta name="description" content="Kurzer SEO Beschreibungstext fuer das Demo Produkt.">
          </head>
          <body>
            <h1>Demo Produkt</h1>
            <p>Dies ist eine lange Produktbeschreibung fuer das Demo Produkt mit genug Inhalt fuer die Vorschlagslogik.</p>
          </body>
        </html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="ENRICH-1", title="Demo Produkt", status="active", source_language="de-CH"),
            VariantCreate(sku="ENRICH-1-1", variant_title="Default"),
        )
        product.source_url_final = "https://voxster.ch/demo-produkt"
        preview = preview_product_data_enrichment(session, [product.id], fields=["short_description", "description", "seo_title", "seo_description"])
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]
        result = apply_product_data_enrichment(session, suggestions)
        session.commit()
        detail = get_product_detail(session, product.id)
        logs = session.query(ProductEnrichmentLog).filter(ProductEnrichmentLog.product_id == product.id).all()

    assert preview["products_with_suggestions"] == 1
    assert result["applied_count"] == 4
    assert any(log.status == "suggested" and log.dry_run is True and log.search_method == "final_url" for log in logs)
    assert any(log.status == "accepted" and log.created_by is None for log in logs)
    assert detail["description"].startswith("Dies ist eine lange Produktbeschreibung")
    translation = detail["translations"][0]
    assert translation["short_description"].startswith("Kurzer SEO Beschreibungstext")
    assert translation["seo_title"] == "Demo Produkt SEO Titel"
    assert translation["seo_description"].startswith("Kurzer SEO Beschreibungstext")


def test_product_data_enrichment_does_not_overwrite_existing_fields(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = '<html><head><meta name="description" content="Neue Kurzbeschreibung."></head><body><p>Neue Beschreibung mit ausreichend Inhalt fuer Vorschlag.</p></body></html>'

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="ENRICH-2", title="Demo Produkt 2", status="active", source_language="de-CH", description="Bestehende Beschreibung"),
            VariantCreate(sku="ENRICH-2-1", variant_title="Default"),
        )
        product.source_url_final = "https://voxster.ch/demo-produkt-2"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description"], overwrite_existing=False)

    assert preview["results"][0]["status"] == "no_missing_fields"
    assert preview["results"][0]["suggestions"] == []


def test_product_update_persists_source_urls_and_enrichment_uses_multiple_sources(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    calls: list[str] = []

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = '<html><head><meta name="description" content="Source URLs Beschreibung aus zweiter Quelle."></head><body><p>Source URLs Beschreibung aus zweiter Quelle mit ausreichend Text.</p></body></html>'

        def __init__(self, url: str):
            self.url = url

        def raise_for_status(self) -> None:
            if "broken" in self.url:
                raise requests.HTTPError("404")

    def fake_get(url, *_args, **_kwargs):
        calls.append(url)
        return FakeResponse(url)

    import requests

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SOURCE-URLS", title="Source URLs", status="active", source_language="de-CH"),
            VariantCreate(sku="SOURCE-URLS-1", variant_title="Default"),
        )
        update_product(
            session,
            product.id,
            ProductUpdate(
                sku=product.sku,
                title=product.title,
                status="active",
                source_language="de-CH",
                description=None,
                source_url="https://example.com/broken\nhttps://example.com/working",
                source_url_final="https://example.com/final",
            ),
        )
        preview = preview_product_data_enrichment(session, [product.id], fields=["short_description"], sources=["source_url"])
        detail = get_product_detail(session, product.id)

    assert detail["source_url"] == "https://example.com/broken\nhttps://example.com/working"
    assert detail["source_url_final"] == "https://example.com/final"
    assert calls[:2] == ["https://example.com/broken", "https://example.com/working"]
    assert preview["products_with_suggestions"] == 1


def test_product_data_enrichment_extracts_visible_body_text_without_paragraphs(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak</title></head><body>
        <div class="product-description">
        Jolly Smak ist ein universelles Vordetachiermittel zum Anbürsten oder Aufsprühen mit ausgezeichneten Wasserbindungseigenschaften.
        Es hat eine hohe Wirksamkeit gegen Flecken auf Wasserbasis und eine spezielle Formel für stark verschmutzte Kleidungsstücke.
        Bietet ein hervorragendes Preis-Leistungs-Verhältnis.
        </div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000XX", title="Jolly Smak", status="active", source_language="de-CH"),
            VariantCreate(sku="A01-000XX-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.voxster.ch/reinigen/anb-rstmittel/jolly-smak-anburstmittel-fur-per.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description"])
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]

    assert preview["products_with_suggestions"] == 1
    assert suggestions[0]["field_name"] == "description"
    assert "universelles Vordetachiermittel" in suggestions[0]["suggested_value"]


def test_product_data_enrichment_ignores_shop_boilerplate(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Shop</title></head><body>
        <p>JavaScript scheint in Ihrem Browser deaktiviert zu sein. Sie müssen JavaScript in Ihrem Browser aktivieren, um alle Funktionen in diesem Shop nutzen zu können.</p>
        <p>Another custom CMS block displayed as a tab. You can use it to display delivery, returns or payment information.</p>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000XX", title="Jolly Smak", status="active", source_language="de-CH"),
            VariantCreate(sku="A01-000XX-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.voxster.ch/reinigen/anb-rstmittel/jolly-smak-anburstmittel-fur-per.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description"])

    assert preview["products_with_suggestions"] == 0
    assert preview["results"][0]["suggestions"] == []
    assert any("Kein sicherer Vorschlag" in warning for warning in preview["results"][0]["warnings"])


def test_product_data_enrichment_uses_configured_domain_search_snippet(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, *_args, **_kwargs):
        if "duckduckgo.com" in url:
            return FakeResponse(
                """
                <html><body>
                  <a class="result__a" href="https://www.voxster.ch/reinigen/jolly-smak.html">Jolly Smak, Anbürstmittel für PER</a>
                  <div class="result__snippet">Jolly Smak, Anbürstmittel für PER ist ein universelles Vordetachiermittel zum Anbürsten oder Aufsprühen mit ausgezeichneten Wasserbindungseigenschaften.</div>
                </body></html>
                """
            )
        return FakeResponse(
            """
            <html><body>
            <p>JavaScript scheint in Ihrem Browser deaktiviert zu sein. Another custom CMS block displayed as a tab.</p>
            </body></html>
            """
        )

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000XX", title="Jolly Smak, Anbürstmittel für PER", status="active", source_language="de-CH"),
            VariantCreate(sku="A01-000XX-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.voxster.ch/reinigen/jolly-smak.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description"], sources=["final_url", "configured_domains"])
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]

    assert preview["products_with_suggestions"] == 1
    assert suggestions[0]["search_method"] == "configured_domain_search"
    assert "universelles Vordetachiermittel" in suggestions[0]["suggested_value"]


def test_product_data_enrichment_extracts_magento_short_description_with_configured_domains(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    calls: list[str] = []

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><body>
          <p>JavaScript scheint in Ihrem Browser deaktiviert zu sein.</p>
          <div class="product-name"><h1>Jolly Smak, Anbürstmittel für PER</h1></div>
          <div class="short-description">
            <div class="std">Jolly Smak ist ein universelles Vordetachiermittel zum Anbürsten oder Aufsprühen mit ausgezeichneten Wasserbindungseigenschaften.</div>
            <div class="main-description">Preis pro Kanister</div>
          </div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, *_args, **_kwargs):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000XX", title="Jolly Smak, Anbürstmittel für PER", status="active", source_language="de-CH"),
            VariantCreate(sku="A01-000XX-1", variant_title="Default"),
        )
        product.source_url = "https://www.voxster.ch/reinigen/jolly-smak.html"
        preview = preview_product_data_enrichment(
            session,
            [product.id],
            fields=["short_description", "description", "seo_description"],
            sources=["configured_domains"],
            overwrite_existing=True,
        )
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]
        suggestions_by_field = {row["field_name"]: row for row in suggestions}

    assert calls[0] == "https://www.voxster.ch/reinigen/jolly-smak.html"
    assert preview["products_with_suggestions"] == 1
    assert "universelles Vordetachiermittel" in suggestions_by_field["description"]["suggested_value"]
    assert "universelles Vordetachiermittel" in suggestions_by_field["short_description"]["suggested_value"]
    assert "universelles Vordetachiermittel" in suggestions_by_field["seo_description"]["suggested_value"]


def test_product_data_enrichment_extracts_tintolav_product_blocks(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak - Tintolav</title><meta property="og:image" content="https://www.tintolav.com/images/dacshop/upload/a01-000kjollysmak-10kg.png"></head><body>
          <nav>Jolly Smak - Tintolav Home About Back Mission and Values Quality Brands Certifications Services Showcase Products Back Tintolav</nav>
          <h1>Jolly Smak A01-000K</h1>
          <div id="dacshop_product_description_main" class="dacshop_product_description_main" itemprop="description">
            <h4>Description</h4>
            <p>Universal pre-spotter for dry-cleaning, to be used with a brush or by spraying it.</p>
            <ul>
              <li>High effectiveness on water-based stains.</li>
              <li>Specific formula for heavily soiled garments.</li>
              <li>Suitable for all kinds of fabrics.</li>
              <li>It does not make foam in the distiller.</li>
              <li>Excellent water-binding power.</li>
            </ul>
          </div>
          <div id="dacshop_product_custom_info_main" class="dacshop_product_custom_info_main">
            <h4>Specifications</h4>
            <table>
              <tr><td><span><label for="howtouse">How To Use</label></span></td><td><span class="dacshop_product_custom_value">Jolly Smak has to be used diluted in water, with a brush or by spraying it. Apply on the dirty parts of garments right before the wash.</span></td></tr>
              <tr><td><span><label for="ingredients">Ingredients</label></span></td><td><span class="dacshop_product_custom_value">aqua, cocamide dea, sodium dodecylbenzene sulfonate.</span></td></tr>
              <tr><td><span><label for="ingredientsearch">Ingredient Search</label></span></td><td><span class="dacshop_product_custom_value">Search for the listed ingredients with the EU Cosing Database</span></td></tr>
              <tr><td><span><label for="function">Function</label></span></td><td><span class="dacshop_product_custom_value">Pre-spotter</span></td></tr>
            </table>
          </div></div><div id="dacshop_product_files_main"></div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000K", title="Jolly Smak", status="active", source_language="en"),
            VariantCreate(sku="A01-000K-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        preview = preview_product_data_enrichment(
            session,
            [product.id],
            fields=["short_description", "description", "technical_features_text"],
            sources=["final_url"],
            overwrite_existing=True,
        )
        suggestions = {item["field_name"]: item for row in preview["results"] for item in row["suggestions"]}

    assert "Home About Back" not in suggestions["description"]["suggested_value"]
    assert "Universal pre-spotter" in suggestions["description"]["suggested_value"]
    assert "- High effectiveness" in suggestions["description"]["suggested_value"]
    assert "High effectiveness" in suggestions["description"]["suggested_value"]
    assert "How to use:" in suggestions["description"]["suggested_value"]
    assert "Jolly Smak has to be used diluted" in suggestions["description"]["suggested_value"]
    assert "Function:" in suggestions["description"]["suggested_value"]
    assert "Pre-spotter" in suggestions["description"]["suggested_value"]
    assert "Ingredients:" in suggestions["description"]["suggested_value"]
    assert "How To Use" in suggestions["technical_features_text"]["suggested_value"]
    assert "Jolly Smak has to be used diluted" in suggestions["technical_features_text"]["suggested_value"]
    assert "Ingredient Search" in suggestions["technical_features_text"]["suggested_value"]
    assert "Function: Pre-spotter" in suggestions["technical_features_text"]["suggested_value"]
    assert suggestions["sku"]["suggested_value"] == "A01-000K"


def test_tintolav_enrichment_stores_candidates_without_direct_de_ch_write(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak - Tintolav</title><meta property="og:image" content="https://www.tintolav.com/images/dacshop/upload/a01-000kjollysmak-10kg.png"></head><body>
          <p>Another custom CMS block displayed as a tab. You can use it to display delivery.</p>
          <h1>Jolly Smak A01-000K</h1>
          <div id="dacshop_product_description_main"><h4>Description</h4>
            Universal pre-spotter for dry-cleaning, to be used with a brush or by spraying it.
            High effectiveness on water-based stains.
          </div>
          <div id="dacshop_product_custom_info_main">
            <table>
              <tr><td><label>How To Use</label></td><td><span class="dacshop_product_custom_value">Apply on the dirty parts before the wash.</span></td></tr>
              <tr><td><label>Ingredients</label></td><td><span class="dacshop_product_custom_value">aqua, cocamide dea.</span></td></tr>
              <tr><td><label>Ingredient Search</label></td><td><span class="dacshop_product_custom_value">Search for the listed ingredients with the EU Cosing Database</span></td></tr>
              <tr><td><label>Function</label></td><td><span class="dacshop_product_custom_value">Pre-spotter</span></td></tr>
              <tr><td><label>Packaging</label></td><td><span class="dacshop_product_custom_value">10kg</span></td></tr>
            </table>
          </div></div><div id="dacshop_product_files_main">
            <a href="/files/jolly-smak-sds.pdf">Safety Data Sheet</a>
            <a href="/files/jolly-smak-tds.pdf">Technical Sheet</a>
          </div>
          <img src="/media/jolly-smak.png" alt="Jolly Smak">
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000K", title="Jolly Smak", status="active", source_language="de-CH"),
            VariantCreate(sku="A01-000K-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description", "short_description"], sources=["final_url"])
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]
        apply_result = apply_product_data_enrichment(session, suggestions)
        session.commit()
        detail = get_product_detail(session, product.id)
        text_candidates = session.query(ProductEnrichmentCandidate).filter(ProductEnrichmentCandidate.product_id == product.id).all()
        asset_candidates = session.query(ProductAssetCandidate).filter(ProductAssetCandidate.product_id == product.id).all()

    assert preview["products_with_suggestions"] == 1
    assert any(row["status"] == "needs_translation" and row["source_language"] == "en" for row in suggestions)
    assert apply_result["applied_count"] == 0
    assert detail["description"] is None
    assert any(candidate.field_name == "description" and candidate.source_language == "en" for candidate in text_candidates)
    assert any(candidate.field_name == "ingredients" and "aqua" in (candidate.source_value or "") for candidate in text_candidates)
    assert any(candidate.field_name == "ingredient_search" and "EU Cosing" in (candidate.source_value or "") for candidate in text_candidates)
    assert any(candidate.field_name == "function" and candidate.source_value == "Pre-spotter" for candidate in text_candidates)
    assert any(candidate.field_name == "specifications" and "Ingredient Search:" in (candidate.source_value or "") for candidate in text_candidates)
    assert any(candidate.asset_type == "sds" for candidate in asset_candidates)
    assert any(candidate.asset_type == "technical_datasheet" for candidate in asset_candidates)
    assert any(candidate.asset_type == "image" for candidate in asset_candidates)


def test_tintolav_enrichment_overwrite_uses_source_language_translation(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak - Tintolav</title></head><body>
          <h1>Jolly Smak A01-000K</h1>
          <div id="dacshop_product_description_main">
            <h4>Description</h4>
            <p>Universal pre-spotter for dry-cleaning, to be used with a brush or by spraying it.</p>
            <ul>
              <li>High effectiveness on water-based stains.</li>
              <li>Specific formula for heavily soiled garments.</li>
              <li>Suitable for all kinds of fabrics.</li>
              <li>It does not make foam in the distiller.</li>
              <li>Excellent water-binding power.</li>
            </ul>
          </div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000K", title="Jolly Smak", status="active", source_language="de-CH", description="Bestehende deutsche Beschreibung"),
            VariantCreate(sku="A01-000K-1", variant_title="Default"),
        )
        session.add(
            ProductTranslation(
                product_id=product.id,
                language_code="en",
                title="Old English Title",
                short_description="Old English short",
                description="Old English description",
            )
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        preview = preview_product_data_enrichment(
            session,
            [product.id],
            fields=["title", "description", "seo_title", "seo_description", "slug"],
            sources=["final_url"],
            overwrite_existing=True,
        )
        suggestions = [
            item
            for row in preview["results"]
            for item in row["suggestions"]
            if item["field_name"] in {"title", "description", "seo_title", "seo_description", "slug"}
        ]
        suggestion = next(item for item in suggestions if item["field_name"] == "description")
        apply_result = apply_product_data_enrichment(session, suggestions, overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)
        translation = next(row for row in detail["translations"] if row["language_code"] == "en")

        assert suggestion["status"] == "suggested"
        assert suggestion["source_language"] == "en"
        assert suggestion["target_locale"] == "en"
        assert {item["field_name"] for item in suggestions} == {"title", "description", "seo_title", "seo_description", "slug"}
        assert all(item["target_locale"] == "en" for item in suggestions)
        assert "Universal pre-spotter" in suggestion["suggested_value"]
        assert "Excellent water-binding power" in suggestion["suggested_value"]
        assert apply_result["applied_count"] == 5
        assert apply_result["skipped_count"] == 0
        assert detail["description"] == "Bestehende deutsche Beschreibung"
        assert translation["title"] == "Jolly Smak A01-000K"
        assert "Universal pre-spotter" in translation["description"]
        assert translation["seo_title"] == "Jolly Smak A01-000K"
        assert "Universal pre-spotter" in translation["seo_description"]
        assert translation["slug"] == "jolly-smak-pre-spotter"


def test_product_text_enrichment_formats_markdown_and_applies_locale_safely(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="MD-1",
                title="Bügeltisch-Überzug HR3",
                handle="buegeltisch-ueberzug-hr3",
                source_language="de-CH",
                description="<p>Komplett fertigkonfektionierter Überzug mit Polsterung für Absaug-/Blas-Saug-Bügeltische. Besteht aus Überzugsstoff HR3 (100 % Polyester) + Silikonpad + Molton + Verteilnetz + Gegenzugkordel. Preis pro Stück.</p>",
            ),
        )
        result = preview_product_text_enrichment(
            session,
            [product.id],
            source_locale="de-CH",
            target_locales=["de-CH"],
            fields=["description", "short_description", "seo_title", "seo_description", "slug"],
            options=TextEnrichmentOptions(only_missing=False, overwrite_existing=True, markdown=True, generate_seo=True, generate_slug=True),
        )
        rows = {row["field_name"]: row for row in result["suggestions"]}
        assert "### Material / Technische Angaben" in rows["description"]["suggested_value"]
        assert rows["short_description"]["suggested_value"]
        assert len(rows["short_description"]["suggested_value"]) <= 250
        assert rows["slug"]["suggested_value"] == "buegeltisch-ueberzug-hr3"

        apply_product_text_enrichment(session, result["suggestions"], overwrite_existing=True)
        session.commit()

    with SessionLocal() as session:
        detail = get_product_detail(session, product.id)
        assert "### Material / Technische Angaben" in detail["description"]
        translation = next(row for row in detail["translations"] if row["language_code"] == "de-CH")
        assert translation["slug"] == "buegeltisch-ueberzug-hr3"
        assert translation["seo_description"]


def test_product_text_enrichment_dry_run_does_not_write_and_protects_existing_values(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="MD-2",
                title="Bestehender Titel",
                handle="bestehender-titel",
                source_language="de-CH",
                description="Bestehende Beschreibung.",
            ),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="en",
                title="Existing English title",
                description="Existing English description.",
                slug="existing-english-title",
            ),
        )
        result = preview_product_text_enrichment(
            session,
            [product.id],
            source_locale="de-CH",
            target_locales=["en"],
            fields=["title", "description", "slug"],
            options=TextEnrichmentOptions(only_missing=True, overwrite_existing=False, markdown=True, generate_slug=True),
        )
        assert {row["status"] for row in result["suggestions"]} == {"unverändert"}
        session.commit()

    with SessionLocal() as session:
        detail = get_product_detail(session, product.id)
        translation = next(row for row in detail["translations"] if row["language_code"] == "en")
        assert translation["title"] == "Existing English title"
        assert translation["description"] == "Existing English description."


def test_product_text_enrichment_does_not_copy_source_language_into_missing_target(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="MD-3",
                title="Deutscher Produkttitel",
                handle="deutscher-produkttitel",
                source_language="de-CH",
                description="Deutsche Beschreibung mit Produktnutzen.",
            ),
        )
        result = preview_product_text_enrichment(
            session,
            [product.id],
            source_locale="de-CH",
            target_locales=["en"],
            fields=["title", "description", "seo_description", "slug"],
            options=TextEnrichmentOptions(only_missing=True, overwrite_existing=False, markdown=True, generate_seo=True, generate_slug=True),
        )
        assert all(not row["suggested_value"] for row in result["suggestions"])
        assert {row["status"] for row in result["suggestions"]} == {"leer"}


def test_product_text_enrichment_removes_trailing_article_code_from_title(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="A15-030",
                title="D1 Sweat A15-030A",
                handle="d1-sweat-a15-030a",
                source_language="en",
                description="Specific stain remover.",
            ),
        )
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product.id,
                language_code="en",
                title="D1 Sweat A15-030A",
                short_description="Specific stain remover for sweat and food stains.",
                seo_title="D1 Sweat A15-030A",
                slug="d1-sweat-a15-030a",
            ),
        )
        result = preview_product_text_enrichment(
            session,
            [product.id],
            source_locale="en",
            target_locales=["en"],
            fields=["title", "seo_title", "slug"],
            options=TextEnrichmentOptions(only_missing=False, overwrite_existing=True, remove_external_numbers=True),
        )
        rows = {row["field_name"]: row for row in result["suggestions"]}
        assert rows["title"]["suggested_value"] == "D1 Sweat Stain Remover"
        assert rows["seo_title"]["suggested_value"] == "D1 Sweat Stain Remover"
        assert rows["slug"]["suggested_value"] == "d1-sweat-stain-remover"


def test_format_markdown_description_short_text_avoids_empty_headings() -> None:
    description = format_markdown_description("Fertig konfektionierter Überzug mit Polsterung. Mit Gegenzugkordel. Aus HR3-Überzugsstoff.")
    assert "###" not in description
    assert "- Mit Gegenzugkordel" in description


def test_format_markdown_description_converts_existing_section_labels() -> None:
    description = format_markdown_description(
        "Specific stain remover.\n\nHow to use:\n1. Wet the stain with water.\n2. Apply product.\n\nIngredients:\naqua, alcohol."
    )
    assert "### How to use" in description
    assert "1. Wet the stain with water." in description
    assert "### Ingredients" in description


def test_supplier_suggestions_prevent_later_generic_overwrite_for_same_field(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, *_args, **_kwargs):
        if "tintolav.com" in url:
            return FakeResponse(
                """
                <html><head><title>Jolly Smak - Tintolav</title></head><body>
                  <h1>Jolly Smak A01-000K</h1>
                  <div id="dacshop_product_description_main">
                    <h4>Description</h4>
                    <p>Universal pre-spotter for dry-cleaning, to be used with a brush or by spraying it.</p>
                    <ul><li>Excellent water-binding power.</li></ul>
                  </div>
                </body></html>
                """
            )
        return FakeResponse(
            """
            <html><head><title>Deutscher SEO Titel</title></head><body>
              <div class="short-description">Jolly Smak ist ein deutsches Vordetachiermittel mit langer deutscher Beschreibung.</div>
            </body></html>
            """
        )

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000K", title="Jolly Smak", status="active", source_language="en", description="Old"),
            VariantCreate(sku="A01-000K-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        product.source_url = "https://www.voxster.ch/reinigen/jolly-smak.html"
        preview = preview_product_data_enrichment(
            session,
            [product.id],
            fields=["short_description", "description"],
            sources=["final_url", "source_url"],
            overwrite_existing=True,
            target_locale="en",
        )
        suggestions = [item for row in preview["results"] for item in row["suggestions"] if item["field_name"] in {"short_description", "description"}]
        apply_result = apply_product_data_enrichment(session, suggestions, overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)
        translation = next(row for row in detail["translations"] if row["language_code"] == "en")

    assert [row["field_name"] for row in suggestions].count("description") == 1
    assert [row["field_name"] for row in suggestions].count("short_description") == 1
    assert all(row["source_domain"] == "www.tintolav.com" for row in suggestions)
    assert apply_result["applied_count"] == 2
    assert "Universal pre-spotter" in detail["description"]
    assert "deutsches Vordetachiermittel" not in detail["description"]
    assert "Universal pre-spotter" in translation["short_description"]


def test_source_locale_description_apply_syncs_visible_translation(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak - Tintolav</title></head><body>
          <h1>Jolly Smak A01-000K</h1>
          <div id="dacshop_product_description_main">
            <h4>Description</h4>
            <p>Universal pre-spotter for dry-cleaning, to be used with a brush or by spraying it.</p>
          </div>
          <div id="dacshop_product_custom_info_main">
            <table>
              <tr><td><label>How To Use</label></td><td><span class="dacshop_product_custom_value">Apply on the dirty parts before the wash.</span></td></tr>
              <tr><td><label>Ingredients</label></td><td><span class="dacshop_product_custom_value">aqua, cocamide dea.</span></td></tr>
              <tr><td><label>Function</label></td><td><span class="dacshop_product_custom_value">Pre-spotter</span></td></tr>
            </table>
          </div></div><div id="dacshop_product_files_main"></div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SYNC-EN", title="Jolly Smak", status="active", source_language="en", description="Old"),
            VariantCreate(sku="SYNC-EN-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["description"], sources=["final_url"], overwrite_existing=True, target_locale="en")
        suggestion = [item for row in preview["results"] for item in row["suggestions"] if item["field_name"] == "description"][0]
        result = apply_product_data_enrichment(session, [suggestion], overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)
        translation = next(row for row in detail["translations"] if row["language_code"] == "en")

    assert result["applied_count"] == 1
    assert "How to use:" in detail["description"]
    assert "Ingredients:" in detail["description"]
    assert "How to use:" in translation["description"]
    assert "Ingredients:" in translation["description"]


def test_product_data_enrichment_slug_candidate_updates_handle_only_on_apply(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html><head><title>Jolly Smak - Tintolav</title></head><body>
          <h1>Jolly Smak A01-000K</h1>
          <div id="dacshop_product_description_main"><h4>Description</h4>Universal pre-spotter for dry-cleaning.</div>
          <div id="dacshop_product_additionaldata"><h4>Function</h4>Pre-spotter</div>
        </body></html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A01-000K", title="Old Title", handle="old-handle", status="active", source_language="en"),
            VariantCreate(sku="A01-000K-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.tintolav.com/en/products/tintolav/product/jolly-smak-pre-spotter.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["slug"], sources=["final_url"], overwrite_existing=True, target_locale="en")
        suggestion = [item for row in preview["results"] for item in row["suggestions"] if item["field_name"] == "slug"][0]
        assert product.handle == "old-handle"
        result = apply_product_data_enrichment(session, [suggestion], overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)
        translation = next(row for row in detail["translations"] if row["language_code"] == "en")

    assert suggestion["suggested_value"] == "jolly-smak-pre-spotter"
    assert result["applied_count"] == 1
    assert detail["handle"] == "jolly-smak-pre-spotter"
    assert translation["slug"] == "jolly-smak-pre-spotter"


def test_product_data_enrichment_locale_slug_does_not_change_product_handle(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SLUG-DE", title="English Title", handle="english-title", status="active", source_language="en"),
            VariantCreate(sku="SLUG-DE-1", variant_title="Default"),
        )
        suggestion = {
            "product_id": product.id,
            "field_name": "slug",
            "target_locale": "de-CH",
            "source_language": "de",
            "suggested_value": "jolly-smak-anbuerstmittel-fuer-per",
            "source_url": "https://example.test",
            "source_domain": "example.test",
            "search_method": "manual_test",
            "confidence": 0.9,
            "status": "suggested",
        }
        result = apply_product_data_enrichment(session, [suggestion], overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)
        de = next(row for row in detail["translations"] if row["language_code"] == "de-CH")

    assert result["applied_count"] == 1
    assert detail["handle"] == "english-title"
    assert de["slug"] == "jolly-smak-anbuerstmittel-fuer-per"


def test_product_data_enrichment_blocks_german_text_for_en_locale(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="LANG-BLOCK", title="Language Block", status="active", source_language="en"),
            VariantCreate(sku="LANG-BLOCK-1", variant_title="Default"),
        )
        suggestion = {
            "product_id": product.id,
            "field_name": "short_description",
            "target_locale": "en",
            "source_language": "de",
            "suggested_value": "Jolly Smak ist ein universelles Vordetachiermittel für die professionelle Reinigung.",
            "source_url": "https://www.voxster.ch/demo",
            "source_domain": "www.voxster.ch",
            "search_method": "source_url",
            "confidence": 0.9,
            "status": "suggested",
        }
        result = apply_product_data_enrichment(session, [suggestion], overwrite_existing=True)
        session.commit()
        detail = get_product_detail(session, product.id)

    assert result["applied_count"] == 0
    assert result["skipped_count"] == 1
    assert "deutsch" in result["skipped"][0]["reason"]
    assert detail["translations"] == []


def test_product_data_enrichment_rejects_shop_claim_seo_title(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = """
        <html>
          <head><title>Ausrüster der professionellen Textilreinigung. Jolly Smak Sortiment für Profis.</title></head>
          <body><p>Jolly Smak ist ein universelles Vordetachiermittel für die professionelle Reinigung mit genug Inhalt.</p></body>
        </html>
        """

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(product_data_enrichment_service.requests, "get", lambda *_args, **_kwargs: FakeResponse())

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="SEO-BLOCK", title="Jolly Smak", status="active", source_language="de-CH"),
            VariantCreate(sku="SEO-BLOCK-1", variant_title="Default"),
        )
        product.source_url_final = "https://www.voxster.ch/jolly-smak.html"
        preview = preview_product_data_enrichment(session, [product.id], fields=["seo_title"], sources=["final_url"], overwrite_existing=True)
        suggestions = [item for row in preview["results"] for item in row["suggestions"]]

    assert suggestions == []


def test_languages_include_existing_locale_codes_from_products(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        create_product(
            session,
            ProductCreate(sku="LANG-1", title="Sprache", source_language="de-CH", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="LANG-1-A", variant_title="Default"),
        )
        languages = list_languages(session)

    codes = {row["code"] for row in languages}
    assert "de-CH" in codes
    assert "de" in codes
    assert next(row for row in languages if row["code"] == "de-CH")["enabled"] is True
