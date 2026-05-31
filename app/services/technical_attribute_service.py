from __future__ import annotations

from decimal import Decimal

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import ProductVariant, TechnicalAttributeLabelTranslation, VariantTechnicalAttribute, VariantTechnicalAttributeValueTranslation
from app.schemas.pim import TechnicalAttributeLabelTranslationUpsert, VariantTechnicalAttributeUpsert, VariantTechnicalAttributeValueTranslationUpsert


def _clean_text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _serialize_variant_technical_attribute(row: VariantTechnicalAttribute) -> dict:
    value_number = float(row.value_number) if row.value_number is not None else None
    value_display = row.value_text
    if value_display in (None, "") and value_number is not None:
        value_display = f"{value_number:g}"
    return {
        "id": row.id,
        "variant_id": row.variant_id,
        "attribute_code": row.attribute_code,
        "attribute_name": row.attribute_name,
        "value_text": row.value_text,
        "value_number": value_number,
        "unit": row.unit,
        "sort_order": row.sort_order,
        "value_display": value_display,
        "display": " ".join(str(part) for part in [row.attribute_name, value_display, row.unit] if part not in (None, "")),
        "translations": [_serialize_variant_technical_attribute_value_translation(translation) for translation in row.translations],
    }


def _serialize_technical_attribute_label_translation(row: TechnicalAttributeLabelTranslation) -> dict:
    return {
        "id": row.id,
        "attribute_code": row.attribute_code,
        "language_code": row.language_code,
        "label": row.label,
    }


def _serialize_variant_technical_attribute_value_translation(row: VariantTechnicalAttributeValueTranslation) -> dict:
    attr = row.technical_attribute
    variant = attr.variant if attr else None
    product = variant.product if variant else None
    value_number = float(attr.value_number) if attr and attr.value_number is not None else None
    original_value = attr.value_text if attr else None
    if original_value in (None, "") and value_number is not None:
        original_value = f"{value_number:g}"
    return {
        "id": row.id,
        "technical_attribute_id": row.technical_attribute_id,
        "variant_id": variant.id if variant else None,
        "variant_sku": variant.sku if variant else None,
        "product_id": product.id if product else None,
        "product_sku": product.sku if product else None,
        "attribute_code": attr.attribute_code if attr else None,
        "attribute_name": attr.attribute_name if attr else None,
        "original_value": original_value,
        "unit": attr.unit if attr else None,
        "language_code": row.language_code,
        "value_text": row.value_text,
    }

def upsert_variant_technical_attribute(session: Session, payload: VariantTechnicalAttributeUpsert) -> VariantTechnicalAttribute:
    variant = session.get(ProductVariant, int(payload.variant_id))
    if variant is None:
        raise ValueError("Variante nicht gefunden.")
    attribute_name = _clean_text(payload.attribute_name)
    if not attribute_name:
        raise ValueError("Attribut ist Pflicht.")
    attribute_code = _clean_text(payload.attribute_code) or slugify(attribute_name, separator="_")
    if not attribute_code:
        raise ValueError("Attribut-Code konnte nicht erzeugt werden.")
    attribute_code = attribute_code.lower().replace("-", "_")[:100]
    row = session.get(VariantTechnicalAttribute, int(payload.id)) if payload.id else None
    if row is None:
        row = session.scalar(
            select(VariantTechnicalAttribute).where(
                VariantTechnicalAttribute.variant_id == variant.id,
                VariantTechnicalAttribute.attribute_code == attribute_code,
            )
        )
    if row is None:
        row = VariantTechnicalAttribute(variant_id=variant.id, attribute_code=attribute_code)
        session.add(row)
    row.attribute_name = attribute_name
    row.attribute_code = attribute_code
    row.value_text = _clean_text(payload.value_text)
    row.value_number = payload.value_number
    row.unit = _clean_text(payload.unit)
    row.sort_order = int(payload.sort_order or 0)
    session.flush()
    return row


def delete_variant_technical_attribute(session: Session, attribute_id: int) -> None:
    row = session.get(VariantTechnicalAttribute, int(attribute_id))
    if row is None:
        raise ValueError("Technisches Attribut nicht gefunden.")
    session.delete(row)
    session.flush()


def upsert_technical_attribute_label_translation(
    session: Session,
    payload: TechnicalAttributeLabelTranslationUpsert,
) -> TechnicalAttributeLabelTranslation:
    attribute_code = (_clean_text(payload.attribute_code) or "").lower().replace("-", "_")[:100]
    language_code = _clean_text(payload.language_code)
    label = _clean_text(payload.label)
    if not attribute_code or not language_code or not label:
        raise ValueError("Attribut-Code, Sprache und Label sind Pflicht.")
    row = session.get(TechnicalAttributeLabelTranslation, int(payload.id)) if payload.id else None
    if row is None:
        row = session.scalar(
            select(TechnicalAttributeLabelTranslation).where(
                TechnicalAttributeLabelTranslation.attribute_code == attribute_code,
                TechnicalAttributeLabelTranslation.language_code == language_code,
            )
        )
    if row is None:
        row = TechnicalAttributeLabelTranslation(attribute_code=attribute_code, language_code=language_code)
        session.add(row)
    row.label = label
    session.flush()
    return row


def upsert_variant_technical_attribute_value_translation(
    session: Session,
    payload: VariantTechnicalAttributeValueTranslationUpsert,
) -> VariantTechnicalAttributeValueTranslation:
    attribute = session.get(VariantTechnicalAttribute, int(payload.technical_attribute_id))
    if attribute is None:
        raise ValueError("Technisches Attribut nicht gefunden.")
    language_code = _clean_text(payload.language_code)
    value_text = _clean_text(payload.value_text)
    if not language_code or not value_text:
        raise ValueError("Sprache und übersetzter Wert sind Pflicht.")
    row = session.get(VariantTechnicalAttributeValueTranslation, int(payload.id)) if payload.id else None
    if row is None:
        row = session.scalar(
            select(VariantTechnicalAttributeValueTranslation).where(
                VariantTechnicalAttributeValueTranslation.technical_attribute_id == attribute.id,
                VariantTechnicalAttributeValueTranslation.language_code == language_code,
            )
        )
    if row is None:
        row = VariantTechnicalAttributeValueTranslation(technical_attribute_id=attribute.id, language_code=language_code)
        session.add(row)
    row.value_text = value_text
    session.flush()
    return row


def list_technical_attribute_label_translations(session: Session) -> list[dict]:
    stmt = select(TechnicalAttributeLabelTranslation).order_by(
        TechnicalAttributeLabelTranslation.attribute_code.asc(),
        TechnicalAttributeLabelTranslation.language_code.asc(),
    )
    return [_serialize_technical_attribute_label_translation(row) for row in session.scalars(stmt)]


def list_variant_technical_attribute_value_translations(session: Session) -> list[dict]:
    stmt = (
        select(VariantTechnicalAttributeValueTranslation)
        .options(
            joinedload(VariantTechnicalAttributeValueTranslation.technical_attribute)
            .joinedload(VariantTechnicalAttribute.variant)
            .joinedload(ProductVariant.product)
        )
        .order_by(VariantTechnicalAttributeValueTranslation.language_code.asc(), VariantTechnicalAttributeValueTranslation.id.desc())
    )
    return [_serialize_variant_technical_attribute_value_translation(row) for row in session.scalars(stmt).unique()]
