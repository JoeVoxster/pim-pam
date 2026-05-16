from pathlib import Path

import fitz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.pdf.parser import extract_pdf_text
from app.pdf.sdb_renderer import render_sdb_pdf
from app.schemas.pim import ProductCreate, ProductSDBUpdate, VariantCreate
from app.services.pim_service import create_product, get_product_sdb, upsert_product_sdb
from app.services.sdb_support import (
    default_sdb_sections,
    prepare_sdb_sections_for_render,
    validate_sdb_payload,
    validate_sdb_sections,
)


def _base_sections() -> dict[str, dict[str, object]]:
    sections = default_sdb_sections()
    sections["section_1"]["content"] = (
        "1.1 Produktidentifikator\n"
        "Handelsname: Demo Chemieprodukt Natriumhypochlorit 14%\n\n"
        "1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird\n"
        "Relevante identifizierte Verwendungen: Industrielles Oxidationsmittel\n"
        "Verwendungen, von denen abgeraten wird: Private oder nicht spezifizierte Anwendungen\n"
    )
    sections["section_1"]["fields"]["identified_uses"] = "Industrielles Oxidationsmittel"
    sections["section_1"]["fields"]["uses_advised_against"] = "Private oder nicht spezifizierte Anwendungen"
    sections["section_2"]["content"] = (
        "Einstufung gemäss Verordnung (EG) Nr. 1272/2008\n"
        "Gefahrenhinweise\n"
        "H314 Verursacht schwere Verätzungen der Haut und schwere Augenschäden.\n"
        "H400 Sehr giftig für Wasserorganismen.\n"
        "Sicherheitshinweise\n"
        "P280 Schutzhandschuhe tragen.\n"
        "P305+P351+P338 BEI KONTAKT MIT DEN AUGEN: Einige Minuten lang behutsam mit Wasser spülen.\n"
    )
    for index in range(3, 14):
        sections[f"section_{index}"]["content"] = f"Abschnitt {index} vorhanden."
    sections["section_8"]["content"] = (
        "8.1 Zu überwachende Parameter\n"
        "Keine zusätzlichen Angaben.\n\n"
        "8.2 Begrenzung und Überwachung der Exposition / persönliche Schutzausrüstung\n"
        "Atemschutz: Nicht erforderlich.\n"
        "Handschutz: Laugenbeständige Schutzhandschuhe.\n"
        "Augenschutz: Dichtschliessende Schutzbrille.\n"
    )
    sections["section_9"]["fields"]["appearance"] = "fluessig"
    sections["section_9"]["fields"]["color"] = "gelbgruen"
    sections["section_9"]["fields"]["odor"] = "nach Chlor"
    sections["section_9"]["fields"]["ph_value"] = "12 bis 13"
    sections["section_9"]["fields"]["density"] = "1.21 bis 1.23 g/cm3"
    sections["section_14"]["fields"].update(
        {
            "un_number_14_1": "1791",
            "shipping_name_14_2": "HYPOCHLORITLOESUNG",
            "transport_class_14_3": "8",
            "packing_group_14_4": "II",
            "environmental_hazards_14_5": "UMWELTGEFAEHRDEND",
            "special_precautions_for_user": "Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
            "bulk_transport_marpol_ibor_imo_or_equivalent_14_7": "Nicht anwendbar bzw. keine Daten verfügbar.",
        }
    )
    sections["section_15"]["fields"]["regulations_ch"] = "CH-Vorschriften gemäss Chemikalienrecht beachten."
    sections["section_15"]["fields"]["chemical_safety_assessment"] = "Nicht verfügbar."
    sections["section_16"]["fields"]["revision_notes"] = "Stand 2025-09-11."
    sections["section_16"]["fields"]["abbreviations_and_acronyms"] = "ADR, IMDG, IATA, GHS, CAS, EG"
    sections["section_16"]["fields"]["h_statement_wording_if_needed"] = (
        "H314 Verursacht schwere Verätzungen der Haut und schwere Augenschäden.; "
        "H400 Sehr giftig für Wasserorganismen."
    )
    return sections


def _render_demo_pdf(tmp_path: Path, *, sections: dict[str, dict[str, object]], review_status: str = "review_required") -> Path:
    render_sections = prepare_sdb_sections_for_render(
        sections,
        review_status=review_status,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
        product_context={
            "un_number": "1791",
            "hazard_class": "8",
            "packing_group": "II",
            "hazard_shipping_note": "Schutzmassnahmen gemäss Abschnitt 7 und 8 beachten.",
            "density": "1.21 bis 1.23 g/cm3",
            "ph_value": "12 bis 13",
            "odor": "nach Chlor",
            "color": "gelbgruen",
        },
    )
    pdf_path = tmp_path / "sdb.pdf"
    render_sdb_pdf(
        document_title="Natriumhypochlorit 14 % 25 kg",
        product_title="Demo Chemieprodukt Natriumhypochlorit 14%",
        brand_name="Demo Chem",
        sku="CHEM-DEMO-001",
        cas_number="7681-52-9",
        ec_number="231-668-3",
        un_number="1791",
        signal_word="GEFAHR",
        ghs_pictograms="GHS05|GHS09",
        review_status=review_status,
        version_label="Version 16 / CH",
        effective_date="2025-09-11",
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_address_line2=None,
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        sections=render_sections,
        output_path=pdf_path,
    )
    return pdf_path


def test_validator_fails_when_14_6_14_7_15_16_missing() -> None:
    sections = _base_sections()
    sections["section_14"]["fields"]["special_precautions_for_user"] = ""
    sections["section_14"]["fields"]["bulk_transport_marpol_ibor_imo_or_equivalent_14_7"] = ""
    sections["section_15"]["content"] = ""
    sections["section_15"]["fields"]["regulations_ch"] = ""
    sections["section_15"]["fields"]["chemical_safety_assessment"] = ""
    sections["section_16"]["content"] = ""
    sections["section_16"]["fields"]["revision_notes"] = ""
    sections["section_16"]["fields"]["abbreviations_and_acronyms"] = ""
    sections["section_16"]["fields"]["h_statement_wording_if_needed"] = ""

    result = validate_sdb_payload(sections, issuer_phone="+41 52 502 67 23", issuer_email="info@voxster.ch")

    assert result["is_valid"] is False
    assert "section_14: 14.6 fehlt oder ist doppelt" in result["errors"]
    assert "section_14: 14.7 fehlt oder ist doppelt" in result["errors"]
    assert "section_15: Abschnitt ist leer" in result["errors"]
    assert "section_16: Abschnitt ist leer" in result["errors"]


def test_validator_fails_when_phone_or_email_missing() -> None:
    sections = _base_sections()

    result = validate_sdb_payload(sections, issuer_phone=None, issuer_email=None)

    assert result["is_valid"] is False
    assert "section_1.3.supplier_phone fehlt" in result["errors"]
    assert "section_1.3.supplier_email fehlt" in result["errors"]


def test_validator_fails_when_identified_uses_missing_without_fallback() -> None:
    sections = _base_sections()
    sections["section_1"]["fields"]["identified_uses"] = ""
    sections["section_1"]["content"] = "1.1 Produktidentifikator\nHandelsname: Demo Chemieprodukt"

    result = validate_sdb_payload(sections, issuer_phone="+41 52 502 67 23", issuer_email="info@voxster.ch")

    assert result["is_valid"] is False
    assert "section_1.2.identified_uses fehlt" in result["errors"]


def test_draft_duplicate_subsections_are_rendered_only_once() -> None:
    sections = _base_sections()
    sections["section_1"]["content"] = (
        sections["section_1"]["content"]
        + "\n1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird\n"
        "Relevante identifizierte Verwendungen: Industrielles Oxidationsmittel\n"
        "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt\n"
        "VOXSTER GmbH\n"
        "1.4 Notrufnummer\n145\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    section_1 = rendered["section_1"]["content"]

    assert section_1.count("1.2") == 1
    assert section_1.count("1.3") == 1
    assert section_1.count("1.4") == 1


def test_section_1_1_does_not_repeat_productidentifikator_label() -> None:
    sections = _base_sections()
    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )

    assert rendered["section_1"]["content"].count("Produktidentifikator") == 1


def test_section_1_cleanup_removes_ocr_label_noise() -> None:
    sections = _base_sections()
    sections["section_1"]["fields"]["identified_uses"] = ""
    sections["section_1"]["fields"]["uses_advised_against"] = ""
    sections["section_1"]["content"] = (
        "1.1 Produktidentifikator\n"
        ". Produktidentifikator\n\n"
        "Natrii hypochlorosi 14% solut\n"
        "Artikel-Nr.\n"
        "21370000\n"
        "Registrierungsnr.\n"
        "EG-Nr.:\n"
        "231-668-3\n"
        "Registrierungsnr.\n"
        "01-2119488154-34-XXXX\n"
        "CAS-Nr.\n"
        "7681-52-9\n"
        "Stoff- / Produktidentifikation\n"
        "UFI\n"
        "R7KM-70NA-7008-S09P\n\n"
        "1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird\n"
        "Relevante identifizierte Verwendungen: . Relevante identifizierte Verwendungen des Stoffs oder Gemischs und\n"
        "Verwendungen, von denen abgeraten wird\n"
        "Verwendungen, von denen abgeraten wird\n"
        "PC8\n"
        "Biozidprodukte (z. B. Desinfektionsmittel, Schädlingsbekämpfungsmittel)\n"
        "Verwendungen, von denen abgeraten wird: Verwendungen, von denen abgeraten wird\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    section_1 = rendered["section_1"]["content"]

    assert ". Produktidentifikator" not in section_1
    assert "Artikel-Nr.: 21370000" in section_1
    assert "EG-Nr.: 231-668-3" in section_1
    assert "Registrierungsnr.: 01-2119488154-34-XXXX" in section_1
    assert "CAS-Nr.: 7681-52-9" in section_1
    assert "UFI: R7KM-70NA-7008-S09P" in section_1
    assert "Relevante identifizierte Verwendungen: Nicht verfügbar." in section_1
    assert "Verwendungen, von denen abgeraten wird: PC8 Biozidprodukte (z. B. Desinfektionsmittel, Schädlingsbekämpfungsmittel)" in section_1
    assert "Verwendungen, von denen abgeraten wird: Verwendungen, von denen abgeraten wird" not in section_1


def test_supplier_and_manufacturer_are_separated_cleanly() -> None:
    sections = _base_sections()
    sections["section_1"]["content"] += (
        "\n1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt\n"
        "Hänseler AG\n"
        "Industriestrasse 35\n"
        "9100 Herisau\n"
        "Telefon: +41 71 353 58 58\n"
        "E-Mail: sdb@haenseler.ch\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    section_1 = rendered["section_1"]["content"]

    assert "VOXSTER GmbH" in section_1
    assert "Hersteller (laut Quelle):" in section_1
    assert "Hänseler AG" in section_1
    assert section_1.count("VOXSTER GmbH") == 1


def test_supplier_and_manufacturer_cleanup_removes_ocr_junk() -> None:
    sections = _base_sections()
    sections["section_1"]["fields"]["manufacturer_name"] = ""
    sections["section_1"]["fields"]["manufacturer_address"] = ""
    sections["section_1"]["fields"]["manufacturer_phone"] = ""
    sections["section_1"]["fields"]["manufacturer_email"] = ""
    sections["section_1"]["content"] = (
        "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt\n"
        "VOXSTER GmbH\n"
        "Obere Ifangstrasse 10\n"
        "8215 Hallau\n"
        "CH\n"
        "Telefon: +41 52 502 67 23\n"
        "E-Mail der für das SDB verantwortlichen Person: info@voxster.ch\n\n"
        "Hersteller (laut Quelle):\n"
        ". Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt\n"
        "Adresse/Hersteller\n"
        "Hänseler AG\n"
        "Industriestrasse 35\n"
        "9100 Herisau\n"
        "0041 (0)71 353 58 58\n"
        "verantwortlichen\n"
        "Person für dieses\n"
        "SDB\n"
        "sdb@haenseler.ch\n"
        "Telefon Hersteller: Nr.\n"
        "E-Mail Hersteller: Adresse der\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    fields = rendered["section_1"]["fields"]
    section_1 = rendered["section_1"]["content"]

    assert fields["manufacturer_name"] == "Hänseler AG"
    assert fields["manufacturer_address"] == "Industriestrasse 35\n9100 Herisau"
    assert fields["manufacturer_phone"] == "0041 (0)71 353 58 58"
    assert fields["manufacturer_email"] == "sdb@haenseler.ch"
    assert ". Einzelheiten zum Lieferanten" not in section_1
    assert "Telefon Hersteller: Nr." not in section_1
    assert "E-Mail Hersteller: Adresse der" not in section_1


def test_generated_style_manufacturer_contact_lines_are_preserved_cleanly() -> None:
    sections = _base_sections()
    sections["section_1"]["fields"]["manufacturer_name"] = ""
    sections["section_1"]["fields"]["manufacturer_address"] = ""
    sections["section_1"]["fields"]["manufacturer_phone"] = ""
    sections["section_1"]["fields"]["manufacturer_email"] = ""
    sections["section_1"]["content"] = (
        "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt\n"
        "VOXSTER GmbH\n"
        "Obere Ifangstrasse 10\n"
        "8215 Hallau\n"
        "CH\n"
        "Telefon: +41 52 502 67 23\n"
        "E-Mail der für das SDB verantwortlichen Person: info@voxster.ch\n\n"
        "Hersteller (laut Quelle):\n"
        "Hänseler AG\n"
        "Industriestrasse 35\n"
        "9100 Herisau\n"
        "Telefon Hersteller: 0041 (0)71 353 58 58\n"
        "E-Mail Hersteller: sdb@haenseler.ch\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    fields = rendered["section_1"]["fields"]

    assert fields["manufacturer_name"] == "Hänseler AG"
    assert fields["manufacturer_phone"] == "0041 (0)71 353 58 58"
    assert fields["manufacturer_email"] == "sdb@haenseler.ch"


def test_polluted_stored_identified_uses_are_suppressed_in_favour_of_clean_fallbacks() -> None:
    sections = _base_sections()
    sections["section_1"]["fields"]["identified_uses"] = (
        ". Relevante identifizierte Verwendungen des Stoffs oder Gemischs und\n"
        "Verwendungen, von denen abgeraten wird\n"
        "PC8\n"
        "Biozidprodukte (z. B. Desinfektionsmittel, Schädlingsbekämpfungsmittel)"
    )
    sections["section_1"]["fields"]["uses_advised_against"] = "Verwendungen, von denen abgeraten wird"
    sections["section_1"]["content"] = (
        "1.2 Relevante identifizierte Verwendungen des Stoffs oder Gemischs und Verwendungen, von denen abgeraten wird\n"
        "Verwendungen, von denen abgeraten wird\n"
        "PC8\n"
        "Biozidprodukte (z. B. Desinfektionsmittel, Schädlingsbekämpfungsmittel)\n"
    )

    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    section_1 = rendered["section_1"]["content"]

    assert "Relevante identifizierte Verwendungen: Nicht verfügbar." in section_1
    assert "Verwendungen, von denen abgeraten wird: PC8 Biozidprodukte (z. B. Desinfektionsmittel, Schädlingsbekämpfungsmittel)" in section_1


def test_section_9_real_values_suppress_placeholders() -> None:
    sections = _base_sections()
    sections["section_9"]["fields"]["density"] = "1.25 g/cm3\nnicht verfügbar"
    rendered = prepare_sdb_sections_for_render(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )
    section_9 = rendered["section_9"]["content"]

    assert "Dichte und/oder relative Dichte: 1.25 g/cm3" in section_9
    assert "Dichte und/oder relative Dichte: nicht verfügbar" not in section_9


def test_section_9_uses_product_voc_content_from_context() -> None:
    sections = default_sdb_sections()
    rendered = prepare_sdb_sections_for_render(
        sections,
        review_status="review_required",
        product_context={"voc_content_percent": "1.12 %"},
    )
    section_9 = rendered["section_9"]["content"]

    assert "VOC-Gehalt: 1.12 %" in section_9
    assert "VOC-Gehalt: nicht verfügbar" not in section_9


def test_validator_fails_for_conflicting_transport_flags() -> None:
    sections = _base_sections()
    sections["section_14"]["content"] = (
        "14.5 Umweltgefahren: UMWELTGEFAEHRDEND\n"
        "14.5 Umweltgefahren: Gemäss vorliegenden Daten prüfen.\n"
    )

    result = validate_sdb_sections(
        sections,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
        product_context={"un_number": "1791", "hazard_class": "8", "packing_group": "II"},
    )

    assert result["is_valid"] is False
    assert any(error.startswith("no_conflicting_transport_flags:") for error in result["errors"])


def test_validator_fails_for_release_build_with_review_markers() -> None:
    sections = _base_sections()
    sections["section_16"]["content"] = "Automatisch aufbereitete CH-Review-Ausgabe; fachliche Prüfung erforderlich."

    result = validate_sdb_sections(
        sections,
        review_status="released",
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        issuer_phone="+41 52 502 67 23",
        issuer_email="info@voxster.ch",
    )

    assert result["is_valid"] is False
    assert "no_release_with_review_markers verletzt" in result["errors"]


def test_generator_renders_14_1_to_14_7_and_15_16(tmp_path) -> None:
    pdf_path = _render_demo_pdf(tmp_path, sections=_base_sections())
    text = extract_pdf_text(pdf_path)

    assert "14.1" in text
    assert "14.2" in text
    assert "14.3" in text
    assert "14.4" in text
    assert "14.5" in text
    assert "14.6" in text
    assert "14.7" in text
    assert "15. Rechtsvorschriften" in text
    assert "16. Sonstige Angaben" in text


def test_generator_renders_section_9_fallbacks(tmp_path) -> None:
    sections = _base_sections()
    sections["section_9"]["fields"]["appearance"] = ""
    sections["section_9"]["fields"]["melting_point"] = ""
    pdf_path = _render_demo_pdf(tmp_path, sections=sections)
    text = extract_pdf_text(pdf_path)

    assert "Aggregatzustand/Form" in text
    assert "Schmelzpunkt/Gefrierpunkt" in text
    assert "nicht verfügbar" in text or "nicht verfuegbar" in text


def test_pdf_embeds_ghs_assets(tmp_path) -> None:
    pdf_path = _render_demo_pdf(tmp_path, sections=_base_sections())
    doc = fitz.open(pdf_path)
    try:
        image_count = sum(len(page.get_images(full=True)) for page in doc)
    finally:
        doc.close()
    assert image_count >= 2


def test_pdf_renders_ghs05_and_ghs07_as_separate_pictograms(tmp_path) -> None:
    sections = _base_sections()
    pdf_path = tmp_path / "ghs07.pdf"
    render_sdb_pdf(
        document_title="GHS07 Test",
        product_title="GHS07 Test",
        brand_name="VOXSTER",
        sku="GHS-TEST",
        cas_number=None,
        ec_number=None,
        un_number=None,
        signal_word="GEFAHR",
        ghs_pictograms="GHS05|GHS07",
        review_status="review_required",
        version_label="1",
        effective_date="2026-05-14",
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_address_line2=None,
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        sections=sections,
        output_path=pdf_path,
    )
    doc = fitz.open(pdf_path)
    try:
        text = "\n".join(page.get_text("text") for page in doc)
        image_count = sum(len(page.get_images(full=True)) for page in doc)
        rendered_rects = []
        for page in doc:
            for image in page.get_images(full=True):
                rendered_rects.extend(page.get_image_rects(image[0]))
    finally:
        doc.close()

    assert "GHS07" in text
    assert image_count >= 2
    assert len(rendered_rects) >= 2
    widths = [round(rect.width, 1) for rect in rendered_rects[:2]]
    heights = [round(rect.height, 1) for rect in rendered_rects[:2]]
    assert widths == [42.0, 42.0]
    assert heights == [42.0, 42.0]


def test_pdf_renders_all_supplied_ghs_codes_even_when_review_flags_priority(tmp_path) -> None:
    sections = _base_sections()
    sections["section_2"]["content"] = (
        "2.1 Einstufung des Stoffs oder Gemischs\n"
        "Skin Irrit. 2, H315\n"
        "Eye Dam. 1, H318\n\n"
        "2.2 Kennzeichnungselemente\n"
        "Piktogramme: GHS05, GHS07\n"
        "Signalwort: Gefahr\n"
        "Gefahrenhinweise\n"
        "H315 Verursacht Hautreizungen.\n"
        "H318 Verursacht schwere Augenschäden.\n"
    )
    pdf_path = tmp_path / "ghs05-only.pdf"
    render_sdb_pdf(
        document_title="GHS05 Test",
        product_title="GHS05 Test",
        brand_name="VOXSTER",
        sku="GHS-TEST",
        cas_number=None,
        ec_number=None,
        un_number=None,
        signal_word="GEFAHR",
        ghs_pictograms="GHS05|GHS07",
        review_status="review_required",
        version_label="1",
        effective_date="2026-05-14",
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_address_line2=None,
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        sections=sections,
        output_path=pdf_path,
    )
    doc = fitz.open(pdf_path)
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()

    assert "GHS05" in text
    assert "GHS07" in text


def test_adr_transport_pictogram_fallback_does_not_render_raw_codes(tmp_path) -> None:
    sections = default_sdb_sections()
    sections["section_14"]["content"] = (
        "14.3 Transportgefahrenklassen\n"
        "8 / 8 / 8\n\n"
        "14.5 Umweltgefahren\n"
        "ADR/RID: UMWELTGEFÄHRDEND"
    )
    pdf_path = tmp_path / "adr.pdf"
    render_sdb_pdf(
        product_title="Natriumhypochlorit 14 %",
        brand_name="VOXSTER",
        sku="CHEM-ADR",
        cas_number="7681-52-9",
        ec_number="231-668-3",
        un_number="1791",
        signal_word="GEFAHR",
        ghs_pictograms="GHS05|GHS09",
        adr_pictograms="ADR_3|ADR_5.1|ADR_8|ADR_pollution|ADR_LQ",
        review_status="review_required",
        version_label="1",
        effective_date="2026-05-01",
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_address_line2=None,
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        sections=sections,
        output_path=pdf_path,
    )

    text = extract_pdf_text(pdf_path)
    assert "ADR-Kennzeichnung / Transportpiktogramme" in text
    assert "Klasse 3" in text
    assert "Klasse 5.1" in text
    assert "Klasse 8" in text
    assert "Umwelt" in text
    assert "LQ" in text
    assert "ADR_3" not in text
    assert "ADR_5.1" not in text
    assert "ADR_8" not in text
    assert "ADR_pollution" not in text
    assert "ADR_LQ" not in text


def test_ch_sdb_section_13_uses_actionable_disposal_review_text() -> None:
    sections = default_sdb_sections()
    rendered = prepare_sdb_sections_for_render(sections, review_status="review_required")
    section_13 = rendered["section_13"]["content"]

    assert "Schweizer Entsorgungshinweise" in section_13
    assert "bewilligten Entsorgungsbetrieb" in section_13
    assert "VVEA, VeVA, LVA" in section_13
    assert "Schweizer Abfallcode/LVA-Code: fachlich prüfen" in section_13


def test_ch_sdb_section_14_uses_not_applicable_when_source_says_no_dangerous_goods() -> None:
    sections = default_sdb_sections()
    sections["section_14"]["content"] = "Nicht im Anwendungsbereich der Vorschriften für den Transport gefährlicher Güter."
    rendered = prepare_sdb_sections_for_render(sections, review_status="review_required")
    section_14 = rendered["section_14"]["content"]

    assert "14.1 UN-Nummer oder ID-Nummer: Nicht anwendbar" in section_14
    assert "14.2 Ordnungsgemässe UN-Versandbezeichnung: Nicht anwendbar" in section_14
    assert "14.3 Transportgefahrenklassen: Nicht anwendbar" in section_14


def test_ch_sdb_section_14_marks_missing_transport_data_for_review_when_un_present() -> None:
    sections = default_sdb_sections()
    sections["section_14"]["fields"]["un_number_14_1"] = "2021"
    rendered = prepare_sdb_sections_for_render(sections, review_status="review_required")
    section_14 = rendered["section_14"]["content"]

    assert "14.1 UN-Nummer oder ID-Nummer: 2021" in section_14
    assert "14.2 Ordnungsgemässe UN-Versandbezeichnung: fachlich prüfen" in section_14
    assert "14.3 Transportgefahrenklassen: fachlich prüfen" in section_14


def test_page_numbering_is_consistent(tmp_path) -> None:
    pdf_path = _render_demo_pdf(tmp_path, sections=_base_sections())
    doc = fitz.open(pdf_path)
    try:
        total_pages = len(doc)
        page_texts = [page.get_text("text") for page in doc]
    finally:
        doc.close()

    assert total_pages >= 1
    assert f"Seite 1/{total_pages}" in page_texts[0]
    assert f"Seite {total_pages}/{total_pages}" in page_texts[-1]


def test_release_pdf_suppresses_review_markers(tmp_path) -> None:
    pdf_path = _render_demo_pdf(tmp_path, sections=_base_sections(), review_status="released")
    text = extract_pdf_text(pdf_path)

    assert "Review-Entwurf" not in text
    assert "fachliche Prüfung erforderlich" not in text
    assert "Review-Status" not in text


def test_pdf_uses_document_title_and_suppresses_meta_lines(tmp_path) -> None:
    pdf_path = _render_demo_pdf(tmp_path, sections=_base_sections(), review_status="released")
    text = extract_pdf_text(pdf_path)

    assert "Natriumhypochlorit 14 % 25 kg" in text
    assert "SKU: CHEM-DEMO-001" not in text
    assert "Marke: Demo Chem" not in text
    assert "CAS: 7681-52-9 | EG: 231-668-3 | UN: 1791" not in text
    assert "Signalwort: GEFAHR | GHS: GHS05|GHS09" not in text
    assert "SKU CHEM-DEMO-001" not in text


def test_demo_product_pdf_contains_required_markers(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with session_local() as session:
        product, _ = create_product(
            session,
            ProductCreate(
                sku="CHEM-DEMO-001",
                title="Demo Chemieprodukt Natriumhypochlorit 14%",
                brand_name="Demo Chem",
                status="active",
                is_chemical=True,
                cas_number="7681-52-9",
                ec_number="231-668-3",
                un_number="1791",
            ),
            VariantCreate(sku="CHEM-DEMO-001", variant_title="Default Variant"),
        )
        upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                issuer_name="VOXSTER GmbH",
                issuer_address_line1="Obere Ifangstrasse 10",
                issuer_postal_code="8215",
                issuer_city="Hallau",
                issuer_country_code="CH",
                issuer_phone="+41 52 502 67 23",
                issuer_email="info@voxster.ch",
                sections_json=_base_sections(),
            ),
        )
        session.commit()
        sdb = get_product_sdb(session, product.id)

    render_sections = prepare_sdb_sections_for_render(
        sdb["sections_json"],
        issuer_name=sdb["issuer_name"],
        issuer_address_line1=sdb["issuer_address_line1"],
        issuer_address_line2=sdb["issuer_address_line2"],
        issuer_postal_code=sdb["issuer_postal_code"],
        issuer_city=sdb["issuer_city"],
        issuer_country_code=sdb["issuer_country_code"],
        issuer_phone=sdb["issuer_phone"],
        issuer_email=sdb["issuer_email"],
        product_context={"un_number": "1791", "hazard_class": "8", "packing_group": "II"},
    )
    pdf_path = tmp_path / "chem-demo.pdf"
    render_sdb_pdf(
        product_title="Demo Chemieprodukt Natriumhypochlorit 14%",
        brand_name="Demo Chem",
        sku="CHEM-DEMO-001",
        cas_number="7681-52-9",
        ec_number="231-668-3",
        un_number="1791",
        signal_word="GEFAHR",
        ghs_pictograms="GHS05|GHS09",
        review_status=sdb["review_status"],
        version_label=sdb["version_label"],
        effective_date="2025-09-11",
        issuer_name=sdb["issuer_name"],
        issuer_address_line1=sdb["issuer_address_line1"],
        issuer_address_line2=sdb["issuer_address_line2"],
        issuer_postal_code=sdb["issuer_postal_code"],
        issuer_city=sdb["issuer_city"],
        issuer_country_code=sdb["issuer_country_code"],
        sections=render_sections,
        output_path=pdf_path,
    )
    text = extract_pdf_text(pdf_path)

    assert "14.6" in text
    assert "14.7" in text
    assert "15. Rechtsvorschriften" in text
    assert "16. Sonstige Angaben" in text
    assert "+41 52 502 67 23" in text
    assert "info@voxster.ch" in text
    assert "Relevante identifizierte Verwendungen" in text
    assert Path(pdf_path).exists()
