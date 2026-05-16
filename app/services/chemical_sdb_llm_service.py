from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Product, ProductSDB, ProductSDBLLMRun
from app.services.sdb_support import SDB_SECTION_TITLES, merge_sdb_sections, sync_sdb_fields_from_content
from app.services.pim_service import upsert_product_sdb
from app.schemas.pim import ProductSDBUpdate


LOGGER = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_REVIEW_STATUS = "review_required"
DEFAULT_VERSION_LABEL = "Entwurf 1.0"
QUALITY_MODES = {
    "standard": {"label": "Standard", "reasoning_effort": "medium", "focused_pass": False},
    "thorough": {"label": "Gründlich", "reasoning_effort": "high", "focused_pass": True},
    "xhigh": {"label": "Sehr gründlich", "reasoning_effort": "xhigh", "focused_pass": True},
}
DEFAULT_ISSUER = {
    "issuer_name": "VOXSTER GmbH",
    "issuer_address_line1": "Obere Ifangstrasse 10",
    "issuer_address_line2": None,
    "issuer_postal_code": "8215",
    "issuer_city": "Hallau",
    "issuer_country_code": "CH",
    "issuer_phone": "+41 52 502 67 23",
    "issuer_email": "info@voxster.ch",
}
DEFAULT_CH_EMERGENCY_BLOCK = (
    "1.4 Notrufnummer\n"
    "Schweiz:\n"
    "Tox Info Suisse, Zürich\n"
    "Notfallnummer: 145\n"
    "Aus dem Ausland: +41 44 251 51 51"
)

def get_sdb_llm_config_status() -> dict[str, object]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    model = os.getenv("OPENAI_SDB_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    quality_mode = _normalize_quality_mode(os.getenv("OPENAI_SDB_QUALITY_MODE") or "thorough")
    reasoning_effort = _normalize_reasoning_effort(os.getenv("OPENAI_SDB_REASONING_EFFORT") or str(QUALITY_MODES[quality_mode]["reasoning_effort"]))
    return {
        "enabled": bool(api_key),
        "model": model,
        "quality_mode": quality_mode,
        "quality_label": QUALITY_MODES[quality_mode]["label"],
        "reasoning_effort": reasoning_effort,
        "focused_pass": bool(QUALITY_MODES[quality_mode]["focused_pass"]),
        "base_url": (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
    }


def run_product_sdb_llm_normalization(session: Session, product_id: int, *, quality_mode: str | None = None) -> dict[str, object]:
    product = session.scalar(
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.sdb_record).joinedload(ProductSDB.llm_runs))
        .where(Product.id == product_id)
    )
    if product is None:
        raise ValueError("Chemieprodukt nicht gefunden")

    record = product.sdb_record
    if record is None:
        record = upsert_product_sdb(
            session,
            product.id,
            ProductSDBUpdate(
                review_status=DEFAULT_REVIEW_STATUS,
                version_label=DEFAULT_VERSION_LABEL,
                **DEFAULT_ISSUER,
            ),
        )

    system_prompt = build_sdb_normalization_system_prompt()
    user_prompt = build_sdb_normalization_user_prompt(product, record)
    config_status = get_sdb_llm_config_status()
    if quality_mode:
        selected_quality_mode = _normalize_quality_mode(quality_mode)
        config_status["quality_mode"] = selected_quality_mode
        config_status["quality_label"] = QUALITY_MODES[selected_quality_mode]["label"]
        config_status["reasoning_effort"] = _normalize_reasoning_effort(str(QUALITY_MODES[selected_quality_mode]["reasoning_effort"]))
        config_status["focused_pass"] = bool(QUALITY_MODES[selected_quality_mode]["focused_pass"])
    model = str(config_status["model"])
    reasoning_effort = str(config_status["reasoning_effort"])

    llm_run = ProductSDBLLMRun(
        product_sdb_id=record.id,
        provider=DEFAULT_PROVIDER,
        model=model,
        status="pending",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    session.add(llm_run)

    if not config_status["enabled"]:
        llm_run.status = "missing_api_key"
        llm_run.error_log = "OPENAI_API_KEY ist nicht konfiguriert."
        record.review_status = record.review_status or DEFAULT_REVIEW_STATUS
        record.version_label = record.version_label or DEFAULT_VERSION_LABEL
        _apply_default_issuer(record)
        session.flush()
        return {
            "status": llm_run.status,
            "run_id": llm_run.id,
            "message": llm_run.error_log,
            "model": model,
        }

    try:
        response = _call_openai_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
            reasoning_effort=reasoning_effort,
        )
        llm_run.raw_response_text = response["raw_text"]
        llm_run.response_json = response["json"]
        normalized = _normalize_llm_response(response["json"], record, product=product)
        focused_sections_applied = False
        if bool(config_status.get("focused_pass")):
            focused_prompt = build_sdb_critical_sections_user_prompt(product, record, normalized["sections_json"])
            focused_response = _call_openai_json(
                system_prompt=build_sdb_critical_sections_system_prompt(),
                user_prompt=focused_prompt,
                model=model,
                api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
                reasoning_effort=reasoning_effort,
            )
            focused_payload = _merge_focused_sections_payload(response["json"], focused_response["json"], normalized["sections_json"])
            normalized = _normalize_llm_response(focused_payload, record, product=product)
            llm_run.raw_response_text = f"{response['raw_text']}\n\n--- focused_sections_13_16 ---\n{focused_response['raw_text']}"
            llm_run.response_json = focused_payload
            focused_sections_applied = True
        llm_run.warnings_json = normalized["warnings"]
        llm_run.status = "completed"

        record.sections_json = normalized["sections_json"]
        record.review_status = normalized["review_status"]
        record.version_label = normalized["version_label"]
        record.effective_date = normalized["effective_date"]
        record.parser_status = "llm_normalized"
        _apply_default_issuer(record)
        if normalized["issuer"].get("issuer_address_line2") is not None:
            record.issuer_address_line2 = normalized["issuer"]["issuer_address_line2"]
        record.issuer_phone = normalized["issuer"]["issuer_phone"]
        record.issuer_email = normalized["issuer"]["issuer_email"]

        session.flush()
        return {
            "status": llm_run.status,
            "run_id": llm_run.id,
            "message": "SDB mit LLM normiert. Fachliche Prüfung weiterhin erforderlich.",
            "model": model,
            "quality_mode": config_status.get("quality_mode"),
            "reasoning_effort": reasoning_effort,
            "focused_sections_applied": focused_sections_applied,
            "warnings": normalized["warnings"],
        }
    except Exception as exc:
        LOGGER.exception("SDB LLM normalization failed for product %s", product_id)
        llm_run.status = "failed"
        llm_run.error_log = str(exc)
        record.review_status = record.review_status or DEFAULT_REVIEW_STATUS
        _apply_default_issuer(record)
        session.flush()
        return {
            "status": llm_run.status,
            "run_id": llm_run.id,
            "message": f"SDB-Normalisierung fehlgeschlagen: {exc}",
            "model": model,
        }


def build_sdb_normalization_system_prompt() -> str:
    return (
        "Du normierst Sicherheitsdatenblatt-Inhalte fuer ein Schweizer B2B-Chemieprodukt.\n"
        "Regeln:\n"
        "- Erfinde niemals regulatorische Fakten.\n"
        "- Verwende ausschliesslich Informationen aus dem uebergebenen Rohtext und den vorhandenen Produktdaten.\n"
        "- Wenn Informationen fehlen oder unsicher sind, lasse das Feld leer oder uebernimm den vorhandenen Wert.\n"
        "- Schreibe in klarem Schweizer Hochdeutsch; verwende kein scharfes S.\n"
        "- Strukturiere die Inhalte sauber in die Abschnitte 1 bis 16.\n"
        "- Entferne offensichtliche OCR-/PDF-Artefakte, aber veraendere den Sinn nicht.\n"
        "- Gib einen Review-Entwurf fuer die Schweiz zurueck, nicht eine finale rechtliche Freigabe.\n"
        "- Uebernimm den Absender mit diesen Daten: VOXSTER GmbH, Obere Ifangstrasse 10, 8215 Hallau, CH, Telefon +41 52 502 67 23, E-Mail info@voxster.ch.\n"
        "- Gib als section title nur den reinen Abschnittstitel zurueck, ohne Praefix wie 'ABSCHNITT 1:' oder '1.'.\n"
        "- Entferne Seitenzahlen, Kopf-/Fusszeilen und Druckvermerke aus den Abschnittsinhalten.\n"
        "- Formatiere Inhalte lesbar mit kurzen Absaetzen und Listen, nicht als Rohdump des PDFs.\n"
        "- Verbessere besonders Abschnitt 13 und 14 strukturell: keine rohen Floskeln, keine halbfertigen Tabellen, keine isolierten 'nicht verfügbar'-Zeilen ohne Prüfhintergrund.\n"
        "- Abschnitt 13 Schweiz: formuliere konkrete CH-Entsorgungshinweise zu Produktresten, verunreinigten Verpackungen, restentleerten Verpackungen, VeVA/VVEA/LVA/kantonalen Vorschriften und bewilligtem Entsorgungsbetrieb. Erfinde keinen Abfallcode; wenn er fehlt, schreibe klar 'Schweizer Abfallcode/LVA-Code fachlich prüfen'.\n"
        "- Abschnitt 14 Transport: wenn die Quelle klar kein Gefahrgut nennt, formuliere 14.1 bis 14.7 mit 'Nicht anwendbar' und 'Kein Gefahrgut im Sinne von ADR/RID, IMDG und IATA'. Wenn eine UN-Nummer vorhanden ist, aber Versandbezeichnung/Klasse/Verpackungsgruppe fehlen, markiere die fehlenden Werte als 'fachlich prüfen' und erfinde keine Transportklassifizierung.\n"
        "- Wenn Abschnitt 13 oder 14 fachlich unvollständig bleibt, schreibe einen klaren Review-Hinweis im Abschnitt und in warnings.\n"
        "- In Abschnitt 1.3 muss der Lieferant/Absender dieser CH-Review-Ausgabe VOXSTER GmbH sein.\n"
        "- In Abschnitt 1.4 muss fuer die CH-Review-Ausgabe stehen: Schweiz, Tox Info Suisse, Zuerich, Notfallnummer 145, Aus dem Ausland +41 44 251 51 51.\n"
        "- Entferne auslaendische Notrufnummern aus Abschnitt 1.4 der CH-Review-Ausgabe, z.B. UK-, London-, Malta- oder Hersteller-Notrufnummern.\n"
        "- Hersteller- oder Quelllieferanten aus dem Original-PDF duerfen nicht als 1.3-Lieferant uebernommen werden.\n"
        "- Wenn Herstellerangaben aus der Quelle wichtig sind, erwaehne sie hoechstens knapp als 'Hersteller laut Quelle' an geeigneter Stelle, aber nicht als Lieferant dieser Ausgabe.\n"
        "- Antworte ausschliesslich als JSON-Objekt.\n"
        'JSON-Format: {"review_status": "...", "version_label": "...", "effective_date": "...", '
        '"issuer": {"issuer_name": "...", "issuer_address_line1": "...", "issuer_address_line2": "...", '
        '"issuer_postal_code": "...", "issuer_city": "...", "issuer_country_code": "...", "issuer_phone": "...", "issuer_email": "..."}, '
        '"sections_json": {"section_1": {"title": "...", "content": "...", "fields": {}}, ..., "section_16": {"title": "...", "content": "...", "fields": {}}}, '
        '"warnings": ["..."]}'
    )


def build_sdb_normalization_user_prompt(product: Product, record: ProductSDB) -> str:
    sections_json = record.sections_json or {}
    current_sections = {
        key: {
            "title": str((sections_json.get(key) or {}).get("title") or title).strip() or title,
            "content": str((sections_json.get(key) or {}).get("content") or "").strip(),
        }
        for key, title in ((f"section_{index}", section_title) for index, section_title in SDB_SECTION_TITLES.items())
    }
    product_payload = {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "brand": product.brand.name if product.brand else None,
        "cas_number": product.cas_number,
        "ec_number": product.ec_number,
        "un_number": product.un_number,
        "hazard_class": product.hazard_class,
        "packing_group": product.packing_group,
        "signal_word": product.signal_word,
        "ghs_pictograms": product.ghs_pictograms,
        "sds_url": product.sds_url,
        "source_url_final": product.source_url_final,
    }
    issuer_payload = {
        "issuer_name": record.issuer_name or DEFAULT_ISSUER["issuer_name"],
        "issuer_address_line1": record.issuer_address_line1 or DEFAULT_ISSUER["issuer_address_line1"],
        "issuer_address_line2": record.issuer_address_line2,
        "issuer_postal_code": record.issuer_postal_code or DEFAULT_ISSUER["issuer_postal_code"],
        "issuer_city": record.issuer_city or DEFAULT_ISSUER["issuer_city"],
        "issuer_country_code": record.issuer_country_code or DEFAULT_ISSUER["issuer_country_code"],
        "issuer_phone": record.issuer_phone or DEFAULT_ISSUER["issuer_phone"],
        "issuer_email": record.issuer_email or DEFAULT_ISSUER["issuer_email"],
    }
    return (
        "Bitte normiere das Sicherheitsdatenblatt fuer eine saubere CH-Ausgabe.\n\n"
        "Wichtig:\n"
        "- Nutze VOXSTER GmbH als Absender/Lieferant in Abschnitt 1.3.\n"
        "- Nutze in Abschnitt 1.4 fuer die Schweiz ausschliesslich Tox Info Suisse: Notfallnummer 145, aus dem Ausland +41 44 251 51 51.\n"
        "- Entferne UK-, London-, Malta- oder andere auslaendische Notrufnummern aus Abschnitt 1.4.\n"
        "- Uebernimm Haenseler oder andere Quelllieferanten nicht als Lieferant dieser Ausgabe.\n"
        "- Verwende kurze, gut lesbare Absaetze und Listen.\n"
        "- Entferne PDF-Kopf-/Fusszeilen, Seitenzahlen und Druckvermerke.\n\n"
        "Fokus fuer diese Ausfuehrung:\n"
        "- Abschnitt 13 soll als CH-Review-Entwurf besser nutzbar sein: Produktreste, kontaminierte Verpackungen, restentleerte Verpackungen, VeVA/VVEA/LVA/kantonale Vorschriften, bewilligter Entsorgungsbetrieb. Kein Abfallcode erfinden.\n"
        "- Abschnitt 14 soll eindeutig sein: entweder kein Gefahrgut nach ADR/RID/IMDG/IATA, wenn die Quelle das hergibt, oder klare Prueffelder bei unvollstaendigen Gefahrgutdaten. Keine UN-Versandbezeichnung, Klasse oder Verpackungsgruppe erfinden.\n\n"
        f"Produktdaten:\n{json.dumps(product_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Aktueller SDB-Stand:\n{json.dumps(current_sections, ensure_ascii=False, indent=2)}\n\n"
        f"Absender:\n{json.dumps(issuer_payload, ensure_ascii=False, indent=2)}\n\n"
        "Rohtext des SDB/PDF:\n"
        f"{(record.raw_text or '').strip()}\n"
    )


def build_sdb_critical_sections_system_prompt() -> str:
    return (
        "Du verbesserst ausschliesslich die CH-Review-Abschnitte 13, 14, 15 und 16 eines Sicherheitsdatenblatts.\n"
        "Du erfindest keine regulatorischen Werte, Transportklassifizierungen, Abfallcodes, SUVA-Werte oder Labordaten.\n"
        "Wenn Daten fehlen, markierst du sie klar als fachlich prüfen / beim Hersteller nicht bekannt.\n"
        "Antworte ausschliesslich als JSON-Objekt im Format "
        '{"sections_json": {"section_13": {"title": "...", "content": "...", "fields": {}}, "section_14": {...}, "section_15": {...}, "section_16": {...}}, "warnings": ["..."]}.'
    )


def build_sdb_critical_sections_user_prompt(product: Product, record: ProductSDB, sections_json: dict[str, dict[str, object]]) -> str:
    focused_sections = {
        key: sections_json.get(key) or {}
        for key in ("section_13", "section_14", "section_15", "section_16")
    }
    product_payload = {
        "id": product.id,
        "sku": product.sku,
        "title": product.title,
        "un_number": product.un_number,
        "hazard_class": product.hazard_class,
        "packing_group": product.packing_group,
        "hazard_shipping_note": product.hazard_shipping_note,
        "sds_url": product.sds_url,
    }
    return (
        "Verbessere diese Abschnitte fuer einen Schweizer CH-Review-Entwurf.\n\n"
        "Abschnitt 13:\n"
        "- Konkrete, aber sichere CH-Entsorgungshinweise formulieren.\n"
        "- Produktreste, kontaminierte Verpackungen, restentleerte Verpackungen, bewilligter Entsorgungsbetrieb, VVEA/VeVA/LVA/kantonale Vorschriften nennen.\n"
        "- Kein Abfallcode erfinden; fehlenden Code als fachlich prüfen markieren.\n\n"
        "Abschnitt 14:\n"
        "- Entweder klare Nicht-Gefahrgut-Formulierung, wenn Quelle dies belegt, oder klare Prüffelder bei unvollständigen Gefahrgutdaten.\n"
        "- Keine UN-Versandbezeichnung, Klasse oder Verpackungsgruppe erfinden.\n\n"
        "Abschnitt 15/16:\n"
        "- Schweizer Rechtsvorschriften konkret strukturieren.\n"
        "- Alte EU-Rechtsgrundlagen aus Abschnitt 16 nicht als Hauptreferenzen ausgeben; moderne REACH/CLP/ChemV/ChemRRV/SUVA-Verweise verwenden.\n\n"
        f"Produktdaten:\n{json.dumps(product_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Aktuelle Abschnitte 13-16:\n{json.dumps(focused_sections, ensure_ascii=False, indent=2)}\n\n"
        "Relevanter Rohtextauszug / Originalkontext:\n"
        f"{(record.raw_text or '').strip()[:40000]}\n"
    )


def _call_openai_json(*, system_prompt: str, user_prompt: str, model: str, api_key: str, reasoning_effort: str | None = None) -> dict[str, object]:
    try:
        return _call_openai_responses_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            api_key=api_key,
            reasoning_effort=reasoning_effort,
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code not in {400, 404, 422}:
            raise
        LOGGER.warning("Responses API failed for SDB normalization, falling back to chat completions: %s", exc)
    except ValueError as exc:
        LOGGER.warning("Responses API returned unusable SDB JSON, falling back to chat completions: %s", exc)
    return _call_openai_chat_json(system_prompt=system_prompt, user_prompt=user_prompt, model=model, api_key=api_key)


def _call_openai_responses_json(*, system_prompt: str, user_prompt: str, model: str, api_key: str, reasoning_effort: str | None = None) -> dict[str, object]:
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    payload: dict[str, object] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": _api_reasoning_effort(reasoning_effort)}
    response = requests.post(
        f"{base_url}/responses",
        timeout=240,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    payload_json = response.json()
    raw_text = _extract_responses_text(payload_json)
    parsed_json = _extract_json_object(raw_text)
    return {"raw_text": raw_text, "json": parsed_json}


def _call_openai_chat_json(*, system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, object]:
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    response = requests.post(
        f"{base_url}/chat/completions",
        timeout=120,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
    )
    response.raise_for_status()
    payload = response.json()
    raw_text = (
        (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
        or ""
    )
    parsed_json = _extract_json_object(raw_text)
    return {"raw_text": raw_text, "json": parsed_json}


def _extract_responses_text(payload: dict[str, object]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks).strip()


def _merge_focused_sections_payload(primary_payload: dict[str, object], focused_payload: dict[str, object], normalized_sections: dict[str, dict[str, object]]) -> dict[str, object]:
    merged = dict(primary_payload)
    sections = dict(normalized_sections)
    focused_sections = focused_payload.get("sections_json") or focused_payload.get("sections") or {}
    if isinstance(focused_sections, dict):
        for key in ("section_13", "section_14", "section_15", "section_16"):
            value = focused_sections.get(key)
            if isinstance(value, dict):
                sections[key] = value
    warnings = []
    for source in (primary_payload.get("warnings"), focused_payload.get("warnings")):
        warnings.extend(str(item).strip() for item in (source or []) if str(item).strip())
    merged["sections_json"] = sections
    merged["warnings"] = list(dict.fromkeys(warnings))
    return merged


def _extract_json_object(raw_text: str) -> dict[str, object]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Leere LLM-Antwort")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("LLM-Antwort enthaelt kein JSON-Objekt")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM-Antwort ist kein JSON-Objekt")
    return parsed


def _normalize_quality_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "normal": "standard",
        "medium": "standard",
        "high": "thorough",
        "gruendlich": "thorough",
        "gründlich": "thorough",
        "very_high": "xhigh",
        "very-thorough": "xhigh",
        "sehr_gruendlich": "xhigh",
        "sehr gründlich": "xhigh",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in QUALITY_MODES else "thorough"


def _normalize_reasoning_effort(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"minimal", "low", "medium", "high", "xhigh"} else "high"


def _api_reasoning_effort(value: str | None) -> str:
    normalized = _normalize_reasoning_effort(value)
    return "high" if normalized == "xhigh" else normalized


def _normalize_llm_response(payload: dict[str, object], record: ProductSDB, *, product: Product | None = None) -> dict[str, object]:
    existing_sections = merge_sdb_sections(record.sections_json)
    incoming_sections = payload.get("sections_json") or payload.get("sections") or {}
    merged_sections: dict[str, dict[str, object]] = {}
    for index, default_title in SDB_SECTION_TITLES.items():
        key = f"section_{index}"
        existing_section = existing_sections.get(key) or {}
        incoming_section = incoming_sections.get(key) if isinstance(incoming_sections, dict) else {}
        if not isinstance(incoming_section, dict):
            incoming_section = {}
        title = _clean_section_title(
            str(incoming_section.get("title") or existing_section.get("title") or default_title).strip()
            or default_title,
            index,
            default_title,
        )
        incoming_content = str(incoming_section.get("content") or "").strip()
        existing_content = str(existing_section.get("content") or "").strip()
        existing_fields = existing_section.get("fields") if isinstance(existing_section.get("fields"), dict) else {}
        incoming_fields = incoming_section.get("fields") if isinstance(incoming_section.get("fields"), dict) else {}
        merged_fields = {**existing_fields, **{key: value for key, value in incoming_fields.items() if value not in (None, "")}}
        merged_sections[key] = {
            "title": title,
            "content": incoming_content or existing_content,
            "fields": merged_fields,
        }
    issuer = payload.get("issuer") if isinstance(payload.get("issuer"), dict) else {}
    warnings = payload.get("warnings")
    normalized_warnings = [str(item).strip() for item in (warnings or []) if str(item).strip()]
    merged_sections["section_1"]["content"] = _normalize_section_1_ch_contact_block(
        merged_sections["section_1"]["content"],
        {
            "issuer_name": str(issuer.get("issuer_name") or DEFAULT_ISSUER["issuer_name"]).strip() or DEFAULT_ISSUER["issuer_name"],
            "issuer_address_line1": str(issuer.get("issuer_address_line1") or DEFAULT_ISSUER["issuer_address_line1"]).strip() or DEFAULT_ISSUER["issuer_address_line1"],
            "issuer_address_line2": str(issuer.get("issuer_address_line2") or "").strip() or None,
            "issuer_postal_code": str(issuer.get("issuer_postal_code") or DEFAULT_ISSUER["issuer_postal_code"]).strip() or DEFAULT_ISSUER["issuer_postal_code"],
            "issuer_city": str(issuer.get("issuer_city") or DEFAULT_ISSUER["issuer_city"]).strip() or DEFAULT_ISSUER["issuer_city"],
            "issuer_country_code": str(issuer.get("issuer_country_code") or DEFAULT_ISSUER["issuer_country_code"]).strip() or DEFAULT_ISSUER["issuer_country_code"],
            "issuer_phone": str(issuer.get("issuer_phone") or DEFAULT_ISSUER["issuer_phone"]).strip() or DEFAULT_ISSUER["issuer_phone"],
            "issuer_email": str(issuer.get("issuer_email") or DEFAULT_ISSUER["issuer_email"]).strip() or DEFAULT_ISSUER["issuer_email"],
        },
    )
    synced_sections = sync_sdb_fields_from_content(
        merged_sections,
        issuer_name=str(issuer.get("issuer_name") or DEFAULT_ISSUER["issuer_name"]).strip() or DEFAULT_ISSUER["issuer_name"],
        issuer_address_line1=str(issuer.get("issuer_address_line1") or DEFAULT_ISSUER["issuer_address_line1"]).strip() or DEFAULT_ISSUER["issuer_address_line1"],
        issuer_address_line2=str(issuer.get("issuer_address_line2") or "").strip() or None,
        issuer_postal_code=str(issuer.get("issuer_postal_code") or DEFAULT_ISSUER["issuer_postal_code"]).strip() or DEFAULT_ISSUER["issuer_postal_code"],
        issuer_city=str(issuer.get("issuer_city") or DEFAULT_ISSUER["issuer_city"]).strip() or DEFAULT_ISSUER["issuer_city"],
        issuer_country_code=str(issuer.get("issuer_country_code") or DEFAULT_ISSUER["issuer_country_code"]).strip() or DEFAULT_ISSUER["issuer_country_code"],
        issuer_phone=str(issuer.get("issuer_phone") or DEFAULT_ISSUER["issuer_phone"]).strip() or DEFAULT_ISSUER["issuer_phone"],
        issuer_email=str(issuer.get("issuer_email") or DEFAULT_ISSUER["issuer_email"]).strip() or DEFAULT_ISSUER["issuer_email"],
        product_context={
            "un_number": product.un_number if product else None,
            "hazard_class": product.hazard_class if product else None,
            "packing_group": product.packing_group if product else None,
            "hazard_shipping_note": product.hazard_shipping_note if product else None,
            "density": product.density if product else None,
            "color": product.color if product else None,
            "odor": product.odor if product else None,
            "ph_value": product.ph_value if product else None,
            "flash_point": product.flash_point if product else None,
            "boiling_point": product.boiling_point if product else None,
            "viscosity": product.viscosity if product else None,
            "solubility": product.solubility if product else None,
        },
    )
    return {
        "sections_json": synced_sections,
        "review_status": str(payload.get("review_status") or record.review_status or DEFAULT_REVIEW_STATUS).strip() or DEFAULT_REVIEW_STATUS,
        "version_label": str(payload.get("version_label") or record.version_label or DEFAULT_VERSION_LABEL).strip() or DEFAULT_VERSION_LABEL,
        "effective_date": str(payload.get("effective_date") or record.effective_date or "").strip() or datetime.now(timezone.utc).date().isoformat(),
        "issuer": {
            "issuer_name": str(issuer.get("issuer_name") or DEFAULT_ISSUER["issuer_name"]).strip() or DEFAULT_ISSUER["issuer_name"],
            "issuer_address_line1": str(issuer.get("issuer_address_line1") or DEFAULT_ISSUER["issuer_address_line1"]).strip() or DEFAULT_ISSUER["issuer_address_line1"],
            "issuer_address_line2": str(issuer.get("issuer_address_line2") or "").strip() or None,
            "issuer_postal_code": str(issuer.get("issuer_postal_code") or DEFAULT_ISSUER["issuer_postal_code"]).strip() or DEFAULT_ISSUER["issuer_postal_code"],
            "issuer_city": str(issuer.get("issuer_city") or DEFAULT_ISSUER["issuer_city"]).strip() or DEFAULT_ISSUER["issuer_city"],
            "issuer_country_code": str(issuer.get("issuer_country_code") or DEFAULT_ISSUER["issuer_country_code"]).strip() or DEFAULT_ISSUER["issuer_country_code"],
            "issuer_phone": str(issuer.get("issuer_phone") or DEFAULT_ISSUER["issuer_phone"]).strip() or DEFAULT_ISSUER["issuer_phone"],
            "issuer_email": str(issuer.get("issuer_email") or DEFAULT_ISSUER["issuer_email"]).strip() or DEFAULT_ISSUER["issuer_email"],
        },
        "warnings": normalized_warnings,
    }


def _clean_section_title(title: str, index: int, default_title: str) -> str:
    cleaned = re.sub(rf"^\s*(?:ABSCHNITT|SECTION)\s*{index}\s*[:.)-]?\s*", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(rf"^\s*{index}\s*[:.)-]?\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :-")
    return cleaned or default_title


def _normalize_section_1_ch_contact_block(content: str, issuer: dict[str, str | None]) -> str:
    normalized = str(content or "").strip()
    if not normalized:
        return normalized

    issuer_lines = [
        "1.3 Einzelheiten zum Lieferanten, der das Sicherheitsdatenblatt bereitstellt",
        issuer.get("issuer_name") or DEFAULT_ISSUER["issuer_name"],
        issuer.get("issuer_address_line1") or DEFAULT_ISSUER["issuer_address_line1"],
    ]
    if issuer.get("issuer_address_line2"):
        issuer_lines.append(str(issuer["issuer_address_line2"]))
    postal_city = " ".join(
        part for part in [issuer.get("issuer_postal_code") or DEFAULT_ISSUER["issuer_postal_code"], issuer.get("issuer_city") or DEFAULT_ISSUER["issuer_city"]] if part
    ).strip()
    if postal_city:
        issuer_lines.append(postal_city)
    if issuer.get("issuer_country_code") or DEFAULT_ISSUER["issuer_country_code"]:
        issuer_lines.append(str(issuer.get("issuer_country_code") or DEFAULT_ISSUER["issuer_country_code"]))
    issuer_lines.append(f"Telefon: {issuer.get('issuer_phone') or DEFAULT_ISSUER['issuer_phone']}")
    issuer_lines.append(f"E-Mail der für das SDB verantwortlichen Person: {issuer.get('issuer_email') or DEFAULT_ISSUER['issuer_email']}")
    replacement = "\n".join(issuer_lines)

    pattern = re.compile(
        r"(?is)(^|\n)\s*1\.3\b.*?(?=\n\s*1\.4\b|\Z)"
    )
    if pattern.search(normalized):
        normalized = pattern.sub(lambda m: ("\n" if m.group(1) else "") + replacement, normalized, count=1)
    normalized = _normalize_section_1_emergency_block(normalized)
    return normalized.strip()


def _normalize_section_1_emergency_block(content: str) -> str:
    normalized = str(content or "").strip()
    if not normalized:
        return normalized
    pattern = re.compile(r"(?is)(^|\n)\s*1\.4\b.*?(?=\n\s*1\.5\b|\n\s*2(?:\.|\s|$)|\Z)")
    if pattern.search(normalized):
        return pattern.sub(lambda m: ("\n" if m.group(1) else "") + DEFAULT_CH_EMERGENCY_BLOCK, normalized, count=1).strip()
    if re.search(r"(?im)^\s*1\.3\b", normalized):
        return f"{normalized.rstrip()}\n\n{DEFAULT_CH_EMERGENCY_BLOCK}"
    return normalized


# Backwards-compatible internal alias for older tests/imports.
def _normalize_section_1_supplier_block(content: str, issuer: dict[str, str | None]) -> str:
    return _normalize_section_1_ch_contact_block(content, issuer)


def _apply_default_issuer(record: ProductSDB) -> None:
    record.issuer_name = DEFAULT_ISSUER["issuer_name"]
    record.issuer_address_line1 = DEFAULT_ISSUER["issuer_address_line1"]
    record.issuer_postal_code = DEFAULT_ISSUER["issuer_postal_code"]
    record.issuer_city = DEFAULT_ISSUER["issuer_city"]
    record.issuer_country_code = DEFAULT_ISSUER["issuer_country_code"]
    record.issuer_phone = DEFAULT_ISSUER["issuer_phone"]
    record.issuer_email = DEFAULT_ISSUER["issuer_email"]
    if record.version_label is None:
        record.version_label = DEFAULT_VERSION_LABEL
    if record.review_status is None:
        record.review_status = DEFAULT_REVIEW_STATUS
