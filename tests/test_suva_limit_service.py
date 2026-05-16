from __future__ import annotations

from io import BytesIO

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import ChemicalDocument, SuvaLimitSource
from app.schemas.pim import ProductCreate, VariantCreate
from app.services.pim_service import create_product
from app.services.sds_swiss_review_service import review_sds_document
from app.services.suva_limit_service import enrich_sdb_sections_with_suva_suggestions, extract_sds_ingredients, generate_section_8_1_ch_block, import_suva_xlsx, run_product_suva_check


SDS_TEXT = """
1. Bezeichnung
Tox Info Suisse 145

3. Zusammensetzung / Angaben zu Bestandteilen
2-(2-butoxyethoxy)ethanol >= 5 < 15% CAS 112-34-5 EG 203-961-6 Eye Irrit. 2 H319
ethanol < 0,1% CAS 64-17-5 H225
Subtilisin < 0,1% CAS 9014-01-1 Resp. Sens. 1 H334
Kokosnussdiethanolamid >= 5 < 15% CAS 68603-42-9 H315 H318
Unbekannter Stoff >= 1 < 5% CAS 7732-18-5 H315

8. Begrenzung und Überwachung der Exposition / persönliche Schutzausrüstung
ACGIH TWA 10 ppm. UK WEL 15 ppm.

14. Angaben zum Transport
Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA.

15. Rechtsvorschriften
Schweizer Rechtsvorschriften
- Chemikalienverordnung (ChemV, SR 813.11)
- Chemikalien-Risikoreduktions-Verordnung (ChemRRV, SR 814.81)
- SUVA-Grenzwerte, soweit relevant

16. Sonstige Angaben
Verordnung (EG) Nr. 1907/2006 REACH, Anhang II in der Fassung der Verordnung (EU) 2020/878
Verordnung (EG) Nr. 1272/2008 CLP
"""


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def _xlsx_bytes() -> bytes:
    frame = pd.DataFrame(
        [
            {
                "Stoffname": "2-(2-Butoxyethoxy)ethanol",
                "CAS-Nummer": "112-34-5",
                "EG-Nr.": "203-961-6",
                "Synonyme": "Butyldiglykol",
                "MAK ppm": "10",
                "MAK mg/m3": "67",
                "KZGW ppm": "15",
                "KZGW mg/m3": "101",
                "BAT-Wert": "",
                "BAT-Matrix": "",
                "Notationen": "H",
                "Bemerkungen": "Testwert",
            },
            {
                "Stoffname": "Ethanol",
                "CAS-Nummer": "64-17-5",
                "MAK ppm": "",
                "MAK mg/m3": "",
            },
            {
                "Stoffname": "Subtilisin",
                "CAS-Nummer": "9014-01-1",
                "KZGW mg/m3": "0.00006",
                "BAT-Wert": "beim Hersteller nicht bekannt",
            },
        ]
    )
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Grenzwerte")
    return buffer.getvalue()


def _document(session) -> ChemicalDocument:
    product, _variant = create_product(
        session,
        ProductCreate(sku="A15-030", title="D1 Schweiss Fleckenentferner", brand_name="Tintolav", status="active", is_chemical=True),
        VariantCreate(sku="A15-030", variant_title="Default"),
    )
    document = ChemicalDocument(
        product_id=product.id,
        document_type="sds",
        locale="de-CH",
        language_code="de-CH",
        region_code="CH",
        title="SDB D1 Schweiss Fleckenentferner",
        generated_text=SDS_TEXT,
        status="draft",
        source="generated",
        is_current=True,
    )
    session.add(document)
    session.flush()
    return document


def test_suva_xlsx_import_is_versioned_and_deduplicated(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    payload = _xlsx_bytes()
    with SessionLocal() as session:
        first = import_suva_xlsx(session, payload, "suva-test.xlsx", imported_by="tester")
        duplicate = import_suva_xlsx(session, payload, "suva-test.xlsx", imported_by="tester")
        source_count = session.query(SuvaLimitSource).count()

    assert first["status"] == "imported"
    assert first["entries_imported"] == 3
    assert duplicate["status"] == "duplicate"
    assert source_count == 1


def test_suva_xlsx_import_promotes_embedded_header_rows(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    frame = pd.DataFrame(
        [
            {
                "Identität": "Stoff",
                "Unnamed: 1": "CAS-Nr.",
                "Unnamed: 2": "Synonyme",
                "MAK Suva": "MAK-Wert 1",
                "Unnamed: 4": "MAK-Einheit 1",
                "Unnamed: 5": "MAK-Wert 2",
                "Unnamed: 6": "MAK-Einheit 2",
                "KZGW Suva": "KZGW-Wert 1",
                "Unnamed: 8": "KZGW-Einheit 1",
                "Unnamed: 9": "KZGW-Wert 2",
                "Unnamed: 10": "KZGW-Einheit 2",
                "Notationen Suva": "H",
            },
            {
                "Identität": "Aceton",
                "Unnamed: 1": "67-64-1",
                "MAK Suva": "500",
                "Unnamed: 4": "ppm",
                "Unnamed: 5": "1200",
                "Unnamed: 6": "mg/m3",
                "KZGW Suva": "1000",
                "Unnamed: 8": "ppm",
                "Unnamed: 9": "2400",
                "Unnamed: 10": "mg/m3",
                "Notationen Suva": "H",
            },
        ]
    )
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="MAK-Werte")
    with SessionLocal() as session:
        result = import_suva_xlsx(session, buffer.getvalue(), "suva-real-shape.xlsx")
        source_id = result["source_id"]
        enriched, report = enrich_sdb_sections_with_suva_suggestions(
            session,
            {
                "section_3": {"content": "Aceton >= 1 < 5% CAS 67-64-1 H319"},
                "section_8": {"content": "8.1 Zu überwachende Parameter\nKeine Angaben."},
            },
        )

    assert result["entries_imported"] == 1
    assert source_id
    assert report["cas_matches"][0]["substance_name"] == "Aceton"
    assert report["cas_matches"][0]["mak_ppm"] == "500"
    assert report["cas_matches"][0]["mak_mg_m3"] == "1200"
    assert report["cas_matches"][0]["kzgw_ppm"] == "1000"
    assert report["cas_matches"][0]["kzgw_mg_m3"] == "2400"
    assert "MAK-Wert: 500 ppm / 1200 mg/m3" in enriched["section_8"]["content"]


def test_suva_check_exact_cas_no_match_and_h334_warning(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        document = _document(session)
        result = run_product_suva_check(session, document.product_id, sds_id=document.id)
        by_cas = {item["cas_number"]: item for item in result["items"]}

    assert result["overall_status"] == "WARNING"
    assert by_cas["112-34-5"]["match_status"] == "exact_cas_match"
    assert by_cas["112-34-5"]["mak_ppm"] == "10"
    assert by_cas["9014-01-1"]["severity"] == "WARNING"
    assert "zusätzlicher Arbeitsschutz-Review" in by_cas["9014-01-1"]["review_note"]
    assert by_cas["7732-18-5"]["match_status"] == "no_match"
    assert by_cas["7732-18-5"]["severity"] == "WARNING"


def test_suva_section_8_1_block_uses_documented_values_only(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        document = _document(session)
        result = run_product_suva_check(session, document.product_id, sds_id=document.id)
        block = generate_section_8_1_ch_block(session, result["id"])

    assert "Schweizer Arbeitsplatzgrenzwerte / SUVA MAK/BAT" in block
    assert "MAK 10 ppm" in block
    assert "KZGW 101 mg/m3" in block
    assert "Unbekannter Stoff" in block
    assert "Keine Werte erfunden" not in block


def test_suva_check_missing_blocks_ch_sds_review(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert keys["suva_import_missing"]["severity"] == "critical"


def test_suva_check_documented_removes_missing_import_block_but_warns_for_items(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        document = _document(session)
        run_product_suva_check(session, document.product_id, sds_id=document.id)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert "suva_import_missing" not in keys
    assert "suva_check_missing" not in keys
    assert keys["suva_check_warning"]["severity"] == "warning"


def test_suva_suggestions_add_safe_cas_matches_to_section_8(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    sections = {
        "section_3": {
            "title": "Zusammensetzung",
            "content": "2-(2-butoxyethoxy)ethanol >= 5 < 15% CAS 112-34-5 H319\nSubtilisin < 0,1% CAS 9014-01-1 H334",
        },
        "section_8": {
            "title": "Begrenzung und Überwachung der Exposition",
            "content": "8.1 Zu überwachende Parameter\nKeine zusätzlichen Angaben.\n\n8.2 Begrenzung und Überwachung der Exposition\nHandschutz: Handschuhe tragen.",
        },
    }
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        enriched, report = enrich_sdb_sections_with_suva_suggestions(session, sections)

    section_8 = enriched["section_8"]
    assert len(report["cas_matches"]) == 2
    assert "Arbeitsplatzgrenzwerte Schweiz / SUVA" in section_8["content"]
    assert "112-34-5" in section_8["content"]
    assert "MAK-Wert: 10 ppm / 67 mg/m3" in section_8["content"]
    assert "KZGW" in section_8["content"]
    assert "arbeitsmedizinische Überwachung" in section_8["content"]
    assert section_8["fields"]["suva_matches"][0]["matched_by"] == "cas"
    assert enriched["section_3"]["fields"]["extracted_substances"][0]["source_section"] == 3


def test_suva_suggestions_do_not_auto_apply_name_or_synonym_matches(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    sections = {
        "section_3": {"title": "Zusammensetzung", "content": "Butyldiglykol >= 5 < 15% H319"},
        "section_8": {"title": "Begrenzung", "content": "8.1 Zu überwachende Parameter\nKeine Angaben."},
    }
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        enriched, report = enrich_sdb_sections_with_suva_suggestions(session, sections)

    assert report["cas_matches"] == []
    assert report["review_required_items"][0]["status"] == "needs_human_review"
    assert report["review_required_items"][0]["confidence"] == "review_required"
    assert "MAK-Wert: 10 ppm" not in enriched["section_8"]["content"]


def test_suva_suggestions_avoid_duplicate_cas_entries(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    sections = {
        "section_3": {"title": "Zusammensetzung", "content": "2-(2-butoxyethoxy)ethanol CAS 112-34-5 H319"},
        "section_8": {
            "title": "Begrenzung",
            "content": "8.1 SUVA-Vorschlag: Arbeitsplatzgrenzwerte Schweiz / SUVA\nCAS-Nr.: 112-34-5\nMAK-Wert: 10 ppm",
        },
    }
    with SessionLocal() as session:
        import_suva_xlsx(session, _xlsx_bytes(), "suva-test.xlsx")
        enriched, _report = enrich_sdb_sections_with_suva_suggestions(session, sections)

    assert enriched["section_8"]["content"].count("112-34-5") == 1


def test_suva_suggestions_add_section_8_2_aerosol_review_for_h334(tmp_path) -> None:
    sections = {
        "section_3": {"title": "Zusammensetzung", "content": "Subtilisin < 0,1% CAS 9014-01-1 Resp. Sens. 1 H334; STOT SE 3 H335"},
        "section_8": {"title": "Begrenzung", "content": "8.2 Begrenzung und Überwachung der Exposition\nAtemschutz: Nicht erforderlich."},
    }
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        enriched, report = enrich_sdb_sections_with_suva_suggestions(session, sections)

    suggestions = enriched["section_8"]["fields"]["section_8_2_suggestions"]
    assert any("Aerosolbildung vermeiden" in value for value in suggestions)
    assert any("Subtilisin" in value for value in suggestions)


def test_import_suggestions_include_ch_review_metadata_for_ufi_ghs_transport_and_law(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    sections = {
        "section_1": {
            "title": "Bezeichnung",
            "content": "1.1 Produktidentifikator\nProduktname: Test\n1.2 Relevante identifizierte Verwendungen\nNicht verfügbar",
        },
        "section_2": {
            "title": "Mögliche Gefahren",
            "content": "Piktogramme: GHS05, GHS07\nSkin Irrit. 2, Eye Dam. 1\nH315\nH318\nUFI: 0A80-10U4-F00M-UJ45",
        },
        "section_14": {
            "title": "Transport",
            "content": "14.1 UN-Nummer oder ID-Nummer: UN 2021\n14.2 Versandbezeichnung: nicht verfügbar\n14.3 Klasse: nicht verfügbar\n14.4 Verpackungsgruppe: nicht verfügbar",
        },
        "section_15": {"title": "Rechtsvorschriften", "content": "Schweizer Vorschriften beachten."},
        "section_16": {"title": "Sonstige Angaben", "content": "Richtlinie 1999/45/EG\nVerordnung 2010/453/EG"},
    }
    with SessionLocal() as session:
        enriched, _report = enrich_sdb_sections_with_suva_suggestions(session, sections)

    section_1_suggestions = enriched["section_1"]["fields"]["ch_review_suggestions"]
    assert any(row["target"] == "section_1_1" and "UFI" in row["message"] for row in section_1_suggestions)
    assert any(row["target"] == "section_1_2" for row in section_1_suggestions)
    assert enriched["section_2"]["fields"]["pictogram_review"]["piktogram_review_required"] is True
    assert enriched["section_14"]["fields"]["ch_review_suggestions"][0]["severity"] == "critical"
    assert enriched["section_15"]["fields"]["ch_legal_checklist"]["ChemV"] == "needs_review"
    assert enriched["section_16"]["fields"]["modern_reference_suggestions"][0]["severity"] == "critical"


def test_extract_sds_ingredients_ignores_index_numbers_when_extracting_cas() -> None:
    ingredients = extract_sds_ingredients(
        """
        3. Zusammensetzung / Angaben zu Bestandteilen
        2-(2-Butoxyethoxy)ethanol
        >= 5 < 15%
        603-096-00-8
        112-34-5
        203-961-6
        Eye Irrit. 2, H319
        Ethanol
        < 0,1%
        603-002-00-5
        64-17-5
        200-578-6
        Flam. Liq. 2, H225
        """
    )
    by_cas = {item.cas_number: item for item in ingredients if item.cas_number}

    assert "112-34-5" in by_cas
    assert "64-17-5" in by_cas
    assert "603-096-00-8" not in by_cas
    assert "603-002-00-5" not in by_cas
