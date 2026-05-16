#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.models import Product, ProductTranslation  # noqa: E402
from app.db.session import session_scope  # noqa: E402


load_dotenv(ROOT / ".env")
VOXSTER_PREFIX = "https://www.voxster.ch/"
DEFAULT_LOCALE = "de-CH"
USER_AGENT = "PIM-PAM-FinalUrlDescriptionImport/1.0"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
TRIVIAL_DESCRIPTIONS = {
    "",
    ".",
    "-",
    "preis pro stück",
    "preis pro flasche",
    "preis pro kanister",
    "preis pro packung",
    "preis pro karton",
    "preis pro rolle",
    "preis pro set",
}


@dataclass
class ImportRow:
    product_id: int
    product_name: str
    final_url: str
    domain: str
    status: str
    found: bool
    old_short_description: str
    new_short_description: str
    old_description: str
    new_description: str
    error: str = ""


def main() -> int:
    args = parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("--apply und --dry-run können nicht kombiniert werden.")
    dry_run = not args.apply
    rows: list[ImportRow]
    with session_scope() as session:
        products = load_final_url_products(session, product_id=args.product_id, limit=args.limit, domain=args.domain)
        rows = process_products(
            session,
            products,
            overwrite=args.overwrite,
            dry_run=dry_run,
            sleep_seconds=args.sleep,
            backup_dir=Path(args.backup_dir),
            enhance_ai=not args.no_ai,
            model=args.model,
        )
        if dry_run:
            session.rollback()
    write_report(rows, Path(args.output))
    print_report(rows, dry_run=dry_run, output=Path(args.output))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Einmaliger Import von Produktbeschreibungen aus Product.source_url_final.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=False, help="Standard: nur anzeigen, nichts speichern.")
    mode.add_argument("--apply", action="store_true", help="Änderungen speichern und vorher Backup schreiben.")
    parser.add_argument("--overwrite", action="store_true", help="Bestehende Beschreibung/Kurzbeschreibung überschreiben.")
    parser.add_argument("--product-id", type=int, default=None, help="Optional nur ein Produkt verarbeiten, z. B. 1294.")
    parser.add_argument("--limit", type=int, default=None, help="Optional Anzahl Produkte begrenzen.")
    parser.add_argument("--domain", default=None, help="Optional auf Domain filtern, z. B. www.voxster.ch. Ohne Filter werden alle Final URLs verarbeitet.")
    parser.add_argument("--sleep", type=float, default=0.35, help="Pause zwischen Requests in Sekunden.")
    parser.add_argument("--no-ai", action="store_true", help="ChatGPT-Erweiterung deaktivieren und nur Originalbeschreibung übernehmen.")
    parser.add_argument("--model", default=None, help=f"OpenAI-Modell fuer Textausbau, default {DEFAULT_OPENAI_MODEL} bzw. OPENAI_MODEL.")
    parser.add_argument("--backup-dir", default=str(ROOT / "output" / "final_url_description_backups"), help="Backup-Verzeichnis für --apply.")
    parser.add_argument("--output", default=str(ROOT / "output" / "final_url_description_import_report.csv"), help="CSV-Report.")
    return parser.parse_args()


def load_final_url_products(
    session: Session,
    *,
    product_id: int | None = None,
    limit: int | None = None,
    domain: str | None = None,
) -> list[Product]:
    stmt = (
        select(Product)
        .options(selectinload(Product.translations))
        .where(Product.source_url_final.is_not(None))
        .where(Product.source_url_final != "")
        .order_by(Product.id.asc())
    )
    if product_id is not None:
        stmt = stmt.where(Product.id == product_id)
    if limit is not None:
        stmt = stmt.limit(limit)
    products = list(session.scalars(stmt).unique())
    domain_filter = normalize_domain(domain)
    if domain_filter:
        products = [product for product in products if normalize_domain(urlparse(product.source_url_final or "").netloc) == domain_filter]
    return products


def load_voxster_products(session: Session, *, product_id: int | None = None, limit: int | None = None) -> list[Product]:
    return load_final_url_products(session, product_id=product_id, limit=limit, domain="www.voxster.ch")


def process_products(
    session: Session,
    products: list[Product],
    *,
    overwrite: bool,
    dry_run: bool,
    sleep_seconds: float = 0.35,
    backup_dir: Path | None = None,
    enhance_ai: bool = True,
    model: str | None = None,
) -> list[ImportRow]:
    rows: list[ImportRow] = []
    backup_payload: list[dict[str, Any]] = []
    for index, product in enumerate(products):
        if index and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        backup_before = product_backup(product)
        row = process_product(session, product, overwrite=overwrite, dry_run=dry_run, enhance_ai=enhance_ai, model=model)
        rows.append(row)
        if row.status in {"would_update", "updated"}:
            backup_payload.append(backup_before)
    if not dry_run and backup_payload:
        write_backup(backup_payload, backup_dir or ROOT / "output" / "final_url_description_backups")
        session.flush()
    return rows


def process_product(
    session: Session,
    product: Product,
    *,
    overwrite: bool,
    dry_run: bool,
    enhance_ai: bool = True,
    model: str | None = None,
) -> ImportRow:
    translation = get_or_create_translation(session, product, dry_run=dry_run)
    old_short = translation.short_description or ""
    old_description = translation.description or product.description or ""
    final_url = product.source_url_final or ""
    domain = urlparse(final_url).netloc
    if not final_url.strip():
        return ImportRow(product.id, product.title, final_url, domain, "skipped_no_final_url", False, old_short, "", old_description, "", "Keine Final URL vorhanden.")
    try:
        page = fetch_page(final_url)
        extracted = extract_final_url_description(page)
    except Exception as exc:
        return ImportRow(product.id, product.title, final_url, domain, "error", False, old_short, "", old_description, "", str(exc))
    if not extracted:
        return ImportRow(product.id, product.title, final_url, domain, "not_found", False, old_short, "", old_description, "")
    try:
        if enhance_ai:
            final_description, short_description = enhance_description_with_ai(product, extracted, model=model)
        else:
            final_description, short_description = build_markdown_description(extracted), build_short_description(extracted)
    except Exception as exc:
        return ImportRow(product.id, product.title, final_url, domain, "ai_error", True, old_short, "", old_description, extracted, str(exc))
    changes = should_update(old_description, overwrite=overwrite) or should_update(old_short, overwrite=overwrite)
    status = "would_update" if dry_run and changes else "updated" if changes else "skipped_existing"
    if changes and not dry_run:
        if overwrite or should_update(product.description, overwrite=False):
            product.description = final_description
        if overwrite or should_update(translation.description, overwrite=False):
            translation.description = final_description
        if overwrite or should_update(translation.short_description, overwrite=False):
            translation.short_description = short_description
        translation.translation_status = "suggested"
        translation.source_language_code = DEFAULT_LOCALE
        translation.provider = "voxster_final_url_script_ai" if enhance_ai else "voxster_final_url_script"
        if enhance_ai:
            translation.model = model or openai_model()
        translation.generated_at = datetime.now(timezone.utc)
        session.flush()
    return ImportRow(product.id, product.title, final_url, domain, status, True, old_short, short_description, old_description, final_description)


def fetch_page(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Ungültige Final-URL: {url}")
    response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    response_bytes = getattr(response, "content", None)
    if response_bytes is None:
        response_bytes = str(getattr(response, "text", "") or "").encode("utf-8")
    if len(response_bytes or b"") > 3_000_000:
        raise ValueError("HTML-Seite ist zu gross für den sicheren Import.")
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower() and "<html" not in response.text[:500].lower():
        raise ValueError("Antwort ist keine HTML-Seite.")
    return response.text


def extract_final_url_description(page_html: str) -> str | None:
    for extractor in (extract_voxster_description, extract_generic_product_description):
        description = extractor(page_html)
        if description:
            return description
    return None


def extract_voxster_description(page_html: str) -> str | None:
    short_block = _first_match(
        page_html,
        r"<div[^>]+class=[\"'][^\"']*short-description[^\"']*[\"'][^>]*>(.*?)(?:<div[^>]+class=[\"'][^\"']*product-type-data|<div[^>]+class=[\"'][^\"']*add-to-box)",
    )
    std_text = _clean_html(_first_match(short_block or "", r"<div[^>]+class=[\"'][^\"']*std[^\"']*[\"'][^>]*>(.*?)</div>"))
    main_text = _clean_html(_first_match(short_block or "", r"<div[^>]+class=[\"'][^\"']*main-description[^\"']*[\"'][^>]*>(.*?)</div>"))
    meta_text = _clean_html(_meta_description(page_html))
    parts = []
    for value in (std_text, main_text):
        if _is_useful_description_part(value) and value not in parts:
            parts.append(value)
    if not parts and _is_useful_description_part(meta_text):
        parts.append(meta_text)
    description = " ".join(parts)
    description = normalize_text(description)
    return description if is_useful_description(description) else None


def extract_generic_product_description(page_html: str) -> str | None:
    candidates: list[str] = []
    patterns = (
        ("json_ld", r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>"),
        ("html", r"<div[^>]+class=[\"'][^\"']*(?:product-description|product_description|description|short-description|main-description)[^\"']*[\"'][^>]*>(.*?)</div>"),
        ("html", r"<section[^>]+class=[\"'][^\"']*(?:product-description|description|product-details)[^\"']*[\"'][^>]*>(.*?)</section>"),
        ("html", r"<main[^>]*>(.*?)</main>"),
    )
    for kind, pattern in patterns:
        for match in re.finditer(pattern, page_html or "", flags=re.I | re.S):
            value = match.group(1)
            if kind == "json_ld":
                candidates.extend(_json_ld_descriptions(value))
            else:
                candidates.append(_clean_html(value))
    meta_text = _clean_html(_meta_description(page_html))
    if meta_text:
        candidates.append(meta_text)
    cleaned_candidates = [candidate for candidate in (normalize_text(item) for item in candidates) if _is_useful_description_part(candidate)]
    cleaned_candidates = [candidate for candidate in cleaned_candidates if not is_navigation_like(candidate)]
    if not cleaned_candidates:
        return None
    cleaned_candidates.sort(key=lambda item: score_description_candidate(item), reverse=True)
    return cleaned_candidates[0]


def _json_ld_descriptions(script_value: str | None) -> list[str]:
    descriptions: list[str] = []
    try:
        payload = json.loads(html.unescape(script_value or ""))
    except Exception:
        return descriptions
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        if not isinstance(item, dict):
            continue
        candidates = item.get("@graph") if isinstance(item.get("@graph"), list) else [item]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("description"):
                descriptions.append(str(candidate.get("description") or ""))
    return descriptions


def is_navigation_like(value: str) -> bool:
    lowered = normalize_text(value).lower()
    bad_markers = (
        "warenkorb",
        "newsletter",
        "mein konto",
        "cookie",
        "datenschutz",
        "agb",
        "related products",
        "ähnliche produkte",
        "kunden kauften auch",
        "home about",
    )
    return any(marker in lowered for marker in bad_markers)


def score_description_candidate(value: str) -> int:
    text = normalize_text(value)
    lowered = text.lower()
    score = min(len(text), 900)
    for marker in ("material", "anwendung", "eigenschaften", "besteht aus", "geeignet", "description", "specifications"):
        if marker in lowered:
            score += 120
    if is_navigation_like(text):
        score -= 1000
    return score


def build_short_description(description: str, *, max_chars: int = 250) -> str:
    plain_description = strip_markdown(description)
    generated = build_structured_short_description(plain_description)
    if generated:
        return generated
    sentences = re.split(r"(?<=[.!?])\s+", normalize_text(plain_description))
    selected: list[str] = []
    for sentence in sentences:
        candidate = normalize_text(" ".join([*selected, sentence]))
        if selected and len(candidate) > max_chars:
            break
        selected.append(sentence)
        if len(candidate) >= 140:
            break
    result = normalize_text(" ".join(selected)) or normalize_text(description)
    if len(result) <= max_chars:
        return result
    words: list[str] = []
    for word in result.split():
        candidate = " ".join([*words, word])
        if len(candidate) > max_chars:
            break
        words.append(word)
    return " ".join(words).rstrip(" ,;:")


def build_structured_short_description(description: str) -> str | None:
    text = normalize_text(description)
    lowered = text.lower()
    if not text:
        return None
    if "fertigkonfektion" in lowered and "absaug" in lowered and "blas-saug" in lowered:
        material_bits = []
        for label in ("HR3-Überzug", "Silikonpad", "Molton", "Gegenzugkordel"):
            normalized_label = label.lower().replace("ü", "u")
            normalized_text = lowered.replace("ü", "u")
            if normalized_label in normalized_text or (label == "HR3-Überzug" and "hr3" in normalized_text):
                material_bits.append(label)
        suffix = f" mit {', '.join(material_bits[:-1])} und {material_bits[-1]}" if len(material_bits) > 1 else ""
        candidate = f"Fertigkonfektionierter gepolsterter Überzug für Absaug-/Blas-Saug-Bügeltische{suffix}."
        if len(candidate) <= 250:
            return candidate
    return None


def build_markdown_description(raw_description: str) -> str:
    text = normalize_text(raw_description)
    intro = first_product_sentence(text)
    sections: list[str] = [intro] if intro else []
    properties = extract_properties(text)
    materials = extract_materials(text)
    hints = extract_hints(text)
    if properties:
        sections.append(markdown_section("Eigenschaften", properties))
    if materials:
        sections.append(markdown_section("Material", materials))
    if hints:
        sections.append(markdown_section("Hinweis", hints))
    markdown = "\n\n".join(section for section in sections if section.strip())
    return sanitize_markdown_description(markdown or text)


def first_product_sentence(text: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+", normalize_text(text)):
        sentence = normalize_text(sentence)
        if not sentence or is_price_sentence(sentence):
            continue
        return normalize_product_terms(sentence)
    return normalize_product_terms(normalize_text(text))


def extract_properties(text: str) -> list[str]:
    lowered = text.lower()
    properties: list[str] = []
    if "fertigkonfektion" in lowered:
        properties.append("Fertig konfektioniert und direkt einsetzbar")
    if "absaug" in lowered and "blas-saug" in lowered:
        properties.append("Geeignet für Absaug-/Blas-Saug-Bügeltische")
    if "gegenzugkordel" in lowered:
        properties.append("Mit Gegenzugkordel ausgestattet")
    return unique_preserve_order(properties)


def extract_materials(text: str) -> list[str]:
    match = re.search(r"besteht aus\s+(.+?)(?:\.|$)", normalize_text(text), flags=re.I)
    if not match:
        return []
    raw_parts = re.split(r"\s*\+\s*|,\s*", match.group(1))
    materials: list[str] = []
    for part in raw_parts:
        item = normalize_product_terms(part)
        if not item:
            continue
        if re.search(r"überzugsstoff\s+hr3.*100\s*%\s*polyester", item, flags=re.I):
            item = "Überzugsstoff HR3: 100 % Polyester"
        materials.append(item)
    return unique_preserve_order(materials)


def extract_hints(text: str) -> list[str]:
    hints: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", normalize_text(text)):
        sentence = normalize_product_terms(sentence)
        if is_price_sentence(sentence):
            hints.append(sentence if sentence.endswith(".") else f"{sentence}.")
    return unique_preserve_order(hints)


def markdown_section(title: str, items: list[str]) -> str:
    clean_items = [normalize_product_terms(item).rstrip(".") for item in items if normalize_product_terms(item)]
    if not clean_items:
        return ""
    return f"### {title}\n\n" + "\n".join(f"- {item}" for item in clean_items)


def sanitize_markdown_description(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [normalize_product_terms(line) if not line.lstrip().startswith("- ") else f"- {normalize_product_terms(line.lstrip()[2:]).rstrip('.')}" for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    text = remove_internal_metadata_bullets(text)
    text = remove_empty_markdown_sections(text)
    return text


def remove_internal_metadata_bullets(value: str) -> str:
    cleaned_lines: list[str] = []
    for line in value.split("\n"):
        if re.match(r"^-\s*(Produkt-ID|SKU|Produktname|Produktlink|Final URL|URL)\s*:", line, flags=re.I):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def remove_empty_markdown_sections(value: str) -> str:
    parts = re.split(r"(?m)^###\s+(.+?)\s*$", value)
    if len(parts) == 1:
        return value.strip()
    rebuilt = [parts[0].strip()] if parts[0].strip() else []
    for idx in range(1, len(parts), 2):
        title = parts[idx].strip()
        body = parts[idx + 1].strip() if idx + 1 < len(parts) else ""
        if body:
            rebuilt.append(f"### {title}\n\n{body}")
    return "\n\n".join(rebuilt).strip()


def strip_markdown(value: str) -> str:
    text = re.sub(r"(?m)^###\s+", "", str(value or ""))
    text = re.sub(r"(?m)^-\s+", "", text)
    return normalize_text(text)


def normalize_product_terms(value: str | None) -> str:
    text = normalize_text(value)
    text = text.replace("Absaug-/ Blas-Saug", "Absaug-/Blas-Saug")
    text = re.sub(r"Bügeltische{2,}", "Bügeltische", text)
    text = re.sub(r"Bügeltisch(?!e)", "Bügeltische", text)
    return text


def is_price_sentence(value: str | None) -> bool:
    return bool(re.search(r"\bpreis pro\b", normalize_text(value), flags=re.I))


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize_text(value).lower()
        if key and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def enhance_description_with_ai(product: Product, raw_description: str, *, model: str | None = None) -> tuple[str, str]:
    payload = call_openai_description_json(product, raw_description, model=model or openai_model())
    description = sanitize_markdown_description(str(payload.get("description") or ""))
    short_description = normalize_text(str(payload.get("short_description") or ""))
    description = enforce_markdown_description(description, raw_description)
    if is_suspicious_markdown_description(description):
        description = build_markdown_description(raw_description)
    short_description = enforce_short_description(short_description, description)
    structured_short = build_structured_short_description(raw_description)
    if structured_short and (is_suspicious_short_description(short_description) or len(short_description) > 180 or "fertigkonfektion" in raw_description.lower()):
        short_description = structured_short
    return description, short_description


def call_openai_description_json(product: Product, raw_description: str, *, model: str) -> dict[str, str]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ist nicht konfiguriert. Nutze --no-ai fuer Originaltexte.")
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    system_prompt = (
        "Du erstellst sachliche Produkttexte fuer einen Schweizer B2B-Shop.\n"
        "Regeln:\n"
        "- Schreibe in Schweizer Hochdeutsch, verwende ss statt scharfem S.\n"
        "- Nutze nur die gelieferten Produktdaten und die Originalbeschreibung.\n"
        "- Erfinde keine technischen Daten, Kompatibilitaeten, Sicherheitsangaben, Zertifikate oder Leistungsversprechen.\n"
        "- Kurzbeschreibung: reiner Text, genau ein klarer Satz, 120 bis 180 Zeichen, maximal 250 Zeichen, keine Bulletpoints, keine abgeschnittenen Woerter.\n"
        "- Beschreibung: sauberes Markdown ohne HTML. Erster Absatz ist eine kurze Einleitung. Danach nur passende Abschnitte mit ###-Ueberschriften und Bulletpoints.\n"
        "- Nutze Abschnitte wie ### Eigenschaften, ### Material / Technische Angaben und ### Anwendung / Hinweis nur, wenn dazu sichere Angaben vorhanden sind.\n"
        "- Keine leeren Ueberschriften. Preise nur nennen, wenn sie in der Quelle produktrelevant vorkommen.\n"
        "- Wenn wenig Quellinformation vorhanden ist, schreibe lieber kuerzer und korrekt statt lang und spekulativ.\n"
        "- Keine generischen Schlusssaetze wie 'Weitere Angaben entnehmen Sie den Produktunterlagen', ausser sie stehen in der Quelle.\n"
        "- Gib ausschliesslich JSON zurueck."
    )
    user_prompt = (
        "Erzeuge aus den folgenden Daten eine erweiterte Beschreibung und Kurzbeschreibung.\n\n"
        f"Produkt-ID: {product.id}\n"
        f"SKU: {product.sku}\n"
        f"Titel: {product.title}\n"
        f"Final URL: {product.source_url_final}\n"
        f"Originalbeschreibung:\n{raw_description}\n\n"
        'Antwortformat: {"description": "...", "short_description": "..."}'
    )
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if not model.startswith("gpt-5"):
        request_payload["temperature"] = 0.2
    response = requests.post(
        f"{base_url}/chat/completions",
        timeout=60,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=request_payload,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI API Fehler {response.status_code}: {response.text[:500]}")
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI-Antwort ist kein JSON-Objekt.")
    return {str(key): str(value or "") for key, value in parsed.items()}


def openai_model() -> str:
    return (os.getenv("PIM_DESCRIPTION_MODEL") or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()


def enforce_markdown_description(value: str, raw_description: str) -> str:
    cleaned = sanitize_markdown_description(value)
    if not cleaned or len(strip_markdown(cleaned)) < 80:
        cleaned = build_markdown_description(raw_description)
    if "### " not in cleaned and len(strip_markdown(raw_description)) >= 80:
        cleaned = build_markdown_description(raw_description)
    if has_empty_markdown_heading(cleaned):
        cleaned = remove_empty_markdown_sections(cleaned)
    if len(cleaned) > 1800:
        cleaned = trim_markdown(cleaned, 1800)
    return cleaned


def enforce_short_description(value: str, description: str) -> str:
    cleaned = normalize_text(value)
    cleaned = re.sub(r"(?m)^[-*]\s+", "", cleaned).replace("\n", " ")
    cleaned = remove_price_fragments_from_short_description(cleaned)
    if len(cleaned) < 120 or not is_complete_short_description(cleaned):
        cleaned = build_short_description(description, max_chars=180)
        cleaned = remove_price_fragments_from_short_description(cleaned)
    if len(cleaned) > 250:
        cleaned = trim_words(cleaned, 250)
    elif len(cleaned) > 180:
        cleaned = trim_words(cleaned, 180)
    if not is_complete_short_description(cleaned):
        first_sentence = re.split(r"(?<=[.!?])\s+", strip_markdown(description))[0]
        if 80 <= len(first_sentence) <= 180:
            cleaned = first_sentence
    return cleaned


def remove_price_fragments_from_short_description(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"\s*[;,.]\s*Preis pro\s+[^.;,]+[.;]?\s*$", ".", text, flags=re.I)
    text = re.sub(r"\bPreis pro\s+[^.;,]+[.;]?\s*$", "", text, flags=re.I).strip(" ,;:")
    if text and text[-1] not in ".!?":
        text += "."
    return normalize_text(text)


def trim_markdown(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    trimmed = value[:max_chars].rsplit("\n\n", 1)[0].strip()
    return trimmed or trim_words(strip_markdown(value), max_chars)


def has_empty_markdown_heading(value: str) -> bool:
    sections = re.split(r"(?m)^###\s+.+?\s*$", value)
    return any(index > 0 and not section.strip() for index, section in enumerate(sections))


def is_suspicious_markdown_description(value: str) -> bool:
    text = normalize_text(value).lower()
    if re.search(r"bügeltische{2,}|buegeltische{2,}", text):
        return True
    match = re.search(r"###\s+eigenschaften\s+(.*?)(?:###|$)", value, flags=re.I | re.S)
    if match and re.search(r"\bpreis pro\b", match.group(1), flags=re.I):
        return True
    for section_title, section_body in re.findall(r"(?ms)^###\s+(.+?)\s*$\n+(.*?)(?=^###|\Z)", value):
        if "hinweis" not in section_title.lower() and re.search(r"\bpreis pro\b", section_body, flags=re.I):
            return True
    return False


def is_suspicious_short_description(value: str) -> bool:
    text = normalize_text(value)
    lowered = text.lower()
    return bool(re.search(r"bügeltische{2,}|buegeltische{2,}", lowered)) or is_price_sentence(text)


def trim_words(value: str, max_chars: int) -> str:
    words: list[str] = []
    for word in normalize_text(value).split():
        candidate = " ".join([*words, word])
        if len(candidate) > max_chars:
            break
        words.append(word)
    cleaned = " ".join(words).rstrip(" ,;:")
    cleaned = re.sub(r"\b(und|oder|sowie|mit|aus|fuer|für)$", "", cleaned, flags=re.I).strip(" ,;:")
    return cleaned


def is_complete_short_description(value: str | None) -> bool:
    cleaned = normalize_text(value)
    if not cleaned:
        return False
    if re.search(r"\b(und|oder|sowie|mit|aus|fuer|für)$", cleaned, flags=re.I):
        return False
    return cleaned[-1] in ".!?"


def should_update(current_value: str | None, *, overwrite: bool) -> bool:
    if overwrite:
        return True
    return is_trivial_existing_text(current_value)


def is_trivial_existing_text(value: str | None) -> bool:
    cleaned = normalize_text(value).strip()
    return cleaned.lower() in TRIVIAL_DESCRIPTIONS


def is_useful_description(value: str | None) -> bool:
    cleaned = normalize_text(value)
    if len(cleaned) < 40:
        return False
    lowered = cleaned.lower()
    bad_markers = (
        "javascript scheint in ihrem browser deaktiviert",
        "in den warenkorb",
        "schreiben sie die erste kundenmeinung",
        "verfügbarkeit:",
        "zum merkzettel hinzufügen",
    )
    return not any(marker in lowered for marker in bad_markers)


def get_or_create_translation(session: Session, product: Product, *, dry_run: bool) -> ProductTranslation:
    for translation in product.translations:
        if translation.language_code == DEFAULT_LOCALE:
            return translation
    translation = ProductTranslation(product_id=product.id, language_code=DEFAULT_LOCALE, title=product.title, translation_status="draft")
    if not dry_run:
        session.add(translation)
        session.flush()
        product.translations.append(translation)
    return translation


def product_backup(product: Product) -> dict[str, Any]:
    return {
        "product": {
            "id": product.id,
            "sku": product.sku,
            "title": product.title,
            "description": product.description,
            "source_url_final": product.source_url_final,
        },
        "translations": [
            {
                "id": translation.id,
                "language_code": translation.language_code,
                "title": translation.title,
                "short_description": translation.short_description,
                "description": translation.description,
                "seo_title": translation.seo_title,
                "seo_description": translation.seo_description,
                "slug": translation.slug,
            }
            for translation in product.translations
        ],
    }


def write_backup(payload: list[dict[str, Any]], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"voxster_descriptions_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def write_report(rows: list[ImportRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "product_id",
                "product_name",
                "final_url",
                "domain",
                "status",
                "found",
                "old_short_description",
                "new_short_description",
                "old_description",
                "new_description",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def print_report(rows: list[ImportRow], *, dry_run: bool, output: Path) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    mode = "Dry-Run" if dry_run else "Apply"
    print(f"{mode} abgeschlossen: {len(rows)} Produkte geprüft · {counts}")
    print(f"CSV-Report: {output}")
    for row in rows[:20]:
        print(f"{row.product_id} · {row.status} · found={row.found} · {row.product_name} · {row.final_url}")
        if row.new_description:
            print(f"  Beschreibung: {row.new_description[:240]}")
        if row.error:
            print(f"  Fehler: {row.error}")


def _is_useful_description_part(value: str | None) -> bool:
    text = normalize_text(value)
    if not text:
        return False
    if is_trivial_existing_text(text):
        return True
    return is_useful_description(text)


def _meta_description(page_html: str) -> str | None:
    return _first_match(page_html, r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>")


def normalize_domain(value: str | None) -> str:
    return str(value or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")


def _first_match(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value or "", flags=re.I | re.S)
    return match.group(1) if match else None


def _clean_html(value: str | None) -> str:
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", value or "", flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|td|h[1-6])>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(html.unescape(text))


def normalize_text(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


if __name__ == "__main__":
    raise SystemExit(main())
