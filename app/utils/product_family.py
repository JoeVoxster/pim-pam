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
FAMILY_SKU_RE = re.compile(r"^([A-Z]{1,3}\d{2}-\d{3})([A-Z]{1,3}\d*)$")
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
class FamilyData:
    family_key: str | None = None
    family_title: str | None = None
    option_name: str | None = None
    option_value: str | None = None


def infer_family_data(
    *,
    sku: str | None,
    title: str | None,
    variant_title: str | None = None,
    extra_fields: dict[str, object] | None = None,
    existing_option_name: str | None = None,
    existing_option_value: str | None = None,
) -> FamilyData:
    fields = extra_fields or {}
    explicit_family_key = _string_or_none(
        fields.get("product_family_key")
        or fields.get("family_key")
        or fields.get("variant_family_key")
    )
    explicit_family_title = _string_or_none(
        fields.get("product_family_title")
        or fields.get("family_title")
        or fields.get("variant_family_title")
    )
    explicit_option_name = _string_or_none(
        fields.get("variant_option_name")
        or fields.get("option_name")
        or existing_option_name
    )
    explicit_option_value = _string_or_none(
        fields.get("variant_option_value")
        or fields.get("option_value")
        or fields.get("color")
        or existing_option_value
    )
    if explicit_family_key:
        return FamilyData(
            family_key=explicit_family_key,
            family_title=explicit_family_title or title or variant_title,
            option_name=explicit_option_name,
            option_value=explicit_option_value,
        )

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

    if not option_value or not sku:
        return FamilyData()

    family_key = None
    family_title = None
    if packaging or title_color or explicit_family_title or _sku_looks_like_family_member(sku):
        family_key = _derive_family_key(sku)
        if family_key:
            family_title = explicit_family_title or _normalize_family_title(
                candidate_title or candidate_variant_title,
                option_name,
                option_value,
            )
            if not family_title:
                family_title = candidate_title or candidate_variant_title or sku

    return FamilyData(
        family_key=family_key,
        family_title=family_title,
        option_name=option_name,
        option_value=option_value,
    )


def _derive_family_key(sku: str) -> str | None:
    normalized = sku.strip().upper()
    match = FAMILY_SKU_RE.match(normalized)
    if not match:
        return None
    return f"{match.group(1)}XX"


def _detect_color(value: str) -> str | None:
    match = COLOR_RE.search(value or "")
    if not match:
        return None
    return match.group(1).title()


def _detect_color_from_sku(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().upper()
    match = FAMILY_SKU_RE.match(normalized)
    if not match:
        return None
    suffix = match.group(2)
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


def _sku_looks_like_family_member(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().upper()
    if normalized.endswith("XX"):
        return True
    match = FAMILY_SKU_RE.match(normalized)
    if not match:
        return False
    return match.group(2) in TINTOLAV_COLOR_SUFFIXES


def _remove_color_token(title: str, color: str) -> str:
    pattern = re.compile(rf"(?i)\b{re.escape(color)}\b")
    cleaned = pattern.sub(" ", title, count=1)
    cleaned = _cleanup_title(cleaned)
    return cleaned.strip(" -")


def _remove_packaging_token(title: str, packaging: str) -> str:
    for match in PACKAGING_RE.finditer(title):
        if _detect_packaging(match.group(0)) == packaging:
            cleaned = f"{title[:match.start()]} {title[match.end():]}"
            cleaned = _cleanup_title(cleaned)
            return cleaned.strip(" -")
    return title.strip()


def _normalize_family_title(title: str, option_name: str | None, option_value: str | None) -> str:
    if option_name == "Packaging" and option_value:
        return _remove_packaging_token(title, option_value)
    if option_name == "Color" and option_value:
        return _remove_color_token(title, option_value)
    return title.strip()


def _cleanup_title(value: str) -> str:
    cleaned = re.sub(r"\s{2,}", " ", value)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    cleaned = re.sub(r"(?<=\w)\.(?=\s+[A-Z])", "", cleaned)
    cleaned = re.sub(r"\s+([,;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -")


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
