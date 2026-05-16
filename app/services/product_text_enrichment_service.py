from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Product, ProductTranslation

TEXT_FIELDS = ("title", "short_description", "description", "seo_title", "seo_description", "slug")


@dataclass(frozen=True)
class TextEnrichmentOptions:
    only_missing: bool = True
    overwrite_existing: bool = False
    markdown: bool = False
    strip_html: bool = True
    collapse_blank_lines: bool = True
    markdown_bullets: bool = True
    structure_sections: bool = True
    remove_supplier_notes: bool = False
    remove_external_numbers: bool = False
    generate_seo: bool = False
    generate_slug: bool = False


def preview_product_text_enrichment(
    session: Session,
    product_ids: Iterable[int],
    *,
    source_locale: str,
    target_locales: Iterable[str],
    fields: Iterable[str],
    options: TextEnrichmentOptions,
) -> dict:
    ids = [int(product_id) for product_id in product_ids]
    selected_fields = [field for field in fields if field in TEXT_FIELDS]
    locales = [locale for locale in target_locales if locale]
    if not ids or not selected_fields or not locales:
        return {"products_checked": 0, "suggestions": [], "warnings": ["Produkte, Felder und Zielsprachen müssen gewählt sein."]}

    products = list(
        session.scalars(
            select(Product)
            .where(Product.id.in_(ids))
            .options(selectinload(Product.translations))
            .order_by(Product.id.asc())
        )
    )
    rows: list[dict] = []
    warnings: list[str] = []
    for product in products:
        source_values = _field_values(product, source_locale)
        for target_locale in locales:
            target_values = _field_values(product, target_locale)
            for field in selected_fields:
                current_value = target_values.get(field) or ""
                if options.only_missing and current_value.strip():
                    rows.append(_row(product, target_locale, field, current_value, "", "unverändert", "Bestehender Wert bleibt erhalten.", source_locale))
                    continue
                candidate = _build_candidate(session, product, field, source_values, target_values, options, source_locale, target_locale)
                if not candidate:
                    rows.append(_row(product, target_locale, field, current_value, "", "leer", "Keine belastbare Quelle für Vorschlag gefunden.", source_locale))
                    continue
                status = "wird ergänzt" if not current_value.strip() else "wird überschrieben"
                if field == "description" and options.markdown and _plain_text(current_value) == _plain_text(candidate):
                    status = "nur formatiert"
                if current_value.strip() and not options.overwrite_existing:
                    status = "Konflikt"
                    message = "Bestehender Wert vorhanden; Überschreiben ist nicht aktiviert."
                else:
                    message = "Vorschlag kann übernommen werden."
                rows.append(_row(product, target_locale, field, current_value, candidate, status, message, source_locale))
    return {
        "products_checked": len(products),
        "suggestions": rows,
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def apply_product_text_enrichment(
    session: Session,
    rows: Iterable[dict],
    *,
    overwrite_existing: bool = False,
    created_by: str = "pim_gui",
) -> dict:
    applied = 0
    skipped = 0
    errors: list[str] = []
    safe_statuses = {"wird ergänzt", "wird überschrieben", "nur formatiert"}
    products: dict[int, Product] = {}

    for row in rows:
        try:
            product_id = int(row.get("product_id") or 0)
            locale = str(row.get("locale") or "").strip()
            field = str(row.get("field_name") or "").strip()
            value = str(row.get("suggested_value") or "").strip()
            status = str(row.get("status") or "").strip()
            current_value = str(row.get("current_value") or "").strip()
            if not product_id or not locale or field not in TEXT_FIELDS or not value:
                skipped += 1
                continue
            if status not in safe_statuses:
                skipped += 1
                continue
            if current_value and not overwrite_existing and status != "wird ergänzt":
                skipped += 1
                continue
            product = products.get(product_id)
            if product is None:
                product = session.scalar(
                    select(Product)
                    .where(Product.id == product_id)
                    .options(selectinload(Product.translations))
                )
                if product is None:
                    skipped += 1
                    continue
                products[product_id] = product
            _set_locale_field(session, product, locale, field, value, created_by=created_by)
            applied += 1
        except Exception as exc:  # pragma: no cover - defensive per-row protection
            skipped += 1
            errors.append(str(exc))
    session.flush()
    return {"applied_count": applied, "skipped_count": skipped, "errors": errors}


def _field_values(product: Product, locale: str) -> dict[str, str]:
    translation = _translation_for(product, locale)
    if translation is not None:
        return {
            "title": translation.title or product.title or "",
            "short_description": translation.short_description or "",
            "description": translation.description or "",
            "seo_title": translation.seo_title or "",
            "seo_description": translation.seo_description or "",
            "slug": translation.slug or "",
        }
    if locale != product.source_language:
        return {
            "title": "",
            "short_description": "",
            "description": "",
            "seo_title": "",
            "seo_description": "",
            "slug": "",
        }
    return {
        "title": product.title or "",
        "short_description": "",
        "description": product.description or "",
        "seo_title": "",
        "seo_description": "",
        "slug": product.handle or "",
    }


def _translation_for(product: Product, locale: str) -> ProductTranslation | None:
    for translation in product.translations:
        if translation.language_code == locale:
            return translation
    return None


def _build_candidate(
    session: Session,
    product: Product,
    field: str,
    source_values: dict[str, str],
    target_values: dict[str, str],
    options: TextEnrichmentOptions,
    source_locale: str,
    target_locale: str,
) -> str:
    same_locale = source_locale == target_locale
    base_title = target_values.get("title") or source_values.get("title") or product.title or ""
    text_context = (
        target_values.get("short_description")
        or source_values.get("short_description")
        or target_values.get("description")
        or source_values.get("description")
        or ""
    )
    if field == "title":
        source = target_values.get("title") or ((source_values.get("title") or product.title) if same_locale else "")
        return _enrich_short_title(_clean_inline(source, options), text_context)
    if field == "short_description":
        source = target_values.get("short_description") or target_values.get("description")
        if same_locale:
            source = source or source_values.get("short_description") or source_values.get("description")
        return _short_description(source or "", max_chars=250)
    if field == "description":
        source = target_values.get("description")
        if same_locale:
            source = source or source_values.get("description") or source_values.get("short_description") or ""
        source = _clean_multiline(source, options)
        return format_markdown_description(source) if options.markdown else source
    if field == "seo_title":
        source = target_values.get("seo_title") or target_values.get("title") or (base_title if same_locale else "")
        source = _enrich_short_title(_clean_inline(source, options), text_context)
        return _seo_title(source)
    if field == "seo_description":
        source = target_values.get("seo_description") or target_values.get("short_description") or target_values.get("description")
        if same_locale:
            source = source or source_values.get("short_description") or source_values.get("description")
        return _seo_description(source or "")
    if field == "slug":
        source = target_values.get("seo_title") or target_values.get("title") or (base_title if same_locale else "")
        source = _enrich_short_title(_clean_inline(source, options), text_context)
        if not source:
            return ""
        return unique_slug(session=session, product=product, locale=target_locale, source=source)
    return ""


def _set_locale_field(session: Session, product: Product, locale: str, field: str, value: str, *, created_by: str) -> None:
    is_base_locale = locale == product.source_language
    if is_base_locale:
        if field == "title":
            product.title = value
        elif field == "description":
            product.description = value
        elif field == "slug":
            product.handle = _unique_product_handle(session, value, product.id)

    translation = _translation_for(product, locale)
    if translation is None:
        translation = ProductTranslation(
            product_id=product.id,
            language_code=locale,
            title=product.title or value or product.sku,
            translation_status="draft",
            provider="pim-text-enrichment",
            model=created_by,
        )
        session.add(translation)
        product.translations.append(translation)
    if field == "title":
        translation.title = value
    elif field == "slug":
        translation.slug = _unique_translation_slug(session, value, product.id, locale)
    else:
        setattr(translation, field, value)
    translation.translation_status = "draft"
    translation.source_language_code = locale
    translation.provider = "pim-text-enrichment"
    translation.model = created_by


def format_markdown_description(text: str) -> str:
    cleaned = _clean_multiline(text, TextEnrichmentOptions())
    if not cleaned:
        return ""
    cleaned = _normalize_section_labels(cleaned)
    if _has_markdown_structure(cleaned):
        return cleaned
    sentences = _split_sentences(cleaned)
    if not sentences:
        return cleaned
    intro = sentences[0]
    rest = sentences[1:]
    if len(cleaned) < 180:
        bullets = [_normalize_bullet_part(part) for part in rest]
        bullets = [part for part in bullets if part and part.lower() != intro.lower()]
        if not bullets:
            return intro
        return intro + "\n\n" + "\n".join(f"- {part}" for part in bullets[:5])

    properties: list[str] = []
    technical: list[str] = []
    notes: list[str] = []
    for sentence in rest:
        lower = sentence.lower()
        if any(token in lower for token in ("material", "polyester", "baumwolle", "silikon", "molton", "%", "cm", "kg", "abmessung", "ausführung")):
            technical.append(sentence)
        elif any(token in lower for token in ("preis", "hinweis", "warn", "anwenden", "anwendung", "geeignet", "verwenden")):
            notes.append(sentence)
        else:
            properties.append(sentence)
    blocks = [intro]
    if properties:
        blocks.append("### Eigenschaften\n\n" + "\n".join(f"- {_normalize_bullet_part(item)}" for item in properties))
    if technical:
        blocks.append("### Material / Technische Angaben\n\n" + "\n".join(f"- {_normalize_bullet_part(item)}" for item in technical))
    if notes:
        blocks.append("### Anwendung / Hinweis\n\n" + "\n".join(f"- {_normalize_bullet_part(item)}" for item in notes))
    return "\n\n".join(block for block in blocks if block.strip())


def unique_slug(session: Session | None, product: Product, locale: str, source: str) -> str:
    base = _make_slug(source)
    if not base:
        base = _make_slug(product.sku or f"produkt-{product.id}")
    if session is None:
        return base
    if locale == product.source_language:
        return _unique_product_handle(session, base, product.id)
    return _unique_translation_slug(session, base, product.id, locale)


def _unique_product_handle(session: Session, slug: str, product_id: int) -> str:
    base = _make_slug(slug)
    candidate = base
    index = 2
    while session.scalar(select(Product.id).where(Product.handle == candidate, Product.id != product_id)) is not None:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _unique_translation_slug(session: Session, slug: str, product_id: int, locale: str) -> str:
    base = _make_slug(slug)
    candidate = base
    index = 2
    while session.scalar(
        select(ProductTranslation.id).where(
            ProductTranslation.language_code == locale,
            ProductTranslation.slug == candidate,
            ProductTranslation.product_id != product_id,
        )
    ) is not None:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _make_slug(value: str) -> str:
    transliterated = (
        str(value or "")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    return slugify(transliterated, separator="-").strip("-")


def _row(product: Product, locale: str, field: str, current: str, suggestion: str, status: str, message: str, source_locale: str) -> dict:
    return {
        "product_id": product.id,
        "sku": product.sku,
        "product_name": product.title,
        "locale": locale,
        "field_name": field,
        "current_value": current,
        "suggested_value": suggestion,
        "source": f"Produktdaten / {source_locale}",
        "status": status,
        "action": "übernehmen" if status in {"wird ergänzt", "wird überschrieben", "nur formatiert"} else "prüfen",
        "message": message,
    }


def _clean_inline(text: str, options: TextEnrichmentOptions) -> str:
    value = re.sub(r"\s+", " ", _clean_multiline(text, options)).strip()
    if options.remove_external_numbers:
        value = _remove_trailing_article_codes(value)
    return value


def _clean_multiline(text: str, options: TextEnrichmentOptions) -> str:
    value = str(text or "")
    value = html.unescape(value)
    if options.strip_html:
        value = re.sub(r"<\s*br\s*/?>", "\n", value, flags=re.I)
        value = re.sub(r"</\s*(p|div|h[1-6]|li)\s*>", "\n", value, flags=re.I)
        value = re.sub(r"<\s*li[^>]*>", "- ", value, flags=re.I)
        value = re.sub(r"<[^>]+>", " ", value)
    if options.remove_supplier_notes:
        value = re.sub(r"(?im)^\s*(lieferant|supplier|hersteller)\s*:.*$", "", value)
    if options.remove_external_numbers:
        value = re.sub(r"\b(?:art\.?|artikel|ref\.?)\s*[-#:]*\s*[A-Z0-9][A-Z0-9._/-]{3,}\b", "", value, flags=re.I)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    if options.collapse_blank_lines:
        value = re.sub(r"\n{3,}", "\n\n", value)
    return "\n".join(line.strip() for line in value.splitlines()).strip()


def _remove_trailing_article_codes(text: str) -> str:
    value = str(text or "").strip()
    # Entfernt typische Lieferanten-/SKU-Codes am Titelende, z. B. "D1 Sweat A15-030A".
    # Nicht global im Text löschen, damit technische Angaben und echte Produktnamen erhalten bleiben.
    value = re.sub(r"(?:\s+[-–—|/]?\s*)\b[A-Z]{1,4}\d{1,4}(?:[-_/]?\d{2,4})+[A-Z0-9]{0,4}\b\s*$", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" -–—|/")


def _plain_text(text: str) -> str:
    value = re.sub(r"(?m)^#{1,6}\s+", "", str(text or ""))
    value = re.sub(r"(?m)^\s*[-*]\s+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _short_description(text: str, *, max_chars: int) -> str:
    cleaned = _plain_text(_clean_multiline(text, TextEnrichmentOptions()))
    if len(cleaned) <= max_chars:
        return cleaned
    sentence_match = re.match(r"^(.{80,250}?[.!?])(?:\s|$)", cleaned)
    if sentence_match:
        return sentence_match.group(1).strip()
    truncated = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return truncated.rstrip(".,;:")


def _seo_title(text: str) -> str:
    return _truncate_words(_clean_inline(text, TextEnrichmentOptions()), 60)


def _seo_description(text: str) -> str:
    return _truncate_words(_plain_text(_clean_multiline(text, TextEnrichmentOptions())), 160)


def _truncate_words(text: str, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")


def _enrich_short_title(title: str, context: str) -> str:
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    product_type = _extract_product_type(context)
    if not cleaned or not product_type:
        return cleaned
    if product_type.lower() in cleaned.lower():
        return cleaned
    if len(cleaned) <= 24 or re.match(r"^[A-Z0-9][A-Z0-9\s-]{1,24}$", cleaned):
        return f"{cleaned} {product_type}".strip()
    return cleaned


def _extract_product_type(text: str) -> str:
    cleaned = _plain_text(_clean_multiline(text, TextEnrichmentOptions())).lower()
    patterns = [
        (r"\b(stain remover|spotting agent|pre-spotter|softener|detergent|neutraliser|neutralizer|filter cartridge)\b", None),
        (r"\b(fleckenentferner|detachiermittel|vordetachiermittel|vorentflecker|weichspüler|reinigungsverstärker|reinigungsverstaerker|neutralisator|filterkartusche)\b", None),
        (r"\b(adoucissant|détachant|detachant|neutralisant|cartouche filtrante)\b", None),
        (r"\b(ammorbidente|smacchiatore|neutralizzante|cartuccia filtro)\b", None),
    ]
    for pattern, _ in patterns:
        match = re.search(pattern, cleaned, flags=re.I)
        if match:
            value = match.group(1)
            if value == "reinigungsverstaerker":
                return "Reinigungsverstärker"
            return " ".join(part.capitalize() if part not in {"for", "de", "à"} else part for part in value.split())
    return ""


def _split_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.!?])\s+|;\s+|\s+\+\s+", normalized)
    return [part.strip(" .") + "." for part in parts if part.strip(" .")]


def _normalize_bullet_part(text: str) -> str:
    return str(text or "").strip().lstrip("-* ").rstrip(".")


def _has_markdown_structure(text: str) -> bool:
    return bool(re.search(r"(?m)^#{1,6}\s+\S|^\s*[-*]\s+\S|^\s*\d+\.\s+\S", text))


def _normalize_section_labels(text: str) -> str:
    labels = {
        "anwendung": "Anwendung",
        "hinweis": "Hinweis",
        "hinweise": "Hinweise",
        "warnhinweis": "Warnhinweis",
        "warning": "Warning",
        "warnings": "Warnings",
        "how to use": "How to use",
        "how-to-use": "How to use",
        "ingredients": "Ingredients",
        "inhaltsstoffe": "Inhaltsstoffe",
        "zusammensetzung": "Zusammensetzung",
        "material": "Material",
        "eigenschaften": "Eigenschaften",
        "properties": "Properties",
        "specifications": "Specifications",
        "technical data": "Technical data",
        "function": "Function",
        "funktion": "Funktion",
        "packaging": "Packaging",
        "verpackung": "Verpackung",
    }

    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        key = stripped.rstrip(":").lower()
        if stripped.endswith(":") and key in labels:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"### {labels[key]}")
            continue
        lines.append(line)
    return "\n".join(lines).strip()
