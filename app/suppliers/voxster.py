from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import re
from slugify import slugify
from urllib.parse import urlparse

from playwright.sync_api import Page

from app.models import ProductInputRow, ProductVariant, ScrapedData
from app.scraping.extractors import extract_generic_product_data
from app.suppliers.base import BaseSupplierExtractor, CrawlConfig


class SupplierExtractor(BaseSupplierExtractor):
    supplier_key = "voxster"

    def crawl_config(self, start_url: str | None = None) -> CrawlConfig:
        return CrawlConfig(
            preferred_url_substrings=(
                "/check-in/",
                "/verpacken/",
                "/ausstatten/",
                "/bueglerei/",
                "/detachieren/",
                "/waschen/",
                "/reinigen/",
            ),
            blocked_url_substrings=(
                "/catalog/product_compare/",
                "/checkout/",
                "/customer/",
                "?mode=",
                "?dir=",
                "javascript:",
            ),
            product_url_patterns=(
                r"^/check-in/[^/?]+\.html$",
                r"^/verpacken/[^/?]+\.html$",
                r"^/ausstatten/[^/?]+\.html$",
                r"^/bueglerei/[^/?]+\.html$",
                r"^/detachieren/[^/?]+\.html$",
                r"^/waschen/[^/?]+\.html$",
                r"^/reinigen/[^/?]+\.html$",
            ),
        )

    def extract(self, page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
        scraped = extract_generic_product_data(page, source_url or row.source_url or page.url, row)
        page_signals = page.evaluate(
            """() => ({
                hasProductView: document.body?.classList.contains("catalog-product-view") || !!document.querySelector(".product-view"),
                hasAddToCart: !!document.querySelector(".add-to-cart, .btn-cart, button[title*='Warenkorb']"),
                hasPriceBox: !!document.querySelector(".price-box"),
                hasCategoryProducts: !!document.querySelector(".category-products, .category-products-grid, .products-grid"),
            })"""
        )
        scraped.has_product_view = bool(page_signals.get("hasProductView"))
        scraped.has_add_to_cart = bool(page_signals.get("hasAddToCart"))
        scraped.has_price_box = bool(page_signals.get("hasPriceBox"))
        scraped.has_category_products = bool(page_signals.get("hasCategoryProducts"))
        scraped.extra_fields.update(_extract_voxster_commerce_fields(page))

        article_sku = str(scraped.extra_fields.get("article_number") or "").strip() or None
        if article_sku:
            scraped.supplier_sku = article_sku
        elif _looks_like_measurement_sku(scraped.supplier_sku, scraped.product_name or scraped.product_title):
            scraped.supplier_sku = _derive_voxster_sku(scraped.product_name or scraped.product_title, page.url)
        elif not scraped.supplier_sku:
            scraped.supplier_sku = _derive_voxster_sku(scraped.product_name or scraped.product_title, page.url)
        table_variants = _extract_voxster_table_variants(page, scraped.supplier_sku, scraped.product_name or scraped.product_title)
        if table_variants:
            scraped.variants = table_variants
        else:
            table_products = _extract_voxster_table_products(page, scraped.supplier_sku, scraped.product_name or scraped.product_title)
            if table_products:
                scraped.variants = table_products
        return scraped

    def classify_product_candidate(self, page_url: str, scraped: ScrapedData) -> bool:
        path = urlparse(page_url).path.lower()
        page_name = (scraped.product_name or "").strip()
        page_title = (scraped.product_title or "").strip()
        if not page_name:
            return False

        blocked_names = {
            "www.voxster.ch",
            "voxster",
            "check-in",
            "verpacken",
            "ausstatten",
            "bueglerei",
            "detachieren",
            "waschen",
            "reinigen",
        }
        if page_name.lower() in blocked_names or page_title.lower() in blocked_names:
            return False

        is_detail_path = self.crawl_config().matches_product_url(path)
        has_asset_signal = bool(scraped.image_urls or scraped.pdf_urls or scraped.datasheet_urls or scraped.sds_urls)
        has_content_signal = bool(scraped.description or scraped.specifications or scraped.technical_features)
        has_sku_signal = bool(scraped.supplier_sku)
        is_category_grid = scraped.has_category_products and not scraped.has_product_view
        has_product_page_signal = scraped.has_product_view or scraped.has_add_to_cart

        if is_category_grid:
            return False
        if is_detail_path and has_product_page_signal and (has_asset_signal or has_content_signal):
            return True
        if scraped.is_product_candidate and has_product_page_signal and (has_asset_signal or has_sku_signal):
            return True
        return False


def _looks_like_measurement_sku(value: str | None, product_name: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    if re.fullmatch(r"\d+\.\d+", normalized):
        return True
    if re.fullmatch(r"\d{1,3}", normalized):
        title = (product_name or "").lower()
        if re.search(rf"\b{re.escape(normalized)}\s*(mm|cm|ml|cl|gr|g|kg|m|meter|gramm)\b", title):
            return True
    return False


def _derive_voxster_sku(product_name: str | None, page_url: str) -> str | None:
    title = (product_name or "").strip()
    title_patterns = [
        r"\b(?:kombi|ace-clipper|bostitch|ace)\s+([a-z0-9]{2,8})\b",
        r"\b([a-z]{1,3}_?[a-z0-9]{1,6})\b",
    ]
    for pattern in title_patterns:
        for match in re.finditer(pattern, title, re.IGNORECASE):
            candidate = match.group(1).upper()
            if not _looks_like_measurement_sku(candidate, title):
                return candidate

    slug = urlparse(page_url).path.rsplit("/", 1)[-1].removesuffix(".html")
    for token in reversed([part for part in slug.split("-") if part]):
        candidate = token.upper()
        if re.fullmatch(r"[A-Z0-9]{2,8}", candidate) and not _looks_like_measurement_sku(candidate, title):
            return candidate
    return None


def _extract_voxster_commerce_fields(page: Page) -> dict[str, object]:
    payload = page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const articleText = normalize(
                Array.from(document.querySelectorAll("div, span, p"))
                    .map((node) => node.textContent || "")
                    .find((text) => /Artikel\\s*:/.test(text)) || ""
            );
            const priceText = normalize(document.querySelector(".product-view .price-box .price")?.textContent || "");
            const tierItems = Array.from(document.querySelectorAll(".product-view .tier-prices li")).map((node) => normalize(node.textContent || ""));
            return {
                articleText,
                priceText,
                tierItems,
            };
        }"""
    )
    result: dict[str, object] = {}
    article_match = re.search(r"Artikel\s*:\s*([A-Z0-9_-]{2,})", str(payload.get("articleText") or ""), re.IGNORECASE)
    if article_match:
        result["article_number"] = article_match.group(1).upper()

    parsed_price = _parse_chf_price(str(payload.get("priceText") or ""))
    if parsed_price is not None:
        result["sales_price"] = parsed_price
        result["sales_currency"] = "CHF"

    tier_items = payload.get("tierItems") or []
    if isinstance(tier_items, list):
        for item in tier_items:
            text = str(item)
            qty_match = re.search(r"Kaufen Sie\s+(\d+)", text, re.IGNORECASE)
            price_match = re.search(r"CHF\s*([0-9]+(?:[.,][0-9]+)?)", text, re.IGNORECASE)
            if qty_match and price_match:
                tier_price = _parse_chf_price(price_match.group(0))
                if tier_price is None:
                    continue
                result["min_qty"] = int(qty_match.group(1))
                result["tier_price"] = tier_price
                result["sales_currency"] = "CHF"
                break
    return result


def _extract_voxster_table_variants(page: Page, parent_sku: str | None, base_product_name: str | None) -> list[ProductVariant]:
    rows = _extract_voxster_table_rows(page)
    if not isinstance(rows, list) or not rows:
        return []

    base = (base_product_name or "").strip()
    if not base:
        return []

    # Only treat a table as a variant matrix when the row titles share the
    # exact product title as prefix and differ only by a compact suffix such as
    # color, size, or length. Broad assortment tables like "Kassenfarbbänder"
    # must not become pseudo-variants.
    prefixed_rows = [
        str(row.get("title") or "").strip()
        for row in rows
        if str(row.get("title") or "").strip().lower().startswith(base.lower())
    ]
    if len(prefixed_rows) != len(rows):
        return []

    variants: list[ProductVariant] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        option_value = _derive_color_variant(base_product_name, title)
        if not option_value:
            continue
        if not _is_simple_variant_option(option_value):
            return []
        price = _parse_chf_price(str(row.get("priceText") or ""))
        tier_text = str(row.get("tierText") or "")
        tier_qty_match = re.search(r"Kaufen Sie\s+(\d+)", tier_text, re.IGNORECASE)
        tier_price_match = re.search(r"CHF\s*([0-9]+(?:[.,][0-9]+)?)", tier_text, re.IGNORECASE)
        extra_fields: dict[str, object] = {}
        if price is not None:
            extra_fields["sales_price"] = price
            extra_fields["sales_currency"] = "CHF"
        if tier_qty_match and tier_price_match:
            parsed_tier_price = _parse_chf_price(tier_price_match.group(0))
            if parsed_tier_price is not None:
                extra_fields["min_qty"] = int(tier_qty_match.group(1))
                extra_fields["tier_price"] = parsed_tier_price
                extra_fields["sales_currency"] = "CHF"
        variant_sku = _build_variant_sku(parent_sku, option_value)
        variants.append(
            ProductVariant(
                supplier_sku=variant_sku,
                title=option_value,
                option_name="Farbe",
                option_value=option_value,
                price=price,
                currency="CHF" if price is not None else None,
                extra_fields=extra_fields,
            )
        )
    return variants


def _extract_voxster_table_products(page: Page, parent_sku: str | None, base_product_name: str | None) -> list[ProductVariant]:
    rows = _extract_voxster_table_rows(page)
    if not isinstance(rows, list) or not rows:
        return []

    standalone_products: list[ProductVariant] = []
    for index, row in enumerate(rows, start=1):
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        price = _parse_chf_price(str(row.get("priceText") or ""))
        tier_text = str(row.get("tierText") or "")
        tier_qty_match = re.search(r"Kaufen Sie\s+(\d+)", tier_text, re.IGNORECASE)
        tier_price_match = re.search(r"CHF\s*([0-9]+(?:[.,][0-9]+)?)", tier_text, re.IGNORECASE)
        extra_fields: dict[str, object] = {
            "parent_product_name": (base_product_name or "").strip() or None,
        }
        if price is not None:
            extra_fields["sales_price"] = price
            extra_fields["sales_currency"] = "CHF"
        if tier_qty_match and tier_price_match:
            parsed_tier_price = _parse_chf_price(tier_price_match.group(0))
            if parsed_tier_price is not None:
                extra_fields["min_qty"] = int(tier_qty_match.group(1))
                extra_fields["tier_price"] = parsed_tier_price
                extra_fields["sales_currency"] = "CHF"
        standalone_products.append(
            ProductVariant(
                supplier_sku=_build_table_product_sku(parent_sku, title, index),
                title=title,
                is_standalone_product=True,
                price=price,
                currency="CHF" if price is not None else None,
                extra_fields=extra_fields,
            )
        )
    return standalone_products


def _extract_voxster_table_rows(page: Page) -> list[dict[str, str]]:
    payload = page.evaluate(
        """() => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const rows = Array.from(document.querySelectorAll("#super-product-table tbody tr")).map((row) => {
                const cells = Array.from(row.querySelectorAll("td"));
                const title = normalize(cells[0]?.textContent || "");
                const priceText = normalize(cells[1]?.querySelector(".price")?.textContent || "");
                const tierText = normalize(cells[1]?.querySelector(".tier-prices")?.textContent || "");
                return { title, priceText, tierText };
            }).filter((row) => row.title);
            return { rows };
        }"""
    )
    rows = payload.get("rows") or []
    return rows if isinstance(rows, list) else []


def _derive_color_variant(base_product_name: str | None, row_title: str) -> str | None:
    base = (base_product_name or "").strip()
    title = row_title.strip()
    if not title:
        return None
    if base and title.lower().startswith(base.lower()):
        suffix = title[len(base):].strip(" ,-/")
        return suffix or None
    if "," in title:
        return title.rsplit(",", 1)[-1].strip()
    return title


def _is_simple_variant_option(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if len(normalized) > 32:
        return False
    if "," in normalized:
        return False
    words = [part for part in re.split(r"\s+", normalized) if part]
    if len(words) > 3:
        return False
    return True


def _build_variant_sku(parent_sku: str | None, option_value: str) -> str | None:
    if not parent_sku:
        return None
    normalized = slugify(option_value, separator="_").upper()
    return f"{parent_sku}_{normalized}" if normalized else parent_sku


def _build_table_product_sku(parent_sku: str | None, title: str, index: int) -> str:
    base = (parent_sku or "VOXSTER").strip().upper()
    slug_part = slugify(title, separator="_").upper()[:24].strip("_")
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:6].upper()
    if slug_part:
        return f"{base}_{slug_part}_{digest}"
    return f"{base}_{index:03d}_{digest}"


def _parse_chf_price(value: str) -> str | None:
    match = re.search(r"CHF\s*([0-9]+(?:[.,][0-9]+)?)", value, re.IGNORECASE)
    if not match:
        return None
    try:
        return str(Decimal(match.group(1).replace(",", ".")).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None
