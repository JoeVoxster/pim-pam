from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.models import ScrapedData


CHEMICAL_FIELD_LABELS: list[tuple[str, str]] = [
    ("product_name", "Produktname"),
    ("manufacturer", "Hersteller"),
    ("brand_name", "Marke"),
    ("ufi", "UFI-Nummer"),
    ("voc_content_percent", "VOC-Gehalt (%)"),
    ("cas_number", "CAS-Nummer"),
    ("ec_number", "EG-Nummer"),
    ("un_number", "UN-Nummer"),
    ("hazard_class", "Gefahrgutklasse"),
    ("packing_group", "Verpackungsgruppe"),
    ("density", "Dichte"),
    ("ph_value", "pH-Wert"),
    ("flash_point", "Flammpunkt"),
    ("color", "Farbe"),
    ("odor", "Geruch"),
    ("solubility", "Löslichkeit"),
    ("boiling_point", "Siedepunkt"),
    ("viscosity", "Viskosität"),
    ("wgk", "WGK"),
    ("storage_class", "Lagerklasse"),
    ("limited_quantity", "Begrenzte Mengen (LQ)"),
]

SDS_KEYWORDS = ("sds", "sdb", "sicherheitsdatenblatt", "safety data sheet", "fiche de securite")
DATASHEET_KEYWORDS = ("datenblatt", "datasheet", "technical data sheet", "technisches datenblatt", "product data sheet")
PDF_KEYWORDS = (".pdf", "pdf", "download")
GENERIC_DOCUMENT_LABELS = (
    "brochure",
    "brochures",
    "catalog",
    "catalogue",
    "catalogues",
    "download",
    "downloads",
    "presentation",
    "presentations",
)


@dataclass(slots=True)
class ChemicalExtractionPayload:
    fields: dict[str, object]
    documents: list[dict[str, object]]
    warnings: list[str]
    raw: dict[str, object]
    source_kind: str


class ChemicalSourceAdapter:
    adapter_name = "generic"
    priority = 10

    def matches(self, url: str, html: str, text: str) -> bool:
        return True

    def extract(
        self,
        *,
        url: str,
        html: str,
        text: str,
        links: list[dict[str, str]],
        generic_data: ScrapedData,
    ) -> ChemicalExtractionPayload:
        raise NotImplementedError


class GenericChemicalAdapter(ChemicalSourceAdapter):
    adapter_name = "generic_html"
    priority = 10

    def extract(
        self,
        *,
        url: str,
        html: str,
        text: str,
        links: list[dict[str, str]],
        generic_data: ScrapedData,
    ) -> ChemicalExtractionPayload:
        normalized_text = _normalize_text(text)
        fields: dict[str, object] = {
            "product_name": generic_data.product_name or generic_data.product_title,
            "brand_name": _label_value(normalized_text, ["Marke", "Brand", "Hersteller", "Manufacturer"]),
            "ufi": _normalize_ufi(_label_value(normalized_text, ["UFI", "UFI-Nummer", "UFI Number"])),
            "voc_content_percent": _normalize_voc(_label_value(normalized_text, ["VOC-Gehalt", "VOC Gehalt", "VOC content", "VOC"])),
            "cas_number": _normalize_cas(_label_value(normalized_text, ["CAS Nummer", "CAS-Nummer", "CAS Number", "CAS"])),
            "ec_number": _normalize_ec(_label_value(normalized_text, ["EG-Nummer", "EC Number", "EC-Nummer", "EG Nummer", "EC"])),
            "un_number": _normalize_un(_label_value(normalized_text, ["UN-Nummer", "UN Nummer", "UN Number", "UN"])),
            "hazard_class": _label_value(normalized_text, ["ADR-Klasse", "ADR Klasse", "Gefahrgutklasse", "Hazard Class"]),
            "packing_group": _normalize_packing_group(_label_value(normalized_text, ["Verpackungsgruppe", "Packing Group"])),
            "chemical_type": _label_value(normalized_text, ["Stoffgruppe", "Produkttyp", "Produktgruppe", "Substance Group", "Chemical Type"]),
            "density": _label_value(normalized_text, ["Dichte", "Density"]),
            "ph_value": _label_value(normalized_text, ["pH-Wert", "pH"]),
            "flash_point": _label_value(normalized_text, ["Flammpunkt", "Flash Point"]),
            "color": _label_value(normalized_text, ["Farbe", "Color"]),
            "odor": _label_value(normalized_text, ["Geruch", "Odor", "Odeur"]),
            "solubility": _label_value(normalized_text, ["Löslichkeit", "Loeslichkeit", "Solubility"]),
            "boiling_point": _label_value(normalized_text, ["Siedepunkt", "Siedebereich", "Boiling Point"]),
            "viscosity": _label_value(normalized_text, ["Viskosität", "Viskositaet", "Viscosity"]),
            "storage_class": _label_value(normalized_text, ["Lagerklasse", "Storage Class"]),
            "wgk": _label_value(normalized_text, ["WGK"]),
            "signal_word": _extract_signal_word(normalized_text),
            "ghs_pictograms": "|".join(_extract_ghs_codes(html, normalized_text)) or None,
            "hazard_statements": _extract_statement_block(normalized_text, ["Gefahrenhinweise", "Hazard Statements", "Hazard Statement"]),
            "precautionary_statements": _extract_statement_block(normalized_text, ["Sicherheitshinweise", "Precautionary Statements", "Precautionary Statement"]),
            "business_only": _contains_any(normalized_text, ["nur für gewerbe", "professional use only", "industrielle und technische anwendungen"]),
            "age_check_required": _contains_any(normalized_text, ["altersprüfung", "age verification", "age check"]),
            "shippable": not _contains_any(normalized_text, ["kein versand", "not shippable", "cannot be shipped"]),
            "hazard_shipping_note": _label_value(normalized_text, ["ADR", "Versandhinweise", "Gefahrgutversand", "Shipping Notes"]),
            "limited_quantity": _label_value(normalized_text, ["Begrenzte Mengen (LQ)", "LQ", "Limited Quantity"]),
        }

        if not fields["cas_number"]:
            fields["cas_number"] = _find_regex(normalized_text, r"\b\d{2,7}-\d{2}-\d\b")
        if not fields["ec_number"]:
            fields["ec_number"] = _find_regex(normalized_text, r"\b\d{3}-\d{3}-\d\b")
        if not fields["ufi"]:
            fields["ufi"] = _normalize_ufi(_find_regex(normalized_text, r"\bUFI\s*:?\s*([A-Z0-9]{4}(?:-[A-Z0-9]{4}){3})\b", group=1))
        if not fields["voc_content_percent"]:
            fields["voc_content_percent"] = _normalize_voc(_find_regex(normalized_text, r"\bVOC(?:[- ]?Gehalt| content)?\s*:?\s*([0-9]+(?:[.,][0-9]+)?\s*%?)", group=1))
        if not fields["un_number"]:
            fields["un_number"] = _find_regex(normalized_text, r"\bUN[- ]?(\d{4})\b", group=1)

        adr_line = fields.get("hazard_shipping_note") or ""
        if not fields["hazard_class"]:
            fields["hazard_class"] = _find_regex(str(adr_line), r",\s*([1-9](?:\.\d)?)\b", group=1)
        if not fields["packing_group"]:
            fields["packing_group"] = _normalize_packing_group(_find_regex(str(adr_line), r",\s*(I|II|III)\b"))
        fields["adr_relevant"] = bool(fields.get("un_number") or fields.get("hazard_class") or _contains_any(normalized_text, ["adr", "gefahrgut"]))
        fields["sds_available"] = False

        documents = _collect_documents(url, links, generic_data)
        sds_document = next((item for item in documents if item.get("role") == "sds"), None)
        if sds_document:
            fields["sds_url"] = sds_document.get("url")
            fields["sds_available"] = True
        else:
            fields["sds_url"] = None

        if not fields["hazard_statements"]:
            h_codes = _extract_codes(normalized_text, r"\bH\d{3}[a-z]?\b")
            if h_codes:
                fields["hazard_statements"] = ", ".join(h_codes)
        if not fields["precautionary_statements"]:
            p_codes = _extract_codes(normalized_text, r"\bP\d{3}[a-z]?\b")
            if p_codes:
                fields["precautionary_statements"] = ", ".join(p_codes)

        warnings: list[str] = []
        if not fields.get("cas_number"):
            warnings.append("CAS-Nummer nicht erkannt")
        if not documents:
            warnings.append("Keine Dokumentlinks erkannt")

        raw = {
            "page_title": generic_data.page_title,
            "text_excerpt": normalized_text[:8000],
            "links": documents,
            "specifications": generic_data.specifications,
            "technical_features": generic_data.technical_features,
        }
        return ChemicalExtractionPayload(fields=fields, documents=documents, warnings=warnings, raw=raw, source_kind=self.adapter_name)


class ChemstoreChemicalAdapter(GenericChemicalAdapter):
    adapter_name = "chemstore"
    priority = 100

    def matches(self, url: str, html: str, text: str) -> bool:
        host = urlparse(url).netloc.lower()
        return "chemstore" in host or "zf chemstore" in text.lower()

    def extract(
        self,
        *,
        url: str,
        html: str,
        text: str,
        links: list[dict[str, str]],
        generic_data: ScrapedData,
    ) -> ChemicalExtractionPayload:
        payload = super().extract(url=url, html=html, text=text, links=links, generic_data=generic_data)
        normalized_text = _normalize_text(text)
        payload.fields["brand_name"] = payload.fields.get("brand_name") or _label_value(normalized_text, ["Marke"]) or "ZF Chemstore"
        payload.fields["chemical_type"] = payload.fields.get("chemical_type") or _infer_chemical_type_from_url(url)
        payload.fields["business_only"] = bool(payload.fields.get("business_only") or _contains_any(normalized_text, ["industrielle und technische anwendungen"]))
        payload.fields["age_check_required"] = bool(
            payload.fields.get("age_check_required") or _contains_any(normalized_text, ["abgabe an private neukunden erfolgt ausschliesslich nach altersprüfung"])
        )
        if not payload.fields.get("hazard_shipping_note"):
            payload.fields["hazard_shipping_note"] = _label_value(normalized_text, ["ADR"])
        return payload


ADAPTERS: list[ChemicalSourceAdapter] = [ChemstoreChemicalAdapter(), GenericChemicalAdapter()]


def pick_chemical_adapter(url: str, html: str, text: str) -> ChemicalSourceAdapter:
    for adapter in sorted(ADAPTERS, key=lambda item: item.priority, reverse=True):
        if adapter.matches(url, html, text):
            return adapter
    return GenericChemicalAdapter()


def _collect_documents(url: str, links: list[dict[str, str]], generic_data: ScrapedData) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add_document(candidate_url: str | None, role: str, label: str | None, source: str) -> None:
        if not candidate_url:
            return
        normalized_url = candidate_url.strip()
        if not normalized_url:
            return
        key = (normalized_url, role)
        if key in seen:
            return
        seen.add(key)
        collected.append({"url": normalized_url, "role": role, "label": label or role.upper(), "source": source})

    for candidate in generic_data.sds_urls:
        add_document(candidate, "sds", "SDB / SDS", "generic_data")
    for candidate in generic_data.datasheet_urls:
        add_document(candidate, "datasheet", "Datenblatt", "generic_data")
    for candidate in generic_data.pdf_urls:
        if _is_product_document_link(candidate, "PDF"):
            add_document(candidate, "pdf", "PDF", "generic_data")

    for link in links:
        href = (link.get("url") or "").strip()
        label = (link.get("label") or "").strip()
        lower = f"{href} {label}".lower()
        if any(keyword in lower for keyword in SDS_KEYWORDS):
            add_document(href, "sds", label or "SDB / SDS", "page_link")
        elif any(keyword in lower for keyword in DATASHEET_KEYWORDS):
            add_document(href, "datasheet", label or "Datenblatt", "page_link")
        elif any(keyword in lower for keyword in PDF_KEYWORDS) and _is_product_document_link(href, label):
            add_document(href, "pdf", label or "PDF", "page_link")

    return collected


def _is_product_document_link(url: str | None, label: str | None) -> bool:
    haystack = f"{url or ''} {label or ''}".lower()
    if any(keyword in haystack for keyword in SDS_KEYWORDS + DATASHEET_KEYWORDS):
        return True
    parsed_path = urlparse(url or "").path.lower()
    filename = parsed_path.rsplit("/", 1)[-1]
    label_clean = re.sub(r"\s+", " ", label or "").strip().lower()
    if label_clean in {"pdf", "download", "downloads"}:
        return False
    if any(keyword in label_clean for keyword in GENERIC_DOCUMENT_LABELS):
        return False
    if any(keyword in filename for keyword in GENERIC_DOCUMENT_LABELS):
        return False
    return filename.endswith(".pdf")


def _normalize_text(value: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (value or "").replace("\r", "\n")).strip()


def _label_value(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = rf"(?im)(?:^|\n)\s*{re.escape(label)}\s*:\s*(.+?)(?:\n|$)"
        match = re.search(pattern, text)
        if match:
            return _clean_value(match.group(1))
    return None


def _clean_value(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", value or "").strip(" :-")
    return cleaned or None


def _find_regex(text: str, pattern: str, group: int = 0) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _clean_value(match.group(group))


def _normalize_cas(value: str | None) -> str | None:
    return _find_regex(value or "", r"(\d{2,7}-\d{2}-\d)", group=1) if value else None


def _normalize_ec(value: str | None) -> str | None:
    return _find_regex(value or "", r"(\d{3}-\d{3}-\d)", group=1) if value else None


def _normalize_un(value: str | None) -> str | None:
    if not value:
        return None
    normalized = _find_regex(value, r"(\d{4})", group=1)
    return normalized


def _normalize_ufi(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b([A-Z0-9]{4}(?:-[A-Z0-9]{4}){3})\b", value, flags=re.I)
    return match.group(1).upper() if match else None


def _normalize_voc(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\s*%?", value)
    if not match:
        return None
    return match.group(1).replace(",", ".")


def _normalize_packing_group(value: str | None) -> str | None:
    if not value:
        return None
    return _find_regex(value.upper(), r"\b(I|II|III)\b", group=1)


def _extract_signal_word(text: str) -> str | None:
    if re.search(r"\bGEFAHR\b", text, flags=re.IGNORECASE):
        return "GEFAHR"
    if re.search(r"\bACHTUNG\b", text, flags=re.IGNORECASE):
        return "ACHTUNG"
    if re.search(r"\bDANGER\b", text, flags=re.IGNORECASE):
        return "DANGER"
    if re.search(r"\bWARNING\b", text, flags=re.IGNORECASE):
        return "WARNING"
    return None


def _extract_ghs_codes(html: str, text: str) -> list[str]:
    values = set(code.upper() for code in re.findall(r"GHS0[1-9]", f"{html} {text}", flags=re.IGNORECASE))
    return sorted(values)


def _extract_statement_block(text: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = rf"(?ims)(?:^|\n)\s*{re.escape(label)}\s*:?\s*(.+?)(?=\n\s*[A-ZÄÖÜ0-9][^\n]{{0,80}}:\s|\Z)"
        match = re.search(pattern, text)
        if match:
            return _clean_value(match.group(1))
    return None


def _extract_codes(text: str, pattern: str) -> list[str]:
    return sorted({match.upper() for match in re.findall(pattern, text, flags=re.IGNORECASE)})


def _contains_any(text: str, needles: list[str]) -> bool:
    lower = text.lower()
    return any(needle.lower() in lower for needle in needles)


def _infer_chemical_type_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if "/laugen" in path:
        return "Lauge"
    if "/säuren" in path or "/sauren" in path or "/saeuren" in path:
        return "Säure"
    if "/alkohole" in path:
        return "Alkohol"
    return None
