from __future__ import annotations

import html
import re
from dataclasses import replace
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from app.models import ProductInputRow, ScrapedData
from app.suppliers.base import BaseSupplierExtractor, CrawlConfig, SupplierAssetCandidate, SupplierExtractionResult


BOILERPLATE_PATTERNS = (
    "javascript seems to be disabled",
    "javaScript scheint in Ihrem Browser deaktiviert".lower(),
    "another custom cms block displayed as a tab",
    "home about back mission and values",
    "mission and values quality brands certifications",
)


class SupplierExtractor(BaseSupplierExtractor):
    supplier_key = "tintolav"
    supplier_name = "Tintolav"
    supported_domains = ("tintolav.com", "tintolav.it", "tintolav.ch", "tintolove.ch")

    def crawl_config(self, start_url: str | None = None) -> CrawlConfig:
        return CrawlConfig(
            preferred_url_substrings=("/products/", "/product/"),
            blocked_url_substrings=("/customer/", "/cart/", "/checkout/", "/account/"),
            product_url_patterns=(r"/product/[^/]+\.html",),
            allow_cross_scope=True,
        )

    def extract(self, page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
        result = self.extract_from_html(page.url or source_url, page.content())
        fallback_sku = row.supplier_sku if row.supplier_sku != "crawl" else None
        if fallback_sku and not result.product_code:
            result = replace(result, product_code=fallback_sku)
        return result.to_scraped_data()

    def classify_product_candidate(self, page_url: str, scraped: ScrapedData) -> bool:
        return bool(scraped.extra_fields.get("supplier_key") == self.supplier_key and (scraped.product_name or scraped.description or scraped.asset_references))

    def extract_from_html(self, source_url: str, body: str) -> SupplierExtractionResult:
        source_domain = urlparse(source_url).netloc.lower()
        warnings: list[str] = []
        product_name = _clean_text(_first_match(body, r"<h1[^>]*>(.*?)</h1>"))
        title = _clean_text(_first_match(body, r"<title[^>]*>(.*?)</title>"))
        if not product_name and title:
            product_name = re.sub(r"\s*[-|]\s*Tintolav.*$", "", title, flags=re.I).strip()
        product_code = _extract_product_code(body, source_url)
        sku = product_code or _extract_variant_sku(body, source_url)
        raw_sections = _extract_sections(body)
        for name, value in list(raw_sections.items()):
            if name in {"Function", "Packaging"}:
                continue
            if _is_boilerplate(value):
                warnings.append(f"Irrelevanter Tintolav-Block ignoriert: {name}")
                raw_sections.pop(name, None)
        description = raw_sections.get("Description")
        specifications = raw_sections.get("Specifications")
        how_to_use, quantity_for_use, warning = _split_how_to_use(raw_sections.get("How To Use"))
        ingredients = raw_sections.get("Ingredients")
        ingredient_search = raw_sections.get("Ingredient Search")
        function = raw_sections.get("Function")
        packaging = raw_sections.get("Packaging") or _extract_packaging(specifications) or _extract_packaging_from_body(body)
        combined_description = _combine_description(
            description=description,
            how_to_use=how_to_use,
            quantity_for_use=quantity_for_use,
            warning=warning,
            function=function,
            ingredients=ingredients,
        )
        pdfs, images = _extract_assets(source_url, body)
        confidence = 0.72
        if description:
            confidence += 0.08
        if specifications:
            confidence += 0.04
        if product_code:
            confidence += 0.05
        if product_name:
            confidence += 0.04
        if not raw_sections and not pdfs and not images:
            warnings.append("Keine strukturierten Tintolav-Produktsektionen gefunden.")
            confidence = 0.2
        return SupplierExtractionResult(
            supplier_key=self.supplier_key,
            supplier_name=self.supplier_name,
            source_url=source_url,
            source_domain=source_domain,
            detected_language="en",
            source_locale="en",
            product_code=product_code,
            sku=sku,
            product_name=product_name or title,
            short_description=_shorten(description),
            description=combined_description or description,
            specifications=specifications,
            how_to_use=how_to_use,
            quantity_for_use=quantity_for_use,
            warning=warning,
            ingredients=ingredients,
            ingredient_search=ingredient_search,
            function=function,
            packaging=packaging,
            pdfs=pdfs,
            images=images,
            raw_sections=raw_sections,
            warnings=warnings,
            confidence=min(confidence, 0.95),
        )


def _combine_description(
    *,
    description: str | None,
    how_to_use: str | None,
    quantity_for_use: str | None,
    warning: str | None,
    function: str | None,
    ingredients: str | None,
) -> str | None:
    parts: list[str] = []
    if description:
        parts.append(description.strip())
    if how_to_use:
        parts.append(f"How to use:\n{how_to_use.strip()}")
    if quantity_for_use:
        parts.append(quantity_for_use.strip())
    if warning:
        parts.append(f"Warning:\n{warning.strip()}")
    if function:
        parts.append(f"Function:\n{function.strip()}")
    if ingredients:
        parts.append(f"Ingredients:\n{ingredients.strip()}")
    return "\n\n".join(part for part in parts if part).strip() or None


def _extract_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    description = _clean_description_text(_first_match(body, r"<div[^>]+id=[\"']dacshop_product_description_main[\"'][^>]*>(.*?)</div>"))
    if description:
        description = re.sub(r"^Description\s+", "", description, flags=re.I).strip()
        if _valid_text(description):
            sections["Description"] = description
    custom_info = _first_match(
        body,
        r"<div[^>]+id=[\"']dacshop_product_custom_info_main[\"'][^>]*>(.*?)</div>\s*</div>\s*<div[^>]+id=[\"']dacshop_product_files_main",
    )
    if custom_info:
        spec_lines: list[str] = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", custom_info, flags=re.I | re.S):
            raw_label = _clean_text(_first_match(row, r"<label[^>]*>(.*?)</label>"))
            label = _normalize_label(raw_label)
            value = _clean_text(
                _first_match(
                    row,
                    r"<td[^>]*>\s*<span[^>]+class=[\"'][^\"']*dacshop_product_custom_value[^\"']*[\"'][^>]*>(.*?)</span>\s*</td>",
                )
            )
            if label and (_valid_text(value) or label in {"Function", "Packaging"}):
                sections[label] = value
                spec_lines.append(f"{label}:\n{value}")
            elif raw_label and _valid_text(value):
                display_label = " ".join(raw_label.split()).strip()
                sections[display_label] = value
                spec_lines.append(f"{display_label}:\n{value}")
        if spec_lines:
            sections["Specifications"] = "\n\n".join(spec_lines)
    for title, content in _heading_blocks(body):
        label = _normalize_label(title)
        if label and label not in sections and _valid_text(content):
            sections[label] = content
    return sections


def _split_how_to_use(value: str | None) -> tuple[str | None, str | None, str | None]:
    if not value:
        return None, None, None
    text = value.strip()
    warning = None
    warning_match = re.search(r"Warning\s*:\s*(.+)$", text, flags=re.I | re.S)
    if warning_match:
        warning = _sentence_case(_clean_text(warning_match.group(1)))
        text = text[: warning_match.start()].strip()
    quantity = None
    quantity_match = re.search(r"Quantity\s+for\s+use\s*:\s*(.+)$", text, flags=re.I | re.S)
    if quantity_match:
        quantity = _format_quantity_for_use(quantity_match.group(1))
        text = text[: quantity_match.start()].strip()
    return _clean_text(text), quantity, warning


def _format_quantity_for_use(value: str | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    intro_match = re.search(r"(dilute\s+1\s+part[^.]*\.)", text, flags=re.I)
    intro = _sentence_case(intro_match.group(1)) if intro_match else None
    resistant = re.search(r"Resistant fabrics\s*:\s*With brush\s*([0-9.,]+),\s*By spraying\s*([0-9.,]+)", text, flags=re.I)
    delicate = re.search(r"Delicate fabrics\s*:\s*With brush\s*([0-9.,]+),\s*By spraying\s*([0-9.,]+)", text, flags=re.I)
    lines: list[str] = []
    if intro:
        lines.extend(["Quantity for use:", intro, ""])
    if resistant:
        lines.extend(["Resistant fabrics:", f"- With brush: {resistant.group(1)}", f"- By spraying: {resistant.group(2)}", ""])
    if delicate:
        lines.extend(["Delicate fabrics:", f"- With brush: {delicate.group(1)}", f"- By spraying: {delicate.group(2)}"])
    return "\n".join(line for line in lines if line is not None).strip() or text


def _heading_blocks(body: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    pattern = r"<(?:h2|h3|h4|strong|label)[^>]*>(Description|Specifications|How\s*To\s*Use|How to use|Ingredients|Function|Packaging|Downloads|PDF|Safety Data Sheet|Technical Sheet|Images)</(?:h2|h3|h4|strong|label)>"
    matches = list(re.finditer(pattern, body, flags=re.I | re.S))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else min(len(body), start + 4000)
        blocks.append((_clean_text(match.group(1)), _clean_text(body[start:end])))
    return blocks


def _extract_assets(source_url: str, body: str) -> tuple[list[SupplierAssetCandidate], list[SupplierAssetCandidate]]:
    pdfs: list[SupplierAssetCandidate] = []
    images: list[SupplierAssetCandidate] = []
    seen: set[str] = set()
    files_block = _first_match(body, r"<div[^>]+id=[\"']dacshop_product_files_main[\"'][^>]*>(.*?)</div>") or ""
    for href, text in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", files_block, flags=re.I | re.S):
        label = _clean_text(text)
        url = urljoin(source_url, html.unescape(href.strip()))
        combined = f"{url} {label}".lower()
        if ".pdf" not in combined and "download" not in combined and "sheet" not in combined:
            continue
        role = "unknown"
        if any(token in combined for token in ("sds", "safety data sheet", "security sheet")):
            role = "sds"
        elif any(token in combined for token in ("technical", "data sheet", "datasheet", "tds")):
            role = "technical_datasheet"
        elif "sheet" in combined:
            role = "product_sheet"
        if url not in seen:
            seen.add(url)
            pdfs.append(SupplierAssetCandidate(asset_url=url, asset_type=role, role=role, title=label or role, filename=url.rsplit("/", 1)[-1], language="en"))
    image_candidates: list[tuple[str, str | None]] = []
    og_image = _clean_text(_first_match(body, r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']"))
    if og_image:
        image_candidates.append((og_image, "product image"))
    product_image_blocks = re.findall(r"<div[^>]+class=[\"'][^\"']*dacshop_product_main_image[^\"']*[\"'][^>]*>(.*?)</div>", body, flags=re.I | re.S)
    for block in product_image_blocks:
        for src, alt in re.findall(r"<img[^>]+(?:src|data-src)=[\"']([^\"']+)[\"'][^>]*(?:alt=[\"']([^\"']*)[\"'])?", block, flags=re.I | re.S):
            image_candidates.append((src, alt))
    for src, alt in image_candidates:
        url = urljoin(source_url, html.unescape(src.strip()))
        if not re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", url, flags=re.I):
            continue
        if url not in seen:
            seen.add(url)
            images.append(SupplierAssetCandidate(asset_url=url, asset_type="image", role="image", title=_clean_text(alt), filename=url.rsplit("/", 1)[-1], language="en"))
    return pdfs, images


def _extract_product_code(body: str, source_url: str) -> str | None:
    for value in (
        _clean_text(_first_match(body, r"<h1[^>]*>(.*?)</h1>")),
        _clean_text(_first_match(body, r"class=[\"'][^\"']*dacshop_product_code[^\"']*[\"'][^>]*>(.*?)</")),
        _clean_text(_first_match(body, r"(?:Product\s*Code|Code|SKU)\s*</[^>]+>\s*<[^>]+>(.*?)</")),
        source_url.rsplit("/", 1)[-1],
    ):
        match = re.search(r"[A-Z]\d{2}-\d{3}[A-Z0-9]*", value or "", flags=re.I)
        if match:
            return match.group(0).upper()
    return None


def _extract_variant_sku(body: str, source_url: str) -> str | None:
    candidates = [
        _clean_text(_first_match(body, r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)[\"']")),
        _clean_text(_first_match(body, r"<img[^>]+alt=[\"']([^\"']*[a-z]\d{2}-\d{3}[^\"']*)[\"']")),
        source_url,
    ]
    for value in candidates:
        match = re.search(r"[a-z]\d{2}-\d{3}[a-z0-9_-]+", value or "", flags=re.I)
        if match:
            return match.group(0).lower().replace("_", "-")
    return None


def _normalize_label(value: str | None) -> str | None:
    label = " ".join(str(value or "").split()).strip().lower()
    mapping = {
        "description": "Description",
        "specifications": "Specifications",
        "specification": "Specifications",
        "how to use": "How To Use",
        "how use": "How To Use",
        "ingredients": "Ingredients",
        "ingredient search": "Ingredient Search",
        "function": "Function",
        "packaging": "Packaging",
        "downloads": "Downloads",
        "pdf": "Downloads",
        "safety data sheet": "Downloads",
        "technical sheet": "Downloads",
    }
    return mapping.get(label)


def _extract_packaging(specifications: str | None) -> str | None:
    match = re.search(r"(?:Packaging|Package)\s*[:\s]+([^\n|]+)", specifications or "", flags=re.I)
    return match.group(1).strip() if match else None


def _extract_packaging_from_body(body: str) -> str | None:
    selected = _clean_text(_first_match(body, r"<option[^>]+selected=[\"']selected[\"'][^>]*>(.*?)</option>"))
    if selected:
        return selected
    return _clean_text(_first_match(body, r"<td>\s*Packaging\s*</td>\s*<td>.*?<option[^>]*>(.*?)</option>"))


def _shorten(value: str | None) -> str | None:
    if not value:
        return None
    first_sentence = re.split(r"(?<=[.!?])\s+", value.strip())[0]
    return first_sentence[:320].strip()


def _sentence_case(value: str | None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    return text[:1].upper() + text[1:]


def _valid_text(value: str | None) -> bool:
    text = (value or "").strip()
    return len(text) >= 8 and not _is_boilerplate(text)


def _is_boilerplate(value: str | None) -> bool:
    text = " ".join(str(value or "").split()).strip().lower()
    if not text:
        return True
    if any(pattern in text for pattern in BOILERPLATE_PATTERNS):
        return True
    return len(text) < 20 and not re.search(r"\d|[.!?]", text)


def _first_match(value: str, pattern: str) -> str | None:
    match = re.search(pattern, value or "", flags=re.I | re.S)
    return match.group(1) if match else None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|tr|h2|h3|h4)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip() or None


def _clean_description_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<h[1-6][^>]*>\s*Description\s*</h[1-6]>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<li[^>]*>(.*?)</li>", lambda match: "\n- " + (_clean_text(match.group(1)) or ""), text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|ul|ol)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line.lower() != "description"]
    return "\n".join(lines).strip() or None
