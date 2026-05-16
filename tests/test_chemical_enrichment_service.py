from pathlib import Path

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Product, ProductChemicalEnrichment
from app.models import ScrapedData
from app.services.chemical_enrichment_adapters import ChemstoreChemicalAdapter, GenericChemicalAdapter
from app.services.chemical_enrichment_service import (
    _build_chemical_enrichment_review,
    _download_pdf_to_path,
    _resolve_sdb_download_dir,
    apply_product_chemical_enrichment_suggestions,
    parse_sdb_sections,
)


def test_parse_sdb_sections_splits_numbered_sections() -> None:
    text = """
    1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens
    Produktname: Testprodukt

    2. Mögliche Gefahren
    Verursacht schwere Verätzungen.

    14. Angaben zum Transport
    UN 1791, Klasse 8, Verpackungsgruppe II
    """

    sections = parse_sdb_sections(text)

    assert sections["section_1"]["content"].startswith("Produktname")
    assert "Verätzungen" in sections["section_2"]["content"]
    assert "1791" in sections["section_14"]["content"]


def test_parse_sdb_sections_prefers_abschnitt_headings_over_subsections() -> None:
    text = """
    ABSCHNITT 7: Handhabung und Lagerung
    Hinweise zum sicheren Umgang.
    7.1. Schutzmaßnahmen zur sicheren Handhabung
    Für gute Lüftung sorgen.

    ABSCHNITT 8: Begrenzung und Überwachung der Exposition/Persönliche
    Schutzausrüstungen
    8.1. Zu überwachende Parameter
    DNEL 1.55 mg/m³
    8.2. Begrenzung und Überwachung der Exposition
    Schutzbrille tragen.

    ABSCHNITT 9: Physikalische und chemische Eigenschaften
    pH-Wert 12
    """

    sections = parse_sdb_sections(text)

    assert "DNEL 1.55" in sections["section_8"]["content"]
    assert "Schutzbrille" in sections["section_8"]["content"]
    assert sections["section_9"]["content"].startswith("pH-Wert")


def test_parse_sdb_sections_handles_abschnitt_without_space_and_ignores_chemical_names() -> None:
    text = """
SICHERHEITSDATENBLATT
ABSCHNITT1. Bezeichnung des Stoffs bzw. des Gemischs und des Unternehmens
Produktname: D1 - Sudore

ABSCHNITT2. Mögliche Gefahren
H315 - Verursacht Hautreizungen.
2-Methyl-2H-isothiazol-3-on [EG nr. 220-239-6] (3:1). Kann allergische Reaktionen hervorrufen.

ABSCHNITT3. Zusammensetzung/Angaben zu den Bestandteilen
2-(2-Butoxyethoxy)ethanol
CAS 112-34-5

ABSCHNITT4. Erste-Hilfe-Massnahmen
Augen spülen.

ABSCHNITT8. Begrenzung und Überwachung der Exposition/Persönliche Schutzausrüstungen
2-(2-Butoxyethoxy)ethanol:
MAK DFG 10 ppm

ABSCHNITT16. Sonstige Angaben
Ende.
"""

    sections = parse_sdb_sections(text)

    assert sections["section_1"]["content"].startswith("Produktname")
    assert "2-Methyl-2H-isothiazol" in sections["section_2"]["content"]
    assert sections["section_3"]["content"].startswith("2-(2-Butoxyethoxy)ethanol")
    assert sections["section_4"]["content"].startswith("Augen spülen")
    assert "MAK DFG" in sections["section_8"]["content"]
    assert sections["section_16"]["content"].startswith("Ende")


def test_chemstore_adapter_extracts_core_chemical_fields() -> None:
    adapter = ChemstoreChemicalAdapter()
    text = """
    Marke: ZF Chemstore
    CAS Nummer: 7681-52-9
    EG-Nummer: 231-668-3
    Dichte: 1.235 g/cm3
    ADR: UN1791 HYPOCHLORITLÖSUNG, 8 (UMWELTGEFÄHRDEND), II, (E)
    Signalwort
    GEFAHR
    Achtung! Gefährliche Chemikalie! Die Abgabe an private Neukunden erfolgt ausschliesslich nach Altersprüfung im Checkout!
    """
    html = "<div>GHS05.svg GHS05 GHS09.svg GHS09</div>"
    links = [
        {"url": "https://example.com/sdb.pdf", "label": "Sicherheitsdatenblatt herunterladen"},
        {"url": "https://example.com/tds.pdf", "label": "PDF Herunterladen"},
    ]

    payload = adapter.extract(
        url="https://www.chemstore.swiss/de/javelle-konzentrat-14-inhalt-5-l",
        html=html,
        text=text,
        links=links,
        generic_data=ScrapedData(product_name="Javelle Konzentrat 14%", product_title="Javelle Konzentrat 14%"),
    )

    assert payload.source_kind == "chemstore"
    assert payload.fields["cas_number"] == "7681-52-9"
    assert payload.fields["ec_number"] == "231-668-3"
    assert payload.fields["un_number"] == "1791"
    assert payload.fields["hazard_class"] == "8"
    assert payload.fields["packing_group"] == "II"
    assert payload.fields["signal_word"] == "GEFAHR"
    assert payload.fields["age_check_required"] is True
    assert payload.fields["sds_available"] is True


def test_generic_adapter_does_not_treat_years_as_un_numbers() -> None:
    adapter = GenericChemicalAdapter()
    payload = adapter.extract(
        url="https://example.com/product",
        html="<html></html>",
        text="Issued on 03/29/2021 - Rel. #4\nAbschnitt 14 Transport information\nNot classified as dangerous goods.",
        links=[],
        generic_data=ScrapedData(product_name="D1 Sweat", product_title="D1 Sweat"),
    )

    assert payload.fields["un_number"] is None


def test_generic_chemical_adapter_filters_generic_document_navigation_links() -> None:
    adapter = GenericChemicalAdapter()
    links = [
        {"url": "https://www.tintolav.com/downloads", "label": "DOWNLOAD"},
        {"url": "https://www.tintolav.com/catalogues/tintolav-catalogue.pdf", "label": "Tintolav Catalogue"},
        {"url": "https://www.tintolav.com/brochures/hygienfresh-point.pdf", "label": "HYGIENFRESH POINT Brochure"},
        {"url": "https://www.tintolav.com/files/d1-sudore-sds.pdf", "label": "D1 - Sudore Safety Data Sheet"},
        {"url": "https://www.tintolav.com/files/d1-sudore-tds.pdf", "label": "D1 Sudore Technical Data Sheet"},
    ]

    payload = adapter.extract(
        url="https://www.tintolav.com/en/products/tintolav/product/d1-sweat.html",
        html="<html></html>",
        text="D1 Sudore",
        links=links,
        generic_data=ScrapedData(
            product_name="D1 Sudore",
            product_title="D1 Sudore",
            pdf_urls=[
                "https://www.tintolav.com/catalogues/tintolav-catalogue.pdf",
                "https://www.tintolav.com/files/generic-download.pdf",
            ],
            sds_urls=["https://www.tintolav.com/files/d1-sudore-sds.pdf"],
            datasheet_urls=["https://www.tintolav.com/files/d1-sudore-tds.pdf"],
        ),
    )

    labels = [str(item.get("label") or "") for item in payload.documents]
    roles = [str(item.get("role") or "") for item in payload.documents]
    urls = [str(item.get("url") or "") for item in payload.documents]
    assert roles == ["sds", "datasheet"]
    assert any("Safety Data Sheet" in label or "SDB" in label for label in labels)
    assert any("Technical Data Sheet" in label or "Datenblatt" in label for label in labels)
    assert not any("catalog" in value.lower() or "brochure" in value.lower() or "download" in value.lower() for value in labels + urls)


def test_enrichment_review_does_not_suggest_un_from_plain_year() -> None:
    product = Product(sku="CHEM-YEAR", title="Year product")
    sdb_data = {
        "sections_json": {
            "section_14": {
                "title": "Angaben zum Transport",
                "content": "14.1 UN-Nummer oder ID-Nummer: nicht verfügbar\nIssued on 03/29/2021",
            }
        }
    }

    review = _build_chemical_enrichment_review(product=product, aggregated={"fields": {}}, documents=[], warnings=[], sdb_data=sdb_data)

    assert not [item for item in review["suggestions"] if item["field"] == "un_number"]


def test_enrichment_review_suggests_general_fields_from_internet_and_sdb() -> None:
    product = Product(sku="CHEM-GEN", title="General product")
    sdb_data = {
        "raw_text": "UFI: 0A80-10U4-F00M-UJ45",
        "sections_json": {
            "section_1": {"title": "Bezeichnung", "content": "UFI: 0A80-10U4-F00M-UJ45"},
            "section_9": {
                "title": "Physikalische und chemische Eigenschaften",
                "content": "Dichte: 1.060 g/cm3\nFarbe: strohgelb\nVOC-Gehalt: 1.12 %",
            },
        },
    }

    review = _build_chemical_enrichment_review(
        product=product,
        aggregated={"fields": {"chemical_type": {"value": "Fleckenentferner"}}},
        documents=[{"role": "sds", "url": "https://example.com/sdb.pdf", "label": "SDB"}],
        warnings=[],
        sdb_data=sdb_data,
    )
    suggestions = {item["field"]: item["suggested_value"] for item in review["suggestions"]}

    assert suggestions["chemical_type"] == "Fleckenentferner"
    assert suggestions["ufi"] == "0A80-10U4-F00M-UJ45"
    assert suggestions["density"] == "1.060 g/cm3"
    assert suggestions["color"] == "strohgelb"
    assert suggestions["voc_content_percent"] == "1.12"
    assert suggestions["sds_available"] is True
    assert suggestions["sds_url"] == "https://example.com/sdb.pdf"


def test_download_pdf_to_path_falls_back_to_curl(tmp_path, monkeypatch) -> None:
    target = tmp_path / "demo.pdf"

    def _fail_requests(*args, **kwargs):
        raise requests.exceptions.ConnectionError("dns failed")

    def _fake_curl(command, check, capture_output, text):
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_bytes(b"%PDF-1.4 fake")

        class Result:
            stdout = ""
            stderr = ""
            returncode = 0

        return Result()

    monkeypatch.setattr("app.services.chemical_enrichment_service.requests.get", _fail_requests)
    monkeypatch.setattr("app.services.chemical_enrichment_service.subprocess.run", _fake_curl)

    _download_pdf_to_path(
        "https://example.com/test.pdf",
        target,
        timeout_seconds=5,
        user_agent="TestAgent/1.0",
    )

    assert target.exists()
    assert target.read_bytes().startswith(b"%PDF-1.4")


def test_resolve_sdb_download_dir_uses_fallback_when_product_dir_not_writable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.chemical_enrichment_service.get_pim_settings", lambda: type("S", (), {"asset_storage_root": tmp_path})())

    def _fake_access(path, mode):
        path = Path(path)
        return "_imports" in path.parts

    monkeypatch.setattr("app.services.chemical_enrichment_service.os.access", _fake_access)

    resolved = _resolve_sdb_download_dir(1419)

    assert resolved == tmp_path / "_imports" / "product-1419"
    assert resolved.exists()


def test_enrichment_review_detects_environmentally_hazardous_from_section_14() -> None:
    product = Product(
        sku="CHEM-1",
        title="Natriumhypochlorit",
        chemical_safety_json={"adr_pictograms": ["ADR_8"], "environmentally_hazardous": False},
        hazard_class="8",
    )
    sdb_data = {
        "pdf_url": "https://example.test/sdb.pdf",
        "parser_status": "parsed",
        "sections_json": {
            "section_14": {
                "title": "Angaben zum Transport",
                "content": """
                14.3 Transportgefahrenklassen: 8
                14.5 Umweltgefahren: umweltgefährdend
                Marine pollutant: yes
                """,
            }
        },
    }

    review = _build_chemical_enrichment_review(product=product, aggregated={"fields": {}}, documents=[], warnings=[], sdb_data=sdb_data)

    environmental = [item for item in review["suggestions"] if item["field"] == "chem_safety.environmentally_hazardous"]
    adr = [item for item in review["suggestions"] if item["field"] == "chem_safety.adr_pictograms" and "ADR_pollution" in item["suggested_value"]]
    assert environmental
    assert environmental[0]["suggested_value"] is True
    assert environmental[0]["source_section"] == "14"
    assert adr


def test_enrichment_review_marks_ghs09_without_adr_as_review() -> None:
    product = Product(sku="CHEM-2", title="Test", ghs_pictograms="GHS09", chemical_safety_json={})
    sdb_data = {
        "sections_json": {
            "section_2": {"title": "Mögliche Gefahren", "content": "Gefahrenpiktogramme: GHS09"},
            "section_14": {"title": "Angaben zum Transport", "content": "14.5 Umweltgefahren: nicht anwendbar"},
        }
    }

    review = _build_chemical_enrichment_review(product=product, aggregated={"fields": {}}, documents=[], warnings=[], sdb_data=sdb_data)

    environmental = [item for item in review["suggestions"] if item["field"] == "chem_safety.environmentally_hazardous"]
    assert environmental
    assert environmental[0]["status"] == "needs_review"
    assert "GHS09 gefunden" in environmental[0]["evidence"]


def test_apply_enrichment_suggestions_updates_structured_environmental_flags(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product = Product(
            sku="CHEM-3",
            handle="chem-3",
            title="Test",
            chemical_safety_json={"adr_pictograms": ["ADR_8"], "environmentally_hazardous": False},
            hazard_class="8",
        )
        session.add(product)
        session.flush()
        session.add(
            ProductChemicalEnrichment(
                product_id=product.id,
                reference_url="https://example.test/sdb.pdf",
                source_kind="sds_pdf",
                status="needs_review",
                normalized_payload_json={
                    "enrichment": {
                        "suggestions": [
                            {
                                "field": "chem_safety.environmentally_hazardous",
                                "current_value": False,
                                "suggested_value": True,
                                "status": "suggested",
                            },
                            {
                                "field": "chem_safety.adr_pictograms",
                                "current_value": ["ADR_8"],
                                "suggested_value": ["ADR_8", "ADR_pollution"],
                                "status": "suggested",
                            },
                        ]
                    }
                },
            )
        )
        session.commit()

        result = apply_product_chemical_enrichment_suggestions(session, product.id, overwrite_existing=False)
        session.commit()
        session.refresh(product)

    assert set(result["applied_fields"]) == {"chem_safety.environmentally_hazardous", "chem_safety.adr_pictograms"}
    assert product.chemical_safety_json["environmentally_hazardous"] is True
    assert product.chemical_safety_json["adr_pictograms"] == ["ADR_8", "ADR_pollution"]
