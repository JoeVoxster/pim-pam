from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import ChemicalDocument, ProductSuvaCheck, SDSReviewIssue, SuvaLimitSource
from app.services.clp_pictogram_service import pictogram_review_payload


FINAL_STATUSES = {"final", "approved", "published", "released"}
OPEN_STATUSES = {"open", "needs_review"}
CH_EMERGENCY_TEXT = "Tox Info Suisse, Zürich\nNotfallnummer: 145\nAus dem Ausland: +41 44 251 51 51"
CH_LEGAL_SOURCES_TEXT = (
    "Schweizer Rechtsvorschriften prüfen und konkret aufführen:\n"
    "- Verordnung (EG) Nr. 1907/2006 (REACH), Anhang II in der Fassung der Verordnung (EU) 2020/878\n"
    "- Verordnung (EG) Nr. 1272/2008 (CLP)\n"
    "- Chemikaliengesetz (ChemG, SR 813.1)\n"
    "- Chemikalienverordnung (ChemV, SR 813.11)\n"
    "- Chemikalien-Risikoreduktions-Verordnung (ChemRRV, SR 814.81)\n"
    "- Detergenzienverordnung (EG) Nr. 648/2004, falls Reinigungs-/Waschmittel\n"
    "- Arbeitnehmerschutz/SUVA-Grenzwerte, soweit relevant\n"
    "- Verordnung über den Verkehr mit Abfällen (VeVA), falls Entsorgung betroffen ist"
)
SECTION_4_FIRST_AID_TEXT = (
    "Nach Einatmen: Frischluft zuführen; bei Beschwerden Arzt oder Tox Info Suisse kontaktieren.\n"
    "Nach Hautkontakt: Kontaminierte Kleidung ausziehen. Haut mit viel Wasser waschen; bei Reizung Arzt konsultieren.\n"
    "Nach Augenkontakt: Einige Minuten behutsam mit Wasser spülen. Kontaktlinsen nach Möglichkeit entfernen. Weiter spülen und sofort Tox Info Suisse/Arzt kontaktieren.\n"
    "Nach Verschlucken: Mund ausspülen. Kein Erbrechen herbeiführen. Bei Beschwerden oder Unsicherheit Tox Info Suisse/Arzt kontaktieren. Bewusstlosen Personen nichts oral verabreichen."
)
SECTION_9_CORE_FIELDS = (
    "Aggregatzustand",
    "Form",
    "Aussehen",
    "Farbe",
    "Geruch",
    "pH",
    "Flammpunkt",
    "Dichte",
    "relative Dichte",
    "Löslichkeit",
    "Mischbarkeit",
    "Viskosität",
)

ASCII_UMLAUT_REPLACEMENTS = {
    "Gueltig": "Gültig",
    "gueltig": "gültig",
    "Schaeden": "Schäden",
    "schaeden": "schäden",
    "Haende": "Hände",
    "haende": "hände",
    "gefaehrlich": "gefährlich",
    "Gefaehrlich": "Gefährlich",
    "Massnahmen": "Massnahmen",
    "massnahmen": "massnahmen",
    "pruefen": "prüfen",
    "Pruefen": "Prüfen",
    "fuer": "für",
    "Fuer": "Für",
    "koennen": "können",
    "Koennen": "Können",
    "moeglich": "möglich",
    "Moeglich": "Möglich",
    "Loeslichkeit": "Löslichkeit",
    "loeslich": "löslich",
    "Entzuendung": "Entzündung",
    "Augenschaeden": "Augenschäden",
    "Schutzmassnahmen": "Schutzmassnahmen",
}


@dataclass(frozen=True)
class ReviewIssue:
    section: str
    severity: str
    issue_key: str
    current_text: str | None
    suggested_text: str | None
    reason: str
    auto_fixable: bool = False
    requires_human_review: bool = True


def review_sds_document(session: Session, document_id: int) -> dict[str, object]:
    document = _get_document(session, document_id)
    text = _document_text(document)
    sections = _extract_sections(text)
    issues = _build_review_issues(session, document, text, sections)

    session.execute(delete(SDSReviewIssue).where(SDSReviewIssue.sds_version_id == document.id))
    for issue in issues:
        session.add(
            SDSReviewIssue(
                product_id=document.product_id,
                sds_version_id=document.id,
                section=issue.section,
                severity=issue.severity,
                issue_key=issue.issue_key,
                current_text=issue.current_text,
                suggested_text=issue.suggested_text,
                reason=issue.reason,
                auto_fixable=issue.auto_fixable,
                requires_human_review=issue.requires_human_review,
                status="open",
            )
        )

    issue_date, revision = _extract_source_metadata(text)
    ufi = _extract_ufi(text)
    severity_counts = _severity_counts(issues)
    critical_count = severity_counts.get("critical", 0)
    warning_count = severity_counts.get("warning", 0)
    document.source_issue_date = issue_date
    document.source_revision = revision
    document.ufi = ufi
    document.last_ch_review_at = datetime.now(timezone.utc)
    document.transport_review_status = "critical_blocked" if any(issue.issue_key == "transport_incomplete" for issue in issues) else "reviewed"
    document.swiss_review_status = "critical_blocked" if critical_count else "needs_review" if warning_count else "reviewed"
    document.compliance_score = max(0, 100 - critical_count * 35 - warning_count * 10 - severity_counts.get("info", 0) * 2)
    if any(issue.issue_key == "waste_code_ch_needed" for issue in issues):
        document.waste_code_ch = document.waste_code_ch or ""
    session.flush()

    return {
        "document_id": document.id,
        "product_id": document.product_id,
        "swiss_review_status": document.swiss_review_status,
        "compliance_score": document.compliance_score,
        "issue_count": len(issues),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": severity_counts.get("info", 0),
        "ok_count": len(_build_ok_checks(document, text, sections)),
        "blocking_errors": critical_count > 0,
        "recommendation": "Nicht freigeben" if critical_count else "Freigabe möglich nach fachlicher Prüfung" if warning_count else "Freigabe möglich",
        "ok_checks": _build_ok_checks(document, text, sections),
        "json_report": _build_json_report(document, issues, sections),
        "markdown_report": _build_markdown_report(document, issues, sections),
        "issues": [serialize_review_issue(row) for row in list_review_issues(session, document.id)],
    }


def list_review_issues(session: Session, document_id: int) -> list[SDSReviewIssue]:
    return list(
        session.scalars(
            select(SDSReviewIssue)
            .where(SDSReviewIssue.sds_version_id == int(document_id))
            .order_by(SDSReviewIssue.severity.asc(), SDSReviewIssue.section.asc(), SDSReviewIssue.id.asc())
        )
    )


def serialize_review_issue(row: SDSReviewIssue) -> dict[str, object]:
    return {
        "id": row.id,
        "product_id": row.product_id,
        "sds_version_id": row.sds_version_id,
        "section": row.section,
        "severity": row.severity,
        "issue_key": row.issue_key,
        "current_text": row.current_text,
        "suggested_text": row.suggested_text,
        "reason": row.reason,
        "auto_fixable": bool(row.auto_fixable),
        "requires_human_review": bool(row.requires_human_review),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def apply_safe_auto_fixes(session: Session, document_id: int) -> dict[str, object]:
    document = _get_document(session, document_id)
    text = _document_text(document)
    issues = [row for row in list_review_issues(session, document.id) if row.status in OPEN_STATUSES and row.auto_fixable]
    applied: list[str] = []
    updated_text = text
    for issue in issues:
        if issue.issue_key == "duplicate_swiss_notice":
            updated_text = _dedupe_repeated_lines(updated_text)
        elif issue.issue_key == "ascii_umlaut_typography":
            updated_text = _fix_ascii_umlauts(updated_text)
        else:
            continue
        issue.status = "fixed"
        issue.resolved_at = datetime.now(timezone.utc)
        applied.append(issue.issue_key)
    if updated_text != text:
        if document.generated_text:
            document.generated_text = updated_text
        else:
            document.extracted_text = updated_text
    session.flush()
    result = review_sds_document(session, document.id)
    result["applied"] = applied
    return result


def mark_issue_status(session: Session, issue_id: int, status: str) -> dict[str, object]:
    issue = session.get(SDSReviewIssue, int(issue_id))
    if issue is None:
        raise ValueError("Review-Issue nicht gefunden.")
    normalized = str(status or "").strip().lower()
    if normalized not in {"open", "ignored", "checked", "needs_review", "fixed"}:
        raise ValueError("Ungültiger Review-Issue-Status.")
    issue.status = normalized
    if normalized in {"ignored", "checked", "fixed"}:
        issue.resolved_at = datetime.now(timezone.utc)
    session.flush()
    return serialize_review_issue(issue)


def release_document_as_final(session: Session, document_id: int) -> dict[str, object]:
    document = _get_document(session, document_id)
    result = review_sds_document(session, document.id)
    if int(result.get("critical_count") or 0) > 0:
        raise ValueError("SDB hat kritische CH-Review-Punkte. Finale Freigabe ist blockiert.")
    document.status = "approved"
    document.swiss_review_status = "final"
    document.review_note = "CH-Review ohne kritische Punkte; manuell als final freigegeben."
    session.flush()
    return {"document_id": document.id, "status": document.status, "swiss_review_status": document.swiss_review_status}


def assert_final_pdf_allowed(session: Session, document: ChemicalDocument) -> dict[str, object]:
    result = review_sds_document(session, document.id)
    if str(document.status or "").strip().lower() in FINAL_STATUSES and int(result.get("critical_count") or 0) > 0:
        raise ValueError("SDB hat kritische CH-Review-Punkte. Bitte prüfen. Finales PDF ist blockiert; nur Review-PDF ist erlaubt.")
    return result


def _get_document(session: Session, document_id: int) -> ChemicalDocument:
    document = session.scalar(select(ChemicalDocument).options(joinedload(ChemicalDocument.product)).where(ChemicalDocument.id == int(document_id)))
    if document is None:
        raise ValueError("SDB-Dokument nicht gefunden.")
    return document


def _document_text(document: ChemicalDocument) -> str:
    return (document.generated_text or document.extracted_text or "").strip()


def _build_review_issues(session: Session, document: ChemicalDocument, text: str, sections: dict[int, str]) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    section_1 = sections.get(1, "")
    section_2 = sections.get(2, "")
    section_3 = sections.get(3, "")
    section_4 = sections.get(4, "")
    section_8 = sections.get(8, "")
    section_13 = sections.get(13, "")
    section_14 = sections.get(14, "")
    section_15 = sections.get(15, "")
    section_16 = sections.get(16, "")
    lowered_text = text.casefold()

    if "review-entwurf ch" in lowered_text and str(document.status or "").strip().lower() in FINAL_STATUSES:
        issues.append(_issue("header", "critical", "review_draft_is_final", "Review-Entwurf CH", None, "Review-Entwurf darf nicht final freigegeben sein."))

    issue_date, _revision = _extract_source_metadata(text)
    if issue_date and _is_older_than_three_years(issue_date):
        issues.append(_issue("source", "warning", "source_outdated", issue_date, None, "Quelle ist älter als 3 Jahre und muss fachlich geprüft werden."))

    section_1_1 = _section_1_subsection(section_1, "1.1")
    if not section_1_1 or not re.search(r"Produktidentifikator|Product identifier|Artikel|Handels|Produktname|Product code|Trades code", section_1_1, flags=re.I):
        issues.append(
            _issue(
                "1.1",
                "critical",
                "product_identifier_missing",
                section_1[:800],
                _product_identifier_review_suggestion(document, _extract_ufi(text)),
                "Abschnitt 1.1 Produktidentifikator fehlt oder ist nicht eindeutig ausgewiesen.",
            )
        )

    if re.search(r"(?is)1\.2.*?(nicht verf(?:ü|ue)gbar|not available|no data available)", section_1):
        issues.append(
            _issue(
                "1.2",
                "critical",
                "identified_uses_missing",
                _excerpt(section_1, "1.2"),
                "Relevante identifizierte Verwendung: Vorbehandlung von Textilien zur Entfernung von Schweiss- und Urinflecken; gewerbliche Anwendung.\n"
                "Verwendungen, von denen abgeraten wird: Nicht für private Anwendung. Nicht für Lebensmittelkontakt. Nicht zum Versprühen oder Aerosolbilden verwenden, sofern keine geeigneten Schutzmassnahmen vorhanden sind.",
                "Abschnitt 1.2 ist nicht ausreichend gefüllt.",
                requires_human_review=True,
            )
        )

    ufi = _extract_ufi(text)
    if ufi and "ufi" not in section_1_1.casefold():
        issues.append(
            _issue(
                "1.1",
                "warning",
                "ufi_not_in_section_1_1",
                _excerpt(section_2, "UFI"),
                f"UFI in Abschnitt 1.1 Produktidentifikator ergänzen: {ufi or 'beim Hersteller nicht bekannt / fachlich prüfen'}",
                "UFI steht nicht korrekt in Abschnitt 1.1.",
            )
        )
    if not ufi and _hazardous_mixture_indicated(section_2):
        issues.append(
            _issue(
                "1.1",
                "critical",
                "ufi_missing",
                section_1[:800],
                "UFI fachlich prüfen und in Abschnitt 1.1 ergänzen. Falls keine UFI erforderlich ist: Begründung dokumentieren.",
                "UFI fehlt oder wurde nicht erkannt.",
            )
        )
    if ufi and str(document.rpc_status or "unknown") == "unknown":
        issues.append(_issue("1.1", "warning", "rpc_status_unknown", ufi, "RPC-Status setzen: not_reported / reported / confirmed", "UFI vorhanden; Schweizer RPC-Meldung muss geprüft werden."))

    if "tox info suisse" not in section_1.casefold() or not re.search(r"\b145\b", section_1):
        issues.append(_issue("1.4", "critical", "tox_info_suisse_missing", _excerpt(section_1, "1.4"), CH_EMERGENCY_TEXT, "Schweizer Notrufnummer fehlt."))

    if _has_un_number(section_14) and _transport_data_incomplete(section_14):
        issues.append(
            _issue(
                "14",
                "critical",
                "transport_incomplete",
                section_14[:1200],
                "Abschnitt 14.2, 14.3, 14.4 und 14.5 vollständig anhand geprüfter Transportdaten ausfüllen.",
                "UN-Nummer vorhanden, aber Versandbezeichnung/Klasse/Verpackungsgruppe/Umweltgefahren sind unvollständig.",
            )
        )
    elif not _has_un_number(section_14) and section_14 and not _has_non_dangerous_goods_statement(section_14):
        issues.append(
            _issue(
                "14",
                "critical",
                "transport_status_unclear",
                section_14[:1200],
                "Wenn kein Gefahrgut: \"Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA.\" Andernfalls vollständige Gefahrgutangaben ergänzen.",
                "Abschnitt 14 enthält weder vollständige Gefahrgutangaben noch eine klare Nicht-Gefahrgut-Aussage.",
            )
        )

    if re.search(r"(?i)abfallcode\s*:\s*(nicht verf(?:ü|ue)gbar|not available)|requires_manual_assignment", section_13):
        severity = "critical" if str(document.status or "").strip().lower() in FINAL_STATUSES else "warning"
        issues.append(
            _issue(
                "13",
                severity,
                "waste_code_ch_needed",
                _excerpt(section_13, "Abfallcode"),
                "VeVA/LVA-Abfallcode fachlich bestimmen und eintragen. Status bis zur Klärung: requires_manual_assignment / beim Hersteller nicht bekannt.",
                "Schweizer Abfallcode fehlt.",
            )
        )

    if _has_duplicate_swiss_notices(text):
        issues.append(_issue("all", "info", "duplicate_swiss_notice", None, "Doppelte Schweizer Hinweiszeilen entfernen.", "Schweizer Hinweise kommen mehrfach vor.", auto_fixable=True, requires_human_review=False))

    if _section_15_is_generic(section_15):
        issues.append(_issue("15", "critical", "swiss_law_generic", section_15[:1200], CH_LEGAL_SOURCES_TEXT, "Abschnitt 15 enthält nur generische oder fehlende Schweizer Rechtsvorschriften."))

    if re.search(r"Directive 1999/45/EC|Directive 2001/60/EC|Regulation 2010/453/EC|Regulation 1272/2008/EC|Richtlinie 1999/45|Richtlinie 2001/60|Verordnung 2010/453|DSD|DPD", section_16, flags=re.I):
        issues.append(
            _issue(
                "16",
                "critical",
                "outdated_legal_references",
                _excerpt(section_16, "Directive") or section_16[:800],
                _modern_section_16_reference_text(),
                "Veraltete Hauptreferenzen im Abschnitt 16 erkannt. Finale CH-Freigabe ist blockiert, bis sie ersetzt oder bewusst fachlich bestätigt sind.",
            )
        )

    if _contains_ascii_umlaut_words(text):
        issues.append(_issue("all", "info", "ascii_umlaut_typography", None, "ASCII-Umlaute typografisch korrigieren; Schweizer ss beibehalten, kein ß verwenden.", "Text enthält Schreibweisen wie Haende/Schaeden/pruefen.", auto_fixable=True, requires_human_review=False))

    if _pictogram_mismatch(section_2):
        issues.append(
            _issue(
                "2",
                "critical",
                "pictogram_mismatch",
                _excerpt(section_2, "GHS"),
                "Piktogramme aus strukturierten Kennzeichnungsdaten rendern. Für A15-030 prüfen: GHS05, Signalwort Gefahr, H315/H318; GHS07 nur verwenden, wenn fachlich begründet.",
                "GHS07/GHS05 widersprüchlich; Abschnitt 2.1 und 2.2 enthalten unterschiedliche Piktogramme.",
            )
        )
    priority_issue = _ghs07_priority_issue(section_2)
    if priority_issue:
        issues.append(priority_issue)
    if _isolated_pictogram_text(section_2):
        issues.append(
            _issue(
                "2",
                "info",
                "isolated_ghs_text",
                _excerpt(section_2, "GHS"),
                "Kaputte/isolierte GHS-Texte aus Freitext entfernen; Piktogramme nur aus strukturierten Daten rendern.",
                "Isolierte Piktogramm-Texte können im Layout falsch erscheinen.",
                auto_fixable=True,
                requires_human_review=False,
            )
        )

    if (
        re.search(r"(?is)Verschlucken:.*?(Nicht gef(?:ä|ae)hrlich|Not hazardous)", section_4)
        or re.search(r"(?is)(Aktivkohle|Paraffin|Physician)", section_4)
    ) and re.search(r"\b(H315|H318|P310)\b", text):
        issues.append(
            _issue(
                "4",
                "critical",
                "first_aid_ingestion_plausibility",
                _excerpt(section_4, "Verschlucken") or section_4[:800],
                SECTION_4_FIRST_AID_TEXT,
                "Erste-Hilfe-Text ist riskant oder widersprüchlich zu H315/H318/P310.",
            )
        )

    if _section_8_has_swiss_limit_contradiction(section_8):
        issues.append(
            _issue(
                "8",
                "critical",
                "swiss_mak_bat_contradiction",
                _excerpt(section_8, "Schweiz") or section_8[:1000],
                "Wenn Schweizer Werte genannt werden, die pauschale Aussage \"keine Schweizer MAK-/BAT-Werte\" entfernen und Werte als eigene SUVA/MAK/BAT-Untersektion prüfen.",
                "Abschnitt 8 enthält Schweizer Grenzwerte und gleichzeitig eine widersprüchliche Pauschalaussage.",
            )
        )
    elif _section_8_foreign_limits_without_ch_review(section_8):
        issues.append(
            _issue(
                "8",
                "critical",
                "swiss_mak_bat_not_checked",
                _excerpt(section_8, "TWA") or _excerpt(section_8, "STEL") or section_8[:1000],
                "Schweizer MAK-/BAT-/KZGW-Werte pro Stoff gegen aktuelle SUVA-Liste prüfen. Status: requires_manual_check / beim Hersteller nicht bekannt, falls keine Quelle vorliegt.",
                "Ausländische Expositionsgrenzwerte vorhanden, aber CH/SUVA-Prüfung ist nicht dokumentiert.",
            )
        )
    elif section_8 and not re.search(r"\b(MAK|BAT|SUVA|Grenzwert|Arbeitsplatzgrenzwert|STEL|TWA)\b", section_8, flags=re.I):
        issues.append(_issue("8", "warning", "swiss_mak_bat_manual_check", section_8[:800], "Für die enthaltenen Stoffe wurden in den vorliegenden Quelldaten keine zusätzlichen Schweizer MAK-/BAT-Werte identifiziert. Manuelle Prüfung gegen die aktuelle SUVA-Grenzwertliste empfohlen.", "CH-Arbeitsplatzgrenzwerte sind nicht konkret erkennbar."))

    issues.extend(_suva_review_issues(session, document, section_3, section_8))

    section_9_issues = _section_9_issues(section_9=sections.get(9, ""))
    issues.extend(section_9_issues)

    if _euh208_substance_missing(section_2, section_3):
        issues.append(
            _issue(
                "3",
                "critical",
                "euh208_substance_missing",
                _excerpt(section_2, "EUH208") or section_2[:800],
                "EUH208-auslösende Stoffe in Abschnitt 3 oder strukturiertem Allergene-/Konserviererblock dokumentieren, inkl. Konzentrationsbereich/Klassifizierung/SCL/ATE/M-Faktoren falls relevant. Status: requires_source_data / beim Hersteller nicht bekannt.",
                "EUH208 vorhanden, aber auslösender Stoff ist in Abschnitt 3 nicht nachvollziehbar dokumentiert.",
            )
        )

    if _environmental_classification_missing(section_3, sections.get(12, "")):
        issues.append(
            _issue(
                "12",
                "critical",
                "environmental_classification_missing",
                sections.get(12, "")[:1200],
                "Umweltklassifizierung der Mischung berechnen oder plausibel begründen. Status: requires_calculation; H400-/Aquatic-Stoffe mit Konzentration und M-Faktor prüfen.",
                "Aquatisch gefährliche Stoffe erkannt, aber Abschnitt 12 enthält pauschale Entwarnung ohne Berechnung/Begründung.",
            )
        )

    return issues


def _issue(section: str, severity: str, issue_key: str, current_text: str | None, suggested_text: str | None, reason: str, *, auto_fixable: bool = False, requires_human_review: bool = True) -> ReviewIssue:
    return ReviewIssue(section=section, severity=severity, issue_key=issue_key, current_text=current_text, suggested_text=suggested_text, reason=reason, auto_fixable=auto_fixable, requires_human_review=requires_human_review)


def _extract_sections(text: str) -> dict[int, str]:
    normalized = (text or "").replace("\r", "\n")
    matches = list(
        re.finditer(
            r"(?im)^\s*(?:(?:ABSCHNITT|SECTION)\s*)?(1[0-6]|[1-9])\s*[\.:](?!\d)\s*(.+?)\s*$",
            normalized,
        )
    )
    if not matches:
        return {1: normalized}
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        sections[number] = normalized[match.start():end].strip()
    return sections


def _extract_source_metadata(text: str) -> tuple[str | None, str | None]:
    issue_date = None
    revision = None
    match = re.search(r"Issued on\s+([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})\s*-\s*Rel\.\s*#?\s*([0-9A-Za-z.-]+)", text or "", flags=re.I)
    if match:
        issue_date = _normalize_us_date(match.group(1))
        revision = f"Rel. # {match.group(2)}"
    return issue_date, revision


def _normalize_us_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _is_older_than_three_years(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    today = datetime.now(timezone.utc).date()
    return (today - parsed).days > 365 * 3


def _extract_ufi(text: str) -> str | None:
    match = re.search(r"\bUFI\s*:?\s*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})\b", text or "", flags=re.I)
    return match.group(1).upper() if match else None


def _section_1_subsection(section_1: str, marker: str) -> str:
    if not section_1:
        return ""
    escaped = re.escape(marker)
    match = re.search(rf"(?is)\b{escaped}\.?.*?(?=\n\s*1\.[2-4]\b|\Z)", section_1)
    return match.group(0).strip() if match else ""


def _product_identifier_review_suggestion(document: ChemicalDocument, ufi: str | None) -> str:
    product = document.product
    lines = ["1.1 Produktidentifikator"]
    if product and product.title:
        lines.append(f"Produktname: {product.title}")
    if product and product.sku:
        lines.append(f"Artikelnummer: {product.sku}")
    lines.append(f"UFI: {ufi or 'beim Hersteller nicht bekannt / fachlich prüfen'}")
    return "\n".join(lines)


def _section_2_contains_ufi(section_2: str) -> bool:
    return bool(re.search(r"\bUFI\b", section_2 or "", flags=re.I))


def _hazardous_mixture_indicated(section_2: str) -> bool:
    return bool(re.search(r"\bH[0-9]{3}[A-Z]?\b|GHS0[0-9]|Eye\s+Dam|Skin\s+Irrit|Acute\s+Tox", section_2 or "", flags=re.I))


def _has_un_number(text: str) -> bool:
    return bool(re.search(r"\bUN\s*[0-9]{4}\b|\b14\.1\b.*?\b[0-9]{4}\b", text or "", flags=re.I | re.S))


def _transport_data_incomplete(text: str) -> bool:
    for marker in ("14.2", "14.3", "14.4", "14.5"):
        match = re.search(rf"(?is){re.escape(marker)}.*?(?=\n\s*14\.[2-7]|\Z)", text or "")
        if not match:
            return True
        if re.search(r"nicht verf(?:ü|ue)gbar|not available|none|keine\b", match.group(0), flags=re.I):
            return True
    return False


def _has_non_dangerous_goods_statement(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").casefold()
    return (
        "kein gefahrgut im sinne von adr/rid, imdg und iata" in normalized
        or "not classified as dangerous goods according to adr/rid, imdg and iata" in normalized
        or "nicht im anwendungsbereich der vorschriften für den transport gefährlicher güter" in normalized
        or "nicht im anwendungsbereich der vorschriften fuer den transport gefaehrlicher gueter" in normalized
    )


def _has_duplicate_swiss_notices(text: str) -> bool:
    lowered_lines = [re.sub(r"\s+", " ", line.strip().casefold()) for line in (text or "").splitlines() if line.strip()]
    tracked = [line for line in lowered_lines if any(token in line for token in ("tox info suisse", "chemv", "chemrrv", "veva", "suva"))]
    return len(tracked) != len(set(tracked))


def _section_15_is_generic(text: str) -> bool:
    if not text:
        return True
    lowered = text.casefold()
    has_specific = any(token.casefold() in lowered for token in ("ChemG", "ChemV", "ChemRRV", "SR 813.11", "SR 814.81", "VeVA", "SUVA"))
    return "schweiz" in lowered and not has_specific


def _modern_section_16_reference_text() -> str:
    return (
        "Aktuelle Hauptreferenzen für die CH-Review-Ausgabe prüfen/verwenden:\n"
        "- Verordnung (EG) Nr. 1907/2006 (REACH), Anhang II in der Fassung der Verordnung (EU) 2020/878\n"
        "- Verordnung (EG) Nr. 1272/2008 (CLP)\n"
        "- Schweizer Chemikaliengesetz (ChemG)\n"
        "- Schweizer Chemikalienverordnung (ChemV)\n"
        "- Schweizer Chemikalien-Risikoreduktions-Verordnung (ChemRRV), soweit anwendbar\n"
        "- Detergenzienverordnung (EG) Nr. 648/2004, falls Reinigungs-/Waschmittel\n"
        "- Abfallrechtliche Vorschriften, falls Abschnitt 13/15 betroffen"
    )


def _section_8_has_swiss_limit_contradiction(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").casefold()
    has_swiss_value = bool(re.search(r"\b(schweiz|switzerland|suisse|suva|mak|bat|stel|twa)\b.*?\d", normalized, flags=re.I))
    denies_swiss_values = bool(
        re.search(
            r"keine\s+(?:zus[aä]tzlichen\s+)?(?:schweizer\s+)?mak-/bat|keine\s+(?:zus[aä]tzlichen\s+)?schweizer\s+grenzwerte|no\s+additional\s+swiss",
            normalized,
            flags=re.I,
        )
    )
    return has_swiss_value and denies_swiss_values


def _section_8_foreign_limits_without_ch_review(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").casefold()
    has_foreign_limits = bool(re.search(r"\b(twa|stel|wel|dfg|acgih|ppm|mg/m3|mg/m³)\b", normalized))
    has_ch_review = bool(re.search(r"\b(suva|schweiz|switzerland|mak|bat|kzgw)\b", normalized))
    return has_foreign_limits and not has_ch_review


def _suva_review_issues(session: Session, document: ChemicalDocument, section_3: str, section_8: str) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    source = session.scalar(select(SuvaLimitSource).order_by(SuvaLimitSource.imported_at.desc(), SuvaLimitSource.id.desc()))
    has_dangerous_ingredients = _section_3_has_dangerous_ingredients(section_3)
    has_foreign_limits = bool(re.search(r"\b(twa|stel|wel|dfg|acgih|ppm|mg/m3|mg/m³)\b", section_8 or "", flags=re.I))
    if not source:
        issues.append(
            _issue(
                "8.1",
                "critical",
                "suva_import_missing",
                None,
                "SUVA-Liste \"Grenzwerte am Arbeitsplatz\" importieren und Produkt-Inhaltsstoffe gegen diese Version prüfen.",
                "Kein SUVA-Grenzwertimport vorhanden. Finale CH-SDB-Freigabe ist blockiert.",
            )
        )
        return issues

    if _source_older_than_months(source.imported_at, 12):
        issues.append(
            _issue(
                "8.1",
                "warning",
                "suva_source_outdated",
                f"Import: {source.imported_at.isoformat() if source.imported_at else '-'} · {source.file_name}",
                "Aktuelle SUVA-Liste importieren oder fachlich bestätigen, dass diese Version weiterhin verwendet werden darf.",
                "SUVA-Import ist älter als 12 Monate.",
            )
        )

    check = session.scalar(
        select(ProductSuvaCheck)
        .where(ProductSuvaCheck.product_id == document.product_id, ProductSuvaCheck.sds_id == document.id)
        .order_by(ProductSuvaCheck.checked_at.desc(), ProductSuvaCheck.id.desc())
    )
    if not check:
        if has_dangerous_ingredients or has_foreign_limits:
            issues.append(
                _issue(
                    "8.1",
                    "critical",
                    "suva_check_missing",
                    section_3[:1000] or section_8[:1000],
                    "Button \"SUVA-Prüfung starten\" ausführen und Resultat dokumentieren. Kein Treffer bedeutet nicht ungefährlich.",
                    "Gefährliche Inhaltsstoffe oder ausländische Arbeitsplatzgrenzwerte vorhanden, aber keine SUVA-Prüfung dokumentiert.",
                )
            )
        return issues

    if _check_is_stale(check, document):
        issues.append(
            _issue(
                "8.1",
                "critical",
                "suva_check_stale",
                f"SUVA-Check: {check.checked_at.isoformat() if check.checked_at else '-'}",
                "SUVA-Prüfung nach der letzten SDB-/Produktänderung erneut ausführen.",
                "SUVA-Check ist älter als die letzte Produkt-/SDB-Änderung.",
            )
        )

    if check.source_id != source.id:
        issues.append(
            _issue(
                "8.1",
                "warning",
                "suva_check_not_latest_source",
                f"Check-Quelle: {check.source_id}; aktuelle Quelle: {source.id}",
                "SUVA-Prüfung mit der neuesten importierten SUVA-Version wiederholen oder alte Version bewusst bestätigen.",
                "SUVA-Check wurde nicht mit der neuesten importierten SUVA-Version durchgeführt.",
            )
        )

    if str(check.overall_status or "").upper() == "BLOCKER":
        issues.append(
            _issue(
                "8.1",
                "critical",
                "suva_check_blocker",
                str(check.report_json or "")[:1000],
                "Blockierende SUVA-Prüfpunkte beheben oder fachlich dokumentieren.",
                "Der dokumentierte SUVA-Check enthält blockierende Punkte.",
            )
        )
    elif str(check.overall_status or "").upper() == "WARNING":
        issues.append(
            _issue(
                "8.1",
                "warning",
                "suva_check_warning",
                str(check.report_json or "")[:1000],
                "Warnungen im SUVA-Check fachlich prüfen; Name-/Synonym-Matches bestätigen.",
                "Der dokumentierte SUVA-Check enthält Warnungen.",
            )
        )
    return issues


def _section_3_has_dangerous_ingredients(section_3: str) -> bool:
    return bool(
        re.search(
            r"\bH[0-9]{3}[A-Z]?\b|Skin Irrit|Eye Dam|Eye Irrit|Acute Tox|Resp\.?\s*Sens|STOT|Aquatic|Carc|Muta|Repr",
            section_3 or "",
            flags=re.I,
        )
    )


def _source_older_than_months(value: datetime | None, months: int) -> bool:
    if not value:
        return False
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now - value).days > months * 31


def _check_is_stale(check: ProductSuvaCheck, document: ChemicalDocument) -> bool:
    checked_at = check.checked_at
    if not checked_at:
        return True
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    candidates = [document.updated_at]
    if document.product and document.product.updated_at:
        candidates.append(document.product.updated_at)
    for value in candidates:
        if not value:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if checked_at < value:
            return True
    return False


def _section_9_issues(section_9: str) -> list[ReviewIssue]:
    if not section_9:
        return [_issue("9", "critical", "section_9_missing", None, "Abschnitt 9 aus Herstellerdatenblatt oder Messdaten ergänzen.", "Abschnitt 9 fehlt vollständig.")]
    unavailable_count = len(re.findall(r"nicht verf(?:ü|ue)gbar|keine daten verf(?:ü|ue)gbar|nicht bestimmt|not available|no data available|not determined", section_9, flags=re.I))
    missing_aggregate = not re.search(r"(Aggregatzustand|Form|Aussehen|Physical state|Appearance)\s*[:\n-]?\s*(fl[üu]ssig|liquid|fest|solid|gas)", section_9, flags=re.I)
    issues: list[ReviewIssue] = []
    if unavailable_count > 5:
        issues.append(
            _issue(
                "9",
                "critical",
                "section_9_many_missing_values",
                section_9[:1200],
                _section_9_todo_text(),
                "Abschnitt 9 enthält zu viele fehlende physikalisch-chemische Angaben.",
            )
        )
    if missing_aggregate:
        issues.append(
            _issue(
                "9",
                "critical",
                "section_9_missing_physical_state",
                section_9[:800],
                "Aggregatzustand/Form fachlich prüfen und eintragen, z.B. nur wenn durch Quelle belegt: flüssig.",
                "Aggregatzustand/Form fehlt oder ist nicht eindeutig belegt.",
            )
        )
    return issues


def _section_9_todo_text() -> str:
    return (
        "Zentrale physikalisch-chemische Daten als strukturierte Prüffelder erfassen:\n"
        "- Aggregatzustand/Form: status=requires_source_data\n"
        "- Farbe: status=requires_source_data\n"
        "- Geruch: status=requires_source_data\n"
        "- pH-Wert: status=requires_lab_value oder requires_source_data\n"
        "- Dichte/relative Dichte: status=requires_lab_value oder requires_source_data\n"
        "- Wasserlöslichkeit/Mischbarkeit: status=requires_source_data\n"
        "- Flammpunkt: value oder not_applicable_with_reason\n"
        "- Viskosität: value oder not_applicable_with_reason\n"
        "Fehlgrund im UI: beim Hersteller nicht bekannt / Laborwert erforderlich / Quelle nachtragen."
    )


def _euh208_substance_missing(section_2: str, section_3: str) -> bool:
    if "EUH208" not in section_2:
        return False
    haystack = section_3.casefold()
    known_triggers = (
        "5-chloro-2-methyl",
        "5-chlor-2-methyl",
        "methylisothiazol",
        "methylchloroisothiazolinone",
        "methylisothiazolinone",
        "cm it",
        "cmit",
        "mit",
    )
    return not any(trigger in haystack for trigger in known_triggers)


def _environmental_classification_missing(section_3: str, section_12: str) -> bool:
    section_3_norm = section_3.casefold()
    has_aquatic_hazard = bool(re.search(r"aquatic\s+(?:acute|chronic)|\bH400\b|\bH410\b|\bH411\b|\bH412\b", section_3_norm, flags=re.I))
    if not has_aquatic_hazard:
        return False
    section_12_norm = re.sub(r"\s+", " ", section_12 or "").casefold()
    has_bad_blanket_statement = any(
        phrase in section_12_norm
        for phrase in (
            "keine schädlichen wirkungen",
            "keine schaedlichen wirkungen",
            "no adverse effects",
            "keine umweltgefahren",
        )
    )
    has_calculation = any(token in section_12_norm for token in ("berechnung", "calculation", "m-faktor", "m-factor", "einstufung der mischung", "mixture classification"))
    return has_bad_blanket_statement and not has_calculation


def _isolated_pictogram_text(section_2: str) -> bool:
    for line in (section_2 or "").splitlines():
        cleaned = line.strip()
        if cleaned in {"GHS05", "GHS07", "GHS09"}:
            return True
    return False


def _contains_ascii_umlaut_words(text: str) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text or "") for word in ASCII_UMLAUT_REPLACEMENTS)


def _fix_ascii_umlauts(text: str) -> str:
    fixed = str(text or "")
    for source, target in ASCII_UMLAUT_REPLACEMENTS.items():
        fixed = re.sub(rf"\b{re.escape(source)}\b", target, fixed)
    fixed = fixed.replace("ß", "ss")
    return fixed


def _dedupe_repeated_lines(text: str) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for line in str(text or "").splitlines():
        key = re.sub(r"\s+", " ", line.strip().casefold())
        if key and key in seen and any(token in key for token in ("tox info suisse", "chemv", "chemrrv", "veva", "suva")):
            continue
        if key:
            seen.add(key)
        result.append(line)
    return "\n".join(result)


def _pictogram_mismatch(section_2: str) -> bool:
    if not section_2:
        return False
    before_label = re.split(r"(?im)^\s*2\.2\b", section_2, maxsplit=1)[0]
    all_codes = set(re.findall(r"\bGHS0[0-9]\b", section_2.upper()))
    top_codes = set(re.findall(r"\bGHS0[0-9]\b", before_label.upper()))
    label_match = re.search(r"(?is)(?:2\.2|Kennzeichnung).*", section_2)
    label_codes = set(re.findall(r"\bGHS0[0-9]\b", label_match.group(0).upper())) if label_match else set()
    return bool(top_codes and label_codes and top_codes != label_codes) or (len(all_codes) > 1 and top_codes and top_codes != all_codes)


def _ghs07_priority_issue(section_2: str) -> ReviewIssue | None:
    payload = pictogram_review_payload(section_2 or "")
    suppressed = payload.get("suppressed_pictograms") or []
    if any(isinstance(item, dict) and item.get("code") == "GHS07" for item in suppressed):
        return _issue(
            "2",
            "critical",
            "ghs07_priority_review",
            _excerpt(section_2, "GHS07") or section_2[:1000],
            str(payload.get("message") or "GHS07 gemäss CLP-Prioritätsregel unterdrücken, sofern keine andere Produktgefahr GHS07 erfordert."),
            "GHS07 ist angezeigt, obwohl es nach berechneter Produktklassifizierung durch GHS05 verdrängt wird.",
        )
    return None


def _excerpt(text: str, marker: str) -> str | None:
    if not text:
        return None
    match = re.search(rf"(?is).{{0,120}}{re.escape(marker)}.{{0,600}}", text)
    return re.sub(r"\s+", " ", match.group(0)).strip() if match else None


def _severity_counts(issues: list[ReviewIssue]) -> dict[str, int]:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    return counts


def _overall_status(issues: list[ReviewIssue]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities:
        return "BLOCKER"
    if "warning" in severities:
        return "WARNING"
    return "OK"


def _build_json_report(document: ChemicalDocument, issues: list[ReviewIssue], sections: dict[int, str]) -> dict[str, object]:
    product = document.product
    return {
        "product_id": document.product_id,
        "product_code": product.sku if product else None,
        "product_name": product.title if product else None,
        "sds_version_id": document.id,
        "sds_status": document.swiss_review_status or document.status,
        "market": document.region_code or "CH",
        "language": document.locale or document.language_code,
        "overall_status": _overall_status(issues),
        "recommendation": "Nicht freigeben" if _overall_status(issues) == "BLOCKER" else "Freigabe möglich nach fachlicher Prüfung" if issues else "Freigabe möglich",
        "ok_checks": _build_ok_checks(document, _document_text(document), sections),
        "findings": [
            {
                "section": issue.section,
                "severity": "BLOCKER" if issue.severity == "critical" else issue.severity.upper(),
                "code": issue.issue_key.upper(),
                "message": issue.reason,
                "current_text": issue.current_text,
                "suggested_fix": issue.suggested_text,
                "status": "requires_human_review" if issue.requires_human_review else "auto_fixable",
            }
            for issue in issues
        ],
    }


def _build_markdown_report(document: ChemicalDocument, issues: list[ReviewIssue], sections: dict[int, str]) -> str:
    report = _build_json_report(document, issues, sections)
    lines = [
        f"# SDB-Review CH: {report.get('product_code') or '-'} / {report.get('product_name') or '-'}",
        "",
        f"Status: {report['overall_status']}",
        f"Empfehlung: {report['recommendation']}",
        "",
    ]
    for severity in ("BLOCKER", "WARNING", "INFO"):
        rows = [item for item in report["findings"] if item["severity"] == severity]
        if not rows:
            continue
        lines.append(f"## {severity}")
        for row in rows:
            lines.append(f"- Abschnitt {row['section']}: {row['message']}")
        lines.append("")
    if report["ok_checks"]:
        lines.append("## OK")
        for row in report["ok_checks"]:
            lines.append(f"- Abschnitt {row['section']}: {row['message']}")
    return "\n".join(lines).strip()


def _build_ok_checks(document: ChemicalDocument, text: str, sections: dict[int, str]) -> list[dict[str, str]]:
    product = document.product
    ok: list[dict[str, str]] = []
    section_1 = sections.get(1, "")
    section_2 = sections.get(2, "")
    section_13 = sections.get(13, "")
    section_16_count = sum(1 for index in range(1, 17) if sections.get(index))
    if "VOXSTER GmbH" in section_1 and "Obere Ifangstrasse 10" in section_1:
        ok.append({"section": "1.3", "key": "supplier_ch_present", "message": "Schweizer Lieferant VOXSTER GmbH mit Adresse vorhanden."})
    if "Tox Info Suisse" in section_1 and re.search(r"\b145\b", section_1):
        ok.append({"section": "1.4", "key": "tox_info_suisse_present", "message": "Tox Info Suisse 145 vorhanden."})
    if _extract_ufi(text):
        ok.append({"section": "1.1", "key": "ufi_present", "message": "UFI vorhanden."})
    if section_16_count == 16:
        ok.append({"section": "all", "key": "sixteen_sections_present", "message": "Alle 16 SDB-Abschnitte vorhanden."})
    if re.search(r"\bH\d{3}\b|\bP\d{3}", section_2):
        ok.append({"section": "2", "key": "clp_statements_present", "message": "CLP-/H-/P-Sätze sind enthalten."})
    if re.search(r"648/2004|Detergenzien|Tenside|surfactants", text, flags=re.I):
        ok.append({"section": "2/15/16", "key": "detergent_reference_present", "message": "Angaben nach Detergenzienverordnung oder Tensid-Angaben erkannt."})
    if re.search(r"VeVA|LVA|Abfallrecht|Entsorgung|Abfallcode", section_13, flags=re.I):
        ok.append({"section": "13", "key": "disposal_reference_present", "message": "Entsorgungshinweise vorhanden; Detailprüfung bleibt erforderlich."})
    if product and product.title:
        ok.append({"section": "metadata", "key": "product_context_present", "message": f"Produktkontext vorhanden: {product.sku or '-'} / {product.title}"})
    return ok
