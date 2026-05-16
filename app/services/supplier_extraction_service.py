from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product, ProductAssetCandidate, ProductEnrichmentCandidate
from app.models import ScrapedData
from app.suppliers.base import SupplierAssetCandidate, SupplierExtractionResult
from app.suppliers.registry import find_extractor_for_url
from app.services.product_translation_service import _call_openai_json, get_translation_config_status


DEFAULT_TARGET_LOCALES = ("de-CH", "fr-CH", "it-CH")


SECTION_FIELD_MAP = {
    "SKU": "sku",
    "Description": "description",
    "Specifications": "specifications",
    "How To Use": "how_to_use",
    "Quantity for use": "quantity_for_use",
    "Warning": "warning",
    "Ingredients": "ingredients",
    "Ingredient Search": "ingredient_search",
    "Function": "function",
    "Packaging": "packaging",
}

LOCALIZATION_PROMPTS = {
    "de-CH": "Übersetze und lokalisiere den folgenden englischen Produkttext ins Schweizer Hochdeutsch für einen B2B-Shop. Verwende sachliche, klare Sprache. Schreibe ss statt ß. Keine erfundenen technischen Daten, Sicherheitsangaben, Gefahrenhinweise oder Leistungsversprechen hinzufügen. Wenn eine Angabe unklar ist, markiere sie mit [PRÜFEN].",
    "fr-CH": "Traduis et localise le texte produit anglais en français suisse pour une boutique B2B. Utilise un style clair, professionnel et factuel. N'ajoute aucune donnée technique, information de sécurité ou promesse de performance non présente dans la source. Marque les points incertains avec [À VÉRIFIER].",
    "it-CH": "Traduci e localizza il testo prodotto inglese in italiano svizzero per uno shop B2B. Usa uno stile chiaro, professionale e concreto. Non aggiungere dati tecnici, indicazioni di sicurezza o promesse di prestazione non presenti nella fonte. Segna i punti incerti con [VERIFICARE].",
}


def extract_supplier_product_data(
    session: Session,
    product_id: int,
    url: str,
    *,
    target_locales: list[str] | None = None,
    translate: bool = False,
) -> dict[str, object]:
    product = session.get(Product, product_id)
    if product is None:
        return {"status": "error", "message": "Produkt nicht gefunden", "candidates": [], "asset_candidates": []}
    return extract_supplier_product_data_for_product(
        session,
        product,
        url,
        target_locales=target_locales,
        translate=translate,
    )


def extract_supplier_product_data_for_product(
    session: Session,
    product: Product,
    url: str,
    *,
    target_locales: list[str] | None = None,
    translate: bool = False,
) -> dict[str, object]:
    extractor = find_extractor_for_url(url)
    if not hasattr(extractor, "extract_from_html"):
        return {"status": "unsupported", "message": "Für diese Quelle ist noch kein spezifischer Lieferanten-Extractor vorhanden.", "candidates": [], "asset_candidates": []}
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "PIM-PAM Supplier Enrichment/1.0"},
    )
    response.raise_for_status()
    result: SupplierExtractionResult = extractor.extract_from_html(url, response.text)  # type: ignore[attr-defined]
    candidates, asset_candidates = save_enrichment_candidates(
        session,
        product,
        result,
        target_locales=target_locales or list(DEFAULT_TARGET_LOCALES),
        translate=translate,
    )
    return {
        "status": "extracted",
        "supplier_key": result.supplier_key,
        "source_url": result.source_url,
        "detected_language": result.detected_language,
        "warnings": result.warnings,
        "candidates": [serialize_enrichment_candidate(row) for row in candidates],
        "asset_candidates": [serialize_asset_candidate(row) for row in asset_candidates],
    }


def save_enrichment_candidates(
    session: Session,
    product: Product,
    result: SupplierExtractionResult,
    *,
    target_locales: list[str],
    translate: bool = False,
) -> tuple[list[ProductEnrichmentCandidate], list[ProductAssetCandidate]]:
    source_candidates: list[ProductEnrichmentCandidate] = []
    if result.short_description:
        source_candidates.append(
            _create_text_candidate(
                product,
                result,
                field_name="short_description",
                section_name="Description",
                source_value=result.short_description,
                target_locale=None,
                suggested_value=None,
                status="new",
                warning=_warning_for_product(product, "Description"),
            )
        )
        for target_locale in target_locales:
            translated = None
            status = "suggested" if _language_base(target_locale) == _language_base(result.detected_language) else "needs_translation"
            warning = None
            if status == "needs_translation":
                warning = f"Quelle ist {result.detected_language or '-'}, Ziel ist {target_locale}; nicht direkt übernehmen."
                if translate:
                    translated = _translate_text(result.short_description, target_locale)
                    if translated:
                        status = "translated"
            source_candidates.append(
                _create_text_candidate(
                    product,
                    result,
                    field_name="short_description",
                    section_name="Description",
                    source_value=result.short_description,
                    target_locale=target_locale,
                    suggested_value=translated if translated else (result.short_description if status == "suggested" else None),
                    status=status,
                    warning=warning or _warning_for_product(product, "Description"),
                )
            )
    for section_name, field_name in SECTION_FIELD_MAP.items():
        value = _value_for_section(result, section_name)
        if not value:
            continue
        source_candidates.append(
            _create_text_candidate(
                product,
                result,
                field_name=field_name,
                section_name=section_name,
                source_value=value,
                target_locale=None,
                suggested_value=None,
                status="new",
                warning=_warning_for_product(product, section_name),
            )
        )
        for target_locale in target_locales:
            translated = None
            status = "suggested" if _language_base(target_locale) == _language_base(result.detected_language) else "needs_translation"
            warning = None
            if status == "needs_translation":
                warning = f"Quelle ist {result.detected_language or '-'}, Ziel ist {target_locale}; nicht direkt übernehmen."
                if translate:
                    translated = _translate_text(value, target_locale)
                    if translated:
                        status = "translated"
            source_candidates.append(
                _create_text_candidate(
                    product,
                    result,
                    field_name=field_name,
                    section_name=section_name,
                    source_value=value,
                    target_locale=target_locale,
                    suggested_value=translated if translated else (value if status == "suggested" else None),
                    status=status,
                    warning=warning or _warning_for_product(product, section_name),
                )
            )
    if result.specifications:
        _append_target_candidates(
            source_candidates,
            product,
            result,
            field_name="specifications_text",
            section_name="Specifications",
            value=result.specifications,
            target_locales=target_locales,
            translate=translate,
        )
    feature_parts = [
        f"How To Use: {result.how_to_use}" if result.how_to_use else None,
        f"Quantity for use:\n{result.quantity_for_use}" if result.quantity_for_use else None,
        f"Warning: {result.warning}" if result.warning else None,
        f"Ingredients: {result.ingredients}" if result.ingredients else None,
        f"Ingredient Search: {result.ingredient_search}" if result.ingredient_search else None,
        f"Function: {result.function}" if result.function else None,
    ]
    feature_text = "\n".join(part for part in feature_parts if part)
    if feature_text:
        _append_target_candidates(
            source_candidates,
            product,
            result,
            field_name="technical_features_text",
            section_name="Technical Features",
            value=feature_text,
            target_locales=target_locales,
            translate=translate,
        )
    asset_candidates: list[ProductAssetCandidate] = []
    for item in [*result.pdfs, *result.images]:
        asset_candidates.append(
            ProductAssetCandidate(
                product_id=product.id,
                supplier_key=result.supplier_key,
                source_url=result.source_url,
                asset_url=item.asset_url,
                asset_type=item.asset_type,
                title=item.title,
                filename=item.filename,
                language=item.language or result.detected_language,
                region=item.region,
                status="new",
            )
        )
    session.add_all([*source_candidates, *asset_candidates])
    session.flush()
    return source_candidates, asset_candidates


def _append_target_candidates(
    rows: list[ProductEnrichmentCandidate],
    product: Product,
    result: SupplierExtractionResult,
    *,
    field_name: str,
    section_name: str,
    value: str,
    target_locales: list[str],
    translate: bool,
) -> None:
    rows.append(
        _create_text_candidate(
            product,
            result,
            field_name=field_name,
            section_name=section_name,
            source_value=value,
            target_locale=None,
            suggested_value=None,
            status="new",
            warning=_warning_for_product(product, section_name),
        )
    )
    for target_locale in target_locales:
        translated = None
        status = "suggested" if _language_base(target_locale) == _language_base(result.detected_language) else "needs_translation"
        warning = None
        if status == "needs_translation":
            warning = f"Quelle ist {result.detected_language or '-'}, Ziel ist {target_locale}; nicht direkt übernehmen."
            if translate:
                translated = _translate_text(value, target_locale)
                if translated:
                    status = "translated"
        rows.append(
            _create_text_candidate(
                product,
                result,
                field_name=field_name,
                section_name=section_name,
                source_value=value,
                target_locale=target_locale,
                suggested_value=translated if translated else (value if status == "suggested" else None),
                status=status,
                warning=warning or _warning_for_product(product, section_name),
            )
        )


def save_scraped_enrichment_candidates(
    session: Session,
    product: Product,
    scraped: ScrapedData,
    *,
    target_locales: list[str] | None = None,
    translate: bool = False,
) -> tuple[list[ProductEnrichmentCandidate], list[ProductAssetCandidate]]:
    payload = scraped.extra_fields.get("supplier_extraction_result") if scraped.extra_fields else None
    if not isinstance(payload, dict):
        return [], []
    payload = dict(payload)
    payload["pdfs"] = [SupplierAssetCandidate(**item) for item in payload.get("pdfs", []) if isinstance(item, dict)]
    payload["images"] = [SupplierAssetCandidate(**item) for item in payload.get("images", []) if isinstance(item, dict)]
    result = SupplierExtractionResult(**payload)
    return save_enrichment_candidates(
        session,
        product,
        result,
        target_locales=target_locales or [product.source_language or "de-CH"],
        translate=translate,
    )


def serialize_enrichment_candidate(row: ProductEnrichmentCandidate) -> dict[str, object]:
    return {
        "id": row.id,
        "product_id": row.product_id,
        "supplier_key": row.supplier_key,
        "source_url": row.source_url,
        "source_domain": row.source_domain,
        "source_language": row.source_language,
        "source_locale": row.source_locale,
        "target_locale": row.target_locale,
        "field_name": row.field_name,
        "section_name": row.section_name,
        "source_value": row.source_value,
        "suggested_value": row.suggested_value,
        "confidence": float(row.confidence or 0),
        "status": row.status,
        "warning": row.warning,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def serialize_asset_candidate(row: ProductAssetCandidate) -> dict[str, object]:
    return {
        "id": row.id,
        "product_id": row.product_id,
        "supplier_key": row.supplier_key,
        "source_url": row.source_url,
        "asset_url": row.asset_url,
        "asset_type": row.asset_type,
        "title": row.title,
        "filename": row.filename,
        "language": row.language,
        "region": row.region,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def latest_candidates_for_product(session: Session, product_id: int, target_locale: str | None = None) -> list[ProductEnrichmentCandidate]:
    stmt = select(ProductEnrichmentCandidate).where(ProductEnrichmentCandidate.product_id == product_id)
    if target_locale:
        stmt = stmt.where(ProductEnrichmentCandidate.target_locale == target_locale)
    stmt = stmt.order_by(ProductEnrichmentCandidate.updated_at.desc(), ProductEnrichmentCandidate.id.desc())
    return list(session.scalars(stmt))


def _create_text_candidate(
    product: Product,
    result: SupplierExtractionResult,
    *,
    field_name: str,
    section_name: str,
    source_value: str,
    target_locale: str | None,
    suggested_value: str | None,
    status: str,
    warning: str | None,
) -> ProductEnrichmentCandidate:
    return ProductEnrichmentCandidate(
        product_id=product.id,
        supplier_key=result.supplier_key,
        source_url=result.source_url,
        source_domain=urlparse(result.source_url).netloc,
        source_language=result.detected_language,
        source_locale=result.source_locale,
        target_locale=target_locale,
        field_name=field_name,
        section_name=section_name,
        source_value=source_value,
        suggested_value=suggested_value,
        confidence=Decimal(str(result.confidence or 0)),
        status=status,
        warning=warning,
    )


def _value_for_section(result: SupplierExtractionResult, section_name: str) -> str | None:
    return {
        "SKU": result.sku,
        "Description": result.description,
        "Specifications": result.specifications,
        "How To Use": result.how_to_use,
        "Quantity for use": result.quantity_for_use,
        "Warning": result.warning,
        "Ingredients": result.ingredients,
        "Ingredient Search": result.ingredient_search,
        "Function": result.function,
        "Packaging": result.packaging,
    }.get(section_name)


def _warning_for_product(product: Product, section_name: str) -> str | None:
    if product.is_chemical and section_name in {"Ingredients", "Description", "Specifications"}:
        return "Chemieprodukt erkannt: Marketingtexte und Inhaltsstoffe wurden als Kandidaten gespeichert. Sicherheitsdaten, H-/P-Sätze, WGK und Lagerklasse werden nicht automatisch daraus abgeleitet."
    return None


def _translate_text(value: str, target_locale: str) -> str | None:
    config = get_translation_config_status()
    if config["provider"] != "openai" or not config["enabled"]:
        return None
    prompt = LOCALIZATION_PROMPTS.get(target_locale) or f"Translate the following product text to {target_locale}. Do not add facts."
    payload = _call_openai_json(
        "Du bist ein professioneller PIM/PAM-Übersetzer für B2B-Produkttexte. Gib nur gültiges JSON zurück.",
        f"{prompt}\n\nText:\n{value}\n\nAntwortformat: {{\"text\":\"\"}}",
        str(config["model"]),
        (os.getenv("OPENAI_API_KEY") or "").strip(),
    )
    text = payload.get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _language_base(value: str | None) -> str:
    return str(value or "").strip().split("-", 1)[0].lower()
