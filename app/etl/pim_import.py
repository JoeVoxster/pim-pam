from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import load_settings
from app.db.models import ImportJob, ImportRow, ProductVariant
from app.db.session import session_scope
from app.etl.mapping import category_values, price_tier_payload, product_payload, product_short_description, raw_payload, variant_payload
from app.etl.pim_assets import sync_product_assets
from app.main import run_pipeline
from app.models import ProductOutputRow
from app.schemas.pim import ImportMappingConfig, VariantPriceTierCreate
from app.services.pim_service import (
    DEFAULT_CATEGORY_CHANNEL_CODE,
    get_or_create_categories,
    set_product_categories_for_channel,
    set_product_translation_short_description,
    upsert_product_with_variant,
    upsert_variant_price_tier,
)
from app.utils.pim_config import get_pim_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import cleaned ETL data into the PIM database")
    parser.add_argument("--input", help="Path to supplier input CSV/XLSX for the ETL pipeline")
    parser.add_argument("--clean-file", help="Path to products_clean.csv or products_clean.xlsx")
    parser.add_argument("--output-dir", default="./output/pim_import", help="Temporary ETL output directory")
    parser.add_argument("--config", default=None, help="Optional ETL config file")
    parser.add_argument("--mapping-config", default="config.pim_import.yaml", help="YAML mapping config")
    parser.add_argument("--source-name", default=None, help="Logical source name for the import job")
    parser.add_argument("--sales-channel-code", default=None, help="Category target sales channel code (default: voxster)")
    parser.add_argument("--sheet-name", default=None)
    parser.add_argument("--sheet-index", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mapping_config = load_mapping_config(args.mapping_config)
    source_name = args.source_name or Path(args.clean_file or args.input or "unknown").name

    with session_scope(get_pim_settings().database_url) as session:
        run_pim_import(
            session=session,
            source_name=source_name,
            mapping_config=mapping_config,
            dry_run=args.dry_run,
            input_path=args.input,
            clean_file=args.clean_file,
            output_dir=args.output_dir,
            etl_config_path=args.config,
            sales_channel_code=args.sales_channel_code,
            sheet_name=args.sheet_name,
            sheet_index=args.sheet_index,
        )
    return 0


def load_mapping_config(path: str | Path | None) -> ImportMappingConfig:
    if not path:
        return ImportMappingConfig()
    file_path = Path(path)
    if not file_path.exists():
        return ImportMappingConfig()
    payload = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    return ImportMappingConfig.model_validate(payload)


def load_clean_products(path: str | Path, sheet_name: str | None = None, sheet_index: int | None = None) -> list[ProductOutputRow]:
    file_path = Path(path)
    if file_path.suffix.lower() in {".xlsx", ".xls"}:
        read_sheet = sheet_name if sheet_name is not None else sheet_index
        frame = pd.read_excel(file_path, sheet_name=read_sheet if read_sheet is not None else 0)
    else:
        frame = pd.read_csv(file_path)
    records = frame.to_dict(orient="records")
    products: list[ProductOutputRow] = []
    for record in records:
        record = {key: _clean_record_value(value) for key, value in record.items()}
        extra_fields = record.get("extra_fields")
        if isinstance(extra_fields, str):
            try:
                record["extra_fields"] = json.loads(extra_fields)
            except json.JSONDecodeError:
                record["extra_fields"] = {}
        elif extra_fields is None:
            record["extra_fields"] = {}
        products.append(ProductOutputRow.model_validate(record))
    return products


def _clean_record_value(value: object) -> object:
    if pd.isna(value):
        return None
    return value


def prepare_clean_products(
    input_path: str | Path | None,
    clean_file: str | Path | None,
    output_dir: str | Path,
    etl_config_path: str | None,
    sheet_name: str | None,
    sheet_index: int | None,
) -> tuple[list[ProductOutputRow], Path | None]:
    generated_output_dir: Path | None = None
    if clean_file:
        return load_clean_products(clean_file, sheet_name=sheet_name, sheet_index=sheet_index), None
    if input_path is None:
        raise ValueError("Either input_path or clean_file must be provided")

    settings = load_settings(etl_config_path)
    generated_output_dir = Path(output_dir)
    run_pipeline(
        input_path=input_path,
        output_dir=generated_output_dir,
        settings=settings,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
    )
    return load_clean_products(generated_output_dir / "products_clean.csv"), generated_output_dir


def create_import_job(session: Session, source_name: str, dry_run: bool) -> ImportJob:
    job = ImportJob(source_name=source_name, job_type="pim_import", status="running", summary_json={"dry_run": dry_run})
    session.add(job)
    session.flush()
    return job


def _should_upsert_base_tier(
    variant_data: dict[str, Any],
    tier_data: dict[str, object] | None,
    price_type: str,
) -> bool:
    amount_key = "price" if price_type == "sale" else "cost_price"
    currency_key = "currency" if price_type == "sale" else "cost_currency"
    amount = variant_data.get(amount_key)
    currency = variant_data.get(currency_key)
    if amount is None or not currency:
        return False
    if not tier_data:
        return True
    if str(tier_data.get("price_type")) != price_type:
        return True
    if price_type == "purchase":
        return False
    if int(tier_data.get("min_qty") or 1) != 1:
        return True
    if tier_data.get("max_qty") is not None:
        return True
    if str(tier_data.get("currency")) != str(currency):
        return True
    return False


def run_pim_import(
    session: Session,
    source_name: str,
    mapping_config: ImportMappingConfig | None = None,
    dry_run: bool = False,
    input_path: str | Path | None = None,
    clean_file: str | Path | None = None,
    output_dir: str | Path = "./output/pim_import",
    etl_config_path: str | None = None,
    sales_channel_code: str | None = None,
    sheet_name: str | None = None,
    sheet_index: int | None = None,
) -> dict[str, Any]:
    config = mapping_config or ImportMappingConfig()
    target_sales_channel_code = (sales_channel_code or config.sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE).strip() or DEFAULT_CATEGORY_CHANNEL_CODE
    products, generated_output_dir = prepare_clean_products(
        input_path=input_path,
        clean_file=clean_file,
        output_dir=output_dir,
        etl_config_path=etl_config_path,
        sheet_name=sheet_name,
        sheet_index=sheet_index,
    )
    settings = get_pim_settings()
    settings.asset_storage_root.mkdir(parents=True, exist_ok=True)

    job = create_import_job(session, source_name=source_name, dry_run=dry_run)
    created = 0
    updated = 0
    errors = 0
    imported_product_assets: set[tuple[int, str]] = set()

    for index, product in enumerate(products, start=1):
        row = ImportRow(
            job_id=job.id,
            external_id=product.variant_sku or product.supplier_sku,
            row_index=index,
            status="pending",
            raw_payload_json=raw_payload(product),
        )
        session.add(row)
        session.flush()

        try:
            payload = product_payload(product)
            variant = variant_payload(product, config)
            tier_payload = price_tier_payload(product, config)
            variant_sku = str(variant["sku"])
            was_existing = session.scalar(select(ProductVariant.id).where(ProductVariant.sku == variant_sku)) is not None
            if dry_run:
                category_count = len(category_values(product, config))
                asset_count = sum(1 for source_path in asset_paths(product) if Path(source_path).exists())
                tier_count = 1 if tier_payload else 0
                row.status = "dry_run"
                row.message = (
                    f"would_{'update' if was_existing else 'create'} "
                    f"categories={category_count} assets={asset_count} tiers={tier_count}"
                )
            else:
                db_product, db_variant = upsert_product_with_variant(
                    session=session,
                    sku=str(payload["sku"]),
                    source_language=str(payload.get("source_language") or "en"),
                    title=str(payload["title"]),
                    description=payload.get("description"),
                    source_url=payload.get("source_url"),
                    source_url_final=payload.get("source_url_final"),
                    specifications_text=payload.get("specifications_text"),
                    technical_features_text=payload.get("technical_features_text"),
                    brand_name=payload.get("brand_name"),
                    status=str(payload["status"]),
                    variant_sku=variant_sku,
                    variant_title=variant.get("variant_title"),
                    option_name=variant.get("option_name"),
                    option_value=variant.get("option_value"),
                    packaging=variant.get("packaging"),
                    price=variant.get("price"),
                    currency=variant.get("currency"),
                    cost_price=variant.get("cost_price"),
                    cost_currency=variant.get("cost_currency"),
                    barcode=variant.get("barcode"),
                    stock_qty=int(variant.get("stock_qty") or 0),
                    **{field_name: payload.get(field_name) for field_name in (
                        "is_chemical",
                        "chemical_type",
                        "cas_number",
                        "ec_number",
                        "un_number",
                        "hazard_class",
                        "packing_group",
                        "adr_relevant",
                        "ghs_pictograms",
                        "signal_word",
                        "hazard_statements",
                        "precautionary_statements",
                        "wgk",
                        "storage_class",
                        "sds_available",
                        "sds_url",
                        "sds_asset_id",
                        "density",
                        "color",
                        "odor",
                        "ph_value",
                        "flash_point",
                        "boiling_point",
                        "viscosity",
                        "solubility",
                        "business_only",
                        "age_check_required",
                        "shippable",
                        "limited_quantity",
                        "hazard_shipping_note",
                        "shop_active",
                    ) if field_name in payload},
                )
                categories = get_or_create_categories(
                    session,
                    category_values(product, config),
                    separator=config.category_separator,
                    sales_channel_code=target_sales_channel_code,
                )
                set_product_categories_for_channel(
                    session,
                    db_product,
                    [category.id for category in categories],
                    sales_channel_code=target_sales_channel_code,
                )
                short_description = product_short_description(product)
                if short_description:
                    set_product_translation_short_description(
                        session,
                        db_product.id,
                        db_product.source_language,
                        db_product.title,
                        short_description,
                    )
                if _should_upsert_base_tier(variant, tier_payload, "sale"):
                    upsert_variant_price_tier(
                        session,
                        VariantPriceTierCreate(
                            variant_id=db_variant.id,
                            price_type="sale",
                            min_qty=1,
                            max_qty=None,
                            price=variant["price"],
                            currency=str(variant["currency"]),
                        ),
                    )
                if _should_upsert_base_tier(variant, tier_payload, "purchase"):
                    upsert_variant_price_tier(
                        session,
                        VariantPriceTierCreate(
                            variant_id=db_variant.id,
                            price_type="purchase",
                            min_qty=1,
                            max_qty=None,
                            price=variant["cost_price"],
                            currency=str(variant["cost_currency"]),
                        ),
                    )
                if tier_payload:
                    upsert_variant_price_tier(
                        session,
                        VariantPriceTierCreate(
                            variant_id=db_variant.id,
                            price_type=str(tier_payload["price_type"]),
                            min_qty=int(tier_payload["min_qty"]),
                            max_qty=tier_payload.get("max_qty"),
                            price=tier_payload["price"],
                            currency=str(tier_payload["currency"]),
                        ),
                    )

                sync_product_assets(
                    session=session,
                    product=product,
                    db_product=db_product,
                    db_variant=db_variant,
                    storage_root=settings.asset_storage_root,
                    imported_asset_keys=imported_product_assets,
                )
                row.status = "imported"
                row.message = f"product_id={db_product.id} variant_id={db_variant.id if db_variant else None}"
            if was_existing:
                updated += 1
            else:
                created += 1
        except Exception as exc:
            row.status = "error"
            row.message = str(exc)
            errors += 1

    job.status = "completed" if errors == 0 else "completed_with_errors"
    job.finished_at = datetime.now(timezone.utc)
    job.summary_json = {
        "rows": len(products),
        "created_or_inserted": created,
        "updated": updated,
        "errors": errors,
        "dry_run": dry_run,
        "sales_channel_code": target_sales_channel_code,
        "generated_output_dir": str(generated_output_dir) if generated_output_dir else None,
    }
    if errors:
        job.error_log = f"{errors} row(s) failed"
    session.flush()
    return job.summary_json or {}
