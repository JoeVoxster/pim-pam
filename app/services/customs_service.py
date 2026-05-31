from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProductVariant, VariantCustomsAdditionalCode
from app.schemas.pim import VariantCreate, VariantCustomsAdditionalCodeUpsert, VariantUpdate

VARIANT_CUSTOMS_FIELDS = [
    "customs_description_de",
    "customs_description_en",
    "origin_country",
    "material_composition",
    "ch_tariff_code",
    "ch_statistical_key",
    "ch_customs_unit_code",
    "ch_customs_quantity_per_unit",
    "ch_net_mass_kg",
    "ch_gross_mass_kg",
    "ch_preference_possible",
    "ch_origin_proof_required",
    "ch_nze_required",
    "ch_nze_code",
    "ch_voc_relevant",
    "eu_cn_code",
    "eu_taric_code",
    "de_import_code",
    "de_customs_unit_code",
    "de_customs_quantity_per_unit",
    "eu_export_control_required",
    "dual_use_required",
    "reach_relevant",
    "antidumping_relevant",
    "customs_notes",
]


def _parse_datetime_value(value: str | datetime | None) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for date_format in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Datum bitte als TT.MM.JJJJ oder YYYY-MM-DD erfassen.") from exc


def _clean_country(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    return normalized[:2] if normalized else None


def _clean_text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _serialize_customs_additional_code(row: VariantCustomsAdditionalCode) -> dict:
    return {
        "id": row.id,
        "variant_id": row.variant_id,
        "jurisdiction": row.jurisdiction,
        "flow": row.flow,
        "code": row.code,
        "description": row.description,
        "valid_from": row.valid_from.isoformat() if row.valid_from else None,
        "valid_to": row.valid_to.isoformat() if row.valid_to else None,
        "status": row.status,
        "source": row.source,
        "notes": row.notes,
    }

def _serialize_variant_customs_fields(variant: ProductVariant) -> dict:
    payload = {}
    for field_name in VARIANT_CUSTOMS_FIELDS:
        value = getattr(variant, field_name)
        if isinstance(value, Decimal):
            payload[field_name] = float(value)
        else:
            payload[field_name] = value
    payload["customs_additional_codes"] = [_serialize_customs_additional_code(row) for row in variant.customs_additional_codes]
    return payload

def _apply_variant_customs_payload(variant: ProductVariant, payload: VariantCreate | VariantUpdate) -> None:
    provided_fields = getattr(payload, "model_fields_set", None) or getattr(payload, "__fields_set__", set(VARIANT_CUSTOMS_FIELDS))
    for field_name in VARIANT_CUSTOMS_FIELDS:
        if isinstance(payload, VariantUpdate) and field_name not in provided_fields:
            continue
        value = getattr(payload, field_name)
        if field_name == "origin_country":
            value = _clean_country(value)
        elif isinstance(value, str):
            value = _clean_text(value)
        setattr(variant, field_name, value)


def upsert_variant_customs_additional_code(session: Session, payload: VariantCustomsAdditionalCodeUpsert) -> VariantCustomsAdditionalCode:
    variant = session.get(ProductVariant, int(payload.variant_id))
    if variant is None:
        raise ValueError("Variante nicht gefunden.")
    jurisdiction = (payload.jurisdiction or "").strip().upper()
    flow = (payload.flow or "").strip().lower()
    code = (payload.code or "").strip().upper()
    if not jurisdiction or not flow or not code:
        raise ValueError("Jurisdiktion, Flow und Code sind Pflicht.")
    if flow not in {"import", "export", "both"}:
        raise ValueError("Flow muss import, export oder both sein.")
    row = session.get(VariantCustomsAdditionalCode, payload.id) if payload.id else None
    if row is None:
        row = session.scalar(
            select(VariantCustomsAdditionalCode).where(
                VariantCustomsAdditionalCode.variant_id == variant.id,
                VariantCustomsAdditionalCode.jurisdiction == jurisdiction,
                VariantCustomsAdditionalCode.flow == flow,
                VariantCustomsAdditionalCode.code == code,
            )
        )
    if row is None:
        row = VariantCustomsAdditionalCode(variant_id=variant.id, jurisdiction=jurisdiction, flow=flow, code=code)
        session.add(row)
    row.variant_id = variant.id
    row.jurisdiction = jurisdiction
    row.flow = flow
    row.code = code
    row.description = _clean_text(payload.description)
    row.valid_from = _parse_datetime_value(payload.valid_from)
    row.valid_to = _parse_datetime_value(payload.valid_to)
    row.status = payload.status or "active"
    row.source = _clean_text(payload.source)
    row.notes = _clean_text(payload.notes)
    session.flush()
    return row


def delete_variant_customs_additional_code(session: Session, code_id: int) -> None:
    row = session.get(VariantCustomsAdditionalCode, int(code_id))
    if row is None:
        raise ValueError("Zusatzcode nicht gefunden.")
    session.delete(row)
    session.flush()
