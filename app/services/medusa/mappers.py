from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.db.models import Asset, MedusaConnectionConfig, Product, ProductTranslation, ProductVariant, ProductVariantPriceTier, VariantTranslation


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_handle(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "produkt"


@dataclass(frozen=True)
class ProductPayload:
    payload: dict[str, Any]
    hash: str
    locale: str


@dataclass(frozen=True)
class VariantPayload:
    local_id: int
    payload: dict[str, Any]
    hash: str
    sku: str | None


class MedusaProductMapper:
    def __init__(self, config: MedusaConnectionConfig) -> None:
        self.config = config

    def map_product(self, product: Product, *, translation: ProductTranslation | None, image_urls: list[str]) -> ProductPayload:
        use_base_fields = bool(translation and translation.language_code == product.source_language)
        title = _text(product.title if use_base_fields else (translation.title if translation and translation.title else product.title))
        description = _text(product.description if use_base_fields else (translation.description if translation and translation.description else product.description))
        short_description = _text(translation.short_description if translation and translation.short_description else None)
        handle_source = product.handle if use_base_fields else (translation.slug if translation and translation.slug else product.handle or title)
        handle = normalize_handle(handle_source)
        thumbnail = image_urls[0] if image_urls else None
        metadata = {
            "pim_product_id": product.id,
            "pim_handle": product.handle,
            "pim_updated_at": product.updated_at.isoformat() if product.updated_at else None,
            "source": "pim-pam",
            "source_language": product.source_language,
            "family_key": product.family_key,
            "is_chemical": bool(product.is_chemical),
            "pim_category_sort_positions": _category_sort_positions(product),
            "seo_title": translation.seo_title if translation else None,
            "seo_description": translation.seo_description if translation else None,
        }
        payload: dict[str, Any] = {
            "title": title,
            "subtitle": short_description,
            "description": description,
            "handle": handle,
            "status": _medusa_status(product.status, self.config.product_status_default),
            "thumbnail": thumbnail,
            "images": [{"url": url} for url in image_urls],
            "external_id": f"pim:{product.id}",
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "", [], {})},
        }
        if product.brand:
            payload["metadata"]["brand"] = product.brand.name
        options = product_options(product)
        if options:
            payload["options"] = options
        return ProductPayload(payload=_prune(payload), hash=stable_hash(payload), locale=self.config.default_locale or "de-CH")


class MedusaVariantMapper:
    def __init__(self, config: MedusaConnectionConfig) -> None:
        self.config = config

    def map_variant(self, variant: ProductVariant, *, translation: VariantTranslation | None) -> VariantPayload:
        title = _text(translation.title if translation and translation.title else (variant.variant_title or variant.option_value or variant.packaging or variant.sku))
        option_name = variant_option_name(variant)
        option_value = translation.package_label if translation and translation.package_label else variant_option_value(variant, fallback=title)
        metadata = {
            "pim_variant_id": variant.id,
            "pim_product_id": variant.product_id,
            "pim_updated_at": variant.updated_at.isoformat() if variant.updated_at else None,
            "pim_hash": None,
            "source": "pim-pam",
            "cost_price": str(variant.cost_price) if variant.cost_price is not None else None,
            "cost_currency": variant.cost_currency,
        }
        payload: dict[str, Any] = {
            "title": title,
            "sku": variant.sku,
            "barcode": variant.barcode,
            "options": {option_name: option_value},
            "manage_inventory": True,
            "allow_backorder": False,
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "", [], {})},
        }
        payload = _prune(payload)
        payload["metadata"]["pim_hash"] = stable_hash(payload)
        return VariantPayload(local_id=variant.id, payload=payload, hash=stable_hash(payload), sku=variant.sku)


def product_options(product: Product) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for variant in sorted(product.variants or [], key=lambda item: item.id or 0):
        if str(variant.status or "").lower() == "archived":
            continue
        name = variant_option_name(variant)
        value = variant_option_value(variant)
        if not name or not value:
            continue
        grouped.setdefault(name, [])
        if value not in grouped[name]:
            grouped[name].append(value)
    return [{"title": title, "values": values} for title, values in grouped.items() if values]


def _category_sort_positions(product: Product) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mapping in sorted(product.channel_category_mappings or [], key=lambda item: (item.sales_channel_id, item.position, item.channel_category_id)):
        category = mapping.channel_category
        if category is None:
            continue
        rows.append(
            {
                "sales_channel": mapping.sales_channel.name if mapping.sales_channel else str(mapping.sales_channel_id),
                "sales_channel_code": mapping.sales_channel.code if mapping.sales_channel else None,
                "channel_category_id": mapping.channel_category_id,
                "external_category_id": category.external_category_id,
                "category_handle": category.external_category_id,
                "category_path": category.external_path,
                "position": int(mapping.position if mapping.position is not None else 9999),
            }
        )
    return rows


def variant_option_name(variant: ProductVariant) -> str:
    return _text(variant.option_name or "Variante")


def variant_option_value(variant: ProductVariant, *, fallback: str | None = None) -> str:
    return _text(variant.option_value or variant.packaging or fallback or variant.variant_title or variant.sku or "Standard")


class MedusaAssetMapper:
    def __init__(self, public_base_url: str | None = None) -> None:
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None

    def image_urls(self, product: Product, variant: ProductVariant | None = None) -> list[str]:
        assets = list(product.assets or [])
        if variant is not None:
            assets.extend(list(variant.assets or []))
        urls: list[str] = []
        for asset in _preferred_assets([asset for asset in assets if str(asset.mime_type or "").startswith("image/")]):
            url = self.asset_url(asset)
            if url and url not in urls:
                urls.append(url)
        return urls

    def asset_url(self, asset: Asset) -> str | None:
        if asset.storage_provider in {"cloudflare_r2", "bunny_storage"} and asset.object_key:
            if self.public_base_url:
                return f"{self.public_base_url}/{asset.object_key.lstrip('/')}"
            if asset.public_url:
                return asset.public_url
        if asset.source_url and str(asset.source_url).startswith(("http://", "https://")):
            return asset.source_url
        return None


class MedusaTranslationMapper:
    PRODUCT_FIELDS = ("title", "short_description", "description", "seo_title", "seo_description", "slug")

    def product_translation_payload(self, translation: ProductTranslation) -> dict[str, Any]:
        return _prune(
            {
                "title": translation.title,
                "subtitle": translation.short_description,
                "description": translation.description,
                "seo_title": translation.seo_title,
                "seo_description": translation.seo_description,
                "handle": translation.slug,
            }
        )

    def variant_translation_payload(self, translation: VariantTranslation) -> dict[str, Any]:
        return _prune({"title": translation.title, "option_label_override": translation.option_label_override, "package_label": translation.package_label})


class MedusaPricingMapper:
    def variant_prices(self, variant: ProductVariant) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if variant.price is not None and variant.currency:
            rows.append(
                {
                    "currency_code": variant.currency.lower(),
                    "amount": money_to_medusa_amount(variant.price),
                    "min_quantity": None,
                    "max_quantity": None,
                    "price_list_code": None,
                    "metadata": {"pim_variant_id": variant.id, "source": "pim-pam", "price_type": "sale"},
                }
            )
        for tier in variant.price_tiers or []:
            if tier.price_type != "sale":
                continue
            if tier.min_qty == 1 and tier.max_qty is None and variant.price is not None and variant.currency:
                continue
            rows.append(self.price_tier_payload(variant, tier))
        validate_tiered_prices(rows)
        return rows

    def price_tier_payload(self, variant: ProductVariant, tier: ProductVariantPriceTier) -> dict[str, Any]:
        return {
            "currency_code": tier.currency.lower(),
            "amount": money_to_medusa_amount(tier.price),
            "min_quantity": tier.min_qty if tier.min_qty != 1 or tier.max_qty is not None else None,
            "max_quantity": tier.max_qty,
            "price_list_code": "default",
            "metadata": {
                "pim_price_id": tier.id,
                "pim_variant_id": variant.id,
                "pim_hash": stable_hash({"tier": tier.id, "price": str(tier.price), "currency": tier.currency, "min": tier.min_qty, "max": tier.max_qty}),
                "source": "pim-pam",
                "price_type": tier.price_type,
            },
        }


def money_to_medusa_amount(value: Decimal | float | str | int) -> int | float:
    normalized = Decimal(str(value)).quantize(Decimal("0.01"))
    return int(normalized) if normalized == normalized.to_integral_value() else float(normalized)


def validate_tiered_prices(prices: list[dict[str, Any]]) -> None:
    seen_ranges: dict[tuple[str, str | None], list[tuple[int | None, int | None]]] = {}
    for price in prices:
        amount = int(price["amount"])
        if amount < 0:
            raise ValueError("Preis darf nicht negativ sein.")
        currency = str(price.get("currency_code") or "").lower()
        if not re.fullmatch(r"[a-z]{3}", currency):
            raise ValueError(f"Ungültige Währung: {currency}")
        min_qty = price.get("min_quantity")
        max_qty = price.get("max_quantity")
        if min_qty is not None and int(min_qty) <= 0:
            raise ValueError("min_quantity muss positiv sein.")
        if max_qty is not None and min_qty is not None and int(max_qty) < int(min_qty):
            raise ValueError("max_quantity muss >= min_quantity sein.")
        key = (currency, price.get("price_list_code"))
        for existing_min, existing_max in seen_ranges.setdefault(key, []):
            if _overlaps(min_qty, max_qty, existing_min, existing_max):
                raise ValueError("Preisstaffeln überlappen sich innerhalb gleicher Währung/Preisliste.")
        seen_ranges[key].append((min_qty, max_qty))


def _overlaps(a_min: int | None, a_max: int | None, b_min: int | None, b_max: int | None) -> bool:
    if a_min is None and a_max is None:
        return b_min is None and b_max is None
    if b_min is None and b_max is None:
        return False
    amin = int(a_min or 1)
    bmin = int(b_min or 1)
    amax = int(a_max or 10**12)
    bmax = int(b_max or 10**12)
    return amin <= bmax and bmin <= amax


def _medusa_status(local_status: str | None, default_status: str | None) -> str:
    status = str(local_status or "").lower()
    if status in {"published", "active", "ready", "ok"}:
        return "published"
    if status in {"draft", "archived", "deleted"}:
        return "draft"
    return default_status or "draft"


def _text(value: str | None) -> str:
    return str(value or "").strip()


def _prune(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _preferred_assets(assets: list[Asset]) -> list[Asset]:
    grouped: dict[str, list[Asset]] = {}
    for asset in assets:
        key = asset.checksum or asset.object_key or asset.source_url or f"asset:{asset.id}"
        grouped.setdefault(key, []).append(asset)
    preferred = [
        sorted(group, key=lambda item: (0 if item.storage_provider in {"cloudflare_r2", "bunny_storage"} and item.object_key else 1, item.sort_order, item.id))[0]
        for group in grouped.values()
    ]
    return sorted(preferred, key=lambda item: (item.sort_order, item.id))
