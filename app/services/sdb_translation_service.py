from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

import requests
from dotenv import load_dotenv
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.db.models import Asset, ChemicalDocument, Product, ProductSDB, ProductSuvaCheck, SDBTranslationPrompt, SDSReviewIssue
from app.pdf.sdb_renderer import render_sdb_pdf
from app.services.asset_service import create_asset_record
from app.services.chemical_enrichment_service import ingest_product_sdb_asset
from app.services.product_translation_service import get_translation_config_status
from app.services.sdb_support import SDB_SECTION_TITLES, merge_sdb_sections
from app.services.sds_swiss_review_service import assert_final_pdf_allowed
from app.utils.pim_config import get_pim_settings


LOGGER = logging.getLogger(__name__)
load_dotenv()

SDB_DRAFT_WARNING = (
    "KI-ENTWURF - NICHT VERÖFFENTLICHEN. "
    "Sicherheitsdatenblätter sind rechtlich relevante Dokumente und müssen vor Verwendung fachlich/rechtlich geprüft und freigegeben werden."
)
DOCUMENT_STATUSES = {
    "draft",
    "generated",
    "review_required",
    "checked",
    "approved",
    "published",
    "outdated",
    "archived",
    "error",
    "failed",
}
DOCUMENT_SOURCES = {"manual", "generated", "imported", "internet_enrichment", "working_version"}

DEFAULT_SDB_SYSTEM_PROMPT = (
    "Du bist ein professioneller Fachübersetzer für chemische Sicherheitsdatenblätter. "
    "Du übersetzt und strukturierst nur auf Basis des vorhandenen Original-SDB. "
    "Du erfindest keine Gefahrstoffdaten und markierst fehlende regionale Pflichtangaben mit [PRÜFEN]."
)

DEFAULT_SDB_USER_PROMPT_TEMPLATE = """Übersetze das vorhandene Sicherheitsdatenblatt von {{source_locale}} nach {{target_locale}} für die Zielregion {{target_region}}.

Wichtig:
- Gib nur gültiges JSON zurück.
- Keine Markdown-Erklärung ausserhalb des JSON.
- Erfinde keine Daten.
- Behalte CAS-Nummern, H-Sätze, P-Sätze, Einstufungen, Signalwörter, Grenzwerte, Transportangaben, UFI, REACH/CLP-Angaben und Abschnittsnummern unverändert, sofern keine geprüften regionenspezifischen Daten vorhanden sind.
- Wenn regionale Pflichtangaben fehlen, markiere sie im Text mit [PRÜFEN] und erkläre kurz, was geprüft werden muss.
- Gib das Ergebnis strukturiert nach den 16 Abschnitten eines Sicherheitsdatenblatts aus.
- Das Ergebnis ist ein Entwurf und darf nicht automatisch veröffentlicht werden.
- Wenn Informationen fehlen, verwende Warnhinweise wie [PRÜFEN: regionale Notrufnummer fehlt], [PRÜFEN: nationale Grenzwerte Abschnitt 8 fehlen], [PRÜFEN: Entsorgungshinweise Abschnitt 13 für Zielregion prüfen] oder [PRÜFEN: nationale Vorschriften Abschnitt 15 prüfen].

Produkt: {{product_name}}
SKU: {{product_sku}}
Gefahrstoffdaten: {{hazard_classification}}
CAS-Nummern: {{cas_numbers}}
Regulatorische Hinweise: {{regulatory_notes}}

Ausgangs-SDB:
{{source_sdb_text}}

Antwortformat:
{
  "title": "",
  "generatedText": "",
  "reviewNotes": []
}
"""


def get_sdb_translation_config_status() -> dict[str, object]:
    config = get_translation_config_status()
    return {
        "enabled": bool(config.get("enabled")),
        "provider": config.get("provider") or "openai",
        "model": os.getenv("OPENAI_SDB_MODEL") or config.get("model") or "gpt-5-mini",
    }


def ensure_sdb_translation_prompts(session: Session) -> None:
    existing = session.scalar(select(SDBTranslationPrompt).limit(1))
    if existing is None:
        session.add(
            SDBTranslationPrompt(
                name="Standard SDB-Entwurf",
                document_type="sds",
                system_prompt=DEFAULT_SDB_SYSTEM_PROMPT,
                user_prompt_template=DEFAULT_SDB_USER_PROMPT_TEMPLATE,
                active=True,
            )
        )
        session.flush()
        return
    standard = session.scalar(select(SDBTranslationPrompt).where(SDBTranslationPrompt.name == "Standard SDB-Entwurf").limit(1))
    if standard is not None and "Antwortformat" not in (standard.user_prompt_template or ""):
        standard.system_prompt = DEFAULT_SDB_SYSTEM_PROMPT
        standard.user_prompt_template = DEFAULT_SDB_USER_PROMPT_TEMPLATE
        standard.active = True
        session.flush()


def list_sdb_translation_prompts(session: Session) -> list[dict]:
    ensure_sdb_translation_prompts(session)
    rows = session.scalars(select(SDBTranslationPrompt).order_by(SDBTranslationPrompt.active.desc(), SDBTranslationPrompt.name.asc()))
    return [_serialize_prompt(row) for row in rows]


def get_sdb_translation_prompt(session: Session, prompt_id: int | None = None) -> SDBTranslationPrompt:
    ensure_sdb_translation_prompts(session)
    prompt = session.get(SDBTranslationPrompt, int(prompt_id)) if prompt_id else None
    if prompt is None:
        prompt = session.scalar(select(SDBTranslationPrompt).where(SDBTranslationPrompt.active.is_(True)).order_by(SDBTranslationPrompt.id.asc()))
    if prompt is None:
        raise ValueError("Kein SDB-Übersetzungs-Prompt vorhanden.")
    return prompt


def save_sdb_translation_prompt(
    session: Session,
    *,
    prompt_id: int | None = None,
    name: str,
    document_type: str = "sds",
    source_locale: str | None = None,
    target_locale: str | None = None,
    target_region: str | None = None,
    system_prompt: str | None = None,
    user_prompt_template: str | None = None,
    active: bool = True,
) -> SDBTranslationPrompt:
    prompt = session.get(SDBTranslationPrompt, int(prompt_id)) if prompt_id else None
    if prompt is None:
        prompt = SDBTranslationPrompt(name=(name or "SDB-Prompt").strip() or "SDB-Prompt", document_type=document_type or "sds")
        session.add(prompt)
    prompt.name = (name or prompt.name or "SDB-Prompt").strip()
    prompt.document_type = (document_type or "sds").strip()
    prompt.source_locale = _blank_to_none(source_locale)
    prompt.target_locale = _blank_to_none(target_locale)
    prompt.target_region = _blank_to_none(target_region)
    prompt.system_prompt = (system_prompt or "").strip() or DEFAULT_SDB_SYSTEM_PROMPT
    prompt.user_prompt_template = (user_prompt_template or "").strip() or DEFAULT_SDB_USER_PROMPT_TEMPLATE
    prompt.active = bool(active)
    session.flush()
    return prompt


def list_sdb_documents_for_product(session: Session, product_id: int) -> list[dict]:
    _sync_source_sdb_document(session, product_id)
    _sync_sdb_documents_from_assets(session, product_id)
    _normalize_document_registry_metadata(session, product_id)
    stmt = (
        select(ChemicalDocument)
        .where(ChemicalDocument.product_id == int(product_id), ChemicalDocument.document_type == "sds")
        .order_by(ChemicalDocument.created_by_ai.asc(), ChemicalDocument.updated_at.desc())
    )
    return [_serialize_document(row) for row in session.scalars(stmt)]


def sync_product_sdb_working_document(session: Session, product_id: int) -> dict[str, object] | None:
    document = _sync_source_sdb_document(session, product_id)
    if document is None:
        return None
    return _serialize_document(document)


def backfill_sdb_documents_from_assets(session: Session, product_id: int | None = None, *, commit: bool = False) -> dict[str, object]:
    stmt = select(Asset)
    if product_id is not None:
        stmt = stmt.where(Asset.product_id == int(product_id))
    created: list[dict[str, object]] = []
    skipped = 0
    for asset in session.scalars(stmt.order_by(Asset.id.asc())):
        if not _is_sdb_asset(asset):
            continue
        if not asset.product_id:
            skipped += 1
            continue
        existing = session.scalar(select(ChemicalDocument).where(ChemicalDocument.asset_id == asset.id, ChemicalDocument.document_type == "sds").limit(1))
        if existing is not None:
            skipped += 1
            continue
        now = datetime.now(timezone.utc)
        locale = _guess_locale_from_asset(asset) or "de-CH"
        if not commit:
            created.append(
                {
                    "product_id": int(asset.product_id),
                    "asset_id": asset.id,
                    "document_type": "sds",
                    "locale": locale,
                    "language_code": _language_from_locale(locale),
                    "region_code": _region_from_locale(locale),
                    "title": f"SDB {asset.filename}",
                    "file_url": f"/asset-file/{asset.id}",
                    "filename": asset.filename,
                    "mime_type": asset.mime_type,
                    "source": "imported",
                    "status": "draft",
                    "generated_at": (asset.created_at or now).isoformat() if (asset.created_at or now) else None,
                }
            )
            continue
        document = ChemicalDocument(
            product_id=int(asset.product_id),
            asset_id=asset.id,
            document_type="sds",
            locale=locale,
            language_code=_language_from_locale(locale),
            region_code=_region_from_locale(locale),
            title=f"SDB {asset.filename}",
            file_url=f"/asset-file/{asset.id}",
            filename=asset.filename,
            mime_type=asset.mime_type,
            source="imported",
            status="draft",
            generated_at=asset.created_at or now,
            generation_log_json=[{"at": now.isoformat(), "event": "asset_backfill", "message": f"Aus Asset {asset.id} erzeugt."}],
            is_current=True,
        )
        session.add(document)
        session.flush()
        _mark_older_documents_not_current(session, int(asset.product_id), locale, document.id)
        created.append(_serialize_document(document))
    if commit:
        session.commit()
    return {"created_count": len(created), "skipped_count": skipped, "created": created, "committed": bool(commit)}


def _sync_sdb_documents_from_assets(session: Session, product_id: int) -> None:
    stmt = select(Asset).where(Asset.product_id == int(product_id)).order_by(Asset.sort_order.asc(), Asset.id.asc())
    for asset in session.scalars(stmt):
        if not _is_sdb_asset(asset):
            continue
        existing = session.scalar(select(ChemicalDocument).where(ChemicalDocument.asset_id == asset.id, ChemicalDocument.document_type == "sds").limit(1))
        if existing is not None:
            continue
        now = datetime.now(timezone.utc)
        locale = _guess_locale_from_asset(asset) or "de-CH"
        try:
            parsed_pdf = ingest_product_sdb_asset(session, int(product_id), asset.id)
            extracted_text = str(parsed_pdf.get("raw_text") or "").strip() or None
            sections = parsed_pdf.get("sections_json") or {}
            parser_status = str(parsed_pdf.get("parser_status") or "parsed")
            error_message = None
        except Exception as exc:
            extracted_text = None
            sections = {}
            parser_status = "error"
            error_message = str(exc)
        document = ChemicalDocument(
            product_id=int(product_id),
            asset_id=asset.id,
            document_type="sds",
            locale=locale,
            language_code=_language_from_locale(locale),
            region_code=_region_from_locale(locale),
            title=f"SDB {asset.filename}",
            file_url=f"/asset-file/{asset.id}",
            filename=asset.filename,
            mime_type=asset.mime_type,
            extracted_text=extracted_text,
            source="imported",
            status="draft" if extracted_text else "error",
            generated_at=asset.created_at or now,
            generation_log_json=[
                {
                    "at": now.isoformat(),
                    "event": "asset_auto_sync",
                    "message": f"Aus Asset {asset.id} erzeugt. Parser-Status: {parser_status}.",
                    "sections_found": sum(1 for section in sections.values() if isinstance(section, dict) and section.get("content")),
                }
            ],
            error_message=error_message,
            is_current=True,
        )
        session.add(document)
        session.flush()
        _mark_older_documents_not_current(session, int(product_id), locale, document.id)


def _is_sdb_asset(asset: Asset) -> bool:
    asset_type = str(asset.asset_type or "").lower()
    if asset_type in {"sds", "sdb", "safety_data_sheet"}:
        return True
    haystack = " ".join([asset.filename or "", asset.original_filename or "", asset.source_url or ""]).lower()
    return any(keyword in haystack for keyword in ("sdb", "sds", "sicherheitsdatenblatt", "safety-data-sheet", "safety_data_sheet", "safety data sheet", "security_sheet"))


def mark_chemical_document_reviewed(session: Session, document_id: int) -> dict:
    document = _get_document(session, document_id)
    document.status = "checked"
    document.review_note = "Manuell als geprüft markiert."
    session.flush()
    return _serialize_document(document)


def update_chemical_document_status(session: Session, document_id: int, status: str) -> dict:
    document = _get_document(session, document_id)
    document.status = _normalize_document_status(status)
    if document.status == "checked":
        document.review_note = "Manuell als geprüft markiert."
    session.flush()
    return _serialize_document(document)


def get_chemical_document_detail(session: Session, document_id: int) -> dict:
    document = _get_document(session, document_id)
    payload = _serialize_document(document)
    payload["text"] = document.generated_text or document.extracted_text or ""
    return payload


def update_chemical_document_text(session: Session, document_id: int, *, title: str | None = None, text: str | None = None) -> dict:
    document = _get_document(session, document_id)
    if title is not None:
        document.title = str(title).strip() or document.title
    if text is not None:
        if document.created_by_ai:
            document.generated_text = _ensure_draft_warning(text)
            if document.status == "published":
                document.status = "review_required"
        else:
            document.extracted_text = str(text or "")
    session.flush()
    return get_chemical_document_detail(session, document.id)


def archive_chemical_document(session: Session, document_id: int) -> dict:
    document = _get_document(session, document_id)
    document.status = "archived"
    document.is_current = False
    session.flush()
    return _serialize_document(document)


def delete_chemical_document(session: Session, document_id: int) -> dict[str, object]:
    document = _get_document(session, document_id)
    product_id = int(document.product_id)
    locale = document.locale

    # Asset-derived SDB rows are recreated by the asset synchronizer. Hide them
    # instead of pretending a permanent delete is possible while the asset exists.
    if document.asset_id and str(document.source or "") == "imported":
        document.status = "archived"
        document.is_current = False
        document.error_message = "Archiviert statt gelöscht, weil die SDB-Version aus einem vorhandenen Asset stammt."
        session.flush()
        _normalize_document_registry_metadata(session, product_id)
        return {**_serialize_document(document), "delete_mode": "archived_asset_source"}

    if str(document.source or "") == "working_version":
        sdb = session.scalar(select(ProductSDB).where(ProductSDB.product_id == product_id))
        if sdb is not None:
            generated_pdf_path = sdb.generated_pdf_path
            if not document.file_url or document.file_url == sdb.generated_pdf_path:
                sdb.generated_pdf_path = None
                sdb.generated_at = None
                sdb.raw_text = None
                sdb.sections_json = None
                sdb.parser_status = None
            if document.asset_id:
                asset = session.get(Asset, int(document.asset_id))
                if asset is not None and generated_pdf_path and asset.storage_path == generated_pdf_path:
                    session.delete(asset)

    session.execute(update(ChemicalDocument).where(ChemicalDocument.source_document_id == int(document.id)).values(source_document_id=None))
    session.execute(update(ProductSuvaCheck).where(ProductSuvaCheck.sds_id == int(document.id)).values(sds_id=None))
    session.execute(delete(SDSReviewIssue).where(SDSReviewIssue.sds_version_id == int(document.id)))
    deleted = _serialize_document(document)
    session.delete(document)
    session.flush()
    _normalize_document_registry_metadata(session, product_id)
    return {**deleted, "delete_mode": "deleted", "deleted_product_id": product_id, "deleted_locale": locale}


def render_chemical_document_pdf(session: Session, document_id: int) -> dict[str, object]:
    document = session.scalar(
        select(ChemicalDocument)
        .options(joinedload(ChemicalDocument.product))
        .where(ChemicalDocument.id == int(document_id))
    )
    if document is None:
        raise ValueError("SDB-Dokument nicht gefunden.")
    product = document.product
    if product is None:
        raise ValueError("Produkt zum SDB-Dokument nicht gefunden.")
    text = (document.generated_text or document.extracted_text or "").strip()
    if not text:
        raise ValueError("SDB-Dokument enthält keinen Text.")
    review_result = assert_final_pdf_allowed(session, document)

    chem_safety = product.chemical_safety_json or {}
    ghs_codes = chem_safety.get("ghs_pictograms") or product.ghs_pictograms or ""
    if isinstance(ghs_codes, list):
        ghs_codes = "|".join(str(code) for code in ghs_codes)
    adr_codes = chem_safety.get("adr_pictograms") or []
    if isinstance(adr_codes, list):
        adr_codes = "|".join(str(code) for code in adr_codes)
    output_path = _chemical_document_pdf_path(document)
    render_sections = _sections_for_render(document, product, text, chem_safety, str(adr_codes or ""))
    render_sdb_pdf(
        product_title=product.title,
        brand_name=product.brand.name if product.brand else None,
        sku=product.sku,
        cas_number=product.cas_number,
        ec_number=product.ec_number,
        un_number=product.un_number,
        signal_word=str(chem_safety.get("signal_word") or product.signal_word or ""),
        ghs_pictograms=str(ghs_codes or ""),
        adr_pictograms=str(adr_codes or ""),
        review_status="critical_blocked" if int(review_result.get("critical_count") or 0) else document.status,
        version_label=document.version,
        effective_date=document.valid_from,
        issuer_name="VOXSTER GmbH",
        issuer_address_line1="Obere Ifangstrasse 10",
        issuer_address_line2=None,
        issuer_postal_code="8215",
        issuer_city="Hallau",
        issuer_country_code="CH",
        sections=render_sections,
        output_path=output_path,
        document_title=document.title,
    )
    document.file_url = f"/chemical-document-pdf/{document.id}"
    document.filename = output_path.name
    document.mime_type = "application/pdf"
    document.source = "generated" if document.created_by_ai else _normalize_document_source(document.source or "generated")
    document.generated_at = document.generated_at or datetime.now(timezone.utc)
    document.generation_log_json = _append_generation_log(
        document.generation_log_json,
        "pdf_generated",
        f"PDF wurde erzeugt: {output_path}. CH-Review: {review_result.get('swiss_review_status')}; critical={review_result.get('critical_count')}.",
    )
    asset = _find_or_create_generated_pdf_asset(session, product.id, output_path, document.title or product.title)
    document.asset_id = asset.id
    session.flush()
    return {**_serialize_document(document), "pdf_path": str(output_path), "pdf_url": document.file_url}


def generate_sdb_translation_draft(
    session: Session,
    *,
    product_id: int,
    source_document_id: int,
    target_locale: str,
    target_region: str,
    source_locale: str | None = None,
    prompt_id: int | None = None,
) -> dict[str, object]:
    config = get_sdb_translation_config_status()
    if config["provider"] != "openai":
        return {"status": "failed", "message": f"Provider nicht unterstützt: {config['provider']}"}
    if not config["enabled"]:
        return {"status": "failed", "message": "OPENAI_API_KEY ist nicht konfiguriert."}
    if not product_id:
        return {"status": "failed", "message": "Kein Produkt ausgewählt."}
    if not source_document_id:
        return {"status": "failed", "message": "Kein Ausgangs-SDB ausgewählt."}
    target_locale = str(target_locale or "").strip()
    target_region = str(target_region or "").strip()
    if not target_locale:
        return {"status": "failed", "message": "Keine Ziel-Locale ausgewählt."}
    if not target_region:
        return {"status": "failed", "message": "Keine Zielregion ausgewählt."}

    product = session.scalar(select(Product).where(Product.id == int(product_id)))
    if product is None:
        return {"status": "failed", "message": "Produkt nicht gefunden."}
    source_document = session.get(ChemicalDocument, int(source_document_id))
    if source_document is None or source_document.product_id != int(product_id):
        return {"status": "failed", "message": "Ausgangs-SDB nicht gefunden."}
    source_text = (source_document.generated_text or source_document.extracted_text or "").strip()
    if not source_text:
        return {"status": "failed", "message": "Ausgangs-SDB enthält keinen Text."}

    prompt = get_sdb_translation_prompt(session, prompt_id)
    rendered_prompt = render_sdb_translation_prompt(
        product=product,
        source_document=source_document,
        source_locale=source_locale or source_document.locale or "de-CH",
        target_locale=target_locale,
        target_region=target_region,
        prompt_template=prompt.user_prompt_template,
        source_text=source_text,
    )
    generated_at = datetime.now(timezone.utc)
    try:
        payload = _call_openai_sdb_translation_json(
            system_prompt=prompt.system_prompt or DEFAULT_SDB_SYSTEM_PROMPT,
            user_prompt=rendered_prompt,
            model=str(config["model"]),
            api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
        )
    except Exception as exc:
        LOGGER.exception("SDB translation failed for product %s", product_id)
        document = ChemicalDocument(
            product_id=int(product_id),
            document_type="sds",
            source_document_id=source_document.id,
            locale=target_locale,
            language_code=_language_from_locale(target_locale),
            region_code=target_region,
            title=f"SDB-Fehler {product.title} {target_locale}/{target_region}",
            status="error",
            source="generated",
            generated_at=generated_at,
            created_by_ai=True,
            ai_provider=str(config["provider"]),
            ai_model=str(config["model"]),
            ai_prompt_id=prompt.id,
            error_message=str(exc),
            generation_log_json=[{"at": generated_at.isoformat(), "event": "generation_error", "message": str(exc)}],
            is_current=False,
        )
        session.add(document)
        session.flush()
        return {"status": "failed", "message": str(exc), "document": _serialize_document(document)}

    generated_text = _ensure_draft_warning(payload["generatedText"])
    review_notes = payload.get("reviewNotes") or []
    review_note = "\n".join([SDB_DRAFT_WARNING, *[str(note) for note in review_notes if str(note or "").strip()]])
    document = ChemicalDocument(
        product_id=int(product_id),
        document_type="sds",
        source_document_id=source_document.id,
        locale=target_locale,
        language_code=_language_from_locale(target_locale),
        region_code=target_region,
        title=payload["title"] or f"SDB-Entwurf {product.title} {target_locale}/{target_region}",
        generated_text=generated_text,
        status="review_required",
        source="generated",
        generated_at=generated_at,
        filename=f"sdb-{product.sku or product.id}-{target_locale}.txt",
        mime_type="text/plain",
        generation_log_json=[
            {
                "at": generated_at.isoformat(),
                "event": "ai_draft_generated",
                "provider": str(config["provider"]),
                "model": str(config["model"]),
                "source_document_id": source_document.id,
                "target_locale": target_locale,
                "target_region": target_region,
            }
        ],
        is_current=True,
        version=source_document.version,
        valid_from=source_document.valid_from,
        created_by_ai=True,
        ai_provider=str(config["provider"]),
        ai_model=str(config["model"]),
        ai_prompt_id=prompt.id,
        review_note=review_note,
    )
    session.add(document)
    session.flush()
    _mark_older_documents_not_current(session, int(product_id), target_locale, document.id)
    return {"status": "review_required", "message": "SDB-Übersetzungsentwurf erstellt. Prüfung erforderlich.", "document": _serialize_document(document)}


def render_sdb_translation_prompt(
    *,
    product: Product,
    source_document: ChemicalDocument,
    source_locale: str,
    target_locale: str,
    target_region: str,
    prompt_template: str,
    source_text: str,
) -> str:
    variables = {
        "product_name": product.title or "",
        "product_sku": product.sku or "",
        "source_locale": source_locale or source_document.locale or "",
        "target_locale": target_locale or "",
        "target_region": target_region or "",
        "source_sdb_text": source_text,
        "hazard_classification": _join_non_empty([product.hazard_class, product.signal_word, product.hazard_statements, product.precautionary_statements]),
        "cas_numbers": product.cas_number or "",
        "regulatory_notes": _join_non_empty([product.ec_number, product.un_number, product.wgk, product.storage_class]),
    }
    rendered = prompt_template or DEFAULT_SDB_USER_PROMPT_TEMPLATE
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return Template(rendered).safe_substitute(variables)


def _sync_source_sdb_document(session: Session, product_id: int) -> ChemicalDocument | None:
    product = session.get(Product, int(product_id))
    if product is None:
        return None
    sdb = session.scalar(select(ProductSDB).where(ProductSDB.product_id == int(product_id)))
    if sdb is None:
        return None
    source_text = _source_text_from_product_sdb(sdb).strip()
    if not source_text:
        return None
    is_working_pdf = bool(sdb.generated_pdf_path)
    stmt = select(ChemicalDocument).where(
        ChemicalDocument.product_id == int(product_id),
        ChemicalDocument.document_type == "sds",
        ChemicalDocument.created_by_ai.is_(False),
        ChemicalDocument.source_document_id.is_(None),
    )
    if is_working_pdf:
        stmt = stmt.where(
            or_(
                ChemicalDocument.source == "working_version",
                ChemicalDocument.file_url == sdb.generated_pdf_path,
            )
        )
    else:
        stmt = stmt.where(or_(ChemicalDocument.source.is_(None), ChemicalDocument.source != "working_version"))
    document = session.scalar(stmt.limit(1))
    created_document = document is None
    if document is None:
        document = ChemicalDocument(product_id=int(product_id), document_type="sds", created_by_ai=False, status=_normalize_document_status(sdb.review_status))
        session.add(document)
    document.locale = product.source_language or "de-CH"
    document.language_code = _language_from_locale(document.locale)
    document.region_code = _region_from_locale(document.locale)
    document.title = (
        (f"{sdb.document_title} (Arbeitsversion)" if sdb.document_title else f"SDB {product.title} (Arbeitsversion)")
        if is_working_pdf
        else (sdb.document_title or f"SDB {product.title}")
    )
    document.file_url = _source_file_url_from_product_sdb(sdb)
    document.asset_id = _source_asset_id_from_product_sdb(session, product, sdb)
    asset = session.get(Asset, int(document.asset_id)) if document.asset_id else None
    document.filename = asset.filename if asset else _filename_from_url_or_path(document.file_url)
    document.mime_type = asset.mime_type if asset else _mime_type_from_filename(document.filename or document.file_url)
    if is_working_pdf:
        normalized_working_text = _sections_text_from_product_sdb(sdb)
        if normalized_working_text:
            document.generated_text = normalized_working_text
    document.extracted_text = source_text
    if created_document:
        document.status = _normalize_document_status(sdb.review_status or document.status)
    document.source = "working_version" if is_working_pdf else _source_kind_from_product_sdb(sdb)
    document.generated_at = sdb.generated_at or document.generated_at or sdb.updated_at or sdb.created_at
    document.generation_log_json = document.generation_log_json or []
    document.error_message = None
    document.is_current = True
    document.version = sdb.version_label
    document.valid_from = sdb.effective_date
    session.flush()
    _mark_older_documents_not_current(session, int(product_id), document.locale, document.id)
    return document


def _source_text_from_product_sdb(sdb: ProductSDB) -> str:
    if sdb.raw_text:
        return sdb.raw_text
    sections = merge_sdb_sections(sdb.sections_json)
    chunks: list[str] = []
    for index, title in SDB_SECTION_TITLES.items():
        section = sections.get(f"section_{index}") or {}
        content = str(section.get("content") or "").strip()
        if content:
            chunks.append(f"{index}. {title}\n{content}")
    return "\n\n".join(chunks)


def _sections_text_from_product_sdb(sdb: ProductSDB) -> str:
    sections = merge_sdb_sections(sdb.sections_json)
    chunks: list[str] = []
    for index, fallback_title in SDB_SECTION_TITLES.items():
        section = sections.get(f"section_{index}") or {}
        title = str(section.get("title") or fallback_title).strip()
        content = str(section.get("content") or "").strip()
        if content:
            chunks.append(f"ABSCHNITT {index}: {title}\n{content}")
    return "\n\n".join(chunks)


def _chemical_document_pdf_path(document: ChemicalDocument) -> Path:
    root = get_pim_settings().asset_storage_root / "generated_sdb_documents"
    root.mkdir(parents=True, exist_ok=True)
    locale = re.sub(r"[^A-Za-z0-9_-]+", "-", document.locale or "unknown").strip("-") or "unknown"
    return root / f"product-{document.product_id}-document-{document.id}-{locale}.pdf"


def _sections_from_document_text(text: str) -> dict[str, dict[str, str]]:
    normalized = (text or "").replace("\r", "\n")
    result = {f"section_{number}": {"title": title, "content": ""} for number, title in SDB_SECTION_TITLES.items()}
    pattern = re.compile(r"(?im)^\s*(?:ABSCHNITT|SECTION)\s*(1[0-6]|[1-9])\s*[:.)-]?\s*(.+?)\s*$")
    matches = [match for match in pattern.finditer(normalized) if int(match.group(1)) in SDB_SECTION_TITLES]
    if not matches:
        pattern = re.compile(r"(?im)^\s*(1[0-6]|[1-9])\.\s+(?!\d)(.+?)\s*$")
        matches = [match for match in pattern.finditer(normalized) if int(match.group(1)) in SDB_SECTION_TITLES]
    if not matches:
        result["section_1"]["content"] = normalized.strip()
        return result
    for index, match in enumerate(matches):
        section_number = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        title = re.sub(r"\s+", " ", match.group(2)).strip(" :-") or SDB_SECTION_TITLES[section_number]
        result[f"section_{section_number}"] = {"title": title, "content": normalized[start:end].strip()}
    return result


def _sections_for_render(
    document: ChemicalDocument,
    product: Product,
    text: str,
    chem_safety: dict,
    adr_codes: str,
) -> dict[str, dict[str, str]]:
    sections = _sections_from_document_text(text)
    sections["section_14"] = _normalize_transport_section_for_render(sections.get("section_14") or {}, product, chem_safety, adr_codes)
    return sections


def _normalize_transport_section_for_render(section: dict[str, str], product: Product, chem_safety: dict, adr_codes: str) -> dict[str, str]:
    content = str(section.get("content") or "").strip()
    content = re.sub(r"\(vide\)", "nicht verfügbar", content, flags=re.I)
    content = re.sub(
        r"(?im)^\s*Gefahrzettel\s*:\s*\[PRÜFEN:[^\n]+\]\s*$",
        "",
        content,
    )
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    adr_class = str(chem_safety.get("adr_class") or product.hazard_class or "").strip()
    transport_labels = _transport_labels_from_codes(adr_codes)
    environmental = "UMWELTGEFÄHRDEND" if chem_safety.get("environmentally_hazardous") else ""
    summary_rows = [
        ("UN-Nummer", product.un_number),
        ("ADR/RID-Klasse", adr_class),
        ("Gefahrzettel / Transportpiktogramme", transport_labels),
        ("Verpackungsgruppe", product.packing_group),
        ("Begrenzte Menge", product.limited_quantity),
        ("Umweltgefahren", environmental),
    ]
    summary = "\n".join(f"{label}: {value}" for label, value in summary_rows if str(value or "").strip())
    if summary:
        content = f"Deterministische Transport-Kurzangaben aus Produkt-/SDB-Daten:\n{summary}\n\n{content}".strip()
    return {
        "title": section.get("title") or "Angaben zum Transport",
        "content": content or "-",
    }


def _transport_labels_from_codes(adr_codes: str) -> str:
    codes = _split_adr_codes_for_summary(adr_codes)
    labels: list[str] = []
    if "ADR_8" in codes:
        labels.append("ADR Klasse 8 - ätzende Stoffe")
    if "ADR_pollution" in codes:
        labels.append("Umweltgefährdend - Fisch/Baum")
    if "ADR_LQ" in codes:
        labels.append("LQ / Limited Quantity")
    return "; ".join(labels)


def _split_adr_codes_for_summary(value: str | None) -> list[str]:
    result: list[str] = []
    for item in re.split(r"[|,;\s]+", value or ""):
        cleaned = item.strip()
        if cleaned in {"ADR_3", "ADR_5.1", "ADR_8", "ADR_pollution", "ADR_LQ"} and cleaned not in result:
            result.append(cleaned)
    return result


def _call_openai_sdb_translation_json(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, Any]:
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    request_payload: dict[str, Any] = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if not str(model or "").startswith("gpt-5"):
        request_payload["temperature"] = 0.1
    response = requests.post(
        f"{base_url}/chat/completions",
        timeout=180,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=request_payload,
    )
    if response.status_code >= 400:
        message = response.text[:500]
        try:
            error_payload = response.json().get("error") or {}
            message = error_payload.get("message") or message
        except ValueError:
            pass
        raise RuntimeError(f"OpenAI API Fehler {response.status_code}: {message}")
    raw_text = ((((response.json().get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
    return _validate_sdb_translation_payload(_extract_json_object(raw_text))


def _validate_sdb_translation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title") or ""
    generated_text = payload.get("generatedText") or payload.get("generated_text") or payload.get("text") or ""
    review_notes = payload.get("reviewNotes") or payload.get("review_notes") or []
    if not isinstance(title, str):
        raise ValueError("KI-Antwort Feld title ist kein String")
    if not isinstance(generated_text, str):
        generated_text = _stringify_generated_sdb_text(generated_text)
    if not generated_text.strip():
        raise ValueError("KI-Antwort enthält keinen SDB-Text")
    if not isinstance(review_notes, list):
        review_notes = [str(review_notes)]
    return {"title": title, "generatedText": generated_text, "reviewNotes": review_notes}


def _stringify_generated_sdb_text(value: object) -> str:
    if isinstance(value, dict):
        chunks: list[str] = []
        sections = value.get("sections") if isinstance(value.get("sections"), list) else None
        rows = sections or [value]
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                chunks.append(str(row))
                continue
            number = row.get("number") or row.get("section") or index
            title = row.get("title") or row.get("heading") or SDB_SECTION_TITLES.get(int(number), "") if str(number).isdigit() else row.get("title") or row.get("heading") or ""
            content = row.get("content") or row.get("text") or row.get("body") or ""
            chunks.append(f"{number}. {title}\n{content}".strip())
        return "\n\n".join(chunk for chunk in chunks if chunk.strip())
    if isinstance(value, list):
        return "\n\n".join(_stringify_generated_sdb_text(item) for item in value if item is not None)
    return str(value or "")


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Leere KI-Antwort")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            raise ValueError("KI-Antwort enthält kein JSON-Objekt") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("KI-Antwort ist kein JSON-Objekt")
    return parsed


def _serialize_prompt(row: SDBTranslationPrompt) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "document_type": row.document_type,
        "source_locale": row.source_locale,
        "target_locale": row.target_locale,
        "target_region": row.target_region,
        "system_prompt": row.system_prompt,
        "user_prompt_template": row.user_prompt_template,
        "active": bool(row.active),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_document(row: ChemicalDocument) -> dict:
    generated_at = row.generated_at or row.created_at
    pdf_url = _document_pdf_url(row)
    has_text = bool((row.generated_text or row.extracted_text or "").strip())
    has_pdf = bool(pdf_url)
    return {
        "id": row.id,
        "product_id": row.product_id,
        "document_type": row.document_type,
        "document_type_label": "SDB" if str(row.document_type or "").lower() in {"sds", "sdb"} else str(row.document_type or "").upper(),
        "source_document_id": row.source_document_id,
        "locale": row.locale,
        "language_code": row.language_code,
        "region_code": row.region_code,
        "title": row.title,
        "file_url": row.file_url,
        "pdf_url": pdf_url,
        "pdf_status": "PDF vorhanden" if has_pdf else "PDF fehlt",
        "text_status": "Text vorhanden" if has_text else "Kein Text",
        "action_hint": _document_action_hint(row, has_text=has_text, has_pdf=has_pdf),
        "asset_id": row.asset_id,
        "filename": row.filename,
        "mime_type": row.mime_type,
        "status": row.status,
        "source": row.source or "manual",
        "generated_at": generated_at.isoformat() if generated_at else None,
        "generated_at_display": _format_dt_display(generated_at),
        "generation_log_json": row.generation_log_json,
        "error_message": row.error_message,
        "is_current": bool(row.is_current),
        "version": row.version,
        "valid_from": row.valid_from,
        "created_by_ai": bool(row.created_by_ai),
        "ai_provider": row.ai_provider,
        "ai_model": row.ai_model,
        "ai_prompt_id": row.ai_prompt_id,
        "review_note": row.review_note,
        "swiss_review_status": row.swiss_review_status,
        "compliance_score": row.compliance_score,
        "source_issue_date": row.source_issue_date,
        "source_revision": row.source_revision,
        "ufi": row.ufi,
        "rpc_status": row.rpc_status,
        "waste_code_ch": row.waste_code_ch,
        "transport_review_status": row.transport_review_status,
        "last_ch_review_at": row.last_ch_review_at.isoformat() if row.last_ch_review_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_at_display": _format_dt_display(row.created_at),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_at_display": _format_dt_display(row.updated_at),
        "has_text": has_text,
        "has_pdf": has_pdf,
    }


def _document_action_hint(row: ChemicalDocument, *, has_text: bool, has_pdf: bool) -> str:
    if str(row.status or "") == "error":
        return f"Fehler prüfen: {str(row.error_message or 'keine Details')[:120]}"
    if not has_text:
        return "Erst Text/Entwurf erzeugen"
    if not has_pdf:
        return "PDF erzeugen"
    return "PDF öffnen/herunterladen"


def _get_document(session: Session, document_id: int) -> ChemicalDocument:
    document = session.get(ChemicalDocument, int(document_id))
    if document is None:
        raise ValueError("SDB-Dokument nicht gefunden.")
    return document


def _ensure_draft_warning(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("KI-ENTWURF"):
        return stripped
    return f"{SDB_DRAFT_WARNING}\n\n{stripped}"


def _language_from_locale(locale: str | None) -> str | None:
    value = str(locale or "").strip()
    return value.split("-", 1)[0] if value else None


def _region_from_locale(locale: str | None) -> str | None:
    value = str(locale or "").strip()
    if "-" in value:
        return value.split("-", 1)[1]
    return value.upper() if value else None


def _blank_to_none(value: str | None) -> str | None:
    stripped = str(value or "").strip()
    return stripped or None


def _join_non_empty(values: list[str | None]) -> str:
    return "; ".join(str(value).strip() for value in values if str(value or "").strip())


def _normalize_document_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in DOCUMENT_STATUSES:
        return normalized
    if normalized in {"freigegeben", "approved_ch"}:
        return "approved"
    if normalized in {"geprüft", "checked_ch"}:
        return "checked"
    if normalized in {"archiviert"}:
        return "archived"
    if normalized in {"fehler", "failed_error"}:
        return "error"
    return "review_required"


def _normalize_document_source(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in DOCUMENT_SOURCES else "manual"


def _source_file_url_from_product_sdb(sdb: ProductSDB) -> str | None:
    if sdb.generated_pdf_path:
        return sdb.generated_pdf_path
    return sdb.pdf_url or sdb.source_url


def _source_kind_from_product_sdb(sdb: ProductSDB) -> str:
    if sdb.generated_pdf_path:
        return "generated"
    if sdb.pdf_url or sdb.source_url:
        return "internet_enrichment"
    if sdb.source_asset_id:
        return "imported"
    return "manual"


def _source_asset_id_from_product_sdb(session: Session, product: Product, sdb: ProductSDB) -> int | None:
    if sdb.generated_pdf_path:
        path = Path(sdb.generated_pdf_path)
        if path.exists():
            return _find_or_create_generated_pdf_asset(session, product.id, path, sdb.document_title or product.title).id
    return sdb.source_asset_id


def _find_or_create_generated_pdf_asset(session: Session, product_id: int, path: Path, title: str | None) -> Asset:
    existing = session.scalar(select(Asset).where(Asset.product_id == int(product_id), Asset.storage_path == str(path)).limit(1))
    if existing is not None:
        return existing
    return create_asset_record(session, path, product_id=int(product_id), alt_text=title, source_url=None)


def _mark_older_documents_not_current(session: Session, product_id: int, locale: str | None, keep_document_id: int) -> None:
    if not locale:
        return
    rows = session.scalars(
        select(ChemicalDocument).where(
            ChemicalDocument.product_id == int(product_id),
            ChemicalDocument.document_type == "sds",
            ChemicalDocument.locale == locale,
            ChemicalDocument.id != int(keep_document_id),
            ChemicalDocument.is_current.is_(True),
        )
    )
    for row in rows:
        row.is_current = False
        if row.status not in {"archived", "outdated"}:
            row.status = "outdated"


def _normalize_document_registry_metadata(session: Session, product_id: int) -> None:
    rows = list(
        session.scalars(
            select(ChemicalDocument)
            .where(ChemicalDocument.product_id == int(product_id), ChemicalDocument.document_type == "sds")
            .order_by(ChemicalDocument.locale.asc(), ChemicalDocument.updated_at.desc(), ChemicalDocument.id.desc())
        )
    )
    latest_by_locale: dict[str, ChemicalDocument] = {}
    for row in rows:
        if row.status == "archived":
            row.is_current = False
            continue
        if row.asset_id and session.get(Asset, int(row.asset_id)) is None:
            row.asset_id = None
            row.file_url = None
            row.is_current = False
            row.status = "error"
            row.error_message = "Verknüpftes Asset fehlt; PDF-Link wurde deaktiviert."
            continue
        if not row.source or row.source == "manual":
            if row.created_by_ai:
                row.source = "generated"
            elif row.asset_id:
                row.source = "imported"
            elif row.file_url:
                row.source = "internet_enrichment" if str(row.file_url).startswith(("http://", "https://")) else "generated"
            else:
                row.source = "manual"
        if not row.generated_at:
            row.generated_at = row.updated_at or row.created_at or datetime.now(timezone.utc)
        if not row.filename:
            row.filename = _filename_from_url_or_path(row.file_url)
        if not row.mime_type:
            row.mime_type = _mime_type_from_filename(row.filename or row.file_url)
        locale_key = row.locale or row.language_code or f"document-{row.id}"
        current_latest = latest_by_locale.get(locale_key)
        if current_latest is None or _document_current_priority(row) > _document_current_priority(current_latest):
            latest_by_locale[locale_key] = row
    for row in rows:
        if row.status in {"archived", "error"}:
            row.is_current = False
            continue
        locale_key = row.locale or row.language_code or f"document-{row.id}"
        latest = latest_by_locale.get(locale_key)
        if latest is row:
            row.is_current = True
            continue
        row.is_current = False
        if row.status not in {"archived", "outdated", "approved", "checked", "error"}:
            row.status = "outdated"


def _document_current_priority(row: ChemicalDocument) -> tuple[int, datetime, int]:
    if row.status == "error":
        return (-100, datetime.min.replace(tzinfo=timezone.utc), int(row.id or 0))
    source_priority = {
        "working_version": 50,
        "generated": 40,
        "imported": 30,
        "internet_enrichment": 20,
        "manual": 10,
    }.get(str(row.source or ""), 0)
    has_pdf_bonus = 5 if _document_pdf_url(row) else 0
    generated_at = row.generated_at or row.updated_at or row.created_at or datetime.min.replace(tzinfo=timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    return (source_priority + has_pdf_bonus, generated_at, int(row.id or 0))


def _append_generation_log(current: list | dict | None, event: str, message: str) -> list[dict[str, str]]:
    entries = current if isinstance(current, list) else []
    return [
        *entries,
        {"at": datetime.now(timezone.utc).isoformat(), "event": event, "message": message},
    ]


def _filename_from_url_or_path(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return Path(text.split("?", 1)[0]).name or None


def _mime_type_from_filename(value: str | None) -> str | None:
    name = str(value or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".txt"):
        return "text/plain"
    return None


def _document_pdf_url(row: ChemicalDocument) -> str | None:
    filename = str(row.filename or row.file_url or "").split("?", 1)[0]
    mime_type = str(row.mime_type or "").lower()
    is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")
    if row.asset_id and is_pdf:
        return f"/asset-file/{row.asset_id}"
    file_url = str(row.file_url or "").strip()
    if not file_url:
        return None
    if file_url.startswith("/chemical-document-pdf/"):
        return file_url
    if file_url.startswith("/asset-file/") and is_pdf:
        return file_url
    if file_url.startswith(("http://", "https://")) and is_pdf:
        return file_url
    return None


def _guess_locale_from_asset(asset: Asset) -> str | None:
    text = " ".join([asset.filename or "", asset.original_filename or "", asset.source_url or ""]).lower()
    for locale in ("de-CH", "de-DE", "fr-CH", "it-CH", "en-GB", "en"):
        if locale.lower() in text:
            return locale
    if "_de" in text or "-de" in text or "/de" in text:
        return "de-CH"
    if "_fr" in text or "-fr" in text or "/fr" in text:
        return "fr-CH"
    if "_it" in text or "-it" in text or "/it" in text:
        return "it-CH"
    return None


def _format_dt_display(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%d.%m.%Y %H:%M")
