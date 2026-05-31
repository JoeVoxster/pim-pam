from __future__ import annotations

from dataclasses import dataclass
import re


COLOR_WORDS = (
    "black",
    "blue",
    "brown",
    "green",
    "grey",
    "gray",
    "orange",
    "pink",
    "purple",
    "red",
    "violet",
    "white",
    "yellow",
)
COLOR_RE = re.compile(rf"(?i)\b({'|'.join(COLOR_WORDS)})\b")
PACKAGING_RE = re.compile(
    r"(?i)\b("
    r"\d+\s*x\s*\d+(?:[.,]\d+)?\s*(?:kg|g|gr|ml|lt|l)"
    r"|"
    r"\d+(?:[.,]\d+)?\s*(?:kg|g|gr|ml|lt|l)"
    r")\b"
)
TINTOLAV_COLOR_SUFFIXES = {
    "AR": "Orange",
    "AZ": "Blue",
    "BI": "White",
    "GI": "Yellow",
    "GR": "Grey",
    "MA": "Brown",
    "RA": "Pink",
    "RO": "Red",
    "VE": "Green",
    "VI": "Purple",
}


@dataclass(slots=True)
class VariantOptionData:
    product_title: str | None = None
    option_name: str | None = None
    option_value: str | None = None


def infer_variant_option_data(
    *,
    sku: str | None,
    title: str | None,
    variant_title: str | None = None,
    extra_fields: dict[str, object] | None = None,
    existing_option_name: str | None = None,
    existing_option_value: str | None = None,
) -> VariantOptionData:
    fields = extra_fields or {}
    explicit_product_title = _string_or_none(fields.get("product_title") or fields.get("template_title"))
    explicit_option_name = _string_or_none(fields.get("variant_option_name") or fields.get("option_name") or existing_option_name)
    explicit_option_value = _string_or_none(fields.get("variant_option_value") or fields.get("option_value") or fields.get("color") or existing_option_value)

    candidate_title = (title or "").strip()
    candidate_variant_title = (variant_title or "").strip()
    packaging = _detect_packaging(candidate_variant_title) or _detect_packaging(candidate_title)
    title_color = _detect_color(candidate_title) or _detect_color(candidate_variant_title)
    sku_color = _detect_color_from_sku(sku)
    if explicit_option_name and explicit_option_value:
        option_name = explicit_option_name
        option_value = explicit_option_value
    elif packaging:
        option_name = "Packaging"
        option_value = packaging
    else:
        color = explicit_option_value or title_color or sku_color
        option_name = explicit_option_name or ("Color" if color else None)
        option_value = color

    return VariantOptionData(
        product_title=explicit_product_title or candidate_title or candidate_variant_title,
        option_name=option_name,
        option_value=option_value,
    )


def _detect_color(value: str) -> str | None:
    match = COLOR_RE.search(value or "")
    if not match:
        return None
    return match.group(1).title()


def _detect_color_from_sku(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper()
    suffix = normalized[-2:]
    return TINTOLAV_COLOR_SUFFIXES.get(suffix)


def _detect_packaging(value: str | None) -> str | None:
    if not value:
        return None
    match = PACKAGING_RE.search(value)
    if not match:
        return None
    packaging = re.sub(r"\s+", " ", match.group(1)).strip()
    packaging = re.sub(r"(?i)\bgr\b", "g", packaging)
    packaging = re.sub(r"(?i)\blt\b", "lt", packaging)
    packaging = re.sub(r"(?i)\bl\b", "l", packaging)
    packaging = re.sub(r"(?i)\bml\b", "ml", packaging)
    packaging = re.sub(r"(?i)\bkg\b", "kg", packaging)
    packaging = re.sub(r"(?i)\bg\b", "g", packaging)
    packaging = re.sub(r"\s*x\s*", " x ", packaging, count=1)
    return packaging


def _string_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
