from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.services.sdb_support import merge_sdb_sections


WGK_LABELS: dict[str, str] = {
    "nwg": "nicht wassergefährdend",
    "awg": "allgemein wassergefährdend",
    "WGK 1": "schwach wassergefährdend",
    "WGK 2": "deutlich wassergefährdend",
    "WGK 3": "stark wassergefährdend",
}

STORAGE_CLASS_LABELS: dict[str, str] = {
    "1": "Explosive Gefahrstoffe",
    "2A": "Gase",
    "2B": "Aerosolpackungen / Feuerzeuge",
    "3": "Entzündbare Flüssigkeiten",
    "4.1A": "Sonstige explosionsgefährliche Gefahrstoffe",
    "4.1B": "Entzündbare feste Gefahrstoffe",
    "4.2": "Pyrophore / selbsterhitzungsfähige Gefahrstoffe",
    "4.3": "Stoffe, die mit Wasser entzündbare Gase bilden",
    "5.1A": "Stark oxidierende Gefahrstoffe",
    "5.1B": "Oxidierende Gefahrstoffe",
    "5.1C": "Ammoniumnitrat / ammoniumnitrathaltige Zubereitungen",
    "5.2": "Organische Peroxide / selbstzersetzliche Stoffe",
    "6.1A": "Brennbare, stark akut toxische Gefahrstoffe",
    "6.1B": "Nicht brennbare, stark akut toxische Gefahrstoffe",
    "6.1C": "Brennbare akut toxische / chronisch wirkende Gefahrstoffe",
    "6.1D": "Nicht brennbare akut toxische / chronisch wirkende Gefahrstoffe",
    "6.2": "Ansteckungsgefährliche Stoffe",
    "7": "Radioaktive Stoffe",
    "8A": "Brennbare ätzende Gefahrstoffe",
    "8B": "Nicht brennbare ätzende Gefahrstoffe",
    "10": "Brennbare Flüssigkeiten, keiner vorherigen LGK zuordenbar",
    "11": "Brennbare Feststoffe, keiner vorherigen LGK zuordenbar",
    "12": "Nicht brennbare Flüssigkeiten, keiner vorherigen LGK zuordenbar",
    "13": "Nicht brennbare Feststoffe, keiner vorherigen LGK zuordenbar",
}


def normalize_wgk(value: str | None, *, allow_roman: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower().replace("-", " ")
    compact = re.sub(r"\s+", "", lowered)
    if compact in {"nwg", "nichtwassergefährdend", "nichtwassergefaehrdend"}:
        return "nwg"
    if compact in {"awg", "allgemeinwassergefährdend", "allgemeinwassergefaehrdend"}:
        return "awg"
    match = re.search(r"\bwgk\s*([123])\b", lowered)
    if match:
        return f"WGK {match.group(1)}"
    if compact in {"wgk1", "wassergefährdungsklasse1", "wassergefaehrdungsklasse1"}:
        return "WGK 1"
    if compact in {"wgk2", "wassergefährdungsklasse2", "wassergefaehrdungsklasse2"}:
        return "WGK 2"
    if compact in {"wgk3", "wassergefährdungsklasse3", "wassergefaehrdungsklasse3"}:
        return "WGK 3"
    if allow_roman and compact in {"wgkii", "wassergefährdungsklasseii", "wassergefaehrdungsklasseii"}:
        return "WGK 2"
    raise ValueError(f"Ungültige WGK: {value}")


def normalize_storage_class(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.upper().replace("LGK", "").replace("LAGERKLASSE", "").strip()
    normalized = re.sub(r"\s+", "", normalized)
    if normalized in {"9", "09"}:
        raise ValueError("LGK 9 ist nach TRGS 510 nicht besetzt und darf nicht gespeichert werden.")
    if normalized not in STORAGE_CLASS_LABELS:
        raise ValueError(f"Ungültige Lagerklasse: {value}")
    return normalized


def wgk_label(value: str | None) -> str | None:
    normalized = normalize_wgk(value) if value else None
    return WGK_LABELS.get(normalized or "")


def storage_class_label(value: str | None) -> str | None:
    normalized = normalize_storage_class(value) if value else None
    return STORAGE_CLASS_LABELS.get(normalized or "")


def build_chem_safety_payload(
    existing: dict | None,
    *,
    wgk: str | None,
    storage_class: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(existing or {})
    normalized_wgk = normalize_wgk(wgk) if wgk else None
    normalized_storage = normalize_storage_class(storage_class) if storage_class else None
    payload["wgk"] = normalized_wgk
    payload["wgk_label"] = WGK_LABELS.get(normalized_wgk or "")
    payload["storage_class"] = normalized_storage
    payload["storage_class_label"] = STORAGE_CLASS_LABELS.get(normalized_storage or "")
    if extra:
        payload.update(extra)
    return payload


def extract_wgk_storage_from_sdb(sdb_data: dict | None, *, existing_wgk: str | None = None, existing_storage_class: str | None = None) -> dict[str, Any]:
    sections = merge_sdb_sections((sdb_data or {}).get("sections_json"))
    raw_text = str((sdb_data or {}).get("raw_text") or "")
    source_url = (sdb_data or {}).get("pdf_url") or (sdb_data or {}).get("source_url")
    source_asset_id = (sdb_data or {}).get("source_asset_id")
    proposals = {
        "status": "not_found",
        "message": "Keine WGK/Lagerklasse im SDB gefunden.",
        "wgk": None,
        "storage_class": None,
    }
    wgk_proposal = _extract_wgk_proposal(sections, raw_text, source_url, source_asset_id)
    storage_proposal = _extract_storage_class_proposal(sections, raw_text, source_url, source_asset_id)
    if wgk_proposal:
        wgk_proposal["would_overwrite"] = bool(existing_wgk)
        proposals["wgk"] = wgk_proposal
    if storage_proposal:
        storage_proposal["would_overwrite"] = bool(existing_storage_class)
        proposals["storage_class"] = storage_proposal
    if wgk_proposal or storage_proposal:
        proposals["status"] = "proposal"
        proposals["message"] = "Vorschläge aus SDB erkannt. Manuelle Übernahme erforderlich."
    return proposals


def apply_classification_proposals_to_product(product: Any, proposals: dict[str, Any], *, apply_wgk: bool = True, apply_storage_class: bool = True) -> None:
    now = datetime.now(timezone.utc)
    if apply_wgk and proposals.get("wgk"):
        proposal = proposals["wgk"]
        product.wgk = proposal["value"]
        product.wgk_label = proposal["label"]
        product.wgk_source_section = proposal["source_section"]
        product.wgk_source_url = proposal.get("source_url")
        product.wgk_source_asset_id = proposal.get("source_asset_id")
        product.wgk_confidence = proposal["confidence"]
        product.wgk_last_enriched_at = now
    if apply_storage_class and proposals.get("storage_class"):
        proposal = proposals["storage_class"]
        product.storage_class = proposal["value"]
        product.storage_class_label = proposal["label"]
        product.storage_class_source_section = proposal["source_section"]
        product.storage_class_source_url = proposal.get("source_url")
        product.storage_class_source_asset_id = proposal.get("source_asset_id")
        product.storage_class_confidence = proposal["confidence"]
        product.storage_class_last_enriched_at = now
    product.chemical_safety_json = build_chem_safety_payload(
        product.chemical_safety_json,
        wgk=product.wgk,
        storage_class=product.storage_class,
        extra={
            "sdb_source_sections": {
                "wgk": getattr(product, "wgk_source_section", None),
                "storage_class": getattr(product, "storage_class_source_section", None),
            }
        },
    )


def _extract_wgk_proposal(sections: dict[str, dict[str, object]], raw_text: str, source_url: str | None, source_asset_id: int | None) -> dict[str, Any] | None:
    candidates: list[tuple[str, str, float]] = []
    for section_key, section_label in (("section_15", "15.1"), ("section_12", "12"), ("raw", "raw")):
        text = raw_text if section_key == "raw" else str((sections.get(section_key) or {}).get("content") or "")
        candidate = _find_wgk_in_text(text)
        if candidate:
            candidates.append((candidate, _excerpt(text, candidate), 0.92 if section_key == "section_15" else 0.65))
            return _proposal(candidate, WGK_LABELS[candidate], section_label, candidates[0][1], candidates[0][2], source_url, source_asset_id)
    return None


def _extract_storage_class_proposal(sections: dict[str, dict[str, object]], raw_text: str, source_url: str | None, source_asset_id: int | None) -> dict[str, Any] | None:
    for section_key, section_label in (("section_7", "7.2"), ("section_15", "15.1"), ("raw", "raw")):
        text = raw_text if section_key == "raw" else str((sections.get(section_key) or {}).get("content") or "")
        candidate = _find_storage_class_in_text(text)
        if candidate:
            confidence = 0.9 if section_key == "section_7" else 0.72
            return _proposal(candidate, STORAGE_CLASS_LABELS[candidate], section_label, _excerpt(text, candidate), confidence, source_url, source_asset_id)
    return None


def _find_wgk_in_text(text: str) -> str | None:
    lowered = str(text or "").lower()
    if "nicht wassergefährdend" in lowered or "nicht wassergefaehrdend" in lowered:
        return "nwg"
    if "allgemein wassergefährdend" in lowered or "allgemein wassergefaehrdend" in lowered:
        return "awg"
    match = re.search(r"\b(?:wgk|wassergefährdungsklasse|wassergefaehrdungsklasse)\s*:?\s*([123])\b", lowered)
    if match:
        return f"WGK {match.group(1)}"
    return None


def _find_storage_class_in_text(text: str) -> str | None:
    normalized = str(text or "")
    match = re.search(r"\b(?:LGK|Lagerklasse(?:\s+nach\s+TRGS\s+510)?)\s*:?\s*([0-9](?:\.[0-9])?[A-D]?|1[0-3])\b", normalized, flags=re.I)
    if not match:
        return None
    try:
        return normalize_storage_class(match.group(1))
    except ValueError:
        return None


def _proposal(value: str, label: str, source_section: str, excerpt: str, confidence: float, source_url: str | None, source_asset_id: int | None) -> dict[str, Any]:
    return {
        "value": value,
        "label": label,
        "source_section": source_section,
        "source_url": source_url,
        "source_asset_id": source_asset_id,
        "excerpt": excerpt,
        "confidence": confidence,
        "status": "proposal",
    }


def _excerpt(text: str, needle: str, limit: int = 260) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    idx = compact.lower().find(str(needle).lower())
    if idx < 0:
        idx = 0
    start = max(0, idx - 90)
    return compact[start : start + limit].strip()
