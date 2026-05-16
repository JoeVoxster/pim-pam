from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Product, ProductTranslation
from scripts.import_descriptions_from_voxster_final_urls import (
    build_markdown_description,
    build_short_description,
    enhance_description_with_ai,
    extract_final_url_description,
    extract_voxster_description,
    load_final_url_products,
    process_product,
    remove_price_fragments_from_short_description,
    sanitize_markdown_description,
)


VOXSTER_HTML = """
<html>
  <head><meta name="description" content="Meta Beschreibung"></head>
  <body class="catalog-product-view">
    <div class="product-name"><h1>Drypad 3C</h1></div>
    <div class="short-description">
      <div class="std">Komplett fertigkonfektionierter Überzug mit Polsterung für Absaug-/ Blas-Saug-Bügeltisch.
      Besteht aus Überzugsstoff HR3 (100 % Polyester) + Silikonpad + Molton + Verteilnetz + Gegenzugkordel. </div>
      <div class="main-description">Preis pro Stück</div>
    </div>
    <div class="product-type-data"><p>Verfügbarkeit: Auf Lager</p></div>
  </body>
</html>
"""

GENERIC_HTML = """
<html>
  <head>
    <script type="application/ld+json">
      {"@type":"Product","name":"Beispiel Produkt","description":"Robuste Produktbeschreibung von einer Herstellerseite mit Materialangabe und Anwendungshinweis."}
    </script>
  </head>
  <body><nav>Home Warenkorb</nav></body>
</html>
"""


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)()


class _Response:
    status_code = 200
    text = VOXSTER_HTML
    headers = {"content-type": "text/html; charset=UTF-8"}

    def raise_for_status(self) -> None:
        return None


def _product() -> Product:
    return Product(
        id=1294,
        sku="DRYPAD-3",
        handle="drypad-3c",
        title="Drypad 3C",
        source_language="de-CH",
        source_url_final="https://www.voxster.ch/drypad-3c.html",
        description="Preis pro Stück",
    )


def test_extract_voxster_description_combines_std_and_main_description() -> None:
    description = extract_voxster_description(VOXSTER_HTML)

    assert description == (
        "Komplett fertigkonfektionierter Überzug mit Polsterung für Absaug-/ Blas-Saug-Bügeltisch. "
        "Besteht aus Überzugsstoff HR3 (100 % Polyester) + Silikonpad + Molton + Verteilnetz + Gegenzugkordel. "
        "Preis pro Stück"
    )
    assert build_short_description(description) == (
        "Fertigkonfektionierter gepolsterter Überzug für Absaug-/Blas-Saug-Bügeltische mit HR3-Überzug, "
        "Silikonpad, Molton und Gegenzugkordel."
    )


def test_markdown_description_formats_product_sections() -> None:
    description = extract_voxster_description(VOXSTER_HTML)
    markdown = build_markdown_description(description or "")

    assert markdown.startswith("Komplett fertigkonfektionierter Überzug")
    assert "### Eigenschaften" in markdown
    assert "- Fertig konfektioniert und direkt einsetzbar" in markdown
    assert "### Material" in markdown
    assert "- Überzugsstoff HR3: 100 % Polyester" in markdown
    assert "- Silikonpad" in markdown
    assert "### Hinweis" in markdown
    assert "### \n\n###" not in markdown


def test_extract_final_url_description_supports_non_voxster_domain() -> None:
    description = extract_final_url_description(GENERIC_HTML)

    assert description == "Robuste Produktbeschreibung von einer Herstellerseite mit Materialangabe und Anwendungshinweis."


def test_dry_run_does_not_persist_product_1294(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.import_descriptions_from_voxster_final_urls.requests.get", lambda *_args, **_kwargs: _Response())
    session = _session(tmp_path)
    product = _product()
    product.translations = [ProductTranslation(product_id=1294, language_code="de-CH", title="Drypad 3C", short_description="Preis pro Stück")]
    session.add(product)
    session.commit()

    row = process_product(session, product, overwrite=False, dry_run=True, enhance_ai=False)
    session.commit()
    saved = session.get(Product, 1294)
    translation = session.scalar(select(ProductTranslation).where(ProductTranslation.product_id == 1294))

    assert row.status == "would_update"
    assert saved.description == "Preis pro Stück"
    assert translation.short_description == "Preis pro Stück"
    assert translation.description is None


def test_load_final_url_products_skips_empty_final_urls(tmp_path) -> None:
    session = _session(tmp_path)
    with_url = _product()
    with_url.id = 1
    with_url.handle = "drypad-3c-a"
    with_url.sku = "DRYPAD-3-A"
    with_url.source_url_final = "https://example.com/product"
    without_url = _product()
    without_url.id = 2
    without_url.handle = "drypad-3c-b"
    without_url.sku = "DRYPAD-3-B"
    without_url.source_url_final = None
    session.add_all([with_url, without_url])
    session.commit()

    products = load_final_url_products(session)

    assert [product.id for product in products] == [1]


def test_invalid_final_url_is_logged_as_error(tmp_path) -> None:
    session = _session(tmp_path)
    product = _product()
    product.source_url_final = "not-a-url"
    product.translations = [ProductTranslation(product_id=1294, language_code="de-CH", title="Drypad 3C")]
    session.add(product)
    session.commit()

    row = process_product(session, product, overwrite=True, dry_run=True, enhance_ai=False)

    assert row.status == "error"
    assert "Ungültige Final-URL" in row.error


def test_short_description_removes_trailing_price_fragment() -> None:
    cleaned = remove_price_fragments_from_short_description(
        "D4 Protein Fleckenentferner entfernt Blut-, Eier-, Milch- und Eisflecken; Preis pro Flasche."
    )

    assert cleaned == "D4 Protein Fleckenentferner entfernt Blut-, Eier-, Milch- und Eisflecken."


def test_markdown_description_removes_internal_metadata_bullets() -> None:
    markdown = sanitize_markdown_description(
        "Text.\n\n### Material / Technische Angaben\n\n- Produkt-ID: 1410\n- SKU: A45-016\n- Produktlink: https://example.test\n- Parfümiert"
    )

    assert "Produkt-ID" not in markdown
    assert "SKU:" not in markdown
    assert "Produktlink" not in markdown
    assert "- Parfümiert" in markdown


def test_without_overwrite_keeps_existing_meaningful_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.import_descriptions_from_voxster_final_urls.requests.get", lambda *_args, **_kwargs: _Response())
    session = _session(tmp_path)
    product = _product()
    product.description = "Bestehende lange Beschreibung bleibt erhalten."
    product.translations = [
        ProductTranslation(
            product_id=1294,
            language_code="de-CH",
            title="Drypad 3C",
            short_description="Bestehende Kurzbeschreibung bleibt.",
            description="Bestehende lange Beschreibung bleibt erhalten.",
        )
    ]
    session.add(product)
    session.commit()

    row = process_product(session, product, overwrite=False, dry_run=False, enhance_ai=False)
    session.commit()
    translation = session.scalar(select(ProductTranslation).where(ProductTranslation.product_id == 1294))

    assert row.status == "skipped_existing"
    assert product.description == "Bestehende lange Beschreibung bleibt erhalten."
    assert translation.description == "Bestehende lange Beschreibung bleibt erhalten."


def test_with_overwrite_replaces_existing_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("scripts.import_descriptions_from_voxster_final_urls.requests.get", lambda *_args, **_kwargs: _Response())
    session = _session(tmp_path)
    product = _product()
    product.description = "Alt"
    product.translations = [ProductTranslation(product_id=1294, language_code="de-CH", title="Drypad 3C", short_description="Alt", description="Alt")]
    session.add(product)
    session.commit()

    row = process_product(session, product, overwrite=True, dry_run=False, enhance_ai=False)
    session.commit()
    translation = session.scalar(select(ProductTranslation).where(ProductTranslation.product_id == 1294))

    assert row.status == "updated"
    assert "### Eigenschaften" in product.description
    assert "- Fertig konfektioniert und direkt einsetzbar" in product.description
    assert "Preis pro Stück" in translation.description
    assert "Preis pro Stück" not in translation.short_description


def test_ai_enrichment_returns_long_description_and_short_summary(monkeypatch) -> None:
    long_description = (
        "Drypad 3C ist ein fertig konfektionierter Buegeltischueberzug fuer Absaug-/Blas-Saug-Buegeltische.\n\n"
        "### Eigenschaften\n\n"
        "- Fertig konfektioniert und direkt einsetzbar\n"
        "- Geeignet fuer Absaug-/Blas-Saug-Buegeltische\n"
        "- Mit Gegenzugkordel ausgestattet\n\n"
        "### Material\n\n"
        "- Ueberzugsstoff HR3: 100 % Polyester\n"
        "- Silikonpad\n"
        "- Molton\n"
        "- Verteilnetz\n"
        "- Gegenzugkordel\n\n"
        "### Hinweis\n\n"
        "- Preis pro Stueck"
    )

    def fake_call(_product, _raw_description, *, model):
        return {
            "description": long_description,
            "short_description": "Fertig konfektionierter Buegeltischueberzug mit Polsterung fuer Absaug- und Blas-Saug-Buegeltische mit HR3-Bezug und Gegenzugkordel.",
        }

    monkeypatch.setattr("scripts.import_descriptions_from_voxster_final_urls.call_openai_description_json", fake_call)

    description, short_description = enhance_description_with_ai(_product(), "Rohtext", model="test-model")

    assert "### Eigenschaften" in description
    assert "- Fertig konfektioniert und direkt einsetzbar" in description
    assert "<" not in description
    assert 120 <= len(short_description) <= 180
    assert "\n" not in short_description
    assert not short_description.lstrip().startswith("- ")
