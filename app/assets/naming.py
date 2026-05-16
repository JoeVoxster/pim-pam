from __future__ import annotations

import re
from pathlib import Path

from slugify import slugify

MAX_FILENAME_STEM_LENGTH = 80


def safe_slug(value: str | None, default: str = "asset") -> str:
    slug = slugify(value or "", separator="_")
    return slug or default


def safe_extension(extension: str | None, fallback: str) -> str:
    ext = (extension or "").lower().strip()
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""
    if not re.fullmatch(r"\.[a-z0-9]{1,6}", ext):
        return fallback
    return ext


def build_asset_filename(
    supplier_sku: str,
    asset_type: str,
    index: int,
    extension: str,
    descriptive_label: str | None = None,
) -> str:
    label = seo_slug(descriptive_label, default=f"{asset_type}_{index}")
    ext = safe_extension(extension, ".bin")
    if asset_type == "pdf":
        suffix = label if label not in {"pdf_1", "document"} else f"document_{index}"
    else:
        suffix = label if descriptive_label else f"image_{index}"
    return f"{suffix}{ext}"


def build_descriptive_image_label(
    product_name: str | None,
    product_title: str | None,
    description: str | None,
    source_hint: str | None,
    context_text: str | None,
    index: int,
) -> str:
    base = clean_product_name(product_name or product_title or context_text or description or "product")
    packaging = extract_packaging_hint(source_hint, context_text, product_name, product_title, description)
    tokens = [token for token in slugify(base, separator="_").split("_") if token]
    filtered = [token for token in tokens if token not in {"mit", "und", "the", "for", "von", "der", "die", "das"}]
    if not filtered:
        filtered = ["product"]
    label = "_".join(filtered[:4])
    if packaging:
        return _shorten_label(f"{label}_{seo_slug(packaging)}_{index}")
    return _shorten_label(f"{label}_{index}")


def build_descriptive_pdf_label(
    product_name: str | None,
    product_title: str | None,
    label: str | None,
    source_hint: str | None,
    context_text: str | None,
) -> str:
    base = clean_product_name(product_name or product_title or "document")
    packaging = extract_packaging_hint(source_hint, context_text, product_name, product_title)
    base_slug = seo_slug(base, default="document")
    packaging_slug = seo_slug(packaging) if packaging else None
    label_slug = _normalize_pdf_label(label, base_slug)
    return _shorten_label("_".join(part for part in [base_slug, packaging_slug, label_slug] if part))


def clean_product_name(value: str | None) -> str:
    normalized = " ".join((value or "").split()).strip()
    if not normalized:
        return "product"
    parts = normalized.split()
    if parts and _looks_like_sku_token(parts[-1]):
        parts = parts[:-1]
    cleaned = " ".join(parts).strip()
    return cleaned or normalized


def extract_packaging_hint(*values: str | None) -> str | None:
    pattern = re.compile(
        r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(ml|millilit(?:er|re)s?|cl|l|lt|liter|litre|kg|g)\b",
        re.IGNORECASE,
    )
    for value in values:
        if not value:
            continue
        normalized = str(value).replace("_", " ").replace("-", " ")
        match = pattern.search(normalized)
        if not match:
            continue
        amount = match.group(1).replace(",", ".")
        unit = match.group(2).lower()
        unit_map = {
            "milliliter": "ml",
            "milliliters": "ml",
            "millilitre": "ml",
            "millilitres": "ml",
            "liter": "liter",
            "liters": "liter",
            "litre": "liter",
            "litres": "liter",
            "lt": "liter",
            "l": "liter",
            "kg": "kg",
            "g": "g",
            "ml": "ml",
            "cl": "cl",
        }
        normalized_unit = unit_map.get(unit, unit)
        amount = amount.rstrip("0").rstrip(".") if "." in amount else amount
        return f"{amount} {normalized_unit}"
    return None


def seo_slug(value: str | None, default: str = "asset") -> str:
    slug = safe_slug(value, default=default)
    return _shorten_label(slug)


def _normalize_pdf_label(value: str | None, base_slug: str | None = None) -> str:
    slug = safe_slug(value, default="document")
    if base_slug:
        base_tokens = [token for token in base_slug.split("_") if token]
        slug_tokens = [token for token in slug.split("_") if token]
        filtered_tokens = [token for token in slug_tokens if token not in base_tokens]
        slug = "_".join(filtered_tokens) or slug
    normalized = slug
    phrase_replacements = (
        ("safety_data_sheet", "sds"),
        ("technical_data_sheet", "tds"),
        ("data_sheet", "datasheet"),
        ("safety_sheet", "sds"),
    )
    for source, target in phrase_replacements:
        normalized = normalized.replace(source, target)
    tokens = normalized.split("_")
    deduped: list[str] = []
    for token in tokens:
        if token and token not in deduped:
            deduped.append(token)
    return _shorten_label("_".join(deduped))


def _shorten_label(value: str, max_length: int = MAX_FILENAME_STEM_LENGTH) -> str:
    cleaned = re.sub(r"_+", "_", value.strip("_"))
    if len(cleaned) <= max_length:
        return cleaned
    tokens = [token for token in cleaned.split("_") if token]
    result: list[str] = []
    current_length = 0
    for token in tokens:
        additional = len(token) if not result else len(token) + 1
        if current_length + additional > max_length:
            break
        result.append(token)
        current_length += additional
    if result:
        return "_".join(result)
    return cleaned[:max_length].rstrip("_")


def _looks_like_sku_token(value: str) -> bool:
    compact = value.strip("()[],:;")
    return bool(re.fullmatch(r"[A-Za-z]{1,4}\d{1,4}(?:[-_/]?[A-Za-z0-9]{1,8})+", compact))


def guess_pdf_label(url: str) -> str:
    lower = url.lower()
    if "sds" in lower or "safety" in lower or "sicurezza" in lower or "securite" in lower:
        return "sds"
    if "manual" in lower or "anleitung" in lower:
        return "manual"
    if "catalog" in lower or "katalog" in lower:
        return "catalog"
    if "data" in lower or "sheet" in lower or "datenblatt" in lower:
        return "datasheet"
    return "document"


def ensure_supplier_asset_dir(base_assets_dir: str | Path, supplier_sku: str) -> Path:
    target = Path(base_assets_dir) / safe_slug(clean_product_name(supplier_sku), default="product")
    target.mkdir(parents=True, exist_ok=True)
    return target
