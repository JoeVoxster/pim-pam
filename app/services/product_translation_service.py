from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from string import Template
from typing import Any

import requests
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from slugify import slugify

from app.db.models import Language, Product, ProductCategoryAssignment, ProductTranslation, ProductVariant, TranslationPrompt, VariantTranslation
from app.schemas.pim import ProductTranslationCreate, VariantTranslationCreate
from app.services.pim_service import create_or_update_translation, create_or_update_variant_translation


LOGGER = logging.getLogger(__name__)
load_dotenv()

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5-mini"
TRANSLATION_STATUSES = {"draft", "generated", "reviewed", "published", "failed"}
DEFAULT_LANGUAGES = [
    ("de-CH", "Deutsch Schweiz", True),
    ("de", "Deutsch", False),
    ("en", "Englisch", False),
    ("fr", "Französisch", False),
    ("it", "Italienisch", False),
    ("es", "Spanisch", False),
]
LANGUAGE_NAMES = {
    "de": "Deutsch",
    "de-DE": "Deutsch Deutschland",
    "de-CH": "Deutsch Schweiz",
    "en": "Englisch",
    "en-US": "Englisch USA",
    "en-GB": "Englisch UK",
    "fr": "Französisch",
    "fr-CH": "Französisch Schweiz",
    "it": "Italienisch",
    "it-CH": "Italienisch Schweiz",
    "es": "Spanisch",
}
DEFAULT_SYSTEM_PROMPT = "Du bist ein professioneller PIM/PAM-Übersetzer für E-Commerce-Produktdaten."
VARIANT_TRANSLATION_PROMPT_TEMPLATE = """Du bist ein professioneller PIM/PAM-Übersetzer für E-Commerce-Produktvarianten.

Übersetze die folgenden Variantendaten von {{sourceLanguage}} nach {{targetLanguage}}.

Wichtig:
- Gib nur gültiges JSON zurück.
- Keine Markdown-Erklärung.
- Keine zusätzlichen Kommentare.
- Erhalte SKU, Modellnamen, Marken, Zahlen, Masseinheiten und Gebindegrössen korrekt.
- Übersetze nur sinnvolle Texte: Variantentitel, Optionslabel und Gebindelabel.
- Falls ein Feld leer ist und fachlich nicht ableitbar ist, gib für dieses Feld einen leeren String zurück.

Produkt: {{productTitle}}
SKU Produkt: {{productSku}}
Variante SKU: {{variantSku}}
Variantentitel: {{variantTitle}}
Optionsname: {{optionName}}
Optionswert: {{optionValue}}
Gebinde/Packaging: {{packaging}}

Antwortformat:
{
  "title": "",
  "optionLabelOverride": "",
  "packageLabel": ""
}
"""
DEFAULT_PROMPT_TEMPLATE = """Du bist ein professioneller PIM/PAM-Übersetzer für E-Commerce-Produktdaten.

Übersetze die folgenden Produktdaten von {{sourceLanguage}} nach {{targetLanguage}}.

Wichtig:
- Gib nur gültiges JSON zurück.
- Keine Markdown-Erklärung.
- Keine zusätzlichen Kommentare.
- Erhalte technische Begriffe, Modellnamen, Marken, Masseinheiten und Artikelnummern korrekt.
- Schreibe natürlich und verkaufsstark.
- SEO Title und SEO Description sollen für Suchmaschinen optimiert sein.
- Die SEO Description soll kurz, sauber und nicht zu lang sein.
- Falls Description Markdown enthält: Erhalte alle Markdown-Elemente exakt. Übersetze nur sichtbaren Text. Entferne keine Überschriftenzeichen, Bulletpoints, Leerzeilen, Links oder Listen. Gib kein HTML zurück.
- Markdown-Regeln: #/##/###/#### bleiben Überschriften, -/* bleiben Bulletpoints, nummerierte Listen bleiben nummeriert, Link-URLs bleiben unverändert, Codeblöcke und Inline-Code nicht übersetzen.
- Falls Short Description leer ist, erstelle aus Titel und Beschreibung eine kurze, saubere Kurzbeschreibung.
- Erzeuge einen SEO-freundlichen Slug in der Zielsprache: klein geschrieben, Bindestriche, keine Leerzeichen.
- Falls andere Felder leer und fachlich nicht ableitbar sind, gib für dieses Feld einen leeren String zurück.

Eingabe:
Title: {{title}}
Short Description: {{shortDescription}}
Description: {{description}}
SEO Title: {{seoTitle}}
SEO Description: {{seoDescription}}
Slug: {{slug}}
Brand: {{brand}}
Category: {{category}}
Attributes: {{attributes}}

Antwortformat:
{
  "title": "",
  "shortDescription": "",
  "description": "",
  "seoTitle": "",
  "seoDescription": "",
  "slug": ""
}
"""


def get_translation_config_status() -> dict[str, object]:
    provider = (os.getenv("PIM_TRANSLATION_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    model = os.getenv("PIM_TRANSLATION_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    return {
        "enabled": bool((os.getenv("OPENAI_API_KEY") or "").strip()) if provider == "openai" else False,
        "provider": provider,
        "model": model,
    }


def ensure_languages(session: Session) -> list[Language]:
    existing = {row.code: row for row in session.scalars(select(Language))}
    discovered_codes = {
        str(code).strip()
        for code in session.scalars(select(Product.source_language).distinct())
        if str(code or "").strip()
    }
    discovered_codes.update(
        str(code).strip()
        for code in session.scalars(select(ProductTranslation.language_code).distinct())
        if str(code or "").strip()
    )
    configured_languages = list(DEFAULT_LANGUAGES)
    seeded_codes = {code for code, _name, _is_default in configured_languages}
    configured_languages.extend(
        (code, LANGUAGE_NAMES.get(code, code), False)
        for code in sorted(discovered_codes - seeded_codes)
    )
    for code, name, is_default in configured_languages:
        if code not in existing:
            row = Language(code=code, name=name, enabled=True, is_default=is_default)
            session.add(row)
            existing[code] = row
        elif code in discovered_codes and not existing[code].enabled:
            existing[code].enabled = True
    if any(row.code == "de-CH" for row in existing.values()):
        for row in existing.values():
            row.is_default = row.code == "de-CH"
    session.flush()
    ensure_translation_prompts(session)
    return list(session.scalars(select(Language).order_by(Language.is_default.desc(), Language.name.asc())))


def list_languages(session: Session, enabled_only: bool = False) -> list[dict]:
    ensure_languages(session)
    stmt = select(Language).order_by(Language.is_default.desc(), Language.name.asc())
    if enabled_only:
        stmt = stmt.where(Language.enabled.is_(True))
    return [
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "enabled": bool(row.enabled),
            "isDefault": bool(row.is_default),
            "createdAt": row.created_at.isoformat() if row.created_at else None,
            "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in session.scalars(stmt)
    ]


def ensure_translation_prompts(session: Session) -> None:
    language_codes = [row.code for row in session.scalars(select(Language))]
    existing = {row.language_code: row for row in session.scalars(select(TranslationPrompt))}
    for code in language_codes:
        if code not in existing:
            session.add(
                TranslationPrompt(
                    language_code=code,
                    prompt_template=DEFAULT_PROMPT_TEMPLATE,
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                )
            )
    session.flush()


def list_translation_prompts(session: Session) -> list[dict]:
    ensure_languages(session)
    ensure_translation_prompts(session)
    return [_serialize_prompt(row) for row in session.scalars(select(TranslationPrompt).order_by(TranslationPrompt.language_code.asc()))]


def get_translation_prompt(session: Session, language_code: str) -> TranslationPrompt:
    ensure_languages(session)
    code = _normalize_language_code(language_code)
    prompt = session.scalar(select(TranslationPrompt).where(TranslationPrompt.language_code == code))
    if prompt is None:
        prompt = TranslationPrompt(language_code=code, prompt_template=DEFAULT_PROMPT_TEMPLATE, system_prompt=DEFAULT_SYSTEM_PROMPT)
        session.add(prompt)
        session.flush()
    return prompt


def save_translation_prompt(session: Session, language_code: str, prompt_template: str, system_prompt: str | None = None) -> TranslationPrompt:
    prompt = get_translation_prompt(session, language_code)
    prompt.prompt_template = prompt_template.strip() or DEFAULT_PROMPT_TEMPLATE
    prompt.system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    session.flush()
    return prompt


def reset_translation_prompt(session: Session, language_code: str) -> TranslationPrompt:
    return save_translation_prompt(session, language_code, DEFAULT_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT)


def generate_product_translations(
    session: Session,
    product_ids: list[int],
    target_languages: list[str],
    *,
    source_language_code: str | None = None,
    overwrite_existing: bool = False,
    allow_original_overwrite: bool = False,
    include_variants: bool = False,
) -> dict[str, object]:
    config = get_translation_config_status()
    if config["provider"] != "openai":
        return {"status": "failed", "message": f"Provider nicht unterstützt: {config['provider']}", "results": []}
    if not config["enabled"]:
        return {"status": "failed", "message": "OPENAI_API_KEY ist nicht konfiguriert.", "results": []}
    if not product_ids:
        return {"status": "failed", "message": "Keine Produkte ausgewählt.", "results": []}
    normalized_languages = [_normalize_language_code(code) for code in target_languages if str(code or "").strip()]
    if not normalized_languages:
        return {"status": "failed", "message": "Keine Zielsprache ausgewählt.", "results": []}

    ensure_languages(session)
    results: list[dict[str, object]] = []
    for product_id in _unique_ints(product_ids):
        product = session.scalar(
            select(Product)
            .options(
                joinedload(Product.brand),
                joinedload(Product.translations),
                joinedload(Product.variants).joinedload(ProductVariant.translations),
                joinedload(Product.category_links).joinedload(ProductCategoryAssignment.category),
            )
            .where(Product.id == product_id)
        )
        if product is None:
            results.append({"product_id": product_id, "status": "failed", "message": "Produkt nicht gefunden"})
            continue
        source_code = source_language_code or product.source_language or "de"
        for target_code in normalized_languages:
            if target_code == source_code and not allow_original_overwrite:
                results.append({"product_id": product.id, "language_code": target_code, "status": "skipped", "message": "Originalsprache nicht überschrieben"})
                continue
            existing = _translation_for(product, target_code)
            if existing is not None and not overwrite_existing:
                results.append({"product_id": product.id, "language_code": target_code, "status": "skipped", "message": "Übersetzung existiert bereits"})
                continue
            try:
                prompt = get_translation_prompt(session, target_code)
                source_payload = product_translation_source_payload(product, source_code)
                rendered_prompt = render_translation_prompt(product, source_code, target_code, prompt.prompt_template)
                payload = call_product_translation_with_markdown_retry(
                    source_payload["description"],
                    system_prompt=prompt.system_prompt or DEFAULT_SYSTEM_PROMPT,
                    user_prompt=rendered_prompt,
                    model=str(config["model"]),
                    api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
                )
                create_or_update_translation(
                    session,
                    ProductTranslationCreate(
                        product_id=product.id,
                        language_code=target_code,
                        title=payload["title"],
                        short_description=_translation_short_description(payload),
                        description=payload["description"],
                        seo_title=payload["seoTitle"],
                        seo_description=payload["seoDescription"],
                        slug=_translation_slug(payload, product.handle or product.title),
                        translation_status="generated",
                        source_language_code=source_code,
                        provider=str(config["provider"]),
                        model=str(config["model"]),
                        prompt_used=rendered_prompt,
                    ),
                )
                if target_code == (product.source_language or "") and allow_original_overwrite:
                    product.title = payload["title"] or product.title
                    product.description = payload["description"] or product.description
                results.append({"product_id": product.id, "language_code": target_code, "status": "generated", "message": "Übersetzung gespeichert"})
            except Exception as exc:
                LOGGER.exception("Product translation failed for product %s language %s", product.id, target_code)
                if existing is not None:
                    existing.translation_status = "failed"
                results.append({"product_id": product.id, "language_code": target_code, "status": "failed", "message": str(exc)})
        if include_variants:
            for variant in product.variants:
                for target_code in normalized_languages:
                    if target_code == source_code and not allow_original_overwrite:
                        results.append({
                            "variant_id": variant.id,
                            "product_id": product.id,
                            "language_code": target_code,
                            "status": "skipped",
                            "message": "Originalsprache nicht überschrieben",
                        })
                        continue
                    existing_variant_translation = _variant_translation_for(variant, target_code)
                    if existing_variant_translation is not None and not overwrite_existing:
                        results.append({
                            "variant_id": variant.id,
                            "product_id": product.id,
                            "language_code": target_code,
                            "status": "skipped",
                            "message": "Varianten-Übersetzung existiert bereits",
                        })
                        continue
                    try:
                        rendered_prompt = render_variant_translation_prompt(variant, source_code, target_code)
                        variant_payload = _call_openai_variant_translation_json(
                            system_prompt=DEFAULT_SYSTEM_PROMPT,
                            user_prompt=rendered_prompt,
                            model=str(config["model"]),
                            api_key=(os.getenv("OPENAI_API_KEY") or "").strip(),
                        )
                        create_or_update_variant_translation(
                            session,
                            VariantTranslationCreate(
                                variant_id=variant.id,
                                language_code=target_code,
                                title=variant_payload["title"] or variant.variant_title or variant.sku,
                                option_label_override=variant_payload["optionLabelOverride"],
                                package_label=variant_payload["packageLabel"],
                            ),
                        )
                        results.append({
                            "variant_id": variant.id,
                            "product_id": product.id,
                            "language_code": target_code,
                            "status": "generated",
                            "message": "Varianten-Übersetzung gespeichert",
                        })
                    except Exception as exc:
                        LOGGER.exception("Variant translation failed for variant %s language %s", variant.id, target_code)
                        results.append({
                            "variant_id": variant.id,
                            "product_id": product.id,
                            "language_code": target_code,
                            "status": "failed",
                            "message": str(exc),
                        })
    session.flush()
    failed = sum(1 for row in results if row.get("status") == "failed")
    generated = sum(1 for row in results if row.get("status") == "generated")
    skipped = sum(1 for row in results if row.get("status") == "skipped")
    return {"status": "completed" if failed == 0 else "partial", "generated": generated, "failed": failed, "skipped": skipped, "results": results}


def render_translation_prompt(product: Product, source_code: str, target_code: str, prompt_template: str) -> str:
    source_payload = product_translation_source_payload(product, source_code)
    variables = {
        "sourceLanguage": source_code,
        "targetLanguage": target_code,
        "title": source_payload["title"],
        "shortDescription": source_payload["short_description"],
        "description": source_payload["description"],
        "seoTitle": source_payload["seo_title"],
        "seoDescription": source_payload["seo_description"],
        "slug": source_payload["slug"],
        "brand": product.brand.name if product.brand else "",
        "category": ", ".join(link.category.name for link in product.category_links if link.category) if product.category_links else "",
        "attributes": ", ".join(
            sorted({str(variant.option_value or variant.packaging) for variant in product.variants if (variant.option_value or variant.packaging)})
        ) if getattr(product, "variants", None) else "",
    }
    rendered = prompt_template
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    rendered = Template(rendered).safe_substitute(variables)
    if looks_like_markdown(source_payload["description"]):
        rendered = append_markdown_preservation_instructions(rendered)
    return rendered


def product_translation_source_payload(product: Product, source_code: str) -> dict[str, str]:
    source_code = _normalize_language_code(source_code)
    source_translation = _translation_for(product, source_code)
    if source_translation is not None:
        return {
            "title": source_translation.title or product.title or "",
            "short_description": source_translation.short_description or "",
            "description": source_translation.description or "",
            "seo_title": source_translation.seo_title or "",
            "seo_description": source_translation.seo_description or "",
            "slug": source_translation.slug or product.handle or "",
        }
    base_translation = _translation_for(product, product.source_language or "")
    return {
        "title": product.title or (base_translation.title if base_translation else "") or "",
        "short_description": (base_translation.short_description if base_translation else "") or "",
        "description": product.description or (base_translation.description if base_translation else "") or "",
        "seo_title": (base_translation.seo_title if base_translation else "") or "",
        "seo_description": (base_translation.seo_description if base_translation else "") or "",
        "slug": product.handle or (base_translation.slug if base_translation else "") or "",
    }


def append_markdown_preservation_instructions(prompt: str) -> str:
    marker = "Markdown-Strukturschutz"
    if marker in prompt:
        return prompt
    return (
        prompt.rstrip()
        + "\n\n"
        + f"{marker}:\n"
        + "- Das Feld Description enthält Markdown. Erhalte alle Markdown-Elemente exakt.\n"
        + "- Übersetze nur sichtbaren Text. Entferne keine Überschriftenzeichen, Bulletpoints, Leerzeilen, Links oder Listen.\n"
        + "- #/##/###/#### bleiben Überschriften mit gleicher Ebene.\n"
        + "- Bulletpoints mit - oder * bleiben Bulletpoints in gleicher Reihenfolge.\n"
        + "- Nummerierte Listen bleiben nummeriert.\n"
        + "- Link-URLs bleiben unverändert; nur Linktext übersetzen.\n"
        + "- Codeblöcke und Inline-Code nicht übersetzen.\n"
        + "- Gib kein HTML und keine Erklärung zurück.\n"
    )


def call_product_translation_with_markdown_retry(
    source_description: str,
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str,
) -> dict[str, str]:
    payload = _call_openai_translation_json(system_prompt=system_prompt, user_prompt=user_prompt, model=model, api_key=api_key)
    try:
        return enforce_markdown_translation_payload(source_description, payload)
    except ValueError:
        if not looks_like_markdown(source_description):
            raise
    retry_prompt = (
        append_markdown_preservation_instructions(user_prompt)
        + "\n\n"
        + "Die vorherige Antwort hat die Markdown-Struktur beschädigt. Wiederhole die Übersetzung und halte die Markdown-Struktur exakt ein. "
        + "Die Anzahl und Reihenfolge von Überschriften, Bulletpoints, nummerierten Listen, Links und Code muss identisch bleiben."
    )
    retry_payload = _call_openai_translation_json(system_prompt=system_prompt, user_prompt=retry_prompt, model=model, api_key=api_key)
    return enforce_markdown_translation_payload(source_description, retry_payload)


def enforce_markdown_translation_payload(source_description: str, payload: dict[str, str]) -> dict[str, str]:
    description = payload.get("description") or ""
    if not source_description.strip() or not description.strip():
        payload["shortDescription"] = sanitize_plain_short_description(payload.get("shortDescription") or "")
        return payload
    if looks_like_markdown(source_description):
        validate_markdown_translation(source_description, description)
    payload["shortDescription"] = sanitize_plain_short_description(payload.get("shortDescription") or "")
    return payload


def looks_like_markdown(value: str) -> bool:
    text = value or ""
    return bool(
        re.search(r"(?m)^#{1,4}\s+\S", text)
        or re.search(r"(?m)^\s*[-*]\s+\S", text)
        or re.search(r"(?m)^\s*\d+\.\s+\S", text)
        or re.search(r"\[[^\]]+\]\([^)]+\)", text)
        or "```" in text
    )


def validate_markdown_translation(source: str, translated: str) -> None:
    source_headings = markdown_heading_levels(source)
    translated_headings = markdown_heading_levels(translated)
    if source_headings != translated_headings:
        raise ValueError(f"Markdown-Überschriftenstruktur beschädigt: Quelle {source_headings}, Übersetzung {translated_headings}")
    source_bullets = markdown_bullet_markers(source)
    translated_bullets = markdown_bullet_markers(translated)
    if source_bullets != translated_bullets:
        raise ValueError(f"Markdown-Bulletstruktur beschädigt: Quelle {source_bullets}, Übersetzung {translated_bullets}")
    source_numbers = markdown_numbered_markers(source)
    translated_numbers = markdown_numbered_markers(translated)
    if source_numbers != translated_numbers:
        raise ValueError(f"Markdown-Nummernlisten beschädigt: Quelle {source_numbers}, Übersetzung {translated_numbers}")
    if markdown_code_fences(source) != markdown_code_fences(translated):
        raise ValueError("Markdown-Codeblock-Struktur beschädigt.")
    if markdown_inline_code(source) != markdown_inline_code(translated):
        raise ValueError("Inline-Code wurde verändert.")
    if markdown_link_urls(source) != markdown_link_urls(translated):
        raise ValueError("Markdown-Link-URLs wurden verändert.")
    if re.search(r"</?[a-z][^>]*>", translated, flags=re.I):
        raise ValueError("Übersetzung enthält HTML statt Markdown.")
    if re.search(r"(?i)\b(here is|hier ist|voici|ecco)\b.*\b(translation|übersetzung|traduction|traduzione)\b", translated):
        raise ValueError("Übersetzung enthält erklärenden KI-Begleittext.")


def markdown_heading_levels(value: str) -> list[str]:
    return re.findall(r"(?m)^(#{1,4})\s+\S", value or "")


def markdown_bullet_markers(value: str) -> list[str]:
    return re.findall(r"(?m)^(\s*[-*])\s+\S", value or "")


def markdown_numbered_markers(value: str) -> list[str]:
    return re.findall(r"(?m)^(\s*\d+\.)\s+\S", value or "")


def markdown_code_fences(value: str) -> int:
    return len(re.findall(r"(?m)^```", value or ""))


def markdown_inline_code(value: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", value or "")


def markdown_link_urls(value: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", value or "")


def sanitize_plain_short_description(value: str) -> str:
    text = re.sub(r"(?m)^#{1,6}\s+", "", value or "")
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= 250:
        return text
    words: list[str] = []
    for word in text.split():
        candidate = " ".join([*words, word])
        if len(candidate) > 250:
            break
        words.append(word)
    return " ".join(words).rstrip(" ,;:")


def render_variant_translation_prompt(variant: ProductVariant, source_code: str, target_code: str) -> str:
    source_variant = _variant_translation_for(variant, source_code)
    product_source = product_translation_source_payload(variant.product, source_code) if variant.product else {}
    variables = {
        "sourceLanguage": source_code,
        "targetLanguage": target_code,
        "productTitle": product_source.get("title") or (variant.product.title if variant.product else ""),
        "productSku": variant.product.sku if variant.product else "",
        "variantSku": variant.sku or "",
        "variantTitle": (source_variant.title if source_variant else None) or variant.variant_title or "",
        "optionName": variant.option_name or "",
        "optionValue": (source_variant.option_label_override if source_variant else None) or variant.option_value or "",
        "packaging": (source_variant.package_label if source_variant else None) or variant.packaging or "",
    }
    rendered = VARIANT_TRANSLATION_PROMPT_TEMPLATE
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return Template(rendered).safe_substitute(variables)


def _call_openai_translation_json(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, str]:
    parsed = _call_openai_json(system_prompt, user_prompt, model, api_key)
    return _validate_translation_payload(parsed)


def _call_openai_variant_translation_json(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, str]:
    parsed = _call_openai_json(system_prompt, user_prompt, model, api_key)
    return _validate_variant_translation_payload(parsed)


def _call_openai_json(system_prompt: str, user_prompt: str, model: str, api_key: str) -> dict[str, Any]:
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    request_payload = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if not str(model or "").startswith("gpt-5"):
        request_payload["temperature"] = 0.2
    response = requests.post(
        f"{base_url}/chat/completions",
        timeout=120,
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
    return _extract_json_object(raw_text)


def _validate_translation_payload(payload: dict[str, Any]) -> dict[str, str]:
    mapping = {
        "title": "title",
        "shortDescription": "shortDescription",
        "description": "description",
        "seoTitle": "seoTitle",
        "seoDescription": "seoDescription",
        "slug": "slug",
    }
    result: dict[str, str] = {}
    for key in mapping:
        value = payload.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"KI-Antwort Feld {key} ist kein String")
        result[key] = value
    return result


def _validate_variant_translation_payload(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("title", "optionLabelOverride", "packageLabel"):
        value = payload.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError(f"KI-Antwort Feld {key} ist kein String")
        result[key] = value
    return result


def _translation_short_description(payload: dict[str, str]) -> str:
    short_description = str(payload.get("shortDescription") or "").strip()
    if short_description:
        return short_description
    description = re.sub(r"\s+", " ", str(payload.get("description") or "")).strip()
    if description:
        return description[:197].rstrip() + "..." if len(description) > 200 else description
    return str(payload.get("title") or "").strip()


def _translation_slug(payload: dict[str, str], fallback: str | None = None) -> str:
    explicit_slug = str(payload.get("slug") or "").strip()
    slug_source = explicit_slug or payload.get("title") or fallback or "product"
    return slugify(slug_source, separator="-") or slugify(fallback or "product", separator="-") or "product"


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


def _serialize_prompt(row: TranslationPrompt) -> dict:
    return {
        "id": row.id,
        "language_code": row.language_code,
        "promptTemplate": row.prompt_template,
        "systemPrompt": row.system_prompt,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def _translation_for(product: Product, language_code: str) -> ProductTranslation | None:
    return next((row for row in product.translations if row.language_code == language_code), None)


def _variant_translation_for(variant: ProductVariant, language_code: str) -> VariantTranslation | None:
    return next((row for row in variant.translations if row.language_code == language_code), None)


def _normalize_language_code(value: str) -> str:
    return str(value or "").strip()


def _unique_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        normalized = int(value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
