from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.db.base import Base
from app.db.models import ChemicalDocument
from app.schemas.pim import ProductCreate, VariantCreate
from app.services.pim_service import create_product
from app.services.sds_swiss_review_service import apply_safe_auto_fixes, assert_final_pdf_allowed, release_document_as_final, review_sds_document


A15_030_REVIEW_TEXT = """
Review-Entwurf CH
SAFETY DATA SHEET
D1 - Sudore
Issued on 03/29/2021 - Rel. # 4 on 03/29/2021

1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens
1.1 Produktidentifikator
Produktname: D1 - Sudore
Artikel-/Trade-Code: A15-030
UFI: 0A80-10U4-F00M-UJ45
1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird
Nicht verfügbar
1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt
VOXSTER GmbH
Obere Ifangstrasse 10
8215 Hallau
CH
1.4 Notrufnummer
Schweiz:
Tox Info Suisse, Zürich
Notfallnummer: 145
Aus dem Ausland: +41 44 251 51 51

2. Mögliche Gefahren
Gefahrenpiktogramme: GHS07
2.1 Einstufung des Stoffs oder Gemischs
Skin Irrit. 2, Eye Dam. 1
2.2 Kennzeichnungselemente
Piktogramme: GHS05, GHS07
H315 Verursacht Hautreizungen.
H318 Verursacht schwere Augenschäden.
EUH208 Enthält Reaktionsmasse aus C(M)IT/MIT. Kann allergische Reaktionen hervorrufen.
P310 Sofort GIFTINFORMATIONSZENTRUM/Arzt anrufen.

3. Zusammensetzung / Angaben zu Bestandteilen
Alcohols, C12-14, ethoxylated >= 1 < 5%, Eye Dam. 1 H318, Aquatic Acute 1 H400
Subtilisin < 0,1%, Resp. Sens. 1 H334

4. Erste-Hilfe-Massnahmen
Verschlucken:
Nicht gefährlich.

8. Begrenzung und Überwachung der Exposition / persönliche Schutzausrüstung
Schweiz: Subtilisin STEL 0,00006 mg/m3
Für die Schweiz sind keine zusätzlichen MAK-/BAT-Grenzwerte aus diesem Datensatz hinterlegt.
Schutzhandschuhe tragen.
Tox Info Suisse, Zürich
Tox Info Suisse, Zürich

9. Physikalische und chemische Eigenschaften
Aggregatzustand/Form: nicht verfügbar
Farbe: nicht verfügbar
Geruch: nicht verfügbar
pH-Wert: nicht verfügbar
Dichte: nicht verfügbar
Löslichkeit: nicht verfügbar
Viskosität: nicht verfügbar
Flammpunkt: nicht verfügbar

13. Hinweise zur Entsorgung
Abfallcode: nicht verfügbar

14. Angaben zum Transport
14.1 UN-Nummer oder ID-Nummer: UN 2021
14.2 Ordnungsgemässe UN-Versandbezeichnung: nicht verfügbar
14.3 Transportgefahrenklassen: nicht verfügbar
14.4 Verpackungsgruppe: nicht verfügbar
14.5 Umweltgefahren: nicht verfügbar

15. Rechtsvorschriften
Schweizer Rechtsvorschriften sind zu beachten.

12. Umweltbezogene Angaben
Keine schädlichen Wirkungen.

16. Sonstige Angaben
Directive 1999/45/EC
Directive 2001/60/EC
Regulation 2010/453/EC
Gueltig bis zur naechsten Pruefung. Haende und Augenschaeden pruefen.
"""


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def _document(session, *, text: str = A15_030_REVIEW_TEXT, status: str = "draft", sku: str = "A15-030") -> ChemicalDocument:
    product, _variant = create_product(
        session,
        ProductCreate(sku=sku, title="D1 Schweiss Fleckenentferner", brand_name="Tintolav", status="active", is_chemical=True),
        VariantCreate(sku=sku, variant_title="Default"),
    )
    document = ChemicalDocument(
        product_id=product.id,
        document_type="sds",
        locale="de-CH",
        language_code="de-CH",
        region_code="CH",
        title="SDB D1 Schweiss Fleckenentferner",
        generated_text=text,
        status=status,
        source="generated",
        is_current=True,
    )
    session.add(document)
    session.flush()
    return document


def test_ch_sds_review_detects_a15_030_issues_reproducibly(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session)
        result = review_sds_document(session, document.id)
        second_result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert result["swiss_review_status"] == "critical_blocked"
    assert keys["source_outdated"]["severity"] == "warning"
    assert keys["identified_uses_missing"]["requires_human_review"] is True
    assert keys["rpc_status_unknown"]["current_text"] == "0A80-10U4-F00M-UJ45"
    assert "tox_info_suisse_missing" not in keys
    assert keys["transport_incomplete"]["severity"] == "critical"
    assert keys["waste_code_ch_needed"]["severity"] == "warning"
    assert keys["swiss_law_generic"]["severity"] == "critical"
    assert keys["outdated_legal_references"]["severity"] == "critical"
    assert keys["section_9_many_missing_values"]["severity"] == "critical"
    assert keys["section_9_missing_physical_state"]["severity"] == "critical"
    assert keys["swiss_mak_bat_contradiction"]["severity"] == "critical"
    assert keys["ascii_umlaut_typography"]["auto_fixable"] is True
    assert keys["pictogram_mismatch"]["requires_human_review"] is True
    assert keys["pictogram_mismatch"]["severity"] == "critical"
    assert keys["ghs07_priority_review"]["severity"] == "critical"
    assert keys["first_aid_ingestion_plausibility"]["severity"] == "critical"
    assert keys["euh208_substance_missing"]["severity"] == "critical"
    assert keys["environmental_classification_missing"]["severity"] == "critical"
    assert sorted(issue["issue_key"] for issue in result["issues"]) == sorted(issue["issue_key"] for issue in second_result["issues"])
    assert result["blocking_errors"] is True
    assert result["recommendation"] == "Nicht freigeben"
    assert result["json_report"]["overall_status"] == "BLOCKER"
    assert "SDB-Review CH" in result["markdown_report"]
    assert any(row["key"] == "supplier_ch_present" for row in result["ok_checks"])
    assert any(row["key"] == "tox_info_suisse_present" for row in result["ok_checks"])


def test_ch_sds_review_accepts_compact_abschnitt_headers_with_tox_info(tmp_path) -> None:
    text = A15_030_REVIEW_TEXT.replace(
        "1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens",
        "ABSCHNITT1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens",
    ).replace(
        "2. Mögliche Gefahren",
        "ABSCHNITT2. Mögliche Gefahren",
    )
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"] for issue in result["issues"]}

    assert "tox_info_suisse_missing" not in keys
    assert any(row["key"] == "tox_info_suisse_present" for row in result["ok_checks"])


def test_ch_sds_review_blocks_final_but_allows_review_pdf_state(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        draft = _document(session, status="draft")
        final = _document(session, status="approved", sku="A15-030-FINAL")
        draft_result = assert_final_pdf_allowed(session, draft)
        with pytest.raises(ValueError):
            assert_final_pdf_allowed(session, final)
        with pytest.raises(ValueError):
            release_document_as_final(session, draft.id)

    assert draft_result["critical_count"] >= 1


def test_ch_sds_review_auto_fix_keeps_swiss_ss(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session)
        review_sds_document(session, document.id)
        apply_safe_auto_fixes(session, document.id)
        session.refresh(document)
        text = document.generated_text or ""

    assert "Gültig" in text
    assert "Hände" in text
    assert "Augenschäden" in text
    assert "prüfen" in text
    assert "ß" not in text
    assert text.count("Tox Info Suisse, Zürich") == 1


def test_ch_sds_review_accepts_clear_non_dangerous_transport_statement(tmp_path) -> None:
    text = A15_030_REVIEW_TEXT.replace(
        """14. Angaben zum Transport
14.1 UN-Nummer oder ID-Nummer: UN 2021
14.2 Ordnungsgemässe UN-Versandbezeichnung: nicht verfügbar
14.3 Transportgefahrenklassen: nicht verfügbar
14.4 Verpackungsgruppe: nicht verfügbar
14.5 Umweltgefahren: nicht verfügbar""",
        """14. Angaben zum Transport
Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA.""",
    )
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"] for issue in result["issues"]}

    assert "transport_incomplete" not in keys
    assert "transport_status_unclear" not in keys


def test_ch_sds_review_accepts_complete_transport_section(tmp_path) -> None:
    text = A15_030_REVIEW_TEXT.replace(
        """14. Angaben zum Transport
14.1 UN-Nummer oder ID-Nummer: UN 2021
14.2 Ordnungsgemässe UN-Versandbezeichnung: nicht verfügbar
14.3 Transportgefahrenklassen: nicht verfügbar
14.4 Verpackungsgruppe: nicht verfügbar
14.5 Umweltgefahren: nicht verfügbar""",
        """14. Angaben zum Transport
14.1 UN-Nummer oder ID-Nummer: UN 2021
14.2 Ordnungsgemässe UN-Versandbezeichnung: Geprüfte Versandbezeichnung
14.3 Transportgefahrenklassen: 8
14.4 Verpackungsgruppe: II
14.5 Umweltgefahren: Nein
14.6 Besondere Vorsichtsmassnahmen für den Verwender: Manuelle Prüfung erforderlich.""",
    )
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"] for issue in result["issues"]}

    assert "transport_incomplete" not in keys


def test_ch_sds_review_accepts_section_9_basis_data_and_structured_swiss_law(tmp_path) -> None:
    text = (
        A15_030_REVIEW_TEXT.replace(
            """9. Physikalische und chemische Eigenschaften
Aggregatzustand/Form: nicht verfügbar
Farbe: nicht verfügbar
Geruch: nicht verfügbar
pH-Wert: nicht verfügbar
Dichte: nicht verfügbar
Löslichkeit: nicht verfügbar
Viskosität: nicht verfügbar
Flammpunkt: nicht verfügbar""",
            """9. Physikalische und chemische Eigenschaften
Aggregatzustand/Form: flüssig
Farbe: strohgelb
Geruch: charakteristisch
pH-Wert: 8,5 - 9,5
Dichte: 1,000 - 1,060 g/cm3
Löslichkeit: vollständig in Wasser löslich""",
        )
        .replace(
            "15. Rechtsvorschriften\nSchweizer Rechtsvorschriften sind zu beachten.",
            """15. Rechtsvorschriften
Schweizer Rechtsvorschriften
- Chemikaliengesetz (ChemG, SR 813.1)
- Chemikalienverordnung (ChemV, SR 813.11)
- Chemikalien-Risikoreduktions-Verordnung (ChemRRV, SR 814.81), soweit anwendbar
- SUVA-Grenzwerte, soweit relevant
- VeVA, soweit Entsorgung betroffen ist""",
        )
    )
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"] for issue in result["issues"]}

    assert "section_9_many_missing_values" not in keys
    assert "section_9_missing_physical_state" not in keys
    assert "swiss_law_generic" not in keys


def test_ch_sds_review_blocks_review_draft_as_final(tmp_path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, status="approved")
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert keys["review_draft_is_final"]["severity"] == "critical"


def test_ch_sds_review_detects_ufi_only_in_section_2(tmp_path) -> None:
    text = """
1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens
1.1 Produktidentifikator
Produktname: Testprodukt
Artikelnummer: TEST-1
1.4 Notrufnummer
Tox Info Suisse 145

2. Mögliche Gefahren
GHS05
H318 Verursacht schwere Augenschäden.
UFI: 0A80-10U4-F00M-UJ45

3. Zusammensetzung / Angaben zu Bestandteilen
Stoff CAS 64-17-5 H225

14. Angaben zum Transport
Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA.

15. Rechtsvorschriften
Schweizer Rechtsvorschriften
- ChemV SR 813.11
- ChemRRV SR 814.81
- SUVA

16. Sonstige Angaben
Verordnung (EG) Nr. 1907/2006 REACH.
"""
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert keys["ufi_not_in_section_1_1"]["severity"] == "warning"


def test_ch_sds_review_detects_missing_product_identifier(tmp_path) -> None:
    text = """
1. Bezeichnung des Stoffs bzw. Gemischs und des Unternehmens
1.4 Notrufnummer
Tox Info Suisse 145

2. Mögliche Gefahren
GHS05
H318 Verursacht schwere Augenschäden.
UFI: 0A80-10U4-F00M-UJ45

3. Zusammensetzung / Angaben zu Bestandteilen
Stoff CAS 64-17-5 H225

14. Angaben zum Transport
Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA.
"""
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"]: issue for issue in result["issues"]}

    assert keys["product_identifier_missing"]["severity"] == "critical"


def test_ch_sds_review_does_not_flag_ghs07_priority_when_other_hazard_justifies_it(tmp_path) -> None:
    text = A15_030_REVIEW_TEXT.replace("H318 Verursacht schwere Augenschäden.", "H318 Verursacht schwere Augenschäden.\nH335 Kann die Atemwege reizen.\nSTOT SE 3")
    SessionLocal = _session(tmp_path)
    with SessionLocal() as session:
        document = _document(session, text=text)
        result = review_sds_document(session, document.id)
        keys = {issue["issue_key"] for issue in result["issues"]}

    assert "ghs07_priority_review" not in keys
