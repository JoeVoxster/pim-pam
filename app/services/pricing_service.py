from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import CostSurcharge, CurrencyRate, PriceList, ProductVariant, ProductVariantPriceTier
from app.schemas.pim import CostSurchargeUpsert, CurrencyRateUpsert, PriceListUpsert, VariantPriceTierCreate


def _parse_datetime_value(value: str | datetime | None) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for date_format in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Datum bitte als TT.MM.JJJJ oder YYYY-MM-DD erfassen.") from exc


def upsert_variant_price_tier(session: Session, payload: VariantPriceTierCreate) -> ProductVariantPriceTier:
    tier = session.scalar(
        select(ProductVariantPriceTier).where(
            ProductVariantPriceTier.variant_id == payload.variant_id,
            ProductVariantPriceTier.price_list_id == payload.price_list_id,
            ProductVariantPriceTier.price_type == payload.price_type,
            ProductVariantPriceTier.currency == payload.currency,
            ProductVariantPriceTier.min_qty == payload.min_qty,
            ProductVariantPriceTier.max_qty == payload.max_qty,
        )
    )
    if tier is None:
        tier = ProductVariantPriceTier(**payload.model_dump())
        session.add(tier)
    else:
        tier.price_list_id = payload.price_list_id
        tier.price = payload.price
        tier.status = payload.status or "active"
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is not None:
        _sync_variant_from_base_tier(variant, tier)
    session.flush()
    return tier


def update_variant_price_tier(session: Session, tier_id: int, payload: VariantPriceTierCreate) -> ProductVariantPriceTier:
    tier = session.get(ProductVariantPriceTier, tier_id)
    if tier is None:
        raise ValueError("Price tier not found")
    tier.variant_id = payload.variant_id
    tier.price_list_id = payload.price_list_id
    tier.price_type = payload.price_type
    tier.min_qty = payload.min_qty
    tier.max_qty = payload.max_qty
    tier.price = payload.price
    tier.currency = payload.currency
    tier.status = payload.status or "active"
    variant = session.get(ProductVariant, payload.variant_id)
    if variant is not None:
        _sync_variant_from_base_tier(variant, tier)
    session.flush()
    return tier


def delete_variant_price_tier(session: Session, tier_id: int) -> None:
    tier = session.get(ProductVariantPriceTier, tier_id)
    if tier is None:
        raise ValueError("Price tier not found")
    variant = tier.variant
    session.delete(tier)
    if variant is not None:
        _rebuild_variant_base_price_from_tiers(variant)
    session.flush()


def _base_tier(variant: ProductVariant, price_type: str, currency: str | None) -> ProductVariantPriceTier | None:
    for tier in variant.price_tiers:
        if tier.price_list_id is not None:
            continue
        if tier.price_type != price_type:
            continue
        if tier.min_qty != 1:
            continue
        if tier.max_qty is not None:
            continue
        if currency and tier.currency != currency:
            continue
        return tier
    return None

def list_price_lists(session: Session) -> list[dict]:
    rows = session.scalars(
        select(PriceList)
        .options(joinedload(PriceList.sales_channel))
        .order_by(PriceList.status.asc(), PriceList.code.asc())
    ).unique()
    return [
        {
            "id": price_list.id,
            "code": price_list.code,
            "name": price_list.name,
            "price_list_type": price_list.price_list_type,
            "sales_channel_id": price_list.sales_channel_id,
            "sales_channel_code": price_list.sales_channel.code if price_list.sales_channel else None,
            "sales_channel_name": price_list.sales_channel.name if price_list.sales_channel else "Alle Kanäle",
            "currency": price_list.currency,
            "valid_from": price_list.valid_from.isoformat() if price_list.valid_from else None,
            "valid_to": price_list.valid_to.isoformat() if price_list.valid_to else None,
            "status": price_list.status,
            "label": f"{price_list.code} · {price_list.currency} · {(price_list.sales_channel.name if price_list.sales_channel else 'Alle Kanäle')}",
        }
        for price_list in rows
    ]


def upsert_price_list(session: Session, payload: PriceListUpsert) -> PriceList:
    code = (payload.code or "").strip()
    if not code:
        raise ValueError("Preislisten-Code ist Pflicht.")
    currency = (payload.currency or "").strip().upper()
    if not currency:
        raise ValueError("Währung ist Pflicht.")
    price_list = session.get(PriceList, payload.id) if payload.id else None
    if price_list is None:
        price_list = session.scalar(select(PriceList).where(PriceList.code == code))
    existing = session.scalar(select(PriceList).where(PriceList.code == code, PriceList.id != (price_list.id if price_list else 0)))
    if existing is not None:
        raise ValueError("Preislisten-Code ist bereits vergeben.")
    if price_list is None:
        price_list = PriceList(code=code, name=payload.name or code, currency=currency)
        session.add(price_list)
    price_list.code = code
    price_list.name = (payload.name or code).strip()
    price_list.price_list_type = (payload.price_list_type or "sale").strip()
    price_list.sales_channel_id = payload.sales_channel_id
    price_list.currency = currency
    price_list.valid_from = _parse_datetime_value(payload.valid_from)
    price_list.valid_to = _parse_datetime_value(payload.valid_to)
    price_list.status = (payload.status or "active").strip()
    session.flush()
    return price_list


def list_currency_rates(session: Session) -> list[dict]:
    rows = session.scalars(select(CurrencyRate).order_by(CurrencyRate.source_currency.asc(), CurrencyRate.target_currency.asc()))
    output = []
    for row in rows:
        calculated_rate = Decimal(row.effective_rate) * (Decimal("1") + Decimal(row.markup_percent) / Decimal("100"))
        output.append(
            {
                "id": row.id,
                "source_currency": row.source_currency,
                "target_currency": row.target_currency,
                "effective_rate": float(row.effective_rate),
                "markup_percent": float(row.markup_percent),
                "calculated_rate": float(round(calculated_rate, 6)),
                "used_rate": float(row.used_rate),
                "rate_date": row.rate_date.isoformat() if row.rate_date else None,
                "status": row.status,
            }
        )
    return output


def upsert_currency_rate(session: Session, payload: CurrencyRateUpsert) -> CurrencyRate:
    source_currency = (payload.source_currency or "").strip().upper()
    target_currency = (payload.target_currency or "").strip().upper()
    if not source_currency or not target_currency:
        raise ValueError("Quell- und Zielwährung sind Pflicht.")
    row = session.get(CurrencyRate, payload.id) if payload.id else None
    if row is None:
        row = session.scalar(select(CurrencyRate).where(CurrencyRate.source_currency == source_currency, CurrencyRate.target_currency == target_currency))
    if row is None:
        row = CurrencyRate(source_currency=source_currency, target_currency=target_currency, effective_rate=payload.effective_rate, used_rate=payload.used_rate)
        session.add(row)
    row.source_currency = source_currency
    row.target_currency = target_currency
    row.effective_rate = payload.effective_rate
    row.markup_percent = payload.markup_percent
    row.used_rate = payload.used_rate
    row.rate_date = _parse_datetime_value(payload.rate_date)
    row.status = payload.status or "active"
    session.flush()
    return row


def list_cost_surcharges(session: Session) -> list[dict]:
    rows = session.scalars(select(CostSurcharge).order_by(CostSurcharge.surcharge_type.asc(), CostSurcharge.scope_type.asc(), CostSurcharge.code.asc()))
    return [
        {
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "surcharge_type": row.surcharge_type,
            "scope_type": row.scope_type,
            "scope_value": row.scope_value,
            "percent": float(row.percent),
            "factor": float(Decimal("1") + Decimal(row.percent) / Decimal("100")),
            "status": row.status,
        }
        for row in rows
    ]


def upsert_cost_surcharge(session: Session, payload: CostSurchargeUpsert) -> CostSurcharge:
    code = (payload.code or "").strip()
    if not code:
        raise ValueError("Zuschlags-Code ist Pflicht.")
    row = session.get(CostSurcharge, payload.id) if payload.id else None
    if row is None:
        row = session.scalar(select(CostSurcharge).where(CostSurcharge.code == code))
    existing = session.scalar(select(CostSurcharge).where(CostSurcharge.code == code, CostSurcharge.id != (row.id if row else 0)))
    if existing is not None:
        raise ValueError("Zuschlags-Code ist bereits vergeben.")
    if row is None:
        row = CostSurcharge(code=code, name=payload.name or code, surcharge_type=payload.surcharge_type, percent=payload.percent)
        session.add(row)
    row.code = code
    row.name = (payload.name or code).strip()
    row.surcharge_type = (payload.surcharge_type or "transport").strip()
    row.scope_type = (payload.scope_type or "global").strip()
    row.scope_value = (payload.scope_value or "").strip() or None
    row.percent = payload.percent
    row.status = payload.status or "active"
    session.flush()
    return row


def _sync_base_tier_from_variant(session: Session, variant: ProductVariant, price_type: str) -> None:
    if price_type == "sale":
        amount = variant.price
        currency = variant.currency
    else:
        amount = variant.cost_price
        currency = variant.cost_currency
    if amount is None or not currency:
        return
    tier = _base_tier(variant, price_type, currency)
    if tier is None:
        tier = ProductVariantPriceTier(
            variant_id=variant.id,
            price_type=price_type,
            min_qty=1,
            max_qty=None,
            price=amount,
            currency=currency,
        )
        session.add(tier)
    else:
        tier.price = amount
        tier.currency = currency


def _sync_variant_from_base_tier(variant: ProductVariant, tier: ProductVariantPriceTier) -> None:
    if tier.price_list_id is not None:
        return
    if tier.min_qty != 1:
        return
    if tier.max_qty is not None:
        return
    if tier.price_type == "sale":
        variant.price = tier.price
        variant.currency = tier.currency
    elif tier.price_type == "purchase":
        variant.cost_price = tier.price
        variant.cost_currency = tier.currency


def _rebuild_variant_base_price_from_tiers(variant: ProductVariant) -> None:
    sale_candidates = [tier for tier in variant.price_tiers if tier.price_list_id is None and tier.price_type == "sale" and tier.min_qty == 1 and tier.max_qty is None]
    purchase_candidates = [tier for tier in variant.price_tiers if tier.price_list_id is None and tier.price_type == "purchase" and tier.min_qty == 1 and tier.max_qty is None]
    if sale_candidates:
        sale_candidates.sort(key=lambda tier: tier.id)
        variant.price = sale_candidates[0].price
        variant.currency = sale_candidates[0].currency
    if purchase_candidates:
        purchase_candidates.sort(key=lambda tier: tier.id)
        variant.cost_price = purchase_candidates[0].price
        variant.cost_currency = purchase_candidates[0].currency


def margin_metrics(variant: ProductVariant) -> tuple[float | None, float | None]:
    if variant.price is None or variant.cost_price is None:
        return None, None
    sale = Decimal(variant.price)
    cost = Decimal(variant.cost_price)
    margin_amount = sale - cost
    if sale == 0:
        return float(margin_amount), None
    return float(margin_amount), float(round((margin_amount / sale) * Decimal("100"), 2))


def _matching_purchase_price_with_currency(variant: ProductVariant, sale_tier: ProductVariantPriceTier) -> tuple[Decimal | None, str | None]:
    matching = [
        tier
        for tier in variant.price_tiers
        if tier.price_type == "purchase"
        and (tier.status or "active") == "active"
        if tier.min_qty <= sale_tier.min_qty and (tier.max_qty is None or tier.max_qty >= sale_tier.min_qty)
    ]
    if matching:
        matching.sort(key=lambda item: (item.currency == sale_tier.currency, item.min_qty), reverse=True)
        return Decimal(matching[0].price), matching[0].currency
    if variant.cost_price is not None:
        return Decimal(variant.cost_price), variant.cost_currency or sale_tier.currency
    return None, None


def _currency_rate(session: Session, source_currency: str | None, target_currency: str | None) -> Decimal | None:
    source = (source_currency or "").strip().upper()
    target = (target_currency or "").strip().upper()
    if not source or not target:
        return None
    if source == target:
        return Decimal("1")
    row = session.scalar(
        select(CurrencyRate).where(
            CurrencyRate.source_currency == source,
            CurrencyRate.target_currency == target,
            CurrencyRate.status == "active",
        )
    )
    return Decimal(row.used_rate) if row else None


def _surcharge_matches(surcharge: CostSurcharge, variant: ProductVariant) -> bool:
    scope_type = (surcharge.scope_type or "global").lower()
    value = str(surcharge.scope_value or "").strip().lower()
    if scope_type == "global":
        return True
    if scope_type == "supplier":
        brand_name = (variant.product.brand.name if variant.product and variant.product.brand else "").lower()
        return bool(value and value == brand_name)
    if scope_type == "product":
        product = variant.product
        return bool(product and value in {str(product.id).lower(), str(product.sku or "").lower()})
    if scope_type == "variant":
        return value in {str(variant.id).lower(), str(variant.sku or "").lower()}
    if scope_type == "product_group":
        product = variant.product
        if not product or not value:
            return False
        category_values = set()
        for link in product.category_links:
            category = link.category
            if category is None:
                continue
            category_values.update({str(category.id).lower(), str(category.name or "").lower(), str(category.slug or "").lower()})
        return value in category_values
    if scope_type == "country":
        origin_country = str(variant.origin_country or "").strip().lower()
        return bool(value and value == origin_country)
    return False


def _surcharge_priority(surcharge: CostSurcharge) -> int:
    return {"variant": 50, "product": 40, "supplier": 30, "product_group": 20, "country": 15, "global": 10}.get((surcharge.scope_type or "global").lower(), 0)


def _active_surcharge_details(session: Session, variant: ProductVariant) -> tuple[Decimal, list[str]]:
    rows = list(session.scalars(select(CostSurcharge).where(CostSurcharge.status == "active")))
    best_by_type: dict[str, CostSurcharge] = {}
    for surcharge in rows:
        if not _surcharge_matches(surcharge, variant):
            continue
        key = surcharge.surcharge_type
        existing = best_by_type.get(key)
        if existing is None or _surcharge_priority(surcharge) > _surcharge_priority(existing):
            best_by_type[key] = surcharge
    factor = Decimal("1")
    details = []
    for surcharge in sorted(best_by_type.values(), key=lambda item: item.surcharge_type):
        surcharge_factor = Decimal("1") + Decimal(surcharge.percent) / Decimal("100")
        factor *= surcharge_factor
        details.append(f"{surcharge.name}: {surcharge.percent}%")
    return factor, details


def calculated_tier_margin_metrics(session: Session, variant: ProductVariant, tier: ProductVariantPriceTier) -> dict[str, object]:
    if tier.price_type not in {"sale", "special", "override"}:
        return {}
    purchase_price, purchase_currency = _matching_purchase_price_with_currency(variant, tier)
    if purchase_price is None:
        return {}
    rate = _currency_rate(session, purchase_currency, tier.currency)
    if rate is None:
        return {"calc_warning": f"Kurs {purchase_currency}/{tier.currency} fehlt"}
    surcharge_factor, surcharge_details = _active_surcharge_details(session, variant)
    calculated_cost = purchase_price * rate * surcharge_factor
    sale = Decimal(tier.price)
    margin_amount = sale - calculated_cost
    margin_percent = None if sale == 0 else (margin_amount / sale) * Decimal("100")
    total_margin_amount = margin_amount * Decimal(tier.min_qty or 1)
    return {
        "calc_purchase_price": float(round(purchase_price, 2)),
        "calc_purchase_currency": purchase_currency,
        "calc_fx_rate": float(round(rate, 6)),
        "calc_surcharges": " | ".join(surcharge_details),
        "calc_cost": float(round(calculated_cost, 2)),
        "calc_margin_amount": float(round(margin_amount, 2)),
        "calc_margin_percent": None if margin_percent is None else float(round(margin_percent, 2)),
        "calc_total_margin_amount": float(round(total_margin_amount, 2)),
    }


def _serialize_price_tier(variant: ProductVariant, tier: ProductVariantPriceTier, session: Session | None = None) -> dict:
    row = {
        "id": tier.id,
        "price_list_id": tier.price_list_id,
        "price_list_code": tier.price_list.code if tier.price_list else None,
        "price_list_name": tier.price_list.name if tier.price_list else "Basispreis",
        "sales_channel_name": tier.price_list.sales_channel.name if tier.price_list and tier.price_list.sales_channel else ("Alle Kanäle" if tier.price_list else None),
        "valid_from": tier.price_list.valid_from.isoformat() if tier.price_list and tier.price_list.valid_from else None,
        "valid_to": tier.price_list.valid_to.isoformat() if tier.price_list and tier.price_list.valid_to else None,
        "status": tier.status,
        "price_type": tier.price_type,
        "min_qty": tier.min_qty,
        "max_qty": tier.max_qty,
        "price": float(tier.price),
        "currency": tier.currency,
    }
    if session is not None:
        row.update(calculated_tier_margin_metrics(session, variant, tier))
    return row


def _sorted_price_tiers(variant: ProductVariant) -> list[ProductVariantPriceTier]:
    return sorted(
        list(variant.price_tiers),
        key=lambda tier: (
            tier.price_list.code if tier.price_list else "",
            0 if tier.price_type == "sale" else 1,
            tier.min_qty,
            0 if tier.max_qty is None else 1,
            tier.max_qty if tier.max_qty is not None else 0,
            tier.id,
        ),
    )
