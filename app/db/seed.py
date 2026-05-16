from __future__ import annotations

from app.db.session import session_scope
from app.schemas.pim import ProductCreate, VariantCreate
from app.services.pim_service import (
    DEFAULT_CATEGORY_CHANNEL_CODE,
    create_product,
    ensure_default_sales_channels,
    get_or_create_categories,
    set_product_categories_for_channel,
)
from app.utils.pim_config import get_pim_settings


def main() -> int:
    settings = get_pim_settings()
    with session_scope(settings.database_url) as session:
        ensure_default_sales_channels(session)
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="DEMO-001",
                title="Demo Product",
                brand_name="Demo Brand",
                status="active",
                description="Seeded demo product for local development.",
            ),
            VariantCreate(sku="DEMO-001", variant_title="Default Variant", price="19.99", currency="EUR", stock_qty=10),
        )
        categories = get_or_create_categories(session, ["Demo > Starter"], sales_channel_code=DEFAULT_CATEGORY_CHANNEL_CODE)
        set_product_categories_for_channel(
            session,
            product,
            [category.id for category in categories],
            sales_channel_code=DEFAULT_CATEGORY_CHANNEL_CODE,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
