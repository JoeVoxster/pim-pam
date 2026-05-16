from __future__ import annotations

import argparse
from typing import Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import joinedload

from app.db.models import ImportJob, ImportRow, Product, ProductVariant
from app.db.session import session_scope
from app.etl.pim_assets import sync_product_assets
from app.models import ProductOutputRow
from app.utils.pim_config import get_pim_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Backfill PIM assets from prior import payloads')
    parser.add_argument('--source-name', default=None, help='ImportJob source_name to backfill from; defaults to latest pim_import')
    parser.add_argument('--brand', default=None, help='Optional brand slug filter, e.g. voxster')
    return parser.parse_args()


def _iter_rows(session, source_name: str | None) -> tuple[ImportJob, Iterable[ImportRow]]:
    stmt = select(ImportJob).where(ImportJob.job_type == 'pim_import').order_by(desc(ImportJob.id))
    if source_name:
        stmt = stmt.where(ImportJob.source_name == source_name)
    job = session.scalars(stmt).first()
    if job is None:
        raise RuntimeError('Kein passender ImportJob gefunden')
    rows = session.scalars(
        select(ImportRow).where(ImportRow.job_id == job.id).order_by(ImportRow.id.asc())
    ).all()
    return job, rows


def main() -> int:
    args = parse_args()
    settings = get_pim_settings()
    with session_scope(settings.database_url) as session:
        job, rows = _iter_rows(session, args.source_name)
        imported_asset_keys: set[tuple[int, str]] = set()
        repaired = 0
        touched_variants = 0
        for row in rows:
            payload = row.raw_payload_json or {}
            try:
                product = ProductOutputRow.model_validate(payload)
            except Exception:
                continue
            variant_sku = product.variant_sku or product.supplier_sku
            db_variant = session.scalars(
                select(ProductVariant)
                .options(joinedload(ProductVariant.product).joinedload(Product.brand))
                .where(ProductVariant.sku == variant_sku)
            ).first()
            if db_variant is None or db_variant.product is None:
                continue
            db_product = db_variant.product
            brand_slug = (db_product.brand.slug.lower() if db_product.brand and db_product.brand.slug else None)
            if args.brand and brand_slug != args.brand.lower():
                continue
            changed = sync_product_assets(
                session=session,
                product=product,
                db_product=db_product,
                db_variant=db_variant,
                storage_root=settings.asset_storage_root,
                imported_asset_keys=imported_asset_keys,
            )
            if changed:
                touched_variants += 1
                repaired += changed
        print({'job_id': job.id, 'source_name': job.source_name, 'brand': args.brand, 'assets_created_or_repaired': repaired, 'variants_touched': touched_variants})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
