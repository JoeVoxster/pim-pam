from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    ChemicalDocument,
    Product,
    ProductSuvaCheck,
    ProductSuvaCheckItem,
    SuvaLimitEntry,
    SuvaLimitSource,
    SuvaSubstanceAlias,
)
from app.services.clp_pictogram_service import pictogram_review_payload
from app.services.sdb_support import merge_sdb_sections


CAS_RE = re.compile(r"(?<![\d-])[0-9]{2,7}-[0-9]{2}-[0-9](?![\d-])")
EC_RE = re.compile(r"\b[0-9]{3}-[0-9]{3}-[0-9]\b")
H_STATEMENT_RE = re.compile(r"\bH[0-9]{3}[A-Z]?\b")
HIGH_REVIEW_H_RE = re.compile(r"\b(H334|H340|H350|H360|H361)\b")

SUVA_UPLOAD_DIR = Path("/opt/data/compliance/suva")


@dataclass(frozen=True)
class SDSIngredient:
    name: str | None
    cas_number: str | None
    ec_number: str | None
    index_number: str | None
    concentration: str | None
    h_statements: str | None
    raw_text: str


def import_suva_xlsx(
    session: Session,
    file_bytes: bytes,
    file_name: str,
    *,
    imported_by: str | None = None,
    source_name: str = "SUVA Grenzwerte am Arbeitsplatz",
    source_url: str | None = None,
    language: str | None = "de",
    notes: str | None = None,
) -> dict[str, Any]:
    if not file_bytes:
        raise ValueError("Keine SUVA-Datei erhalten.")
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    existing = session.scalar(select(SuvaLimitSource).where(SuvaLimitSource.sha256 == sha256))
    if existing:
        return {
            "status": "duplicate",
            "source_id": existing.id,
            "sha256": sha256,
            "entries_imported": 0,
            "message": "Diese SUVA-Datei wurde bereits importiert.",
        }

    stored_path = _store_upload(file_bytes, file_name, sha256)
    source = SuvaLimitSource(
        source_name=source_name or "SUVA Grenzwerte am Arbeitsplatz",
        source_url=source_url,
        imported_by=imported_by,
        file_name=file_name or stored_path.name,
        sha256=sha256,
        language=language,
        notes=notes,
    )
    session.add(source)
    session.flush()

    rows = _parse_suva_workbook(file_bytes)
    imported = 0
    skipped = 0
    for row in rows:
        entry = _entry_from_row(source.id, row)
        if not entry.substance_name and not entry.cas_number:
            skipped += 1
            continue
        session.add(entry)
        session.flush()
        imported += 1
        for alias in _aliases_for_entry(entry):
            session.add(SuvaSubstanceAlias(entry_id=entry.id, alias=alias, language=language, source="xlsx"))

    source.notes = _join_notes(notes, f"Gespeicherte Importdatei: {stored_path}")
    session.flush()
    return {
        "status": "imported",
        "source_id": source.id,
        "sha256": sha256,
        "entries_imported": imported,
        "rows_skipped": skipped,
        "stored_path": str(stored_path),
    }


def latest_suva_source(session: Session) -> SuvaLimitSource | None:
    return session.scalar(select(SuvaLimitSource).order_by(SuvaLimitSource.imported_at.desc(), SuvaLimitSource.id.desc()))


def list_suva_sources(session: Session, limit: int = 20) -> list[dict[str, Any]]:
    rows = session.scalars(select(SuvaLimitSource).order_by(SuvaLimitSource.imported_at.desc(), SuvaLimitSource.id.desc()).limit(limit)).all()
    return [serialize_suva_source(row) for row in rows]


def serialize_suva_source(row: SuvaLimitSource) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_name": row.source_name,
        "source_url": row.source_url,
        "imported_at": row.imported_at.isoformat() if row.imported_at else None,
        "imported_by": row.imported_by,
        "file_name": row.file_name,
        "sha256": row.sha256,
        "language": row.language,
        "notes": row.notes,
    }


def run_product_suva_check(
    session: Session,
    product_id: int,
    *,
    sds_id: int | None = None,
    source_id: int | None = None,
    checked_by: str | None = None,
) -> dict[str, Any]:
    product = session.get(Product, int(product_id))
    if not product:
        raise ValueError(f"Produkt {product_id} nicht gefunden.")
    document = _resolve_document(session, product.id, sds_id)
    source = session.get(SuvaLimitSource, int(source_id)) if source_id else latest_suva_source(session)

    if not source:
        check = ProductSuvaCheck(
            product_id=product.id,
            sds_id=document.id if document else None,
            source_id=None,
            checked_by=checked_by,
            overall_status="BLOCKER",
            report_json={
                "overall_status": "BLOCKER",
                "message": "Kein SUVA-Import vorhanden. CH-SDS-Freigabe ist blockiert, bis eine SUVA-Liste importiert und geprüft wurde.",
            },
        )
        session.add(check)
        session.flush()
        return serialize_suva_check(check)

    text = _document_text(document)
    ingredients = extract_sds_ingredients(text)
    session.execute(delete(ProductSuvaCheck).where(ProductSuvaCheck.product_id == product.id, ProductSuvaCheck.sds_id == (document.id if document else None)))
    check = ProductSuvaCheck(product_id=product.id, sds_id=document.id if document else None, source_id=source.id, checked_by=checked_by)
    session.add(check)
    session.flush()

    if not ingredients:
        check.overall_status = "BLOCKER"
        check.report_json = {
            "overall_status": "BLOCKER",
            "source_id": source.id,
            "message": "Keine Abschnitt-3-Inhaltsstoffe gefunden. SUVA-Prüfung kann nicht dokumentiert werden.",
            "items": [],
        }
        session.flush()
        return serialize_suva_check(check)

    item_payloads: list[dict[str, Any]] = []
    severities: list[str] = []
    for ingredient in ingredients:
        entry, match_status = _match_ingredient(session, source.id, ingredient)
        severity, note = _severity_for_match(ingredient, entry, match_status)
        item = ProductSuvaCheckItem(
            check_id=check.id,
            ingredient_name=ingredient.name,
            cas_number=ingredient.cas_number,
            ec_number=ingredient.ec_number,
            index_number=ingredient.index_number,
            concentration=ingredient.concentration,
            h_statements=ingredient.h_statements,
            match_status=match_status,
            suva_entry_id=entry.id if entry else None,
            mak_ppm=entry.mak_ppm if entry else None,
            mak_mg_m3=entry.mak_mg_m3 if entry else None,
            kzgw_ppm=entry.kzgw_ppm if entry else None,
            kzgw_mg_m3=entry.kzgw_mg_m3 if entry else None,
            bat_value=entry.bat_value if entry else None,
            bat_matrix=entry.bat_matrix if entry else None,
            notations=entry.notations if entry else None,
            review_note=note,
            severity=severity,
        )
        session.add(item)
        session.flush()
        severities.append(severity)
        item_payloads.append(serialize_suva_check_item(item))

    check.overall_status = "BLOCKER" if "BLOCKER" in severities else "WARNING" if "WARNING" in severities else "OK"
    check.report_json = {
        "overall_status": check.overall_status,
        "source": serialize_suva_source(source),
        "ingredient_count": len(ingredients),
        "items": item_payloads,
    }
    session.flush()
    return serialize_suva_check(check)


def latest_product_suva_check(session: Session, product_id: int, sds_id: int | None = None) -> ProductSuvaCheck | None:
    stmt = select(ProductSuvaCheck).where(ProductSuvaCheck.product_id == int(product_id))
    if sds_id is not None:
        stmt = stmt.where(ProductSuvaCheck.sds_id == int(sds_id))
    return session.scalar(stmt.order_by(ProductSuvaCheck.checked_at.desc(), ProductSuvaCheck.id.desc()))


def serialize_suva_check(check: ProductSuvaCheck) -> dict[str, Any]:
    return {
        "id": check.id,
        "product_id": check.product_id,
        "sds_id": check.sds_id,
        "source_id": check.source_id,
        "checked_at": check.checked_at.isoformat() if check.checked_at else None,
        "checked_by": check.checked_by,
        "overall_status": check.overall_status,
        "source": serialize_suva_source(check.source) if check.source else None,
        "items": [serialize_suva_check_item(item) for item in sorted(check.items, key=lambda row: row.id or 0)],
        "report_json": check.report_json,
    }


def serialize_suva_check_item(item: ProductSuvaCheckItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "check_id": item.check_id,
        "ingredient_name": item.ingredient_name,
        "cas_number": item.cas_number,
        "ec_number": item.ec_number,
        "index_number": item.index_number,
        "concentration": item.concentration,
        "h_statements": item.h_statements,
        "match_status": item.match_status,
        "suva_entry_id": item.suva_entry_id,
        "mak_ppm": item.mak_ppm,
        "mak_mg_m3": item.mak_mg_m3,
        "kzgw_ppm": item.kzgw_ppm,
        "kzgw_mg_m3": item.kzgw_mg_m3,
        "bat_value": item.bat_value,
        "bat_matrix": item.bat_matrix,
        "notations": item.notations,
        "review_note": item.review_note,
        "severity": item.severity,
    }


def generate_section_8_1_ch_block(session: Session, check_id: int) -> str:
    check = session.execute(
        select(ProductSuvaCheck)
        .options(joinedload(ProductSuvaCheck.source), joinedload(ProductSuvaCheck.items))
        .where(ProductSuvaCheck.id == int(check_id))
    ).unique().scalar_one_or_none()
    if not check:
        raise ValueError(f"SUVA-Prüfung {check_id} nicht gefunden.")
    source = check.source
    lines = [
        "Schweizer Arbeitsplatzgrenzwerte / SUVA MAK/BAT",
        "",
        "Die in Abschnitt 3 genannten Inhaltsstoffe wurden mit der SUVA-Liste \"Grenzwerte am Arbeitsplatz\" abgeglichen.",
        f"Prüfdatum: {_format_dt(check.checked_at)}",
        f"Quelle: {(source.file_name if source else '-') or '-'}",
        f"SUVA-Import-ID: {(source.id if source else '-')}",
        f"SHA256: {(source.sha256 if source else '-')}",
        "",
    ]
    matched = [item for item in check.items if item.match_status in {"exact_cas_match", "ec_match"} and item.suva_entry_id]
    if matched:
        lines.append("Gefundene Schweizer Grenzwerte:")
        for item in matched:
            values = _limit_values_for_item(item)
            lines.append(f"- {item.ingredient_name or '-'} ({item.cas_number or 'CAS fehlt'}): {values or 'SUVA-Eintrag vorhanden, aber kein MAK/KZGW/BAT-Wert in den importierten Feldern.'}")
        lines.append("")
    unmatched = [item for item in check.items if item.match_status in {"no_match", "manual_review", "name_match", "synonym_match"}]
    if unmatched:
        names = ", ".join(f"{item.ingredient_name or item.cas_number or '-'}" for item in unmatched)
        lines.extend(
            [
                "Für Stoffe ohne spezifischen SUVA-Grenzwert:",
                f"Für folgende Inhaltsstoffe wurde in der importierten SUVA-Liste kein eindeutig bestätigter Schweizer Arbeitsplatzgrenzwert gefunden: {names}.",
                "Dies bedeutet keine Entwarnung. Die allgemeinen Schutzmassnahmen gemäss Abschnitt 8.2 bleiben gültig; Name-/Synonym-Treffer benötigen manuelle Prüfung.",
            ]
        )
    return "\n".join(lines).strip()


def enrich_sdb_sections_with_suva_suggestions(session: Session, sections_json: dict | None, *, product: Product | None = None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Add SUVA import suggestions to parsed SDB sections without accepting review-only matches.

    Safe automatic text suggestions are generated only for exact CAS matches. Name/synonym
    matches stay in metadata under review_required_items and are not inserted into section 8.
    """
    sections = merge_sdb_sections(sections_json)
    source = latest_suva_source(session)
    _add_ch_import_review_suggestions(sections, product=product)
    section_3 = sections["section_3"]
    section_8 = sections["section_8"]
    ingredients = extract_sds_ingredients(str(section_3.get("content") or ""))
    extracted = [_ingredient_payload(row) for row in ingredients]
    section_3.setdefault("fields", {})["extracted_substances"] = extracted

    report: dict[str, Any] = {
        "status": "no_suva_source" if not source else "checked",
        "source": serialize_suva_source(source) if source else None,
        "extracted_substances": extracted,
        "cas_matches": [],
        "review_required_items": [],
        "no_match_items": [],
        "section_8_1_suggestions": [],
        "section_8_2_suggestions": [],
    }
    if not source:
        section_8.setdefault("fields", {})["suva_matches"] = []
        section_8["fields"]["review_required_items"] = []
        section_8["fields"]["section_8_1_suggestions"] = []
        section_8["fields"]["section_8_2_suggestions"] = _section_8_2_suggestions(str(section_8.get("content") or ""), [], str(section_3.get("content") or ""))
        report["section_8_2_suggestions"] = section_8["fields"]["section_8_2_suggestions"]
        return sections, report

    for ingredient in ingredients:
        entry, match_status = _match_ingredient(session, source.id, ingredient)
        item = _suggestion_payload(ingredient, entry, match_status, source)
        if match_status == "exact_cas_match" and entry:
            report["cas_matches"].append(item)
            report["section_8_1_suggestions"].append(item)
        elif match_status in {"synonym_match", "name_match"}:
            item["status"] = "needs_human_review"
            item["confidence"] = "review_required"
            item["review_note"] = "Nur Name-/Synonym-Treffer. Bitte CAS-Nummer und Stoffidentität manuell prüfen."
            report["review_required_items"].append(item)
        else:
            report["no_match_items"].append(item)

    section_8.setdefault("fields", {})["suva_matches"] = report["cas_matches"]
    section_8["fields"]["section_8_1_suggestions"] = report["section_8_1_suggestions"]
    section_8["fields"]["section_8_2_suggestions"] = _section_8_2_suggestions(str(section_8.get("content") or ""), report["cas_matches"], str(section_3.get("content") or ""))
    section_8["fields"]["review_required_items"] = report["review_required_items"] + report["no_match_items"]
    report["section_8_2_suggestions"] = section_8["fields"]["section_8_2_suggestions"]

    if report["cas_matches"]:
        section_8["content"] = _append_suva_suggestion_block(str(section_8.get("content") or ""), report["cas_matches"], section_8["fields"]["section_8_2_suggestions"])
    return sections, report


def _ingredient_payload(row: SDSIngredient) -> dict[str, Any]:
    return {
        "substance_name": row.name,
        "cas_number": row.cas_number,
        "ec_number": row.ec_number,
        "index_number": row.index_number,
        "concentration_range": row.concentration,
        "classification": row.h_statements,
        "source_section": 3,
        "raw_text": row.raw_text,
    }


def _suggestion_payload(ingredient: SDSIngredient, entry: SuvaLimitEntry | None, match_status: str, source: SuvaLimitSource) -> dict[str, Any]:
    matched_by = "cas" if match_status == "exact_cas_match" else "synonym" if match_status == "synonym_match" else "name" if match_status == "name_match" else "none"
    confidence = "high" if matched_by == "cas" else "review_required" if matched_by in {"name", "synonym"} else "none"
    return {
        "substance_name": (entry.substance_name if match_status == "exact_cas_match" and entry else None) or ingredient.name,
        "cas_number": ingredient.cas_number,
        "concentration_range": ingredient.concentration,
        "classification": ingredient.h_statements,
        "matched_by": matched_by,
        "match_status": match_status,
        "confidence": confidence,
        "status": "safe_cas_match" if matched_by == "cas" else "no_match",
        "suva_entry_id": entry.id if entry else None,
        "suva_substance_name": entry.substance_name if entry else None,
        "mak_ppm": entry.mak_ppm if entry else None,
        "mak_mg_m3": entry.mak_mg_m3 if entry else None,
        "kzgw_ppm": entry.kzgw_ppm if entry else None,
        "kzgw_mg_m3": entry.kzgw_mg_m3 if entry else None,
        "bat_value": entry.bat_value if entry else None,
        "bat_matrix": entry.bat_matrix if entry else None,
        "notations": entry.notations if entry else None,
        "remarks": entry.remarks if entry else None,
        "source_name": source.source_name,
        "source_version": source.file_name,
        "downloaded_at": source.imported_at.isoformat() if source.imported_at else None,
        "file_hash": source.sha256,
        "source_id": source.id,
    }


def _append_suva_suggestion_block(section_8: str, cas_matches: list[dict[str, Any]], section_8_2_suggestions: list[str]) -> str:
    text = str(section_8 or "").strip()
    if cas_matches:
        text = re.sub(
            r"(?im)^\s*Für die Schweiz sind keine zusätzlichen MAK-/BAT-Grenzwerte aus diesem Datensatz hinterlegt\.?\s*$",
            "",
            text,
        ).strip()
    existing_cas = set(CAS_RE.findall(text))
    lines = [
        "8.1 Arbeitsplatzgrenzwerte Schweiz / SUVA",
        "Die in Abschnitt 3 genannten CAS-Nummern wurden mit der hinterlegten SUVA-Referenzliste abgeglichen. Werte werden nur bei sicherem CAS-Match übernommen.",
    ]
    appended = 0
    for item in cas_matches:
        cas = item.get("cas_number")
        if cas and cas in existing_cas and "Arbeitsplatzgrenzwerte Schweiz / SUVA" in text:
            continue
        values = _limit_values_for_payload(item)
        lines.extend(
            [
                "",
                f"Stoff: {item.get('substance_name') or item.get('suva_substance_name') or '-'}",
                f"CAS-Nr.: {cas or '-'}",
                f"MAK-Wert: {values.get('mak') or 'beim Hersteller nicht bekannt / in SUVA-Import leer'}",
                f"Kurzzeitgrenzwert/KZGW: {values.get('kzgw') or 'beim Hersteller nicht bekannt / in SUVA-Import leer'}",
                f"BAT-Wert: {values.get('bat') or 'beim Hersteller nicht bekannt / in SUVA-Import leer'}",
                f"Bemerkungen/Notation: {item.get('notations') or item.get('remarks') or '-'}",
                f"Quelle: {item.get('source_name')} · Version/Datei: {item.get('source_version')} · SHA256: {item.get('file_hash')}",
                "Matched by: CAS · Confidence: high",
            ]
        )
        appended += 1
    if section_8_2_suggestions:
        lines.extend(["", "8.2 Schutzmassnahmen Schweiz / SUVA-Prüfung"])
        lines.extend(f"- {suggestion}" for suggestion in section_8_2_suggestions)
    if not appended:
        return text
    block = "\n".join(lines).strip()
    return f"{text}\n\n{block}".strip() if text else block


def _add_ch_import_review_suggestions(sections: dict[str, dict[str, Any]], *, product: Product | None = None) -> None:
    product_name = product.title if product else None
    sku = product.sku if product else None
    full_text = "\n".join(str(section.get("content") or "") for section in sections.values())
    ufi = _extract_ufi_from_text(full_text)

    section_1 = sections["section_1"]
    section_1_fields = section_1.setdefault("fields", {})
    section_1_content = str(section_1.get("content") or "")
    suggestions_1: list[dict[str, Any]] = []
    if not re.search(r"\b1\.1\b|Produktidentifikator|Product identifier", section_1_content, flags=re.I):
        suggestions_1.append(
            {
                "target": "section_1_1",
                "severity": "critical",
                "status": "ch_review_required",
                "message": "Abschnitt 1.1 Produktidentifikator fehlt oder ist nicht eindeutig erkannt.",
                "suggested_text": _product_identifier_suggestion(product_name, sku, ufi),
                "auto_apply": False,
                "requires_human_review": True,
            }
        )
    if ufi and not re.search(r"\bUFI\b", section_1_content, flags=re.I):
        suggestions_1.append(
            {
                "target": "section_1_1",
                "severity": "high",
                "status": "ch_review_required",
                "message": "UFI wurde im SDB erkannt, steht aber nicht in Abschnitt 1.1.",
                "suggested_text": f"UFI: {ufi}",
                "auto_apply": False,
                "requires_human_review": True,
            }
        )
    if not ufi:
        suggestions_1.append(
            {
                "target": "section_1_1",
                "severity": "high",
                "status": "ch_review_required",
                "message": "UFI fehlt oder wurde nicht erkannt.",
                "suggested_text": "UFI fachlich prüfen und in Abschnitt 1.1 ergänzen.",
                "auto_apply": False,
                "requires_human_review": True,
            }
        )
    if re.search(r"(?is)1\.2.*?(nicht verf(?:ü|ue)gbar|keine daten verf(?:ü|ue)gbar|not available|no data available)", section_1_content):
        suggestions_1.append(
            {
                "target": "section_1_2",
                "severity": "medium",
                "status": "needs_human_review",
                "message": "Abschnitt 1.2 ist leer oder zu allgemein.",
                "suggested_text": _identified_use_suggestion(product_name),
                "auto_apply": False,
                "requires_human_review": True,
            }
        )
    section_1_fields["ch_review_suggestions"] = suggestions_1

    section_2 = sections["section_2"]
    section_2_fields = section_2.setdefault("fields", {})
    section_2_fields["pictogram_review"] = _ghs_priority_review(str(section_2.get("content") or ""))

    sections["section_13"].setdefault("fields", {})["swiss_waste_code_status"] = "missing"
    sections["section_13"]["fields"]["ch_review_suggestions"] = [
        {
            "target": "section_13",
            "severity": "medium",
            "status": "needs_human_review",
            "message": "Schweizer Abfallcode/LVA-Code muss fachlich geprüft werden.",
            "suggested_text": "Entsorgung nach VVEA, VeVA/LVA und kantonalen Vorschriften über bewilligten Entsorgungsbetrieb prüfen. Keinen Abfallcode automatisch erfinden.",
            "auto_apply": False,
            "requires_human_review": True,
        }
    ]
    sections["section_14"].setdefault("fields", {})["ch_review_suggestions"] = [_transport_suggestion(str(sections["section_14"].get("content") or ""))]
    sections["section_15"].setdefault("fields", {})["ch_legal_checklist"] = _ch_legal_checklist(str(sections["section_15"].get("content") or ""))
    sections["section_16"].setdefault("fields", {})["modern_reference_suggestions"] = _modern_reference_suggestions(str(sections["section_16"].get("content") or ""))


def _product_identifier_suggestion(product_name: str | None, sku: str | None, ufi: str | None) -> str:
    lines = ["1.1 Produktidentifikator"]
    if product_name:
        lines.append(f"Produktname: {product_name}")
    if sku:
        lines.append(f"Artikelnummer: {sku}")
    lines.append(f"UFI: {ufi or 'beim Hersteller nicht bekannt / fachlich prüfen'}")
    return "\n".join(lines)


def _identified_use_suggestion(product_name: str | None) -> str:
    if re.search(r"flecken|spot|stain", product_name or "", flags=re.I):
        return (
            "Relevante identifizierte Verwendung: Vorbehandlung von Textilien zur Entfernung von Schweiss- und Urinflecken; gewerbliche Anwendung.\n"
            "Verwendungen, von denen abgeraten wird: Nicht für private Anwendung. Nicht für Lebensmittelkontakt. "
            "Nicht zum Versprühen oder Aerosolbilden verwenden, sofern keine geeigneten Schutzmassnahmen vorhanden sind."
        )
    return "Relevante identifizierte Verwendung und Verwendungen, von denen abgeraten wird, fachlich aus Herstellerdaten ergänzen."


def _extract_ufi_from_text(text: str) -> str | None:
    match = re.search(r"\bUFI\s*:?\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b", text or "", flags=re.I)
    return match.group(1).upper() if match else None


def _ghs_priority_review(section_2: str) -> dict[str, Any]:
    payload = pictogram_review_payload(section_2)
    payload["detected_pictograms"] = payload.get("original_label_pictograms", [])
    return payload


def _transport_suggestion(section_14: str) -> dict[str, Any]:
    has_un = bool(re.search(r"\bUN\s*[0-9]{4}\b|\b14\.1\b.*?\b[0-9]{4}\b", section_14, flags=re.I | re.S))
    incomplete = any(re.search(rf"(?is){marker}.*?(nicht verf(?:ü|ue)gbar|not available)", section_14) for marker in ("14.2", "14.3", "14.4"))
    return {
        "target": "section_14",
        "severity": "critical" if has_un and incomplete else "medium",
        "status": "not_compliant" if has_un and incomplete else "needs_review",
        "message": "Transportangaben unvollständig: UN-Nummer vorhanden, aber Pflichtangaben fehlen." if has_un and incomplete else "Transportklassifizierung ADR/RID/IMDG/IATA prüfen.",
        "suggested_text": (
            "Wenn kein Gefahrgut fachlich bestätigt ist:\n"
            "14.1 UN-Nummer oder ID-Nummer: Nicht anwendbar\n"
            "14.2 Ordnungsgemässe UN-Versandbezeichnung: Nicht anwendbar\n"
            "14.3 Transportgefahrenklassen: Nicht anwendbar\n"
            "14.4 Verpackungsgruppe: Nicht anwendbar\n"
            "14.5 Umweltgefahren: Nicht anwendbar\n"
            "14.6 Besondere Vorsichtsmassnahmen: Siehe Abschnitt 7 und 8\n"
            "14.7 Massengutbeförderung auf dem Seeweg gemäss IMO-Instrumenten: Nicht anwendbar"
        ),
        "auto_apply": False,
        "requires_human_review": True,
    }


def _ch_legal_checklist(section_15: str) -> dict[str, str]:
    lowered = section_15.casefold()
    checks = {
        "ChemV": "confirmed" if "chemv" in lowered or "813.11" in lowered else "needs_review",
        "ChemRRV": "confirmed" if "chemrrv" in lowered or "814.81" in lowered else "needs_review",
        "Arbeitnehmerschutz": "needs_review",
        "Jugendarbeitsschutz": "needs_review",
        "Mutterschutz": "needs_review",
        "SUVA-Grenzwerte": "confirmed" if "suva" in lowered else "needs_review",
        "VOCV": "needs_review",
        "VeVA/VVEA": "confirmed" if "veva" in lowered or "vvea" in lowered else "needs_review",
        "RPC/UFI": "needs_review",
        "Abgabebeschränkungen": "needs_review",
        "Biozid/Zulassung": "not_applicable",
    }
    return checks


def _modern_reference_suggestions(section_16: str) -> list[dict[str, Any]]:
    if not re.search(r"1999/45|2001/60|2010/453|67/548|DSD|DPD", section_16 or "", flags=re.I):
        return []
    return [
        {
            "target": "section_16",
            "severity": "critical",
            "status": "ch_review_required",
            "message": "Alte Rechtsgrundlagen dürfen im finalen CH-SDB nicht als wichtigste normative Verweisungen stehen.",
            "suggested_text": (
                "Verordnung (EG) Nr. 1907/2006 REACH, Anhang II in der Fassung der Verordnung (EU) 2020/878\n"
                "Verordnung (EG) Nr. 1272/2008 CLP\n"
                "Schweizer Chemikalienverordnung ChemV, SR 813.11\n"
                "Schweizer Chemikalien-Risikoreduktions-Verordnung ChemRRV, SR 814.81, soweit relevant\n"
                "SUVA Grenzwerte am Arbeitsplatz, aktuelle MAK-/BAT-Liste"
            ),
            "auto_apply": False,
            "requires_human_review": True,
        }
    ]


def _section_8_2_suggestions(section_8: str, cas_matches: list[dict[str, Any]], hazard_text: str = "") -> list[str]:
    text = re.sub(r"\s+", " ", section_8 or "").casefold()
    suggestions: list[str] = []
    has_limits = any(_limit_values_for_payload(item) for item in cas_matches)
    has_bat = any(item.get("bat_value") for item in cas_matches)
    has_inhalation_hazard = bool(re.search(r"\b(H334|H335)\b|Resp\.?\s*Sens\.?\s*1|STOT\s+SE\s*3", hazard_text or "", flags=re.I))
    if has_limits:
        checks = [
            ("lüftung", "Für ausreichende Lüftung bzw. technische Absaugung sorgen."),
            ("atemschutz", "Bei Überschreitung der Arbeitsplatzgrenzwerte oder unzureichender Lüftung geeigneten Atemschutz verwenden."),
            ("handschuh", "Geeignete Schutzhandschuhe tragen."),
            ("schutzbrille", "Geeignete Schutzbrille bzw. Gesichtsschutz tragen."),
            ("hygiene", "Allgemeine Hygienemassnahmen beachten; Kontakt mit Haut und Augen vermeiden."),
        ]
        for needle, suggestion in checks:
            if needle not in text:
                suggestions.append(suggestion)
    if has_bat and not re.search(r"biomonitoring|arbeitsmedizin|bat", text, flags=re.I):
        suggestions.append("Bei Stoffen mit BAT-Wert arbeitsmedizinische Überwachung bzw. biologisches Monitoring prüfen.")
    if has_inhalation_hazard:
        if "aerosol" not in text:
            suggestions.append("Aerosolbildung vermeiden; bei Tätigkeiten mit möglicher Aerosolbildung technische Absaugung prüfen.")
        if not re.search(r"subtilisin|enzym", text, flags=re.I):
            suggestions.append("Bei enzymhaltigen Bestandteilen wie Subtilisin Einatmen von Aerosolen vermeiden.")
        if re.search(r"keine besondere steuerung|no specific monitoring|nicht erforderlich", text, flags=re.I):
            suggestions.append("Pauschale Aussage zu fehlenden Steuerungsmassnahmen fachlich prüfen, da inhalative Gefahren oder Expositionsgrenzwerte vorliegen.")
    return suggestions


def _limit_values_for_payload(item: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    if item.get("mak_ppm") or item.get("mak_mg_m3"):
        values["mak"] = " / ".join(part for part in [f"{item.get('mak_ppm')} ppm" if item.get("mak_ppm") else "", f"{item.get('mak_mg_m3')} mg/m3" if item.get("mak_mg_m3") else ""] if part)
    if item.get("kzgw_ppm") or item.get("kzgw_mg_m3"):
        values["kzgw"] = " / ".join(part for part in [f"{item.get('kzgw_ppm')} ppm" if item.get("kzgw_ppm") else "", f"{item.get('kzgw_mg_m3')} mg/m3" if item.get("kzgw_mg_m3") else ""] if part)
    if item.get("bat_value") or item.get("bat_matrix"):
        values["bat"] = " / ".join(part for part in [str(item.get("bat_value") or ""), f"Matrix: {item.get('bat_matrix')}" if item.get("bat_matrix") else ""] if part)
    return values


def extract_sds_ingredients(text: str) -> list[SDSIngredient]:
    section_3 = _extract_section(text, 3)
    if not section_3:
        section_3 = str(text or "").strip()
    if not section_3:
        return []
    chunks = _ingredient_chunks(section_3)
    ingredients: list[SDSIngredient] = []
    seen: set[tuple[str | None, str | None]] = set()
    for chunk in chunks:
        cas = _first_valid_cas(chunk)
        ec_match = EC_RE.search(chunk)
        name = _extract_ingredient_name(chunk, cas)
        h_statements = ", ".join(sorted(set(H_STATEMENT_RE.findall(chunk)))) or None
        concentration = _extract_concentration(chunk)
        key = (cas, _normalize_name(name or ""))
        if key in seen or (not cas and not name):
            continue
        seen.add(key)
        ingredients.append(
            SDSIngredient(
                name=name,
                cas_number=cas,
                ec_number=ec_match.group(0) if ec_match else None,
                index_number=None,
                concentration=concentration,
                h_statements=h_statements,
                raw_text=chunk.strip(),
            )
        )
    return ingredients


def _first_valid_cas(text: str) -> str | None:
    for match in CAS_RE.finditer(text or ""):
        candidate = match.group(0)
        if _is_valid_cas(candidate):
            return candidate
    return None


def _is_valid_cas(value: str | None) -> bool:
    cleaned = (value or "").strip()
    if not CAS_RE.fullmatch(cleaned):
        return False
    digits = cleaned.replace("-", "")
    check_digit = int(digits[-1])
    body = digits[:-1]
    checksum = sum(int(digit) * multiplier for multiplier, digit in enumerate(reversed(body), start=1)) % 10
    return checksum == check_digit


def _store_upload(file_bytes: bytes, file_name: str, sha256: str) -> Path:
    SUVA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(file_name or "suva.xlsx").name).strip("-") or "suva.xlsx"
    target = SUVA_UPLOAD_DIR / f"{sha256[:12]}-{safe_name}"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(file_bytes)
    shutil.move(str(tmp), str(target))
    return target


def _parse_suva_workbook(file_bytes: bytes) -> list[dict[str, Any]]:
    workbook = pd.ExcelFile(BytesIO(file_bytes))
    parsed: list[dict[str, Any]] = []
    for sheet in workbook.sheet_names:
        frame = workbook.parse(sheet_name=sheet, dtype=str)
        if frame.empty:
            continue
        frame = frame.dropna(how="all")
        frame = _promote_embedded_suva_header(frame)
        headers = [_normalize_header(column) for column in frame.columns]
        mapping = _detect_columns(headers)
        for _, row in frame.iterrows():
            raw = {str(column): _clean_cell(value) for column, value in row.to_dict().items() if _clean_cell(value)}
            normalized_row = {"sheet": sheet, "raw": raw}
            for target, index in mapping.items():
                if index is not None and index < len(frame.columns):
                    normalized_row[target] = _clean_cell(row.iloc[index])
            _augment_suva_limit_row(normalized_row)
            parsed.append(normalized_row)
    return parsed


def _promote_embedded_suva_header(frame: pd.DataFrame) -> pd.DataFrame:
    """Handle SUVA workbooks where the real column labels are stored in the first data row."""
    for row_index in range(min(len(frame), 8)):
        values = [_normalize_header(value) for value in frame.iloc[row_index].tolist()]
        has_stoff = any(value in {"stoff", "substance"} or "stoffname" in value for value in values)
        has_cas = any(value in {"casnr", "casnummer", "cas number", "casnumber"} for value in values)
        has_limit = any("mak" in value or "kzgw" in value or "bat" in value for value in values)
        if has_stoff and has_cas and has_limit:
            promoted = frame.iloc[row_index + 1 :].copy()
            headers = []
            for index, value in enumerate(frame.iloc[row_index].tolist()):
                cleaned = _clean_cell(value)
                headers.append(cleaned or str(frame.columns[index]))
            promoted.columns = headers
            return promoted.dropna(how="all")
    return frame


def _augment_suva_limit_row(row: dict[str, Any]) -> None:
    raw = row.get("raw") or {}
    if not row.get("substance_name"):
        row["substance_name"] = raw.get("Stoff") or raw.get("Substanz") or raw.get("Identität")
    if not row.get("cas_number"):
        row["cas_number"] = raw.get("CAS-Nr.") or raw.get("CAS") or raw.get("CAS-Nummer")
    if not row.get("synonyms"):
        row["synonyms"] = raw.get("Synonyme")
    _assign_limit_with_unit(row, raw, "MAK-Wert 1", "MAK-Einheit 1", "mak")
    _assign_limit_with_unit(row, raw, "MAK-Wert 2", "MAK-Einheit 2", "mak")
    _assign_limit_with_unit(row, raw, "KZGW-Wert 1", "KZGW-Einheit 1", "kzgw")
    _assign_limit_with_unit(row, raw, "KZGW-Wert 2", "KZGW-Einheit 2", "kzgw")
    if not row.get("remarks"):
        row["remarks"] = raw.get("Bemerkung") or raw.get("Bemerkungen")
    notation_values = []
    for column in ("H", "S", "C", "M", "R", "SS", "OL", "B", "P", "Notationen"):
        value = _clean_cell(raw.get(column))
        if value:
            notation_values.append(value if column == "Notationen" else f"{column}:{value}")
    if notation_values:
        row["notations"] = "; ".join(dict.fromkeys(notation_values))


def _assign_limit_with_unit(row: dict[str, Any], raw: dict[str, str], value_key: str, unit_key: str, prefix: str) -> None:
    value = _clean_cell(raw.get(value_key))
    unit = _clean_cell(raw.get(unit_key)).casefold().replace("³", "3")
    if not value:
        return
    if "ppm" in unit:
        row[f"{prefix}_ppm"] = row.get(f"{prefix}_ppm") or value
    elif "mg/m3" in unit or "mg/m³" in unit:
        row[f"{prefix}_mg_m3"] = row.get(f"{prefix}_mg_m3") or value


def _detect_columns(headers: list[str]) -> dict[str, int | None]:
    specs = {
        "substance_name": ("stoffname", "stoff", "substance", "name", "bezeichnung"),
        "cas_number": ("cas", "casnr", "casnummer", "casnumber"),
        "ec_number": ("egnr", "einecs", "ec", "egnummer"),
        "index_number": ("index", "indexnummer"),
        "synonyms": ("synonym", "synonyme", "alias"),
        "mak_ppm": ("makppm", "mak ppm"),
        "mak_mg_m3": ("makmgm3", "mak mg/m3", "mak mgm3", "makmg/m3"),
        "kzgw_ppm": ("kzgwppm", "stelppm", "kzgw ppm"),
        "kzgw_mg_m3": ("kzgwmgm3", "stelmgm3", "kzgw mg/m3", "kzgw mgm3"),
        "bat_value": ("batwert", "bat value", "bat"),
        "bat_matrix": ("batmatrix", "matrix"),
        "notations": ("notation", "notations", "hinweise"),
        "remarks": ("bemerkung", "remarks", "anmerkung", "note"),
    }
    mapping: dict[str, int | None] = {key: None for key in specs}
    for index, header in enumerate(headers):
        compact = header.replace(" ", "")
        for target, needles in specs.items():
            if mapping[target] is not None:
                continue
            if any(needle.replace(" ", "") in compact or needle in header for needle in needles):
                mapping[target] = index
    return mapping


def _entry_from_row(source_id: int, row: dict[str, Any]) -> SuvaLimitEntry:
    return SuvaLimitEntry(
        source_id=source_id,
        substance_name=row.get("substance_name"),
        cas_number=_normalize_cas(row.get("cas_number")),
        ec_number=_clean_cell(row.get("ec_number")),
        index_number=_clean_cell(row.get("index_number")),
        synonyms=_split_synonyms(row.get("synonyms")),
        mak_ppm=_clean_cell(row.get("mak_ppm")),
        mak_mg_m3=_clean_cell(row.get("mak_mg_m3")),
        kzgw_ppm=_clean_cell(row.get("kzgw_ppm")),
        kzgw_mg_m3=_clean_cell(row.get("kzgw_mg_m3")),
        bat_value=_clean_cell(row.get("bat_value")),
        bat_matrix=_clean_cell(row.get("bat_matrix")),
        notations=_clean_cell(row.get("notations")),
        remarks=_clean_cell(row.get("remarks")),
        raw_row_json=_json_safe(row),
    )


def _aliases_for_entry(entry: SuvaLimitEntry) -> list[str]:
    aliases: set[str] = set()
    if entry.substance_name:
        aliases.add(entry.substance_name.strip())
    for alias in entry.synonyms or []:
        if str(alias).strip():
            aliases.add(str(alias).strip())
    return sorted(aliases)


def _match_ingredient(session: Session, source_id: int, ingredient: SDSIngredient) -> tuple[SuvaLimitEntry | None, str]:
    if ingredient.cas_number:
        entry = session.scalar(select(SuvaLimitEntry).where(SuvaLimitEntry.source_id == source_id, SuvaLimitEntry.cas_number == ingredient.cas_number))
        if entry:
            return entry, "exact_cas_match"
    if ingredient.ec_number:
        entry = session.scalar(select(SuvaLimitEntry).where(SuvaLimitEntry.source_id == source_id, SuvaLimitEntry.ec_number == ingredient.ec_number))
        if entry:
            return entry, "ec_match"
    normalized_name = _normalize_name(ingredient.name or "")
    if normalized_name:
        aliases = session.scalars(
            select(SuvaSubstanceAlias)
            .join(SuvaLimitEntry, SuvaLimitEntry.id == SuvaSubstanceAlias.entry_id)
            .where(SuvaLimitEntry.source_id == source_id, SuvaSubstanceAlias.status == "active")
        ).all()
        for alias in aliases:
            if _normalize_name(alias.alias) == normalized_name:
                return alias.entry, "synonym_match"
        entries = session.scalars(select(SuvaLimitEntry).where(SuvaLimitEntry.source_id == source_id)).all()
        best_entry = None
        best_score = 0.0
        for entry in entries:
            score = SequenceMatcher(None, normalized_name, _normalize_name(entry.substance_name or "")).ratio()
            if score > best_score:
                best_score = score
                best_entry = entry
        if best_entry and best_score >= 0.86:
            return best_entry, "name_match"
    return None, "no_match"


def _severity_for_match(ingredient: SDSIngredient, entry: SuvaLimitEntry | None, match_status: str) -> tuple[str, str]:
    notes: list[str] = []
    severity = "OK"
    if not ingredient.cas_number:
        severity = "WARNING"
        notes.append("CAS fehlt; eindeutiger SUVA-Abgleich ist nicht möglich.")
    if match_status in {"synonym_match", "name_match"}:
        severity = "WARNING"
        notes.append("Nur Name-/Synonym-Treffer. Mapping manuell bestätigen, bevor Werte in ein finales CH-SDS übernommen werden.")
    if match_status == "no_match":
        severity = "WARNING"
        notes.append("Kein spezifischer Schweizer Arbeitsplatzgrenzwert in importierter SUVA-Liste gefunden. Dies ist keine Entwarnung.")
    if ingredient.h_statements and HIGH_REVIEW_H_RE.search(ingredient.h_statements):
        severity = "WARNING"
        notes.append("Sensibilisierungs-/CMR-Hinweis erkannt; zusätzlicher Arbeitsschutz-Review erforderlich.")
    if entry and not _entry_has_any_limit(entry):
        notes.append("SUVA-Eintrag gefunden, aber importierte Grenzwertfelder sind leer. Keine Werte erfinden.")
    return severity, " ".join(notes).strip() or "SUVA-Abgleich dokumentiert."


def _entry_has_any_limit(entry: SuvaLimitEntry) -> bool:
    return any([entry.mak_ppm, entry.mak_mg_m3, entry.kzgw_ppm, entry.kzgw_mg_m3, entry.bat_value, entry.bat_matrix])


def _resolve_document(session: Session, product_id: int, sds_id: int | None) -> ChemicalDocument | None:
    if sds_id:
        return session.get(ChemicalDocument, int(sds_id))
    return session.scalar(
        select(ChemicalDocument)
        .where(ChemicalDocument.product_id == int(product_id), ChemicalDocument.document_type == "sds")
        .order_by(ChemicalDocument.is_current.desc(), ChemicalDocument.updated_at.desc(), ChemicalDocument.id.desc())
    )


def _document_text(document: ChemicalDocument | None) -> str:
    return ((document.generated_text or document.extracted_text) if document else "") or ""


def _extract_section(text: str, number: int) -> str:
    normalized = (text or "").replace("\r", "\n")
    pattern = re.compile(rf"(?ims)^\s*(?:ABSCHNITT|SECTION)?\s*{number}\s*[\.:]\s+.*?(?=^\s*(?:ABSCHNITT|SECTION)?\s*(?:{number + 1}|[1-9]|1[0-6])\s*[\.:]\s+|\Z)")
    match = pattern.search(normalized)
    return match.group(0).strip() if match else ""


def _ingredient_chunks(section_3: str) -> list[str]:
    lines = [line.strip() for line in section_3.splitlines() if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    for line in lines:
        starts_new = bool(CAS_RE.search(line)) or bool(re.match(r"^[A-ZÄÖÜa-zäöü0-9][A-Za-zÄÖÜäöüß0-9 (),.'+-]{3,}$", line) and current and len(" ".join(current)) > 80)
        if starts_new and current and CAS_RE.search(" ".join(current)):
            chunks.append(" ".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(" ".join(current))
    if not chunks:
        chunks = [section_3]
    # Split large chunks around repeated CAS numbers.
    expanded: list[str] = []
    for chunk in chunks:
        cas_positions = [match.start() for match in CAS_RE.finditer(chunk)]
        if len(cas_positions) <= 1:
            expanded.append(chunk)
            continue
        parts = re.split(r"(?=\b[A-ZÄÖÜa-zäöü][^.;:\n]{2,80}\s+[0-9]{2,7}-[0-9]{2}-[0-9]\b)", chunk)
        expanded.extend(part for part in parts if CAS_RE.search(part))
    return expanded


def _extract_ingredient_name(chunk: str, cas: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", chunk).strip()
    if cas and cas in cleaned:
        before = cleaned.split(cas, 1)[0]
        before = re.sub(r"\bCAS\s*$", "", before, flags=re.I).strip()
        before = re.sub(r"\s*(?:>=|<=|>|<)\s*\d+(?:[,.]\d+)?(?:\s*<\s*\d+(?:[,.]\d+)?)?\s*%?\s*$", "", before).strip()
        tokens = [token.strip(" -;:,") for token in re.split(r"\s{2,}| Index| EINECS| EG", before, flags=re.I) if token.strip(" -;:,")]
        candidate = tokens[-1] if tokens else before.strip(" -;:,")
    else:
        candidate = re.split(r"\s*(?:>=|<=|>|<)\s*\d+|\bH[0-9]{3}", cleaned, maxsplit=1)[0].strip(" -;:,")
    candidate = re.sub(r"^(Substance|Stoff|Name)\s*[:.-]\s*", "", candidate, flags=re.I)
    candidate = re.sub(r"\b(>=|<=|<|>)\s*\d+.*$", "", candidate).strip(" -;:,")
    return candidate[:500] or None


def _extract_concentration(chunk: str) -> str | None:
    match = re.search(r"(?:>=|<=|>|<)?\s*\d+(?:[,.]\d+)?\s*(?:-\s*\d+(?:[,.]\d+)?)?\s*%|\b>=\s*\d+\s*<\s*\d+\s*%", chunk)
    return match.group(0).strip() if match else None


def _limit_values_for_item(item: ProductSuvaCheckItem) -> str:
    values = []
    if item.mak_ppm:
        values.append(f"MAK {item.mak_ppm} ppm")
    if item.mak_mg_m3:
        values.append(f"MAK {item.mak_mg_m3} mg/m3")
    if item.kzgw_ppm:
        values.append(f"KZGW {item.kzgw_ppm} ppm")
    if item.kzgw_mg_m3:
        values.append(f"KZGW {item.kzgw_mg_m3} mg/m3")
    if item.bat_value:
        values.append(f"BAT {item.bat_value}")
    if item.bat_matrix:
        values.append(f"BAT-Matrix {item.bat_matrix}")
    if item.notations:
        values.append(f"Notationen: {item.notations}")
    return "; ".join(values)


def _normalize_header(value: object) -> str:
    text = _clean_cell(value).casefold()
    text = text.replace("³", "3").replace("m³", "m3")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9/ ]+", "", text).strip()


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _normalize_cas(value: object) -> str | None:
    return _first_valid_cas(_clean_cell(value))


def _clean_cell(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return re.sub(r"\s+", " ", text).strip()


def _split_synonyms(value: object) -> list[str]:
    text = _clean_cell(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[;|]", text) if part.strip()]


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _join_notes(*values: str | None) -> str | None:
    rows = [value.strip() for value in values if value and value.strip()]
    return "\n".join(rows) if rows else None


def _format_dt(value: datetime | None) -> str:
    if not value:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
