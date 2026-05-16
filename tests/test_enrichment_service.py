from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import Product, ProductVariant
from app.models import ProductVariant as ScrapedVariant, ScrapedData
from app.schemas.pim import EnrichmentJobOptions
from app.services.enrichment_service import (
    MatchTarget,
    ResolverCandidate,
    _apply_scraped_data,
    _apply_scraped_data_detailed,
    _find_tintolav_candidate_for_target,
    _forced_matches_for_url,
    _match_scraped_to_product,
    _resolve_tintolav_targets,
    _selected_targets,
    _target_urls,
    _tintolav_sku_key,
    run_selected_website_enrichment,
)
from app.suppliers.base import SupplierAssetCandidate, SupplierExtractionResult


class DummyDownloader:
    def download_images(self, **kwargs):
        return []

    def download_pdfs(self, **kwargs):
        return []


def test_match_scraped_to_product_matches_by_variant_sku(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product = Product(sku="P-1", handle="demo-product", title="Demo Product", status="active")
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, sku="SKU-100", variant_title="Default")
        session.add(variant)
        session.commit()

        products = session.query(Product).all()
        match = _match_scraped_to_product(products, ScrapedData(supplier_sku="SKU-100", product_name="Ignored"))

    assert match is not None
    assert match.product.sku == "P-1"
    assert match.variant is not None
    assert match.variant.sku == "SKU-100"


def test_apply_scraped_data_updates_empty_fields_and_packaging(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        product = Product(sku="P-1", handle="demo-product", title="Demo Product", status="active")
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, sku="SKU-100", variant_title="Default")
        session.add(variant)
        session.flush()

        updates = _apply_scraped_data(
            session=session,
            target=MatchTarget(product=product, variant=variant),
            scraped=ScrapedData(
                supplier_sku="SKU-100",
                description="Updated description",
                source_url_final="https://tintolav.com/demo-product",
                specifications=["10 kg", "liquid"],
                technical_features=["ph-neutral"],
                variants=[ScrapedVariant(supplier_sku="SKU-100", packaging="2 x 5 kg")],
            ),
            options=EnrichmentJobOptions(seed_url="https://tintolav.com/", supplier_name="Tintolav"),
            downloader=DummyDownloader(),
            storage_root=Path(tmp_path / "assets"),
        )

        session.commit()

    assert updates == 6
    assert product.description == "Updated description"
    assert product.source_url == "https://tintolav.com/"
    assert product.source_url_final == "https://tintolav.com/demo-product"
    assert product.specifications_text == "10 kg | liquid"
    assert product.technical_features_text == "ph-neutral"
    assert variant.packaging == "2 x 5 kg"


def test_apply_scraped_data_reports_supplier_candidates_separately(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        product = Product(sku="A15-030", handle="d1", title="D1", status="active", source_language="de-CH")
        session.add(product)
        session.flush()
        scraped = SupplierExtractionResult(
            supplier_key="tintolav",
            supplier_name="Tintolav",
            source_url="https://www.tintolav.com/en/products/tintolav/product/d1-sweat.html",
            source_domain="tintolav.com",
            detected_language="en",
            product_name="D1 Sweat",
            short_description="Stain remover.",
            description="Stain remover description.",
            specifications="Packaging: 6x500ml",
            how_to_use="Apply before washing.",
            ingredients="aqua",
            function="Spotter",
            packaging="6x500ml",
            pdfs=[SupplierAssetCandidate(asset_url="https://example.com/d1-sds.pdf", asset_type="sds", language="en")],
            images=[SupplierAssetCandidate(asset_url="https://example.com/d1.png", asset_type="image", language="en")],
        ).to_scraped_data()

        stats = _apply_scraped_data_detailed(
            session=session,
            target=MatchTarget(product=product, variant=None),
            scraped=scraped,
            options=EnrichmentJobOptions(seed_url="", supplier_name="Tintolav"),
            downloader=DummyDownloader(),
            storage_root=Path(tmp_path / "assets"),
        )
        session.commit()

    assert stats.direct_updates == 1
    assert stats.text_candidates > 0
    assert stats.asset_candidates == 2
    assert product.source_url_final == "https://www.tintolav.com/en/products/tintolav/product/d1-sweat.html"
    assert product.description is None
    assert product.specifications_text is None


def test_selected_targets_and_urls_use_marked_records(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product = Product(
            sku="P-1",
            handle="demo-product",
            title="Demo Product",
            status="active",
            source_url="https://tintolav.com/start",
            source_url_final="https://tintolav.com/product/demo-product",
        )
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, sku="SKU-100", variant_title="Default")
        session.add(variant)
        session.commit()

        products = session.query(Product).all()
        targets = _selected_targets(products, product_ids=[product.id], variant_ids=[variant.id])
        urls = _target_urls(targets)

    assert len(targets) == 2
    assert urls == [
        "https://tintolav.com/product/demo-product",
        "https://tintolav.com/start",
    ]


def test_tintolav_sku_key_strips_variant_suffix() -> None:
    assert _tintolav_sku_key("A01-000K") == "A01-000"
    assert _tintolav_sku_key("A32-000H2") == "A32-000"


def test_find_tintolav_candidate_matches_by_sku_prefix(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        product = Product(sku="A01-000K", handle="jolly-smak-10-kg-pre-spotter", title="Jolly Smak 10 kg. Pre-Spotter", status="active")
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, sku="A01-000K", variant_title="Jolly Smak 10 kg")
        session.add(variant)
        session.flush()

        target = MatchTarget(product=product, variant=variant)
        candidate = _find_tintolav_candidate_for_target(
            target,
            [
                ResolverCandidate(
                    url="https://www.tintolav.com/en/products/tintolav/product/jolly-smak.html",
                    title="Jolly Smak",
                    subtitle="Pre-spotter",
                    sku="A01-000",
                )
            ],
        )

    assert candidate is not None
    assert candidate.url.endswith("/jolly-smak.html")


def test_run_selected_website_enrichment_normalizes_urls_and_names(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)

    captured: dict[str, object] = {}

    def fake_run_job(session, job, options, products, selected_targets, direct_urls):
        captured["seed_url"] = options.seed_url
        captured["supplier_name"] = options.supplier_name
        captured["resolver_listing_url"] = options.resolver_listing_url
        return {"ok": True}

    monkeypatch.setattr("app.services.enrichment_service._run_enrichment_job", fake_run_job)

    with Session(engine, expire_on_commit=False) as session:
        product = Product(sku="A01-000K", handle="jolly-smak", title="Jolly Smak", status="active")
        session.add(product)
        session.flush()
        variant = ProductVariant(product_id=product.id, sku="A01-000K", variant_title="Default")
        session.add(variant)
        session.flush()

        result = run_selected_website_enrichment(
            session=session,
            options=EnrichmentJobOptions(
                seed_url=" https://tintolav.com/ ",
                supplier_name=" Tintolav ",
                resolver_mode="tintolav_catalog",
                resolver_listing_url=" https://www.tintolav.com/en/products/tintolav/product/listing.html ",
            ),
            product_ids=[product.id],
        )

    assert result == {"ok": True}
    assert captured["seed_url"] == "https://tintolav.com/"
    assert captured["supplier_name"] == "Tintolav"
    assert captured["resolver_listing_url"] == "https://www.tintolav.com/en/products/tintolav/product/listing.html"


def test_resolve_tintolav_targets_allows_multiple_targets_for_same_url(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        product_one = Product(sku="B01-045AR", handle="orange", title="Marking Tape Rolls 24 mm. Orange 6 pz.", status="active")
        product_two = Product(sku="B01-045BL", handle="blue", title="Marking Tape Rolls 24 mm. Blue 6 pz.", status="active")
        session.add_all([product_one, product_two])
        session.flush()
        variant_one = ProductVariant(product_id=product_one.id, sku="B01-045AR", variant_title="Orange")
        variant_two = ProductVariant(product_id=product_two.id, sku="B01-045BL", variant_title="Blue")
        session.add_all([variant_one, variant_two])
        session.flush()

        targets = [
            MatchTarget(product=product_one, variant=variant_one),
            MatchTarget(product=product_two, variant=variant_two),
        ]

        monkeypatch.setattr(
            "app.services.enrichment_service._fetch_tintolav_catalog_candidates",
            lambda browser, listing_url: [
                ResolverCandidate(
                    url="https://www.tintolav.com/en/products/tintolav/product/marking-tape-rolls-24mm..html",
                    title="Marking Tape Rolls 24 mm. 6 rolls",
                    subtitle=None,
                    sku="B01-045XX",
                )
            ],
        )

        resolved = _resolve_tintolav_targets(browser=None, selected_targets=targets, listing_url="https://example.com/listing")  # type: ignore[arg-type]
        forced = _forced_matches_for_url(resolved, "https://www.tintolav.com/en/products/tintolav/product/marking-tape-rolls-24mm..html")

    assert len(resolved) == 1
    assert len(forced) == 2
