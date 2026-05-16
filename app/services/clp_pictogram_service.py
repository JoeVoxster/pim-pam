from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class PictogramDecision:
    code: str
    required: bool
    reason_classifications: list[str]
    suppressed_by_priority: bool = False
    suppressed_reason: str | None = None
    review_required: bool = False


GHS_CODE_RE = re.compile(r"\bGHS0[1-9]\b", re.I)


def resolve_clp_pictograms(classifications: Iterable[str], original_pictograms: Iterable[str] | None = None) -> list[PictogramDecision]:
    """Resolve product-level CLP pictograms and apply priority suppression.

    The function intentionally does not invent classifications. It derives pictograms only
    from supplied product-level classifications/H-statements and records source pictograms
    that should be suppressed by CLP priority rules.
    """
    joined = "\n".join(str(item or "") for item in classifications)
    original_codes = _normalize_codes(original_pictograms or [])

    reasons: dict[str, list[str]] = {}
    if _has_eye_damage_or_corrosion(joined):
        reasons.setdefault("GHS05", []).append(_reason(joined, ("Eye Dam. 1", "Skin Corr. 1", "H318", "H314"), "Eye Dam. 1 / H318"))
    if _has_skin_or_eye_irritation(joined):
        reasons.setdefault("GHS07", []).append(_reason(joined, ("Skin Irrit. 2", "Eye Irrit. 2", "H315", "H319"), "Skin/Eye Irrit."))
    if _has_other_ghs07_reason(joined):
        reasons.setdefault("GHS07", []).append(_reason(joined, ("Acute Tox. 4", "STOT SE 3", "Skin Sens. 1", "H302", "H312", "H332", "H317", "H335", "H336"), "andere GHS07-relevante Einstufung"))
    if _has_environmental(joined):
        reasons.setdefault("GHS09", []).append(_reason(joined, ("Aquatic Acute 1", "Aquatic Chronic 1", "H400", "H410"), "Umweltgefahr"))

    decisions: dict[str, PictogramDecision] = {}
    for code, reason_values in reasons.items():
        decisions[code] = PictogramDecision(code=code, required=True, reason_classifications=_unique(reason_values))

    if "GHS05" in decisions and "GHS07" in decisions and not _has_other_ghs07_reason(joined):
        decisions["GHS07"] = PictogramDecision(
            code="GHS07",
            required=False,
            reason_classifications=decisions["GHS07"].reason_classifications,
            suppressed_by_priority=True,
            suppressed_reason=(
                "GHS07 wurde gemäss Piktogramm-Priorität nicht übernommen, da GHS05 für schwere "
                "Augenschädigung/Haut-/Augenwirkung massgeblich ist und keine andere Produktgefahr GHS07 rechtfertigt."
            ),
            review_required=False,
        )

    for code in original_codes:
        if code not in decisions:
            decisions[code] = PictogramDecision(
                code=code,
                required=True,
                reason_classifications=["Original-Kennzeichnung aus Quelle; fachlich prüfen"],
                review_required=True,
            )

    return [decisions[code] for code in sorted(decisions)]


def resolve_clp_pictograms_from_text(text: str, original_pictograms: Iterable[str] | None = None) -> list[PictogramDecision]:
    original = _normalize_codes(original_pictograms or []) or _extract_ghs_codes(text)
    return resolve_clp_pictograms(_extract_classification_lines(text), original)


def resolved_pictogram_codes(decisions: Iterable[PictogramDecision]) -> list[str]:
    return [decision.code for decision in decisions if decision.required and not decision.suppressed_by_priority]


def pictogram_review_payload(text: str, original_pictograms: Iterable[str] | None = None) -> dict[str, object]:
    original = _normalize_codes(original_pictograms or []) or _extract_ghs_codes(text)
    decisions = resolve_clp_pictograms_from_text(text, original)
    resolved = resolved_pictogram_codes(decisions)
    suppressed = [decision for decision in decisions if decision.suppressed_by_priority]
    return {
        "original_label_pictograms": original,
        "resolved_label_pictograms": resolved,
        "decisions": [asdict(decision) for decision in decisions],
        "suppressed_pictograms": [asdict(decision) for decision in suppressed],
        "piktogram_review_required": bool(suppressed) or any(decision.review_required for decision in decisions),
        "status": "auto_fix_available" if suppressed else "needs_review" if any(decision.review_required for decision in decisions) else "ok",
        "message": (
            suppressed[0].suppressed_reason
            if suppressed
            else "Keine offensichtliche GHS-Piktogramm-Prioritätsauffälligkeit erkannt."
        ),
        "auto_apply": bool(suppressed),
        "requires_human_review": any(decision.review_required for decision in decisions),
    }


def _extract_ghs_codes(text: str) -> list[str]:
    return _normalize_codes(GHS_CODE_RE.findall(text or ""))


def _extract_classification_lines(text: str) -> list[str]:
    lines = []
    for line in (text or "").splitlines():
        if re.search(r"Skin\s+Irrit|Eye\s+Dam|Eye\s+Irrit|Skin\s+Corr|Acute\s+Tox|STOT\s+SE|Skin\s+Sens|Aquatic|H[0-9]{3}", line, flags=re.I):
            lines.append(line.strip())
    return lines or [text or ""]


def _normalize_codes(values: Iterable[str]) -> list[str]:
    output = []
    for value in values:
        code = str(value or "").strip().upper()
        if GHS_CODE_RE.fullmatch(code) and code not in output:
            output.append(code)
    return sorted(output)


def _has_eye_damage_or_corrosion(text: str) -> bool:
    return bool(re.search(r"\bEye\s+Dam\.?\s*1\b|\bSkin\s+Corr\.?\s*1\b|\bH318\b|\bH314\b|schwere augensch", text, flags=re.I))


def _has_skin_or_eye_irritation(text: str) -> bool:
    return bool(re.search(r"\bSkin\s+Irrit\.?\s*2\b|\bEye\s+Irrit\.?\s*2\b|\bH315\b|\bH319\b|hautreiz", text, flags=re.I))


def _has_other_ghs07_reason(text: str) -> bool:
    return bool(re.search(r"\bAcute\s+Tox\.?\s*4\b|\bSTOT\s+SE\s*3\b|\bSkin\s+Sens\.?\s*1\b|\bH302\b|\bH312\b|\bH332\b|\bH317\b|\bH335\b|\bH336\b", text, flags=re.I))


def _has_environmental(text: str) -> bool:
    return bool(re.search(r"\bAquatic\s+(?:Acute|Chronic)\s*1\b|\bH400\b|\bH410\b", text, flags=re.I))


def _reason(text: str, needles: Iterable[str], fallback: str) -> str:
    for needle in needles:
        if re.search(re.escape(needle), text, flags=re.I):
            return needle
    return fallback


def _unique(values: Iterable[str]) -> list[str]:
    output = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output
