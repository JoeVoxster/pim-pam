from __future__ import annotations

from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from app.models import AssetReference, ProductInputRow, ProductVariant, ScrapedData


def extract_generic_product_data(page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
    data = page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const pickMeta = (selector) => normalize(document.querySelector(selector)?.getAttribute("content") || "");
            const pickText = (selectors) => {
                for (const selector of selectors) {
                    const node = document.querySelector(selector);
                    const text = normalize(node?.innerText || node?.textContent || "");
                    if (text) return text;
                }
                return null;
            };
            const parseJsonLd = () => {
                const results = [];
                for (const node of document.querySelectorAll("script[type='application/ld+json']")) {
                    try {
                        const parsed = JSON.parse(node.textContent || "null");
                        const items = Array.isArray(parsed) ? parsed : [parsed];
                        for (const item of items) {
                            if (!item) continue;
                            if (Array.isArray(item['@graph'])) {
                                results.push(...item['@graph']);
                            } else {
                                results.push(item);
                            }
                        }
                    } catch (error) {}
                }
                return results;
            };
            const productJsonLd = parseJsonLd().find((item) => {
                const type = item?.['@type'];
                if (Array.isArray(type)) return type.includes('Product');
                return type === 'Product';
            }) || null;
            const textBlob = normalize(document.body?.innerText || document.body?.textContent || "");
            const skuPatterns = [
                /(?:sku|item code|product code|code|cod\\.?|art\\.?|ref\\.?|reference)\\s*[:#-]?\\s*([a-z0-9][a-z0-9._\\/-]{2,})/i,
            ];
            let detectedSku = normalize(
                document.querySelector("[itemprop='sku']")?.textContent ||
                document.querySelector("[data-product-sku]")?.getAttribute("data-product-sku") ||
                productJsonLd?.sku ||
                productJsonLd?.mpn ||
                ""
            );
            if (!detectedSku) {
                for (const pattern of skuPatterns) {
                    const match = textBlob.match(pattern);
                    if (match?.[1]) {
                        detectedSku = normalize(match[1]);
                        break;
                    }
                }
            }
            const features = [];
            for (const selector of ["table tr", ".specifications li", ".technical-data li", ".features li", "ul li"]) {
                for (const node of document.querySelectorAll(selector)) {
                    const text = normalize(node.innerText || node.textContent || "");
                    if (text && text.length > 2 && text.length < 240) {
                        features.push(text);
                    }
                    if (features.length >= 30) break;
                }
                if (features.length) break;
            }
            const specifications = [];
            const specificationRows = [];
            for (const row of document.querySelectorAll(".specifications tr, .product-specifications tr, table tr")) {
                const cells = Array.from(row.querySelectorAll("th, td")).map((cell) => normalize(cell.innerText || cell.textContent || "")).filter(Boolean);
                if (!cells.length || cells.length > 4) continue;
                if (cells[0].toLowerCase() === "downloads") continue;
                specificationRows.push(cells);
                if (cells.length >= 2) {
                    specifications.push(`${cells[0]}: ${cells.slice(1).join(" ")}`);
                } else if (cells[0].length < 240) {
                    specifications.push(cells[0]);
                }
            }
            for (const node of document.querySelectorAll("dt")) {
                const key = normalize(node.innerText || node.textContent || "");
                const value = normalize(node.nextElementSibling?.innerText || node.nextElementSibling?.textContent || "");
                if (key && value) specifications.push(`${key}: ${value}`);
            }
            const imageRefs = [];
            for (const node of document.querySelectorAll("img")) {
                const candidate = node.currentSrc || node.src || node.getAttribute("data-src") || "";
                const contextNode = node.closest("figure, .product, .card, li, article, section, div");
                const contextText = normalize(
                    node.getAttribute("alt") ||
                    node.getAttribute("title") ||
                    node.getAttribute("aria-label") ||
                    contextNode?.querySelector("figcaption")?.innerText ||
                    contextNode?.innerText ||
                    ""
                );
                if (candidate) {
                    imageRefs.push({
                        url: candidate,
                        assetType: "image",
                        role: "image",
                        label: contextText || null,
                        contextText: contextText || null,
                        packaging: normalize((candidate.match(/(\d+(?:[.,]\d+)?)(ml|cl|l|lt|liter|litre|kg|g)/i) || [])[0] || "") || null,
                        supplierSku: normalize((candidate.match(/([a-z]{1,4}\d{1,4}(?:[-_/]?[a-z0-9]{1,8})+)/i) || [])[0] || "") || null,
                    });
                }
            }
            const pdfRefs = [];
            for (const node of document.querySelectorAll("a[href]")) {
                const href = node.getAttribute("href") || "";
                const text = normalize(node.innerText || node.textContent || "");
                const surrounding = normalize(node.closest("li, p, div, section, article")?.innerText || "");
                const combined = `${href} ${text}`.toLowerCase();
                if (
                    combined.includes(".pdf") ||
                    combined.includes("datenblatt") ||
                    combined.includes("manual") ||
                    combined.includes("anleitung") ||
                    combined.includes("catalog") ||
                    combined.includes("scheda") ||
                    combined.includes("sheet") ||
                    href.toLowerCase().includes("/download/")
                ) {
                    let role = "pdf";
                    if (/(sds|safety data sheet|scheda di sicurezza|fiche de securite|hoja de seguridad)/i.test(combined)) {
                        role = "sds";
                    } else if (/(technical|tech sheet|data sheet|datasheet|datenblatt|scheda tecnica)/i.test(combined)) {
                        role = "datasheet";
                    } else if (/(manual|anleitung|instructions)/i.test(combined)) {
                        role = "manual";
                    } else if (/(catalog|catalogue|katalog)/i.test(combined)) {
                        role = "catalog";
                    }
                    pdfRefs.push({
                        url: href,
                        assetType: "pdf",
                        role,
                        label: text || role,
                        contextText: surrounding || text || null,
                    });
                }
            }
            const pageTitle = normalize(document.title);
            const productName = normalize(
                pickText(["h1", ".product-title", "[itemprop='name']", ".woocommerce-product-details__short-description + h1"]) ||
                productJsonLd?.name ||
                pickMeta("meta[property='og:title']")
            );
            const productDescription = normalize(
                pickText([".product-description", ".description", "[itemprop='description']", ".woocommerce-product-details__short-description", "main p"]) ||
                productJsonLd?.description ||
                pickMeta("meta[name='description']")
            );
            const productTitle = normalize(pickMeta("meta[property='og:title']") || pageTitle || productName);
            const barcodePatterns = [
                /(?:barcode|ean|gtin|upc)\s*[:#-]?\s*(\(?\d{8,18}\)?)/i,
                /\(01\)\s*(\d{14})/,
            ];
            let detectedBarcode = normalize(
                document.querySelector("[itemprop='gtin13']")?.textContent ||
                document.querySelector("[itemprop='gtin']")?.textContent ||
                document.querySelector("[itemprop='barcode']")?.textContent ||
                productJsonLd?.gtin13 ||
                productJsonLd?.gtin14 ||
                productJsonLd?.gtin ||
                productJsonLd?.barcode ||
                ""
            );
            if (!detectedBarcode) {
                for (const item of specifications) {
                    for (const pattern of barcodePatterns) {
                        const match = item.match(pattern);
                        if (match?.[1]) {
                            detectedBarcode = normalize(match[1]);
                            break;
                        }
                    }
                    if (detectedBarcode) break;
                }
            }
            if (!detectedBarcode) {
                for (const pattern of barcodePatterns) {
                    const match = textBlob.match(pattern);
                    if (match?.[1]) {
                        detectedBarcode = normalize(match[1]);
                        break;
                    }
                }
            }
            return {
                pageTitle,
                productName,
                productTitle,
                description: productDescription,
                supplierSku: detectedSku || null,
                barcode: detectedBarcode || null,
                specifications: Array.from(new Set(specifications)),
                specificationRows,
                technicalFeatures: Array.from(new Set(features)),
                imageRefs,
                pdfRefs,
                hasProductJsonLd: !!productJsonLd,
            };
        }"""
    )

    page_title = _non_empty(data.get("pageTitle")) or _non_empty(page.title())
    product_name = _non_empty(data.get("productName")) or _non_empty(row.title_raw) or page_title
    product_title = _non_empty(data.get("productTitle")) or product_name or page_title
    description = _non_empty(data.get("description")) or _non_empty(row.description_raw)
    supplier_sku = (
        _normalize_sku(data.get("supplierSku"))
        or _extract_sku_from_text(product_name)
        or _extract_sku_from_text(product_title)
        or _normalize_sku(row.supplier_sku)
    )
    barcode = _normalize_barcode(data.get("barcode")) or _normalize_barcode(row.ean)
    specifications = _filter_specifications([_non_empty(item) for item in data.get("specifications", [])])
    technical_features = [_non_empty(item) for item in data.get("technicalFeatures", [])]
    asset_references = _filter_product_pdf_references(
        _build_asset_references(page.url, data),
        product_name,
        page.url,
    )
    variants = _build_variants(data.get("specificationRows", []), asset_references, product_name, supplier_sku, barcode)
    image_refs = _filter_image_references(
        [asset for asset in asset_references if asset.asset_type == "image"],
        product_name,
        page.url,
    )
    pdf_refs = [asset for asset in asset_references if asset.asset_type == "pdf"]
    image_urls = [asset.url for asset in image_refs]
    pdf_urls = [asset.url for asset in pdf_refs]
    datasheet_urls = [asset.url for asset in pdf_refs if asset.role == "datasheet"]
    sds_urls = [asset.url for asset in pdf_refs if asset.role == "sds"]
    is_product_candidate = _is_product_candidate(
        page.url,
        product_name,
        supplier_sku,
        image_refs,
        pdf_refs,
        bool(data.get("hasProductJsonLd")),
    )

    return ScrapedData(
        source_url_final=page.url,
        supplier_sku=supplier_sku,
        barcode=barcode,
        product_name=product_name,
        product_title=product_title,
        description=description,
        specifications=[item for item in specifications if item],
        variants=variants,
        technical_features=[item for item in technical_features if item],
        image_urls=_unique(image_urls),
        pdf_urls=_unique(pdf_urls),
        datasheet_urls=_unique(datasheet_urls),
        sds_urls=_unique(sds_urls),
        asset_references=[*image_refs, *pdf_refs],
        page_title=page_title,
        is_product_candidate=is_product_candidate,
    )


def _looks_like_asset(value: str, asset_type: str) -> bool:
    lower = value.lower()
    if asset_type == "image":
        return any(token in lower for token in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"])
    return ".pdf" in lower or "pdf" in lower or "/download/" in lower or "file_id-" in lower


def _build_asset_references(page_url: str, data: dict) -> list[AssetReference]:
    assets: list[AssetReference] = []
    for raw in data.get("imageRefs", []):
        url = urljoin(page_url, str(raw.get("url", "")).strip())
        if not _looks_like_asset(url, "image"):
            continue
        assets.append(
            AssetReference(
                url=url,
                asset_type="image",
                role="image",
                label=_non_empty(raw.get("label")),
                context_text=_non_empty(raw.get("contextText")),
                page_url=page_url,
                packaging=_normalize_packaging(_non_empty(raw.get("packaging"))),
                supplier_sku=_extract_sku_from_asset_url(url) or _normalize_sku(_non_empty(raw.get("supplierSku"))),
            )
        )
    for raw in data.get("pdfRefs", []):
        url = urljoin(page_url, str(raw.get("url", "")).strip())
        role = _non_empty(raw.get("role")) or "pdf"
        label = _non_empty(raw.get("label")) or role
        context = _non_empty(raw.get("contextText"))
        if not (_looks_like_asset(url, "pdf") or _looks_like_pdf_reference(url, label, context, role)):
            continue
        assets.append(
            AssetReference(
                url=url,
                asset_type="pdf",
                role=role,
                label=label,
                context_text=context,
                page_url=page_url,
            )
        )
    return _unique_assets(assets)


def _unique_assets(values: list[AssetReference]) -> list[AssetReference]:
    seen: set[tuple[str, str, str | None]] = set()
    output: list[AssetReference] = []
    for value in values:
        key = (value.url, value.asset_type, value.role)
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split()).strip()
    return normalized or None


def _normalize_sku(value: str | None) -> str | None:
    normalized = _non_empty(value)
    if not normalized:
        return None
    if len(normalized) > 80:
        return None
    if " " in normalized:
        return None
    if not any(char.isdigit() for char in normalized):
        return None
    if normalized.lower() in {"home", "about", "quality", "mission", "values", "reference"}:
        return None
    return normalized.strip(":-# ")


def _normalize_barcode(value: str | None) -> str | None:
    normalized = _non_empty(value)
    if not normalized:
        return None
    digits = "".join(char for char in normalized if char.isdigit())
    if len(digits) == 16 and digits.startswith("01"):
        digits = digits[2:]
    if len(digits) not in {8, 12, 13, 14}:
        return None
    return digits


def _normalize_packaging(value: str | None) -> str | None:
    normalized = _non_empty(value)
    if not normalized:
        return None
    match = None
    import re as _re
    match = _re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l|lt|liter|litre|kg|g)\b", normalized, _re.IGNORECASE)
    if not match:
        return None
    amount = match.group(1).replace(",", ".")
    unit = match.group(2).lower()
    unit_map = {"l": "liter", "lt": "liter", "litre": "liter", "liter": "liter"}
    return f"{amount.rstrip('0').rstrip('.') if '.' in amount else amount} {unit_map.get(unit, unit)}"


def _build_variants(
    specification_rows: list[list[str]],
    asset_references: list[AssetReference],
    product_name: str | None,
    fallback_sku: str | None,
    fallback_barcode: str | None,
) -> list[ProductVariant]:
    packaging_values: list[str] = []
    barcode_values: list[str] = []
    for row in specification_rows:
        if not row:
            continue
        key = (row[0] or "").strip().lower()
        value = " ".join(row[1:]).strip() if len(row) > 1 else ""
        if key == "packaging":
            packaging_values.extend(_extract_packaging_values(value))
        elif key == "barcode":
            barcode = _normalize_barcode(value)
            if barcode and barcode not in barcode_values:
                barcode_values.append(barcode)

    asset_variants: dict[str, dict[str, str | None]] = {}
    for asset in asset_references:
        if asset.asset_type != "image":
            continue
        packaging = _normalize_packaging(asset.packaging or asset.context_text or asset.url)
        if not packaging:
            continue
        variant = asset_variants.setdefault(packaging, {})
        variant["supplier_sku"] = _extract_sku_from_asset_url(asset.url) or _normalize_sku(asset.supplier_sku) or variant.get("supplier_sku")

    variants: list[ProductVariant] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    if packaging_values:
        for index, packaging in enumerate(packaging_values):
            barcode = barcode_values[index] if index < len(barcode_values) else None
            image_data = asset_variants.get(packaging, {})
            supplier_sku = image_data.get("supplier_sku") or fallback_sku
            title = " ".join(part for part in [product_name, packaging] if part)
            variant = ProductVariant(
                packaging=packaging,
                supplier_sku=supplier_sku,
                barcode=barcode or fallback_barcode,
                title=title,
            )
            key = (variant.packaging, variant.supplier_sku, variant.barcode)
            if key not in seen:
                seen.add(key)
                variants.append(variant)

    if variants:
        return variants

    return [
        ProductVariant(
            packaging=None,
            supplier_sku=fallback_sku,
            barcode=fallback_barcode,
            title=product_name,
        )
    ]


def _extract_packaging_values(value: str | None) -> list[str]:
    normalized = _non_empty(value)
    if not normalized:
        return []
    import re as _re
    matches = _re.findall(r"(\d+(?:[.,]\d+)?)\s*(ml|cl|l|lt|liter|litre|kg|g)\b", normalized, _re.IGNORECASE)
    results: list[str] = []
    for amount, unit in matches:
        packaging = _normalize_packaging(f"{amount} {unit}")
        if packaging and packaging not in results:
            results.append(packaging)
    return results


def _extract_sku_from_asset_url(url: str | None) -> str | None:
    normalized = _non_empty(url)
    if not normalized:
        return None
    parsed_path = urlparse(normalized).path
    candidates = [segment for segment in parsed_path.split("/") if segment]
    if parsed_path:
        candidates.append(parsed_path)
    import re as _re
    patterns = [
        r"(?:^|[^a-z0-9])([a-z]\d{2}-\d{3}[a-z]\d{0,2})(?:[^a-z0-9]|[a-z]{3,}|$)",
        r"(?:^|[^a-z0-9])([a-z]\d{2}-\d{3}[a-z0-9]{1,4})(?:[^a-z0-9]|$)",
        r"(?:^|[^a-z0-9])([a-z]{1,4}-\d{2,4}[a-z0-9]{1,6})(?:[^a-z0-9]|$)",
        r"(?:^|[^a-z0-9])([a-z]{1,4}\d{2,4}[a-z0-9]{1,6})(?:[^a-z0-9]|$)",
    ]
    for candidate in reversed(candidates):
        for pattern in patterns:
            match = _re.search(pattern, candidate, _re.IGNORECASE)
            if match:
                return _normalize_sku(match.group(1))
    return None


def _looks_like_pdf_reference(
    url: str,
    label: str | None,
    context: str | None,
    role: str | None,
) -> bool:
    haystack = " ".join(part for part in [url, label, context, role] if part).lower()
    return any(
        token in haystack
        for token in (
            "pdf",
            "sheet",
            "datasheet",
            "technical data",
            "safety data",
            "sds",
            "download",
            "scheda",
        )
    )


def _extract_sku_from_text(value: str | None) -> str | None:
    normalized = _non_empty(value)
    if not normalized:
        return None
    candidates = normalized.replace("/", " ").split()
    for candidate in reversed(candidates):
        maybe = _normalize_sku(candidate.strip("()[],:;"))
        if maybe:
            return maybe
    return None


def _is_product_candidate(
    page_url: str,
    product_name: str | None,
    supplier_sku: str | None,
    image_references: list[AssetReference],
    pdf_references: list[AssetReference],
    has_product_jsonld: bool,
) -> bool:
    if not product_name:
        return False

    parsed = urlparse(page_url)
    path = parsed.path.lower().strip("/")
    segments = [segment for segment in path.split("/") if segment]
    blocked_segments = {"about", "contacts", "contact", "news", "download", "quality", "mission-and-values"}
    blocked_product_names = {"www.voxster.ch", "voxster", "home"}
    if not segments:
        return False
    if len(segments) == 1 and len(segments[0]) <= 3:
        return False
    if any(segment in blocked_segments for segment in segments):
        return False
    if product_name.strip().lower() in blocked_product_names:
        return False

    pdf_roles = {asset.role for asset in pdf_references}
    rich_pdf = bool(pdf_roles & {"datasheet", "sds", "manual"})
    detail_url_signal = "/product/" in parsed.path.lower()
    category_detail_signal = parsed.path.lower().endswith(".html") and len(segments) >= 2
    product_url_signal = "product" in path or len(segments) >= 4
    image_count = len(image_references)
    return bool(
        has_product_jsonld
        or (detail_url_signal and image_count >= 1)
        or (category_detail_signal and image_count >= 1)
        or (product_url_signal and (supplier_sku or rich_pdf))
        or (supplier_sku and (rich_pdf or image_count >= 2))
    )


def _filter_image_references(
    image_references: list[AssetReference],
    product_name: str | None,
    page_url: str,
) -> list[AssetReference]:
    product_tokens = {
        token
        for token in (product_name or "").lower().replace("-", " ").split()
        if len(token) > 2 and not token.isdigit()
    }
    page_slug_tokens = {
        token
        for token in urlparse(page_url).path.lower().replace("-", "/").split("/")
        if len(token) > 2
    }
    keep: list[AssetReference] = []
    for asset in image_references:
        lower_url = asset.url.lower()
        lower_context = f"{asset.label or ''} {asset.context_text or ''}".lower()
        if any(blocked in lower_url for blocked in ("logo", "icon", "linkedin", "facebook", "newsletter")):
            continue
        if "banner" in lower_url and not any(token in lower_url for token in product_tokens):
            continue
        if any(marker in lower_url for marker in ("/dacshop/", "/upload/", "/product/")):
            keep.append(asset)
            continue
        if product_tokens and any(token in lower_context or token in lower_url for token in product_tokens):
            keep.append(asset)
            continue
        if page_slug_tokens and any(token in lower_url for token in page_slug_tokens):
            keep.append(asset)
    return _unique_assets(keep)


def _filter_product_pdf_references(
    asset_references: list[AssetReference],
    product_name: str | None,
    page_url: str,
) -> list[AssetReference]:
    pdf_refs = [asset for asset in asset_references if asset.asset_type == "pdf"]
    other_refs = [asset for asset in asset_references if asset.asset_type != "pdf"]
    product_tokens = {
        token
        for token in (product_name or "").lower().replace("-", " ").split()
        if len(token) > 2 and not token.isdigit()
    }
    page_slug = urlparse(page_url).path.lower()
    keep_pdfs: list[AssetReference] = []
    is_detail_page = "/product/" in page_slug
    for asset in pdf_refs:
        haystack = " ".join(
            part.lower()
            for part in [asset.url, asset.label, asset.context_text, asset.role]
            if part
        )
        if "component/dacshop/product/download/" in haystack:
            continue
        if "file_id-" in haystack or "/download/" in haystack and (asset.role in {"sds", "datasheet", "manual"}):
            keep_pdfs.append(asset)
            continue
        if is_detail_page:
            continue
        if product_tokens and any(token in haystack for token in product_tokens):
            keep_pdfs.append(asset)
            continue
        if page_slug and any(segment for segment in page_slug.split("/") if segment and segment in haystack):
            keep_pdfs.append(asset)
    return [*other_refs, *_unique_assets(keep_pdfs)]


def _filter_specifications(values: list[str | None]) -> list[str]:
    blocked_prefixes = {
        "news:",
        "news",
        "downloads",
        "i agree with the terms and conditions",
        "please enable the javascript",
    }
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _non_empty(value)
        if not normalized:
            continue
        lower = normalized.lower()
        if any(lower.startswith(prefix) for prefix in blocked_prefixes):
            continue
        if len(normalized) > 500:
            continue
        if normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output
