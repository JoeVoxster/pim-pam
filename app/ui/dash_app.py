from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
from pathlib import Path
import threading
import uuid
from datetime import datetime, timezone
from io import BytesIO

import dash
import dash_ag_grid as dag
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import abort, jsonify, redirect, request, send_file, send_from_directory
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Asset, ChannelCategory, ChemicalDocument, Product
from app.db.session import session_scope
from app.etl.pim_import import load_mapping_config, run_pim_import
from app.pdf.sdb_renderer import render_sdb_pdf
from app.schemas.pim import (
    ChannelCategoryUpsert,
    EnrichmentJobOptions,
    ProductCategoryMappingUpsert,
    ProductChannelListingUpdate,
    ProductCreate,
    ProductSDBUpdate,
    ProductTranslationCreate,
    ProductUpdate,
    SalesChannelCreate,
    SalesChannelUpdate,
    VariantChannelListingUpdate,
    VariantCreate,
    VariantPriceTierCreate,
    VariantTranslationCreate,
    VariantUpdate,
)
from app.services.asset_service import create_asset_record, upload_r2_asset_from_bytes, upload_selected_assets_to_r2
from app.services.chemical_enrichment_service import (
    apply_product_chemical_enrichment,
    apply_product_chemical_enrichment_suggestions,
    get_latest_product_chemical_enrichment,
    ingest_product_sdb_asset,
    ingest_product_sdb_pdf,
    list_product_chemical_enrichment_runs,
    run_product_chemical_enrichment,
)
from app.services.chemical_classification_service import (
    STORAGE_CLASS_LABELS,
    WGK_LABELS,
    apply_classification_proposals_to_product,
    build_chem_safety_payload,
    extract_wgk_storage_from_sdb,
    normalize_storage_class,
    normalize_wgk,
    storage_class_label,
    wgk_label,
)
from app.services.chemical_sdb_llm_service import get_sdb_llm_config_status, run_product_sdb_llm_normalization
from app.services.enrichment_service import run_selected_website_enrichment, run_website_enrichment
from app.services.product_translation_service import (
    DEFAULT_PROMPT_TEMPLATE,
    generate_product_translations,
    get_translation_config_status,
    list_languages,
    list_translation_prompts,
    reset_translation_prompt,
    save_translation_prompt,
)
from app.services.product_dedupe_service import (
    create_duplicate_group_preview,
    get_duplicate_group_detail,
    ignore_duplicate_group,
    list_duplicate_groups,
    merge_duplicate_group,
    scan_duplicate_groups,
    set_duplicate_group_master,
)
from app.services.product_data_enrichment_service import (
    DEFAULT_DOMAINS,
    SUPPORTED_FIELDS,
    apply_product_data_enrichment,
    preview_product_data_enrichment,
)
from app.services.product_text_enrichment_service import (
    TextEnrichmentOptions,
    apply_product_text_enrichment,
    preview_product_text_enrichment,
)
from app.services.product_asset_enrichment_service import enrich_missing_product_assets
from app.services.process_status_service import (
    ProcessAlreadyRunning,
    fail_process,
    finish_process,
    get_process_status,
    process_guard,
    update_process,
)
from scripts.import_descriptions_from_voxster_final_urls import (
    load_final_url_products,
    process_products as process_final_url_description_products,
    write_report as write_final_url_description_report,
)
from app.services.sdb_translation_service import (
    DEFAULT_SDB_SYSTEM_PROMPT,
    DEFAULT_SDB_USER_PROMPT_TEMPLATE,
    archive_chemical_document,
    delete_chemical_document,
    generate_sdb_translation_draft,
    get_sdb_translation_config_status,
    get_chemical_document_detail,
    list_sdb_documents_for_product,
    list_sdb_translation_prompts,
    mark_chemical_document_reviewed,
    render_chemical_document_pdf,
    save_sdb_translation_prompt,
    sync_product_sdb_working_document,
    update_chemical_document_text,
    update_chemical_document_status,
)
from app.services.sds_swiss_review_service import (
    apply_safe_auto_fixes,
    list_review_issues,
    mark_issue_status,
    release_document_as_final,
    review_sds_document,
    serialize_review_issue,
)
from app.services.suva_limit_service import (
    enrich_sdb_sections_with_suva_suggestions,
    generate_section_8_1_ch_block,
    import_suva_xlsx,
    latest_product_suva_check,
    list_suva_sources,
    run_product_suva_check,
    serialize_suva_check,
)
from app.services.pim_service import (
    DEFAULT_CATEGORY_CHANNEL_CODE,
    archive_product,
    archive_variants,
    bulk_update_products,
    bulk_update_variants,
    bulk_upsert_product_category_mappings,
    bulk_upsert_product_channel_listings,
    bulk_upsert_variant_category_mappings,
    bulk_upsert_variant_channel_listings,
    create_category,
    create_or_update_sales_channel,
    create_or_update_translation,
    create_or_update_variant_translation,
    delete_assets,
    ensure_default_sales_channels,
    export_channel_rows,
    get_channel_category_tree,
    list_channel_categories,
    list_channel_export_rows,
    create_product,
    dashboard_counts,
    delete_asset,
    delete_or_archive_variants,
    delete_category,
    get_category_detail,
    get_products_for_category,
    get_product_category_assignment_for_channel,
    get_product_detail,
    list_chemical_products,
    list_assets,
    list_attribute_overview,
    list_brands,
    list_categories,
    list_product_category_assignments,
    list_family_overview,
    list_import_jobs,
    list_products,
    list_rule_overview,
    list_sales_channels,
    list_translation_overview,
    list_variant_translation_overview,
    get_products_for_channel_category,
    upsert_channel_category,
    upsert_product_category_mapping,
    upsert_product_channel_listing,
    upsert_variant_channel_listing,
    list_variants,
    move_asset,
    get_product_sdb,
    upsert_variant_price_tier,
    delete_variant_price_tier,
    upsert_product_sdb,
    set_product_translation_short_description,
    update_category,
    update_product,
    update_variant_translation_by_id,
    update_variant_price_tier,
    update_variant,
    variant_ids_for_products,
)
from app.services.sdb_support import SDB_SECTION_TITLES, merge_sdb_sections, prepare_sdb_sections_for_render, sync_sdb_fields_from_content, validate_sdb_sections
from app.services.r2_storage_service import object_key_from_storage_path, safe_r2_public_url
from app.services.r2_config_service import (
    DEFAULT_R2_BUCKET,
    DEFAULT_R2_ENDPOINT,
    DEFAULT_R2_PATH_PREFIX,
    DEFAULT_R2_PROVIDER,
    DEFAULT_R2_REGION,
    build_r2_storage,
    get_or_create_r2_config,
    get_r2_upload_options,
    save_r2_config,
    serialize_r2_config,
    test_r2_connection,
)
from app.services.medusa import (
    MedusaSyncService,
    get_or_create_medusa_connection,
    list_medusa_run_items,
    list_medusa_runs,
    save_medusa_connection,
    serialize_medusa_connection,
)
from app.utils.pim_config import get_pim_settings


PIM_IMPORT_RUNS: dict[str, dict[str, object]] = {}
PIM_IMPORT_RUNS_LOCK = threading.Lock()

NUMERIC_COLUMN_STYLE = {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}
TEXT_ELLIPSIS_STYLE = {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"}


def _set_pim_import_run(job_id: str, **changes: object) -> None:
    with PIM_IMPORT_RUNS_LOCK:
        payload = PIM_IMPORT_RUNS.setdefault(job_id, {"status": "queued"})
        payload.update(changes)


def _get_pim_import_run(job_id: str | None) -> dict[str, object] | None:
    if not job_id:
        return None
    with PIM_IMPORT_RUNS_LOCK:
        payload = PIM_IMPORT_RUNS.get(job_id)
        return dict(payload) if payload else None


def _run_pim_import_background(
    job_id: str,
    clean_file: str,
    source_name: str,
    mapping_path: str | None,
    dry_run: bool,
    sales_channel_code: str,
) -> None:
    _set_pim_import_run(
        job_id,
        status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        source_name=source_name,
        clean_file=clean_file,
        sales_channel_code=sales_channel_code,
    )
    try:
        with session_scope(get_pim_settings().database_url) as session:
            summary = run_pim_import(
                session=session,
                source_name=source_name,
                mapping_config=load_mapping_config(mapping_path),
                dry_run=dry_run,
                clean_file=clean_file,
                sales_channel_code=sales_channel_code,
            )
        _set_pim_import_run(job_id, status="completed", finished_at=datetime.now(timezone.utc).isoformat(), summary=summary)
    except Exception as exc:
        _set_pim_import_run(job_id, status="failed", finished_at=datetime.now(timezone.utc).isoformat(), error=str(exc))


PRODUCT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "variant_nav", "headerName": "V", "maxWidth": 55, "editable": False, "filter": False, "sortable": False},
    {"field": "sku"},
    {"field": "title", "flex": 1.8, "minWidth": 280, "editable": True, "cellRenderer": "ProductTitleButton"},
    {"field": "photo_asset_id", "headerName": "Photo", "maxWidth": 90, "editable": False, "filter": False, "sortable": False, "cellRenderer": "ProductPhotoCell"},
    {
        "field": "source_language",
        "headerName": "Sprache",
        "maxWidth": 120,
        "editable": True,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["en", "de-CH", "it", "fr", "de"]},
    },
    {"field": "family_key", "headerName": "Family Key"},
    {"field": "colors", "headerName": "Variantenwerte", "flex": 1.4, "minWidth": 220},
    {"field": "brand", "editable": True},
    {"field": "status", "editable": True},
    {"field": "price_from", "headerName": "Ab-Preis"},
    {"field": "price", "headerName": "Normalpreis"},
    {"field": "cost_price", "headerName": "EK"},
    {"field": "margin_percent"},
    {"field": "currency", "maxWidth": 110},
    {"field": "updated_at", "flex": 1},
]
VARIANT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "product_title", "flex": 1.6, "minWidth": 280},
    {"field": "sku"},
    {"field": "variant_title", "flex": 1.8, "minWidth": 320, "editable": True},
    {"field": "option_name", "headerName": "Attribut", "editable": True},
    {"field": "option_value", "headerName": "Wert", "editable": True},
    {"field": "packaging", "editable": True},
    {"field": "price", "editable": True},
    {"field": "cost_price", "editable": True},
    {"field": "margin_percent"},
    {"field": "currency", "maxWidth": 110, "editable": True},
    {"field": "stock_qty", "editable": True},
    {"field": "barcode", "editable": True},
    {
        "field": "status",
        "editable": True,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["active", "inactive", "archived"]},
    },
]
DEDUPLICATE_GROUP_COLUMNS = [
    {"field": "id", "headerName": "Gruppe-ID", "maxWidth": 110},
    {"field": "master_product", "headerName": "Master-Produkt", "flex": 1.8, "minWidth": 320},
    {"field": "duplicate_count", "headerName": "Dubletten", "maxWidth": 120},
    {"field": "confidence_display", "headerName": "Confidence", "maxWidth": 130},
    {"field": "confidence_level", "headerName": "Stufe", "maxWidth": 110},
    {"field": "status", "maxWidth": 120},
    {"field": "conflict_summary", "headerName": "Hauptkonflikte", "flex": 1.3, "minWidth": 220},
    {"field": "source", "headerName": "Quelle", "maxWidth": 120},
    {"field": "created_at", "headerName": "Erstellt am", "minWidth": 150},
    {"field": "updated_at", "headerName": "Aktualisiert am", "minWidth": 150},
]
DEDUPLICATE_ITEM_COLUMNS = [
    {"field": "product_id", "headerName": "Produkt-ID", "maxWidth": 120, "checkboxSelection": True, "headerCheckboxSelection": True},
    {"field": "role", "headerName": "Rolle", "maxWidth": 110},
    {"field": "title", "headerName": "Titel", "flex": 1.5, "minWidth": 260},
    {"field": "sku", "headerName": "SKU", "minWidth": 150},
    {"field": "status", "maxWidth": 110},
    {"field": "brand", "headerName": "Marke", "minWidth": 140},
    {"field": "family_key", "headerName": "Family Key", "minWidth": 140},
    {"field": "variant_count", "headerName": "Varianten", "maxWidth": 115},
    {"field": "asset_count", "headerName": "Assets", "maxWidth": 100},
    {"field": "sale_price_count", "headerName": "VK", "maxWidth": 80},
    {"field": "cost_price_count", "headerName": "EK", "maxWidth": 80},
    {"field": "match_reasons", "headerName": "Match-Gründe", "flex": 1, "minWidth": 180},
    {"field": "conflicts", "headerName": "Konflikte", "maxWidth": 110},
]
DEDUPLICATE_CONFLICT_COLUMNS = [
    {"field": "field", "headerName": "Feld", "minWidth": 140},
    {"field": "product_id", "headerName": "Produkt", "maxWidth": 110},
    {"field": "variant_id", "headerName": "Variante", "maxWidth": 110},
    {"field": "master", "headerName": "Master-Wert", "flex": 1, "minWidth": 220},
    {"field": "duplicate", "headerName": "Dubletten-Wert", "flex": 1, "minWidth": 220},
    {"field": "suggestion", "headerName": "Vorschlag", "flex": 1.2, "minWidth": 260},
    {"field": "strategy", "headerName": "Strategie", "minWidth": 220},
]
PRODUCT_ENRICHMENT_SUGGESTION_COLUMNS = [
    {"field": "product_id", "headerName": "Produkt-ID", "maxWidth": 110},
    {"field": "sku", "headerName": "SKU", "minWidth": 140},
    {"field": "title", "headerName": "Produkt", "flex": 1, "minWidth": 220},
    {"field": "field_name", "headerName": "Feld", "minWidth": 150},
    {"field": "current_value", "headerName": "Aktueller Wert", "flex": 1, "minWidth": 220},
    {"field": "original_value", "headerName": "Original", "flex": 1.2, "minWidth": 280},
    {"field": "suggested_value", "headerName": "Vorschlag", "flex": 1.5, "minWidth": 320},
    {"field": "source_language", "headerName": "Quelle Sprache", "minWidth": 130},
    {"field": "target_locale", "headerName": "Ziel", "maxWidth": 110},
    {"field": "section_name", "headerName": "Sektion", "minWidth": 140},
    {"field": "source_domain", "headerName": "Quelle", "minWidth": 160},
    {"field": "source_url", "headerName": "URL", "flex": 1, "minWidth": 240},
    {"field": "search_method", "headerName": "Suchmethode", "minWidth": 150},
    {"field": "status", "headerName": "Status", "maxWidth": 120},
    {"field": "warning", "headerName": "Warnung", "flex": 1, "minWidth": 260},
    {"field": "confidence", "headerName": "Confidence", "maxWidth": 120},
    {"field": "searched_at", "headerName": "Gesucht am", "minWidth": 190},
]
FINAL_URL_DESCRIPTION_IMPORT_COLUMNS = [
    {"field": "product_id", "headerName": "Produkt-ID", "maxWidth": 110},
    {"field": "product_name", "headerName": "Produkt", "flex": 1, "minWidth": 220},
    {"field": "domain", "headerName": "Domain", "minWidth": 170},
    {"field": "final_url", "headerName": "Final URL", "flex": 1.2, "minWidth": 280},
    {"field": "status", "headerName": "Status", "minWidth": 150},
    {"field": "found", "headerName": "Beschreibung gefunden", "maxWidth": 170},
    {"field": "old_short_description", "headerName": "Kurz alt", "flex": 1, "minWidth": 220},
    {"field": "new_short_description", "headerName": "Kurz neu", "flex": 1, "minWidth": 220},
    {"field": "old_description", "headerName": "Beschreibung alt", "flex": 1.2, "minWidth": 280},
    {"field": "new_description", "headerName": "Beschreibung neu", "flex": 1.4, "minWidth": 320},
    {"field": "error", "headerName": "Fehler / Hinweis", "flex": 1, "minWidth": 260},
]
PRODUCT_TEXT_ENRICHMENT_COLUMNS = [
    {"field": "product_id", "headerName": "Produkt-ID", "maxWidth": 110},
    {"field": "sku", "headerName": "SKU", "minWidth": 130},
    {"field": "product_name", "headerName": "Produkt", "flex": 1, "minWidth": 220},
    {"field": "locale", "headerName": "Sprache", "maxWidth": 110},
    {"field": "field_name", "headerName": "Feld", "minWidth": 160},
    {"field": "current_value", "headerName": "Aktueller Wert", "flex": 1.2, "minWidth": 260},
    {"field": "suggested_value", "headerName": "Neuer Vorschlag", "flex": 1.5, "minWidth": 320},
    {"field": "source", "headerName": "Quelle", "minWidth": 180},
    {"field": "status", "headerName": "Status", "minWidth": 140},
    {"field": "action", "headerName": "Aktion", "minWidth": 130},
    {"field": "message", "headerName": "Hinweis", "flex": 1, "minWidth": 260},
]
BULK_EDIT_PREVIEW_COLUMNS = [
    {"field": "entity_type", "headerName": "Typ", "maxWidth": 110},
    {"field": "id", "headerName": "ID", "maxWidth": 90},
    {"field": "sku", "headerName": "SKU", "minWidth": 140},
    {"field": "title", "headerName": "Titel", "flex": 1.4, "minWidth": 240},
    {"field": "field", "headerName": "Feld", "minWidth": 140},
    {"field": "old_value", "headerName": "Alter Wert", "flex": 1, "minWidth": 180},
    {"field": "new_value", "headerName": "Neuer Wert", "flex": 1, "minWidth": 180},
    {"field": "status", "headerName": "Status", "minWidth": 130},
    {"field": "message", "headerName": "Hinweis", "flex": 1, "minWidth": 220},
]
CATEGORY_COLUMNS = [
    {"field": "id", "maxWidth": 90, "sortable": False},
    {"field": "tree_toggle", "headerName": "", "maxWidth": 70, "editable": False, "sortable": False, "filter": False, "cellRenderer": "CategoryToggleButton"},
    {"field": "tree_name", "headerName": "Kategorienbaum", "flex": 1.8, "minWidth": 360, "editable": False, "sortable": False, "filter": True},
    {"field": "sort_order", "headerName": "#", "maxWidth": 90, "editable": True, "sortable": False},
    {"field": "parent_id", "headerName": "Parent", "maxWidth": 120, "editable": True, "sortable": False},
    {
        "field": "language_code",
        "headerName": "Sprache",
        "maxWidth": 120,
        "editable": True,
        "sortable": False,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["en", "de-CH", "it", "fr", "de"]},
    },
    {"field": "name", "headerName": "Name", "flex": 1.1, "minWidth": 220, "editable": True, "sortable": False},
    {"field": "slug", "flex": 1, "editable": True, "sortable": False},
]
CHEMISTRY_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "sku", "minWidth": 150},
    {"field": "title", "flex": 1.5, "minWidth": 260},
    {"field": "brand", "minWidth": 160},
    {"field": "cas_number", "headerName": "CAS", "minWidth": 120},
    {"field": "un_number", "headerName": "UN", "minWidth": 100, "maxWidth": 110},
    {"field": "adr_label", "headerName": "ADR", "maxWidth": 90},
    {"field": "sds_label", "headerName": "SDB", "maxWidth": 90},
    {"field": "business_only_label", "headerName": "Gewerbe", "maxWidth": 110},
    {"field": "status", "maxWidth": 120},
]
CHEMICAL_DOCUMENT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "locale", "headerName": "Sprache", "maxWidth": 120},
    {"field": "region_code", "headerName": "Region", "maxWidth": 100},
    {"field": "title", "headerName": "Titel", "flex": 1.4, "minWidth": 260, "cellRenderer": "SdbTitleLinkCell"},
    {"field": "text_status", "headerName": "Text", "maxWidth": 130},
    {"field": "pdf_status", "headerName": "PDF", "maxWidth": 135},
    {"field": "action_hint", "headerName": "Nächster Schritt", "minWidth": 220},
    {"field": "source", "headerName": "Quelle", "maxWidth": 150},
    {"field": "status", "headerName": "Status", "minWidth": 150},
    {"field": "swiss_review_status", "headerName": "CH-Review", "minWidth": 150},
    {"field": "compliance_score", "headerName": "Score", "maxWidth": 100},
    {"field": "generated_at_display", "headerName": "Generiert am", "minWidth": 160},
    {"field": "is_current", "headerName": "Aktuell", "maxWidth": 105},
    {"field": "version", "headerName": "Version", "maxWidth": 130},
    {"field": "valid_from", "headerName": "Gültig ab", "maxWidth": 130},
    {"field": "created_by_ai", "headerName": "KI", "maxWidth": 90},
    {"field": "filename", "headerName": "Datei", "minWidth": 190},
]
SDS_REVIEW_ISSUE_COLUMNS = [
    {"field": "section", "headerName": "Abschnitt", "maxWidth": 120},
    {"field": "severity", "headerName": "Schweregrad", "maxWidth": 140},
    {"field": "issue_key", "headerName": "Problem", "minWidth": 220},
    {"field": "current_text", "headerName": "Aktueller Text", "flex": 1.2, "minWidth": 260},
    {"field": "suggested_text", "headerName": "Vorschlag", "flex": 1.2, "minWidth": 260},
    {"field": "reason", "headerName": "Grund", "flex": 1.1, "minWidth": 260},
    {"field": "auto_fixable", "headerName": "Auto-Fix", "maxWidth": 110},
    {"field": "requires_human_review", "headerName": "Prüfung", "maxWidth": 110},
    {"field": "status", "headerName": "Status", "maxWidth": 120},
]
SUVA_SOURCE_COLUMNS = [
    {"field": "id", "headerName": "Import-ID", "maxWidth": 110},
    {"field": "source_name", "headerName": "Quelle", "flex": 1, "minWidth": 220},
    {"field": "file_name", "headerName": "Datei", "flex": 1, "minWidth": 240},
    {"field": "imported_at", "headerName": "Importiert", "minWidth": 170},
    {"field": "imported_by", "headerName": "Benutzer", "minWidth": 140},
    {"field": "language", "headerName": "Sprache", "maxWidth": 110},
    {"field": "sha256", "headerName": "SHA256", "flex": 1.2, "minWidth": 280},
    {"field": "source_url", "headerName": "URL", "flex": 1, "minWidth": 220},
]
SUVA_CHECK_ITEM_COLUMNS = [
    {"field": "ingredient_name", "headerName": "Stoff", "flex": 1.2, "minWidth": 240},
    {"field": "cas_number", "headerName": "CAS", "minWidth": 130},
    {"field": "concentration", "headerName": "Konzentration", "minWidth": 130},
    {"field": "h_statements", "headerName": "H-Sätze", "minWidth": 140},
    {"field": "match_status", "headerName": "Match", "minWidth": 160},
    {"field": "mak_ppm", "headerName": "MAK ppm", "maxWidth": 110},
    {"field": "mak_mg_m3", "headerName": "MAK mg/m3", "maxWidth": 130},
    {"field": "kzgw_ppm", "headerName": "KZGW ppm", "maxWidth": 120},
    {"field": "kzgw_mg_m3", "headerName": "KZGW mg/m3", "maxWidth": 140},
    {"field": "bat_value", "headerName": "BAT", "minWidth": 130},
    {"field": "bat_matrix", "headerName": "BAT Matrix", "minWidth": 130},
    {"field": "notations", "headerName": "Notationen", "minWidth": 140},
    {"field": "severity", "headerName": "Status", "maxWidth": 120},
    {"field": "review_note", "headerName": "Hinweis", "flex": 1.4, "minWidth": 300},
]
CHEMISTRY_ENRICHMENT_SUGGESTION_COLUMNS = [
    {"field": "field", "headerName": "Feld", "minWidth": 230},
    {"field": "current_value", "headerName": "Aktuell", "minWidth": 180},
    {"field": "suggested_value", "headerName": "Vorschlag", "minWidth": 180},
    {"field": "source_section", "headerName": "Abschnitt", "maxWidth": 120},
    {"field": "confidence", "headerName": "Confidence", "maxWidth": 130},
    {"field": "status", "headerName": "Status", "maxWidth": 130},
    {"field": "evidence", "headerName": "Nachweis", "flex": 1.4, "minWidth": 320},
]
SDB_TRANSLATION_PROMPT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "name", "headerName": "Name", "flex": 1, "minWidth": 220},
    {"field": "source_locale", "headerName": "Quelle", "maxWidth": 120},
    {"field": "target_locale", "headerName": "Ziel", "maxWidth": 120},
    {"field": "target_region", "headerName": "Region", "maxWidth": 110},
    {"field": "active", "headerName": "Aktiv", "maxWidth": 100},
]
ASSET_COLUMNS = [
    {"field": "asset_preview", "headerName": "Vorschau", "maxWidth": 120, "editable": False, "filter": False, "sortable": False, "cellRenderer": "AssetPreviewCell"},
    {"field": "id", "maxWidth": 90},
    {"field": "title", "headerName": "Titel", "minWidth": 160},
    {"field": "original_filename", "headerName": "Original-Datei", "minWidth": 180},
    {"field": "asset_type", "headerName": "Asset-Typ", "minWidth": 150},
    {"field": "product_id", "headerName": "Product_ID", "maxWidth": 110, "cellRenderer": "ProductIdLinkCell"},
    {"field": "product_sku", "headerName": "Artikel", "maxWidth": 140},
    {"field": "product_title", "headerName": "Produkt", "flex": 1},
    {"field": "language_code", "headerName": "Sprache", "maxWidth": 110},
    {"field": "variant_id", "headerName": "Variant_ID", "maxWidth": 110, "cellRenderer": "VariantIdLinkCell"},
    {"field": "variant_sku", "headerName": "Variante", "maxWidth": 140},
    {"field": "filename", "flex": 1},
    {"field": "mime_type"},
    {"field": "file_size"},
    {"field": "storage_provider", "headerName": "Storage", "minWidth": 150},
    {"field": "bucket", "headerName": "Bucket", "minWidth": 150},
    {"field": "object_key", "headerName": "Object Key", "flex": 1},
    {"field": "status", "headerName": "Status", "maxWidth": 120},
    {"field": "uploaded_at", "headerName": "Hochgeladen", "minWidth": 160},
    {"field": "sdb_document_type", "headerName": "Dok.typ", "maxWidth": 110},
    {"field": "sdb_language_code", "headerName": "SDB Sprache", "maxWidth": 130},
    {"field": "sdb_generated_at_display", "headerName": "Generiert am", "minWidth": 155},
    {"field": "sdb_status", "headerName": "SDB Status", "minWidth": 130},
    {"field": "sdb_source", "headerName": "SDB Quelle", "minWidth": 130},
    {"field": "source_url", "flex": 1},
    {"field": "storage_path", "flex": 1},
]
ASSET_TYPE_OPTIONS = [
    {"label": "Produktbild", "value": "product_image"},
    {"label": "Produktgalerie", "value": "product_gallery"},
    {"label": "Sicherheitsdatenblatt / SDB", "value": "safety_data_sheet"},
    {"label": "Technisches Datenblatt", "value": "technical_data_sheet"},
    {"label": "Manual / Anleitung", "value": "manual"},
    {"label": "Rechnung PDF", "value": "invoice_pdf"},
    {"label": "Importdatei", "value": "import_file"},
    {"label": "Sonstige Datei", "value": "other"},
]
IMPORT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "source_name", "flex": 1},
    {"field": "job_type"},
    {"field": "status"},
    {"field": "sales_channel_code", "headerName": "Kanal", "maxWidth": 130},
    {"field": "started_at"},
    {"field": "finished_at"},
]
ATTRIBUTE_COLUMNS = [
    {"field": "attribute_name", "headerName": "Attribut", "minWidth": 180},
    {"field": "variant_count", "headerName": "Varianten", "maxWidth": 120},
    {"field": "value_count", "headerName": "Werte", "maxWidth": 120},
    {"field": "example_values", "headerName": "Beispielwerte", "flex": 1, "minWidth": 280},
]
FAMILY_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "family_key", "headerName": "Family Key", "minWidth": 160},
    {"field": "sku", "minWidth": 140},
    {"field": "title", "flex": 1.4, "minWidth": 260},
    {"field": "brand"},
    {"field": "variant_count", "headerName": "Varianten", "maxWidth": 120},
    {"field": "attributes", "headerName": "Attribute", "minWidth": 160},
    {"field": "values", "headerName": "Werte", "flex": 1, "minWidth": 220},
    {"field": "updated_at", "minWidth": 180},
]
TRANSLATION_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "product_sku", "headerName": "Artikel", "minWidth": 140},
    {"field": "product_title", "headerName": "Produkt", "flex": 1, "minWidth": 240},
    {"field": "language_code", "headerName": "Sprache", "maxWidth": 110},
    {"field": "title", "headerName": "Titel", "flex": 1, "minWidth": 220},
    {"field": "short_description", "headerName": "Kurzbeschreibung", "flex": 1, "minWidth": 220},
    {"field": "description", "headerName": "Beschreibung", "flex": 1.2, "minWidth": 280},
    {"field": "translation_status", "headerName": "Status", "minWidth": 130},
    {"field": "provider", "headerName": "Provider", "minWidth": 110},
    {"field": "model", "headerName": "Modell", "minWidth": 130},
    {"field": "slug", "headerName": "Slug", "minWidth": 180},
]
LANGUAGE_COLUMNS = [
    {"field": "code", "headerName": "Code", "maxWidth": 110},
    {"field": "name", "headerName": "Sprache", "minWidth": 160},
    {"field": "enabled", "headerName": "Aktiv", "maxWidth": 100},
    {"field": "isDefault", "headerName": "Default", "maxWidth": 110},
]
VARIANT_TRANSLATION_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "product_sku", "headerName": "Artikel", "minWidth": 140},
    {"field": "variant_sku", "headerName": "Variante", "minWidth": 160},
    {"field": "language_code", "headerName": "Sprache", "maxWidth": 110},
    {"field": "title", "headerName": "Titel", "flex": 1, "minWidth": 220},
    {"field": "option_label_override", "headerName": "Optionslabel", "flex": 1, "minWidth": 180},
    {"field": "package_label", "headerName": "Gebindelabel", "flex": 1, "minWidth": 180},
]
SALES_CHANNEL_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "code", "headerName": "Code", "minWidth": 140},
    {"field": "name", "headerName": "Name", "minWidth": 180, "editable": True},
    {"field": "is_active", "headerName": "Aktiv", "maxWidth": 100, "editable": True},
    {"field": "sort_order", "headerName": "#", "maxWidth": 90, "editable": True},
]
CHANNEL_CATEGORY_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "sales_channel_code", "headerName": "Kanal", "minWidth": 130},
    {"field": "external_category_id", "headerName": "Externe ID", "minWidth": 160},
    {"field": "name", "headerName": "Name", "minWidth": 180},
    {"field": "external_path", "headerName": "Pfad", "flex": 1, "minWidth": 260},
    {"field": "is_active", "headerName": "Aktiv", "maxWidth": 100},
]
CHANNEL_CATEGORY_TREE_COLUMNS = [
    {"field": "id", "maxWidth": 90, "sortable": False},
    {"field": "tree_toggle", "headerName": "", "maxWidth": 70, "editable": False, "sortable": False, "filter": False, "cellRenderer": "CategoryToggleButton"},
    {"field": "tree_name", "headerName": "Kategorienbaum", "flex": 1.5, "minWidth": 280, "sortable": False},
    {"field": "external_category_id", "headerName": "Externe ID", "minWidth": 140},
    {"field": "product_count", "headerName": "Produkte", "maxWidth": 110},
    {"field": "is_active", "headerName": "Aktiv", "maxWidth": 90},
]
CHANNEL_CATEGORY_PRODUCT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "sku", "headerName": "SKU", "minWidth": 150},
    {"field": "title", "headerName": "Produkt", "flex": 1.5, "minWidth": 260},
    {"field": "brand", "headerName": "Marke", "minWidth": 140},
    {"field": "variant_count", "headerName": "Varianten", "maxWidth": 120},
    {"field": "status", "headerName": "Status", "maxWidth": 120},
    {"field": "sales_channel_name", "headerName": "Kanal", "minWidth": 140},
]
PRODUCT_CHANNEL_LISTING_COLUMNS = [
    {"field": "sales_channel_name", "headerName": "Kanal", "minWidth": 160},
    {"field": "allowed", "headerName": "Erlaubt", "maxWidth": 100, "editable": True},
    {"field": "is_active", "headerName": "Aktiv", "maxWidth": 100, "editable": True},
    {
        "field": "publication_status",
        "headerName": "Status",
        "minWidth": 130,
        "editable": True,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["imported", "draft", "ready", "published", "inactive", "archived"]},
    },
    {"field": "active_from", "headerName": "Aktiv ab", "minWidth": 180, "editable": True},
    {"field": "active_until", "headerName": "Aktiv bis", "minWidth": 180, "editable": True},
    {"field": "channel_external_category_id", "headerName": "Externe Kategorie", "minWidth": 180},
    {"field": "channel_category_name", "headerName": "Kategoriepfad", "flex": 1, "minWidth": 220},
]
PRODUCT_CATEGORY_MAPPING_COLUMNS = [
    {"field": "sales_channel_name", "headerName": "Kanal", "minWidth": 160},
    {"field": "external_category_id", "headerName": "Externe ID", "minWidth": 160},
    {"field": "channel_category_name", "headerName": "Kategorie", "minWidth": 180},
    {"field": "external_path", "headerName": "Pfad", "flex": 1, "minWidth": 260},
    {"field": "is_primary", "headerName": "Primär", "maxWidth": 100},
]
VARIANT_CATEGORY_MAPPING_COLUMNS = [
    {"field": "variant_sku", "headerName": "Variante", "minWidth": 180},
    {"field": "sales_channel_name", "headerName": "Kanal", "minWidth": 140},
    {"field": "external_category_id", "headerName": "Externe ID", "minWidth": 150},
    {"field": "channel_category_name", "headerName": "Kategorie", "minWidth": 180},
    {"field": "external_path", "headerName": "Pfad", "flex": 1, "minWidth": 240},
    {"field": "is_primary", "headerName": "Primär", "maxWidth": 100},
]
VARIANT_CHANNEL_LISTING_COLUMNS = [
    {"field": "variant_sku", "headerName": "Variante", "minWidth": 180},
    {"field": "sales_channel_name", "headerName": "Kanal", "minWidth": 140},
    {"field": "allowed", "headerName": "Erlaubt", "maxWidth": 100, "editable": True},
    {"field": "is_active", "headerName": "Aktiv", "maxWidth": 100, "editable": True},
    {
        "field": "publication_status",
        "headerName": "Status",
        "minWidth": 130,
        "editable": True,
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["imported", "draft", "ready", "published", "inactive", "archived"]},
    },
    {"field": "price_enabled", "headerName": "Preis", "maxWidth": 90, "editable": True},
    {"field": "shippable", "headerName": "Versand", "maxWidth": 100, "editable": True},
    {"field": "hazardous_goods", "headerName": "Gefahrgut", "maxWidth": 110, "editable": True},
    {"field": "limited_quantity", "headerName": "LQ", "minWidth": 120, "editable": True},
    {"field": "channel_sku", "headerName": "Kanal-SKU", "minWidth": 180, "editable": True},
    {"field": "channel_ean", "headerName": "Kanal-EAN", "minWidth": 180, "editable": True},
]
RULE_COLUMNS = [
    {"field": "rule_type", "headerName": "Typ", "minWidth": 140},
    {"field": "name", "headerName": "Regel", "minWidth": 180},
    {"field": "scope", "headerName": "Bereich", "minWidth": 160},
    {"field": "details", "headerName": "Details", "flex": 1, "minWidth": 320},
]
DETAIL_VARIANT_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "sku"},
    {"field": "variant_title", "flex": 1.8, "minWidth": 320},
    {"field": "option_name", "headerName": "Attribut"},
    {"field": "option_value", "headerName": "Wert"},
    {"field": "packaging"},
    {"field": "price"},
    {"field": "cost_price"},
    {"field": "margin_percent"},
    {"field": "currency", "maxWidth": 110},
    {"field": "stock_qty"},
    {"field": "status", "headerName": "Status", "maxWidth": 120},
]
DETAIL_TIER_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {
        "field": "price_type",
        "headerName": "Typ",
        "editable": True,
        "minWidth": 120,
        "maxWidth": 140,
        "cellStyle": {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"},
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["sale", "purchase"]},
    },
    {"field": "min_qty", "headerName": "Min.", "editable": True, "minWidth": 96, "maxWidth": 110, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {"field": "max_qty", "headerName": "Max.", "editable": True, "minWidth": 96, "maxWidth": 110, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {"field": "price", "headerName": "Preis", "editable": True, "minWidth": 120, "maxWidth": 140, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {"field": "margin_amount", "headerName": "Marge", "minWidth": 126, "maxWidth": 150, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {"field": "total_margin_amount", "headerName": "Gesamtmarge", "minWidth": 150, "maxWidth": 176, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {"field": "margin_percent", "headerName": "Marge %", "minWidth": 120, "maxWidth": 136, "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"}},
    {
        "field": "currency",
        "headerName": "Währung",
        "editable": True,
        "minWidth": 96,
        "maxWidth": 110,
        "cellStyle": {"textAlign": "right", "whiteSpace": "nowrap", "fontVariantNumeric": "tabular-nums"},
        "cellEditor": "agSelectCellEditor",
        "cellEditorParams": {"values": ["EUR", "CHF", "USD"]},
    },
    {
        "field": "delete_action",
        "headerName": "Löschen",
        "maxWidth": 108,
        "editable": False,
        "sortable": False,
        "filter": False,
        "cellStyle": {"whiteSpace": "nowrap", "overflow": "hidden", "textOverflow": "ellipsis"},
    },
]
DETAIL_ASSET_COLUMNS = [
    {"field": "asset_preview", "headerName": "Vorschau", "maxWidth": 120, "editable": False, "filter": False, "sortable": False, "cellRenderer": "AssetPreviewCell"},
    {"field": "id", "maxWidth": 90},
    {"field": "sort_order", "headerName": "#", "maxWidth": 80},
    {"field": "product_sku", "headerName": "Artikel", "maxWidth": 140},
    {"field": "variant_sku", "headerName": "Variante", "maxWidth": 140},
    {"field": "filename", "flex": 1},
    {"field": "mime_type"},
    {"field": "sdb_document_type", "headerName": "Dok.typ", "maxWidth": 110},
    {"field": "sdb_language_code", "headerName": "SDB Sprache", "maxWidth": 130},
    {"field": "sdb_generated_at_display", "headerName": "Generiert am", "minWidth": 155},
    {"field": "sdb_status", "headerName": "SDB Status", "minWidth": 130},
    {"field": "sdb_source", "headerName": "SDB Quelle", "minWidth": 130},
    {"field": "source_url", "flex": 1},
    {"field": "storage_path", "flex": 1},
]
DETAIL_TRANSLATION_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "language_code"},
    {"field": "title", "flex": 1},
    {"field": "short_description", "headerName": "Kurzbeschreibung", "flex": 1},
    {"field": "description", "flex": 1},
    {"field": "seo_title", "headerName": "SEO-Titel", "flex": 1},
    {"field": "slug", "headerName": "Slug", "minWidth": 180},
]
DETAIL_VARIANT_TRANSLATION_COLUMNS = [
    {"field": "id", "maxWidth": 90},
    {"field": "variant_sku", "headerName": "Variante", "minWidth": 160},
    {"field": "language_code", "headerName": "Sprache", "maxWidth": 110},
    {"field": "title", "headerName": "Titel", "flex": 1, "minWidth": 220},
    {"field": "option_label_override", "headerName": "Optionslabel", "flex": 1, "minWidth": 180},
    {"field": "package_label", "headerName": "Gebindelabel", "flex": 1, "minWidth": 180},
]
ALLOWED_IMPORT_SUFFIXES = {".csv", ".xlsx", ".xls"}
HIDDEN_TAB_STYLE = {"display": "none"}
LANGUAGE_CODE_OPTIONS = [
    {"label": "de-CH", "value": "de-CH"},
    {"label": "de", "value": "de"},
    {"label": "en", "value": "en"},
    {"label": "fr", "value": "fr"},
    {"label": "it", "value": "it"},
    {"label": "es", "value": "es"},
]
BOOLEAN_OPTIONS = [
    {"label": "Ja", "value": True},
    {"label": "Nein", "value": False},
]
PUBLICATION_STATUS_OPTIONS = [
    {"label": "imported", "value": "imported"},
    {"label": "draft", "value": "draft"},
    {"label": "ready", "value": "ready"},
    {"label": "published", "value": "published"},
    {"label": "inactive", "value": "inactive"},
    {"label": "archived", "value": "archived"},
]
PRODUCT_BULK_STATUS_OPTIONS = [{"label": value, "value": value} for value in ["active", "imported", "draft", "ready", "published", "inactive", "archived"]]
SIGNAL_WORD_OPTIONS = [
    {"label": "Kein Signalwort", "value": "none"},
    {"label": "ACHTUNG / warning", "value": "warning"},
    {"label": "GEFAHR / danger", "value": "danger"},
]
GHS_SYMBOLS = {
    "GHS05": {"label_de": "Ätzend", "label_en": "Corrosive", "src": "/chem/ghs/GHS05.svg"},
    "GHS07": {"label_de": "Ausrufezeichen", "label_en": "Exclamation mark", "src": "/chem/ghs/GHS07.svg"},
    "GHS09": {"label_de": "Umweltgefährlich", "label_en": "Hazardous to the aquatic environment", "src": "/chem/ghs/GHS09.svg"},
}
ADR_SYMBOLS = {
    "ADR_3": {"label_de": "Entzündbare flüssige Stoffe", "label_en": "Flammable liquids", "src": "/chem/adr/ADR_3.png"},
    "ADR_5.1": {"label_de": "Entzündend wirkende Stoffe", "label_en": "Oxidizing substances", "src": "/chem/adr/ADR_5.1.png"},
    "ADR_8": {"label_de": "Ätzende Stoffe", "label_en": "Corrosive substances", "src": "/chem/adr/ADR_8.svg"},
    "ADR_pollution": {"label_de": "Umweltgefährdend", "label_en": "Environmentally hazardous", "src": "/chem/adr/ADR_pollution.svg"},
    "ADR_LQ": {"label_de": "LQ / Limited Quantity", "label_en": "Limited Quantity", "src": "/chem/adr/ADR_LQ.jpg"},
}
GHS_OPTIONS = [
    {"label": f"{code} · {definition['label_de']}", "value": code}
    for code, definition in GHS_SYMBOLS.items()
]
ADR_OPTIONS = [
    {"label": "ADR Klasse 3 · Entzündbare flüssige Stoffe", "value": "ADR_3"},
    {"label": "ADR Klasse 5.1 · Entzündend wirkende Stoffe", "value": "ADR_5.1"},
    {"label": "ADR Klasse 8 · Ätzende Stoffe", "value": "ADR_8"},
    {"label": "Umweltgefährdend / Fisch-Baum-Symbol", "value": "ADR_pollution"},
    {"label": "LQ / Limited Quantity", "value": "ADR_LQ"},
]
WGK_OPTIONS = [{"label": "leer / unbekannt", "value": ""}] + [
    {"label": f"{code} · {label}", "value": code}
    for code, label in WGK_LABELS.items()
]
STORAGE_CLASS_OPTIONS = [{"label": "leer / unbekannt", "value": ""}] + [
    {"label": f"{code} · {label}", "value": code}
    for code, label in STORAGE_CLASS_LABELS.items()
]


def _with_session(callback):
    def wrapper(*args, **kwargs):
        with session_scope(get_pim_settings().database_url) as session:
            return callback(session, *args, **kwargs)

    wrapper.__name__ = callback.__name__
    return wrapper


def import_upload_root() -> Path:
    root = get_pim_settings().asset_storage_root.parent / "import_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_uploaded_import_file(contents: str, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMPORT_SUFFIXES:
        raise ValueError("Unterstützt werden nur .csv, .xlsx und .xls")
    try:
        _header, encoded = contents.split(",", 1)
        payload = base64.b64decode(encoded)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Upload konnte nicht dekodiert werden") from exc
    target = import_upload_root() / filename
    candidate = target
    counter = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
        counter += 1
    candidate.write_bytes(payload)
    return candidate


def decode_uploaded_file(contents: str | None) -> bytes:
    if not contents:
        raise ValueError("Keine Datei hochgeladen.")
    try:
        _header, encoded = contents.split(",", 1)
        return base64.b64decode(encoded)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Upload konnte nicht dekodiert werden") from exc


def _render_suva_check_summary(check: dict | None) -> html.Div:
    if not check:
        return html.Div("Noch keine SUVA-Prüfung für die gewählte SDB-Version dokumentiert.")
    source = check.get("source") or {}
    return html.Div(
        [
            html.Strong(f"SUVA-Status: {check.get('overall_status') or '-'}"),
            html.Div(f"Prüfdatum: {check.get('checked_at') or '-'}"),
            html.Div(f"Quelle: {source.get('file_name') or '-'} · Import-ID: {source.get('id') or '-'}"),
            html.Div(f"SHA256: {source.get('sha256') or '-'}"),
        ]
    )


def _upsert_suva_block_in_section_8(section_8: str, suva_block: str) -> str:
    text = str(section_8 or "").strip()
    block = str(suva_block or "").strip()
    marker = "Schweizer Arbeitsplatzgrenzwerte / SUVA MAK/BAT"
    if not block:
        return text
    if marker in text:
        text = re.sub(rf"(?is){re.escape(marker)}.*?(?=\n\s*(?:8\.2\b|$))", block, text).strip()
        return text
    split = re.search(r"(?im)^\s*8\.2\b", text)
    if split:
        return f"{text[:split.start()].rstrip()}\n\n{block}\n\n{text[split.start():].lstrip()}".strip()
    return f"{text}\n\n{block}".strip() if text else block


def _replace_section_in_sdb_text(text: str, section_number: int, new_section: str) -> str:
    normalized = (text or "").replace("\r", "\n")
    pattern = re.compile(
        rf"(?ims)^\s*(?:ABSCHNITT|SECTION)?\s*{section_number}\s*[\.:]\s+.*?(?=^\s*(?:ABSCHNITT|SECTION)?\s*(?:{section_number + 1}|[1-9]|1[0-6])\s*[\.:]\s+|\Z)"
    )
    if pattern.search(normalized):
        return pattern.sub(str(new_section or "").strip(), normalized, count=1)
    return f"{normalized.rstrip()}\n\n{str(new_section or '').strip()}".strip()


def _add_suva_suggestions_to_parsed_sdb(session: Session, parsed_pdf: dict, product: Product | None = None) -> tuple[dict, dict]:
    enriched = dict(parsed_pdf or {})
    sections_json, suva_report = enrich_sdb_sections_with_suva_suggestions(session, enriched.get("sections_json") or {}, product=product)
    enriched["sections_json"] = sections_json
    enriched["suva_report"] = suva_report
    return enriched, suva_report


def _suva_import_protocol_message(suva_report: dict | None) -> str:
    report = suva_report or {}
    if report.get("status") == "no_suva_source":
        return "SUVA-Grenzwerte konnten nicht geprüft werden: keine SUVA-Referenzliste importiert."
    return (
        "SUVA-Grenzwerte geprüft. "
        f"CAS-Treffer: {len(report.get('cas_matches') or [])}, "
        f"prüfpflichtig: {len(report.get('review_required_items') or [])}, "
        f"ohne Treffer: {len(report.get('no_match_items') or [])}."
    )


def app_snapshot(session: Session, product_archive_filter: str = "active", variant_archive_filter: str = "active") -> dict:
    ensure_default_sales_channels(session)
    counts = dashboard_counts(session)
    brands = list_brands(session)
    categories = list_categories(session, sales_channel_code="*")
    sales_channels = list_sales_channels(session)
    channel_categories = list_channel_categories(session)
    return {
        "counts": counts,
        "products": list_products(session, archive_filter=product_archive_filter),
        "chemistry_products": list_chemical_products(session),
        "variants": list_variants(session, archive_filter=variant_archive_filter),
        "categories": categories,
        "assets": list_assets(session),
        "jobs": list_import_jobs(session),
        "attributes": list_attribute_overview(session),
        "families": list_family_overview(session),
        "translations": list_translation_overview(session),
        "variant_translations": list_variant_translation_overview(session),
        "languages": list_languages(session),
        "translation_prompts": list_translation_prompts(session),
        "rules": list_rule_overview(session),
        "sales_channels": sales_channels,
        "channel_categories": channel_categories,
        "brand_options": [{"label": item["name"], "value": item["name"]} for item in brands],
        "category_options": [
            {
                "label": item["name"],
                "value": item["id"],
                "sales_channel_id": item.get("sales_channel_id"),
                "sales_channel_code": item.get("sales_channel_code"),
            }
            for item in categories
        ],
        "category_parent_options": [
            {
                "label": item["name"],
                "value": item["id"],
                "sales_channel_id": item.get("sales_channel_id"),
                "sales_channel_code": item.get("sales_channel_code"),
            }
            for item in categories
        ],
        "sales_channel_options": [{"label": f"{item['name']} ({item['code']})", "value": item["id"]} for item in sales_channels],
        "sales_channel_code_options": [{"label": f"{item['name']} ({item['code']})", "value": item["code"]} for item in sales_channels],
        "channel_category_options": [
            {
                "label": f"{item['sales_channel_code']} · {item['name']} · {item['external_category_id']}",
                "value": item["id"],
                "sales_channel_id": item["sales_channel_id"],
            }
            for item in channel_categories
        ],
    }


def _enrichment_options(
    seed_url: str | None,
    supplier_name: str | None,
    max_pages: int | None,
    option_values: list[str] | None,
    resolver_mode: str | None = None,
    resolver_listing_url: str | None = None,
) -> EnrichmentJobOptions:
    selected = set(option_values or [])
    normalized_seed_url = (seed_url or "").strip()
    normalized_supplier_name = (supplier_name or "").strip() or None
    normalized_listing_url = (resolver_listing_url or "").strip() or None
    return EnrichmentJobOptions(
        seed_url=normalized_seed_url,
        supplier_name=normalized_supplier_name,
        resolver_mode=resolver_mode or "generic_crawl",
        resolver_listing_url=normalized_listing_url,
        max_pages=max_pages or 40,
        only_empty_fields="only_empty" in selected,
        update_description="description" in selected,
        update_assets="assets" in selected,
        update_packaging="packaging" in selected,
        update_specifications="specifications" in selected,
        update_technical_features="technical" in selected,
        update_source_urls="source_urls" in selected,
    )


def _page_rows(rows: list[dict] | None, pagination_info: dict | None) -> list[dict]:
    row_list = rows or []
    if not row_list:
        return []
    if not pagination_info:
        return row_list
    current_page = int(pagination_info.get("currentPage", 0) or 0)
    page_size = int(pagination_info.get("pageSize", len(row_list)) or len(row_list))
    start = current_page * page_size
    end = start + page_size
    return row_list[start:end]


def _float_or_none(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(str(value))
    except Exception:
        return None


def _int_or_zero(value: object) -> int:
    if value in {None, ""}:
        return 0
    try:
        return int(float(str(value)))
    except Exception:
        return 0


def _int_or_none(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(float(str(value)))
    except Exception:
        return None


def _selected_ids(rows: list[dict] | None) -> list[int]:
    return [int(row["id"]) for row in (rows or []) if row.get("id") is not None]


def _bulk_action_options(context: str | None) -> list[dict]:
    base = [
        {"label": "Produkt-Listings verwalten", "value": "product_listings"},
        {"label": "Kanal-Kategorie-Mappings verwalten", "value": "product_category_mappings"},
        {"label": "Vertriebskanal zuweisen", "value": "assign_sales_channel"},
        {"label": "Kanal-Kategorie zuweisen", "value": "assign_channel_category"},
        {"label": "Varianten-Listings verwalten", "value": "variant_listings"},
    ]
    if context == "variants":
        return [
            {"label": "Varianten-Listings verwalten", "value": "variant_listings"},
            {"label": "Vertriebskanal zuweisen", "value": "assign_sales_channel"},
            {"label": "Kanal-Kategorie zuweisen", "value": "assign_channel_category"},
        ]
    return base


def _bulk_action_default(trigger_id: object, context: str) -> str:
    if trigger_id in {"product-category-action-open-button"}:
        return "assign_channel_category"
    if trigger_id in {"product-variant-listings-action-open-button", "variant-listings-action-open-button"}:
        return "variant_listings"
    if trigger_id in {"product-listings-action-open-button"}:
        return "product_listings"
    if context == "variants":
        return "variant_listings"
    return "assign_sales_channel"


def _channel_bulk_actions_style(has_selection: bool) -> dict:
    return {"display": "block"} if has_selection else {"display": "none"}


def _product_bulk_updates(fields: list[str] | None, source_language: str | None, brand_name: str | None, status: str | None, is_chemical: bool | None) -> dict[str, object]:
    selected = set(fields or [])
    updates: dict[str, object] = {}
    if "source_language" in selected:
        updates["source_language"] = source_language
    if "brand_name" in selected:
        updates["brand_name"] = brand_name
    if "status" in selected:
        updates["status"] = status
    if "is_chemical" in selected:
        updates["is_chemical"] = bool(is_chemical)
    return updates


def _variant_bulk_updates(
    fields: list[str] | None,
    status: str | None,
    price: float | None,
    currency: str | None,
    cost_price: float | None,
    cost_currency: str | None,
    stock_qty: int | None,
    barcode: str | None,
    option_name: str | None,
    option_value: str | None,
    packaging: str | None,
) -> dict[str, object]:
    selected = set(fields or [])
    values = {
        "status": status,
        "price": price,
        "currency": currency,
        "cost_price": cost_price,
        "cost_currency": cost_currency,
        "stock_qty": stock_qty,
        "barcode": barcode,
        "option_name": option_name,
        "option_value": option_value,
        "packaging": packaging,
    }
    return {field: values.get(field) for field in selected}


def _bulk_edit_message(prefix: str, result: dict[str, object], *, apply: bool) -> str:
    mode = "Apply" if apply else "Vorschau"
    backup = f" · Backup: {result.get('backup_path')}" if result.get("backup_path") else ""
    extra_message = f" · {result.get('message')}" if result.get("message") else ""
    return (
        f"{prefix}: {mode} · {result.get('updated', 0)} Änderung(en), "
        f"{result.get('skipped', 0)} übersprungen, {result.get('errors', 0)} Fehler.{backup}{extra_message}"
    )


def _website_crawler_message(label: str, summary: dict[str, object]) -> str:
    direct_updates = int(summary.get("direct_updated_fields", summary.get("updated_fields", 0)) or 0)
    text_candidates = int(summary.get("text_candidates", 0) or 0)
    asset_candidates = int(summary.get("asset_candidates", 0) or 0)
    candidate_total = int(summary.get("candidate_fields", text_candidates + asset_candidates) or 0)
    discovered = int(summary.get("discovered_urls", 0) or 0)
    matched = int(summary.get("matched_products", 0) or 0)
    errors = int(summary.get("errors", 0) or 0)
    parts = [
        f"{label} abgeschlossen",
        f"{discovered} URL(s) geprüft",
        f"{matched} Treffer",
        f"{direct_updates} Produkt-/Variantenfelder direkt geändert",
        f"{candidate_total} Kandidaten gespeichert",
    ]
    if text_candidates or asset_candidates:
        parts.append(f"Textkandidaten: {text_candidates}")
        parts.append(f"Asset-Kandidaten: {asset_candidates}")
    parts.append(f"Fehler: {errors}")
    message = " · ".join(parts) + "."
    if candidate_total and not direct_updates:
        message += " Tintolav/lieferantenspezifische Inhalte wurden als Kandidaten gespeichert; bitte über Produktdaten- oder Asset-Anreicherung prüfen und übernehmen."
    elif candidate_total:
        message += " Lieferantenspezifische Inhalte wurden teilweise als Kandidaten gespeichert; bitte prüfen."
    return message


def _website_crawler_result_box(label: str, summary: dict[str, object]) -> html.Div:
    direct_updates = int(summary.get("direct_updated_fields", summary.get("updated_fields", 0)) or 0)
    text_candidates = int(summary.get("text_candidates", 0) or 0)
    asset_candidates = int(summary.get("asset_candidates", 0) or 0)
    candidate_total = int(summary.get("candidate_fields", text_candidates + asset_candidates) or 0)
    errors = int(summary.get("errors", 0) or 0)
    status_label = "Erfolgreich abgeschlossen" if errors == 0 else "Mit Fehlern abgeschlossen"
    status_class = "crawler-result-status crawler-result-status-success" if errors == 0 else "crawler-result-status crawler-result-status-error"
    metrics = [
        ("Lieferant", summary.get("supplier_name") or "-"),
        ("Gefundene URLs", summary.get("discovered_urls", 0)),
        ("Gematchte Produkte", summary.get("matched_products", 0)),
        ("Direkt aktualisierte Felder", direct_updates),
        ("Kandidaten gespeichert", candidate_total),
        ("Fehler", errors),
    ]
    candidate_hint = None
    if candidate_total and not direct_updates:
        candidate_hint = (
            "Lieferantenspezifische Texte und Assets wurden als Kandidaten gespeichert. "
            "Prüfen und übernehmen erfolgt separat über Produktdaten- oder Asset-Anreicherung."
        )
    elif candidate_total:
        candidate_hint = "Ein Teil der gefundenen Inhalte wurde als Kandidat gespeichert und muss geprüft werden."
    return html.Div(
        [
            html.Div(
                [
                    html.Div(status_label, className=status_class),
                    html.Div(label, className="crawler-result-title"),
                ],
                className="crawler-result-header",
            ),
            html.Div(
                [
                    html.Div([html.Span(title), html.Strong(str(value))], className="crawler-result-metric")
                    for title, value in metrics
                ],
                className="crawler-result-grid",
            ),
            html.Div(candidate_hint, className="crawler-result-hint") if candidate_hint else None,
            html.Details(
                [
                    html.Summary("Details / technisches Log"),
                    html.Pre(json.dumps(summary, ensure_ascii=False, indent=2), className="crawler-log-block"),
                ],
                className="crawler-details",
            ),
        ],
        className="crawler-result-card",
    )


def _website_crawler_error_box(message: str) -> html.Div:
    return html.Div(
        [
            html.Div("Fehler", className="crawler-result-status crawler-result-status-error"),
            html.Div(message, className="crawler-result-error"),
        ],
        className="crawler-result-card",
    )


def _empty_chemistry_detail(message: str) -> tuple[object, ...]:
    return (
        message,
        None,
        None,
        None,
        None,
        "draft",
        "en",
        False,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        False,
        [],
        "none",
        [],
        False,
        None,
        None,
        None,
        None,
        html.Div("Keine Quelle gespeichert.", style={"color": "#64748b"}),
        "Aus SDB anreichern",
        False,
        None,
        None,
        [],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        False,
        False,
        True,
        None,
        None,
        True,
    )


def _empty_chemistry_sdb_detail(message: str) -> tuple[object, ...]:
    return (
        None,
        None,
        None,
        [],
        message,
        "OpenAI-Anbindung: nicht konfiguriert",
        "review_required",
        "Entwurf 1.0",
        None,
        None,
        "VOXSTER GmbH",
        "Obere Ifangstrasse 10",
        None,
        "8215",
        "Hallau",
        "CH",
        "+41 52 502 67 23",
        "info@voxster.ch",
        "Noch keine LLM-Normalisierung gespeichert.",
        None,
        [""] * len(SDB_SECTION_TITLES),
        html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"}),
        html.Div("Noch keine LLM-Normalisierung gespeichert.", style={"color": "#64748b"}),
        [],
    )


def _category_rows_for_grid(categories: list[dict] | None, collapsed_ids: list[int] | None = None) -> list[dict]:
    category_list = categories or []
    collapsed = {int(item) for item in (collapsed_ids or [])}
    valid_ids = {int(item["id"]) for item in category_list if item.get("id") is not None}
    children_by_parent: dict[int | None, list[dict]] = {None: []}
    for item in category_list:
        category_id = item.get("id")
        if category_id is None:
            continue
        parent_id = item.get("parent_id")
        key = int(parent_id) if parent_id in valid_ids else None
        children_by_parent.setdefault(key, []).append(item)
        children_by_parent.setdefault(int(category_id), [])

    rows: list[dict] = []

    def walk(parent_id: int | None, level: int) -> None:
        for item in children_by_parent.get(parent_id, []):
            category_id = int(item["id"])
            children = children_by_parent.get(category_id, [])
            has_children = bool(children)
            expanded = category_id not in collapsed
            prefix = "▾ " if has_children and expanded else ("▸ " if has_children else "• ")
            indent = " " * (level * 4)
            rows.append(
                {
                    **item,
                    "tree_toggle": "▾" if has_children and expanded else ("▸" if has_children else "•"),
                    "tree_name": f"{indent}{item.get('name') or ''}",
                    "tree_level": level,
                    "has_children": has_children,
                    "expanded": expanded,
                }
            )
            if has_children and expanded:
                walk(category_id, level + 1)

    walk(None, 0)
    return rows


def _channel_category_tree_rows_for_grid(tree_rows: list[dict] | None, collapsed_ids: list[int] | None = None) -> list[dict]:
    rows = tree_rows or []
    collapsed = {int(item) for item in (collapsed_ids or [])}
    hidden_levels: list[int] = []
    visible: list[dict] = []
    for item in rows:
        level = int(item.get("tree_level") or 0)
        hidden_levels = [hidden_level for hidden_level in hidden_levels if hidden_level < level]
        if hidden_levels:
            continue
        category_id = item.get("id")
        has_children = bool(item.get("has_children"))
        expanded = category_id is not None and int(category_id) not in collapsed
        indent = " " * (level * 4)
        visible.append(
            {
                **item,
                "tree_toggle": "▾" if has_children and expanded else ("▸" if has_children else "•"),
                "tree_name": f"{indent}{item.get('name') or ''}",
                "expanded": expanded,
            }
        )
        if has_children and not expanded and category_id is not None:
            hidden_levels.append(level)
    return visible


def _filter_categories_for_channel(categories: list[dict] | None, sales_channel_code: str | None) -> list[dict]:
    rows = categories or []
    code = (sales_channel_code or "").strip()
    if not code:
        return rows
    return [row for row in rows if row.get("sales_channel_code") == code]


def _filter_category_options_for_channel(options: list[dict] | None, sales_channel_code: str | None) -> list[dict]:
    rows = options or []
    code = (sales_channel_code or "").strip()
    if not code:
        return rows
    return [row for row in rows if row.get("sales_channel_code") == code]


def _detail_variant_translation_rows(detail: dict | None) -> list[dict]:
    rows: list[dict] = []
    for variant in (detail or {}).get("variants", []):
        for translation in variant.get("translations", []):
            rows.append(
                {
                    "id": translation.get("id"),
                    "variant_id": variant.get("id"),
                    "variant_sku": variant.get("sku"),
                    "language_code": translation.get("language_code"),
                    "title": translation.get("title"),
                    "option_label_override": translation.get("option_label_override"),
                    "package_label": translation.get("package_label"),
                }
            )
    return rows


def _detail_source_short_description(detail: dict | None) -> str | None:
    if not detail:
        return None
    source_language = (detail.get("source_language") or "").strip()
    translations = detail.get("translations") or []
    if source_language:
        for translation in translations:
            if translation.get("language_code") == source_language:
                return translation.get("short_description")
    return None


def render_global_process_status(status: dict | None) -> html.Div:
    data = status or {}
    state = data.get("status") or "ready"
    labels = {
        "ready": "Bereit",
        "running": "Läuft",
        "success": "Erfolgreich abgeschlossen",
        "partial_success": "Teilweise erfolgreich",
        "error": "Fehler",
        "cancelled": "Abgebrochen",
    }
    color_map = {
        "ready": ("#f8fafc", "#475569", "#cbd5e1"),
        "running": ("#eff6ff", "#1d4ed8", "#93c5fd"),
        "success": ("#ecfdf5", "#047857", "#86efac"),
        "partial_success": ("#fffbeb", "#b45309", "#fcd34d"),
        "error": ("#fef2f2", "#b91c1c", "#fecaca"),
        "cancelled": ("#f8fafc", "#475569", "#cbd5e1"),
    }
    background, color, border = color_map.get(state, color_map["ready"])
    running_hint = "Prozess läuft - bitte warten und keine weiteren Aktionen starten." if state == "running" else ""
    counters = data.get("counters") or {}
    options = data.get("options") or {}
    selection = data.get("selection") or {}
    progress_current = data.get("progress_current") or 0
    progress_total = data.get("progress_total") or 0
    progress_text = f"{progress_current} / {progress_total}" if progress_total else "-"
    messages = data.get("last_messages") or []
    return html.Div(
        [
            html.Div(
                [
                    html.Strong(f"Prozessstatus: {labels.get(state, state)}"),
                    html.Span(f" · {data.get('process_name') or 'Kein laufender Prozess'}"),
                    html.Span(" · " + running_hint, style={"fontWeight": "700"}) if running_hint else html.Span(""),
                ]
            ),
            html.Div(
                [
                    html.Span(f"Start: {data.get('started_at') or '-'}"),
                    html.Span(f" · Ende: {data.get('finished_at') or '-'}"),
                    html.Span(f" · Fortschritt: {progress_text}"),
                    html.Span(f" · Report: {data.get('report_path') or '-'}"),
                ],
                style={"fontSize": "13px"},
            ),
            html.Div(f"Optionen: {options or '-'} · Auswahl: {selection or '-'}", style={"fontSize": "13px"}) if state != "ready" else html.Div(),
            html.Div(f"Zähler: {counters}", style={"fontSize": "13px"}) if counters else html.Div(),
            html.Div(f"Fehler: {data.get('error_message')}", style={"fontSize": "13px", "fontWeight": "700"}) if data.get("error_message") else html.Div(),
            html.Details(
                [
                    html.Summary("Prozess-Log anzeigen"),
                    html.Pre("\n".join(messages[-50:]), style={"whiteSpace": "pre-wrap", "fontSize": "12px", "margin": "8px 0 0"}),
                ],
                open=state in {"running", "error"},
            )
            if messages
            else html.Div(),
        ],
        style={"background": background, "color": color, "border": f"1px solid {border}", "borderRadius": "10px", "padding": "10px 12px", "margin": "10px 0"},
    )


def metric_card(title: str, store_key: str, button_id: str) -> html.Button:
    return html.Button(
        [
            html.Div(title, className="metric-title"),
            html.Div(id=store_key, className="metric-value"),
        ],
        id=button_id,
        className="metric-card metric-card-button",
    )


PRODUCT_ENRICH_MODAL_HIDDEN = {
    "display": "none",
}


PRODUCT_ENRICH_MODAL_VISIBLE = {
    "position": "fixed",
    "inset": "0",
    "background": "rgba(0, 0, 0, 0.45)",
    "display": "flex",
    "alignItems": "flex-start",
    "justifyContent": "center",
    "padding": "48px 20px",
    "zIndex": "1000",
}


VARIANT_ENRICH_MODAL_HIDDEN = PRODUCT_ENRICH_MODAL_HIDDEN


VARIANT_ENRICH_MODAL_VISIBLE = PRODUCT_ENRICH_MODAL_VISIBLE


def _render_asset_preview(assets: list[dict] | None) -> html.Div:
    asset_list = assets or []
    if not asset_list:
        return html.Div("Keine Asset-Vorschau vorhanden.")

    cards: list[html.Div] = []
    for asset in asset_list:
        asset_id = asset.get("id")
        if asset_id is None:
            continue
        filename = asset.get("filename") or f"asset-{asset_id}"
        product_label = asset.get("product_sku") or "-"
        variant_label = asset.get("variant_sku") or "-"
        mime_type = _normalized_asset_mime_type(asset)
        meta = f"{mime_type or 'datei'}"
        if asset.get("width") and asset.get("height"):
            meta += f" | {asset['width']}x{asset['height']}"
        cards.append(
            html.Div(
                [
                    html.Div(f"Artikel: {product_label}", style={"fontSize": "12px", "color": "#444"}),
                    html.Div(f"Variante: {variant_label}", style={"fontSize": "12px", "color": "#444", "marginBottom": "6px"}),
                    html.Div(f"Asset-ID: {asset_id}", style={"fontSize": "12px", "color": "#475569", "marginBottom": "4px"}),
                    html.Div(filename, style={"fontWeight": "600", "marginBottom": "6px"}),
                    _render_asset_media(asset, mode="card"),
                    html.Div(meta, style={"fontSize": "12px", "marginTop": "6px"}),
                    html.Div(
                        html.A("Quelle", href=asset.get("source_url"), target="_blank") if asset.get("source_url") else "-",
                        style={"fontSize": "12px", "marginTop": "4px"},
                    ),
                ],
                style={"border": "1px solid #ddd", "borderRadius": "6px", "padding": "10px", "background": "#fff"},
            )
        )
    return html.Div(cards, style={"display": "grid", "gridTemplateColumns": "repeat(auto-fill, minmax(240px, 1fr))", "gap": "12px"})


def _render_asset_links(assets: list[dict] | None) -> html.Div:
    asset_list = assets or []
    if not asset_list:
        return html.Div("Keine Assets vorhanden.")
    rows: list[html.Div] = []
    for asset in asset_list:
        asset_id = asset.get("id")
        if asset_id is None:
            continue
        asset_url = f"/asset-file/{asset_id}"
        filename = asset.get("filename") or f"asset-{asset_id}"
        mime_type = asset.get("mime_type") or "-"
        product_label = asset.get("product_sku") or "-"
        variant_label = asset.get("variant_sku") or "-"
        sdb_label = ""
        if asset.get("sdb_document_id"):
            sdb_label = (
                f" · SDB {asset.get('sdb_language_code') or '-'} · "
                f"{asset.get('sdb_generated_at_display') or '-'} · {asset.get('sdb_status') or '-'}"
            )
        rows.append(
            html.Div(
                [
                    html.Span(f"{product_label}"),
                    html.Span(" / "),
                    html.Span(f"{variant_label}"),
                    html.Span(" - "),
                    html.A(f"Öffnen [{asset_id}]: {filename}", href=asset_url, target="_blank"),
                    html.Span(f" ({mime_type})"),
                    html.Span(sdb_label, style={"color": "#0f766e", "fontSize": "12px"}),
                    html.Span(" "),
                    html.Button("Löschen", id={"type": "asset-delete-direct", "asset_id": asset_id}, n_clicks=0),
                ]
            )
        )
    return html.Div(rows, style={"display": "grid", "gap": "6px"})


def _normalized_asset_mime_type(asset: dict | None) -> str:
    data = asset or {}
    mime_type = str(data.get("mime_type") or "").strip().lower()
    if mime_type and mime_type != "application/octet-stream":
        return mime_type
    filename = str(data.get("filename") or "")
    guessed, _encoding = mimetypes.guess_type(filename)
    return (guessed or mime_type or "application/octet-stream").lower()


def _options_from_rows(rows: list[dict] | None, field: str) -> list[dict]:
    values = sorted({str(row.get(field)).strip() for row in rows or [] if str(row.get(field) or "").strip()})
    return [{"label": value, "value": value} for value in values]


def _filter_sdb_documents(
    rows: list[dict] | None,
    language_filter: str | None,
    status_filter: str | None,
    source_filter: str | None,
    current_filter: str | None,
) -> list[dict]:
    result: list[dict] = []
    for row in rows or []:
        if language_filter and row.get("locale") != language_filter:
            continue
        if status_filter and row.get("status") != status_filter:
            continue
        if source_filter and row.get("source") != source_filter:
            continue
        if (current_filter or "current") == "current" and not row.get("is_current"):
            continue
        result.append(row)
    return result


def _selected_sdb_document_ids(selected_rows: list[dict] | None) -> list[int]:
    ids: list[int] = []
    for row in selected_rows or []:
        value = row.get("id")
        if value in (None, ""):
            continue
        document_id = int(value)
        if document_id not in ids:
            ids.append(document_id)
    return ids


def _render_sdb_document_selection_summary(row: dict | None) -> html.Div:
    data = row or {}
    if not data:
        return html.Div("Keine SDB-Version ausgewählt. Wähle eine Zeile, um Text, PDF-Status und nächste Aktion zu sehen.")
    pdf_url = str(data.get("pdf_url") or "").strip()
    has_text = bool(data.get("has_text"))
    status = str(data.get("status") or "-")
    title = str(data.get("title") or f"SDB-Dokument {data.get('id') or ''}").strip()
    parts: list[object] = [
        html.Strong(f"Gewählte Version {data.get('id')}: {title}"),
        html.Div(
            f"Sprache/Region: {data.get('locale') or '-'} / {data.get('region_code') or '-'} · "
            f"Status: {status} · Text: {'Ja' if has_text else 'Nein'} · PDF: {'Ja' if pdf_url else 'Nein'}",
            style={"marginTop": "4px"},
        ),
    ]
    if data.get("error_message"):
        parts.append(html.Div(f"Fehler: {data.get('error_message')}", style={"color": "#b91c1c", "marginTop": "4px"}))
    if pdf_url:
        parts.append(
            html.Div(
                [
                    html.A("PDF öffnen / herunterladen", href=pdf_url, target="_blank"),
                    html.Span(" · "),
                    html.Span("Falls der Browser öffnet statt lädt: Rechtsklick > Link speichern."),
                ],
                style={"marginTop": "6px"},
            )
        )
    elif has_text:
        parts.append(html.Div("Nächster Schritt: PDF für gewählte Version erzeugen.", style={"marginTop": "6px", "color": "#92400e"}))
    else:
        parts.append(html.Div("Nächster Schritt: Erst Text erzeugen oder ein anderes Dokument mit Text wählen.", style={"marginTop": "6px", "color": "#b91c1c"}))
    return html.Div(parts)


def _render_sdb_document_multi_selection_summary(rows: list[dict] | None) -> html.Div:
    selected_rows = rows or []
    if len(selected_rows) <= 1:
        return _render_sdb_document_selection_summary(selected_rows[0] if selected_rows else None)
    ids = [str(row.get("id")) for row in selected_rows if row.get("id") not in (None, "")]
    first = selected_rows[0]
    return html.Div(
        [
            html.Strong(f"{len(selected_rows)} SDB-Versionen ausgewählt"),
            html.Div(f"IDs: {', '.join(ids[:12])}{' …' if len(ids) > 12 else ''}"),
            html.Div(
                "Sammelaktionen: archivieren, löschen, als geprüft markieren oder Status setzen. "
                f"Einzelaktionen wie CH-SDB prüfen, PDF erzeugen und final freigeben verwenden die erste Auswahl: {first.get('id')}.",
                style={"marginTop": "4px"},
            ),
        ]
    )


def _render_chemical_internet_status(kind: str, message: str, *, details: list[str] | None = None) -> html.Div:
    palette = {
        "running": ("#1d4ed8", "#eff6ff", "#bfdbfe"),
        "success": ("#047857", "#ecfdf5", "#a7f3d0"),
        "error": ("#b91c1c", "#fef2f2", "#fecaca"),
        "info": ("#475569", "#f8fafc", "#cbd5e1"),
    }
    color, background, border = palette.get(kind, palette["info"])
    return html.Div(
        [
            html.Div(message, style={"fontWeight": "700", "color": color}),
            *[html.Div(item, style={"fontSize": "13px", "color": "#475569", "marginTop": "4px"}) for item in (details or [])],
        ],
        style={
            "border": f"1px solid {border}",
            "borderLeft": f"4px solid {color}",
            "background": background,
            "borderRadius": "10px",
            "padding": "10px 12px",
        },
    )


def _render_asset_media(asset: dict, mode: str = "detail"):
    asset_id = asset.get("id")
    if asset_id is None:
        return html.Div("Keine Vorschau")
    asset_url = f"/asset-file/{asset_id}"
    filename = asset.get("filename") or f"asset-{asset_id}"
    mime_type = _normalized_asset_mime_type(asset)
    if mime_type.startswith("image/"):
        image_style = {
            "display": "block",
            "objectFit": "contain",
            "borderRadius": "8px",
            "background": "#fff",
        }
        if mode == "card":
            image_style.update({"maxWidth": "180px", "maxHeight": "180px", "width": "100%"})
        elif mode == "grid":
            image_style.update({"width": "56px", "height": "56px", "margin": "0 auto"})
        else:
            image_style.update({"width": "100%", "maxHeight": "520px"})
        return html.A(html.Img(src=asset_url, style=image_style), href=asset_url, target="_blank")
    if mime_type == "application/pdf":
        pdf_badge = html.Div(
            [
                html.Div("PDF", style={"fontWeight": "700", "fontSize": "12px", "letterSpacing": "0.06em", "color": "#991b1b"}),
                html.Div(filename, style={"fontSize": "12px", "color": "#334155", "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap", "maxWidth": "100%"}),
            ],
            style={
                "border": "1px solid #fecaca",
                "background": "#fef2f2",
                "borderRadius": "10px",
                "padding": "10px",
                "display": "flex",
                "flexDirection": "column",
                "gap": "4px",
                "alignItems": "flex-start",
                "justifyContent": "center",
                "minHeight": "56px" if mode == "grid" else "84px",
            },
        )
        if mode == "grid":
            return html.A(pdf_badge, href=asset_url, target="_blank", title="PDF öffnen")
        iframe = html.Iframe(
            src=asset_url,
            style={"width": "100%", "height": "520px" if mode == "detail" else "280px", "border": "1px solid #e2e8f0", "borderRadius": "10px", "background": "#fff"},
        )
        return html.Div(
            [
                pdf_badge,
                html.Div(html.A("Öffnen", href=asset_url, target="_blank"), style={"marginTop": "8px", "marginBottom": "8px"}),
                iframe,
            ]
        )
    fallback = html.Div(
        [
            html.Div("DATEI", style={"fontWeight": "700", "fontSize": "12px", "letterSpacing": "0.06em", "color": "#1e3a8a"}),
            html.Div(filename, style={"fontSize": "12px", "color": "#334155", "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap", "maxWidth": "100%"}),
        ],
        style={
            "border": "1px solid #bfdbfe",
            "background": "#eff6ff",
            "borderRadius": "10px",
            "padding": "10px",
            "minHeight": "56px" if mode == "grid" else "84px",
        },
    )
    return html.Div([fallback, html.Div(html.A("Öffnen", href=asset_url, target="_blank"), style={"marginTop": "8px"})]) if mode != "grid" else html.A(fallback, href=asset_url, target="_blank")


def _render_asset_detail(asset: dict | None) -> html.Div:
    if not asset:
        return html.Div("Kein Asset ausgewählt.", style={"color": "#64748b"})
    asset_id = asset.get("id")
    asset_url = f"/asset-file/{asset_id}" if asset_id is not None else None
    mime_type = _normalized_asset_mime_type(asset)
    meta_parts = [mime_type]
    if asset.get("width") and asset.get("height"):
        meta_parts.append(f"{asset['width']}x{asset['height']}")
    if asset.get("file_size"):
        meta_parts.append(f"{int(asset['file_size']):,} B".replace(",", "."))
    sdb_metadata = None
    if asset.get("sdb_document_id"):
        sdb_metadata = html.Div(
            [
                html.Div("SDB-Metadaten", style={"fontWeight": "700", "marginBottom": "4px"}),
                html.Div(f"Dokumenttyp: {asset.get('sdb_document_type') or 'SDB'}"),
                html.Div(f"Sprache: {asset.get('sdb_language_code') or '-'}"),
                html.Div(f"Generiert/importiert am: {asset.get('sdb_generated_at_display') or '-'}"),
                html.Div(f"Status: {asset.get('sdb_status') or '-'}"),
                html.Div(f"Quelle: {asset.get('sdb_source') or '-'}"),
                html.A("Im Chemie-Reiter öffnen", href="#chemistry-sdb-documents-grid"),
            ],
            className="selection-summary",
            style={"marginTop": "10px"},
        )
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.H4(asset.get("filename") or f"Asset {asset_id}", style={"margin": "0 0 8px 0"}),
                            html.Div(f"Asset-ID: {asset_id}", style={"fontSize": "12px", "color": "#475569"}),
                            html.Div(f"Artikel: {asset.get('product_sku') or '-'}", style={"fontSize": "13px", "color": "#334155", "marginTop": "6px"}),
                            html.Div(f"Variante: {asset.get('variant_sku') or '-'}", style={"fontSize": "13px", "color": "#334155"}),
                            html.Div(" | ".join(meta_parts), style={"fontSize": "13px", "color": "#475569", "marginTop": "8px"}),
                        ]
                    ),
                    html.Div(
                        [
                            html.A("Öffnen", href=asset_url, target="_blank") if asset_url else html.Span("-"),
                            html.Span(" · "),
                            html.A("Quelle", href=asset.get("source_url"), target="_blank") if asset.get("source_url") else html.Span("Keine Quelle"),
                        ],
                        style={"fontSize": "13px", "marginTop": "8px"},
                    ),
                    sdb_metadata,
                ],
                style={"marginBottom": "14px"},
            ),
            _render_asset_media(asset, mode="detail"),
        ],
        style={"border": "1px solid #e2e8f0", "borderRadius": "12px", "background": "#fff", "padding": "16px"},
    )


def _chemical_enrichment_field_labels() -> dict[str, str]:
    return {
        "product_name": "Produktname",
        "brand_name": "Hersteller / Marke",
        "cas_number": "CAS-Nummer",
        "ec_number": "EG-Nummer",
        "un_number": "UN-Nummer",
        "adr_relevant": "ADR-relevant",
        "hazard_class": "ADR-Klasse / Gefahrgutklasse",
        "packing_group": "Verpackungsgruppe",
        "chemical_type": "Stoffgruppe / Produkttyp",
        "ufi": "UFI-Nummer",
        "voc_content_percent": "VOC-Gehalt (%)",
        "ghs_pictograms": "GHS-Piktogramme",
        "signal_word": "Signalwort",
        "hazard_statements": "H-Sätze / Gefahrenhinweise",
        "precautionary_statements": "P-Sätze / Sicherheitshinweise",
        "sds_url": "SDB-/SDS-Link",
        "density": "Dichte",
        "ph_value": "pH-Wert",
        "flash_point": "Flammpunkt",
        "color": "Farbe",
        "odor": "Geruch",
        "solubility": "Löslichkeit",
        "boiling_point": "Siedepunkt / Siedebereich",
        "viscosity": "Viskosität",
        "storage_class": "Lagerklasse",
        "wgk": "WGK",
        "hazard_shipping_note": "Versandhinweise / ADR",
        "business_only": "Nur für Gewerbe",
        "age_check_required": "Altersprüfung erforderlich",
        "shippable": "Versandfähig",
        "limited_quantity": "LQ / Gefahrgutversand Hinweis",
        "shop_active": "Aktiv im Shop",
    }


def _format_chemical_preview_value(value: object) -> str:
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip()) or "-"
    if value in {None, ""}:
        return "-"
    return str(value)


def _is_blank_ui_value(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _render_chemical_enrichment_preview(product_detail: dict | None, enrichment: dict | None) -> html.Div:
    if not enrichment:
        return html.Div("Noch keine Internet-Anreicherung vorhanden.", style={"color": "#64748b"})
    normalized = enrichment.get("normalized_payload_json") or {}
    fields = normalized.get("fields") or {}
    if not fields:
        return html.Div("Keine extrahierten Werte vorhanden.", style={"color": "#64748b"})
    label_map = _chemical_enrichment_field_labels()
    rows: list[html.Tr] = []
    detail = product_detail or {}
    for field_name, label in label_map.items():
        field_payload = fields.get(field_name)
        if not isinstance(field_payload, dict):
            continue
        proposed = field_payload.get("value")
        current = detail.get(field_name)
        conflicts = field_payload.get("conflicts") or []
        status = "Neu"
        if not _is_blank_ui_value(current):
            status = "Konflikt" if _format_chemical_preview_value(current) != _format_chemical_preview_value(proposed) else "Gleich"
        rows.append(
            html.Tr(
                [
                    html.Td(label, style={"fontWeight": "600", "verticalAlign": "top", "padding": "8px"}),
                    html.Td(_format_chemical_preview_value(current), style={"padding": "8px", "color": "#475569", "verticalAlign": "top"}),
                    html.Td(_format_chemical_preview_value(proposed), style={"padding": "8px", "verticalAlign": "top"}),
                    html.Td(status, style={"padding": "8px", "fontWeight": "600", "color": "#b45309" if status == "Konflikt" else "#166534"}),
                    html.Td(
                        html.Div(
                            [
                                html.Div(
                                    f"{item.get('source_kind') or '-'}: {item.get('value')}",
                                    style={"fontSize": "12px", "color": "#475569"},
                                )
                                for item in conflicts[:4]
                            ]
                        )
                        if conflicts
                        else "-",
                        style={"padding": "8px", "verticalAlign": "top"},
                    ),
                ]
            )
        )
    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Feld", style={"textAlign": "left", "padding": "8px"}),
                            html.Th("Bestehend", style={"textAlign": "left", "padding": "8px"}),
                            html.Th("Extrahiert", style={"textAlign": "left", "padding": "8px"}),
                            html.Th("Status", style={"textAlign": "left", "padding": "8px"}),
                            html.Th("Konflikte", style={"textAlign": "left", "padding": "8px"}),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            style={"width": "100%", "borderCollapse": "collapse"},
        ),
        style={"overflowX": "auto"},
    )


def _chemical_enrichment_suggestion_rows(enrichment: dict | None) -> list[dict]:
    review = ((enrichment or {}).get("normalized_payload_json") or {}).get("enrichment") or {}
    suggestions = review.get("suggestions") or []
    rows: list[dict] = []
    for index, item in enumerate(suggestions):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": index + 1,
                "field": item.get("field"),
                "current_value": _format_chemical_preview_value(item.get("current_value")),
                "suggested_value": _format_chemical_preview_value(item.get("suggested_value")),
                "source_section": item.get("source_section"),
                "confidence": item.get("confidence"),
                "status": item.get("status"),
                "evidence": item.get("evidence"),
            }
        )
    return rows


def _chemical_enrichment_log_text(enrichment: dict | None) -> str:
    if not enrichment:
        return "Noch kein Protokoll vorhanden."
    review = ((enrichment or {}).get("normalized_payload_json") or {}).get("enrichment") or {}
    payload = {
        "status": review.get("status") or enrichment.get("status"),
        "last_run_at": review.get("last_run_at") or enrichment.get("extracted_at"),
        "sources": review.get("sources") or enrichment.get("document_links_json") or [],
        "not_found": review.get("not_found") or [],
        "warnings": review.get("warnings") or enrichment.get("warnings_json") or [],
        "log": review.get("log") or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_chemical_documents(documents: list[dict] | None) -> html.Div:
    if not documents:
        return html.Div("Keine gefundenen Dokumente.", style={"color": "#64748b"})
    return html.Div(
        [
            html.Div(
                [
                    html.Strong(str(item.get("role") or "-").upper()),
                    html.Span(" · "),
                    html.A(str(item.get("label") or item.get("url") or "Dokument"), href=item.get("url"), target="_blank"),
                    html.Span(f" · Quelle: {item.get('source') or '-'}", style={"color": "#64748b"}),
                ],
                style={"padding": "6px 0"},
            )
            for item in documents
        ]
    )


def _render_chemical_runs(runs: list[dict] | None) -> html.Div:
    if not runs:
        return html.Div("Noch keine Anreicherungs-Läufe.", style={"color": "#64748b"})
    return html.Div(
        [
            html.Div(
                [
                    html.Strong(f"#{run.get('id')}"),
                    html.Span(f" · {run.get('status') or '-'}"),
                    html.Span(f" · {run.get('source_kind') or '-'}"),
                    html.Span(f" · {run.get('extracted_at') or '-'}"),
                    html.Div(
                        html.A(run.get("reference_url"), href=run.get("reference_url"), target="_blank")
                        if run.get("reference_url")
                        else "-",
                        style={"marginTop": "4px"},
                    ),
                ],
                style={"padding": "8px 0", "borderBottom": "1px solid #e2e8f0"},
            )
            for run in runs[:6]
        ]
    )


def _render_sdb_pdf_link(sdb_data: dict | None, product_id: int | None) -> html.Div:
    if not sdb_data or not product_id or not sdb_data.get("generated_pdf_path"):
        return html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"})
    return html.Div(
        html.A("SDB-PDF öffnen", href=f"/chemical-sdb-pdf/{product_id}", target="_blank"),
        style={"marginTop": "8px"},
    )


def _render_sdb_llm_runs(runs: list[dict] | None) -> html.Div:
    if not runs:
        return html.Div("Noch keine LLM-Normalisierung gespeichert.", style={"color": "#64748b"})
    return html.Div(
        [
            html.Div(
                [
                    html.Strong(f"#{run.get('id')}"),
                    html.Span(f" · {run.get('status') or '-'}"),
                    html.Span(f" · {run.get('provider') or '-'}"),
                    html.Span(f" · {run.get('model') or '-'}"),
                    html.Div(
                        [
                            html.Div(f"Erstellt: {run.get('created_at') or '-'}", style={"fontSize": "12px", "color": "#64748b"}),
                            html.Div(
                                f"Prompt-Laenge: S={len(run.get('system_prompt') or '')} / U={len(run.get('user_prompt') or '')}",
                                style={"fontSize": "12px", "color": "#64748b"},
                            ),
                            html.Div(
                                f"Warnungen: {len(run.get('warnings_json') or [])}",
                                style={"fontSize": "12px", "color": "#64748b"},
                            ),
                        ],
                        style={"marginTop": "4px"},
                    ),
                ],
                style={"padding": "8px 0", "borderBottom": "1px solid #e2e8f0"},
            )
            for run in runs[:6]
        ]
    )


def _merge_sdb_ui_sections(
    stored_sections_json: dict | None,
    section_values: list[str] | None,
    *,
    issuer_name: str | None = None,
    issuer_address_line1: str | None = None,
    issuer_address_line2: str | None = None,
    issuer_postal_code: str | None = None,
    issuer_city: str | None = None,
    issuer_country_code: str | None = None,
    issuer_phone: str | None = None,
    issuer_email: str | None = None,
    product_context: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    sections = merge_sdb_sections(stored_sections_json)
    values = section_values or []
    for index in SDB_SECTION_TITLES:
        sections[f"section_{index}"]["title"] = SDB_SECTION_TITLES[index]
        if index - 1 < len(values):
            sections[f"section_{index}"]["content"] = str(values[index - 1] or "").strip()
    return sync_sdb_fields_from_content(
        sections,
        issuer_name=issuer_name,
        issuer_address_line1=issuer_address_line1,
        issuer_address_line2=issuer_address_line2,
        issuer_postal_code=issuer_postal_code,
        issuer_city=issuer_city,
        issuer_country_code=issuer_country_code,
        issuer_phone=issuer_phone,
        issuer_email=issuer_email,
        product_context=product_context,
    )


def _format_sdb_effective_date_for_input(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.day}.{parsed.month}.{parsed.year}"
        except ValueError:
            continue
    return text


def _normalize_sdb_effective_date_for_storage(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _append_sdb_protocol(protocol: list[dict] | None, step: str, outcome: str, details: str) -> list[dict]:
    items = list(protocol or [])
    items.insert(
        0,
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "step": step,
            "outcome": outcome,
            "details": details,
        },
    )
    return items[:20]


def _render_sdb_protocol(entries: list[dict] | None) -> html.Div:
    if not entries:
        return html.Div("Noch kein SDB-Protokoll vorhanden.", style={"color": "#64748b"})
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Strong(entry.get("step") or "-"),
                            html.Span(f" · {entry.get('outcome') or '-'}", style={"marginLeft": "6px"}),
                            html.Span(f" · {entry.get('timestamp') or '-'}", style={"marginLeft": "6px", "color": "#64748b", "fontSize": "12px"}),
                        ]
                    ),
                    html.Div(entry.get("details") or "-", style={"marginTop": "4px", "fontSize": "13px", "color": "#334155"}),
                ],
                style={"padding": "8px 0", "borderBottom": "1px solid #e2e8f0"},
            )
            for entry in entries
        ]
    )


def _normalize_signal_word_for_ui(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"gefahr", "danger"}:
        return "danger"
    if normalized in {"achtung", "warning"}:
        return "warning"
    return "none"


def _signal_word_for_legacy_storage(value: str | None) -> str | None:
    normalized = _normalize_signal_word_for_ui(value)
    if normalized == "danger":
        return "GEFAHR"
    if normalized == "warning":
        return "ACHTUNG"
    return None


def _build_chemical_safety_payload(
    ghs_pictograms: list[str] | None,
    signal_word: str | None,
    adr_pictograms: list[str] | None,
    hazard_class: str | None,
    environmentally_hazardous: bool | None = None,
) -> dict[str, object]:
    ghs_codes = [code for code in (ghs_pictograms or []) if code in GHS_SYMBOLS]
    adr_codes = [code for code in (adr_pictograms or []) if code in ADR_SYMBOLS]
    if environmentally_hazardous is True and "ADR_pollution" not in adr_codes:
        adr_codes.append("ADR_pollution")
    if environmentally_hazardous is False:
        adr_codes = [code for code in adr_codes if code != "ADR_pollution"]
    adr_class = (hazard_class or "").strip() or ("8" if "ADR_8" in adr_codes else None)
    return {
        "ghs_pictograms": ghs_codes,
        "signal_word": _normalize_signal_word_for_ui(signal_word),
        "adr_pictograms": adr_codes,
        "adr_class": adr_class,
        "environmentally_hazardous": bool(environmentally_hazardous),
    }


def _render_chemical_symbol_preview(ghs_pictograms: list[str] | None, adr_pictograms: list[str] | None, signal_word: str | None) -> html.Div:
    def symbol_items(symbols: dict[str, dict[str, str]], codes: list[str] | None) -> list[html.Div]:
        items: list[html.Div] = []
        for code in codes or []:
            definition = symbols.get(code)
            if not definition:
                continue
            items.append(
                html.Div(
                    [
                        html.Img(src=definition["src"], alt=definition["label_de"], style={"width": "58px", "height": "58px", "objectFit": "contain"}),
                        html.Div([html.Div(definition["label_de"], style={"fontWeight": "600"}), html.Div(code, style={"fontSize": "12px", "color": "#64748b"})]),
                    ],
                    style={"display": "flex", "gap": "8px", "alignItems": "center", "border": "1px solid #e2e8f0", "borderRadius": "8px", "padding": "8px"},
                )
            )
        return items

    ghs_items = symbol_items(GHS_SYMBOLS, ghs_pictograms)
    adr_items = symbol_items(ADR_SYMBOLS, adr_pictograms)
    if not ghs_items and not adr_items:
        return html.Div("Keine Piktogramme ausgewählt.", style={"color": "#64748b"})
    return html.Div(
        [
            html.Div(f"Signalwort: {_signal_word_for_legacy_storage(signal_word) or 'kein Signalwort'}", style={"fontWeight": "700", "marginBottom": "8px"}),
            html.Div([html.H5("GHS", style={"margin": "8px 0"}), *ghs_items]) if ghs_items else html.Div(),
            html.Div([html.H5("ADR", style={"margin": "12px 0 8px"}), *adr_items]) if adr_items else html.Div(),
        ]
    )


def _render_wgk_storage_meta(detail: dict | None) -> html.Div:
    if not detail:
        return html.Div("Keine Quelle gespeichert.", style={"color": "#64748b"})
    rows = [
        ("WGK Label", detail.get("wgk_label")),
        ("WGK Quelle", detail.get("wgk_source_url") or (f"Asset {detail.get('wgk_source_asset_id')}" if detail.get("wgk_source_asset_id") else None)),
        ("WGK SDB-Abschnitt", detail.get("wgk_source_section")),
        ("WGK Confidence", detail.get("wgk_confidence")),
        ("WGK zuletzt geprüft", detail.get("wgk_last_enriched_at")),
        ("Lagerklasse Label", detail.get("storage_class_label")),
        ("Lagerklasse Quelle", detail.get("storage_class_source_url") or (f"Asset {detail.get('storage_class_source_asset_id')}" if detail.get("storage_class_source_asset_id") else None)),
        ("Lagerklasse SDB-Abschnitt", detail.get("storage_class_source_section")),
        ("Lagerklasse Confidence", detail.get("storage_class_confidence")),
        ("Lagerklasse zuletzt geprüft", detail.get("storage_class_last_enriched_at")),
    ]
    return html.Div([html.Div(f"{label}: {value or '-'}") for label, value in rows], style={"fontSize": "13px", "color": "#334155"})


def _render_classification_proposal(proposals: dict | None) -> html.Div:
    if not proposals:
        return html.Div("Noch kein WGK-/Lagerklasse-Vorschlag vorhanden.", style={"color": "#64748b"})
    blocks: list[html.Div] = []
    for key, title in (("wgk", "WGK"), ("storage_class", "Lagerklasse")):
        proposal = proposals.get(key)
        if not proposal:
            blocks.append(html.Div(f"{title}: nicht gefunden", style={"color": "#64748b"}))
            continue
        blocks.append(
            html.Div(
                [
                    html.Strong(f"{title}: {proposal.get('value')} · {proposal.get('label')}"),
                    html.Div(f"Quelle: {proposal.get('source_url') or ('Asset ' + str(proposal.get('source_asset_id')) if proposal.get('source_asset_id') else '-')}"),
                    html.Div(f"SDB-Abschnitt: {proposal.get('source_section') or '-'} · Confidence: {proposal.get('confidence') or '-'}"),
                    html.Div(f"Ausschnitt: {proposal.get('excerpt') or '-'}", style={"marginTop": "4px"}),
                    html.Div("Würde vorhandenen manuellen Wert ersetzen - nur nach Klick auf Übernehmen.", style={"color": "#b45309"}) if proposal.get("would_overwrite") else html.Div(),
                ],
                style={"padding": "8px 0", "borderBottom": "1px solid #e2e8f0"},
            )
        )
    return html.Div([html.Div(proposals.get("message") or "", style={"fontWeight": "600", "marginBottom": "6px"}), *blocks])


def _asset_bulk_actions_style(selected_rows: list[dict] | None) -> dict[str, str]:
    count = len(selected_rows or [])
    if count <= 0:
        return {"display": "none"}
    return {
        "display": "flex",
        "justifyContent": "space-between",
        "alignItems": "center",
        "gap": "12px",
        "marginBottom": "12px",
    }


def _unique_rows_by_id(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        if row.get("id") is None:
            continue
        row_id = int(row["id"])
        if row_id in seen:
            continue
        seen.add(row_id)
        result.append(row)
    return result


def _render_r2_config_status(data: dict[str, object]) -> html.Div:
    enabled = "Ja" if data.get("enabled") else "Nein"
    last_status = str(data.get("last_test_status") or "noch nicht getestet")
    last_at = str(data.get("last_test_at") or "-")
    error = str(data.get("last_error_message") or "")
    rows = [
        html.Div([html.Strong("Aktiviert: "), html.Span(enabled)]),
        html.Div([html.Strong("Bucket: "), html.Span(str(data.get("bucket") or "-"))]),
        html.Div([html.Strong("Endpoint: "), html.Span(str(data.get("endpoint") or "-"))]),
        html.Div([html.Strong("Letzter Test: "), html.Span(last_status)]),
        html.Div([html.Strong("Testzeitpunkt: "), html.Span(last_at)]),
    ]
    if error:
        rows.append(html.Div([html.Strong("Letzter Fehler: "), html.Span(error)], style={"color": "#b91c1c"}))
    return html.Div(rows)


def _render_asset_uploader_selection_result(result: dict[str, object]) -> html.Div:
    items = list(result.get("items") or [])
    rows = [
        html.Tr(
            [
                html.Td(str(item.get("asset_id") or "-")),
                html.Td(str(item.get("filename") or "-")),
                html.Td(str(item.get("asset_type") or "-")),
                html.Td(str(item.get("product_id") or "-")),
                html.Td(str(item.get("product_title") or "-")),
                html.Td(str(item.get("status") or "-")),
                html.Td(str(item.get("message") or "-")),
            ]
        )
        for item in items
    ]
    return html.Div(
        [
            html.Div(
                f"Uploader-Übergabe: {result.get('uploaded_count', 0)} hochgeladen, "
                f"{result.get('skipped_count', 0)} übersprungen, {result.get('error_count', 0)} Fehler.",
                style={"fontWeight": "700", "marginBottom": "8px"},
            ),
            html.Table(
                [
                    html.Thead(
                        html.Tr(
                            [
                                html.Th("Asset-ID"),
                                html.Th("Datei"),
                                html.Th("Typ"),
                                html.Th("Produkt-ID"),
                                html.Th("Produkt"),
                                html.Th("Status"),
                                html.Th("Hinweis"),
                            ]
                        )
                    ),
                    html.Tbody(rows),
                ],
                className="summary-table",
            ),
        ]
    )


def _render_medusa_config_status(data: dict[str, object]) -> html.Div:
    return html.Div(
        [
            html.Div([html.Strong("Aktiviert: "), html.Span("Ja" if data.get("enabled") else "Nein")]),
            html.Div([html.Strong("Admin URL: "), html.Span(str(data.get("effective_admin_url") or "-"))]),
            html.Div([html.Strong("Token gespeichert: "), html.Span("Ja" if data.get("api_token_configured") else "Nein")]),
            html.Div([html.Strong("Default Locale: "), html.Span(str(data.get("default_locale") or "-"))]),
            html.Div([html.Strong("Locales: "), html.Span(str(data.get("enabled_locales") or "-"))]),
            html.Div([html.Strong("Letzter Test: "), html.Span(str(data.get("last_test_status") or "-"))]),
            html.Div([html.Strong("Testzeitpunkt: "), html.Span(str(data.get("last_test_at") or "-"))]),
            html.Div([html.Strong("Letzter Fehler: "), html.Span(str(data.get("last_error_message") or "-"))]),
        ]
    )


def grid(

    grid_id: str,
    columns: list[dict],
    row_data: list[dict] | None = None,
    height: str = "320px",
    row_selection: str = "single",
    extra_grid_options: dict | None = None,
) -> dag.AgGrid:
    selection_mode = "singleRow" if row_selection == "single" else "multiRow"
    grid_options = {"pagination": True, "paginationPageSize": 20, "rowSelection": {"mode": selection_mode}}
    if extra_grid_options:
        grid_options.update(extra_grid_options)
    return dag.AgGrid(
        id=grid_id,
        rowData=row_data or [],
        columnDefs=columns,
        defaultColDef={"resizable": True, "sortable": True, "filter": True, "editable": False},
        dashGridOptions=grid_options,
        style={"height": height},
    )


def _selected_dedupe_group_id(selected_rows: list[dict] | None) -> int | None:
    if not selected_rows:
        return None
    value = selected_rows[0].get("id")
    return int(value) if value not in (None, "") else None


def _final_url_description_products(
    session: Session,
    scope: str,
    product_id: int | None,
    selected_product_ids: list[int] | None,
) -> list[Product]:
    if scope == "single":
        if not product_id:
            return []
        product = session.scalar(select(Product).options(selectinload(Product.translations)).where(Product.id == int(product_id)))
        return [product] if product else []
    if scope == "all":
        return load_final_url_products(session)
    ids = [int(value) for value in (selected_product_ids or []) if value not in (None, "")]
    if not ids:
        return []
    return list(
        session.scalars(
            select(Product)
            .options(selectinload(Product.translations))
            .where(Product.id.in_(ids))
            .order_by(Product.id.asc())
        ).unique()
    )


def _selected_dedupe_product_id(selected_rows: list[dict] | None) -> int | None:
    if not selected_rows:
        return None
    value = selected_rows[0].get("product_id")
    return int(value) if value not in (None, "") else None


def _dedupe_item_product_ids(rows: list[dict] | None) -> set[int]:
    ids: set[int] = set()
    for row in rows or []:
        value = row.get("product_id")
        if value in (None, ""):
            continue
        ids.add(int(value))
    return ids


def _dedupe_group_selection_state(row_data: list[dict] | None, selected_rows: list[dict] | None) -> str:
    group_ids = _dedupe_item_product_ids(row_data)
    if not group_ids:
        return "empty"
    selected_ids = _dedupe_item_product_ids(selected_rows)
    selected_in_group = group_ids & selected_ids
    if not selected_in_group:
        return "none"
    if selected_in_group == group_ids:
        return "all"
    return "partial"


def _dedupe_select_group_rows(row_data: list[dict] | None, selected_rows: list[dict] | None, *, selected: bool) -> list[dict]:
    group_rows = [row for row in row_data or [] if row.get("product_id") not in (None, "")]
    group_ids = _dedupe_item_product_ids(group_rows)
    if selected:
        return _unique_rows_by_product_id([*(selected_rows or []), *group_rows])
    return [row for row in (selected_rows or []) if row.get("product_id") not in group_ids]


def _unique_rows_by_product_id(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        value = row.get("product_id")
        if value in (None, ""):
            continue
        product_id = int(value)
        if product_id in seen:
            continue
        seen.add(product_id)
        result.append(row)
    return result


def _dedupe_group_selection_status(row_data: list[dict] | None, selected_rows: list[dict] | None) -> str:
    group_count = len(_dedupe_item_product_ids(row_data))
    selected_count = len(_dedupe_item_product_ids(row_data) & _dedupe_item_product_ids(selected_rows))
    state = _dedupe_group_selection_state(row_data, selected_rows)
    if state == "empty":
        return "Keine Produkte in dieser Gruppe."
    if state == "all":
        return f"Alle {group_count} Produkte dieser Gruppe sind markiert."
    if state == "partial":
        return f"Teilweise markiert: {selected_count} von {group_count} Produkten dieser Gruppe."
    return f"Keine Produkte dieser Gruppe markiert ({group_count} verfügbar)."


def _render_dedupe_master(master: dict) -> html.Div:
    if not master:
        return html.Div("Kein Master geladen.")
    rows = [
        ("Produkt-ID", master.get("product_id")),
        ("Titel", master.get("title")),
        ("SKU", master.get("sku")),
        ("Status", master.get("status")),
        ("Marke", master.get("brand")),
        ("Family Key", master.get("family_key")),
        ("Varianten", master.get("variant_count")),
        ("Assets", master.get("asset_count")),
        ("VK/EK", f"{master.get('sale_price_count', 0)} / {master.get('cost_price_count', 0)}"),
        ("Aktualisiert", master.get("updated_at")),
    ]
    return html.Div([html.Div([html.Strong(f"{label}: "), html.Span("" if value is None else str(value))]) for label, value in rows])


def _render_dedupe_preview(preview: dict | list | None) -> html.Div:
    if not isinstance(preview, dict):
        return html.Div("Noch keine gespeicherte Vorschau. Erst „Dry-Run / Vorschau erstellen“ ausführen.")
    return html.Div(
        [
            html.Div([html.Strong("Master: "), html.Span(str(preview.get("master_product_id") or ""))]),
            html.Div([html.Strong("Dubletten würden archiviert: "), html.Span(", ".join(str(row) for row in preview.get("duplicate_product_ids", [])))]),
            html.Div([html.Strong("Assets übernehmen: "), html.Span(str(preview.get("merged_assets_count", 0)))]),
            html.Div([html.Strong("Varianten übernehmen: "), html.Span(str(preview.get("merged_variants_count", 0)))]),
            html.Div([html.Strong("Preise übernehmen: "), html.Span(str(preview.get("merged_prices_count", 0)))]),
            html.Div([html.Strong("Konflikte: "), html.Span(str(preview.get("conflicts_count", 0)))]),
        ]
    )


def _product_enrichment_suggestion_rows(result: dict | None) -> list[dict]:
    rows: list[dict] = []
    for product_result in (result or {}).get("results", []):
        for suggestion in product_result.get("suggestions", []):
            row = dict(suggestion)
            confidence = row.get("confidence")
            row["confidence"] = f"{float(confidence):.2f}" if confidence not in (None, "") else ""
            rows.append(row)
    return rows


def _product_enrichment_warnings(result: dict | None) -> html.Div:
    warnings: list[str] = []
    details: list[str] = []
    for product_result in (result or {}).get("results", []):
        label = f"{product_result.get('product_id')} · {product_result.get('sku')}"
        for source in product_result.get("sources_checked", []):
            if source.get("status") == "search_hint":
                details.append(f"{label}: Suchhinweis: {source.get('search_query')}")
        for warning in product_result.get("warnings", []):
            if str(warning).startswith("Kein sicherer Vorschlag"):
                details.append(f"{label}: {warning}")
            else:
                warnings.append(f"{label}: {warning}")
        for suggestion in product_result.get("suggestions", []):
            field_name = str(suggestion.get("field_name") or "")
            status = str(suggestion.get("status") or "")
            warning = str(suggestion.get("warning") or "").strip()
            if status == "candidate_only" or field_name not in SUPPORTED_FIELDS:
                if warning:
                    details.append(f"{label}: {field_name}: {warning}")
                continue
            if warning:
                warnings.append(f"{label}: {field_name}: {warning}")
            if status == "needs_translation":
                warnings.append(f"{label}: {field_name}: Quelle {suggestion.get('source_language') or '-'} muss zuerst nach {suggestion.get('target_locale') or '-'} übersetzt werden.")
        for error in product_result.get("errors", []):
            warnings.append(f"{label}: Fehler: {error}")
    children: list[object] = []
    if warnings:
        children.append(html.Div([html.Div(line) for line in warnings], className="product-enrichment-warning-list"))
    else:
        children.append(html.Div("Keine kritischen Warnungen."))
    if details:
        children.append(
            html.Details(
                [
                    html.Summary(f"Suchhinweise und technische Details anzeigen ({len(details)})"),
                    html.Div([html.Div(line) for line in details], className="product-enrichment-technical-details"),
                ],
                className="product-enrichment-details",
            )
        )
    return html.Div(children)


def _parse_product_ids(raw_value: str | None, selected_product_ids: list[int] | None) -> list[int]:
    explicit_ids = [
        int(match)
        for match in re.findall(r"\d+", str(raw_value or ""))
    ]
    if explicit_ids:
        return list(dict.fromkeys(explicit_ids))
    return [int(product_id) for product_id in (selected_product_ids or [])]


def _rules_enrichment_fields(action: str | None, selected_fields: list[str] | None) -> list[str]:
    action_fields = {
        "short_description": ["short_description"],
        "description": ["description"],
        "seo_title": ["seo_title"],
        "seo_description": ["seo_description"],
        "slug": ["slug"],
        "technical": ["technical_features_text", "specifications_text"],
        "missing_texts": ["short_description", "description", "seo_title", "seo_description"],
    }
    if action in action_fields:
        return action_fields[action]
    return [field for field in (selected_fields or []) if field in SUPPORTED_FIELDS] or ["short_description", "description", "seo_title", "seo_description"]


def _rules_enrichment_should_preview_existing(action: str | None, overwrite_existing: bool | None) -> bool:
    if overwrite_existing:
        return True
    # Explizite Suchaktionen sollen eine Vergleichsvorschau liefern, auch wenn das Feld schon gefuellt ist.
    return action in {"short_description", "description", "seo_title", "seo_description", "technical", "preview"}


def _filter_detail_variants(variants: list[dict], archive_filter: str | None) -> list[dict]:
    normalized_filter = (archive_filter or "active").strip().lower()
    if normalized_filter in {"all", "alle"}:
        return variants
    archived_statuses = {"archived", "archiviert"}
    if normalized_filter in {"archived", "archiviert"}:
        return [variant for variant in variants if str(variant.get("status") or "").strip().lower() in archived_statuses]
    return [variant for variant in variants if str(variant.get("status") or "").strip().lower() not in archived_statuses]


def create_dash_app() -> Dash:
    app = Dash(__name__, title="PIM/PAM Admin", assets_folder=str(Path(__file__).resolve().parents[1] / "assets"))

    @app.server.get("/upload-ui")
    def upload_ui_redirect():
        host = request.host.split(":", 1)[0]
        return redirect(f"{request.scheme}://{host}:8000", code=302)

    @app.server.get("/asset-file/<int:asset_id>")
    def serve_asset_file(asset_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            asset = session.get(Asset, asset_id)
            if asset is None:
                abort(404)
            if asset.storage_provider in {"cloudflare_r2", "bunny_storage"}:
                public_url = asset.public_url or safe_r2_public_url(asset.object_key)
                if not public_url and asset.object_key:
                    try:
                        public_url = build_r2_storage(session).public_url(asset.object_key)
                    except Exception:
                        public_url = None
                if public_url:
                    return redirect(public_url, code=302)
                object_key = asset.object_key or object_key_from_storage_path(asset.storage_path)
                if not object_key:
                    abort(404)
                try:
                    return redirect(build_r2_storage(session).generate_presigned_download_url(object_key), code=302)
                except Exception:
                    abort(404)
            path = Path(asset.storage_path)
            if not path.exists():
                abort(404)
            return send_file(path, mimetype=asset.mime_type, download_name=asset.original_filename, conditional=True)

    @app.server.get("/asset-thumb/<int:asset_id>")
    def serve_asset_thumbnail(asset_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            asset = session.get(Asset, asset_id)
            if asset is None or not str(asset.mime_type or "").lower().startswith("image/"):
                abort(404)
            path = Path(asset.storage_path)
            if not path.exists():
                abort(404)
            thumb_dir = get_pim_settings().asset_storage_root / "_thumbs"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = thumb_dir / f"{asset.id}.jpg"
            try:
                source_mtime = path.stat().st_mtime
                if not thumb_path.exists() or thumb_path.stat().st_mtime < source_mtime:
                    with Image.open(path) as image:
                        image.thumbnail((96, 96))
                        if image.mode not in {"RGB", "L"}:
                            image = image.convert("RGB")
                        image.save(thumb_path, format="JPEG", quality=78, optimize=True)
                response = send_file(thumb_path, mimetype="image/jpeg", conditional=True, max_age=86400)
                response.headers["Cache-Control"] = "public, max-age=86400"
                return response
            except Exception:
                abort(404)

    @app.server.get("/chem/<path:filename>")
    def serve_chemical_symbol(filename: str):
        return send_from_directory(Path(__file__).resolve().parents[2] / "public" / "chem", filename)

    @app.server.get("/api/languages")
    def api_languages():
        with session_scope(get_pim_settings().database_url) as session:
            return jsonify(list_languages(session))

    @app.server.get("/api/translation-prompts")
    def api_translation_prompts():
        with session_scope(get_pim_settings().database_url) as session:
            return jsonify(list_translation_prompts(session))

    @app.server.put("/api/translation-prompts/<language_code>")
    def api_save_translation_prompt(language_code: str):
        payload = request.get_json(silent=True) or {}
        with session_scope(get_pim_settings().database_url) as session:
            prompt = save_translation_prompt(
                session,
                language_code,
                str(payload.get("promptTemplate") or payload.get("prompt_template") or ""),
                str(payload.get("systemPrompt") or payload.get("system_prompt") or ""),
            )
            return jsonify({"language_code": prompt.language_code, "updated": True})

    @app.server.post("/api/products/translations/generate")
    def api_generate_product_translations():
        payload = request.get_json(silent=True) or {}
        with session_scope(get_pim_settings().database_url) as session:
            result = generate_product_translations(
                session,
                [int(item) for item in (payload.get("productIds") or payload.get("product_ids") or [])],
                list(payload.get("targetLanguages") or payload.get("target_languages") or []),
                source_language_code=payload.get("sourceLanguageCode") or payload.get("source_language_code"),
                overwrite_existing=bool(payload.get("overwriteExisting") or payload.get("overwrite_existing")),
                allow_original_overwrite=bool(payload.get("allowOriginalOverwrite") or payload.get("allow_original_overwrite")),
            )
            return jsonify(result)

    @app.server.get("/api/products/<int:product_id>/translations")
    def api_product_translations(product_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            detail = get_product_detail(session, product_id)
            if detail is None:
                abort(404)
            return jsonify(detail.get("translations", []))

    @app.server.get("/api/products/<int:product_id>/sdb-documents")
    def api_product_sdb_documents(product_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            if get_product_detail(session, product_id) is None:
                abort(404)
            return jsonify(list_sdb_documents_for_product(session, product_id))

    @app.server.get("/api/sdb-translation-prompts")
    def api_sdb_translation_prompts():
        with session_scope(get_pim_settings().database_url) as session:
            return jsonify(list_sdb_translation_prompts(session))

    @app.server.get("/api/chemical-documents/<int:document_id>")
    def api_chemical_document_detail(document_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            try:
                return jsonify(get_chemical_document_detail(session, document_id))
            except ValueError:
                abort(404)

    @app.server.get("/chemical-document-pdf/<int:document_id>")
    def serve_chemical_document_pdf(document_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            try:
                result = render_chemical_document_pdf(session, document_id)
                path = Path(str(result.get("pdf_path") or ""))
                title = str(result.get("title") or f"sdb-document-{document_id}").strip() or f"sdb-document-{document_id}"
            except ValueError:
                abort(404)
            if not path.exists():
                abort(404)
            filename = f"{title[:80].replace('/', '-')}.pdf"
            return send_file(path, mimetype="application/pdf", download_name=filename, conditional=True)

    @app.server.put("/api/chemical-documents/<int:document_id>")
    def api_update_chemical_document(document_id: int):
        payload = request.get_json(silent=True) or {}
        with session_scope(get_pim_settings().database_url) as session:
            try:
                return jsonify(update_chemical_document_text(session, document_id, title=payload.get("title"), text=payload.get("text")))
            except ValueError:
                abort(404)

    @app.server.post("/api/sdb-translation-prompts")
    def api_save_sdb_translation_prompt():
        payload = request.get_json(silent=True) or {}
        with session_scope(get_pim_settings().database_url) as session:
            prompt = save_sdb_translation_prompt(
                session,
                prompt_id=payload.get("id"),
                name=str(payload.get("name") or "SDB-Prompt"),
                document_type=str(payload.get("documentType") or payload.get("document_type") or "sds"),
                source_locale=payload.get("sourceLocale") or payload.get("source_locale"),
                target_locale=payload.get("targetLocale") or payload.get("target_locale"),
                target_region=payload.get("targetRegion") or payload.get("target_region"),
                system_prompt=payload.get("systemPrompt") or payload.get("system_prompt"),
                user_prompt_template=payload.get("userPromptTemplate") or payload.get("user_prompt_template"),
                active=bool(payload.get("active", True)),
            )
            return jsonify({"id": prompt.id, "updated": True})

    @app.server.post("/api/products/<int:product_id>/sdb-translations/generate")
    def api_generate_product_sdb_translation(product_id: int):
        payload = request.get_json(silent=True) or {}
        with session_scope(get_pim_settings().database_url) as session:
            result = generate_sdb_translation_draft(
                session,
                product_id=product_id,
                source_document_id=int(payload.get("sourceDocumentId") or payload.get("source_document_id") or 0),
                source_locale=payload.get("sourceLocale") or payload.get("source_locale"),
                target_locale=str(payload.get("targetLocale") or payload.get("target_locale") or ""),
                target_region=str(payload.get("targetRegion") or payload.get("target_region") or ""),
                prompt_id=payload.get("promptId") or payload.get("prompt_id"),
            )
            return jsonify(result)

    @app.server.get("/chemical-sdb-pdf/<int:product_id>")
    def serve_chemical_sdb_pdf(product_id: int):
        with session_scope(get_pim_settings().database_url) as session:
            detail = get_product_detail(session, product_id)
            if detail is None:
                abort(404)
            sdb_data = detail.get("sdb") or {}
            pdf_path = sdb_data.get("generated_pdf_path")
            if not pdf_path:
                abort(404)
            path = Path(str(pdf_path))
            if not path.exists():
                abort(404)
            return send_file(path, mimetype="application/pdf", download_name=f"{detail.get('sku') or product_id}-sdb.pdf", conditional=True)

    @app.server.get("/channel-export-file/<path:filename>")
    def serve_channel_export_file(filename: str):
        export_root = (get_pim_settings().asset_storage_root.parent / "channel_exports").resolve()
        path = (export_root / filename).resolve()
        if export_root not in path.parents or not path.exists():
            abort(404)
        return send_file(path, mimetype="text/csv", download_name=path.name, conditional=True)

    app.layout = html.Div(
        [
            dcc.Store(id="refresh-token", data=0),
            dcc.Store(id="chemistry-sdb-refresh-token", data=0),
            dcc.Store(id="chemistry-sdb-protocol-store", data=[]),
            dcc.Store(id="chemistry-classification-proposal-store", data={}),
            dcc.Store(id="snapshot-store"),
            dcc.Store(id="selected-product-ids", data=[]),
            dcc.Store(id="product-focus-id"),
            dcc.Store(id="last-product-clicked-id"),
            dcc.Store(id="last-product-click-event"),
            dcc.Store(id="active-product-row"),
            dcc.Store(id="selected-variant-ids", data=[]),
            dcc.Store(id="variant-focus-product-id"),
            dcc.Store(id="sidebar-collapsed-store", data=False),
            dcc.Store(id="category-tree-collapsed-store", data=[]),
            dcc.Store(id="channel-category-tree-collapsed-store", data=[]),
            dcc.Store(id="selected-category-id"),
            dcc.Store(id="selected-channel-category-id"),
            dcc.Store(id="selected-chemical-product-id"),
            dcc.Store(id="selected-asset-ids", data=[]),
            dcc.Store(id="channel-bulk-action-context", data={}),
            dcc.Store(id="product-bulk-edit-context", data={}),
            dcc.Store(id="variant-bulk-edit-context", data={}),
            dcc.Store(id="translation-bulk-context", data={}),
            dcc.Store(id="product-data-enrichment-results", data={}),
            dcc.Store(id="product-text-enrichment-results", data={}),
            dcc.Store(id="rules-product-enrichment-results", data={}),
            dcc.Store(id="pim-import-job-store"),
            dcc.Store(id="dedupe-refresh-token", data=0),
            dcc.Interval(id="global-process-status-poll", interval=3000, n_intervals=0),
            dcc.Interval(id="pim-import-job-poll", interval=1500, n_intervals=0, disabled=True),
            html.Div(
                [
                    html.H1("PIM/PAM Admin", className="page-title"),
                    html.Div(
                        [
                            html.Button("Dashboard", id="open-dashboard-button"),
                            html.Button("Daten neu laden", id="refresh-button"),
                            html.A("ETL Upload UI", href="/upload-ui", target="_blank", className="toolbar-link-button"),
                        ],
                        className="toolbar",
                    ),
                ],
                className="page-header",
            ),
            html.Div(id="flash-message", className="flash"),
            html.Div(id="global-process-status"),
            dcc.ConfirmDialog(id="channel-bulk-confirm"),
            dcc.ConfirmDialog(id="product-bulk-edit-confirm"),
            dcc.ConfirmDialog(id="variant-bulk-edit-confirm"),
            dcc.ConfirmDialog(id="dedupe-merge-confirm"),
            dcc.ConfirmDialog(
                id="variant-delete-confirm",
                message=(
                    "Variante löschen? Diese Aktion kann Auswirkungen auf Preise, Assets, Kanal-Listings und externe Systeme haben. "
                    "Wenn die Variante bereits verwendet wird, wird sie nicht endgültig gelöscht, sondern archiviert."
                ),
            ),
            dcc.ConfirmDialog(
                id="product-detail-variant-delete-confirm",
                message=(
                    "Variante aus diesem Produkt löschen? Wenn Preise, Assets, Listings oder andere abhängige Daten vorhanden sind, "
                    "wird die Variante sicher archiviert statt hart gelöscht."
                ),
            ),
            html.Div(
                id="channel-bulk-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Kanal-Aktion", style={"margin": "0"}),
                                    html.Button("Schließen", id="channel-bulk-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(id="channel-bulk-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div([html.Label("Aktion"), dcc.Dropdown(id="channel-bulk-action", clearable=False)]),
                                    html.Div([html.Label("Vertriebskanal"), dcc.Dropdown(id="channel-bulk-sales-channel-id", clearable=False)]),
                                    html.Div([html.Label("Kanal-Kategorie"), dcc.Dropdown(id="channel-bulk-channel-category-id", placeholder="Optional")]),
                                    html.Div([html.Label("Erlaubt"), dcc.Dropdown(id="channel-bulk-allowed", options=BOOLEAN_OPTIONS, value=True, clearable=False)]),
                                    html.Div([html.Label("Aktiv"), dcc.Dropdown(id="channel-bulk-is-active", options=BOOLEAN_OPTIONS, value=True, clearable=False)]),
                                    html.Div([html.Label("Publikationsstatus"), dcc.Dropdown(id="channel-bulk-publication-status", options=PUBLICATION_STATUS_OPTIONS, value="published", clearable=False)]),
                                    html.Div([html.Label("Aktiv ab"), dcc.Input(id="channel-bulk-active-from", placeholder="YYYY-MM-DD oder leer")]),
                                    html.Div([html.Label("Aktiv bis"), dcc.Input(id="channel-bulk-active-until", placeholder="YYYY-MM-DD oder leer")]),
                                ],
                                className="form-grid",
                            ),
                            html.Div([html.Button("Ausführen", id="channel-bulk-run-button")], className="button-row", style={"marginTop": "12px"}),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(980px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                id="product-bulk-edit-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Markierte Produkte bearbeiten", style={"margin": "0"}),
                                    html.Button("Schließen", id="product-bulk-edit-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(id="product-bulk-edit-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label("Zu ändernde Felder"),
                                            dcc.Checklist(
                                                id="product-bulk-edit-fields",
                                                options=[
                                                    {"label": "Originalsprache", "value": "source_language"},
                                                    {"label": "Marke / Brand", "value": "brand_name"},
                                                    {"label": "Status", "value": "status"},
                                                    {"label": "Chemieprodukt", "value": "is_chemical"},
                                                ],
                                                value=[],
                                            ),
                                        ]
                                    ),
                                    html.Div([html.Label("Originalsprache"), dcc.Dropdown(id="product-bulk-edit-source-language", options=LANGUAGE_CODE_OPTIONS, value="de-CH", clearable=False)]),
                                    html.Div([html.Label("Marke / Brand"), dcc.Input(id="product-bulk-edit-brand", placeholder="z. B. Tintolav")]),
                                    html.Div([html.Label("Status"), dcc.Dropdown(id="product-bulk-edit-status", options=PRODUCT_BULK_STATUS_OPTIONS, value="active", clearable=False)]),
                                    html.Div([html.Label("Chemieprodukt"), dcc.Dropdown(id="product-bulk-edit-is-chemical", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                    html.Div(
                                        [
                                            html.Label("Schreibmodus"),
                                            dcc.Checklist(
                                                id="product-bulk-edit-options",
                                                options=[{"label": "Nur leere Werte füllen", "value": "only_empty"}],
                                                value=[],
                                            ),
                                        ]
                                    ),
                                ],
                                className="form-grid",
                            ),
                            html.Div(
                                [
                                    html.Button("Vorschau erzeugen", id="product-bulk-edit-preview-button"),
                                    html.Button("Änderungen anwenden", id="product-bulk-edit-apply-button"),
                                ],
                                className="button-row",
                                style={"marginTop": "12px"},
                            ),
                            html.Div("Nur angehakte Felder werden geändert. Apply erzeugt vorher ein JSON-Backup.", className="form-hint"),
                            html.Div(id="product-bulk-edit-result", className="selection-summary", style={"marginTop": "10px"}),
                            grid("product-bulk-edit-preview-grid", BULK_EDIT_PREVIEW_COLUMNS, height="320px"),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(1100px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                id="variant-bulk-edit-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Markierte Varianten bearbeiten", style={"margin": "0"}),
                                    html.Button("Schließen", id="variant-bulk-edit-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(id="variant-bulk-edit-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label("Zu ändernde Felder"),
                                            dcc.Checklist(
                                                id="variant-bulk-edit-fields",
                                                options=[
                                                    {"label": "Status", "value": "status"},
                                                    {"label": "Verkaufspreis", "value": "price"},
                                                    {"label": "Verkaufswährung", "value": "currency"},
                                                    {"label": "Einkaufspreis", "value": "cost_price"},
                                                    {"label": "Einkaufswährung", "value": "cost_currency"},
                                                    {"label": "Lagerbestand", "value": "stock_qty"},
                                                    {"label": "Barcode / EAN", "value": "barcode"},
                                                    {"label": "Optionsname", "value": "option_name"},
                                                    {"label": "Optionswert", "value": "option_value"},
                                                    {"label": "Packaging / Gebinde", "value": "packaging"},
                                                ],
                                                value=[],
                                            ),
                                        ]
                                    ),
                                    html.Div([html.Label("Status"), dcc.Dropdown(id="variant-bulk-edit-status", options=[{"label": value, "value": value} for value in ["active", "inactive", "archived"]], value="active", clearable=False)]),
                                    html.Div([html.Label("Verkaufspreis"), dcc.Input(id="variant-bulk-edit-price", type="number", step=0.01)]),
                                    html.Div([html.Label("Verkaufswährung"), dcc.Input(id="variant-bulk-edit-currency", placeholder="CHF / EUR")]),
                                    html.Div([html.Label("Einkaufspreis"), dcc.Input(id="variant-bulk-edit-cost-price", type="number", step=0.01)]),
                                    html.Div([html.Label("Einkaufswährung"), dcc.Input(id="variant-bulk-edit-cost-currency", placeholder="CHF / EUR")]),
                                    html.Div([html.Label("Lagerbestand"), dcc.Input(id="variant-bulk-edit-stock-qty", type="number", step=1)]),
                                    html.Div([html.Label("Barcode / EAN"), dcc.Input(id="variant-bulk-edit-barcode", placeholder="Barcode")]),
                                    html.Div([html.Label("Optionsname"), dcc.Input(id="variant-bulk-edit-option-name", placeholder="z. B. Packaging")]),
                                    html.Div([html.Label("Optionswert"), dcc.Input(id="variant-bulk-edit-option-value", placeholder="z. B. 10 kg")]),
                                    html.Div([html.Label("Packaging / Gebinde"), dcc.Input(id="variant-bulk-edit-packaging", placeholder="z. B. 10 kg Kanister")]),
                                    html.Div(
                                        [
                                            html.Label("Schreibmodus"),
                                            dcc.Checklist(
                                                id="variant-bulk-edit-options",
                                                options=[{"label": "Nur leere Werte füllen", "value": "only_empty"}],
                                                value=[],
                                            ),
                                        ]
                                    ),
                                ],
                                className="form-grid",
                            ),
                            html.Div(
                                [
                                    html.Button("Vorschau erzeugen", id="variant-bulk-edit-preview-button"),
                                    html.Button("Änderungen anwenden", id="variant-bulk-edit-apply-button"),
                                ],
                                className="button-row",
                                style={"marginTop": "12px"},
                            ),
                            html.Div("Nur angehakte Felder werden geändert. Apply erzeugt vorher ein JSON-Backup.", className="form-hint"),
                            html.Div(id="variant-bulk-edit-result", className="selection-summary", style={"marginTop": "10px"}),
                            grid("variant-bulk-edit-preview-grid", BULK_EDIT_PREVIEW_COLUMNS, height="320px"),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(1100px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                id="translation-bulk-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Übersetzungen erstellen", style={"margin": "0"}),
                                    html.Button("Schließen", id="translation-bulk-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(id="translation-bulk-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div([html.Label("Ausgangssprache"), dcc.Dropdown(id="translation-source-language", clearable=False)]),
                                    html.Div([html.Label("Zielsprache(n)"), dcc.Dropdown(id="translation-target-languages", multi=True)]),
                                    html.Div([html.Label("Bestehende Übersetzungen überschreiben"), dcc.Dropdown(id="translation-overwrite-existing", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                    html.Div([html.Label("Originalsprache überschreiben"), dcc.Dropdown(id="translation-overwrite-original", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                    html.Div([html.Label("Zugehörige Varianten mitübersetzen"), dcc.Dropdown(id="translation-include-variants", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                ],
                                className="form-grid",
                            ),
                            html.Div(id="translation-provider-status", className="selection-summary", style={"marginTop": "12px"}),
                            html.Div([html.Button("Übersetzungen generieren", id="translation-generate-button")], className="button-row", style={"marginTop": "12px"}),
                            html.Div("Status: bereit. Während der Generierung wird dieser Bereich aktualisiert.", className="selection-summary", style={"marginTop": "12px"}),
                            dcc.Loading(
                                html.Div(id="translation-result", className="selection-summary", style={"marginTop": "12px"}),
                                type="default",
                            ),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(980px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                id="translation-prompt-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Übersetzungs-Prompts verwalten", style={"margin": "0"}),
                                    html.Button("Schließen", id="translation-prompt-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(
                                [
                                    html.Div([html.Label("Sprache"), dcc.Dropdown(id="translation-prompt-language", clearable=False)]),
                                    html.Div([html.Label("System Prompt"), dcc.Textarea(id="translation-prompt-system", style={"width": "100%", "height": "90px"})]),
                                    html.Div([html.Label("Prompt Template"), dcc.Textarea(id="translation-prompt-template", style={"width": "100%", "height": "300px"})]),
                                ],
                                className="form-grid",
                                style={"gridTemplateColumns": "1fr"},
                            ),
                            html.Div(
                                [
                                    html.Button("Prompt speichern", id="translation-prompt-save-button"),
                                    html.Button("Auf Standard zurücksetzen", id="translation-prompt-reset-button"),
                                ],
                                className="button-row",
                                style={"marginTop": "12px"},
                            ),
                            html.Div(id="translation-prompt-status", className="selection-summary", style={"marginTop": "12px"}),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(980px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                id="product-data-enrichment-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H3("Produktdaten anreichern", style={"margin": "0"}),
                                            html.P(
                                                "Ergänzt Produktinformationen, Beschreibungen, technische Daten und Assets aus bestehenden Source- oder Final-URLs.",
                                                className="unified-enrichment-subtitle",
                                            ),
                                        ]
                                    ),
                                    html.Button("Schließen", id="product-data-enrichment-close-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "flex-start", "gap": "16px", "marginBottom": "16px"},
                            ),
                            html.Div(id="product-data-enrichment-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H4("1. Produktdaten ergänzen", className="unified-enrichment-title"),
                                            html.P("Sucht Vorschläge für Texte, SEO-Felder, Slug und technische Produktfelder. Erst Preview erzeugen, dann bewusst übernehmen.", className="form-hint"),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Felder"),
                                                            dcc.Checklist(
                                                                id="product-data-enrichment-fields",
                                                                options=[
                                                                    {"label": "Titel", "value": "title"},
                                                                    {"label": "Kurzbeschreibung", "value": "short_description"},
                                                                    {"label": "Beschreibung", "value": "description"},
                                                                    {"label": "SEO-Titel", "value": "seo_title"},
                                                                    {"label": "SEO-Beschreibung", "value": "seo_description"},
                                                                    {"label": "Slug / Handle", "value": "slug"},
                                                                ],
                                                                value=["short_description", "description", "seo_title", "seo_description"],
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Quellen"),
                                                            dcc.Checklist(
                                                                id="product-data-enrichment-sources",
                                                                options=[
                                                                    {"label": "Final URL verwenden", "value": "final_url"},
                                                                    {"label": "Source-URL verwenden", "value": "source_url"},
                                                                    {"label": "bekannte Domains als Suchhinweis", "value": "configured_domains"},
                                                                ],
                                                                value=["final_url", "source_url", "configured_domains"],
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Bestehende Werte überschreiben"),
                                                            dcc.Dropdown(id="product-data-enrichment-overwrite", options=BOOLEAN_OPTIONS, value=False, clearable=False),
                                                        ]
                                                    ),
                                                ],
                                                className="form-grid unified-enrichment-grid",
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("Dry-Run / Preview erzeugen", id="product-data-enrichment-preview-button", className="crawler-secondary-button"),
                                                    html.Button("Ausgewählte übernehmen", id="product-data-enrichment-apply-selected-button", className="crawler-primary-button"),
                                                    html.Button("Alle Vorschläge übernehmen", id="product-data-enrichment-apply-all-button", className="crawler-secondary-button"),
                                                ],
                                                className="button-row",
                                                style={"marginTop": "12px"},
                                            ),
                                            dcc.Loading(html.Div(id="product-data-enrichment-status", className="selection-summary", style={"marginTop": "12px"}), type="default"),
                                            grid("product-data-enrichment-suggestions-grid", PRODUCT_ENRICHMENT_SUGGESTION_COLUMNS, height="330px", row_selection="multiple"),
                                            html.Div(id="product-data-enrichment-warnings", className="selection-summary", style={"marginTop": "12px"}),
                                        ],
                                        className="unified-enrichment-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("2. Produkt-Assets holen", className="unified-enrichment-title"),
                                            html.P("Sucht fehlende Bilder, PDFs, SDB/SDS und Datenblätter für die markierten Produkte. Bestehende Assets werden nicht überschrieben.", className="form-hint"),
                                            html.Button("Fehlende Produkt-Assets anreichern", id="product-asset-enrichment-run-button", className="crawler-primary-button"),
                                            dcc.Loading(html.Div(id="product-asset-enrichment-status", className="selection-summary", style={"marginTop": "12px"}), type="default"),
                                        ],
                                        className="unified-enrichment-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("3. Beschreibungen aus Final URLs importieren", className="unified-enrichment-title"),
                                            html.P("Liest vorhandene Final URLs aus, extrahiert Beschreibung und Kurzbeschreibung und formatiert Langtexte als Markdown. Standard ist Dry-Run.", className="form-hint"),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Produktauswahl"),
                                                            dcc.RadioItems(
                                                                id="product-final-url-description-scope",
                                                                options=[
                                                                    {"label": "Markierte Produkte", "value": "selected"},
                                                                    {"label": "Einzelne Produkt-ID testen", "value": "single"},
                                                                    {"label": "Alle Produkte mit Final URL prüfen", "value": "all"},
                                                                ],
                                                                value="selected",
                                                                inline=True,
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Produkt-ID"),
                                                            dcc.Input(id="product-final-url-description-product-id", type="number", placeholder="z. B. 1294"),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Modus"),
                                                            dcc.RadioItems(
                                                                id="product-final-url-description-run-mode",
                                                                options=[
                                                                    {"label": "Dry-Run", "value": "dry_run"},
                                                                    {"label": "Apply ausführen", "value": "apply"},
                                                                ],
                                                                value="dry_run",
                                                                inline=True,
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Schreiboptionen"),
                                                            dcc.Checklist(
                                                                id="product-final-url-description-options",
                                                                options=[
                                                                    {"label": "Bestehende Texte überschreiben", "value": "overwrite"},
                                                                ],
                                                                value=[],
                                                            ),
                                                        ]
                                                    ),
                                                ],
                                                className="form-grid unified-enrichment-grid",
                                            ),
                                            html.Div("Ohne Apply werden keine Daten geschrieben. Apply erzeugt vorher ein Backup.", className="form-hint"),
                                            html.Button("Beschreibungen aus Final URLs importieren", id="product-final-url-description-run-button", className="crawler-primary-button"),
                                            dcc.Loading(html.Div(id="product-final-url-description-status", className="selection-summary", style={"marginTop": "12px"}), type="default"),
                                            grid("product-final-url-description-grid", FINAL_URL_DESCRIPTION_IMPORT_COLUMNS, height="320px", row_selection="multiple"),
                                        ],
                                        id="product-final-url-description-panel",
                                        className="unified-enrichment-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("4. Sprachfelder / Texte / SEO", className="unified-enrichment-title"),
                                            html.P(
                                                "Ergänzt, formatiert und speichert Titel, Kurzbeschreibung, Beschreibung, SEO-Felder und Slugs je Sprache.",
                                                className="form-hint",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Quellsprache"),
                                                            dcc.Dropdown(id="product-text-source-language", value="de-CH", clearable=False),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Zielsprache(n)"),
                                                            dcc.Dropdown(id="product-text-target-languages", value=["de-CH"], multi=True),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Felder"),
                                                            dcc.Checklist(
                                                                id="product-text-fields",
                                                                options=[
                                                                    {"label": "Titel aktualisieren", "value": "title"},
                                                                    {"label": "Kurzbeschreibung aktualisieren", "value": "short_description"},
                                                                    {"label": "Beschreibung aktualisieren", "value": "description"},
                                                                    {"label": "SEO-Titel aktualisieren", "value": "seo_title"},
                                                                    {"label": "SEO-Beschreibung aktualisieren", "value": "seo_description"},
                                                                    {"label": "Slug aktualisieren", "value": "slug"},
                                                                ],
                                                                value=["short_description", "description", "seo_title", "seo_description", "slug"],
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Bearbeitungsmodus"),
                                                            dcc.Checklist(
                                                                id="product-text-mode-options",
                                                                options=[
                                                                    {"label": "Nur fehlende Felder ergänzen", "value": "only_missing"},
                                                                    {"label": "Bestehende Felder aktualisieren", "value": "overwrite"},
                                                                    {"label": "SEO aus Produkttext generieren", "value": "seo"},
                                                                    {"label": "Slug aus Titel generieren", "value": "slug"},
                                                                ],
                                                                value=["only_missing", "seo", "slug"],
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Textqualität / Markdown"),
                                                            dcc.Checklist(
                                                                id="product-text-quality-options",
                                                                options=[
                                                                    {"label": "Beschreibung in sauberes Markdown umwandeln", "value": "markdown"},
                                                                    {"label": "HTML-Reste entfernen", "value": "strip_html"},
                                                                    {"label": "Doppelte Leerzeilen entfernen", "value": "collapse_blank_lines"},
                                                                    {"label": "Listen als Markdown-Bullets formatieren", "value": "markdown_bullets"},
                                                                    {"label": "Technische Abschnitte mit Überschriften strukturieren", "value": "structure_sections"},
                                                                    {"label": "Lieferantenhinweise entfernen", "value": "remove_supplier_notes"},
                                                                    {"label": "Fremde interne Artikelnummern entfernen", "value": "remove_external_numbers"},
                                                                ],
                                                                value=["strip_html", "collapse_blank_lines", "markdown_bullets", "structure_sections"],
                                                            ),
                                                        ],
                                                        className="unified-enrichment-wide",
                                                    ),
                                                ],
                                                className="form-grid unified-enrichment-grid",
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("Text-/SEO-Vorschau erzeugen", id="product-text-preview-button", className="crawler-secondary-button"),
                                                    html.Button("Ausgewählte Textvorschläge übernehmen", id="product-text-apply-selected-button", className="crawler-primary-button"),
                                                    html.Button("Alle sicheren Textvorschläge übernehmen", id="product-text-apply-safe-button", className="crawler-secondary-button"),
                                                ],
                                                className="button-row",
                                                style={"marginTop": "12px"},
                                            ),
                                            html.Div(
                                                "Locale-Schutz: jeder Vorschlag wird mit expliziter Sprache gespeichert. Standard ist Preview, keine automatische Überschreibung.",
                                                className="form-hint",
                                            ),
                                            dcc.Loading(html.Div(id="product-text-status", className="selection-summary", style={"marginTop": "12px"}), type="default"),
                                            grid("product-text-preview-grid", PRODUCT_TEXT_ENRICHMENT_COLUMNS, height="360px", row_selection="multiple"),
                                            html.Div(id="product-text-warnings", className="selection-summary", style={"marginTop": "12px"}),
                                        ],
                                        className="unified-enrichment-card",
                                    ),
                                ],
                                className="unified-enrichment-layout",
                            ),
                        ],
                        style={"background": "#f8fafc", "borderRadius": "16px", "padding": "22px", "width": "min(1320px, 100%)", "maxHeight": "92vh", "overflow": "auto", "boxShadow": "0 20px 60px rgba(0,0,0,0.24)", "border": "1px solid #e2e8f0"},
                    )
                ],
            ),
            dcc.Store(id="sidebar-nav-store", data="dashboard"),
            html.Div(
                id="product-enrich-modal",
                style=PRODUCT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H3("Website-Crawler für Produkte", className="crawler-modal-title"),
                                            html.P(
                                                "Crawlt Hersteller- oder Lieferanten-Websites und übernimmt Produktdaten, Assets und Source-URLs.",
                                                className="crawler-modal-subtitle",
                                            ),
                                        ]
                                    ),
                                    html.Button("Schliessen", id="close-product-enrich-modal-button", className="crawler-close-button"),
                                ],
                                className="crawler-modal-header",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H4("Quelle / Supplier / Resolver", className="crawler-card-title"),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Fallback-Start-URL"),
                                                            dcc.Input(
                                                                id="product-enrich-seed-url",
                                                                placeholder="https://lieferant.example/products",
                                                                className="crawler-input",
                                                            ),
                                                            html.Div(
                                                                "Wird verwendet, falls ein Produkt keine eigene Source-URL besitzt.",
                                                                className="crawler-field-hint",
                                                            ),
                                                        ],
                                                        className="crawler-field crawler-field-wide",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Lieferant / Supplier"),
                                                            dcc.Input(id="product-enrich-supplier-name", placeholder="Lieferantenname", value="Tintolav", className="crawler-input"),
                                                        ],
                                                        className="crawler-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Resolver"),
                                                            dcc.Dropdown(
                                                                id="product-enrich-resolver-mode",
                                                                options=[
                                                                    {"label": "Generischer Crawl", "value": "generic_crawl"},
                                                                    {"label": "Tintolav Katalog-Resolver", "value": "tintolav_catalog"},
                                                                ],
                                                                value="tintolav_catalog",
                                                                placeholder="Resolver",
                                                            ),
                                                        ],
                                                        className="crawler-field",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Start-URL"),
                                                            dcc.Input(
                                                                id="product-enrich-listing-url",
                                                                placeholder="Resolver Listing-URL",
                                                                value="https://www.tintolav.com/en/products/tintolav/product/listing.html",
                                                                className="crawler-input",
                                                            ),
                                                            html.Div("Startpunkt für Resolver/Katalog-Crawl.", className="crawler-field-hint"),
                                                        ],
                                                        className="crawler-field crawler-field-wide",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Limit / Anzahl Seiten"),
                                                            dcc.Input(id="product-enrich-max-pages", type="number", placeholder="Max. Seiten", value=40, className="crawler-input crawler-number-input"),
                                                        ],
                                                        className="crawler-field",
                                                    ),
                                                ],
                                                className="crawler-source-grid",
                                            ),
                                        ],
                                        className="crawler-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Was soll übernommen werden?", className="crawler-card-title"),
                                            dcc.Checklist(
                                                id="product-enrich-options",
                                                options=[
                                                    {"label": "Nur leere Felder füllen", "value": "only_empty"},
                                                    {"label": "Beschreibung aktualisieren", "value": "description"},
                                                    {"label": "Assets holen", "value": "assets"},
                                                    {"label": "Packaging übernehmen", "value": "packaging"},
                                                    {"label": "Spezifikationen übernehmen", "value": "specifications"},
                                                    {"label": "Technische Merkmale übernehmen", "value": "technical"},
                                                    {"label": "Source-URLs aktualisieren", "value": "source_urls"},
                                                ],
                                                value=[
                                                    "only_empty",
                                                    "description",
                                                    "assets",
                                                    "packaging",
                                                    "specifications",
                                                    "technical",
                                                    "source_urls",
                                                ],
                                                className="crawler-options-grid",
                                            ),
                                        ],
                                        className="crawler-card",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("Produktauswahl", className="crawler-card-title"),
                                                    html.Div(id="product-selection-summary", className="crawler-selection-summary"),
                                                ],
                                                className="crawler-card-heading-row",
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("Alle Produkte markieren", id="product-select-all-button", className="crawler-secondary-button"),
                                                    html.Button("Nur gefilterte Produkte markieren", id="product-select-filtered-button", className="crawler-secondary-button"),
                                                    html.Button("Aktuelle Produkt-Seite markieren", id="product-select-page-button", className="crawler-secondary-button"),
                                                    html.Button("Produkt-Markierung leeren", id="product-clear-selection-button", className="crawler-muted-button"),
                                                ],
                                                className="crawler-button-grid",
                                            ),
                                        ],
                                        className="crawler-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Start", className="crawler-card-title"),
                                            html.Div(
                                                [
                                                    html.Button("Website-Crawler für Produkte starten", id="product-enrich-run-button", className="crawler-primary-button"),
                                                ],
                                                className="crawler-start-row",
                                            ),
                                            html.Div(id="product-enrich-running-hint", className="crawler-running-hint"),
                                        ],
                                        className="crawler-card crawler-start-card",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Status / Ergebnis", className="crawler-card-title"),
                                            html.Div(
                                                "Noch nicht gestartet. Nach dem Lauf erscheinen hier Ergebnis, Kennzahlen und Details.",
                                                id="product-enrich-status",
                                                className="crawler-status-placeholder",
                                            ),
                                        ],
                                        className="crawler-card",
                                    ),
                                ],
                                className="crawler-modal-body",
                            ),
                        ],
                        className="crawler-modal-panel",
                    )
                ],
            ),
            html.Div(
                id="variant-enrich-modal",
                style=VARIANT_ENRICH_MODAL_HIDDEN,
                children=[
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.H3("Website-Crawler für Varianten", style={"margin": "0"}),
                                    html.Button("Schließen", id="close-variant-enrich-modal-button"),
                                ],
                                style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "16px"},
                            ),
                            html.Div(
                                [
                                    dcc.Input(id="variant-enrich-seed-url", placeholder="Fallback-Start-URL, falls Produkt keine Source-URL hat"),
                                    dcc.Input(id="variant-enrich-supplier-name", placeholder="Lieferantenname", value="Tintolav"),
                                    dcc.Dropdown(
                                        id="variant-enrich-resolver-mode",
                                        options=[
                                            {"label": "Generischer Crawl", "value": "generic_crawl"},
                                            {"label": "Tintolav Katalog-Resolver", "value": "tintolav_catalog"},
                                        ],
                                        value="tintolav_catalog",
                                        placeholder="Resolver",
                                    ),
                                    dcc.Input(
                                        id="variant-enrich-listing-url",
                                        placeholder="Resolver Listing-URL",
                                        value="https://www.tintolav.com/en/products/tintolav/product/listing.html",
                                    ),
                                    dcc.Input(id="variant-enrich-max-pages", type="number", placeholder="Max. Seiten", value=40),
                                    dcc.Checklist(
                                        id="variant-enrich-options",
                                        options=[
                                            {"label": "Nur leere Felder fuellen", "value": "only_empty"},
                                            {"label": "Beschreibung aktualisieren", "value": "description"},
                                            {"label": "Assets holen", "value": "assets"},
                                            {"label": "Packaging uebernehmen", "value": "packaging"},
                                            {"label": "Spezifikationen uebernehmen", "value": "specifications"},
                                            {"label": "Technische Merkmale uebernehmen", "value": "technical"},
                                            {"label": "Source-URLs aktualisieren", "value": "source_urls"},
                                        ],
                                        value=[
                                            "only_empty",
                                            "description",
                                            "assets",
                                            "packaging",
                                            "specifications",
                                            "technical",
                                            "source_urls",
                                        ],
                                    ),
                                    html.Button("Alle Varianten markieren", id="variant-select-all-button-modal"),
                                    html.Button("Nur gefilterte Varianten markieren", id="variant-select-filtered-button-modal"),
                                    html.Button("Aktuelle Varianten-Seite markieren", id="variant-select-page-button-modal"),
                                    html.Button("Varianten-Markierung leeren", id="variant-clear-selection-button-modal"),
                                    html.Button("Website-Crawler für Varianten starten", id="variant-enrich-run-button"),
                                ],
                                className="form-grid",
                            ),
                            html.Div(id="variant-selection-summary-modal", className="selection-summary"),
                            html.Div(id="variant-enrich-status", className="selection-summary"),
                        ],
                        style={"background": "#fff", "borderRadius": "10px", "padding": "20px", "width": "min(1100px, 100%)", "boxShadow": "0 12px 40px rgba(0,0,0,0.2)"},
                    )
                ],
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Button("‹", id="sidebar-toggle-button", className="sidebar-toggle-button", title="Menü ein-/ausklappen"),
                                ],
                                className="sidebar-toggle-row",
                            ),
                            html.Div(
                                [
                                    html.Button("Dashboard", id="nav-dashboard", className="sidebar-nav-button"),
                                    html.Button("Produkte", id="nav-products", className="sidebar-nav-button"),
                                    html.Button("Chemie", id="nav-chemistry", className="sidebar-nav-button"),
                                    html.Button("Varianten", id="nav-variants", className="sidebar-nav-button"),
                                    html.Button("Kanal-Kategorien", id="nav-categories", className="sidebar-nav-button"),
                                    html.Button("Vertriebskanäle", id="nav-sales-channels", className="sidebar-nav-button"),
                                    html.Button("Externe Kanal-Kategorien", id="nav-channel-categories", className="sidebar-nav-button"),
                                    html.Button("Assets", id="nav-assets", className="sidebar-nav-button"),
                                    html.Button("Importjobs", id="nav-jobs", className="sidebar-nav-button"),
                                    html.Button("Attribute", id="nav-attributes", className="sidebar-nav-button"),
                                    html.Button("Familien", id="nav-families", className="sidebar-nav-button"),
                                    html.Button("Übersetzungen", id="nav-translations", className="sidebar-nav-button"),
                                    html.Button("Regeln / Anreicherung", id="nav-rules", className="sidebar-nav-button"),
                                    html.Button("Dubletten / Produkt-Merge", id="nav-dedupe", className="sidebar-nav-button"),
                                    html.Button("Compliance Schweiz / SUVA", id="nav-compliance-swiss", className="sidebar-nav-button"),
                                    html.Button("Medusa Schnittstelle", id="nav-medusa", className="sidebar-nav-button"),
                                ],
                                id="sidebar-nav",
                                className="sidebar-nav",
                            ),
                        ],
                        id="sidebar-shell",
                        className="sidebar-shell",
                    ),
                    dcc.Tabs(
                        [
                    dcc.Tab(
                        value="dashboard",
                        label="Dashboard",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    metric_card("Produkte", "metric-products", "metric-products-button"),
                                    metric_card("Varianten", "metric-variants", "metric-variants-button"),
                                    metric_card("Assets", "metric-assets", "metric-assets-button"),
                                    metric_card("Importjobs", "metric-import-jobs", "metric-import-jobs-button"),
                                ],
                                className="metrics",
                            ),
                            html.H3("Letzte Importjobs"),
                            grid("jobs-grid-dashboard", IMPORT_COLUMNS, height="380px"),
                        ],
                    ),
                    dcc.Tab(
                        value="products",
                        label="Produkte",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label("Produktstatus anzeigen"),
                                            dcc.Dropdown(
                                                id="product-list-status-filter",
                                                options=[
                                                    {"label": "Aktive Produkte", "value": "active"},
                                                    {"label": "Archivierte Produkte", "value": "archived"},
                                                    {"label": "Alle Produkte", "value": "all"},
                                                ],
                                                value="active",
                                                clearable=False,
                                            ),
                                        ],
                                        style={"maxWidth": "320px", "marginBottom": "12px"},
                                    ),
                                    html.Div(id="product-channel-action-count", style={"fontWeight": "600"}),
                                    dcc.Checklist(
                                        id="product-channel-include-variants",
                                        options=[{"label": "Zugehörige Varianten ebenfalls auswählen", "value": "include"}],
                                        value=[],
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Kanal-Aktionen", id="product-channel-action-open-button"),
                                            html.Button("Produkt-Listings", id="product-listings-action-open-button"),
                                            html.Button("Kanal-Kategorien", id="product-category-action-open-button"),
                                            html.Button("Varianten-Listings", id="product-variant-listings-action-open-button"),
                                            html.Button("Markierte Produkte bearbeiten", id="product-bulk-edit-open-button"),
                                            html.Button("Übersetzungen erstellen", id="product-translation-open-button"),
                                            html.Button("Übersetzungs-Prompts", id="product-translation-prompts-button"),
                                            html.Button("Produktdaten anreichern", id="product-data-enrichment-open-button"),
                                        ],
                                        className="button-row",
                                    ),
                                ],
                                id="product-channel-actions",
                                className="selection-summary",
                                style={"display": "none"},
                            ),
                            grid("products-grid", PRODUCT_COLUMNS, height="520px", row_selection="multiple"),
                            html.H3("Produkt bearbeiten"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Produkt-ID"),
                                                    dcc.Input(id="product-id", type="number", disabled=True, style={"width": "100%"}),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Artikelnummer (SKU)"),
                                                    dcc.Input(id="product-sku", style={"width": "100%"}),
                                                ]
                                            ),
                                        ],
                                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Originaltitel"),
                                            dcc.Input(id="product-title", placeholder="Originaltitel", style={"width": "100%"}),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Marke"),
                                                    dcc.Dropdown(id="product-brand", placeholder="Marke"),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Status"),
                                                    dcc.Dropdown(
                                                        id="product-status",
                                                        options=PUBLICATION_STATUS_OPTIONS,
                                                        value="draft",
                                                    ),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Originalsprache"),
                                                    dcc.Dropdown(
                                                        id="product-source-language",
                                                        options=LANGUAGE_CODE_OPTIONS,
                                                        placeholder="Sprachcode wählen",
                                                        clearable=False,
                                                    ),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Chemieprodukt"),
                                                    dcc.Dropdown(
                                                        id="product-is-chemical",
                                                        options=BOOLEAN_OPTIONS,
                                                        value=False,
                                                        clearable=False,
                                                    ),
                                                ]
                                            ),
                                        ],
                                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr 1fr", "gap": "12px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Kanal für Produkt-Kategorien"),
                                                    dcc.Dropdown(id="product-category-channel-code", value="voxster", clearable=False),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Kanal-Kategorien"),
                                                    dcc.Dropdown(id="product-categories", multi=True, placeholder="Kanal-Kategorien auswählen"),
                                                ]
                                            ),
                                        ],
                                        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Kurzbeschreibung"),
                                            dcc.Textarea(
                                                id="product-short-description",
                                                placeholder="Kurzbeschreibung als reiner Text, ein Satz mit ca. 120-180 Zeichen",
                                                style={"width": "100%", "height": "72px"},
                                            ),
                                        ]
                                    ),
                                    html.H4("Beschreibung", className="form-section-heading"),
                                    dcc.Textarea(
                                        id="product-description",
                                        placeholder="Beschreibung in Markdown, z.B. Einleitung, ### Eigenschaften, Bulletpoints",
                                        style={"width": "100%", "height": "240px"},
                                    ),
                                    html.H4("Quellen", className="form-section-heading"),
                                    html.Div(
                                        [
                                            html.Label("Quell-URLs"),
                                            dcc.Textarea(
                                                id="product-source-url",
                                                placeholder="Eine Quelle pro Zeile, z.B. Herstellerseite, Lieferantenseite, alte Shop-URL",
                                                style={"width": "100%", "height": "90px"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Final URL"),
                                            dcc.Input(
                                                id="product-source-url-final",
                                                placeholder="Kanonische Hauptquelle / beste Produktseite",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Neu anlegen", id="product-create-button"),
                                            html.Button("Speichern", id="product-save-button"),
                                            html.Button("Archivieren", id="product-archive-button"),
                                            html.Button("Zur Chemieansicht", id="product-open-chemistry-button", style={"display": "none"}),
                                        ],
                                        className="button-row",
                                    ),
                                ],
                                className="form-grid",
                            ),
                            dcc.Tabs(
                                id="product-detail-tabs",
                                value="overview",
                                className="detail-tabs",
                                children=[
                                    dcc.Tab(
                                        label="Übersicht",
                                        value="overview",
                                        className="detail-tab",
                                        selected_className="detail-tab-selected",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H4("Stammdaten"),
                                                            html.Div(id="product-detail-summary", className="detail-summary-grid"),
                                                        ],
                                                        className="panel",
                                                    )
                                                ],
                                                style={"marginTop": "12px"},
                                            )
                                        ],
                                    ),
                                    dcc.Tab(
                                        label="Varianten",
                                        value="variants",
                                        className="detail-tab",
                                        selected_className="detail-tab-selected",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H4("Varianten"),
                                                            html.Div(
                                                                [
                                                                    dcc.Dropdown(
                                                                        id="product-detail-variant-status-filter",
                                                                        options=[
                                                                            {"label": "Aktive Varianten", "value": "active"},
                                                                            {"label": "Archivierte Varianten", "value": "archived"},
                                                                            {"label": "Alle Varianten", "value": "all"},
                                                                        ],
                                                                        value="active",
                                                                        clearable=False,
                                                                        style={"minWidth": "220px"},
                                                                    ),
                                                                    html.Button("Ausgewählte Varianten archivieren", id="product-detail-variant-archive-button"),
                                                                    html.Button("Ausgewählte Varianten löschen / entfernen", id="product-detail-variant-delete-button"),
                                                                ],
                                                                className="button-row",
                                                            ),
                                                            grid("product-detail-variants", DETAIL_VARIANT_COLUMNS, height="280px", row_selection="multiple"),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Staffelpreise"),
                                                            grid("product-detail-tiers", DETAIL_TIER_COLUMNS, height="240px"),
                                                        ],
                                                        className="panel",
                                                    ),
                                                ],
                                                className="detail-columns",
                                                style={"marginTop": "12px"},
                                            )
                                        ],
                                    ),
                                    dcc.Tab(
                                        label="Assets",
                                        value="assets",
                                        className="detail-tab",
                                        selected_className="detail-tab-selected",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H4("Assets"),
                                                            html.Div(
                                                                [
                                                                    html.Button("Asset nach oben", id="asset-move-up-button"),
                                                                    html.Button("Asset nach unten", id="asset-move-down-button"),
                                                                    html.Button("Asset löschen", id="asset-delete-button"),
                                                                ],
                                                                className="button-row",
                                                            ),
                                                            grid("product-detail-assets", DETAIL_ASSET_COLUMNS, height="300px"),
                                                            html.Div(id="product-detail-asset-links", style={"marginTop": "10px"}),
                                                            html.Div(id="product-detail-asset-preview", style={"marginTop": "10px"}),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Asset hochladen"),
                                                            html.Div(id="asset-upload-status", className="selection-summary"),
                                                            dcc.Upload(
                                                                id="asset-upload",
                                                                children=html.Div(["Datei hier ablegen oder klicken"]),
                                                                style={
                                                                    "width": "100%",
                                                                    "height": "60px",
                                                                    "lineHeight": "60px",
                                                                    "borderWidth": "1px",
                                                                    "borderStyle": "dashed",
                                                                    "borderRadius": "4px",
                                                                    "textAlign": "center",
                                                                    "marginBottom": "12px",
                                                                },
                                                            ),
                                                        ],
                                                        className="panel",
                                                    ),
                                                ],
                                                className="detail-columns",
                                                style={"marginTop": "12px"},
                                            )
                                        ],
                                    ),
                                    dcc.Tab(
                                        label="Kanäle / Listings",
                                        value="channels",
                                        className="detail-tab",
                                        selected_className="detail-tab-selected",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.H4("Produkt-Listings"),
                                                            grid("product-detail-channel-listings", PRODUCT_CHANNEL_LISTING_COLUMNS, height="220px"),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Kanal-Kategorie-Mappings"),
                                                            grid("product-detail-category-mappings", PRODUCT_CATEGORY_MAPPING_COLUMNS, height="180px"),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Vertriebskanal"), dcc.Dropdown(id="product-channel-mapping-sales-channel-id", placeholder="Kanal")]),
                                                                    html.Div([html.Label("Kanal-Kategorie"), dcc.Dropdown(id="product-channel-mapping-channel-category-id", placeholder="Kanal-Kategorie")]),
                                                                    html.Div([html.Label("Primär"), dcc.Dropdown(id="product-channel-mapping-is-primary", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                                                    html.Div([html.Label("Aktion"), html.Button("Kanal-Kategorie speichern", id="product-channel-mapping-save-button")]),
                                                                ],
                                                                className="form-grid",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Varianten-Listings"),
                                                            grid("product-detail-variant-channel-listings", VARIANT_CHANNEL_LISTING_COLUMNS, height="260px"),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Varianten-Kategorie-Mappings"),
                                                            grid("product-detail-variant-category-mappings", VARIANT_CATEGORY_MAPPING_COLUMNS, height="220px"),
                                                        ],
                                                        className="panel",
                                                    ),
                                                ],
                                                className="detail-columns",
                                                style={"marginTop": "12px"},
                                            )
                                        ],
                                    ),
                                    dcc.Tab(
                                        label="Übersetzungen",
                                        value="translations",
                                        className="detail-tab",
                                        selected_className="detail-tab-selected",
                                        children=[
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.H4("Vorhandene Übersetzungen"),
                                                                    grid("product-detail-translations", DETAIL_TRANSLATION_COLUMNS, height="280px"),
                                                                ],
                                                                className="panel",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Übersetzung speichern"),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("Sprache"), dcc.Dropdown(
                                                                                id="translation-language",
                                                                                options=LANGUAGE_CODE_OPTIONS,
                                                                                placeholder="Sprache wählen",
                                                                                clearable=False,
                                                                            )]),
                                                                            html.Div([html.Label("Titel"), dcc.Input(id="translation-title", placeholder="Titel")]),
                                                                            html.Div([html.Label("Kurzbeschreibung"), dcc.Input(id="translation-short-description", placeholder="Kurzbeschreibung")]),
                                                                            html.Div([html.Label("Beschreibung"), dcc.Textarea(id="translation-description", placeholder="Beschreibung in Markdown", style={"width": "100%", "height": "240px"})]),
                                                                            html.Div([html.Label("SEO-Titel"), dcc.Input(id="translation-seo-title", placeholder="SEO-Titel")]),
                                                                            html.Div([html.Label("SEO-Beschreibung"), dcc.Textarea(id="translation-seo-description", placeholder="SEO-Beschreibung", style={"width": "100%", "height": "110px"})]),
                                                                            html.Div([html.Label("Slug"), dcc.Input(id="translation-slug", placeholder="Slug")]),
                                                                            html.Button("Übersetzung speichern", id="translation-save-button"),
                                                                        ],
                                                                        className="form-grid",
                                                                    ),
                                                                ],
                                                                className="panel",
                                                            ),
                                                        ],
                                                        className="translation-row",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.H4("Vorhandene Varianten-Übersetzungen"),
                                                                    grid("product-detail-variant-translations", DETAIL_VARIANT_TRANSLATION_COLUMNS, height="240px"),
                                                                ],
                                                                className="panel",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Varianten-Übersetzung speichern"),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("Übersetzungs-ID"), dcc.Input(id="variant-translation-id", readOnly=True, placeholder="Neu")]),
                                                                            html.Div([html.Label("Variante"), dcc.Dropdown(id="variant-translation-variant-id", placeholder="Variante wählen")]),
                                                                            html.Div([html.Label("Sprache"), dcc.Dropdown(
                                                                                id="variant-translation-language",
                                                                                options=LANGUAGE_CODE_OPTIONS,
                                                                                placeholder="Sprache wählen",
                                                                                clearable=False,
                                                                            )]),
                                                                            html.Div([html.Label("Titel"), dcc.Input(id="variant-translation-title", placeholder="Titel")]),
                                                                            html.Div([html.Label("Optionslabel"), dcc.Input(id="variant-translation-option-label-override", placeholder="Optionslabel")]),
                                                                            html.Div([html.Label("Gebindelabel"), dcc.Input(id="variant-translation-package-label", placeholder="Gebindelabel")]),
                                                                            html.Button("Varianten-Übersetzung speichern", id="variant-translation-save-button"),
                                                                        ],
                                                                        className="form-grid",
                                                                    ),
                                                                ],
                                                                className="panel",
                                                            ),
                                                        ],
                                                        className="translation-row",
                                                    ),
                                                ],
                                                className="translation-layout",
                                                style={"marginTop": "12px"},
                                            )
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="chemistry",
                        label="Chemie",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("ADR-relevant"),
                                                    dcc.Dropdown(
                                                        id="chemistry-filter-adr",
                                                        options=[{"label": "Alle", "value": "all"}, {"label": "Ja", "value": "yes"}, {"label": "Nein", "value": "no"}],
                                                        value="all",
                                                        clearable=False,
                                                    ),
                                                ]
                                            , style={"maxWidth": "180px"}),
                                            html.Div(
                                                [
                                                    html.Label("SDB vorhanden"),
                                                    dcc.Dropdown(
                                                        id="chemistry-filter-sds",
                                                        options=[{"label": "Alle", "value": "all"}, {"label": "Ja", "value": "yes"}, {"label": "Nein", "value": "no"}],
                                                        value="all",
                                                        clearable=False,
                                                    ),
                                                ]
                                            , style={"maxWidth": "180px"}),
                                            html.Div(
                                                [
                                                    html.Label("Nur Gewerbe"),
                                                    dcc.Dropdown(
                                                        id="chemistry-filter-business",
                                                        options=[{"label": "Alle", "value": "all"}, {"label": "Ja", "value": "yes"}, {"label": "Nein", "value": "no"}],
                                                        value="all",
                                                        clearable=False,
                                                    ),
                                                ]
                                            , style={"maxWidth": "180px"}),
                                            html.Div(
                                                [
                                                    html.Label("Status"),
                                                    dcc.Dropdown(
                                                        id="chemistry-filter-status",
                                                        options=[
                                                            {"label": "Alle", "value": "all"},
                                                            *PUBLICATION_STATUS_OPTIONS,
                                                        ],
                                                        value="all",
                                                        clearable=False,
                                                    ),
                                                ]
                                            , style={"maxWidth": "180px"}),
                                        ],
                                        className="form-grid",
                                        style={"gridTemplateColumns": "repeat(4, minmax(140px, 180px))", "justifyContent": "start", "alignItems": "end", "marginBottom": "12px"},
                                    ),
                                    grid("chemistry-grid", CHEMISTRY_COLUMNS, height="460px"),
                                ],
                                className="panel",
                            ),
                            html.Div(
                                [
                                    html.H3("Chemie-Detail"),
                                    html.Div(id="chemistry-detail-summary", className="selection-summary"),
                                    dcc.Tabs(
                                        id="chemistry-detail-tabs",
                                        value="chemical-general",
                                        className="detail-tabs",
                                        children=[
                                            dcc.Tab(
                                                label="Allgemein",
                                                value="chemical-general",
                                                className="detail-tab",
                                                selected_className="detail-tab-selected",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.Div(
                                                                        [
                                                                            html.H4("Produkt", style={"gridColumn": "1 / -1", "margin": "0 0 4px"}),
                                                                            html.Div([html.Label("Produkt-ID"), dcc.Input(id="chemistry-product-id", type="number", disabled=True, style={"width": "100%"})]),
                                                                            html.Div([html.Label("SKU"), dcc.Input(id="chemistry-product-sku", style={"width": "100%"})]),
                                                                            html.Div([html.Label("Originaltitel"), dcc.Input(id="chemistry-product-title", style={"width": "100%"})], style={"gridColumn": "span 2"}),
                                                                            html.Div([html.Label("Marke"), dcc.Dropdown(id="chemistry-product-brand", placeholder="Marke")]),
                                                                            html.Div(
                                                                                [
                                                                                    html.Label("Status"),
                                                                                    dcc.Dropdown(
                                                                                        id="chemistry-product-status",
                                                                                        options=PUBLICATION_STATUS_OPTIONS,
                                                                                        value="draft",
                                                                                        clearable=False,
                                                                                    ),
                                                                                ]
                                                                            ),
                                                                            html.Div([html.Label("Originalsprache"), dcc.Dropdown(id="chemistry-product-language", options=LANGUAGE_CODE_OPTIONS, clearable=False)]),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"gridTemplateColumns": "repeat(3, minmax(180px, 1fr))", "alignContent": "start"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.H4("Identifikation", style={"gridColumn": "1 / -1", "margin": "0 0 4px"}),
                                                                            html.Div([html.Label("Chemieprodukt"), dcc.Dropdown(id="chemistry-is-chemical", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                            html.Div([html.Label("Chemie-Typ / Stoffgruppe"), dcc.Input(id="chemistry-chemical-type", style={"width": "100%"})]),
                                                                            html.Div([html.Label("UFI-Nummer"), dcc.Input(id="chemistry-ufi", placeholder="z. B. 0A80-10U4-F00M-UJ45", style={"width": "100%"})]),
                                                                            html.Div([html.Label("CAS-Nummer"), dcc.Input(id="chemistry-cas-number", style={"width": "100%"})]),
                                                                            html.Div([html.Label("EG-Nummer"), dcc.Input(id="chemistry-ec-number", style={"width": "100%"})]),
                                                                            html.Div([html.Label("VOC-Gehalt (%)"), dcc.Input(id="chemistry-voc-content-percent", placeholder="z. B. 1.12", style={"width": "100%"})]),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"gridTemplateColumns": "repeat(2, minmax(190px, 1fr))", "alignContent": "start"},
                                                                    ),
                                                                ],
                                                                style={"display": "grid", "gridTemplateColumns": "minmax(420px, 1.25fr) minmax(360px, 1fr)", "gap": "18px"},
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px", "maxWidth": "1280px"},
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Transport", style={"margin": "0 0 12px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("UN-Nummer"), dcc.Input(id="chemistry-un-number", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Gefahrgutklasse"), dcc.Input(id="chemistry-hazard-class", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Verpackungsgruppe"), dcc.Input(id="chemistry-packing-group", style={"width": "100%"})]),
                                                                    html.Div([html.Label("ADR-relevant"), dcc.Dropdown(id="chemistry-adr-relevant", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                ],
                                                                className="form-grid",
                                                                style={"gridTemplateColumns": "repeat(4, minmax(160px, 240px))", "justifyContent": "start"},
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px", "maxWidth": "1080px"},
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Physikalische / technische Daten", style={"margin": "0 0 12px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Dichte"), dcc.Input(id="chemistry-density", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Farbe"), dcc.Input(id="chemistry-color", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Geruch"), dcc.Input(id="chemistry-odor", style={"width": "100%"})]),
                                                                    html.Div([html.Label("pH-Wert"), dcc.Input(id="chemistry-ph-value", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Flammpunkt"), dcc.Input(id="chemistry-flash-point", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Siedebereich / Siedepunkt"), dcc.Input(id="chemistry-boiling-point", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Viskosität"), dcc.Input(id="chemistry-viscosity", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Löslichkeit"), dcc.Input(id="chemistry-solubility", style={"width": "100%"})]),
                                                                ],
                                                                className="form-grid",
                                                                style={"gridTemplateColumns": "repeat(4, minmax(150px, 220px))", "justifyContent": "start"},
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px", "maxWidth": "980px"},
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Kennzeichnung / Sicherheit", style={"margin": "0 0 12px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("GHS-Piktogramme"), dcc.Dropdown(id="chemistry-ghs-pictograms", options=GHS_OPTIONS, multi=True)]),
                                                                    html.Div([html.Label("Signalwort"), dcc.Dropdown(id="chemistry-signal-word", options=SIGNAL_WORD_OPTIONS)]),
                                                                    html.Div([html.Label("ADR-Piktogramme"), dcc.Dropdown(id="chemistry-adr-pictograms", options=ADR_OPTIONS, multi=True)]),
                                                                    html.Div([html.Label("Umweltgefährdend"), dcc.Dropdown(id="chemistry-environmentally-hazardous", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                ],
                                                                className="form-grid",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Piktogramm-Vorschau"),
                                                                    html.Div(id="chemistry-symbol-preview"),
                                                                ],
                                                                className="selection-summary",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("H-Sätze"), dcc.Textarea(id="chemistry-hazard-statements", style={"width": "100%", "height": "96px"})]),
                                                                    html.Div([html.Label("P-Sätze"), dcc.Textarea(id="chemistry-precautionary-statements", style={"width": "100%", "height": "96px"})]),
                                                                ],
                                                                className="detail-columns",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("WGK / Lagerklasse"),
                                                                    html.Div("WGK, Lagerklasse und ADR sind unterschiedliche Klassifizierungen und dürfen nicht vermischt werden. ADR ist Transport, WGK ist Gewässerschutz/AwSV, Lagerklasse ist Lagerung/TRGS 510.", style={"color": "#475569"}),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("WGK"), dcc.Dropdown(id="chemistry-wgk", options=WGK_OPTIONS, clearable=False)]),
                                                                            html.Div([html.Label("Lagerklasse nach TRGS 510"), dcc.Dropdown(id="chemistry-storage-class", options=STORAGE_CLASS_OPTIONS, clearable=False)]),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"marginTop": "10px"},
                                                                    ),
                                                                    html.Div(id="chemistry-wgk-storage-meta", style={"marginTop": "8px"}),
                                                                    html.Div(
                                                                        [
                                                                            html.Button("Aus SDB anreichern", id="chemistry-wgk-storage-enrich-button"),
                                                                            html.Button("Vorschlag übernehmen", id="chemistry-wgk-storage-apply-button"),
                                                                            html.Button("Vorschlag verwerfen", id="chemistry-wgk-storage-discard-button"),
                                                                        ],
                                                                        className="button-row",
                                                                        style={"marginTop": "10px"},
                                                                    ),
                                                                    html.Div(id="chemistry-wgk-storage-proposal", className="selection-summary", style={"marginTop": "10px"}),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("SDB vorhanden"), dcc.Dropdown(id="chemistry-sds-available", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                    html.Div([html.Label("SDB URL"), dcc.Input(id="chemistry-sds-url", style={"width": "100%"})]),
                                                                    html.Div([html.Label("SDB Asset"), dcc.Dropdown(id="chemistry-sds-asset-id", placeholder="Asset wählen")]),
                                                                ],
                                                                className="form-grid",
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px", "maxWidth": "1280px"},
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.H4("Vertrieb / Freigabe", style={"margin": "0 0 12px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Nur für Gewerbe"), dcc.Dropdown(id="chemistry-business-only", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                    html.Div([html.Label("Altersprüfung erforderlich"), dcc.Dropdown(id="chemistry-age-check-required", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                    html.Div([html.Label("Versandfähig"), dcc.Dropdown(id="chemistry-shippable", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                    html.Div([html.Label("Aktiv im Shop"), dcc.Dropdown(id="chemistry-shop-active", options=BOOLEAN_OPTIONS, clearable=False)]),
                                                                ],
                                                                className="form-grid",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("LQ / Gefahrgutversand Hinweis"), dcc.Input(id="chemistry-limited-quantity", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Gefahrgutversand / ADR Hinweis"), dcc.Textarea(id="chemistry-hazard-shipping-note", style={"width": "100%", "height": "96px"})]),
                                                                ],
                                                                className="detail-columns",
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px", "maxWidth": "1080px"},
                                                    ),
                                                ],
                                            ),
                                            dcc.Tab(
                                                label="Internet",
                                                value="chemical-internet",
                                                className="detail-tab",
                                                selected_className="detail-tab-selected",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.Div(
                                                                        [
                                                                            html.Label("Referenz-URLs"),
                                                                            dcc.Textarea(
                                                                                id="chemistry-enrichment-reference-urls",
                                                                                style={"width": "100%", "height": "96px"},
                                                                                placeholder="Eine oder mehrere Referenz-URLs, jeweils in neuer Zeile",
                                                                            ),
                                                                        ]
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.Label("Übernahme-Modus"),
                                                                            dcc.Dropdown(
                                                                                id="chemistry-enrichment-overwrite-mode",
                                                                                options=[
                                                                                    {"label": "Nur leere Felder füllen", "value": "empty"},
                                                                                    {"label": "Bestehende Felder überschreiben", "value": "overwrite"},
                                                                                ],
                                                                                value="empty",
                                                                                clearable=False,
                                                                            ),
                                                                        ]
                                                                    ),
                                                                ],
                                                                className="detail-columns",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Button("Internetanreicherung starten", id="chemistry-enrichment-run-button"),
                                                                    html.Button("Ausgewählte/alle Vorschläge übernehmen", id="chemistry-enrichment-apply-button"),
                                                                ],
                                                                className="button-row",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                "Während eine Internet-Aktion läuft, bitte warten und keine zweite Chemie-Aktion starten.",
                                                                className="selection-summary",
                                                                style={"marginTop": "10px", "fontSize": "13px", "borderLeft": "4px solid #2563eb"},
                                                            ),
                                                            dcc.Loading(
                                                                html.Div(id="chemistry-enrichment-status", className="selection-summary", style={"marginTop": "12px"}),
                                                                type="default",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Div(
                                                                        [
                                                                            html.H4("Gefundene Quellen"),
                                                                            html.Div(id="chemistry-enrichment-runs"),
                                                                        ],
                                                                        className="panel",
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.H4("Dokumente / PDF-Links"),
                                                                            html.Div(id="chemistry-enrichment-documents"),
                                                                        ],
                                                                        className="panel",
                                                                    ),
                                                                ],
                                                                className="detail-columns",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Extrahierte Werte vs. bestehende Werte"),
                                                                    html.Div(id="chemistry-enrichment-preview"),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Vorschläge zur manuellen Übernahme"),
                                                                    html.Div(
                                                                        "Werte werden nicht automatisch veröffentlicht. Wähle einzelne Zeilen aus oder übernimm alle vorgeschlagenen Zeilen.",
                                                                        className="selection-summary",
                                                                    ),
                                                                    grid("chemistry-enrichment-suggestions-grid", CHEMISTRY_ENRICHMENT_SUGGESTION_COLUMNS, height="280px", row_selection="multiple"),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Protokoll"),
                                                                    dcc.Textarea(
                                                                        id="chemistry-enrichment-log",
                                                                        readOnly=True,
                                                                        style={"width": "100%", "height": "180px", "fontFamily": "monospace"},
                                                                    ),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px"},
                                                    )
                                                ],
                                            ),
                                            dcc.Tab(
                                                label="SDB",
                                                value="chemical-sdb",
                                                className="detail-tab",
                                                selected_className="detail-tab-selected",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.H3("SDB-Arbeitsbereich", style={"margin": "0"}),
                                                                    html.Div(
                                                                        "1. Quelle importieren, 2. Abschnitte 1-16 prüfen, 3. Voxster/CH-Daten normieren, 4. PDF-Version erzeugen.",
                                                                        style={"color": "#475569", "marginTop": "4px"},
                                                                    ),
                                                                    html.Div(
                                                                        "Sicherheitsdatenblätter sind rechtlich relevante Dokumente und müssen vor Verwendung fachlich geprüft und freigegeben werden.",
                                                                        className="selection-summary",
                                                                        style={"marginTop": "8px", "borderLeft": "4px solid #f59e0b"},
                                                                    ),
                                                                ],
                                                                style={"marginBottom": "14px"},
                                                            ),
                                                            html.H4("1. Quelle importieren", style={"marginBottom": "8px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Quell-URL"), dcc.Input(id="chemistry-sdb-source-url", style={"width": "100%"})]),
                                                                    html.Div([html.Label("PDF-/SDB-URL"), dcc.Input(id="chemistry-sdb-pdf-url", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Quell-Asset"), dcc.Dropdown(id="chemistry-sdb-source-asset-id", placeholder="Asset wählen")]),
                                                                ],
                                                                className="form-grid",
                                                                style={"gridTemplateColumns": "repeat(auto-fit, minmax(220px, 320px))", "justifyContent": "start"},
                                                            ),
                                                            html.Div(
                                                                "Tipp: Wenn unten ein bestehendes Dokument ausgewählt ist, kann der Import-Button dessen Asset automatisch als Quelle verwenden.",
                                                                className="selection-summary",
                                                                style={"marginTop": "8px", "fontSize": "13px"},
                                                            ),
                                                            html.H4("2. Schweizer/Voxster-Kopfdaten", style={"marginTop": "18px", "marginBottom": "8px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Dokumenttitel"), dcc.Input(id="chemistry-sdb-document-title", style={"width": "100%"})]),
                                                                    html.Div(
                                                                        [
                                                                            html.Label("Review-Status"),
                                                                            dcc.Dropdown(
                                                                                id="chemistry-sdb-review-status",
                                                                                options=[
                                                                                    {"label": "Review erforderlich", "value": "review_required"},
                                                                                    {"label": "In Prüfung", "value": "in_review"},
                                                                                    {"label": "Freigegeben", "value": "approved"},
                                                                                ],
                                                                                clearable=False,
                                                                            ),
                                                                        ]
                                                                    ),
                                                                    html.Div([html.Label("Version"), dcc.Input(id="chemistry-sdb-version-label", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Gültig ab"), dcc.Input(id="chemistry-sdb-effective-date", placeholder="TT.MM.JJJJ", style={"width": "100%"})]),
                                                                ],
                                                                className="form-grid",
                                                                style={"marginTop": "12px", "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 260px))", "justifyContent": "start"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Div([html.Label("Absender"), dcc.Input(id="chemistry-sdb-issuer-name", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Adresse 1"), dcc.Input(id="chemistry-sdb-issuer-address-line1", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Adresse 2"), dcc.Input(id="chemistry-sdb-issuer-address-line2", style={"width": "100%"})]),
                                                                    html.Div([html.Label("PLZ"), dcc.Input(id="chemistry-sdb-issuer-postal-code", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Ort"), dcc.Input(id="chemistry-sdb-issuer-city", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Land"), dcc.Input(id="chemistry-sdb-issuer-country-code", style={"width": "100%"})]),
                                                                    html.Div([html.Label("Telefon"), dcc.Input(id="chemistry-sdb-issuer-phone", style={"width": "100%"})]),
                                                                    html.Div([html.Label("E-Mail"), dcc.Input(id="chemistry-sdb-issuer-email", style={"width": "100%"})]),
                                                                ],
                                                                className="form-grid",
                                                                style={"marginTop": "12px", "gridTemplateColumns": "repeat(auto-fit, minmax(160px, 240px))", "justifyContent": "start"},
                                                            ),
                                                            html.Div(id="chemistry-sdb-parser-status", className="selection-summary", style={"marginTop": "12px"}),
                                                            html.Div(id="chemistry-sdb-openai-status", className="selection-summary", style={"marginTop": "12px"}),
                                                            dcc.Loading(
                                                                html.Div(id="chemistry-sdb-llm-status", className="selection-summary", style={"marginTop": "12px"}),
                                                                type="default",
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Strong("Ausführungsmodus"),
                                                                    html.Div(
                                                                        "Deterministisch: importiert Quelle und erzeugt PDF aus geprüften Abschnitten. KI/LLM: erstellt nur einen prüfpflichtigen Textentwurf.",
                                                                        style={"marginTop": "4px", "fontSize": "13px", "color": "#475569"},
                                                                    ),
                                                                ],
                                                                className="selection-summary",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.H4("3. Rohtext und Abschnitte 1-16", style={"marginTop": "18px", "marginBottom": "8px"}),
                                                            html.Div([html.Label("Rohtext"), dcc.Textarea(id="chemistry-sdb-raw-text", style={"width": "100%", "height": "180px"})], style={"marginTop": "12px"}),
                                                            html.Div(
                                                                [
                                                                    html.Div(
                                                                        [
                                                                            html.Label(f"{index}. {title}"),
                                                                            dcc.Textarea(
                                                                                id={"type": "chemistry-sdb-section", "index": index},
                                                                                style={"width": "100%", "height": "160px"},
                                                                            ),
                                                                        ]
                                                                    )
                                                                    for index, title in SDB_SECTION_TITLES.items()
                                                                ],
                                                                className="detail-columns",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.Button("Arbeitsversion speichern", id="chemistry-sdb-save-button"),
                                                                    html.Button("Rohtext + Abschnitte leeren", id="chemistry-sdb-clear-button"),
                                                                    html.Button("Quelle importieren und Abschnitte füllen", id="chemistry-sdb-import-from-source-button"),
                                                                    dcc.Dropdown(
                                                                        id="chemistry-sdb-llm-quality-mode",
                                                                        options=[
                                                                            {"label": "Standard", "value": "standard"},
                                                                            {"label": "Gründlich", "value": "thorough"},
                                                                            {"label": "Sehr gründlich", "value": "xhigh"},
                                                                        ],
                                                                        value="thorough",
                                                                        clearable=False,
                                                                        style={"minWidth": "170px"},
                                                                    ),
                                                                    html.Button("Text mit KI strukturieren / helvetisieren", id="chemistry-sdb-llm-normalize-button"),
                                                                    html.Button("Arbeitsversion als PDF erzeugen", id="chemistry-sdb-generate-pdf-button"),
                                                                ],
                                                                className="button-row",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H3("4. Versionen, Übersetzungen und PDF-Export", style={"marginTop": "0"}),
                                                                    html.Div(
                                                                        "Jede Zeile ist eine SDB-Version. Nur Versionen mit Text können als PDF erzeugt werden. KI-Versionen sind Entwürfe und müssen vor Veröffentlichung geprüft werden.",
                                                                        id="chemistry-sdb-translation-warning",
                                                                        className="selection-summary",
                                                                        style={"borderLeft": "4px solid #f59e0b", "padding": "10px 12px"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("Sprache"), dcc.Dropdown(id="chemistry-sdb-document-language-filter", clearable=True)]),
                                                                            html.Div([html.Label("Status"), dcc.Dropdown(id="chemistry-sdb-document-status-filter", clearable=True)]),
                                                                            html.Div([html.Label("Quelle"), dcc.Dropdown(id="chemistry-sdb-document-source-filter", clearable=True)]),
                                                                            html.Div(
                                                                                [
                                                                                    html.Label("Versionen"),
                                                                                    dcc.Dropdown(
                                                                                        id="chemistry-sdb-document-current-filter",
                                                                                        options=[
                                                                                            {"label": "Nur aktuelle", "value": "current"},
                                                                                            {"label": "Alle Versionen", "value": "all"},
                                                                                        ],
                                                                                        value="current",
                                                                                        clearable=False,
                                                                                    ),
                                                                                ]
                                                                            ),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"gridTemplateColumns": "repeat(auto-fit, minmax(160px, 220px))", "justifyContent": "start", "marginTop": "10px"},
                                                                    ),
                                                                    grid("chemistry-sdb-documents-grid", CHEMICAL_DOCUMENT_COLUMNS, height="308px", row_selection="multiple"),
                                                                    html.Div(id="chemistry-sdb-selected-document-summary", className="selection-summary", style={"marginTop": "10px"}),
                                                                            html.Div(
                                                                                [
                                                                                    html.Button("Gewählte Version als geprüft markieren", id="chemistry-sdb-document-review-button"),
                                                                                    html.Button("Gewählte Version archivieren", id="chemistry-sdb-document-archive-button"),
                                                                                    html.Button("Gewählte Version löschen", id="chemistry-sdb-document-delete-button"),
                                                                                    html.Button("SUVA-Prüfung starten", id="chemistry-sdb-suva-check-button"),
                                                                                    html.Button("SUVA-Block in Abschnitt 8.1 übernehmen", id="chemistry-sdb-suva-section8-button"),
                                                                                    html.Button("CH-SDB prüfen", id="chemistry-sdb-ch-review-button"),
                                                                                    html.Button("PDF für gewählte Version erzeugen", id="chemistry-sdb-document-pdf-button"),
                                                                                    html.Button("Als final freigeben", id="chemistry-sdb-final-release-button"),
                                                                            dcc.Dropdown(
                                                                                id="chemistry-sdb-document-status-set",
                                                                                options=[
                                                                                    {"label": "draft", "value": "draft"},
                                                                                    {"label": "generated", "value": "generated"},
                                                                                    {"label": "checked", "value": "checked"},
                                                                                    {"label": "approved", "value": "approved"},
                                                                                    {"label": "outdated", "value": "outdated"},
                                                                                    {"label": "error", "value": "error"},
                                                                                    {"label": "archived", "value": "archived"},
                                                                                ],
                                                                                value="checked",
                                                                                clearable=False,
                                                                                style={"minWidth": "150px"},
                                                                            ),
                                                                            html.Button("Status setzen", id="chemistry-sdb-document-set-status-button"),
                                                                        ],
                                                                        className="button-row",
                                                                        style={"marginTop": "10px"},
                                                                    ),
                                                                    dcc.Loading(
                                                                        html.Div(id="chemistry-sdb-document-action-status", className="selection-summary", style={"marginTop": "10px"}),
                                                                        type="default",
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.H4("SUVA-Grenzwerte / Abschnitt 8.1"),
                                                                            html.Div(
                                                                                "Prüft Abschnitt-3-Inhaltsstoffe gegen die importierte SUVA-Liste. Kein Treffer bedeutet keine Entwarnung.",
                                                                                className="selection-summary",
                                                                            ),
                                                                            html.Div(id="chemistry-sdb-suva-source-summary", className="selection-summary", style={"marginTop": "8px"}),
                                                                            grid("chemistry-sdb-suva-items-grid", SUVA_CHECK_ITEM_COLUMNS, height="390px", row_selection="single"),
                                                                        ],
                                                                        className="panel",
                                                                        style={"marginTop": "12px"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.H4("CH-SDB-Review-Punkte"),
                                                                            html.Div(
                                                                                "Kritische Punkte blockieren eine finale Freigabe. Review-PDFs bleiben als Entwurf möglich.",
                                                                                className="selection-summary",
                                                                            ),
                                                                            grid("chemistry-sdb-review-issues-grid", SDS_REVIEW_ISSUE_COLUMNS, height="442px", row_selection="multiple"),
                                                                            html.Div(
                                                                                [
                                                                                    html.Button("Alle sicheren Auto-Fixes anwenden", id="chemistry-sdb-review-autofix-button"),
                                                                                    html.Button("Issue ignorieren", id="chemistry-sdb-review-ignore-button"),
                                                                                    html.Button("Issue als geprüft markieren", id="chemistry-sdb-review-check-button"),
                                                                                    html.Button("Menschliche Prüfung erforderlich", id="chemistry-sdb-review-needs-review-button"),
                                                                                ],
                                                                                className="button-row",
                                                                                style={"marginTop": "10px"},
                                                                            ),
                                                                        ],
                                                                        className="panel",
                                                                        style={"marginTop": "12px"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("Ausgewählter Dokumenttitel"), dcc.Input(id="chemistry-sdb-document-edit-title", style={"width": "100%"})]),
                                                                            html.Div([html.Label("Dokumenttext / KI-Entwurf"), dcc.Textarea(id="chemistry-sdb-document-edit-text", style={"width": "100%", "height": "260px"})], style={"gridColumn": "1 / -1"}),
                                                                            html.Button("Dokumenttext speichern", id="chemistry-sdb-document-save-button"),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"marginTop": "10px", "gridTemplateColumns": "repeat(auto-fit, minmax(240px, 1fr))"},
                                                                    ),
                                                                    html.H4("SDB-Übersetzung / regionale Version erstellen"),
                                                                    html.Div(
                                                                        "Hier entsteht eine neue Dokument-Version. Sie erscheint danach oben in der Versionen-Tabelle und kann dort geprüft, als PDF erzeugt und heruntergeladen werden.",
                                                                        className="selection-summary",
                                                                        style={"marginBottom": "10px", "fontSize": "13px"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([html.Label("Ausgangsdokument"), dcc.Dropdown(id="chemistry-sdb-translation-source-document-id", clearable=False)]),
                                                                            html.Div([html.Label("Ausgangssprache"), dcc.Input(id="chemistry-sdb-translation-source-locale", value="de-CH", style={"width": "100%"})]),
                                                                            html.Div(
                                                                                [
                                                                                    html.Label("Ziel-Sprache"),
                                                                                    dcc.Dropdown(
                                                                                        id="chemistry-sdb-translation-target-locale",
                                                                                        options=[
                                                                                            {"label": "Deutsch Schweiz (de-CH)", "value": "de-CH"},
                                                                                            {"label": "Französisch Schweiz (fr-CH)", "value": "fr-CH"},
                                                                                            {"label": "Italienisch Schweiz (it-CH)", "value": "it-CH"},
                                                                                            {"label": "Deutsch Deutschland (de-DE)", "value": "de-DE"},
                                                                                            {"label": "Französisch Frankreich (fr-FR)", "value": "fr-FR"},
                                                                                            {"label": "Englisch EU (en-EU)", "value": "en-EU"},
                                                                                            {"label": "Englisch UK (en-GB)", "value": "en-GB"},
                                                                                        ],
                                                                                        value="fr-CH",
                                                                                        clearable=False,
                                                                                    ),
                                                                                ]
                                                                            ),
                                                                            html.Div(
                                                                                [
                                                                                    html.Label("Ziel-Region"),
                                                                                    dcc.Dropdown(
                                                                                        id="chemistry-sdb-translation-target-region",
                                                                                        options=[
                                                                                            {"label": "CH", "value": "CH"},
                                                                                            {"label": "DE", "value": "DE"},
                                                                                            {"label": "EU", "value": "EU"},
                                                                                            {"label": "FR", "value": "FR"},
                                                                                            {"label": "GB", "value": "GB"},
                                                                                        ],
                                                                                        value="CH",
                                                                                        clearable=False,
                                                                                    ),
                                                                                ]
                                                                            ),
                                                                            html.Div([html.Label("Prompt"), dcc.Dropdown(id="chemistry-sdb-translation-prompt-id", clearable=False)]),
                                                                        ],
                                                                        className="form-grid",
                                                                        style={"gridTemplateColumns": "repeat(auto-fit, minmax(180px, 260px))", "justifyContent": "start"},
                                                                    ),
                                                                    html.Div(
                                                                        [
                                                                            html.Button("Neue Übersetzungs-Version erstellen", id="chemistry-sdb-translation-generate-button"),
                                                                            html.Button("Neue regionale Entwurfs-Version erstellen", id="chemistry-sdb-region-draft-generate-button"),
                                                                        ],
                                                                        className="button-row",
                                                                        style={"marginTop": "10px"},
                                                                    ),
                                                                    html.Div(id="chemistry-sdb-translation-status", className="selection-summary", style={"marginTop": "10px"}),
                                                                    html.H4("Übersetzungs-Prompts"),
                                                                    html.Div(
                                                                        [
                                                                            html.Div([grid("chemistry-sdb-prompts-grid", SDB_TRANSLATION_PROMPT_COLUMNS, height="210px", row_selection="single")], style={"minWidth": "320px"}),
                                                                            html.Div(
                                                                                [
                                                                                    html.Div([html.Label("Prompt-ID"), dcc.Input(id="chemistry-sdb-prompt-id", type="number", disabled=True, style={"width": "100%"})]),
                                                                                    html.Div([html.Label("Name"), dcc.Input(id="chemistry-sdb-prompt-name", style={"width": "100%"})]),
                                                                                    html.Div([html.Label("Quell-Locale"), dcc.Input(id="chemistry-sdb-prompt-source-locale", style={"width": "100%"})]),
                                                                                    html.Div([html.Label("Ziel-Locale"), dcc.Input(id="chemistry-sdb-prompt-target-locale", style={"width": "100%"})]),
                                                                                    html.Div([html.Label("Ziel-Region"), dcc.Input(id="chemistry-sdb-prompt-target-region", style={"width": "100%"})]),
                                                                                    html.Div([html.Label("Aktiv"), dcc.Dropdown(id="chemistry-sdb-prompt-active", options=BOOLEAN_OPTIONS, value=True, clearable=False)]),
                                                                                    html.Div([html.Label("System Prompt"), dcc.Textarea(id="chemistry-sdb-prompt-system", value=DEFAULT_SDB_SYSTEM_PROMPT, style={"width": "100%", "height": "90px"})], style={"gridColumn": "1 / -1"}),
                                                                                    html.Div([html.Label("User Prompt Template"), dcc.Textarea(id="chemistry-sdb-prompt-template", value=DEFAULT_SDB_USER_PROMPT_TEMPLATE, style={"width": "100%", "height": "220px"})], style={"gridColumn": "1 / -1"}),
                                                                                    html.Div(
                                                                                        [
                                                                                            html.Button("Neuen SDB-Prompt erstellen", id="chemistry-sdb-prompt-new-button"),
                                                                                            html.Button("SDB-Prompt speichern", id="chemistry-sdb-prompt-save-button"),
                                                                                        ],
                                                                                        className="button-row",
                                                                                    ),
                                                                                ],
                                                                                className="form-grid",
                                                                                style={"gridTemplateColumns": "repeat(auto-fit, minmax(160px, 1fr))"},
                                                                            ),
                                                                        ],
                                                                        className="translation-row",
                                                                    ),
                                                                    html.Div(id="chemistry-sdb-prompt-status", className="selection-summary", style={"marginTop": "10px"}),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("SDB-Protokoll"),
                                                                    html.Div(id="chemistry-sdb-protocol"),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(
                                                                [
                                                                    html.H4("Gespeicherte LLM-Läufe"),
                                                                    html.Div(id="chemistry-sdb-llm-runs"),
                                                                ],
                                                                className="panel",
                                                                style={"marginTop": "12px"},
                                                            ),
                                                            html.Div(id="chemistry-sdb-pdf-link", style={"marginTop": "12px"}),
                                                        ],
                                                        className="panel",
                                                        style={"marginTop": "12px"},
                                                    )
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Chemie speichern", id="chemistry-save-button"),
                                            html.Button("Zum Produkt", id="chemistry-open-product-button"),
                                        ],
                                        className="button-row",
                                        style={"marginTop": "12px"},
                                    ),
                                ],
                                className="panel",
                                style={"marginTop": "16px"},
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="variants",
                        label="Varianten",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Label("Variantenstatus anzeigen"),
                                    dcc.Dropdown(
                                        id="variant-list-status-filter",
                                        options=[
                                            {"label": "Aktive Varianten", "value": "active"},
                                            {"label": "Archivierte Varianten", "value": "archived"},
                                            {"label": "Alle Varianten", "value": "all"},
                                        ],
                                        value="active",
                                        clearable=False,
                                    ),
                                ],
                                style={"maxWidth": "320px", "marginBottom": "12px"},
                            ),
                            html.Div(
                                [
                                    html.Div(id="variant-channel-action-count", style={"fontWeight": "600"}),
                                    html.Div(
                                        [
                                            html.Button("Variante bearbeiten", id="variant-edit-selected-button"),
                                            html.Button("Markierte Varianten bearbeiten", id="variant-bulk-edit-open-button"),
                                            html.Button("Kanal-Aktionen", id="variant-channel-action-open-button"),
                                            html.Button("Varianten-Listings", id="variant-listings-action-open-button"),
                                            html.Button("Ausgewählte Varianten archivieren", id="variant-archive-selected-button"),
                                            html.Button("Ausgewählte Varianten löschen", id="variant-delete-selected-button"),
                                            html.Button("Auswahl aufheben", id="variant-clear-context-selection-button"),
                                        ],
                                        className="button-row",
                                    ),
                                ],
                                id="variant-channel-actions",
                                className="selection-summary",
                                style={"display": "block"},
                            ),
                            grid("variants-grid", VARIANT_COLUMNS, height="520px", row_selection="multiple"),
                            html.Div(id="variant-selection-summary", className="selection-summary"),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.H4("Staffelpreis anlegen/aktualisieren"),
                                            html.Div(
                                                [
                                                    dcc.Dropdown(
                                                        id="tier-price-type",
                                                        options=[
                                                            {"label": "sale", "value": "sale"},
                                                            {"label": "purchase", "value": "purchase"},
                                                        ],
                                                        value="sale",
                                                    ),
                                                    dcc.Input(id="tier-min-qty", type="number", placeholder="Min. Menge", value=1),
                                                    dcc.Input(id="tier-max-qty", type="number", placeholder="Max. Menge"),
                                                    dcc.Input(id="tier-price", type="number", placeholder="Preis"),
                                                    dcc.Input(id="tier-currency", placeholder="Währung", value="EUR"),
                                                    html.Button("Staffelpreis speichern", id="tier-save-button"),
                                                ],
                                                className="form-grid",
                                            ),
                                        ],
                                        className="panel",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("ID"),
                                                            dcc.Input(id="variant-id", type="number", placeholder="ID", disabled=True, style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Variantentitel"),
                                                            dcc.Input(id="variant-title", placeholder="Variantentitel", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                ],
                                                className="variant-form-row variant-form-row--wide",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Attribut"),
                                                            dcc.Input(id="variant-option-name", placeholder="Attribut", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Wert"),
                                                            dcc.Input(id="variant-option-value", placeholder="Wert", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Packaging"),
                                                            dcc.Input(id="variant-packaging", placeholder="Packaging", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                ],
                                                className="variant-form-row",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Preis"),
                                                            dcc.Input(id="variant-price", type="number", placeholder="Preis", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Einkaufspreis"),
                                                            dcc.Input(id="variant-cost-price", type="number", placeholder="Einkaufspreis", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Währung"),
                                                            dcc.Input(id="variant-currency", placeholder="Währung", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("EK-Währung"),
                                                            dcc.Input(id="variant-cost-currency", placeholder="EK-Währung", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                ],
                                                className="variant-form-row",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Bestand"),
                                                            dcc.Input(id="variant-stock", type="number", placeholder="Bestand", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Barcode"),
                                                            dcc.Input(id="variant-barcode", placeholder="Barcode", style={"width": "100%"}),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Aktion"),
                                                            html.Button("Variante speichern", id="variant-save-button"),
                                                        ]
                                                    ),
                                                ],
                                                className="variant-form-row variant-form-row--compact",
                                            ),
                                        ],
                                        className="variant-form-card",
                                    ),
                                ],
                                className="variant-editor-layout",
                            ),
                            html.Div(
                                [
                                    html.H4("Preis- und Staffelpreise"),
                                    grid("variant-tier-grid", DETAIL_TIER_COLUMNS, height="320px"),
                                ],
                                className="panel",
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="categories",
                        label="Kanal-Kategorien",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.H3("Kanal-Kategorienbaum"),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Kanal"),
                                                    dcc.Dropdown(id="categories-sales-channel-code", value="voxster", clearable=False),
                                                ],
                                                style={"maxWidth": "340px"},
                                            )
                                        ],
                                        className="selection-summary",
                                    ),
                                    html.Div(
                                        "Die Kanal-Kategorien werden direkt im bearbeitbaren Grid als Menübaum mit Einrückung und Expand/Collapse dargestellt.",
                                        className="category-tree-description",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("Kategorienbaum"),
                                                    grid(
                                                        "categories-grid",
                                                        CATEGORY_COLUMNS,
                                                        height="520px",
                                                        extra_grid_options={"pagination": False, "animateRows": True},
                                                    ),
                                                ],
                                                className="panel",
                                            ),
                                            html.Div(
                                                [
                                                    html.H4("Produkte dieser Kategorie"),
                                                    html.Div(id="category-products-status", className="selection-summary"),
                                                    html.Div(id="category-breadcrumb", className="category-tree-description"),
                                                    grid("category-products-grid", CHANNEL_CATEGORY_PRODUCT_COLUMNS, height="520px"),
                                                ],
                                                className="panel",
                                            ),
                                        ],
                                        className="detail-columns",
                                    ),
                                ],
                                className="panel",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Name"),
                                                    dcc.Input(id="category-name", placeholder="Name", style={"width": "100%"}),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Parent"),
                                                    dcc.Dropdown(id="category-parent-id", placeholder="Parent"),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Sprache"),
                                                    dcc.Dropdown(
                                                        id="category-language-code",
                                                        options=LANGUAGE_CODE_OPTIONS,
                                                        placeholder="Sprache wählen",
                                                        clearable=False,
                                                        value="de",
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="category-form-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Sortierung"),
                                                    dcc.Input(id="category-sort-order", type="number", placeholder="Sortierung", value=0, style={"width": "100%"}),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                            html.Label("Aktion"),
                                                            html.Button("Kanal-Kategorie anlegen", id="category-create-button"),
                                                ]
                                            ),
                                        ],
                                        className="category-form-row category-form-row--compact",
                                    ),
                                ],
                                className="category-form-card",
                            ),
                            html.Div(
                                [
                                    html.H4("Kanal-Kategorie bearbeiten"),
                                    html.Div(id="category-detail-summary", className="selection-summary"),
                                    dcc.Input(id="category-detail-id", type="number", disabled=True, style={"display": "none"}),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Name"),
                                                    dcc.Input(id="category-detail-name", placeholder="Name", style={"width": "100%"}),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Parent"),
                                                    dcc.Dropdown(id="category-detail-parent-id", placeholder="Parent"),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Sprache"),
                                                    dcc.Dropdown(id="category-detail-language-code", options=LANGUAGE_CODE_OPTIONS, placeholder="Sprache wählen", clearable=False),
                                                ]
                                            ),
                                        ],
                                        className="category-form-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.Label("Sortierung"),
                                                    dcc.Input(id="category-detail-sort-order", type="number", placeholder="Sortierung", style={"width": "100%"}),
                                                ]
                                            ),
                                            html.Div(
                                                [
                                                    html.Label("Aktionen"),
                                                    html.Div(
                                                        [
                                                            html.Button("Kategorie speichern", id="category-save-button"),
                                                            html.Button("Kategorie löschen", id="category-delete-button", className="danger-button"),
                                                        ],
                                                        className="button-row",
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="category-form-row category-form-row--compact",
                                    ),
                                    dcc.ConfirmDialog(id="category-delete-confirm", message="Kategorie wirklich löschen?"),
                                ],
                                className="category-form-card",
                            ),
                        ],
                    ),
                    dcc.Tab(
                        value="sales-channels",
                        label="Vertriebskanäle",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    grid("sales-channels-grid", SALES_CHANNEL_COLUMNS, height="300px"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("Bestehender Kanal"), dcc.Dropdown(id="sales-channel-form-id", placeholder="Zum Bearbeiten auswählen")]),
                                            html.Div([html.Label("Code"), dcc.Input(id="sales-channel-form-code", placeholder="z. B. voxster")]),
                                            html.Div([html.Label("Name"), dcc.Input(id="sales-channel-form-name", placeholder="Anzeigename")]),
                                            html.Div([html.Label("Aktiv"), dcc.Dropdown(id="sales-channel-form-is-active", options=BOOLEAN_OPTIONS, value=True, clearable=False)]),
                                            html.Div([html.Label("Sortierung"), dcc.Input(id="sales-channel-form-sort-order", type="number", value=0)]),
                                            html.Div([html.Label("Aktion"), html.Button("Vertriebskanal speichern", id="sales-channel-save-button")]),
                                        ],
                                        className="form-grid",
                                        style={"marginTop": "16px"},
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Kanal-Export"),
                                            html.Div(
                                                [
                                                    html.Div([html.Label("Vertriebskanal"), dcc.Dropdown(id="channel-export-code", placeholder="Kanal wählen")]),
                                                    html.Div([html.Label("Sprache"), dcc.Dropdown(id="channel-export-language", options=LANGUAGE_CODE_OPTIONS, value="de-CH", placeholder="Sprache")]),
                                                    html.Div([html.Label("Aktion"), html.Button("Kanal-Export erzeugen", id="channel-export-run-button")]),
                                                ],
                                                className="form-grid",
                                            ),
                                            html.Div(id="channel-export-result", className="selection-summary", style={"marginTop": "12px"}),
                                        ],
                                        className="panel",
                                        style={"marginTop": "16px"},
                                    ),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="channel-categories",
                        label="Kanal-Kategorien",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div("Verwaltung externer Zielkategorien je Vertriebskanal mit Baum und zugeordneten Produkten.", className="category-tree-description"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("Vertriebskanal"), dcc.Dropdown(id="channel-category-tree-sales-channel-id", placeholder="Kanal wählen", clearable=False)]),
                                            html.Div(
                                                [
                                                    html.Label("Baum"),
                                                    html.Div(
                                                        [
                                                            html.Button("Alle aufklappen", id="channel-category-tree-expand-all-button"),
                                                            html.Button("Alle einklappen", id="channel-category-tree-collapse-all-button"),
                                                        ],
                                                        className="button-row",
                                                    ),
                                                ]
                                            ),
                                        ],
                                        className="form-grid",
                                        style={"gridTemplateColumns": "minmax(220px, 320px) minmax(220px, 320px)", "alignItems": "end"},
                                    ),
                                    html.Div(id="channel-category-tree-status", className="selection-summary", style={"marginTop": "12px"}),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("Kategorienbaum"),
                                                    grid(
                                                        "channel-category-tree-grid",
                                                        CHANNEL_CATEGORY_TREE_COLUMNS,
                                                        height="420px",
                                                        extra_grid_options={"pagination": False, "animateRows": True},
                                                    ),
                                                ],
                                                className="panel",
                                            ),
                                            html.Div(
                                                [
                                                    html.H4("Produkte dieser Kategorie"),
                                                    html.Div(id="channel-category-products-status", className="selection-summary"),
                                                    html.Div(id="channel-category-breadcrumb", className="category-tree-description"),
                                                    grid("channel-category-products-grid", CHANNEL_CATEGORY_PRODUCT_COLUMNS, height="420px"),
                                                ],
                                                className="panel",
                                            ),
                                        ],
                                        className="detail-columns",
                                        style={"marginTop": "12px"},
                                    ),
                                    html.H4("Alle externen Kanal-Kategorien"),
                                    grid("channel-categories-grid", CHANNEL_CATEGORY_COLUMNS, height="260px"),
                                    html.Div(
                                        [
                                            html.Div([html.Label("Vertriebskanal"), dcc.Dropdown(id="channel-category-form-sales-channel-id", placeholder="Kanal")]),
                                            html.Div([html.Label("Externe Kategorie-ID"), dcc.Input(id="channel-category-form-external-id", placeholder="Externe ID")]),
                                            html.Div([html.Label("Name"), dcc.Input(id="channel-category-form-name", placeholder="Name")]),
                                            html.Div([html.Label("Pfad"), dcc.Input(id="channel-category-form-path", placeholder="Externer Pfad")]),
                                            html.Div([html.Label("Pflichtattribute JSON"), dcc.Textarea(id="channel-category-form-required-attributes", style={"width": "100%", "height": "80px"})]),
                                            html.Div([html.Label("Aktiv"), dcc.Dropdown(id="channel-category-form-is-active", options=BOOLEAN_OPTIONS, value=True, clearable=False)]),
                                            html.Div([html.Label("Aktion"), html.Button("Kanal-Kategorie speichern", id="channel-category-save-button")]),
                                        ],
                                        className="form-grid",
                                        style={"marginTop": "16px"},
                                    ),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="assets",
                        label="Assets",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Div(id="assets-bulk-count", style={"fontWeight": "600"}),
                                            html.Div(
                                                [
                                                    html.Button("Auswahl aufheben", id="assets-clear-selection-button"),
                                                    html.Button("Ausgewählte lokale Assets nach R2 hochladen", id="assets-send-to-uploader-button"),
                                                    html.Button("Ausgewählte Assets löschen", id="assets-bulk-delete-button", className="danger-button"),
                                                    html.Button("Asset-Typ ändern", id="assets-bulk-type-button", disabled=True, title="TODO: Bulk-Metadatenänderung"),
                                                    html.Button("Produkt verknüpfen", id="assets-bulk-product-button", disabled=True, title="TODO: Bulk-Produktverknüpfung"),
                                                    html.Button("Sprache setzen", id="assets-bulk-language-button", disabled=True, title="TODO: Bulk-Sprache setzen"),
                                                    html.Button("Status ändern", id="assets-bulk-status-button", disabled=True, title="TODO: Bulk-Status ändern"),
                                                    html.Button("Links kopieren", id="assets-bulk-copy-links-button", disabled=True, title="TODO: Link-Export/Kopieren"),
                                                ],
                                                className="button-row",
                                            ),
                                        ],
                                        id="assets-bulk-actions",
                                        className="selection-summary",
                                        style={"display": "none"},
                                    ),
                                    html.Div(
                                        [
                                            html.Button("R2-Speicher Conf", id="r2-config-toggle-button"),
                                            html.Span(
                                                "Cloudflare-R2-Konfiguration anzeigen oder ausblenden.",
                                                style={"marginLeft": "10px", "color": "#64748b"},
                                            ),
                                        ],
                                        className="button-row",
                                        style={"marginBottom": "10px"},
                                    ),
                                    html.Div(
                                        [
                                            html.H3("R2 Speicher", style={"marginTop": "0"}),
                                            html.H4("Konfiguration"),
                                            html.Div(
                                                [
                                                    html.Div([html.Label("Aktiviert"), dcc.Dropdown(id="r2-config-enabled", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                                    html.Div(
                                                        [
                                                            html.Label("Storage Provider"),
                                                            dcc.Dropdown(
                                                                id="r2-config-provider",
                                                                options=[
                                                                    {"label": "Cloudflare R2 / S3", "value": "cloudflare_r2"},
                                                                    {"label": "Bunny Storage", "value": "bunny_storage"},
                                                                ],
                                                                value=DEFAULT_R2_PROVIDER,
                                                                clearable=False,
                                                            ),
                                                        ]
                                                    ),
                                                    html.Div([html.Label("Endpoint"), dcc.Input(id="r2-config-endpoint", value=DEFAULT_R2_ENDPOINT, style={"width": "100%"})]),
                                                    html.Div([html.Label("Bucket"), dcc.Input(id="r2-config-bucket", value=DEFAULT_R2_BUCKET)]),
                                                    html.Div([html.Label("Region"), dcc.Input(id="r2-config-region", value=DEFAULT_R2_REGION)]),
                                                ],
                                                className="form-grid",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Access Key ID setzen / ersetzen"),
                                                            dcc.Input(id="r2-config-access-key-id", type="password", placeholder="Leer lassen, um bestehenden Wert zu behalten", style={"width": "100%"}),
                                                            html.Div(id="r2-config-access-key-status", className="hint"),
                                                        ]
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Secret Access Key setzen / ersetzen"),
                                                            dcc.Input(id="r2-config-secret-key", type="password", placeholder="Leer lassen, um Secret beizubehalten", style={"width": "100%"}),
                                                            html.Div(id="r2-config-secret-status", className="hint"),
                                                        ]
                                                    ),
                                                    html.Div([html.Label("Public Base URL optional"), dcc.Input(id="r2-config-public-base-url", placeholder="z. B. https://media.voxster.ch", style={"width": "100%"})]),
                                                    html.Div([html.Label("Pfad-Präfix"), dcc.Input(id="r2-config-path-prefix", value=DEFAULT_R2_PATH_PREFIX)]),
                                                    html.Div([html.Label("Standard-Speicherklasse optional"), dcc.Input(id="r2-config-storage-class", placeholder="optional")]),
                                                    html.Div([html.Label("Upload max. Dateigrösse in MB"), dcc.Input(id="r2-config-max-upload-size-mb", type="number", value=50, min=1)]),
                                                ],
                                                className="form-grid",
                                                style={"marginTop": "10px"},
                                            ),
                                            html.Div(
                                                [
                                                    html.Div([html.Label("Erlaubte Dateitypen"), dcc.Textarea(id="r2-config-allowed-file-types", style={"width": "100%", "height": "70px"})]),
                                                    html.Div([html.Label("Notizen / Beschreibung"), dcc.Textarea(id="r2-config-notes", style={"width": "100%", "height": "70px"})]),
                                                ],
                                                className="detail-columns",
                                                style={"marginTop": "10px"},
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("R2-Konfiguration speichern", id="r2-config-save-button"),
                                                    html.Button("Verbindung testen", id="r2-config-test-button"),
                                                    html.Span(
                                                        "Wenn keine Public Base URL gesetzt ist, werden Assets hochgeladen, aber nur über interne Download-/Signed-URLs geöffnet.",
                                                        style={"color": "#64748b", "marginLeft": "10px"},
                                                    ),
                                                ],
                                                className="button-row",
                                                style={"marginTop": "10px"},
                                            ),
                                            html.Div(id="r2-config-status", className="selection-summary", style={"marginTop": "10px"}),
                                        ],
                                        id="r2-config-panel",
                                        className="panel",
                                        style={"display": "none", "marginBottom": "16px"},
                                    ),
                                    html.Div(
                                        [
                                            html.H3("Asset Uploader", style={"marginTop": "0"}),
                                            html.Div(
                                                [
                                                    html.Div([html.Label("Asset-Typ"), dcc.Dropdown(id="r2-asset-type", options=ASSET_TYPE_OPTIONS, value="product_image", clearable=False)]),
                                                    html.Div([html.Label("Produkt-ID optional"), dcc.Input(id="r2-product-id", type="number", placeholder="z. B. 1420")]),
                                                    html.Div([html.Label("Sprache optional"), dcc.Dropdown(id="r2-language-code", options=LANGUAGE_CODE_OPTIONS, placeholder="z. B. de-CH")]),
                                                    html.Div([html.Label("Titel optional"), dcc.Input(id="r2-asset-title", placeholder="Titel")]),
                                                ],
                                                className="form-grid",
                                            ),
                                            html.Div([html.Label("Beschreibung optional"), dcc.Textarea(id="r2-asset-description", style={"width": "100%", "height": "80px"})], style={"marginTop": "10px"}),
                                            dcc.Upload(
                                                id="r2-asset-upload",
                                                children=html.Div(["Dateien hierher ziehen oder ", html.A("auswählen")]),
                                                multiple=True,
                                                style={
                                                    "width": "100%",
                                                    "height": "86px",
                                                    "lineHeight": "86px",
                                                    "borderWidth": "1px",
                                                    "borderStyle": "dashed",
                                                    "borderRadius": "10px",
                                                    "textAlign": "center",
                                                    "marginTop": "12px",
                                                    "background": "#f8fafc",
                                                },
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("Upload zu Object Storage starten", id="r2-asset-upload-button"),
                                                    html.Span(
                                                        "Dieser Button lädt neu ausgewählte Dateien aus dem Datei-Dropzone-Feld hoch.",
                                                        style={"marginLeft": "10px", "color": "#64748b"},
                                                    ),
                                                ],
                                                className="button-row",
                                                style={"marginTop": "10px"},
                                            ),
                                            html.Div(id="r2-asset-upload-result", className="selection-summary", style={"marginTop": "12px"}),
                                        ],
                                        className="panel",
                                        style={"marginBottom": "16px"},
                                    ),
                                    grid(
                                        "assets-grid",
                                        ASSET_COLUMNS,
                                        height="540px",
                                        row_selection="multiple",
                                        extra_grid_options={
                                            "rowSelection": {
                                                "mode": "multiRow",
                                                "checkboxes": True,
                                                "headerCheckbox": True,
                                                "selectAll": "currentPage",
                                                "enableClickSelection": True,
                                            }
                                        },
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Alle sichtbaren Assets auswählen", id="assets-select-visible-button"),
                                            html.Button("Alle sichtbaren Assets abwählen", id="assets-deselect-visible-button"),
                                            html.Span("Betrifft die aktuell geladene/gefilterte Ansicht.", style={"marginLeft": "10px", "color": "#64748b"}),
                                            html.Button("Markierte Assets löschen", id="assets-visible-delete-button", className="danger-button"),
                                        ],
                                        className="button-row",
                                        style={"marginTop": "8px"},
                                    ),
                                    html.Div(id="assets-detail-preview", style={"marginTop": "16px"}),
                                    dcc.ConfirmDialog(id="assets-bulk-delete-confirm"),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="jobs",
                        label="Importjobs",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            grid("jobs-grid", IMPORT_COLUMNS, height="420px"),
                            html.H4("PIM-Import starten"),
                            dcc.Upload(
                                id="import-clean-upload",
                                children=html.Div(["Clean-Datei hier ablegen oder klicken"]),
                                style={
                                    "width": "100%",
                                    "height": "60px",
                                    "lineHeight": "60px",
                                    "borderWidth": "1px",
                                    "borderStyle": "dashed",
                                    "borderRadius": "4px",
                                    "textAlign": "center",
                                    "marginBottom": "12px",
                                },
                            ),
                            html.Div(
                                [
                                    dcc.Input(id="import-clean-file", placeholder="Pfad zu products_clean.csv/.xlsx"),
                                    dcc.Input(id="import-source-name", placeholder="Source Name"),
                                    dcc.Input(id="import-mapping-config", placeholder="Mapping Config", value="config.pim_import.yaml"),
                                    dcc.Dropdown(id="import-sales-channel-code", value="voxster", clearable=False, placeholder="Vertriebskanal"),
                                    dcc.Checklist(
                                        id="import-dry-run",
                                        options=[{"label": "Dry-Run", "value": "dry"}],
                                        value=[],
                                    ),
                                    html.Button("Import starten", id="import-run-button"),
                                ],
                                className="form-grid",
                            ),
                            html.Div(id="import-status", className="flash flash-inline"),
                            html.H4("Website-Crawler für markierte Produkte / Varianten"),
                            html.Div(
                                [
                                    html.Button("Website-Crawler für Produkte", id="open-product-enrich-modal-button"),
                                    html.Button("Website-Crawler für Varianten", id="open-variant-enrich-modal-button"),
                                ],
                                className="button-row",
                            ),
                            html.Div(
                                "Spezialwerkzeug für Resolver/Crawling. Für normale Produkttexte bevorzugt im Menü Produkte die sichere Funktion 'Fehlende Produktdaten anreichern' verwenden.",
                                className="form-hint",
                            ),
                            html.H4("Website-Anreicherung starten"),
                            html.Div(
                                [
                                    dcc.Input(id="enrich-seed-url", placeholder="Start-URL, z. B. https://tintolav.com/"),
                                    dcc.Input(id="enrich-supplier-name", placeholder="Lieferantenname", value="Tintolav"),
                                    dcc.Dropdown(
                                        id="enrich-resolver-mode",
                                        options=[
                                            {"label": "Generischer Crawl", "value": "generic_crawl"},
                                            {"label": "Tintolav Katalog-Resolver", "value": "tintolav_catalog"},
                                        ],
                                        value="tintolav_catalog",
                                        placeholder="Resolver",
                                    ),
                                    dcc.Input(
                                        id="enrich-listing-url",
                                        placeholder="Resolver Listing-URL",
                                        value="https://www.tintolav.com/en/products/tintolav/product/listing.html",
                                    ),
                                    dcc.Input(id="enrich-max-pages", type="number", placeholder="Max. Seiten", value=80),
                                    dcc.Checklist(
                                        id="enrich-options",
                                        options=[
                                            {"label": "Nur leere Felder fuellen", "value": "only_empty"},
                                            {"label": "Beschreibung aktualisieren", "value": "description"},
                                            {"label": "Assets holen", "value": "assets"},
                                            {"label": "Packaging uebernehmen", "value": "packaging"},
                                            {"label": "Spezifikationen uebernehmen", "value": "specifications"},
                                            {"label": "Technische Merkmale uebernehmen", "value": "technical"},
                                            {"label": "Source-URLs aktualisieren", "value": "source_urls"},
                                        ],
                                        value=[
                                            "only_empty",
                                            "description",
                                            "assets",
                                            "packaging",
                                            "specifications",
                                            "technical",
                                            "source_urls",
                                        ],
                                    ),
                                    html.Button("Anreicherung starten", id="enrich-run-button"),
                                    html.Button("Markierte Produkte + Varianten anreichern", id="enrich-selected-run-button"),
                                ],
                                className="form-grid",
                            ),
                            html.Div(id="combined-selection-summary", className="selection-summary"),
                            html.Div(id="enrich-status", className="selection-summary"),
                        ],
                    ),
                    dcc.Tab(
                        value="attributes",
                        label="Attribute",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[grid("attributes-grid", ATTRIBUTE_COLUMNS, height="520px")],
                    ),
                    dcc.Tab(
                        value="families",
                        label="Familien",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[grid("families-grid", FAMILY_COLUMNS, height="520px")],
                    ),
                    dcc.Tab(
                        value="translations",
                        label="Übersetzungen",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div([html.H4("Sprachen"), grid("languages-grid", LANGUAGE_COLUMNS, height="220px")], className="panel"),
                                    html.Div([html.H4("Produkt-Übersetzungen"), grid("translations-grid", TRANSLATION_COLUMNS, height="260px")], className="panel"),
                                    html.Div([html.H4("Varianten-Übersetzungen"), grid("variant-translations-grid", VARIANT_TRANSLATION_COLUMNS, height="260px")], className="panel"),
                                ],
                                className="detail-columns",
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="rules",
                        label="Regeln / Anreicherung",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.H3("Regeln / Anreicherung / Preisregeln"),
                                    grid("rules-grid", RULE_COLUMNS, height="420px"),
                                    html.Div(
                                        [
                                            html.H4("Produktdaten-Webanreicherung"),
                                            html.P(
                                                "Gezielte Dry-Run-Suche für fehlende Produkttexte. "
                                                "Quelle ist zuerst Final URL/Quell-URL am Produkt; konfigurierte Domains werden als nachvollziehbare Suchhinweise protokolliert."
                                            ),
                                            html.Div(
                                                [
                                                    dcc.Input(
                                                        id="rules-product-enrichment-product-ids",
                                                        placeholder="Produkt-IDs optional, z. B. 1, 1404. Leer = markierte Produkte.",
                                                    ),
                                                    dcc.Dropdown(
                                                        id="rules-product-enrichment-action",
                                                        options=[
                                                            {"label": "Alle fehlenden Texte suchen", "value": "missing_texts"},
                                                            {"label": "Produktbeschreibung im Web suchen", "value": "description"},
                                                            {"label": "Kurzbeschreibung im Web suchen", "value": "short_description"},
                                                            {"label": "Langbeschreibung im Web suchen", "value": "description"},
                                                            {"label": "SEO-Titel im Web suchen", "value": "seo_title"},
                                                            {"label": "SEO-Beschreibung im Web suchen", "value": "seo_description"},
                                                            {"label": "Technische Daten im Web suchen", "value": "technical"},
                                                            {"label": "Anreicherung mit Vorschau starten", "value": "preview"},
                                                        ],
                                                        value="missing_texts",
                                                        clearable=False,
                                                    ),
                                                    dcc.Dropdown(
                                                        id="rules-product-enrichment-language",
                                                        options=[
                                                            {"label": "Produkt-Originalsprache verwenden", "value": "product_source"},
                                                            {"label": "de-CH", "value": "de-CH"},
                                                            {"label": "de-DE", "value": "de-DE"},
                                                            {"label": "fr-FR", "value": "fr-FR"},
                                                            {"label": "it-IT", "value": "it-IT"},
                                                            {"label": "en-GB", "value": "en-GB"},
                                                        ],
                                                        value="product_source",
                                                        clearable=False,
                                                    ),
                                                    dcc.Dropdown(
                                                        id="rules-product-enrichment-overwrite",
                                                        options=BOOLEAN_OPTIONS,
                                                        value=False,
                                                        clearable=False,
                                                        placeholder="Bestehende Werte überschreiben",
                                                    ),
                                                ],
                                                className="form-grid",
                                            ),
                                            html.Div(
                                                [
                                                    html.Div(
                                                        [
                                                            html.Label("Felder"),
                                                            dcc.Checklist(
                                                                id="rules-product-enrichment-fields",
                                                                options=[
                                                                    {"label": "Titel", "value": "title"},
                                                                    {"label": "Kurzbeschreibung", "value": "short_description"},
                                                                    {"label": "Beschreibung", "value": "description"},
                                                                    {"label": "SEO-Titel", "value": "seo_title"},
                                                                    {"label": "SEO-Beschreibung", "value": "seo_description"},
                                                                    {"label": "Slug / Handle", "value": "slug"},
                                                                    {"label": "Technische Daten", "value": "technical_features_text"},
                                                                    {"label": "Spezifikationen", "value": "specifications_text"},
                                                                    {"label": "Final URL / Referenz-URL", "value": "source_url_final"},
                                                                ],
                                                                value=["short_description", "description", "seo_title", "seo_description"],
                                                                inline=True,
                                                            ),
                                                        ],
                                                        className="panel",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Label("Quellen"),
                                                            dcc.Checklist(
                                                                id="rules-product-enrichment-sources",
                                                                options=[
                                                                    {"label": "Final URL", "value": "final_url"},
                                                                    {"label": "Quell-URL", "value": "source_url"},
                                                                    {"label": "Konfigurierte Domains", "value": "configured_domains"},
                                                                ],
                                                                value=["final_url", "source_url", "configured_domains"],
                                                                inline=True,
                                                            ),
                                                            html.Small("Konfigurierte Domains: " + ", ".join(DEFAULT_DOMAINS)),
                                                        ],
                                                        className="panel",
                                                    ),
                                                ],
                                                className="detail-columns",
                                            ),
                                            html.Div(
                                                [
                                                    html.Button("Dry Run / Vorschau starten", id="rules-product-enrichment-preview-button"),
                                                    html.Button("Ausgewählte übernehmen", id="rules-product-enrichment-apply-selected-button"),
                                                    html.Button("Alle sicheren Vorschläge übernehmen", id="rules-product-enrichment-apply-safe-button"),
                                                    html.Button("Abbrechen / Vorschau leeren", id="rules-product-enrichment-clear-button"),
                                                ],
                                                className="button-row",
                                            ),
                                            dcc.Loading(html.Div(id="rules-product-enrichment-status", className="selection-summary"), type="default"),
                                            grid("rules-product-enrichment-suggestions-grid", PRODUCT_ENRICHMENT_SUGGESTION_COLUMNS, height="360px", row_selection="multiple"),
                                            html.Div(id="rules-product-enrichment-warnings", className="selection-summary", style={"marginTop": "12px"}),
                                        ],
                                        className="panel",
                                        style={"marginTop": "16px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("Anreicherung"),
                                                    html.P("Website-Anreicherung arbeitet mit Resolvern, Lieferanten-URL, Feldoptionen und markierten Datensätzen."),
                                                ],
                                                className="panel",
                                            ),
                                            html.Div(
                                                [
                                                    html.H4("Preisregeln"),
                                                    html.P("Verkaufspreise, Einkaufspreise, Staffelpreise und Margen werden auf Variantenebene zusammengeführt."),
                                                ],
                                                className="panel",
                                            ),
                                        ],
                                        className="detail-columns",
                                    ),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="dedupe",
                        label="Dubletten / Produkt-Merge",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.H3("Dubletten / Produkt-Merge"),
                                    html.P(
                                        "Nicht-destruktive Dublettenverwaltung: erst Scan und Dry-Run, danach kontrollierter Merge. "
                                        "Dubletten werden archiviert und mit merged_into_product_id verknüpft, nicht gelöscht."
                                    ),
                                    html.Div(
                                        [
                                            html.Div([html.Label("Status"), dcc.Dropdown(id="dedupe-status-filter", options=[{"label": label, "value": label} for label in ["open", "reviewed", "merged", "ignored", "conflict", "error"]], placeholder="Alle")]),
                                            html.Div([html.Label("Min. Confidence"), dcc.Dropdown(id="dedupe-confidence-filter", options=[{"label": "Alle", "value": ""}, {"label": ">= 90 % sicher", "value": "90"}, {"label": ">= 70 % mittel", "value": "70"}, {"label": ">= 1 % alle", "value": "1"}], value="1", clearable=False)]),
                                            html.Div([html.Label("Quelle"), dcc.Input(id="dedupe-source-filter", placeholder="rule, import, TintoLove ...", style={"width": "100%"})]),
                                            html.Div([html.Label("Suche"), dcc.Input(id="dedupe-query-filter", placeholder="Produktname, SKU, Family Key", style={"width": "100%"})]),
                                        ],
                                        className="form-grid",
                                    ),
                                    html.Div(
                                        [
                                            dcc.Checklist(
                                                id="dedupe-extra-filters",
                                                options=[
                                                    {"label": "Nur offene Gruppen", "value": "open"},
                                                    {"label": "Nur mit Konflikten", "value": "conflicts"},
                                                    {"label": "Nur sichere Gruppen >= 90 %", "value": "safe"},
                                                ],
                                                value=["open"],
                                            )
                                        ],
                                        className="selection-summary",
                                    ),
                                    html.Div(
                                        [
                                            html.Button("Dublettenerkennung starten", id="dedupe-scan-button"),
                                            html.Button("Dry-Run / Vorschau erstellen", id="dedupe-preview-button"),
                                            html.Button("Merge bestätigen", id="dedupe-merge-button"),
                                            html.Button("Ignorieren", id="dedupe-ignore-button"),
                                        ],
                                        className="button-row",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Ignorieren-Grund"),
                                            dcc.Input(id="dedupe-ignore-reason", placeholder="Optionaler Grund", style={"width": "100%"}),
                                        ],
                                        style={"marginTop": "8px"},
                                    ),
                                    dcc.Loading(html.Div(id="dedupe-status", className="selection-summary"), type="default"),
                                    grid("dedupe-groups-grid", DEDUPLICATE_GROUP_COLUMNS, height="330px", row_selection="single"),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("Master-Produkt"),
                                                    html.Div(id="dedupe-master-detail", className="selection-summary"),
                                                    html.H4("Merge-Preview"),
                                                    html.Div(id="dedupe-preview-detail", className="selection-summary"),
                                                ],
                                                className="panel",
                                                style={"flex": "1 1 33%", "minWidth": "320px"},
                                            ),
                                            html.Div(
	                                                [
	                                                    html.Div(
	                                                        [
	                                                            html.H4("Produkte in der Gruppe", style={"margin": "0"}),
	                                                            html.Div(
	                                                                [
	                                                                    html.Button("Alle in dieser Gruppe auswählen", id="dedupe-select-group-button"),
	                                                                    html.Button("Alle in dieser Gruppe abwählen", id="dedupe-deselect-group-button"),
	                                                                    html.Button("Markiertes Produkt als Master setzen", id="dedupe-set-master-button"),
	                                                                ],
	                                                                className="button-row",
	                                                                style={"margin": "0"},
	                                                            ),
	                                                        ],
	                                                        style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "gap": "12px"},
	                                                    ),
	                                                    html.Div(
	                                                        "Produktzeilen einzeln markieren oder die Kopf-Checkbox im Grid bzw. die Gruppen-Auswahl verwenden. Es gibt kein Rechtsklick-Kontextmenü.",
	                                                        className="selection-summary",
	                                                    ),
	                                                    html.Div(id="dedupe-group-selection-status", className="selection-summary"),
	                                                    grid("dedupe-items-grid", DEDUPLICATE_ITEM_COLUMNS, height="260px", row_selection="multiple"),
	                                                ],
                                                className="panel",
                                                style={"flex": "2 1 66%", "minWidth": "620px"},
                                            ),
                                        ],
                                        className="detail-columns",
                                        style={"display": "flex", "gap": "16px", "alignItems": "stretch"},
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Konflikte"),
                                            grid("dedupe-conflicts-grid", DEDUPLICATE_CONFLICT_COLUMNS, height="260px"),
                                        ],
                                        className="panel",
                                    ),
                                    html.Div(
                                        [
                                            html.H4("Audit / Merge-Log"),
                                            html.Pre(id="dedupe-merge-log", style={"whiteSpace": "pre-wrap", "maxHeight": "260px", "overflow": "auto"}),
                                        ],
                                        className="panel",
                                    ),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="compliance-swiss",
                        label="Compliance Schweiz / SUVA",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.H3("Compliance → Schweiz → SUVA-Grenzwerte"),
                                    html.P(
                                        "Importiert die SUVA Excel-Liste 'Grenzwerte am Arbeitsplatz' als versionierte Referenzdatenbank. "
                                        "CAS-Nummern sind der primäre Match-Key; Name-/Synonym-Treffer bleiben prüfpflichtig."
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H4("SUVA-XLSX importieren"),
                                                    html.Div(
                                                        [
                                                            html.Div([html.Label("Quelle"), dcc.Input(id="suva-source-name", value="SUVA Grenzwerte am Arbeitsplatz", style={"width": "100%"})]),
                                                            html.Div([html.Label("Quell-URL optional"), dcc.Input(id="suva-source-url", placeholder="https://...", style={"width": "100%"})]),
                                                            html.Div([html.Label("Sprache"), dcc.Dropdown(id="suva-source-language", options=LANGUAGE_CODE_OPTIONS, value="de", clearable=False)]),
                                                            html.Div([html.Label("Importiert von"), dcc.Input(id="suva-imported-by", placeholder="Name / Benutzer", style={"width": "100%"})]),
                                                        ],
                                                        className="form-grid",
                                                        style={"gridTemplateColumns": "repeat(auto-fit, minmax(200px, 280px))"},
                                                    ),
                                                    html.Div([html.Label("Notizen"), dcc.Textarea(id="suva-source-notes", style={"width": "100%", "height": "70px"})], style={"marginTop": "10px"}),
                                                    dcc.Upload(
                                                        id="suva-upload",
                                                        children=html.Div(["SUVA-XLSX hierher ziehen oder auswählen"]),
                                                        multiple=False,
                                                        style={
                                                            "border": "1px dashed #94a3b8",
                                                            "borderRadius": "10px",
                                                            "padding": "22px",
                                                            "textAlign": "center",
                                                            "background": "#f8fafc",
                                                            "marginTop": "12px",
                                                        },
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Button("SUVA-Grenzwerte importieren", id="suva-import-button"),
                                                            html.Button("Importliste neu laden", id="suva-refresh-button"),
                                                        ],
                                                        className="button-row",
                                                        style={"marginTop": "12px"},
                                                    ),
                                                    dcc.Loading(html.Div(id="suva-import-status", className="selection-summary"), type="default"),
                                                ],
                                                className="panel",
                                            ),
                                            html.Div(
                                                [
                                                    html.H4("Versionierte SUVA-Importe"),
                                                    grid("suva-sources-grid", SUVA_SOURCE_COLUMNS, height="420px", row_selection="single"),
                                                    html.Div(
                                                        "Gleiche Dateien werden über SHA256 erkannt und nicht doppelt importiert.",
                                                        className="selection-summary",
                                                        style={"marginTop": "10px"},
                                                    ),
                                                ],
                                                className="panel",
                                            ),
                                        ],
                                        className="detail-columns",
                                    ),
                                ]
                            )
                        ],
                    ),
                    dcc.Tab(
                        value="medusa",
                        label="Medusa Schnittstelle",
                        style=HIDDEN_TAB_STYLE,
                        selected_style=HIDDEN_TAB_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.H3("Medusa Schnittstelle"),
                                    html.P(
                                        "Produktiver Sync über Medusa Admin REST APIs unter /admin. CSV bleibt nur Debug-/Migrationshilfe; PIM/PAM bleibt Master."
                                    ),
                                    dcc.Tabs(
                                        id="medusa-tabs",
                                        value="connection",
                                        children=[
                                            dcc.Tab(
                                                label="Verbindung",
                                                value="connection",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div([html.Label("Aktiviert"), dcc.Dropdown(id="medusa-enabled", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                                            html.Div([html.Label("Name"), dcc.Input(id="medusa-name", value="default")]),
                                                            html.Div([html.Label("Base URL"), dcc.Input(id="medusa-base-url", placeholder="http://localhost:9000", style={"width": "100%"})]),
                                                            html.Div([html.Label("Admin Path"), dcc.Input(id="medusa-admin-path", value="/admin")]),
                                                            html.Div([html.Label("Auth Type"), dcc.Dropdown(id="medusa-auth-type", options=[{"label": "API Token", "value": "api_token"}, {"label": "JWT", "value": "jwt"}], value="api_token", clearable=False)]),
                                                            html.Div([html.Label("API Token setzen / ersetzen"), dcc.Input(id="medusa-api-token", type="password", placeholder="Neuen Medusa Admin API Token einfügen", autoComplete="new-password", style={"width": "100%"}), html.Div(id="medusa-token-status", className="hint")]),
                                                            html.Div([html.Label("Timeout Sekunden"), dcc.Input(id="medusa-timeout", type="number", value=30, min=1)]),
                                                            html.Div([html.Label("Retries"), dcc.Input(id="medusa-retry-count", type="number", value=2, min=0)]),
                                                        ],
                                                        className="form-grid",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Button("Medusa-Konfiguration speichern", id="medusa-save-button"),
                                                            html.Button("Verbindung testen", id="medusa-test-button"),
                                                        ],
                                                        className="button-row",
                                                    ),
                                                    html.Div(id="medusa-config-status", className="selection-summary"),
                                                ],
                                            ),
                                            dcc.Tab(
                                                label="Exportumfang",
                                                value="scope",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div([html.Label("Default Locale"), dcc.Dropdown(id="medusa-default-locale", options=LANGUAGE_CODE_OPTIONS, value="de-CH", clearable=False)]),
                                                            html.Div([html.Label("Aktive Locales"), dcc.Input(id="medusa-enabled-locales", value="de-CH, fr-CH, it-CH, en", style={"width": "100%"})]),
                                                            html.Div([html.Label("Default Currency"), dcc.Input(id="medusa-default-currency", value="CHF")]),
                                                            html.Div([html.Label("Product Status Default"), dcc.Dropdown(id="medusa-product-status-default", options=[{"label": "draft", "value": "draft"}, {"label": "published", "value": "published"}], value="draft", clearable=False)]),
                                                            html.Div([html.Label("Public Asset Base URL"), dcc.Input(id="medusa-public-asset-base-url", placeholder="https://media.voxster.ch", style={"width": "100%"})]),
                                                            html.Div([html.Label("Batch Size"), dcc.Input(id="medusa-batch-size", type="number", value=20, min=1)]),
                                                        ],
                                                        className="form-grid",
                                                    ),
                                                    dcc.Checklist(
                                                        id="medusa-export-flags",
                                                        options=[
                                                            {"label": "Produkte", "value": "export_products"},
                                                            {"label": "Varianten", "value": "export_variants"},
                                                            {"label": "Optionen", "value": "export_options"},
                                                            {"label": "Bilder", "value": "export_images"},
                                                            {"label": "SEO", "value": "export_seo"},
                                                            {"label": "Metadata", "value": "export_metadata"},
                                                            {"label": "Übersetzungen", "value": "export_translations"},
                                                            {"label": "Default Preise", "value": "export_default_prices"},
                                                            {"label": "Preislisten", "value": "export_price_lists"},
                                                            {"label": "Staffelpreise", "value": "export_tiered_prices"},
                                                            {"label": "Inventory", "value": "export_inventory"},
                                                            {"label": "IDs nach Export zurückschreiben", "value": "pull_ids_after_export"},
                                                            {"label": "Mapping Repair vor Export", "value": "repair_mapping_before_export"},
                                                        ],
                                                        value=[
                                                            "export_products",
                                                            "export_variants",
                                                            "export_options",
                                                            "export_images",
                                                            "export_seo",
                                                            "export_metadata",
                                                            "export_translations",
                                                            "export_default_prices",
                                                            "export_price_lists",
                                                            "export_tiered_prices",
                                                            "pull_ids_after_export",
                                                            "repair_mapping_before_export",
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            dcc.Tab(
                                                label="Sync",
                                                value="sync",
                                                children=[
                                                    html.Div(
                                                        [
                                                            html.Div(
                                                                [
                                                                    html.Label("Produktauswahl"),
                                                                    dcc.Dropdown(
                                                                        id="medusa-product-selection-mode",
                                                                        options=[
                                                                            {"label": "Einzelne Produkt-ID", "value": "single"},
                                                                            {"label": "Markierte Produkte aus Produktliste", "value": "selected"},
                                                                            {"label": "Alle aktiven Produkte", "value": "all_active"},
                                                                            {"label": "Nur Produkte ohne Medusa-ID", "value": "without_mapping"},
                                                                        ],
                                                                        value="single",
                                                                        clearable=False,
                                                                    ),
                                                                ]
                                                            ),
                                                            html.Div([html.Label("Produkt-ID"), dcc.Input(id="medusa-product-id", type="number", placeholder="z. B. 1")]),
                                                            html.Div([html.Label("Max Produkte bei Filter"), dcc.Input(id="medusa-product-limit", type="number", value=20, min=1)]),
                                                            html.Div([html.Label("Force Update"), dcc.Dropdown(id="medusa-force-update", options=BOOLEAN_OPTIONS, value=False, clearable=False)]),
                                                        ],
                                                        className="form-grid",
                                                    ),
                                                    html.Div(
                                                        [
                                                            html.Button("Dry Run", id="medusa-dry-run-button"),
                                                            html.Button("Export starten", id="medusa-export-button"),
                                                            html.Button("IDs aus Medusa zurückladen", id="medusa-repair-button"),
                                                        ],
                                                        className="button-row",
                                                    ),
                                                    html.Div(id="medusa-sync-status", className="selection-summary"),
                                                ],
                                            ),
                                            dcc.Tab(
                                                label="Logs",
                                                value="logs",
                                                children=[
                                                    html.Div([html.Button("Logs neu laden", id="medusa-logs-refresh-button")], className="button-row"),
                                                    html.H4("Sync Runs"),
                                                    grid(
                                                        "medusa-runs-grid",
                                                        [
                                                            {"field": "id", "headerName": "Run-ID", "maxWidth": 100},
                                                            {"field": "mode", "headerName": "Modus", "minWidth": 140},
                                                            {"field": "status", "headerName": "Status", "minWidth": 140},
                                                            {"field": "started_at", "headerName": "Gestartet", "minWidth": 180},
                                                            {"field": "finished_at", "headerName": "Beendet", "minWidth": 180},
                                                            {"field": "summary", "headerName": "Summary", "flex": 1},
                                                        ],
                                                        height="260px",
                                                        row_selection="single",
                                                    ),
                                                    html.H4("Run Items"),
                                                    grid(
                                                        "medusa-run-items-grid",
                                                        [
                                                            {"field": "id", "headerName": "ID", "maxWidth": 90},
                                                            {"field": "run_id", "headerName": "Run", "maxWidth": 90},
                                                            {"field": "entity_type", "headerName": "Typ", "minWidth": 120},
                                                            {"field": "local_entity_id", "headerName": "PIM-ID", "maxWidth": 110},
                                                            {"field": "medusa_id", "headerName": "Medusa-ID", "minWidth": 180},
                                                            {"field": "locale_code", "headerName": "Locale", "maxWidth": 110},
                                                            {"field": "action", "headerName": "Aktion", "minWidth": 150},
                                                            {"field": "status", "headerName": "Status", "minWidth": 140},
                                                            {"field": "error_message", "headerName": "Fehler", "flex": 1},
                                                        ],
                                                        height="320px",
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                ]
                            )
                        ],
                    ),
                ],
                        id="main-tabs",
                        value="dashboard",
                        className="main-tabs-panel",
                    ),
                ],
                id="page-body",
                className="page-body",
            ),
        ],
        className="page",
    )
    register_callbacks(app)
    return app


def register_callbacks(app: Dash) -> None:
    @app.callback(
        Output("r2-config-panel", "style"),
        Output("r2-config-toggle-button", "children"),
        Input("r2-config-toggle-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_r2_config_panel(n_clicks: int | None):
        if (n_clicks or 0) % 2:
            return {"display": "block", "marginBottom": "16px"}, "R2-Speicher Conf ausblenden"
        return {"display": "none", "marginBottom": "16px"}, "R2-Speicher Conf"

    @app.callback(
        Output("product-enrich-modal", "style"),
        Input("open-product-enrich-modal-button", "n_clicks"),
        Input("close-product-enrich-modal-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_product_enrich_modal(_: int | None, __: int | None):
        if ctx.triggered_id == "open-product-enrich-modal-button":
            return PRODUCT_ENRICH_MODAL_VISIBLE
        return PRODUCT_ENRICH_MODAL_HIDDEN

    @app.callback(
        Output("variant-enrich-modal", "style"),
        Input("open-variant-enrich-modal-button", "n_clicks"),
        Input("close-variant-enrich-modal-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def toggle_variant_enrich_modal(_: int | None, __: int | None):
        if ctx.triggered_id == "open-variant-enrich-modal-button":
            return VARIANT_ENRICH_MODAL_VISIBLE
        return VARIANT_ENRICH_MODAL_HIDDEN

    @app.callback(
        Output("global-process-status", "children"),
        Input("global-process-status-poll", "n_intervals"),
    )
    def refresh_global_process_status(_: int | None):
        return render_global_process_status(get_process_status())

    @app.callback(
        Output("product-data-enrichment-open-button", "disabled"),
        Output("product-data-enrichment-preview-button", "disabled"),
        Output("product-data-enrichment-apply-selected-button", "disabled"),
        Output("product-data-enrichment-apply-all-button", "disabled"),
        Output("product-asset-enrichment-run-button", "disabled"),
        Output("product-final-url-description-run-button", "disabled"),
        Output("product-text-preview-button", "disabled"),
        Output("product-text-apply-selected-button", "disabled"),
        Output("product-text-apply-safe-button", "disabled"),
        Output("product-translation-open-button", "disabled"),
        Output("medusa-dry-run-button", "disabled"),
        Output("medusa-export-button", "disabled"),
        Output("medusa-repair-button", "disabled"),
        Output("open-product-enrich-modal-button", "disabled"),
        Output("product-enrich-run-button", "disabled"),
        Output("product-enrich-run-button", "children"),
        Output("product-enrich-running-hint", "children"),
        Input("global-process-status-poll", "n_intervals"),
    )
    def disable_long_action_buttons_while_process_runs(_: int | None):
        status = get_process_status()
        running = bool(status.get("running"))
        process_name = str(status.get("process_name") or "Prozess")
        if running:
            button_label = [
                html.Span(className="crawler-spinner-inline"),
                html.Span("Crawler läuft ..." if process_name == "Website-Crawler für Produkte" else "Prozess läuft ..."),
            ]
            running_hint = html.Div(
                [
                    html.Strong("Prozess läuft - bitte warten und keine weiteren Aktionen starten."),
                    html.Div(f"Aktuell: {process_name}", className="crawler-running-subtext"),
                ]
            )
        else:
            button_label = "Website-Crawler für Produkte starten"
            running_hint = ""
        return (running,) * 15 + (button_label, running_hint)

    @app.callback(
        Output("product-data-enrichment-modal", "style"),
        Output("product-data-enrichment-summary", "children"),
        Output("product-data-enrichment-results", "data", allow_duplicate=True),
        Output("product-data-enrichment-suggestions-grid", "rowData", allow_duplicate=True),
        Output("product-data-enrichment-status", "children", allow_duplicate=True),
        Output("product-data-enrichment-warnings", "children", allow_duplicate=True),
        Output("product-text-enrichment-results", "data", allow_duplicate=True),
        Output("product-text-preview-grid", "rowData", allow_duplicate=True),
        Output("product-text-status", "children", allow_duplicate=True),
        Output("product-text-warnings", "children", allow_duplicate=True),
        Input("product-data-enrichment-open-button", "n_clicks"),
        Input("product-data-enrichment-close-button", "n_clicks"),
        State("selected-product-ids", "data"),
        prevent_initial_call=True,
    )
    def toggle_product_data_enrichment_modal(_: int | None, __: int | None, selected_product_ids: list[int] | None):
        if ctx.triggered_id == "product-data-enrichment-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, "", no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
        count = len(selected_product_ids or [])
        if not count:
            return PRODUCT_ENRICH_MODAL_HIDDEN, "Keine Produkte ausgewählt.", no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update
        return (
            PRODUCT_ENRICH_MODAL_VISIBLE,
            f"{count} Produkt(e) ausgewählt. Erst Preview erzeugen, dann Vorschläge übernehmen.",
            {},
            [],
            "Bereit. Wähle einen Bereich und starte einen Dry-Run oder Apply.",
            "",
            {},
            [],
            "Bereit. Text-/SEO-Vorschau erzeugen, dann gezielt übernehmen.",
            "",
        )

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Input("open-dashboard-button", "n_clicks"),
        Input("metric-products-button", "n_clicks"),
        Input("metric-variants-button", "n_clicks"),
        Input("metric-assets-button", "n_clicks"),
        Input("metric-import-jobs-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def navigate_from_metrics(_: int | None, __: int | None, ___: int | None, ____: int | None, _____: int | None) -> str:
        target = {
            "open-dashboard-button": "dashboard",
            "metric-products-button": "products",
            "metric-variants-button": "variants",
            "metric-assets-button": "assets",
            "metric-import-jobs-button": "jobs",
        }
        return target.get(ctx.triggered_id, "dashboard")

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Input("nav-dashboard", "n_clicks"),
        Input("nav-products", "n_clicks"),
        Input("nav-chemistry", "n_clicks"),
        Input("nav-variants", "n_clicks"),
        Input("nav-categories", "n_clicks"),
        Input("nav-sales-channels", "n_clicks"),
        Input("nav-channel-categories", "n_clicks"),
        Input("nav-assets", "n_clicks"),
        Input("nav-jobs", "n_clicks"),
        Input("nav-attributes", "n_clicks"),
        Input("nav-families", "n_clicks"),
        Input("nav-translations", "n_clicks"),
        Input("nav-rules", "n_clicks"),
        Input("nav-dedupe", "n_clicks"),
        Input("nav-compliance-swiss", "n_clicks"),
        Input("nav-medusa", "n_clicks"),
        prevent_initial_call=True,
    )
    def navigate_from_sidebar(
        _: int | None,
        __: int | None,
        ___: int | None,
        ____: int | None,
        _____: int | None,
        ______: int | None,
        _______: int | None,
        ________: int | None,
        _________: int | None,
        __________: int | None,
        ___________: int | None,
        ____________: int | None,
        _____________: int | None,
        ______________: int | None,
        _______________: int | None,
        ________________: int | None,
    ) -> str:
        target = {
            "nav-dashboard": "dashboard",
            "nav-products": "products",
            "nav-chemistry": "chemistry",
            "nav-variants": "variants",
            "nav-categories": "categories",
            "nav-sales-channels": "sales-channels",
            "nav-channel-categories": "channel-categories",
            "nav-assets": "assets",
            "nav-jobs": "jobs",
            "nav-attributes": "attributes",
            "nav-families": "families",
            "nav-translations": "translations",
            "nav-rules": "rules",
            "nav-dedupe": "dedupe",
            "nav-compliance-swiss": "compliance-swiss",
            "nav-medusa": "medusa",
        }
        return target.get(ctx.triggered_id, "dashboard")

    @app.callback(
        Output("suva-import-status", "children"),
        Output("suva-sources-grid", "rowData", allow_duplicate=True),
        Input("suva-import-button", "n_clicks"),
        State("suva-upload", "contents"),
        State("suva-upload", "filename"),
        State("suva-source-name", "value"),
        State("suva-source-url", "value"),
        State("suva-source-language", "value"),
        State("suva-imported-by", "value"),
        State("suva-source-notes", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def import_suva_limits_callback(
        session: Session,
        _clicks: int | None,
        contents: str | None,
        filename: str | None,
        source_name: str | None,
        source_url: str | None,
        language: str | None,
        imported_by: str | None,
        notes: str | None,
    ):
        try:
            payload = decode_uploaded_file(contents)
            result = import_suva_xlsx(
                session,
                payload,
                filename or "suva-grenzwerte.xlsx",
                imported_by=imported_by,
                source_name=source_name or "SUVA Grenzwerte am Arbeitsplatz",
                source_url=source_url,
                language=language or "de",
                notes=notes,
            )
        except Exception as exc:
            return _render_chemical_internet_status("error", f"SUVA-Import fehlgeschlagen: {exc}"), list_suva_sources(session)
        if result.get("status") == "duplicate":
            status = _render_chemical_internet_status(
                "success",
                "SUVA-Datei wurde bereits importiert.",
                details=[f"Import-ID: {result.get('source_id')}", f"SHA256: {result.get('sha256')}"],
            )
        else:
            status = _render_chemical_internet_status(
                "success",
                "SUVA-Grenzwerte importiert.",
                details=[
                    f"Import-ID: {result.get('source_id')}",
                    f"Einträge: {result.get('entries_imported')}",
                    f"Übersprungen: {result.get('rows_skipped')}",
                    f"SHA256: {result.get('sha256')}",
                ],
            )
        return status, list_suva_sources(session)

    @app.callback(
        Output("suva-sources-grid", "rowData"),
        Input("main-tabs", "value"),
        Input("suva-refresh-button", "n_clicks"),
    )
    @_with_session
    def load_suva_sources_callback(session: Session, active_tab: str | None, _clicks: int | None):
        if active_tab != "compliance-swiss":
            return no_update
        return list_suva_sources(session)

    @app.callback(
        Output("medusa-enabled", "value"),
        Output("medusa-name", "value"),
        Output("medusa-base-url", "value"),
        Output("medusa-admin-path", "value"),
        Output("medusa-auth-type", "value"),
        Output("medusa-api-token", "value"),
        Output("medusa-token-status", "children"),
        Output("medusa-timeout", "value"),
        Output("medusa-retry-count", "value"),
        Output("medusa-default-locale", "value"),
        Output("medusa-enabled-locales", "value"),
        Output("medusa-default-currency", "value"),
        Output("medusa-product-status-default", "value"),
        Output("medusa-public-asset-base-url", "value"),
        Output("medusa-batch-size", "value"),
        Output("medusa-export-flags", "value"),
        Output("medusa-config-status", "children"),
        Input("snapshot-store", "data"),
    )
    @_with_session
    def load_medusa_config_callback(session: Session, _snapshot: dict | None):
        data = serialize_medusa_connection(get_or_create_medusa_connection(session))
        flags = [
            flag
            for flag in [
                "export_products",
                "export_variants",
                "export_options",
                "export_categories",
                "export_collections",
                "export_tags",
                "export_types",
                "export_images",
                "export_seo",
                "export_metadata",
                "export_translations",
                "export_default_prices",
                "export_price_lists",
                "export_tiered_prices",
                "export_inventory",
                "pull_ids_after_export",
                "repair_mapping_before_export",
            ]
            if data.get(flag)
        ]
        token_status = "API Token gespeichert: ja" if data.get("api_token_configured") else "API Token gespeichert: nein"
        return (
            data["enabled"],
            data["name"],
            data["base_url"],
            data["admin_path"],
            data["auth_type"],
            "",
            token_status,
            data["timeout_seconds"],
            data["retry_count"],
            data["default_locale"],
            data["enabled_locales"],
            data["default_currency_code"],
            data["product_status_default"],
            data["public_asset_base_url"],
            data["batch_size"],
            flags,
            _render_medusa_config_status(data),
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("medusa-config-status", "children", allow_duplicate=True),
        Output("medusa-token-status", "children", allow_duplicate=True),
        Output("medusa-api-token", "value"),
        Input("medusa-save-button", "n_clicks"),
        State("medusa-enabled", "value"),
        State("medusa-name", "value"),
        State("medusa-base-url", "value"),
        State("medusa-admin-path", "value"),
        State("medusa-auth-type", "value"),
        State("medusa-api-token", "value"),
        State("medusa-timeout", "value"),
        State("medusa-retry-count", "value"),
        State("medusa-default-locale", "value"),
        State("medusa-enabled-locales", "value"),
        State("medusa-default-currency", "value"),
        State("medusa-product-status-default", "value"),
        State("medusa-public-asset-base-url", "value"),
        State("medusa-batch-size", "value"),
        State("medusa-export-flags", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_medusa_config_callback(
        session: Session,
        _clicks: int | None,
        enabled: bool | None,
        name: str | None,
        base_url: str | None,
        admin_path: str | None,
        auth_type: str | None,
        api_token: str | None,
        timeout_seconds: int | None,
        retry_count: int | None,
        default_locale: str | None,
        enabled_locales: str | None,
        default_currency: str | None,
        product_status_default: str | None,
        public_asset_base_url: str | None,
        batch_size: int | None,
        export_flags: list[str] | None,
    ):
        flags = set(export_flags or [])
        payload = {
            "enabled": bool(enabled),
            "name": name or "default",
            "base_url": base_url,
            "admin_path": admin_path,
            "auth_type": auth_type,
            "api_token": api_token,
            "timeout_seconds": timeout_seconds,
            "retry_count": retry_count,
            "default_locale": default_locale,
            "enabled_locales": enabled_locales,
            "default_currency_code": default_currency,
            "product_status_default": product_status_default,
            "public_asset_base_url": public_asset_base_url,
            "batch_size": batch_size,
            **{
                flag: flag in flags
                for flag in [
                    "export_products",
                    "export_variants",
                    "export_options",
                    "export_categories",
                    "export_collections",
                    "export_tags",
                    "export_types",
                    "export_images",
                    "export_seo",
                    "export_metadata",
                    "export_translations",
                    "export_default_prices",
                    "export_price_lists",
                    "export_tiered_prices",
                    "export_inventory",
                    "pull_ids_after_export",
                    "repair_mapping_before_export",
                ]
            },
        }
        try:
            config = save_medusa_connection(session, payload)
            token_status = "API Token gespeichert: ja" if serialize_medusa_connection(config).get("api_token_configured") else "API Token gespeichert: nein"
            return "Medusa-Konfiguration gespeichert. Token wurde nicht zurückgegeben.", _render_medusa_config_status(serialize_medusa_connection(config)), token_status, ""
        except Exception as exc:
            return f"Medusa-Konfiguration konnte nicht gespeichert werden: {exc}", str(exc), no_update, no_update

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("medusa-config-status", "children", allow_duplicate=True),
        Input("medusa-test-button", "n_clicks"),
        prevent_initial_call=True,
    )
    @_with_session
    def test_medusa_connection_callback(session: Session, _clicks: int | None):
        result = MedusaSyncService(session).test_connection()
        data = serialize_medusa_connection(get_or_create_medusa_connection(session))
        message = str(result.get("message") or result.get("status"))
        return message, _render_medusa_config_status(data)

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("medusa-sync-status", "children", allow_duplicate=True),
        Output("medusa-runs-grid", "rowData", allow_duplicate=True),
        Output("medusa-run-items-grid", "rowData", allow_duplicate=True),
        Input("medusa-dry-run-button", "n_clicks"),
        Input("medusa-export-button", "n_clicks"),
        Input("medusa-repair-button", "n_clicks"),
        State("medusa-product-selection-mode", "value"),
        State("medusa-product-id", "value"),
        State("selected-product-ids", "data"),
        State("medusa-product-limit", "value"),
        State("medusa-force-update", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_medusa_sync_callback(
        session: Session,
        _dry_clicks: int | None,
        _export_clicks: int | None,
        _repair_clicks: int | None,
        selection_mode: str | None,
        product_id: int | None,
        selected_product_ids: list[int] | None,
        product_limit: int | None,
        force_update: bool | None,
    ):
        service = MedusaSyncService(session)
        try:
            if ctx.triggered_id == "medusa-repair-button":
                result = service.repair_mapping()
            elif ctx.triggered_id == "medusa-export-button":
                product_ids = service.resolve_product_ids(
                    selection_mode=selection_mode or "single",
                    product_id=product_id,
                    selected_product_ids=selected_product_ids,
                    limit=product_limit,
                )
                if not product_ids:
                    raise ValueError("Keine Produkte für Medusa-Export ausgewählt.")
                result = service.export_products(product_ids, dry_run=False, force=bool(force_update))
            else:
                product_ids = service.resolve_product_ids(
                    selection_mode=selection_mode or "single",
                    product_id=product_id,
                    selected_product_ids=selected_product_ids,
                    limit=product_limit,
                )
                if not product_ids:
                    raise ValueError("Keine Produkte für Medusa-Dry-Run ausgewählt.")
                result = service.export_products(product_ids, dry_run=True, force=bool(force_update))
            count = result.get("product_count") or (1 if result.get("run_id") else 0)
            message = f"Medusa {ctx.triggered_id}: {result.get('status')} · {count} Produkt(e)"
            run_ids = result.get("run_ids") or ([result.get("run_id")] if result.get("run_id") else [])
            latest_run_id = int(run_ids[-1]) if run_ids else None
            return message, html.Pre(json.dumps(result, ensure_ascii=False, indent=2, default=str)), list_medusa_runs(session), list_medusa_run_items(session, latest_run_id)
        except Exception as exc:
            return f"Medusa-Sync fehlgeschlagen: {exc}", str(exc), list_medusa_runs(session), list_medusa_run_items(session)

    @app.callback(
        Output("medusa-runs-grid", "rowData"),
        Output("medusa-run-items-grid", "rowData"),
        Input("snapshot-store", "data"),
        Input("medusa-logs-refresh-button", "n_clicks"),
    )
    @_with_session
    def load_medusa_logs_callback(session: Session, _snapshot: dict | None, _clicks: int | None):
        return list_medusa_runs(session), list_medusa_run_items(session)

    @app.callback(
        Output("sidebar-collapsed-store", "data"),
        Input("sidebar-toggle-button", "n_clicks"),
        State("sidebar-collapsed-store", "data"),
        prevent_initial_call=True,
    )
    def toggle_sidebar(_: int | None, collapsed: bool | None) -> bool:
        return not bool(collapsed)

    @app.callback(
        Output("page-body", "className"),
        Output("sidebar-shell", "className"),
        Output("sidebar-nav", "className"),
        Output("sidebar-toggle-button", "children"),
        Input("sidebar-collapsed-store", "data"),
    )
    def update_sidebar_layout(collapsed: bool | None):
        is_collapsed = bool(collapsed)
        return (
            "page-body page-body-collapsed" if is_collapsed else "page-body",
            "sidebar-shell sidebar-shell-collapsed" if is_collapsed else "sidebar-shell",
            "sidebar-nav sidebar-nav-collapsed" if is_collapsed else "sidebar-nav",
            "›" if is_collapsed else "‹",
        )

    @app.callback(
        Output("nav-dashboard", "className"),
        Output("nav-products", "className"),
        Output("nav-chemistry", "className"),
        Output("nav-variants", "className"),
        Output("nav-categories", "className"),
        Output("nav-sales-channels", "className"),
        Output("nav-channel-categories", "className"),
        Output("nav-assets", "className"),
        Output("nav-jobs", "className"),
        Output("nav-attributes", "className"),
        Output("nav-families", "className"),
        Output("nav-translations", "className"),
        Output("nav-rules", "className"),
        Output("nav-dedupe", "className"),
        Output("nav-compliance-swiss", "className"),
        Input("main-tabs", "value"),
    )
    def update_sidebar_classes(active_tab: str | None):
        active = active_tab or "dashboard"
        tabs = ["dashboard", "products", "chemistry", "variants", "categories", "sales-channels", "channel-categories", "assets", "jobs", "attributes", "families", "translations", "rules", "dedupe", "compliance-swiss"]
        return tuple(
            "sidebar-nav-button sidebar-nav-button-active" if tab == active else "sidebar-nav-button"
            for tab in tabs
        )

    @app.callback(Output("refresh-token", "data"), Input("refresh-button", "n_clicks"), prevent_initial_call=True)
    def refresh_token(n_clicks: int) -> int:
        return n_clicks or 0

    @app.callback(
        Output("dedupe-groups-grid", "rowData"),
        Input("main-tabs", "value"),
        Input("dedupe-refresh-token", "data"),
        Input("dedupe-status-filter", "value"),
        Input("dedupe-confidence-filter", "value"),
        Input("dedupe-source-filter", "value"),
        Input("dedupe-query-filter", "value"),
        Input("dedupe-extra-filters", "value"),
    )
    @_with_session
    def load_dedupe_groups(
        session: Session,
        active_tab: str | None,
        _: int | None,
        status: str | None,
        min_score: str | None,
        source: str | None,
        query: str | None,
        extra_filters: list[str] | None,
    ):
        filters = set(extra_filters or [])
        score = int(min_score) if str(min_score or "").isdigit() else None
        return list_duplicate_groups(
            session,
            status=status or None,
            min_score=score,
            source=source or None,
            query=query or None,
            only_open="open" in filters,
            conflicts_only="conflicts" in filters,
            safe_only="safe" in filters,
        )

    @app.callback(
        Output("dedupe-status", "children", allow_duplicate=True),
        Output("dedupe-refresh-token", "data", allow_duplicate=True),
        Input("dedupe-scan-button", "n_clicks"),
        State("dedupe-confidence-filter", "value"),
        State("dedupe-query-filter", "value"),
        State("dedupe-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_dedupe_scan(session: Session, n_clicks: int | None, min_score: str | None, query: str | None, token: int | None):
        if not n_clicks:
            return no_update, no_update
        min_confidence = "HIGH" if min_score == "90" else "MEDIUM" if min_score == "70" else "LOW"
        product_id = int(query) if str(query or "").isdigit() else None
        result = scan_duplicate_groups(session, min_confidence=min_confidence, product_id=product_id, created_by="pim_gui")
        return (
            f"Scan abgeschlossen: {result['groups_count']} Gruppen erkannt, {result['created_count']} neu, {result['updated_count']} aktualisiert.",
            (token or 0) + 1,
        )

    @app.callback(
        Output("dedupe-master-detail", "children"),
        Output("dedupe-items-grid", "rowData"),
        Output("dedupe-items-grid", "selectedRows"),
        Output("dedupe-conflicts-grid", "rowData"),
        Output("dedupe-preview-detail", "children"),
        Output("dedupe-merge-log", "children"),
        Input("dedupe-groups-grid", "selectedRows"),
        Input("dedupe-refresh-token", "data"),
    )
    @_with_session
    def load_dedupe_detail(session: Session, selected_rows: list[dict] | None, _: int | None):
        group_id = _selected_dedupe_group_id(selected_rows)
        if group_id is None:
            return "Keine Dublettengruppe ausgewählt.", [], [], [], "Noch keine Vorschau.", ""
        detail = get_duplicate_group_detail(session, group_id)
        if detail is None:
            return "Dublettengruppe nicht gefunden.", [], [], [], "Noch keine Vorschau.", ""
        return (
            _render_dedupe_master(detail.get("master") or {}),
            detail.get("items") or [],
            [],
            detail.get("conflicts") or [],
            _render_dedupe_preview(detail.get("latest_preview")),
            json.dumps(detail.get("merge_log") or detail.get("latest_preview") or {}, ensure_ascii=False, indent=2, default=str),
        )

    @app.callback(
        Output("dedupe-group-selection-status", "children"),
        Input("dedupe-items-grid", "rowData"),
        Input("dedupe-items-grid", "selectedRows"),
    )
    def update_dedupe_group_selection_status(row_data: list[dict] | None, selected_rows: list[dict] | None):
        return _dedupe_group_selection_status(row_data, selected_rows)

    @app.callback(
        Output("dedupe-items-grid", "selectedRows", allow_duplicate=True),
        Input("dedupe-select-group-button", "n_clicks"),
        Input("dedupe-deselect-group-button", "n_clicks"),
        State("dedupe-items-grid", "rowData"),
        State("dedupe-items-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def toggle_dedupe_group_selection(
        select_clicks: int | None,
        deselect_clicks: int | None,
        row_data: list[dict] | None,
        selected_rows: list[dict] | None,
    ):
        if not row_data:
            return []
        if ctx.triggered_id == "dedupe-select-group-button":
            return _dedupe_select_group_rows(row_data, selected_rows, selected=True)
        if ctx.triggered_id == "dedupe-deselect-group-button":
            return _dedupe_select_group_rows(row_data, selected_rows, selected=False)
        return no_update

    @app.callback(
        Output("dedupe-status", "children", allow_duplicate=True),
        Output("dedupe-refresh-token", "data", allow_duplicate=True),
        Input("dedupe-preview-button", "n_clicks"),
        State("dedupe-groups-grid", "selectedRows"),
        State("dedupe-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def create_dedupe_preview(session: Session, n_clicks: int | None, selected_rows: list[dict] | None, token: int | None):
        if not n_clicks:
            return no_update, no_update
        group_id = _selected_dedupe_group_id(selected_rows)
        if group_id is None:
            return "Bitte zuerst eine Dublettengruppe auswählen.", no_update
        try:
            preview = create_duplicate_group_preview(session, group_id, created_by="pim_gui")
        except Exception as exc:
            return f"Dry-Run fehlgeschlagen: {exc}", no_update
        return (
            f"Dry-Run erstellt: Master {preview['master_product_id']}, Dubletten {preview['duplicate_product_ids']}, Konflikte {preview['conflicts_count']}.",
            (token or 0) + 1,
        )

    @app.callback(
        Output("dedupe-merge-confirm", "displayed"),
        Output("dedupe-merge-confirm", "message"),
        Input("dedupe-merge-button", "n_clicks"),
        State("dedupe-groups-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def ask_dedupe_merge_confirm(n_clicks: int | None, selected_rows: list[dict] | None):
        if not n_clicks:
            return False, ""
        group_id = _selected_dedupe_group_id(selected_rows)
        if group_id is None:
            return False, ""
        return True, "Dieser Vorgang archiviert Dubletten und übernimmt fehlende Assets, Varianten und Preise zum Master. Fortfahren?"

    @app.callback(
        Output("dedupe-status", "children", allow_duplicate=True),
        Output("dedupe-refresh-token", "data", allow_duplicate=True),
        Input("dedupe-merge-confirm", "submit_n_clicks"),
        State("dedupe-groups-grid", "selectedRows"),
        State("dedupe-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_dedupe_merge(session: Session, submit_n_clicks: int | None, selected_rows: list[dict] | None, token: int | None):
        if not submit_n_clicks:
            return no_update, no_update
        group_id = _selected_dedupe_group_id(selected_rows)
        if group_id is None:
            return "Bitte zuerst eine Dublettengruppe auswählen.", no_update
        try:
            result = merge_duplicate_group(session, group_id, yes=True, created_by="pim_gui")
        except Exception as exc:
            return f"Merge fehlgeschlagen: {exc}", no_update
        return (
            f"Merge erfolgreich: Master {result['master_product_id']}, archivierte Produkte {result.get('archived_products_detail', result.get('duplicate_product_ids', []))}.",
            (token or 0) + 1,
        )

    @app.callback(
        Output("dedupe-status", "children", allow_duplicate=True),
        Output("dedupe-refresh-token", "data", allow_duplicate=True),
        Input("dedupe-ignore-button", "n_clicks"),
        State("dedupe-groups-grid", "selectedRows"),
        State("dedupe-ignore-reason", "value"),
        State("dedupe-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_dedupe_ignore(session: Session, n_clicks: int | None, selected_rows: list[dict] | None, reason: str | None, token: int | None):
        if not n_clicks:
            return no_update, no_update
        group_id = _selected_dedupe_group_id(selected_rows)
        if group_id is None:
            return "Bitte zuerst eine Dublettengruppe auswählen.", no_update
        ignore_duplicate_group(session, group_id, reason=reason, reviewed_by="pim_gui")
        return "Dublettengruppe wurde ignoriert. Produkte bleiben unverändert.", (token or 0) + 1

    @app.callback(
        Output("dedupe-status", "children", allow_duplicate=True),
        Output("dedupe-refresh-token", "data", allow_duplicate=True),
        Input("dedupe-set-master-button", "n_clicks"),
        State("dedupe-groups-grid", "selectedRows"),
        State("dedupe-items-grid", "selectedRows"),
        State("dedupe-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_dedupe_set_master(session: Session, n_clicks: int | None, selected_group_rows: list[dict] | None, selected_item_rows: list[dict] | None, token: int | None):
        if not n_clicks:
            return no_update, no_update
        group_id = _selected_dedupe_group_id(selected_group_rows)
        product_id = _selected_dedupe_product_id(selected_item_rows)
        if group_id is None or product_id is None:
            return "Bitte zuerst Dublettengruppe und Produktzeile auswählen.", no_update
        try:
            set_duplicate_group_master(session, group_id, product_id, reviewed_by="pim_gui")
        except Exception as exc:
            return f"Master-Wechsel fehlgeschlagen: {exc}", no_update
        return f"Produkt {product_id} ist jetzt Master der Dublettengruppe.", (token or 0) + 1

    @app.callback(
        Output("product-data-enrichment-results", "data"),
        Output("product-data-enrichment-suggestions-grid", "rowData"),
        Output("product-data-enrichment-status", "children"),
        Output("product-data-enrichment-warnings", "children"),
        Input("product-data-enrichment-preview-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("product-data-enrichment-fields", "value"),
        State("product-data-enrichment-sources", "value"),
        State("product-data-enrichment-overwrite", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def preview_product_data_enrichment_callback(
        session: Session,
        n_clicks: int | None,
        selected_product_ids: list[int] | None,
        fields: list[str] | None,
        sources: list[str] | None,
        overwrite_existing: bool | None,
    ):
        if not n_clicks:
            return no_update, no_update, no_update, no_update
        product_ids = [int(product_id) for product_id in (selected_product_ids or [])]
        if not product_ids:
            return {}, [], "Keine Produkte ausgewählt.", ""
        try:
            with process_guard(
                "Fehlende Produktdaten anreichern",
                options={"dry_run": True, "overwrite": bool(overwrite_existing), "fields": fields or [], "sources": sources or []},
                selection={"products": len(product_ids), "product_ids": product_ids[:20]},
                progress_total=len(product_ids),
            ):
                result = preview_product_data_enrichment(
                    session,
                    product_ids,
                    fields=fields or [],
                    sources=sources or [],
                    overwrite_existing=bool(overwrite_existing),
                )
                suggestions = _product_enrichment_suggestion_rows(result)
                warnings = _product_enrichment_warnings(result)
                counters = {
                    "products_checked": int(result.get("products_checked", 0) or 0),
                    "products_with_suggestions": int(result.get("products_with_suggestions", 0) or 0),
                    "field_suggestions": len(suggestions),
                }
                status = (
                    f"{result.get('products_checked', 0)} Produkte geprüft · "
                    f"{result.get('products_with_suggestions', 0)} Produkte mit Vorschlägen · "
                    f"{len(suggestions)} Feldvorschläge."
                )
                finish_process(status="success", message=status, counters=counters)
                return result, suggestions, status, warnings
        except ProcessAlreadyRunning as exc:
            return {}, [], str(exc), ""

    @app.callback(
        Output("product-data-enrichment-status", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("product-data-enrichment-suggestions-grid", "selectedRows", allow_duplicate=True),
        Input("product-data-enrichment-apply-selected-button", "n_clicks"),
        Input("product-data-enrichment-apply-all-button", "n_clicks"),
        State("product-data-enrichment-suggestions-grid", "selectedRows"),
        State("product-data-enrichment-suggestions-grid", "rowData"),
        State("product-data-enrichment-overwrite", "value"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_product_data_enrichment_callback(
        session: Session,
        selected_clicks: int | None,
        all_clicks: int | None,
        selected_rows: list[dict] | None,
        all_rows: list[dict] | None,
        overwrite_existing: bool | None,
        refresh_token: int | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "product-data-enrichment-apply-all-button":
            rows = all_rows or []
        else:
            rows = selected_rows or []
        if not rows:
            return "Keine Vorschläge ausgewählt.", no_update, no_update
        try:
            with process_guard(
                "Produktdaten-Vorschläge übernehmen",
                options={"apply": True, "overwrite": bool(overwrite_existing)},
                selection={"suggestions": len(rows)},
                progress_total=len(rows),
            ):
                result = apply_product_data_enrichment(session, rows, overwrite_existing=bool(overwrite_existing), created_by="pim_gui")
                message = f"{result['applied_count']} Vorschläge übernommen, {result['skipped_count']} übersprungen."
                finish_process(status="success", message=message, counters={"applied": int(result["applied_count"]), "skipped": int(result["skipped_count"])})
                return (
                    message,
                    (refresh_token or 0) + 1,
                    [],
                )
        except ProcessAlreadyRunning as exc:
            return str(exc), no_update, no_update

    @app.callback(
        Output("product-text-enrichment-results", "data"),
        Output("product-text-preview-grid", "rowData"),
        Output("product-text-status", "children"),
        Output("product-text-warnings", "children"),
        Input("product-text-preview-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("product-text-source-language", "value"),
        State("product-text-target-languages", "value"),
        State("product-text-fields", "value"),
        State("product-text-mode-options", "value"),
        State("product-text-quality-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def preview_product_text_enrichment_callback(
        session: Session,
        n_clicks: int | None,
        selected_product_ids: list[int] | None,
        source_locale: str | None,
        target_locales: list[str] | None,
        fields: list[str] | None,
        mode_options: list[str] | None,
        quality_options: list[str] | None,
    ):
        if not n_clicks:
            return no_update, no_update, no_update, no_update
        product_ids = [int(product_id) for product_id in (selected_product_ids or [])]
        if not product_ids:
            return {}, [], "Keine Produkte ausgewählt.", ""
        selected_mode = set(mode_options or [])
        selected_quality = set(quality_options or [])
        options = TextEnrichmentOptions(
            only_missing="only_missing" in selected_mode,
            overwrite_existing="overwrite" in selected_mode,
            markdown="markdown" in selected_quality,
            strip_html="strip_html" in selected_quality,
            collapse_blank_lines="collapse_blank_lines" in selected_quality,
            markdown_bullets="markdown_bullets" in selected_quality,
            structure_sections="structure_sections" in selected_quality,
            remove_supplier_notes="remove_supplier_notes" in selected_quality,
            remove_external_numbers="remove_external_numbers" in selected_quality,
            generate_seo="seo" in selected_mode,
            generate_slug="slug" in selected_mode,
        )
        try:
            with process_guard(
                "Sprachfelder / Texte / SEO Vorschau",
                options={"dry_run": True, "source_locale": source_locale, "target_locales": target_locales or [], "fields": fields or []},
                selection={"products": len(product_ids), "product_ids": product_ids[:20]},
                progress_total=len(product_ids),
            ):
                result = preview_product_text_enrichment(
                    session,
                    product_ids,
                    source_locale=source_locale or "de-CH",
                    target_locales=target_locales or [],
                    fields=fields or [],
                    options=options,
                )
                rows = result.get("suggestions", [])
                counts: dict[str, int] = {}
                for row in rows:
                    counts[str(row.get("status") or "unbekannt")] = counts.get(str(row.get("status") or "unbekannt"), 0) + 1
                status = f"Text-/SEO-Vorschau: {result.get('products_checked', 0)} Produkt(e) geprüft · {len(rows)} Vorschläge."
                warnings = html.Ul([html.Li(item) for item in result.get("warnings", [])]) if result.get("warnings") else ""
                finish_process(status="success", message=status, counters=counts)
                return result, rows, status, warnings
        except ProcessAlreadyRunning as exc:
            return {}, [], str(exc), ""

    @app.callback(
        Output("product-text-status", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("product-text-preview-grid", "selectedRows", allow_duplicate=True),
        Input("product-text-apply-selected-button", "n_clicks"),
        Input("product-text-apply-safe-button", "n_clicks"),
        State("product-text-preview-grid", "selectedRows"),
        State("product-text-preview-grid", "rowData"),
        State("product-text-mode-options", "value"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_product_text_enrichment_callback(
        session: Session,
        selected_clicks: int | None,
        safe_clicks: int | None,
        selected_rows: list[dict] | None,
        all_rows: list[dict] | None,
        mode_options: list[str] | None,
        refresh_token: int | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "product-text-apply-safe-button":
            rows = [
                row
                for row in (all_rows or [])
                if row.get("status") in {"wird ergänzt", "wird überschrieben", "nur formatiert"}
            ]
        else:
            rows = selected_rows or []
        if not rows:
            return "Keine Textvorschläge ausgewählt.", no_update, no_update
        overwrite = "overwrite" in set(mode_options or [])
        try:
            with process_guard(
                "Sprachfelder / Texte / SEO übernehmen",
                options={"apply": True, "overwrite": overwrite},
                selection={"suggestions": len(rows)},
                progress_total=len(rows),
            ):
                result = apply_product_text_enrichment(session, rows, overwrite_existing=overwrite, created_by="pim_gui")
                message = f"{result['applied_count']} Textvorschläge übernommen, {result['skipped_count']} übersprungen."
                if result.get("errors"):
                    message += f" Fehler: {len(result['errors'])}"
                finish_process(status="success", message=message, counters={"applied": int(result["applied_count"]), "skipped": int(result["skipped_count"])})
                return message, (refresh_token or 0) + 1, []
        except ProcessAlreadyRunning as exc:
            return str(exc), no_update, no_update

    @app.callback(
        Output("product-asset-enrichment-status", "children"),
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-asset-enrichment-run-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_product_asset_enrichment_callback(
        session: Session,
        n_clicks: int | None,
        selected_product_ids: list[int] | None,
        refresh_token: int | None,
    ):
        if not n_clicks:
            return no_update, no_update, no_update
        product_ids = [int(product_id) for product_id in (selected_product_ids or [])]
        if not product_ids:
            message = "Bitte zuerst mindestens ein Produkt auswählen."
            return message, message, no_update
        try:
            with process_guard(
                "Fehlende Produkt-Assets anreichern",
                options={"apply": True, "only_missing": True},
                selection={"products": len(product_ids), "product_ids": product_ids[:20]},
                progress_total=len(product_ids),
            ):
                result = enrich_missing_product_assets(
                    session,
                    product_ids,
                    storage_root=get_pim_settings().asset_storage_root,
                )
        except ProcessAlreadyRunning as exc:
            message = str(exc)
            return message, message, no_update
        except Exception as exc:
            message = f"Produkt-Asset-Anreicherung fehlgeschlagen: {exc}"
            fail_process(message)
            return message, message, no_update
        summary = html.Div(
            [
                html.Div(
                    f"{result.get('products_checked', 0)} Produkte geprüft · "
                    f"{result.get('saved_count', 0)} Assets gespeichert · "
                    f"{result.get('skipped_count', 0)} übersprungen · "
                    f"{result.get('error_count', 0)} Fehler."
                ),
                html.Ul([html.Li(str(entry)) for entry in (result.get("logs") or [])[:12]]),
            ]
        )
        finish_process(
            status="success" if int(result.get("error_count", 0) or 0) == 0 else "partial_success",
            message="Produkt-Asset-Anreicherung abgeschlossen.",
            counters={
                "products_checked": int(result.get("products_checked", 0) or 0),
                "assets_saved": int(result.get("saved_count", 0) or 0),
                "skipped": int(result.get("skipped_count", 0) or 0),
                "errors": int(result.get("error_count", 0) or 0),
            },
        )
        return summary, "Produkt-Asset-Anreicherung abgeschlossen.", (refresh_token or 0) + 1

    @app.callback(
        Output("product-final-url-description-status", "children"),
        Output("product-final-url-description-grid", "rowData"),
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-final-url-description-run-button", "n_clicks"),
        State("product-final-url-description-scope", "value"),
        State("product-final-url-description-product-id", "value"),
        State("product-final-url-description-run-mode", "value"),
        State("product-final-url-description-options", "value"),
        State("selected-product-ids", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_final_url_description_import_callback(
        session: Session,
        n_clicks: int | None,
        scope: str | None,
        product_id: int | None,
        run_mode: str | None,
        options: list[str] | None,
        selected_product_ids: list[int] | None,
        refresh_token: int | None,
    ):
        if not n_clicks:
            return no_update, no_update, no_update, no_update
        selected_options = set(options or [])
        dry_run = run_mode != "apply"
        overwrite = "overwrite" in selected_options
        try:
            products = _final_url_description_products(session, scope or "selected", product_id, selected_product_ids)
            if not products:
                message = "Keine Produkte gefunden. Produkt markieren, Produkt-ID eintragen oder Scope 'Alle Produkte mit Final URL prüfen' wählen."
                return message, [], message, no_update
            with process_guard(
                "Beschreibungen aus Final URLs importieren",
                options={"mode": "Dry-Run" if dry_run else "Apply", "overwrite": overwrite, "scope": scope or "selected"},
                selection={"products": len(products), "product_ids": [product.id for product in products[:20]]},
                progress_total=len(products),
            ):
                rows = process_final_url_description_products(
                    session,
                    products,
                    overwrite=overwrite,
                    dry_run=dry_run,
                    sleep_seconds=0.2,
                    backup_dir=Path("/opt/output/final_url_description_backups"),
                    enhance_ai=True,
                )
                if dry_run:
                    session.rollback()
                report_path = Path("/opt/output") / f"final_url_description_import_gui_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
                write_final_url_description_report(rows, report_path)
        except ProcessAlreadyRunning as exc:
            message = str(exc)
            return message, [], message, no_update
        except Exception as exc:
            message = f"Beschreibungsimport aus Final URLs fehlgeschlagen: {exc}"
            fail_process(message)
            return message, [], message, no_update
        row_data = [row.__dict__ for row in rows]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        mode = "Dry-Run" if dry_run else "Apply"
        status = html.Div(
            [
                html.Div(f"{mode}: {len(rows)} Produkte geprüft · {counts}"),
                html.Div(f"CSV-Report: {report_path}"),
                html.Div("Apply hat betroffene Produkte vorher als JSON gesichert." if not dry_run else "Dry-Run: keine Datenbankänderung."),
            ]
        )
        flash = f"Beschreibungsimport aus Final URLs: {mode} abgeschlossen · {len(rows)} Produkt(e)."
        finish_process(
            status="success" if not counts.get("error") and not counts.get("ai_error") else "partial_success",
            message=flash,
            report_path=str(report_path),
            counters={key: int(value) for key, value in counts.items()},
        )
        return status, row_data, flash, (refresh_token or 0) + 1 if not dry_run else no_update

    @app.callback(
        Output("rules-product-enrichment-results", "data"),
        Output("rules-product-enrichment-suggestions-grid", "rowData"),
        Output("rules-product-enrichment-status", "children"),
        Output("rules-product-enrichment-warnings", "children"),
        Input("rules-product-enrichment-preview-button", "n_clicks"),
        Input("rules-product-enrichment-clear-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("rules-product-enrichment-product-ids", "value"),
        State("rules-product-enrichment-action", "value"),
        State("rules-product-enrichment-fields", "value"),
        State("rules-product-enrichment-sources", "value"),
        State("rules-product-enrichment-overwrite", "value"),
        State("rules-product-enrichment-language", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def preview_rules_product_data_enrichment_callback(
        session: Session,
        preview_clicks: int | None,
        clear_clicks: int | None,
        selected_product_ids: list[int] | None,
        raw_product_ids: str | None,
        action: str | None,
        fields: list[str] | None,
        sources: list[str] | None,
        overwrite_existing: bool | None,
        language_code: str | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "rules-product-enrichment-clear-button":
            return {}, [], "Vorschau geleert.", html.Div("Keine Warnungen.")
        if not preview_clicks:
            return no_update, no_update, no_update, no_update
        product_ids = _parse_product_ids(raw_product_ids, selected_product_ids)
        if not product_ids:
            return {}, [], "Keine Produkte ausgewählt. Produkt-IDs eintragen oder Produkte in der Produktliste markieren.", html.Div("")
        target_fields = _rules_enrichment_fields(action, fields)
        result = preview_product_data_enrichment(
            session,
            product_ids,
            fields=target_fields,
            sources=sources or [],
            overwrite_existing=_rules_enrichment_should_preview_existing(action, overwrite_existing),
            target_locale=None if language_code in (None, "product_source") else str(language_code),
        )
        suggestions = _product_enrichment_suggestion_rows(result)
        warnings = _product_enrichment_warnings(result)
        language_hint = "Produkt-Originalsprache" if language_code in (None, "product_source") else str(language_code)
        status = (
            f"Dry Run abgeschlossen · Sprache: {language_hint} · "
            f"{result.get('products_checked', 0)} Produkte geprüft · "
            f"{result.get('products_with_suggestions', 0)} Produkte mit Vorschlägen · "
            f"{len(suggestions)} Feldvorschläge."
        )
        return result, suggestions, status, warnings

    @app.callback(
        Output("rules-product-enrichment-status", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("rules-product-enrichment-suggestions-grid", "selectedRows", allow_duplicate=True),
        Input("rules-product-enrichment-apply-selected-button", "n_clicks"),
        Input("rules-product-enrichment-apply-safe-button", "n_clicks"),
        State("rules-product-enrichment-suggestions-grid", "selectedRows"),
        State("rules-product-enrichment-suggestions-grid", "rowData"),
        State("rules-product-enrichment-overwrite", "value"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_rules_product_data_enrichment_callback(
        session: Session,
        selected_clicks: int | None,
        safe_clicks: int | None,
        selected_rows: list[dict] | None,
        all_rows: list[dict] | None,
        overwrite_existing: bool | None,
        refresh_token: int | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "rules-product-enrichment-apply-safe-button":
            rows = [
                row
                for row in (all_rows or [])
                if float(str(row.get("confidence") or "0").replace(",", ".")) >= 0.80
            ]
        else:
            rows = selected_rows or []
        if not rows:
            return "Keine passenden Vorschläge ausgewählt.", no_update, no_update
        result = apply_product_data_enrichment(session, rows, overwrite_existing=bool(overwrite_existing), created_by="rules_enrichment_gui")
        return (
            f"{result['applied_count']} Vorschläge übernommen, {result['skipped_count']} übersprungen.",
            (refresh_token or 0) + 1,
            [],
        )

    @app.callback(
        Output("last-product-clicked-id", "data"),
        Output("last-product-click-event", "data"),
        Input("products-grid", "cellClicked"),
        prevent_initial_call=True,
    )
    def store_last_product_clicked_id(cell_event: dict | None):
        if not cell_event:
            return no_update, no_update
        row = cell_event.get("data") or {}
        if row.get("id") is None:
            return no_update, no_update
        product_id = int(row["id"])
        return product_id, {"id": product_id, "ts": cell_event.get("timestamp")}

    @app.callback(Output("selected-product-ids", "data"), Input("products-grid", "selectedRows"))
    def store_selected_product_ids(selected_rows: list[dict] | None) -> list[int]:
        return [int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None]

    @app.callback(Output("product-focus-id", "data"), Input("products-grid", "selectedRows"))
    def store_product_focus_id(selected_rows: list[dict] | None):
        if not selected_rows:
            return no_update
        first = selected_rows[0]
        return int(first["id"]) if first.get("id") is not None else no_update

    @app.callback(
        Output("active-product-row", "data", allow_duplicate=True),
        Output("products-grid", "selectedRows", allow_duplicate=True),
        Input("products-grid", "cellRendererData"),
        prevent_initial_call=True,
    )
    def activate_product_from_renderer(renderer_data: dict | None):
        if not renderer_data:
            return no_update, no_update
        value = renderer_data.get("value") or {}
        if value.get("action") != "activate_product":
            return no_update, no_update
        row = value.get("row") or {}
        if not row.get("id"):
            return no_update, no_update
        return row, [row]

    @app.callback(
        Output("active-product-row", "data"),
        Input("products-grid", "cellClicked"),
        Input("products-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def store_active_product_row(cell_event: dict | None, selected_rows: list[dict] | None):
        trigger = ctx.triggered_id
        if trigger == "products-grid" and cell_event:
            row = cell_event.get("data") or {}
            return row or no_update
        if selected_rows:
            return selected_rows[0]
        return no_update

    @app.callback(Output("selected-variant-ids", "data"), Input("variants-grid", "selectedRows"))
    def store_selected_variant_ids(selected_rows: list[dict] | None) -> list[int]:
        return [int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None]

    @app.callback(
        Output("product-selection-summary", "children"),
        Input("selected-product-ids", "data"),
    )
    def render_product_selection_summary(selected_ids: list[int] | None) -> str:
        count = len(selected_ids or [])
        return f"{count} Produkte markiert"

    @app.callback(
        Output("variant-selection-summary", "children"),
        Input("selected-variant-ids", "data"),
    )
    def render_variant_selection_summary(selected_ids: list[int] | None) -> str:
        count = len(selected_ids or [])
        return f"{count} Varianten markiert"

    @app.callback(
        Output("variant-selection-summary-modal", "children"),
        Input("selected-variant-ids", "data"),
    )
    def render_variant_selection_summary_modal(selected_ids: list[int] | None) -> str:
        count = len(selected_ids or [])
        return f"{count} Varianten markiert"

    @app.callback(
        Output("product-channel-actions", "style"),
        Output("product-channel-action-count", "children"),
        Input("selected-product-ids", "data"),
        Input("products-grid", "selectedRows"),
        Input("product-channel-include-variants", "value"),
    )
    @_with_session
    def render_product_channel_actions(
        session: Session,
        selected_product_ids: list[int] | None,
        selected_rows: list[dict] | None,
        include_values: list[str] | None,
    ):
        product_count = len(selected_product_ids or [])
        include_variants = "include" in (include_values or [])
        variant_count = 0
        if include_variants:
            variant_count = len(variant_ids_for_products(session, selected_product_ids or []))
            if variant_count == 0:
                variant_count = sum(int(row.get("variant_count") or 0) for row in (selected_rows or []))
        summary = f"{product_count} Produkte"
        if include_variants:
            summary = f"{summary} und {variant_count} Varianten ausgewählt"
        else:
            summary = f"{summary} ausgewählt"
        return _channel_bulk_actions_style(product_count > 0), summary

    @app.callback(
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("selected-product-ids", "data"),
        Input("product-channel-include-variants", "value"),
        State("snapshot-store", "data"),
        State("variants-grid", "rowData"),
        prevent_initial_call=True,
    )
    def sync_related_variant_selection(
        selected_product_ids: list[int] | None,
        include_values: list[str] | None,
        snapshot: dict | None,
        variant_rows: list[dict] | None,
    ):
        include_variants = "include" in (include_values or [])
        if not include_variants:
            return [] if ctx.triggered_id == "product-channel-include-variants" else no_update
        product_ids = {int(product_id) for product_id in (selected_product_ids or [])}
        if not product_ids:
            return []
        rows = (snapshot or {}).get("variants") or variant_rows or []
        return [row for row in rows if row.get("product_id") is not None and int(row["product_id"]) in product_ids]

    @app.callback(
        Output("variant-channel-actions", "style"),
        Output("variant-channel-action-count", "children"),
        Input("selected-variant-ids", "data"),
    )
    def render_variant_channel_actions(selected_variant_ids: list[int] | None):
        variant_count = len(selected_variant_ids or [])
        return {"display": "block", "marginBottom": "12px"}, f"{variant_count} Varianten ausgewählt"

    @app.callback(
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("variant-clear-context-selection-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_variant_context_selection(n_clicks: int | None):
        if not n_clicks:
            return no_update
        return []

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Input("variant-edit-selected-button", "n_clicks"),
        State("selected-variant-ids", "data"),
        prevent_initial_call=True,
    )
    def edit_selected_variant_hint(n_clicks: int | None, selected_variant_ids: list[int] | None):
        if not n_clicks:
            return no_update
        if not selected_variant_ids:
            return "Keine Variante ausgewählt."
        if len(selected_variant_ids) > 1:
            return "Mehrere Varianten ausgewählt. Bitte eine einzelne Variante markieren oder direkt in der Tabelle bearbeiten."
        return "Variante ist markiert. Felder können direkt in der Varianten-Tabelle bearbeitet werden."

    @app.callback(
        Output("product-bulk-edit-modal", "style"),
        Output("product-bulk-edit-context", "data"),
        Output("product-bulk-edit-summary", "children"),
        Output("product-bulk-edit-result", "children", allow_duplicate=True),
        Output("product-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("product-bulk-edit-open-button", "n_clicks"),
        Input("product-bulk-edit-close-button", "n_clicks"),
        State("selected-product-ids", "data"),
        prevent_initial_call=True,
    )
    def toggle_product_bulk_edit_modal(open_clicks: int | None, close_clicks: int | None, selected_product_ids: list[int] | None):
        if ctx.triggered_id == "product-bulk-edit-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "", "", []
        if not open_clicks:
            return no_update, no_update, no_update, no_update, no_update
        product_ids = [int(product_id) for product_id in (selected_product_ids or [])]
        if not product_ids:
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "", "Bitte zuerst mindestens ein Produkt auswählen.", []
        return PRODUCT_ENRICH_MODAL_VISIBLE, {"product_ids": product_ids}, f"{len(product_ids)} Produkte ausgewählt.", "", []

    @app.callback(
        Output("variant-bulk-edit-modal", "style"),
        Output("variant-bulk-edit-context", "data"),
        Output("variant-bulk-edit-summary", "children"),
        Output("variant-bulk-edit-result", "children", allow_duplicate=True),
        Output("variant-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("variant-bulk-edit-open-button", "n_clicks"),
        Input("variant-bulk-edit-close-button", "n_clicks"),
        State("selected-variant-ids", "data"),
        prevent_initial_call=True,
    )
    def toggle_variant_bulk_edit_modal(open_clicks: int | None, close_clicks: int | None, selected_variant_ids: list[int] | None):
        if ctx.triggered_id == "variant-bulk-edit-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "", "", []
        if not open_clicks:
            return no_update, no_update, no_update, no_update, no_update
        variant_ids = [int(variant_id) for variant_id in (selected_variant_ids or [])]
        if not variant_ids:
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "", "Bitte zuerst mindestens eine Variante auswählen.", []
        return PRODUCT_ENRICH_MODAL_VISIBLE, {"variant_ids": variant_ids}, f"{len(variant_ids)} Varianten ausgewählt.", "", []

    @app.callback(
        Output("product-bulk-edit-result", "children", allow_duplicate=True),
        Output("product-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("product-bulk-edit-preview-button", "n_clicks"),
        State("product-bulk-edit-context", "data"),
        State("product-bulk-edit-fields", "value"),
        State("product-bulk-edit-source-language", "value"),
        State("product-bulk-edit-brand", "value"),
        State("product-bulk-edit-status", "value"),
        State("product-bulk-edit-is-chemical", "value"),
        State("product-bulk-edit-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def preview_product_bulk_edit(
        session: Session,
        n_clicks: int | None,
        context: dict | None,
        fields: list[str] | None,
        source_language: str | None,
        brand_name: str | None,
        status: str | None,
        is_chemical: bool | None,
        options: list[str] | None,
    ):
        if not n_clicks:
            return no_update, no_update
        result = bulk_update_products(
            session,
            (context or {}).get("product_ids") or [],
            _product_bulk_updates(fields, source_language, brand_name, status, is_chemical),
            apply=False,
            only_empty="only_empty" in (options or []),
        )
        return _bulk_edit_message("Produkte", result, apply=False), result.get("rows") or []

    @app.callback(
        Output("variant-bulk-edit-result", "children", allow_duplicate=True),
        Output("variant-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("variant-bulk-edit-preview-button", "n_clicks"),
        State("variant-bulk-edit-context", "data"),
        State("variant-bulk-edit-fields", "value"),
        State("variant-bulk-edit-status", "value"),
        State("variant-bulk-edit-price", "value"),
        State("variant-bulk-edit-currency", "value"),
        State("variant-bulk-edit-cost-price", "value"),
        State("variant-bulk-edit-cost-currency", "value"),
        State("variant-bulk-edit-stock-qty", "value"),
        State("variant-bulk-edit-barcode", "value"),
        State("variant-bulk-edit-option-name", "value"),
        State("variant-bulk-edit-option-value", "value"),
        State("variant-bulk-edit-packaging", "value"),
        State("variant-bulk-edit-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def preview_variant_bulk_edit(
        session: Session,
        n_clicks: int | None,
        context: dict | None,
        fields: list[str] | None,
        status: str | None,
        price: float | None,
        currency: str | None,
        cost_price: float | None,
        cost_currency: str | None,
        stock_qty: int | None,
        barcode: str | None,
        option_name: str | None,
        option_value: str | None,
        packaging: str | None,
        options: list[str] | None,
    ):
        if not n_clicks:
            return no_update, no_update
        try:
            result = bulk_update_variants(
                session,
                (context or {}).get("variant_ids") or [],
                _variant_bulk_updates(fields, status, price, currency, cost_price, cost_currency, stock_qty, barcode, option_name, option_value, packaging),
                apply=False,
                only_empty="only_empty" in (options or []),
            )
        except Exception as exc:
            return f"Varianten: Vorschau fehlgeschlagen · {exc}", []
        return _bulk_edit_message("Varianten", result, apply=False), result.get("rows") or []

    @app.callback(
        Output("product-bulk-edit-confirm", "displayed"),
        Output("product-bulk-edit-confirm", "message"),
        Input("product-bulk-edit-apply-button", "n_clicks"),
        State("product-bulk-edit-context", "data"),
        State("product-bulk-edit-fields", "value"),
        prevent_initial_call=True,
    )
    def confirm_product_bulk_edit(n_clicks: int | None, context: dict | None, fields: list[str] | None):
        product_count = len((context or {}).get("product_ids") or [])
        if not n_clicks or product_count <= 0 or not fields:
            return False, no_update
        return True, f"{product_count} Produkte bearbeiten? Es werden nur diese Felder geändert: {', '.join(fields)}. Vorher wird ein Backup erstellt."

    @app.callback(
        Output("variant-bulk-edit-confirm", "displayed"),
        Output("variant-bulk-edit-confirm", "message"),
        Input("variant-bulk-edit-apply-button", "n_clicks"),
        State("variant-bulk-edit-context", "data"),
        State("variant-bulk-edit-fields", "value"),
        prevent_initial_call=True,
    )
    def confirm_variant_bulk_edit(n_clicks: int | None, context: dict | None, fields: list[str] | None):
        variant_count = len((context or {}).get("variant_ids") or [])
        if not n_clicks or variant_count <= 0 or not fields:
            return False, no_update
        return True, f"{variant_count} Varianten bearbeiten? Es werden nur diese Felder geändert: {', '.join(fields)}. Vorher wird ein Backup erstellt."

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("product-bulk-edit-result", "children", allow_duplicate=True),
        Output("product-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("product-bulk-edit-confirm", "submit_n_clicks"),
        State("refresh-token", "data"),
        State("product-bulk-edit-context", "data"),
        State("product-bulk-edit-fields", "value"),
        State("product-bulk-edit-source-language", "value"),
        State("product-bulk-edit-brand", "value"),
        State("product-bulk-edit-status", "value"),
        State("product-bulk-edit-is-chemical", "value"),
        State("product-bulk-edit-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_product_bulk_edit(
        session: Session,
        submit_n_clicks: int | None,
        refresh_token: int | None,
        context: dict | None,
        fields: list[str] | None,
        source_language: str | None,
        brand_name: str | None,
        status: str | None,
        is_chemical: bool | None,
        options: list[str] | None,
    ):
        if not submit_n_clicks:
            return no_update, no_update, no_update, no_update
        result = bulk_update_products(
            session,
            (context or {}).get("product_ids") or [],
            _product_bulk_updates(fields, source_language, brand_name, status, is_chemical),
            apply=True,
            only_empty="only_empty" in (options or []),
        )
        message = _bulk_edit_message("Produkte", result, apply=True)
        return message, (refresh_token or 0) + 1, message, result.get("rows") or []

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("variant-bulk-edit-result", "children", allow_duplicate=True),
        Output("variant-bulk-edit-preview-grid", "rowData", allow_duplicate=True),
        Input("variant-bulk-edit-confirm", "submit_n_clicks"),
        State("refresh-token", "data"),
        State("variant-bulk-edit-context", "data"),
        State("variant-bulk-edit-fields", "value"),
        State("variant-bulk-edit-status", "value"),
        State("variant-bulk-edit-price", "value"),
        State("variant-bulk-edit-currency", "value"),
        State("variant-bulk-edit-cost-price", "value"),
        State("variant-bulk-edit-cost-currency", "value"),
        State("variant-bulk-edit-stock-qty", "value"),
        State("variant-bulk-edit-barcode", "value"),
        State("variant-bulk-edit-option-name", "value"),
        State("variant-bulk-edit-option-value", "value"),
        State("variant-bulk-edit-packaging", "value"),
        State("variant-bulk-edit-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_variant_bulk_edit(
        session: Session,
        submit_n_clicks: int | None,
        refresh_token: int | None,
        context: dict | None,
        fields: list[str] | None,
        status: str | None,
        price: float | None,
        currency: str | None,
        cost_price: float | None,
        cost_currency: str | None,
        stock_qty: int | None,
        barcode: str | None,
        option_name: str | None,
        option_value: str | None,
        packaging: str | None,
        options: list[str] | None,
    ):
        if not submit_n_clicks:
            return no_update, no_update, no_update, no_update
        try:
            result = bulk_update_variants(
                session,
                (context or {}).get("variant_ids") or [],
                _variant_bulk_updates(fields, status, price, currency, cost_price, cost_currency, stock_qty, barcode, option_name, option_value, packaging),
                apply=True,
                only_empty="only_empty" in (options or []),
            )
        except Exception as exc:
            return f"Varianten: Apply fehlgeschlagen · {exc}", no_update, f"Varianten: Apply fehlgeschlagen · {exc}", []
        message = _bulk_edit_message("Varianten", result, apply=True)
        return message, (refresh_token or 0) + 1, message, result.get("rows") or []

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("variant-archive-selected-button", "n_clicks"),
        State("selected-variant-ids", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def archive_selected_variants_callback(session: Session, n_clicks: int | None, selected_variant_ids: list[int] | None, refresh_token: int | None):
        if not n_clicks:
            return no_update, no_update, no_update
        ids = [int(row) for row in (selected_variant_ids or [])]
        if not ids:
            return "Keine Variante ausgewählt.", no_update, no_update
        count = archive_variants(session, ids)
        return f"{count} Variante(n) archiviert.", (refresh_token or 0) + 1, []

    @app.callback(
        Output("variant-delete-confirm", "displayed"),
        Input("variant-delete-selected-button", "n_clicks"),
        State("selected-variant-ids", "data"),
        prevent_initial_call=True,
    )
    def ask_variant_delete_confirm(n_clicks: int | None, selected_variant_ids: list[int] | None):
        if not n_clicks or not selected_variant_ids:
            return False
        return True

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("variant-delete-confirm", "submit_n_clicks"),
        State("selected-variant-ids", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_selected_variants_callback(session: Session, submit_n_clicks: int | None, selected_variant_ids: list[int] | None, refresh_token: int | None):
        if not submit_n_clicks:
            return no_update, no_update, no_update
        ids = [int(row) for row in (selected_variant_ids or [])]
        if not ids:
            return "Keine Variante ausgewählt.", no_update, no_update
        result = delete_or_archive_variants(session, ids)
        deleted = result.get("deleted", 0)
        archived = result.get("archived_due_to_relations", 0)
        if archived and not deleted:
            message = f"{archived} Variante(n) konnten wegen abhängiger Daten nicht hart gelöscht werden und wurden archiviert."
        elif archived:
            message = f"{deleted} Variante(n) gelöscht, {archived} wegen abhängiger Daten archiviert."
        else:
            message = f"{deleted} Variante(n) gelöscht."
        return message, (refresh_token or 0) + 1, []

    @app.callback(
        Output("channel-bulk-modal", "style"),
        Output("channel-bulk-action-context", "data"),
        Output("channel-bulk-action", "options"),
        Output("channel-bulk-action", "value"),
        Output("channel-bulk-summary", "children"),
        Input("product-channel-action-open-button", "n_clicks"),
        Input("product-listings-action-open-button", "n_clicks"),
        Input("product-category-action-open-button", "n_clicks"),
        Input("product-variant-listings-action-open-button", "n_clicks"),
        Input("variant-channel-action-open-button", "n_clicks"),
        Input("variant-listings-action-open-button", "n_clicks"),
        Input("channel-bulk-close-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("selected-variant-ids", "data"),
        State("product-channel-include-variants", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def toggle_channel_bulk_modal(
        session: Session,
        *_args,
    ):
        trigger = ctx.triggered_id
        if trigger == "channel-bulk-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, [], None, ""
        product_ids = _args[-3] or []
        variant_ids = _args[-2] or []
        include_values = _args[-1] or []
        context = "variants" if trigger in {"variant-channel-action-open-button", "variant-listings-action-open-button"} else "products"
        include_variants = context == "products" and "include" in include_values
        effective_variant_ids = list(variant_ids or [])
        if include_variants:
            effective_variant_ids = variant_ids_for_products(session, product_ids or [])
        if context == "products" and not product_ids:
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, [], None, "Keine Produkte ausgewählt."
        if context == "variants" and not effective_variant_ids:
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, [], None, "Keine Varianten ausgewählt."
        action = _bulk_action_default(trigger, context)
        action_options = _bulk_action_options(context)
        payload = {
            "context": context,
            "product_ids": product_ids or [],
            "variant_ids": effective_variant_ids,
            "include_variants": include_variants,
        }
        summary = f"{len(product_ids or [])} Produkte · {len(effective_variant_ids or [])} Varianten betroffen"
        return PRODUCT_ENRICH_MODAL_VISIBLE, payload, action_options, action, summary

    @app.callback(
        Output("channel-bulk-channel-category-id", "options"),
        Output("channel-bulk-channel-category-id", "value"),
        Input("channel-bulk-sales-channel-id", "value"),
        State("snapshot-store", "data"),
    )
    def filter_channel_bulk_category_options(sales_channel_id: int | None, snapshot: dict | None):
        options = (snapshot or {}).get("channel_category_options", [])
        if not sales_channel_id:
            return [], None
        filtered = [item for item in options if item.get("sales_channel_id") == sales_channel_id]
        return filtered, None

    @app.callback(
        Output("channel-bulk-confirm", "displayed"),
        Output("channel-bulk-confirm", "message"),
        Input("channel-bulk-run-button", "n_clicks"),
        State("channel-bulk-action", "value"),
        State("channel-bulk-action-context", "data"),
        prevent_initial_call=True,
    )
    def confirm_channel_bulk_action(_: int | None, action: str | None, payload: dict | None):
        payload = payload or {}
        product_count = len(payload.get("product_ids") or [])
        variant_count = len(payload.get("variant_ids") or [])
        if not action or (product_count <= 0 and variant_count <= 0):
            return False, no_update
        return True, f"Kanal-Aktion '{action}' für {product_count} Produkte und {variant_count} Varianten ausführen?"

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("channel-bulk-modal", "style", allow_duplicate=True),
        Input("channel-bulk-confirm", "submit_n_clicks"),
        State("refresh-token", "data"),
        State("channel-bulk-action-context", "data"),
        State("channel-bulk-action", "value"),
        State("channel-bulk-sales-channel-id", "value"),
        State("channel-bulk-channel-category-id", "value"),
        State("channel-bulk-allowed", "value"),
        State("channel-bulk-is-active", "value"),
        State("channel-bulk-publication-status", "value"),
        State("channel-bulk-active-from", "value"),
        State("channel-bulk-active-until", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_channel_bulk_action(
        session: Session,
        _: int | None,
        refresh_token: int,
        payload: dict | None,
        action: str | None,
        sales_channel_id: int | None,
        channel_category_id: int | None,
        allowed: bool | None,
        is_active: bool | None,
        publication_status: str | None,
        active_from: str | None,
        active_until: str | None,
    ):
        payload = payload or {}
        product_ids = payload.get("product_ids") or []
        variant_ids = payload.get("variant_ids") or []
        if not action or not sales_channel_id:
            return "Aktion und Vertriebskanal sind Pflicht.", no_update, no_update
        try:
            product_count = 0
            variant_count = 0
            if action in {"product_listings", "assign_sales_channel"} and product_ids:
                product_count = bulk_upsert_product_channel_listings(
                    session,
                    product_ids,
                    int(sales_channel_id),
                    allowed=bool(allowed),
                    is_active=bool(is_active),
                    publication_status=publication_status or "published",
                    active_from=active_from,
                    active_until=active_until,
                )
            if action in {"variant_listings", "assign_sales_channel"} and variant_ids:
                variant_count = bulk_upsert_variant_channel_listings(
                    session,
                    variant_ids,
                    int(sales_channel_id),
                    allowed=bool(allowed),
                    is_active=bool(is_active),
                    publication_status=publication_status or "published",
                )
            if action in {"product_category_mappings", "assign_channel_category"}:
                if not channel_category_id:
                    return "Kanal-Kategorie ist für diese Aktion Pflicht.", no_update, no_update
                if payload.get("context") == "variants":
                    variant_count = bulk_upsert_variant_category_mappings(
                        session,
                        variant_ids,
                        int(sales_channel_id),
                        int(channel_category_id),
                        is_primary=True,
                    )
                else:
                    product_count = bulk_upsert_product_category_mappings(
                        session,
                        product_ids,
                        int(sales_channel_id),
                        int(channel_category_id),
                        is_primary=True,
                    )
        except ValueError as exc:
            return str(exc), no_update, no_update
        return (
            f"Kanal-Aktion ausgeführt: {product_count} Produkte, {variant_count} Varianten.",
            (refresh_token or 0) + 1,
            PRODUCT_ENRICH_MODAL_HIDDEN,
        )

    @app.callback(
        Output("translation-source-language", "options"),
        Output("translation-target-languages", "options"),
        Output("translation-prompt-language", "options"),
        Output("product-text-source-language", "options"),
        Output("product-text-target-languages", "options"),
        Input("snapshot-store", "data"),
    )
    def load_translation_language_options(snapshot: dict | None):
        rows = (snapshot or {}).get("languages", [])
        options = [{"label": f"{row.get('name')} ({row.get('code')})", "value": row.get("code")} for row in rows if row.get("enabled")]
        return options, options, options, options, options

    @app.callback(
        Output("translation-bulk-modal", "style"),
        Output("translation-bulk-context", "data"),
        Output("translation-bulk-summary", "children"),
        Output("translation-source-language", "value"),
        Output("translation-provider-status", "children"),
        Input("product-translation-open-button", "n_clicks"),
        Input("translation-bulk-close-button", "n_clicks"),
        State("selected-product-ids", "data"),
        State("products-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def toggle_translation_modal(
        _open_clicks: int | None,
        _close_clicks: int | None,
        selected_product_ids: list[int] | None,
        selected_rows: list[dict] | None,
    ):
        if ctx.triggered_id == "translation-bulk-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "", None, ""
        product_ids = selected_product_ids or []
        if not product_ids:
            return PRODUCT_ENRICH_MODAL_HIDDEN, {}, "Keine Produkte ausgewählt.", None, ""
        available_codes = set()
        with session_scope(get_pim_settings().database_url) as session:
            available_codes = {row["code"] for row in list_languages(session, enabled_only=True)}
        raw_source_language = (selected_rows or [{}])[0].get("source_language")
        source_language = raw_source_language if raw_source_language in available_codes else None
        if source_language is None and raw_source_language:
            base_language = str(raw_source_language).split("-", 1)[0]
            source_language = base_language if base_language in available_codes else None
        if source_language is None:
            source_language = next(
                (code for code in ["de-CH", "de", "en", "fr", "it", "es"] if code in available_codes),
                next(iter(sorted(available_codes)), None),
            )
        config = get_translation_config_status()
        language_note = "" if raw_source_language == source_language else f" · Hinweis: Produkt-Originalsprache {raw_source_language or '-'} ist nicht als aktive Sprache vorhanden, bitte prüfen."
        status = f"Provider: {config.get('provider')} · Modell: {config.get('model')} · {'aktiv' if config.get('enabled') else 'OPENAI_API_KEY fehlt'}{language_note}"
        return PRODUCT_ENRICH_MODAL_VISIBLE, {"product_ids": product_ids}, f"{len(product_ids)} Produkte ausgewählt.", source_language, status

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("translation-result", "children"),
        Input("translation-generate-button", "n_clicks"),
        State("refresh-token", "data"),
        State("translation-bulk-context", "data"),
        State("translation-source-language", "value"),
        State("translation-target-languages", "value"),
        State("translation-overwrite-existing", "value"),
        State("translation-overwrite-original", "value"),
        State("translation-include-variants", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def generate_product_translations_callback(
        session: Session,
        _clicks: int | None,
        refresh_token: int,
        context_payload: dict | None,
        source_language: str | None,
        target_languages: list[str] | None,
        overwrite_existing: bool | None,
        overwrite_original: bool | None,
        include_variants: bool | None,
    ):
        product_ids = (context_payload or {}).get("product_ids") or []
        result = generate_product_translations(
            session,
            [int(product_id) for product_id in product_ids],
            target_languages or [],
            source_language_code=source_language,
            overwrite_existing=bool(overwrite_existing),
            allow_original_overwrite=bool(overwrite_original),
            include_variants=bool(include_variants),
        )
        summary = f"Übersetzungen: {result.get('generated', 0)} erstellt, {result.get('skipped', 0)} übersprungen, {result.get('failed', 0)} fehlgeschlagen."
        rows = list(result.get("results") or [])
        details = html.Div(
            [
                html.Div(summary, style={"fontWeight": "700", "marginBottom": "6px"}),
                html.Div(f"Status: {result.get('status') or 'unbekannt'} · Einträge: {len(rows)}"),
                html.Ul(
                    [
                        html.Li(
                            [
                                html.Span(
                                    (
                                        f"Variante {row.get('variant_id')} / Produkt {row.get('product_id')} / {row.get('language_code') or '-'}: {row.get('status')}"
                                        if row.get("variant_id")
                                        else f"Produkt {row.get('product_id')} / {row.get('language_code') or '-'}: {row.get('status')}"
                                    ),
                                    style={"fontWeight": "600"},
                                ),
                                html.Span(f" · {row.get('message') or ''}"),
                            ],
                            style={"color": "#9f1239" if row.get("status") == "failed" else "#166534" if row.get("status") == "generated" else "#854d0e"},
                        )
                        for row in rows[:20]
                    ],
                    style={"margin": "8px 0 0 18px", "padding": "0"},
                ),
            ]
        )
        first_failed = next((row for row in rows if row.get("status") == "failed"), None)
        message = result.get("message") or (f"{summary} Fehler: {first_failed.get('message')}" if first_failed else summary)
        return str(message), (refresh_token or 0) + 1, details

    @app.callback(
        Output("translation-prompt-modal", "style"),
        Output("translation-prompt-language", "value"),
        Input("product-translation-prompts-button", "n_clicks"),
        Input("translation-prompt-close-button", "n_clicks"),
        State("snapshot-store", "data"),
        prevent_initial_call=True,
    )
    def toggle_translation_prompt_modal(_open_clicks: int | None, _close_clicks: int | None, snapshot: dict | None):
        if ctx.triggered_id == "translation-prompt-close-button":
            return PRODUCT_ENRICH_MODAL_HIDDEN, None
        languages = (snapshot or {}).get("languages", [])
        default_language = next((row.get("code") for row in languages if row.get("enabled")), "de")
        return PRODUCT_ENRICH_MODAL_VISIBLE, default_language

    @app.callback(
        Output("translation-prompt-system", "value"),
        Output("translation-prompt-template", "value"),
        Input("translation-prompt-language", "value"),
        Input("snapshot-store", "data"),
    )
    def load_translation_prompt_form(language_code: str | None, snapshot: dict | None):
        if not language_code:
            return "", DEFAULT_PROMPT_TEMPLATE
        prompts = (snapshot or {}).get("translation_prompts", [])
        row = next((item for item in prompts if item.get("language_code") == language_code), None)
        if row is None:
            return "", DEFAULT_PROMPT_TEMPLATE
        return row.get("systemPrompt") or "", row.get("promptTemplate") or DEFAULT_PROMPT_TEMPLATE

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("translation-prompt-status", "children"),
        Input("translation-prompt-save-button", "n_clicks"),
        Input("translation-prompt-reset-button", "n_clicks"),
        State("refresh-token", "data"),
        State("translation-prompt-language", "value"),
        State("translation-prompt-system", "value"),
        State("translation-prompt-template", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_translation_prompt_callback(
        session: Session,
        _save_clicks: int | None,
        _reset_clicks: int | None,
        refresh_token: int,
        language_code: str | None,
        system_prompt: str | None,
        prompt_template: str | None,
    ):
        if not language_code:
            return "Sprache fehlt.", no_update, no_update
        if ctx.triggered_id == "translation-prompt-reset-button":
            reset_translation_prompt(session, language_code)
            message = f"Prompt für {language_code} auf Standard zurückgesetzt."
        else:
            save_translation_prompt(session, language_code, prompt_template or DEFAULT_PROMPT_TEMPLATE, system_prompt)
            message = f"Prompt für {language_code} gespeichert."
        return message, (refresh_token or 0) + 1, message

    @app.callback(
        Output("combined-selection-summary", "children"),
        Input("selected-product-ids", "data"),
        Input("selected-variant-ids", "data"),
    )
    def render_combined_selection_summary(product_ids: list[int] | None, variant_ids: list[int] | None) -> str:
        return f"Aktuell markiert: {len(product_ids or [])} Produkte, {len(variant_ids or [])} Varianten"

    @app.callback(
        Output("products-grid", "selectedRows", allow_duplicate=True),
        Input("product-select-all-button", "n_clicks"),
        Input("product-select-filtered-button", "n_clicks"),
        Input("product-select-page-button", "n_clicks"),
        Input("product-clear-selection-button", "n_clicks"),
        State("products-grid", "virtualRowData"),
        State("products-grid", "rowData"),
        State("products-grid", "paginationInfo"),
        prevent_initial_call=True,
    )
    def control_product_selection(
        _: int | None,
        __: int | None,
        ___: int | None,
        ____: int | None,
        virtual_rows: list[dict] | None,
        row_data: list[dict] | None,
        pagination_info: dict | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "product-clear-selection-button":
            return []
        if trigger == "product-select-all-button":
            return row_data or []
        if trigger == "product-select-filtered-button":
            return virtual_rows if virtual_rows is not None else (row_data or [])
        if trigger == "product-select-page-button":
            return _page_rows(virtual_rows if virtual_rows is not None else row_data, pagination_info)
        return no_update

    @app.callback(
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("variant-select-all-button-modal", "n_clicks"),
        Input("variant-select-filtered-button-modal", "n_clicks"),
        Input("variant-select-page-button-modal", "n_clicks"),
        Input("variant-clear-selection-button-modal", "n_clicks"),
        State("variants-grid", "virtualRowData"),
        State("variants-grid", "rowData"),
        State("variants-grid", "paginationInfo"),
        prevent_initial_call=True,
    )
    def control_variant_selection(
        _: int | None,
        __: int | None,
        ___: int | None,
        ____: int | None,
        virtual_rows: list[dict] | None,
        row_data: list[dict] | None,
        pagination_info: dict | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "variant-clear-selection-button-modal":
            return []
        if trigger == "variant-select-all-button-modal":
            return row_data or []
        if trigger == "variant-select-filtered-button-modal":
            return virtual_rows if virtual_rows is not None else (row_data or [])
        if trigger == "variant-select-page-button-modal":
            return _page_rows(virtual_rows if virtual_rows is not None else row_data, pagination_info)
        return no_update

    @app.callback(
        Output("snapshot-store", "data"),
        Input("refresh-token", "data"),
        Input("product-list-status-filter", "value"),
        Input("variant-list-status-filter", "value"),
    )
    @_with_session
    def load_snapshot(session: Session, _: int, product_archive_filter: str | None, variant_archive_filter: str | None) -> dict:
        return app_snapshot(
            session,
            product_archive_filter=product_archive_filter or "active",
            variant_archive_filter=variant_archive_filter or "active",
        )

    @app.callback(
        Output("metric-products", "children"),
        Output("metric-variants", "children"),
        Output("metric-assets", "children"),
        Output("metric-import-jobs", "children"),
        Output("products-grid", "rowData"),
        Output("assets-grid", "rowData"),
        Output("jobs-grid", "rowData"),
        Output("jobs-grid-dashboard", "rowData"),
        Output("attributes-grid", "rowData"),
        Output("families-grid", "rowData"),
        Output("languages-grid", "rowData"),
        Output("translations-grid", "rowData"),
        Output("variant-translations-grid", "rowData"),
        Output("rules-grid", "rowData"),
        Output("sales-channels-grid", "rowData"),
        Output("channel-categories-grid", "rowData"),
        Output("product-brand", "options"),
        Output("chemistry-product-brand", "options"),
        Output("categories-sales-channel-code", "options"),
        Output("product-category-channel-code", "options"),
        Output("import-sales-channel-code", "options"),
        Output("channel-bulk-sales-channel-id", "options"),
        Output("sales-channel-form-id", "options"),
        Output("channel-category-form-sales-channel-id", "options"),
        Output("product-channel-mapping-sales-channel-id", "options"),
        Input("snapshot-store", "data"),
    )
    def apply_snapshot(snapshot: dict | None):
        snapshot = snapshot or {"counts": {}}
        counts = snapshot.get("counts", {})
        return (
            counts.get("products", 0),
            counts.get("variants", 0),
            counts.get("assets", 0),
            counts.get("import_jobs", 0),
            snapshot.get("products", []),
            snapshot.get("assets", []),
            snapshot.get("jobs", []),
            snapshot.get("jobs", []),
            snapshot.get("attributes", []),
            snapshot.get("families", []),
            snapshot.get("languages", []),
            snapshot.get("translations", []),
            snapshot.get("variant_translations", []),
            snapshot.get("rules", []),
            snapshot.get("sales_channels", []),
            snapshot.get("channel_categories", []),
            snapshot.get("brand_options", []),
            snapshot.get("brand_options", []),
            snapshot.get("sales_channel_code_options", []),
            snapshot.get("sales_channel_code_options", []),
            snapshot.get("sales_channel_code_options", []),
            snapshot.get("sales_channel_options", []),
            snapshot.get("sales_channel_options", []),
            snapshot.get("sales_channel_options", []),
            snapshot.get("sales_channel_options", []),
        )

    @app.callback(
        Output("sales-channel-form-code", "value"),
        Output("sales-channel-form-name", "value"),
        Output("sales-channel-form-is-active", "value"),
        Output("sales-channel-form-sort-order", "value"),
        Input("sales-channel-form-id", "value"),
        State("snapshot-store", "data"),
    )
    def load_sales_channel_form(channel_id: int | None, snapshot: dict | None):
        if not channel_id:
            return None, None, True, 0
        rows = (snapshot or {}).get("sales_channels", [])
        row = next((item for item in rows if item.get("id") == channel_id), None)
        if row is None:
            return None, None, True, 0
        return row.get("code"), row.get("name"), bool(row.get("is_active")), row.get("sort_order") or 0

    @app.callback(
        Output("channel-export-code", "options"),
        Input("snapshot-store", "data"),
    )
    def load_channel_export_options(snapshot: dict | None):
        rows = (snapshot or {}).get("sales_channels", [])
        return [{"label": f"{row.get('name')} ({row.get('code')})", "value": row.get("code")} for row in rows if row.get("code")]

    @app.callback(
        Output("channel-category-tree-sales-channel-id", "options"),
        Output("channel-category-tree-sales-channel-id", "value"),
        Input("snapshot-store", "data"),
        State("channel-category-tree-sales-channel-id", "value"),
    )
    def load_channel_category_tree_channel_options(snapshot: dict | None, current_value: int | None):
        rows = (snapshot or {}).get("sales_channels", [])
        options = [{"label": f"{row.get('name')} ({row.get('code')})", "value": row.get("id")} for row in rows if row.get("id")]
        allowed_ids = {item["value"] for item in options}
        if current_value in allowed_ids:
            return options, current_value
        voxster = next((row for row in rows if row.get("code") == DEFAULT_CATEGORY_CHANNEL_CODE), None)
        default_value = voxster.get("id") if voxster else (options[0]["value"] if options else None)
        return options, default_value

    @app.callback(
        Output("channel-category-tree-grid", "rowData"),
        Output("channel-category-tree-status", "children"),
        Input("channel-category-tree-sales-channel-id", "value"),
        Input("channel-category-tree-collapsed-store", "data"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_channel_category_tree_grid(
        session: Session,
        sales_channel_id: int | None,
        collapsed_ids: list[int] | None,
        _refresh_token: int | None,
    ):
        if not sales_channel_id:
            return [], "Bitte zuerst Vertriebskanal auswählen."
        tree_rows = get_channel_category_tree(session, int(sales_channel_id))
        grid_rows = _channel_category_tree_rows_for_grid(tree_rows, collapsed_ids)
        if not grid_rows:
            return [], "Für diesen Vertriebskanal sind noch keine Kanal-Kategorien vorhanden."
        return grid_rows, f"{len(tree_rows)} Kanal-Kategorien im gewählten Vertriebskanal."

    @app.callback(
        Output("channel-category-tree-collapsed-store", "data"),
        Input("channel-category-tree-grid", "cellRendererData"),
        Input("channel-category-tree-expand-all-button", "n_clicks"),
        Input("channel-category-tree-collapse-all-button", "n_clicks"),
        State("channel-category-tree-collapsed-store", "data"),
        State("channel-category-tree-grid", "rowData"),
        prevent_initial_call=True,
    )
    def toggle_channel_category_tree(
        renderer_data: dict | None,
        _expand_clicks: int | None,
        _collapse_clicks: int | None,
        collapsed_ids: list[int] | None,
        rows: list[dict] | None,
    ):
        trigger = ctx.triggered_id
        if trigger == "channel-category-tree-expand-all-button":
            return []
        if trigger == "channel-category-tree-collapse-all-button":
            return [int(row["id"]) for row in (rows or []) if row.get("has_children") and row.get("id") is not None]
        value = (renderer_data or {}).get("value") or {}
        category_id = value.get("id") or value.get("category_id")
        if not category_id:
            return no_update
        collapsed = {int(item) for item in (collapsed_ids or [])}
        normalized_id = int(category_id)
        if normalized_id in collapsed:
            collapsed.remove(normalized_id)
        else:
            collapsed.add(normalized_id)
        return sorted(collapsed)

    @app.callback(
        Output("selected-channel-category-id", "data"),
        Output("channel-category-tree-grid", "selectedRows", allow_duplicate=True),
        Input("channel-category-tree-grid", "cellClicked"),
        State("channel-category-tree-grid", "rowData"),
        prevent_initial_call=True,
    )
    def select_channel_category_tree_row(cell_event: dict | None, rows: list[dict] | None):
        if not cell_event:
            return no_update, no_update
        if cell_event.get("colId") == "tree_toggle":
            return no_update, no_update
        row = cell_event.get("data") or {}
        category_id = row.get("id")
        if not category_id:
            return no_update, no_update
        current_row = next((item for item in (rows or []) if item.get("id") == category_id), row)
        return int(category_id), [current_row]

    @app.callback(
        Output("selected-channel-category-id", "data", allow_duplicate=True),
        Input("channel-category-tree-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def select_channel_category_from_selected_rows(selected_rows: list[dict] | None):
        if not selected_rows:
            return no_update
        category_id = selected_rows[0].get("id")
        return int(category_id) if category_id is not None else no_update

    @app.callback(
        Output("channel-category-products-grid", "rowData"),
        Output("channel-category-products-status", "children"),
        Output("channel-category-breadcrumb", "children"),
        Input("selected-channel-category-id", "data"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_products_for_selected_channel_category(
        session: Session,
        category_id: int | None,
        _refresh_token: int | None,
    ):
        if not category_id:
            return [], "Bitte Kategorie im Baum auswählen.", ""
        category = session.get(ChannelCategory, int(category_id))
        if category is None:
            return [], "Kategorie nicht gefunden.", ""
        products = get_products_for_channel_category(session, int(category_id), include_variants=False)
        breadcrumb = f"{category.sales_channel.name if category.sales_channel else 'Vertriebskanal'} > {(category.external_path or category.name)}"
        if not products:
            return [], "Keine Produkte in dieser Kanal-Kategorie.", breadcrumb
        return products, f"{len(products)} Produkte in dieser Kanal-Kategorie.", breadcrumb

    @app.callback(
        Output("categories-grid", "rowData"),
        Output("category-parent-id", "options"),
        Output("category-detail-parent-id", "options"),
        Input("snapshot-store", "data"),
        Input("category-tree-collapsed-store", "data"),
        Input("categories-sales-channel-code", "value"),
    )
    def load_category_workspace(
        snapshot: dict | None,
        collapsed_category_ids: list[int] | None,
        sales_channel_code: str | None,
    ):
        snapshot = snapshot or {}
        categories = _filter_categories_for_channel(snapshot.get("categories", []), sales_channel_code)
        parent_options = _filter_category_options_for_channel(snapshot.get("category_parent_options", []), sales_channel_code)
        return (
            _category_rows_for_grid(categories, collapsed_category_ids),
            parent_options,
            parent_options,
        )

    @app.callback(
        Output("product-categories", "options"),
        Output("product-categories", "value"),
        Input("snapshot-store", "data"),
        Input("product-category-channel-code", "value"),
        Input("product-id", "value"),
        State("product-categories", "value"),
    )
    @_with_session
    def load_product_category_options(
        session: Session,
        snapshot: dict | None,
        sales_channel_code: str | None,
        product_id: int | None,
        current_values: list[int] | None,
    ):
        snapshot = snapshot or {}
        options = _filter_category_options_for_channel(snapshot.get("category_options", []), sales_channel_code)
        allowed_ids = {item["value"] for item in options}
        values = [item for item in (current_values or []) if item in allowed_ids]
        if not product_id:
            return options, values
        assignment = get_product_category_assignment_for_channel(
            session,
            int(product_id),
            sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
        )
        values = [item for item in assignment.get("category_ids", []) if item in allowed_ids]
        return options, values

    @app.callback(
        Output("category-products-grid", "rowData"),
        Output("category-products-status", "children"),
        Output("category-breadcrumb", "children"),
        Input("selected-category-id", "data"),
        Input("categories-sales-channel-code", "value"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_products_for_selected_category(
        session: Session,
        category_id: int | None,
        sales_channel_code: str | None,
        _refresh_token: int | None,
    ):
        if not sales_channel_code:
            return [], "Bitte zuerst Vertriebskanal auswählen.", ""
        if not category_id:
            return [], "Bitte Kategorie im Baum auswählen.", ""
        detail = get_category_detail(session, int(category_id), sales_channel_code=sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)
        if detail is None:
            return [], "Kategorie nicht gefunden oder gehört nicht zum gewählten Vertriebskanal.", ""
        products = get_products_for_category(session, int(category_id), include_variants=False)
        breadcrumb_parts = [detail.get("sales_channel_name") or detail.get("sales_channel_code")]
        if detail.get("parent_name"):
            breadcrumb_parts.append(detail["parent_name"])
        breadcrumb_parts.append(detail.get("name"))
        breadcrumb = " > ".join(str(part) for part in breadcrumb_parts if part)
        if not products:
            return [], "Keine Produkte in dieser Kategorie.", breadcrumb
        return products, f"{len(products)} Produkte in dieser Kategorie.", breadcrumb

    @app.callback(
        Output("translation-language", "value"),
        Output("translation-title", "value"),
        Output("translation-short-description", "value"),
        Output("translation-description", "value"),
        Output("translation-seo-title", "value"),
        Output("translation-seo-description", "value"),
        Output("translation-slug", "value"),
        Input("product-detail-translations", "selectedRows"),
        Input("product-id", "value"),
    )
    def load_product_translation_form(selected_rows: list[dict] | None, _product_id: int | None):
        if ctx.triggered_id == "product-id":
            return None, None, None, None, None, None, None
        if not selected_rows:
            return None, None, None, None, None, None, None
        row = selected_rows[0]
        return (
            row.get("language_code"),
            row.get("title"),
            row.get("short_description"),
            row.get("description"),
            row.get("seo_title"),
            row.get("seo_description"),
            row.get("slug"),
        )

    @app.callback(
        Output("variant-translation-variant-id", "options"),
        Input("product-detail-variants", "rowData"),
    )
    def load_variant_translation_options(variant_rows: list[dict] | None):
        return [
            {
                "label": f"{row.get('sku') or row.get('variant_title') or row.get('id')}",
                "value": row.get("id"),
            }
            for row in (variant_rows or [])
            if row.get("id") is not None
        ]

    @app.callback(
        Output("variant-translation-id", "value"),
        Output("variant-translation-variant-id", "value"),
        Output("variant-translation-language", "value"),
        Output("variant-translation-title", "value"),
        Output("variant-translation-option-label-override", "value"),
        Output("variant-translation-package-label", "value"),
        Input("product-detail-variant-translations", "selectedRows"),
        Input("product-id", "value"),
        prevent_initial_call=True,
    )
    def load_variant_translation_form(selected_rows: list[dict] | None, _product_id: int | None):
        if ctx.triggered_id == "product-id":
            return None, None, None, None, None, None
        if not selected_rows:
            return None, None, None, None, None, None
        row = selected_rows[0]
        return (
            row.get("id"),
            row.get("variant_id"),
            row.get("language_code"),
            row.get("title"),
            row.get("option_label_override"),
            row.get("package_label"),
        )

    @app.callback(
        Output("product-channel-mapping-channel-category-id", "options"),
        Input("product-channel-mapping-sales-channel-id", "value"),
        Input("snapshot-store", "data"),
    )
    def filter_channel_category_options(sales_channel_id: int | None, snapshot: dict | None):
        options = (snapshot or {}).get("channel_category_options", [])
        if not sales_channel_id:
            return options
        return [item for item in options if item.get("sales_channel_id") == sales_channel_id]

    @app.callback(
        Output("variants-grid", "rowData"),
        Input("snapshot-store", "data"),
        Input("variant-focus-product-id", "data"),
    )
    def apply_variant_rows(snapshot: dict | None, focused_product_id: int | None):
        snapshot = snapshot or {}
        variants = snapshot.get("variants", [])
        if not focused_product_id:
            return variants
        return [row for row in variants if row.get("product_id") == focused_product_id]

    @app.callback(
        Output("chemistry-grid", "rowData"),
        Input("snapshot-store", "data"),
        Input("chemistry-filter-adr", "value"),
        Input("chemistry-filter-sds", "value"),
        Input("chemistry-filter-business", "value"),
        Input("chemistry-filter-status", "value"),
    )
    def apply_chemical_rows(
        snapshot: dict | None,
        adr_filter: str | None,
        sds_filter: str | None,
        business_filter: str | None,
        status_filter: str | None,
    ):
        rows = list((snapshot or {}).get("chemistry_products", []))

        def _matches_boolean(value: bool, selected: str | None) -> bool:
            if selected in {None, "all"}:
                return True
            return value if selected == "yes" else not value

        filtered = [
            row
            for row in rows
            if _matches_boolean(bool(row.get("adr_relevant")), adr_filter)
            and _matches_boolean(bool(row.get("sds_available")), sds_filter)
            and _matches_boolean(bool(row.get("business_only")), business_filter)
            and (status_filter in {None, "all"} or row.get("status") == status_filter)
        ]
        return filtered

    @app.callback(Output("selected-chemical-product-id", "data"), Input("chemistry-grid", "selectedRows"), prevent_initial_call=True)
    def store_selected_chemical_product_id(selected_rows: list[dict] | None):
        if not selected_rows:
            return None
        row = selected_rows[0]
        return int(row["id"]) if row.get("id") is not None else None

    @app.callback(
        Output("selected-chemical-product-id", "data", allow_duplicate=True),
        Output("chemistry-grid", "selectedRows", allow_duplicate=True),
        Input("chemistry-grid", "cellClicked"),
        prevent_initial_call=True,
    )
    def select_chemical_from_grid(cell_event: dict | None):
        if not cell_event:
            return no_update, no_update
        row = cell_event.get("data") or {}
        product_id = row.get("id")
        if not product_id:
            return no_update, no_update
        return int(product_id), [row]

    @app.callback(
        Output("chemistry-grid", "selectedRows", allow_duplicate=True),
        Input("selected-chemical-product-id", "data"),
        State("chemistry-grid", "rowData"),
        prevent_initial_call=True,
    )
    def sync_chemical_grid_selection(selected_product_id: int | None, rows: list[dict] | None):
        if not selected_product_id:
            return []
        return [row for row in (rows or []) if row.get("id") == selected_product_id]

    @app.callback(
        Output("selected-asset-ids", "data"),
        Input("assets-grid", "selectedRows"),
    )
    def store_selected_asset_ids(selected_rows: list[dict] | None) -> list[int]:
        return [int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None]

    @app.callback(
        Output("assets-bulk-actions", "style"),
        Output("assets-bulk-count", "children"),
        Input("assets-grid", "selectedRows"),
    )
    def render_assets_bulk_actions(selected_rows: list[dict] | None):
        count = len(selected_rows or [])
        return _asset_bulk_actions_style(selected_rows), f"{count} Assets ausgewählt"

    @app.callback(
        Output("assets-grid", "selectedRows", allow_duplicate=True),
        Output("selected-asset-ids", "data", allow_duplicate=True),
        Input("assets-select-visible-button", "n_clicks"),
        Input("assets-deselect-visible-button", "n_clicks"),
        Input("assets-clear-selection-button", "n_clicks"),
        State("assets-grid", "virtualRowData"),
        State("assets-grid", "rowData"),
        State("assets-grid", "selectedRows"),
        prevent_initial_call=True,
    )
    def control_asset_grid_selection(
        _select_clicks: int | None,
        _deselect_clicks: int | None,
        _clear_clicks: int | None,
        virtual_rows: list[dict] | None,
        row_data: list[dict] | None,
        selected_rows: list[dict] | None,
    ):
        if ctx.triggered_id == "assets-clear-selection-button":
            return [], []
        visible_rows = list(virtual_rows or row_data or [])
        selected_ids = {int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None}
        visible_ids = {int(row["id"]) for row in visible_rows if row.get("id") is not None}
        if ctx.triggered_id == "assets-deselect-visible-button":
            selected = [
                row
                for row in (selected_rows or [])
                if row.get("id") is not None and int(row["id"]) not in visible_ids
            ]
            return selected, [int(row["id"]) for row in selected]
        selected = _unique_rows_by_id([*(selected_rows or []), *visible_rows])
        return selected, [int(row["id"]) for row in selected]

    @app.callback(
        Output("assets-bulk-delete-confirm", "displayed"),
        Output("assets-bulk-delete-confirm", "message"),
        Input("assets-bulk-delete-button", "n_clicks"),
        Input("assets-visible-delete-button", "n_clicks"),
        State("selected-asset-ids", "data"),
        prevent_initial_call=True,
    )
    def confirm_bulk_asset_delete(_: int | None, __: int | None, selected_ids: list[int] | None):
        count = len(selected_ids or [])
        if count <= 0:
            return False, no_update
        noun = "Asset" if count == 1 else "Assets"
        return True, f"{count} ausgewählte {noun} wirklich löschen?"

    @app.callback(
        Output("assets-detail-preview", "children"),
        Input("assets-grid", "selectedRows"),
    )
    def render_selected_asset_detail(selected_rows: list[dict] | None):
        if not selected_rows:
            return html.Div("Kein Asset ausgewählt.", style={"color": "#64748b", "padding": "12px 0"})
        if len(selected_rows) > 1:
            count = len(selected_rows)
            return html.Div(f"{count} Assets ausgewählt.", style={"color": "#475569", "padding": "12px 0"})
        return _render_asset_detail(selected_rows[0])

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("assets-grid", "selectedRows", allow_duplicate=True),
        Output("selected-asset-ids", "data", allow_duplicate=True),
        Input("assets-bulk-delete-confirm", "submit_n_clicks"),
        State("refresh-token", "data"),
        State("selected-asset-ids", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_selected_assets_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        selected_ids: list[int] | None,
    ):
        if not selected_ids:
            return "Keine Assets ausgewählt.", no_update, no_update, no_update
        result = delete_assets(session, selected_ids)
        deleted_count = int(result.get("deleted_count") or 0)
        error_count = int(result.get("error_count") or 0)
        errors = result.get("errors") or []
        if deleted_count <= 0 and error_count > 0:
            first_error = errors[0].get("message") if errors else "Unbekannter Fehler"
            return f"Bulk-Delete fehlgeschlagen: {first_error}", no_update, no_update, no_update
        if error_count > 0:
            first_error = errors[0].get("message") if errors else "Unbekannter Fehler"
            message = f"{deleted_count} Assets gelöscht, {error_count} fehlgeschlagen. Erstes Problem: {first_error}"
        else:
            message = f"{deleted_count} Assets gelöscht."
        return message, (refresh_token or 0) + 1, [], []

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Output("last-product-clicked-id", "data", allow_duplicate=True),
        Output("last-product-click-event", "data", allow_duplicate=True),
        Output("products-grid", "selectedRows", allow_duplicate=True),
        Output("flash-message", "children", allow_duplicate=True),
        Input("assets-grid", "cellRendererData"),
        State("snapshot-store", "data"),
        prevent_initial_call=True,
    )
    def open_product_from_asset_link(renderer_data: dict | None, snapshot: dict | None):
        if not renderer_data:
            return no_update, no_update, no_update, no_update, no_update
        value = renderer_data.get("value") or {}
        if value.get("action") != "open_product_from_asset":
            return no_update, no_update, no_update, no_update, no_update
        product_id = value.get("product_id")
        if product_id in (None, ""):
            return no_update, no_update, no_update, no_update, no_update
        product_id = int(product_id)
        products = (snapshot or {}).get("products", [])
        product_row = next((item for item in products if item.get("id") == product_id), {"id": product_id})
        return (
            "products",
            product_id,
            {"id": product_id, "ts": value.get("ts")},
            [product_row],
            f"Zeige Produkt {product_id}.",
        )

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Output("variant-focus-product-id", "data", allow_duplicate=True),
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Output("flash-message", "children", allow_duplicate=True),
        Input("assets-grid", "cellRendererData"),
        State("snapshot-store", "data"),
        prevent_initial_call=True,
    )
    def open_variant_from_asset_link(renderer_data: dict | None, snapshot: dict | None):
        if not renderer_data:
            return no_update, no_update, no_update, no_update
        value = renderer_data.get("value") or {}
        if value.get("action") != "open_variant_from_asset":
            return no_update, no_update, no_update, no_update
        variant_id = value.get("variant_id")
        if variant_id in (None, ""):
            return no_update, no_update, no_update, no_update
        variant_id = int(variant_id)
        product_id = value.get("product_id")
        product_id = int(product_id) if product_id not in (None, "") else None
        variants = (snapshot or {}).get("variants", [])
        variant_row = next((item for item in variants if item.get("id") == variant_id), None)
        if variant_row is None:
            variant_row = {"id": variant_id, "product_id": product_id}
        if product_id is None:
            product_id = variant_row.get("product_id")
        return (
            "variants",
            product_id if product_id is not None else no_update,
            [variant_row],
            f"Zeige Variante {variant_id}.",
        )

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Output("variant-focus-product-id", "data", allow_duplicate=True),
        Output("flash-message", "children", allow_duplicate=True),
        Input("products-grid", "cellClicked"),
        State("products-grid", "virtualRowData"),
        State("products-grid", "rowData"),
        prevent_initial_call=True,
    )
    def jump_from_product_to_variants(
        cell_event: dict | None,
        virtual_rows: list[dict] | None,
        rows: list[dict] | None,
    ):
        if not cell_event:
            return no_update, no_update, no_update
        col_id = cell_event.get("colId") or ""
        row_index = cell_event.get("rowIndex")
        row_list = virtual_rows if virtual_rows is not None else (rows or [])
        row = row_list[row_index] if isinstance(row_index, int) and 0 <= row_index < len(row_list) else {}
        product_id = row.get("id")
        if col_id != "variant_nav" or not product_id:
            return no_update, no_update, no_update
        label = row.get("sku") or product_id
        return "variants", int(product_id), f"Zeige Varianten für Produkt {label}."

    @app.callback(
        Output("product-focus-id", "data", allow_duplicate=True),
        Output("products-grid", "selectedRows", allow_duplicate=True),
        Input("products-grid", "cellClicked"),
        State("products-grid", "virtualRowData"),
        State("products-grid", "rowData"),
        prevent_initial_call=True,
    )
    def select_product_from_title_click(
        cell_event: dict | None,
        virtual_rows: list[dict] | None,
        rows: list[dict] | None,
    ):
        if not cell_event:
            return no_update, no_update
        col_id = cell_event.get("colId") or ""
        if col_id not in {"title", "product_title"}:
            return no_update, no_update
        row = cell_event.get("data") or {}
        if not row.get("id"):
            row_index = cell_event.get("rowIndex")
            row_list = virtual_rows if virtual_rows is not None else (rows or [])
            if not isinstance(row_index, int) or not (0 <= row_index < len(row_list)):
                return no_update, no_update
            row = row_list[row_index]
        if not row.get("id"):
            return no_update, no_update
        return int(row["id"]), [row]

    @app.callback(
        Output("variant-focus-product-id", "data", allow_duplicate=True),
        Input("nav-variants", "n_clicks"),
        Input("metric-variants-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_variant_focus(_: int | None, __: int | None):
        return None

    @app.callback(
        Output("variants-grid", "selectedRows", allow_duplicate=True),
        Input("variant-focus-product-id", "data"),
        State("variants-grid", "rowData"),
        prevent_initial_call=True,
    )
    def select_focused_variants(product_id: int | None, rows: list[dict] | None):
        if not product_id:
            return []
        row_list = rows or []
        return [row for row in row_list if row.get("product_id") == product_id]

    @app.callback(
        Output("categories-grid", "selectedRows", allow_duplicate=True),
        Output("selected-category-id", "data", allow_duplicate=True),
        Input("categories-grid", "cellClicked"),
        prevent_initial_call=True,
    )
    def select_category_from_grid(cell_event: dict | None):
        if not cell_event:
            return no_update, no_update
        if cell_event.get("colId") == "tree_toggle":
            return no_update, no_update
        row = cell_event.get("data") or {}
        category_id = row.get("id")
        if not category_id:
            return no_update, no_update
        return [row], int(category_id)

    @app.callback(Output("selected-category-id", "data"), Input("categories-grid", "selectedRows"), prevent_initial_call=True)
    def store_selected_category_id(selected_rows: list[dict] | None):
        if not selected_rows:
            return None
        row = selected_rows[0]
        return int(row["id"]) if row.get("id") is not None else None

    @app.callback(
        Output("category-detail-id", "value"),
        Output("category-detail-name", "value"),
        Output("category-detail-parent-id", "value"),
        Output("category-detail-language-code", "value"),
        Output("category-detail-sort-order", "value"),
        Output("category-detail-summary", "children"),
        Input("selected-category-id", "data"),
        Input("categories-sales-channel-code", "value"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_category_detail(session: Session, category_id: int | None, sales_channel_code: str | None, _: int | None):
        if not category_id:
            return None, None, None, "de", 0, "Keine Kanal-Kategorie ausgewählt."
        detail = get_category_detail(session, int(category_id), sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)
        if detail is None:
            return None, None, None, "de", 0, "Kanal-Kategorie nicht gefunden."
        summary = (
            f"Kanal: {detail.get('sales_channel_name') or 'voxster.ch'} · "
            f"ID {detail['id']} · Parent: {detail.get('parent_name') or '-'} · "
            f"Unterkategorien: {detail['child_count']} · Produktzuweisungen: {detail['product_count']}"
        )
        return detail['id'], detail['name'], detail['parent_id'], detail['language_code'], detail['sort_order'], summary

    @app.callback(
        Output("category-tree-collapsed-store", "data"),
        Input("categories-grid", "cellRendererData"),
        State("category-tree-collapsed-store", "data"),
        prevent_initial_call=True,
    )
    def toggle_category_row_tree(renderer_data: dict | None, collapsed_ids: list[int] | None):
        if not renderer_data:
            return no_update
        value = renderer_data.get("value") or {}
        if value.get("action") != "toggle_category":
            return no_update
        category_id = value.get("category_id")
        if category_id is None:
            return no_update
        collapsed = {int(item) for item in (collapsed_ids or [])}
        category_id = int(category_id)
        if category_id in collapsed:
            collapsed.remove(category_id)
        else:
            collapsed.add(category_id)
        return sorted(collapsed)

    @app.callback(
        Output("categories-grid", "selectedRows", allow_duplicate=True),
        Output("selected-category-id", "data", allow_duplicate=True),
        Input("categories-sales-channel-code", "value"),
        prevent_initial_call=True,
    )
    def clear_category_selection_on_channel_change(_: str | None):
        return [], None

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("categories-grid", "cellValueChanged"),
        State("categories-sales-channel-code", "value"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_category_grid_edit_callback(
        session: Session,
        event: dict | None,
        sales_channel_code: str | None,
        refresh_token: int,
    ):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        category_id = row.get("id")
        if not category_id:
            return no_update, no_update
        try:
            update_category(
                session,
                int(category_id),
                row.get("name"),
                row.get("parent_id"),
                row.get("language_code"),
                _int_or_zero(row.get("sort_order")),
                slug=row.get("slug"),
                sales_channel_code=sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
            )
        except ValueError as exc:
            return str(exc), no_update
        return f"Kanal-Kategorie {category_id} für voxster.ch gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("categories-grid", "virtualRowData"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    def persist_category_row_order_callback(
        rows: list[dict] | None,
        refresh_token: int,
    ):
        return no_update, no_update

    @app.callback(
        Output("product-id", "value"),
        Output("product-sku", "value"),
        Output("product-title", "value"),
        Output("product-brand", "value"),
        Output("product-status", "value"),
        Output("product-source-language", "value"),
        Output("product-is-chemical", "value"),
        Output("product-category-channel-code", "value"),
        Output("product-short-description", "value"),
        Output("product-description", "value"),
        Output("product-source-url", "value"),
        Output("product-source-url-final", "value"),
        Output("product-detail-summary", "children"),
        Output("product-detail-variants", "rowData"),
        Output("product-detail-tiers", "rowData"),
        Output("product-detail-assets", "rowData"),
        Output("product-detail-translations", "rowData"),
        Output("product-detail-variant-translations", "rowData"),
        Output("product-detail-channel-listings", "rowData"),
        Output("product-detail-category-mappings", "rowData"),
        Output("product-detail-variant-channel-listings", "rowData"),
        Output("product-detail-variant-category-mappings", "rowData"),
        Output("product-detail-asset-links", "children"),
        Output("product-detail-asset-preview", "children"),
        Input("last-product-clicked-id", "data"),
        Input("last-product-click-event", "data"),
        Input("selected-product-ids", "data"),
        Input("active-product-row", "data"),
        Input("refresh-token", "data"),
        Input("product-detail-variant-status-filter", "value"),
        State("product-id", "value"),
    )
    @_with_session
    def select_product(
        session: Session,
        last_clicked_id: int | None,
        _last_click_event: dict | None,
        selected_product_ids: list[int] | None,
        active_row: dict | None,
        _: int | None,
        variant_archive_filter: str | None,
        current_product_id: int | None,
    ):
        product_id = None
        debug_parts = []
        if last_clicked_id is not None:
            debug_parts.append(f"clicked={last_clicked_id}")
            product_id = int(last_clicked_id)
        if product_id is None and selected_product_ids:
            debug_parts.append(f"selected={selected_product_ids[:3]}")
            product_id = int(selected_product_ids[0])
        if product_id is None and active_row and active_row.get("id") is not None:
            debug_parts.append(f"activeRow id={active_row.get('id')}")
            product_id = int(active_row["id"])
        if product_id is None and current_product_id:
            debug_parts.append(f"current id={current_product_id}")
            product_id = current_product_id
        if not product_id:
            return None, None, None, None, "draft", "en", False, DEFAULT_CATEGORY_CHANNEL_CODE, None, None, None, None, "Kein Produkt gewählt.", [], [], [], [], [], [], [], [], [], "Keine Assets vorhanden.", "Keine Asset-Vorschau vorhanden."
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return None, None, None, None, "draft", "en", False, DEFAULT_CATEGORY_CHANNEL_CODE, None, None, None, None, "Produkt nicht gefunden.", [], [], [], [], [], [], [], [], [], "Keine Assets vorhanden.", "Keine Asset-Vorschau vorhanden."
        asset_links = _render_asset_links(detail["assets"])
        asset_preview = _render_asset_preview(detail["assets"])
        category_summary = detail.get("category_assignments") or list_product_category_assignments(session, int(product_id))
        category_summary_text = ", ".join(
            f"{item.get('sales_channel_name') or item.get('sales_channel_code')}: {len(item.get('category_ids') or [])}"
            for item in category_summary
        ) or "-"
        summary = html.Div(
            [
                html.Div(f"Family Key: {detail.get('family_key') or '-'}"),
                html.Div(f"Originalsprache: {detail.get('source_language') or '-'}"),
                html.Div(f"Handle: {detail['handle']}"),
                html.Div(f"Kategorie-Sets pro Kanal: {category_summary_text}"),
                html.Div(f"Source URL: {detail.get('source_url') or '-'}"),
                html.Div(f"Final URL: {detail.get('source_url_final') or '-'}"),
                html.Div(f"Spezifikationen: {detail.get('specifications_text') or '-'}"),
                html.Div(f"Technische Merkmale: {detail.get('technical_features_text') or '-'}"),
            ]
        )
        visible_variants = _filter_detail_variants(detail["variants"], variant_archive_filter)
        tier_rows = []
        for variant in visible_variants:
            for tier in variant.get("price_tiers", []):
                tier_rows.append(
                    {
                        **tier,
                        "variant_id": variant["id"],
                        "variant_sku": variant["sku"],
                    }
                )
        return (
            detail["id"],
            detail["sku"],
            detail["title"],
            detail["brand_name"],
            detail["status"],
            detail.get("source_language") or "en",
            bool(detail.get("is_chemical")),
            detail.get("category_channel_code") or DEFAULT_CATEGORY_CHANNEL_CODE,
            _detail_source_short_description(detail),
            detail["description"],
            detail.get("source_url"),
            detail.get("source_url_final"),
            summary,
            visible_variants,
            tier_rows,
            detail["assets"],
            detail["translations"],
            _detail_variant_translation_rows(detail),
            detail.get("channel_listings", []),
            detail.get("channel_category_mappings", []),
            detail.get("variant_channel_listings", []),
            detail.get("variant_category_mappings", []),
            asset_links,
            asset_preview,
        )

    @app.callback(
        Output("product-open-chemistry-button", "style"),
        Output("product-open-chemistry-button", "disabled"),
        Input("product-id", "value"),
        Input("snapshot-store", "data"),
    )
    def toggle_product_chemistry_button(product_id: int | None, snapshot: dict | None):
        if not product_id:
            return {"display": "none"}, True
        return {"display": "inline-flex"}, False

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Output("selected-chemical-product-id", "data", allow_duplicate=True),
        Input("product-open-chemistry-button", "n_clicks"),
        State("product-id", "value"),
        prevent_initial_call=True,
    )
    def open_product_chemistry_view(_: int | None, product_id: int | None):
        if not product_id:
            return no_update, no_update
        return "chemistry", int(product_id)

    @app.callback(
        Output("chemistry-detail-summary", "children"),
        Output("chemistry-product-id", "value"),
        Output("chemistry-product-sku", "value"),
        Output("chemistry-product-title", "value"),
        Output("chemistry-product-brand", "value"),
        Output("chemistry-product-status", "value"),
        Output("chemistry-product-language", "value"),
        Output("chemistry-is-chemical", "value"),
        Output("chemistry-chemical-type", "value"),
        Output("chemistry-ufi", "value"),
        Output("chemistry-voc-content-percent", "value"),
        Output("chemistry-cas-number", "value"),
        Output("chemistry-ec-number", "value"),
        Output("chemistry-un-number", "value"),
        Output("chemistry-hazard-class", "value"),
        Output("chemistry-packing-group", "value"),
        Output("chemistry-adr-relevant", "value"),
        Output("chemistry-ghs-pictograms", "value"),
        Output("chemistry-signal-word", "value"),
        Output("chemistry-adr-pictograms", "value"),
        Output("chemistry-environmentally-hazardous", "value"),
        Output("chemistry-hazard-statements", "value"),
        Output("chemistry-precautionary-statements", "value"),
        Output("chemistry-wgk", "value"),
        Output("chemistry-storage-class", "value"),
        Output("chemistry-wgk-storage-meta", "children"),
        Output("chemistry-wgk-storage-enrich-button", "children"),
        Output("chemistry-sds-available", "value"),
        Output("chemistry-sds-url", "value"),
        Output("chemistry-sds-asset-id", "value"),
        Output("chemistry-sds-asset-id", "options"),
        Output("chemistry-density", "value"),
        Output("chemistry-color", "value"),
        Output("chemistry-odor", "value"),
        Output("chemistry-ph-value", "value"),
        Output("chemistry-flash-point", "value"),
        Output("chemistry-boiling-point", "value"),
        Output("chemistry-viscosity", "value"),
        Output("chemistry-solubility", "value"),
        Output("chemistry-business-only", "value"),
        Output("chemistry-age-check-required", "value"),
        Output("chemistry-shippable", "value"),
        Output("chemistry-limited-quantity", "value"),
        Output("chemistry-hazard-shipping-note", "value"),
        Output("chemistry-shop-active", "value"),
        Input("selected-chemical-product-id", "data"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_chemical_detail(session: Session, product_id: int | None, _: int | None):
        if not product_id:
            return _empty_chemistry_detail("Kein Chemieprodukt ausgewählt.")
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return _empty_chemistry_detail("Chemieprodukt nicht gefunden.")
        asset_options = [
            {"label": f"{asset.get('id')} · {asset.get('filename')}", "value": asset.get("id")}
            for asset in detail.get("assets", [])
            if asset.get("id") is not None
        ]
        ghs_values = [
            value.strip()
            for value in str(detail.get("ghs_pictograms") or "").replace(",", "|").split("|")
            if value.strip()
        ]
        chem_safety = detail.get("chemical_safety_json") or {}
        adr_values = [
            value
            for value in (chem_safety.get("adr_pictograms") or [])
            if value in ADR_SYMBOLS
        ]
        if not adr_values and str(detail.get("hazard_class") or "").strip() == "8":
            adr_values.append("ADR_8")
        environmentally_hazardous = bool(chem_safety.get("environmentally_hazardous") or "ADR_pollution" in adr_values)
        summary = (
            f"Produkt {detail['id']} · {detail['sku']} · CAS: {detail.get('cas_number') or '-'} · "
            f"UN: {detail.get('un_number') or '-'} · ADR: {'Ja' if detail.get('adr_relevant') else 'Nein'}"
        )
        return (
            summary,
            detail["id"],
            detail["sku"],
            detail["title"],
            detail["brand_name"],
            detail["status"],
            detail.get("source_language") or "en",
            bool(detail.get("is_chemical")),
            detail.get("chemical_type"),
            detail.get("ufi"),
            detail.get("voc_content_percent"),
            detail.get("cas_number"),
            detail.get("ec_number"),
            detail.get("un_number"),
            detail.get("hazard_class"),
            detail.get("packing_group"),
            bool(detail.get("adr_relevant")),
            ghs_values,
            _normalize_signal_word_for_ui(str((chem_safety.get("signal_word") if isinstance(chem_safety, dict) else None) or detail.get("signal_word") or "")),
            adr_values,
            environmentally_hazardous,
            detail.get("hazard_statements"),
            detail.get("precautionary_statements"),
            detail.get("wgk"),
            detail.get("storage_class"),
            _render_wgk_storage_meta(detail),
            "Aus SDB erneut prüfen" if detail.get("wgk") or detail.get("storage_class") else "Aus SDB anreichern",
            bool(detail.get("sds_available") or detail.get("sds_url") or detail.get("sds_asset_id")),
            detail.get("sds_url"),
            detail.get("sds_asset_id"),
            asset_options,
            detail.get("density"),
            detail.get("color"),
            detail.get("odor"),
            detail.get("ph_value"),
            detail.get("flash_point"),
            detail.get("boiling_point"),
            detail.get("viscosity"),
            detail.get("solubility"),
            bool(detail.get("business_only")),
            bool(detail.get("age_check_required")),
            bool(detail.get("shippable")),
            detail.get("limited_quantity"),
            detail.get("hazard_shipping_note"),
            bool(detail.get("shop_active")),
        )

    @app.callback(
        Output("chemistry-enrichment-reference-urls", "value"),
        Output("chemistry-enrichment-status", "children"),
        Output("chemistry-enrichment-runs", "children"),
        Output("chemistry-enrichment-documents", "children"),
        Output("chemistry-enrichment-preview", "children"),
        Output("chemistry-enrichment-suggestions-grid", "rowData"),
        Output("chemistry-enrichment-log", "value"),
        Input("selected-chemical-product-id", "data"),
        Input("refresh-token", "data"),
    )
    @_with_session
    def load_chemical_enrichment_detail(session: Session, product_id: int | None, _: int | None):
        if not product_id:
            return None, "Bitte zuerst ein Produkt auswählen.", html.Div("Noch keine Anreicherungs-Läufe."), html.Div("Keine gefundenen Dokumente."), html.Div("Keine Vorschau."), [], "Kein Produkt ausgewählt."
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return None, "Chemieprodukt nicht gefunden.", html.Div("Noch keine Anreicherungs-Läufe."), html.Div("Keine gefundenen Dokumente."), html.Div("Keine Vorschau."), [], "Chemieprodukt nicht gefunden."
        latest = get_latest_product_chemical_enrichment(session, int(product_id))
        runs = list_product_chemical_enrichment_runs(session, int(product_id))
        reference_values = [
            detail.get("sds_url"),
            detail.get("chemical_reference_url"),
            latest.get("reference_url") if latest else None,
            detail.get("source_url_final"),
            detail.get("source_url"),
        ]
        reference_urls = "\n".join(dict.fromkeys(str(value).strip() for value in reference_values if str(value or "").strip())) or None
        if latest:
            warnings = latest.get("warnings_json") or []
            status_message = _render_chemical_internet_status(
                "success" if str(latest.get("status") or "").lower() in {"completed", "success", "applied"} else "info",
                f"Letzte Anreicherung: {latest.get('status') or '-'}",
                details=[
                    f"Quelle: {latest.get('source_kind') or '-'}",
                    f"Zeitpunkt: {latest.get('extracted_at') or '-'}",
                    f"Warnungen: {len(warnings)}" if warnings else "Warnungen: 0",
                ],
            )
        else:
            status_message = _render_chemical_internet_status("info", "Noch keine Internet-Anreicherung vorhanden.")
        return (
            reference_urls,
            status_message,
            _render_chemical_runs(runs),
            _render_chemical_documents((latest or {}).get("document_links_json")),
            _render_chemical_enrichment_preview(detail, latest),
            _chemical_enrichment_suggestion_rows(latest),
            _chemical_enrichment_log_text(latest),
        )

    @app.callback(
        Output("chemistry-symbol-preview", "children"),
        Input("chemistry-ghs-pictograms", "value"),
        Input("chemistry-adr-pictograms", "value"),
        Input("chemistry-signal-word", "value"),
    )
    def render_chemical_symbol_preview(ghs_pictograms: list[str] | None, adr_pictograms: list[str] | None, signal_word: str | None):
        return _render_chemical_symbol_preview(ghs_pictograms, adr_pictograms, signal_word)

    @app.callback(
        Output("chemistry-hazard-class", "value", allow_duplicate=True),
        Output("chemistry-environmentally-hazardous", "value", allow_duplicate=True),
        Input("chemistry-adr-pictograms", "value"),
        State("chemistry-environmentally-hazardous", "value"),
        State("chemistry-hazard-class", "value"),
        prevent_initial_call=True,
    )
    def suggest_adr_fields(adr_pictograms: list[str] | None, environmentally_hazardous: bool | None, hazard_class: str | None):
        codes = set(adr_pictograms or [])
        next_hazard_class = no_update
        if not str(hazard_class or "").strip():
            for code, adr_class in (("ADR_3", "3"), ("ADR_5.1", "5.1"), ("ADR_8", "8")):
                if code in codes:
                    next_hazard_class = adr_class
                    break
        next_environment = True if "ADR_pollution" in codes else no_update
        return next_hazard_class, next_environment

    @app.callback(
        Output("chemistry-classification-proposal-store", "data"),
        Output("chemistry-wgk-storage-proposal", "children"),
        Input("chemistry-wgk-storage-enrich-button", "n_clicks"),
        State("selected-chemical-product-id", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def enrich_wgk_storage_from_sdb(session: Session, _clicks: int | None, product_id: int | None):
        if not product_id:
            return {}, "Kein Chemieprodukt ausgewählt."
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return {}, "Chemieprodukt nicht gefunden."
        sdb_data = get_product_sdb(session, int(product_id))
        proposals = extract_wgk_storage_from_sdb(
            sdb_data,
            existing_wgk=detail.get("wgk"),
            existing_storage_class=detail.get("storage_class"),
        )
        return proposals, _render_classification_proposal(proposals)

    @app.callback(
        Output("chemistry-wgk-storage-proposal", "children", allow_duplicate=True),
        Output("chemistry-classification-proposal-store", "data", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("chemistry-wgk-storage-apply-button", "n_clicks"),
        State("selected-chemical-product-id", "data"),
        State("chemistry-classification-proposal-store", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def apply_wgk_storage_proposal(session: Session, _clicks: int | None, product_id: int | None, proposals: dict | None, refresh_token: int | None):
        if not product_id:
            return "Kein Chemieprodukt ausgewählt.", no_update, no_update
        if not proposals or not (proposals.get("wgk") or proposals.get("storage_class")):
            return "Kein Vorschlag vorhanden.", no_update, no_update
        product = session.get(Product, int(product_id))
        if product is None:
            return "Chemieprodukt nicht gefunden.", no_update, no_update
        apply_classification_proposals_to_product(product, proposals)
        session.flush()
        return "WGK-/Lagerklasse-Vorschlag übernommen.", {}, (refresh_token or 0) + 1

    @app.callback(
        Output("chemistry-wgk-storage-proposal", "children", allow_duplicate=True),
        Output("chemistry-classification-proposal-store", "data", allow_duplicate=True),
        Input("chemistry-wgk-storage-discard-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def discard_wgk_storage_proposal(_clicks: int | None):
        return "Vorschlag verworfen.", {}

    @app.callback(
        Output("chemistry-sdb-protocol", "children"),
        Input("chemistry-sdb-protocol-store", "data"),
    )
    def render_chemistry_sdb_protocol(protocol_entries: list[dict] | None):
        return _render_sdb_protocol(protocol_entries)

    @app.callback(
        Output("chemistry-sdb-source-url", "value"),
        Output("chemistry-sdb-pdf-url", "value"),
        Output("chemistry-sdb-source-asset-id", "value"),
        Output("chemistry-sdb-source-asset-id", "options"),
        Output("chemistry-sdb-parser-status", "children"),
        Output("chemistry-sdb-openai-status", "children"),
        Output("chemistry-sdb-review-status", "value"),
        Output("chemistry-sdb-version-label", "value"),
        Output("chemistry-sdb-effective-date", "value"),
        Output("chemistry-sdb-document-title", "value"),
        Output("chemistry-sdb-issuer-name", "value"),
        Output("chemistry-sdb-issuer-address-line1", "value"),
        Output("chemistry-sdb-issuer-address-line2", "value"),
        Output("chemistry-sdb-issuer-postal-code", "value"),
        Output("chemistry-sdb-issuer-city", "value"),
        Output("chemistry-sdb-issuer-country-code", "value"),
        Output("chemistry-sdb-issuer-phone", "value"),
        Output("chemistry-sdb-issuer-email", "value"),
        Output("chemistry-sdb-llm-status", "children"),
        Output("chemistry-sdb-raw-text", "value"),
        Output({"type": "chemistry-sdb-section", "index": ALL}, "value"),
        Output("chemistry-sdb-pdf-link", "children"),
        Output("chemistry-sdb-llm-runs", "children"),
        Output("chemistry-sdb-protocol-store", "data"),
        Input("selected-chemical-product-id", "data"),
        Input("chemistry-sdb-refresh-token", "data"),
    )
    @_with_session
    def load_chemical_sdb_detail(session: Session, product_id: int | None, _: int | None):
        if not product_id:
            return _empty_chemistry_sdb_detail("Kein Chemieprodukt ausgewählt.")
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return _empty_chemistry_sdb_detail("Chemieprodukt nicht gefunden.")
        sdb_data = get_product_sdb(session, int(product_id))
        asset_options = [
            {"label": f"{asset.get('id')} · {asset.get('filename')}", "value": asset.get("id")}
            for asset in detail.get("assets", [])
            if asset.get("id") is not None
        ]
        sections = sdb_data.get("sections_json") or {}
        section_values = [str((sections.get(f"section_{index}") or {}).get("content") or "") for index in SDB_SECTION_TITLES]
        parser_status = f"Parser-Status: {sdb_data.get('parser_status') or '-'}"
        warnings = sdb_data.get("parser_warnings_json") or []
        if warnings:
            parser_status += f" · Warnungen: {len(warnings)}"
        llm_config = get_sdb_llm_config_status()
        openai_status = (
            f"OpenAI-Anbindung: {'aktiv' if llm_config.get('enabled') else 'nicht konfiguriert'}"
            f" · Modell: {llm_config.get('model') or '-'}"
            f" · Qualität: {llm_config.get('quality_label') or '-'}"
            f" · Reasoning: {llm_config.get('reasoning_effort') or '-'}"
        )
        llm_runs = sdb_data.get("llm_runs") or []
        latest_llm_run = llm_runs[0] if llm_runs else None
        if latest_llm_run:
            llm_status = (
                f"LLM-Status: {latest_llm_run.get('status') or '-'}"
                f" · {latest_llm_run.get('provider') or '-'}"
                f" · {latest_llm_run.get('model') or '-'}"
            )
        else:
            llm_status = "Noch keine LLM-Normalisierung gespeichert."
        return (
            sdb_data.get("source_url"),
            sdb_data.get("pdf_url"),
            sdb_data.get("source_asset_id"),
            asset_options,
            parser_status,
            openai_status,
            sdb_data.get("review_status"),
            sdb_data.get("version_label"),
            _format_sdb_effective_date_for_input(sdb_data.get("effective_date")),
            sdb_data.get("document_title") or detail.get("title"),
            sdb_data.get("issuer_name"),
            sdb_data.get("issuer_address_line1"),
            sdb_data.get("issuer_address_line2"),
            sdb_data.get("issuer_postal_code"),
            sdb_data.get("issuer_city"),
            sdb_data.get("issuer_country_code"),
            sdb_data.get("issuer_phone"),
            sdb_data.get("issuer_email"),
            llm_status,
            sdb_data.get("raw_text"),
            section_values,
            _render_sdb_pdf_link(sdb_data, int(product_id)),
            _render_sdb_llm_runs(llm_runs),
            sdb_data.get("action_log_json") or _append_sdb_protocol([], "Produkt geladen", "info", f"Chemieprodukt {product_id} wurde für den SDB-Flow geladen."),
        )

    @app.callback(
        Output("chemistry-sdb-documents-grid", "rowData"),
        Output("chemistry-sdb-translation-source-document-id", "options"),
        Output("chemistry-sdb-translation-source-document-id", "value"),
        Output("chemistry-sdb-translation-source-locale", "value"),
        Output("chemistry-sdb-prompts-grid", "rowData"),
        Output("chemistry-sdb-translation-prompt-id", "options"),
        Output("chemistry-sdb-translation-prompt-id", "value"),
        Output("chemistry-sdb-document-language-filter", "options"),
        Output("chemistry-sdb-document-status-filter", "options"),
        Output("chemistry-sdb-document-source-filter", "options"),
        Input("selected-chemical-product-id", "data"),
        Input("chemistry-sdb-refresh-token", "data"),
        Input("chemistry-sdb-document-language-filter", "value"),
        Input("chemistry-sdb-document-status-filter", "value"),
        Input("chemistry-sdb-document-source-filter", "value"),
        Input("chemistry-sdb-document-current-filter", "value"),
    )
    @_with_session
    def load_sdb_translation_workspace(
        session: Session,
        product_id: int | None,
        _: int | None,
        language_filter: str | None,
        status_filter: str | None,
        source_filter: str | None,
        current_filter: str | None,
    ):
        prompts = list_sdb_translation_prompts(session)
        prompt_options = [{"label": f"{row['name']} · {row.get('target_locale') or '*'} / {row.get('target_region') or '*'}", "value": row["id"]} for row in prompts]
        prompt_value = prompt_options[0]["value"] if prompt_options else None
        if not product_id:
            return [], [], None, "de-CH", prompts, prompt_options, prompt_value, [], [], []
        documents = list_sdb_documents_for_product(session, int(product_id))
        language_options = _options_from_rows(documents, "locale")
        status_options = _options_from_rows(documents, "status")
        source_options_filter = _options_from_rows(documents, "source")
        filtered_documents = _filter_sdb_documents(documents, language_filter, status_filter, source_filter, current_filter)
        source_documents = [row for row in documents if row.get("has_text")]
        source_options = [
            {
                "label": f"{row.get('id')} · {row.get('title') or 'SDB'} · {row.get('locale') or '-'} / {row.get('region_code') or '-'} · {row.get('status') or '-'}",
                "value": row["id"],
            }
            for row in source_documents
        ]
        source_value = source_options[0]["value"] if source_options else None
        source_locale = next((row.get("locale") for row in source_documents if row.get("id") == source_value), None) or "de-CH"
        return filtered_documents, source_options, source_value, source_locale, prompts, prompt_options, prompt_value, language_options, status_options, source_options_filter

    @app.callback(
        Output("chemistry-sdb-prompt-id", "value"),
        Output("chemistry-sdb-prompt-name", "value"),
        Output("chemistry-sdb-prompt-source-locale", "value"),
        Output("chemistry-sdb-prompt-target-locale", "value"),
        Output("chemistry-sdb-prompt-target-region", "value"),
        Output("chemistry-sdb-prompt-active", "value"),
        Output("chemistry-sdb-prompt-system", "value"),
        Output("chemistry-sdb-prompt-template", "value"),
        Input("chemistry-sdb-prompts-grid", "selectedRows"),
        Input("chemistry-sdb-prompt-new-button", "n_clicks"),
    )
    def load_selected_sdb_prompt(selected_rows: list[dict] | None, _new_clicks: int | None):
        if ctx.triggered_id == "chemistry-sdb-prompt-new-button":
            return (
                None,
                "Neuer SDB-Prompt",
                "",
                "",
                "",
                True,
                DEFAULT_SDB_SYSTEM_PROMPT,
                DEFAULT_SDB_USER_PROMPT_TEMPLATE,
            )
        row = (selected_rows or [{}])[0] if selected_rows else {}
        return (
            row.get("id"),
            row.get("name") or "SDB-Prompt",
            row.get("source_locale") or "",
            row.get("target_locale") or "",
            row.get("target_region") or "",
            bool(row.get("active", True)),
            row.get("system_prompt") or DEFAULT_SDB_SYSTEM_PROMPT,
            row.get("user_prompt_template") or DEFAULT_SDB_USER_PROMPT_TEMPLATE,
        )

    @app.callback(
        Output("chemistry-sdb-document-edit-title", "value"),
        Output("chemistry-sdb-document-edit-text", "value"),
        Input("chemistry-sdb-documents-grid", "selectedRows"),
    )
    @_with_session
    def load_selected_sdb_document_text(session: Session, selected_rows: list[dict] | None):
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        if not document_id:
            return "", ""
        try:
            detail = get_chemical_document_detail(session, int(document_id))
        except ValueError:
            return "", ""
        return detail.get("title") or "", detail.get("text") or ""

    @app.callback(
        Output("chemistry-sdb-selected-document-summary", "children"),
        Input("chemistry-sdb-documents-grid", "selectedRows"),
    )
    def render_selected_sdb_document_summary(selected_rows: list[dict] | None):
        return _render_sdb_document_multi_selection_summary(selected_rows)

    @app.callback(
        Output("chemistry-sdb-review-issues-grid", "rowData"),
        Input("chemistry-sdb-documents-grid", "selectedRows"),
        Input("chemistry-sdb-refresh-token", "data"),
    )
    @_with_session
    def load_sdb_review_issues(session: Session, selected_rows: list[dict] | None, _refresh_token: int | None):
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        if not document_id:
            return []
        return [serialize_review_issue(issue) for issue in list_review_issues(session, int(document_id))]

    @app.callback(
        Output("chemistry-sdb-suva-source-summary", "children"),
        Output("chemistry-sdb-suva-items-grid", "rowData"),
        Input("chemistry-sdb-documents-grid", "selectedRows"),
        Input("chemistry-sdb-refresh-token", "data"),
    )
    @_with_session
    def load_sdb_suva_check_callback(session: Session, selected_rows: list[dict] | None, _refresh_token: int | None):
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        product_id = row.get("product_id")
        if not document_id or not product_id:
            return _render_suva_check_summary(None), []
        check = latest_product_suva_check(session, int(product_id), int(document_id))
        data = serialize_suva_check(check) if check else None
        return _render_suva_check_summary(data), (data.get("items") if data else [])

    @app.callback(
        Output("chemistry-sdb-document-action-status", "children", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-suva-source-summary", "children", allow_duplicate=True),
        Output("chemistry-sdb-suva-items-grid", "rowData", allow_duplicate=True),
        Output({"type": "chemistry-sdb-section", "index": 8}, "value", allow_duplicate=True),
        Input("chemistry-sdb-suva-check-button", "n_clicks"),
        Input("chemistry-sdb-suva-section8-button", "n_clicks"),
        State("chemistry-sdb-documents-grid", "selectedRows"),
        State({"type": "chemistry-sdb-section", "index": 8}, "value"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-sdb-suva-check-button", "disabled"), True, False),
            (Output("chemistry-sdb-suva-section8-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def run_sdb_suva_action_callback(
        session: Session,
        _check_clicks: int | None,
        _section_clicks: int | None,
        selected_rows: list[dict] | None,
        section_8_value: str | None,
        refresh_token: int | None,
    ):
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        product_id = row.get("product_id")
        if not document_id or not product_id:
            return _render_chemical_internet_status("error", "Keine SDB-Version ausgewählt."), no_update, no_update, no_update, no_update
        try:
            if ctx.triggered_id == "chemistry-sdb-suva-check-button":
                data = run_product_suva_check(session, int(product_id), sds_id=int(document_id), checked_by="pim-ui")
                return (
                    _render_chemical_internet_status(
                        "error" if data.get("overall_status") == "BLOCKER" else "success",
                        f"SUVA-Prüfung abgeschlossen: {data.get('overall_status')}.",
                        details=[f"Stoffe geprüft: {len(data.get('items') or [])}"],
                    ),
                    (refresh_token or 0) + 1,
                    _render_suva_check_summary(data),
                    data.get("items") or [],
                    no_update,
                )
            check = latest_product_suva_check(session, int(product_id), int(document_id))
            if not check:
                raise ValueError("Keine SUVA-Prüfung vorhanden. Zuerst 'SUVA-Prüfung starten' ausführen.")
            block = generate_section_8_1_ch_block(session, check.id)
            updated_section_8 = _upsert_suva_block_in_section_8(section_8_value or "", block)
            document = session.get(ChemicalDocument, int(document_id))
            if document:
                full_text = document.generated_text or document.extracted_text or ""
                updated_text = _replace_section_in_sdb_text(full_text, 8, updated_section_8)
                if document.generated_text:
                    document.generated_text = updated_text
                else:
                    document.extracted_text = updated_text
                session.flush()
            data = serialize_suva_check(check)
            return (
                _render_chemical_internet_status("success", "SUVA-Block wurde in Abschnitt 8.1 übernommen.", details=["Arbeitsversion wurde gespeichert."]),
                (refresh_token or 0) + 1,
                _render_suva_check_summary(data),
                data.get("items") or [],
                updated_section_8,
            )
        except Exception as exc:
            return _render_chemical_internet_status("error", f"SUVA-Aktion fehlgeschlagen: {exc}"), no_update, no_update, no_update, no_update

    @app.callback(
        Output("chemistry-sdb-document-action-status", "children", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Input("chemistry-sdb-review-autofix-button", "n_clicks"),
        Input("chemistry-sdb-review-ignore-button", "n_clicks"),
        Input("chemistry-sdb-review-check-button", "n_clicks"),
        Input("chemistry-sdb-review-needs-review-button", "n_clicks"),
        State("chemistry-sdb-documents-grid", "selectedRows"),
        State("chemistry-sdb-review-issues-grid", "selectedRows"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-sdb-review-autofix-button", "disabled"), True, False),
            (Output("chemistry-sdb-review-ignore-button", "disabled"), True, False),
            (Output("chemistry-sdb-review-check-button", "disabled"), True, False),
            (Output("chemistry-sdb-review-needs-review-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def update_sdb_review_issue_callback(
        session: Session,
        _autofix_clicks: int | None,
        _ignore_clicks: int | None,
        _check_clicks: int | None,
        _needs_review_clicks: int | None,
        selected_document_rows: list[dict] | None,
        selected_issue_rows: list[dict] | None,
        refresh_token: int | None,
    ):
        document = (selected_document_rows or [{}])[0] if selected_document_rows else {}
        document_id = document.get("id")
        if not document_id:
            return _render_chemical_internet_status("error", "Keine SDB-Version ausgewählt."), no_update
        triggered = ctx.triggered_id
        if triggered == "chemistry-sdb-review-autofix-button":
            result = apply_safe_auto_fixes(session, int(document_id))
            return _render_chemical_internet_status(
                "success",
                "Sichere CH-SDB-Auto-Fixes angewendet.",
                details=[f"Angewendet: {len(result.get('applied') or [])}", f"Offene Issues: {result.get('issue_count')}"],
            ), (refresh_token or 0) + 1
        issue_ids = []
        for issue in selected_issue_rows or []:
            issue_id = issue.get("id")
            if issue_id in (None, ""):
                continue
            issue_ids.append(int(issue_id))
        if not issue_ids:
            return _render_chemical_internet_status("error", "Kein Review-Issue ausgewählt."), no_update
        target_status = {
            "chemistry-sdb-review-ignore-button": "ignored",
            "chemistry-sdb-review-check-button": "checked",
            "chemistry-sdb-review-needs-review-button": "needs_review",
        }.get(str(triggered), "open")
        updated = [mark_issue_status(session, issue_id, target_status) for issue_id in issue_ids]
        return _render_chemical_internet_status(
            "success",
            f"{len(updated)} Review-Issue(s) Status gesetzt: {target_status}.",
            details=[f"IDs: {', '.join(str(row.get('id')) for row in updated)}"],
        ), (refresh_token or 0) + 1

    @app.callback(
        Output("chemistry-sdb-document-action-status", "children", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Input("chemistry-sdb-document-save-button", "n_clicks"),
        State("chemistry-sdb-documents-grid", "selectedRows"),
        State("chemistry-sdb-document-edit-title", "value"),
        State("chemistry-sdb-document-edit-text", "value"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_selected_sdb_document_text(
        session: Session,
        _clicks: int | None,
        selected_rows: list[dict] | None,
        title: str | None,
        text: str | None,
        refresh_token: int | None,
    ):
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        if not document_id:
            return "Kein SDB-Dokument ausgewählt.", no_update
        detail = update_chemical_document_text(session, int(document_id), title=title, text=text)
        return f"SDB-Dokument {detail['id']} gespeichert. Status: {detail['status']}.", (refresh_token or 0) + 1

    @app.callback(
        Output("chemistry-sdb-prompt-status", "children"),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Input("chemistry-sdb-prompt-save-button", "n_clicks"),
        State("chemistry-sdb-prompt-id", "value"),
        State("chemistry-sdb-prompt-name", "value"),
        State("chemistry-sdb-prompt-source-locale", "value"),
        State("chemistry-sdb-prompt-target-locale", "value"),
        State("chemistry-sdb-prompt-target-region", "value"),
        State("chemistry-sdb-prompt-active", "value"),
        State("chemistry-sdb-prompt-system", "value"),
        State("chemistry-sdb-prompt-template", "value"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_sdb_prompt_callback(
        session: Session,
        _clicks: int | None,
        prompt_id: int | None,
        name: str | None,
        source_locale: str | None,
        target_locale: str | None,
        target_region: str | None,
        active: bool | None,
        system_prompt: str | None,
        user_prompt_template: str | None,
        refresh_token: int | None,
    ):
        prompt = save_sdb_translation_prompt(
            session,
            prompt_id=prompt_id,
            name=name or "SDB-Prompt",
            source_locale=source_locale,
            target_locale=target_locale,
            target_region=target_region,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            active=bool(active),
        )
        return f"SDB-Prompt {prompt.id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("chemistry-sdb-translation-status", "children"),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Input("chemistry-sdb-translation-generate-button", "n_clicks"),
        Input("chemistry-sdb-region-draft-generate-button", "n_clicks"),
        State("selected-chemical-product-id", "data"),
        State("chemistry-sdb-translation-source-document-id", "value"),
        State("chemistry-sdb-translation-source-locale", "value"),
        State("chemistry-sdb-translation-target-locale", "value"),
        State("chemistry-sdb-translation-target-region", "value"),
        State("chemistry-sdb-translation-prompt-id", "value"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def generate_sdb_translation_callback(
        session: Session,
        _translate_clicks: int | None,
        _region_clicks: int | None,
        product_id: int | None,
        source_document_id: int | None,
        source_locale: str | None,
        target_locale: str | None,
        target_region: str | None,
        prompt_id: int | None,
        refresh_token: int | None,
    ):
        if not product_id:
            return "Kein Chemieprodukt ausgewählt.", "Kein Chemieprodukt ausgewählt.", no_update
        config = get_sdb_translation_config_status()
        result = generate_sdb_translation_draft(
            session,
            product_id=int(product_id),
            source_document_id=int(source_document_id or 0),
            source_locale=source_locale,
            target_locale=target_locale or "",
            target_region=target_region or "",
            prompt_id=prompt_id,
        )
        details = (
            f"Status: {result.get('status')} · Provider: {config.get('provider')} · Modell: {config.get('model')} · "
            f"{result.get('message') or ''}"
        )
        return str(result.get("message") or details), details, (refresh_token or 0) + 1

    @app.callback(
        Output("chemistry-sdb-document-action-status", "children"),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Input("chemistry-sdb-document-review-button", "n_clicks"),
        Input("chemistry-sdb-document-archive-button", "n_clicks"),
        Input("chemistry-sdb-document-delete-button", "n_clicks"),
        Input("chemistry-sdb-ch-review-button", "n_clicks"),
        Input("chemistry-sdb-document-pdf-button", "n_clicks"),
        Input("chemistry-sdb-final-release-button", "n_clicks"),
        Input("chemistry-sdb-document-set-status-button", "n_clicks"),
        State("chemistry-sdb-documents-grid", "selectedRows"),
        State("chemistry-sdb-document-status-set", "value"),
        State("chemistry-sdb-refresh-token", "data"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-sdb-document-review-button", "disabled"), True, False),
            (Output("chemistry-sdb-document-archive-button", "disabled"), True, False),
            (Output("chemistry-sdb-document-delete-button", "disabled"), True, False),
            (Output("chemistry-sdb-ch-review-button", "disabled"), True, False),
            (Output("chemistry-sdb-document-pdf-button", "disabled"), True, False),
            (Output("chemistry-sdb-final-release-button", "disabled"), True, False),
            (Output("chemistry-sdb-document-set-status-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def update_sdb_document_status_callback(
        session: Session,
        _review_clicks: int | None,
        _archive_clicks: int | None,
        _delete_clicks: int | None,
        _ch_review_clicks: int | None,
        _pdf_clicks: int | None,
        _final_release_clicks: int | None,
        _set_status_clicks: int | None,
        selected_rows: list[dict] | None,
        selected_status: str | None,
        refresh_token: int | None,
    ):
        document_ids = _selected_sdb_document_ids(selected_rows)
        row = (selected_rows or [{}])[0] if selected_rows else {}
        document_id = row.get("id")
        if not document_id:
            return _render_chemical_internet_status("error", "Kein SDB-Dokument ausgewählt.", details=["Bitte zuerst eine SDB-Version in der Tabelle auswählen."]), no_update
        triggered = ctx.triggered_id
        if triggered == "chemistry-sdb-document-archive-button":
            archived = [archive_chemical_document(session, document_id) for document_id in document_ids]
            return _render_chemical_internet_status(
                "success",
                f"{len(archived)} SDB-Dokument(e) archiviert.",
                details=[f"IDs: {', '.join(str(row.get('id')) for row in archived)}"],
            ), (refresh_token or 0) + 1
        if triggered == "chemistry-sdb-document-delete-button":
            deleted = [delete_chemical_document(session, document_id) for document_id in document_ids]
            archived_asset_rows = [row for row in deleted if row.get("delete_mode") == "archived_asset_source"]
            hard_deleted_rows = [row for row in deleted if row.get("delete_mode") == "deleted"]
            if archived_asset_rows:
                return _render_chemical_internet_status(
                    "warning",
                    f"{len(hard_deleted_rows)} SDB-Dokument(e) gelöscht, {len(archived_asset_rows)} asset-basierte Version(en) archiviert.",
                    details=[
                        "Asset-basierte SDB-Versionen würden nach hartem Löschen automatisch wieder erscheinen.",
                        f"Gelöscht: {', '.join(str(row.get('id')) for row in hard_deleted_rows) or '-'}",
                        f"Archiviert: {', '.join(str(row.get('id')) for row in archived_asset_rows) or '-'}",
                    ],
                ), (refresh_token or 0) + 1
            return _render_chemical_internet_status(
                "success",
                f"{len(hard_deleted_rows)} SDB-Dokument(e) gelöscht.",
                details=[f"IDs: {', '.join(str(row.get('id')) for row in hard_deleted_rows)}"],
            ), (refresh_token or 0) + 1
        if triggered == "chemistry-sdb-ch-review-button":
            if len(document_ids) != 1:
                return _render_chemical_internet_status("error", "CH-SDB-Review bitte für genau eine SDB-Version ausführen."), no_update
            result = review_sds_document(session, int(document_id))
            return _render_chemical_internet_status(
                "error" if int(result.get("critical_count") or 0) else "success",
                f"CH-SDB-Review abgeschlossen: {result.get('swiss_review_status')}.",
                details=[
                    f"Kritisch: {result.get('critical_count')}",
                    f"Warnungen: {result.get('warning_count')}",
                    f"Info: {result.get('info_count')}",
                    f"Compliance-Score: {result.get('compliance_score')}",
                ],
            ), (refresh_token or 0) + 1
        if triggered == "chemistry-sdb-document-pdf-button":
            if len(document_ids) != 1:
                return _render_chemical_internet_status("error", "PDF-Erzeugung bitte für genau eine SDB-Version ausführen."), no_update
            try:
                document = render_chemical_document_pdf(session, int(document_id))
            except ValueError as exc:
                return _render_chemical_internet_status(
                    "error",
                    f"PDF für SDB-Dokument {document_id} konnte nicht erzeugt werden.",
                    details=[str(exc), "Die Buttons sind wieder freigegeben. Prüfe, ob die gewählte Version Text enthält."],
                ), no_update
            pdf_url = document.get("pdf_url")
            return _render_chemical_internet_status(
                "success",
                f"PDF für SDB-Dokument {document['id']} wurde erzeugt.",
                details=[
                    "Status: abgeschlossen",
                    f"Datei: {document.get('filename') or '-'}",
                    "Öffnen/Download: " if not pdf_url else "",
                ],
            ) if not pdf_url else html.Div(
                [
                    _render_chemical_internet_status(
                        "success",
                        f"PDF für SDB-Dokument {document['id']} wurde erzeugt.",
                        details=[f"Datei: {document.get('filename') or '-'}"],
                    ),
                    html.Div(html.A("PDF öffnen / herunterladen", href=pdf_url, target="_blank"), style={"marginTop": "8px"}),
                ]
            ), (refresh_token or 0) + 1
        if triggered == "chemistry-sdb-final-release-button":
            if len(document_ids) != 1:
                return _render_chemical_internet_status("error", "Finale Freigabe bitte für genau eine SDB-Version ausführen."), no_update
            try:
                document = release_document_as_final(session, int(document_id))
            except ValueError as exc:
                return _render_chemical_internet_status(
                    "error",
                    "Finale Freigabe blockiert.",
                    details=[str(exc), "Bitte CH-SDB prüfen, kritische Punkte beheben und erneut freigeben."],
                ), (refresh_token or 0) + 1
            return _render_chemical_internet_status(
                "success",
                f"SDB-Dokument {document['document_id']} wurde final freigegeben.",
                details=[f"Status: {document.get('status')}", f"CH-Review: {document.get('swiss_review_status')}"],
            ), (refresh_token or 0) + 1
        if triggered == "chemistry-sdb-document-set-status-button":
            updated = [update_chemical_document_status(session, document_id, selected_status or "checked") for document_id in document_ids]
            return _render_chemical_internet_status(
                "success",
                f"{len(updated)} SDB-Dokument(e) Status gesetzt: {selected_status or 'checked'}.",
                details=[f"IDs: {', '.join(str(row.get('id')) for row in updated)}"],
            ), (refresh_token or 0) + 1
        reviewed = [mark_chemical_document_reviewed(session, document_id) for document_id in document_ids]
        return _render_chemical_internet_status(
            "success",
            f"{len(reviewed)} SDB-Dokument(e) als geprüft markiert.",
            details=[f"IDs: {', '.join(str(row.get('id')) for row in reviewed)}"],
        ), (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("chemistry-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-product-sku", "value"),
        State("chemistry-product-title", "value"),
        State("chemistry-product-brand", "value"),
        State("chemistry-product-status", "value"),
        State("chemistry-product-language", "value"),
        State("chemistry-is-chemical", "value"),
        State("chemistry-chemical-type", "value"),
        State("chemistry-ufi", "value"),
        State("chemistry-voc-content-percent", "value"),
        State("chemistry-cas-number", "value"),
        State("chemistry-ec-number", "value"),
        State("chemistry-un-number", "value"),
        State("chemistry-hazard-class", "value"),
        State("chemistry-packing-group", "value"),
        State("chemistry-adr-relevant", "value"),
        State("chemistry-ghs-pictograms", "value"),
        State("chemistry-signal-word", "value"),
        State("chemistry-adr-pictograms", "value"),
        State("chemistry-environmentally-hazardous", "value"),
        State("chemistry-hazard-statements", "value"),
        State("chemistry-precautionary-statements", "value"),
        State("chemistry-wgk", "value"),
        State("chemistry-storage-class", "value"),
        State("chemistry-sds-available", "value"),
        State("chemistry-sds-url", "value"),
        State("chemistry-sds-asset-id", "value"),
        State("chemistry-density", "value"),
        State("chemistry-color", "value"),
        State("chemistry-odor", "value"),
        State("chemistry-ph-value", "value"),
        State("chemistry-flash-point", "value"),
        State("chemistry-boiling-point", "value"),
        State("chemistry-viscosity", "value"),
        State("chemistry-solubility", "value"),
        State("chemistry-business-only", "value"),
        State("chemistry-age-check-required", "value"),
        State("chemistry-shippable", "value"),
        State("chemistry-limited-quantity", "value"),
        State("chemistry-hazard-shipping-note", "value"),
        State("chemistry-shop-active", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_chemical_product_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        product_id: int | None,
        sku: str | None,
        title: str | None,
        brand_name: str | None,
        status: str | None,
        source_language: str | None,
        is_chemical: bool | None,
        chemical_type: str | None,
        ufi: str | None,
        voc_content_percent: str | None,
        cas_number: str | None,
        ec_number: str | None,
        un_number: str | None,
        hazard_class: str | None,
        packing_group: str | None,
        adr_relevant: bool | None,
        ghs_pictograms: list[str] | None,
        signal_word: str | None,
        adr_pictograms: list[str] | None,
        environmentally_hazardous: bool | None,
        hazard_statements: str | None,
        precautionary_statements: str | None,
        wgk: str | None,
        storage_class: str | None,
        sds_available: bool | None,
        sds_url: str | None,
        sds_asset_id: int | None,
        density: str | None,
        color: str | None,
        odor: str | None,
        ph_value: str | None,
        flash_point: str | None,
        boiling_point: str | None,
        viscosity: str | None,
        solubility: str | None,
        business_only: bool | None,
        age_check_required: bool | None,
        shippable: bool | None,
        limited_quantity: str | None,
        hazard_shipping_note: str | None,
        shop_active: bool | None,
    ):
        if not product_id or not sku or not title or not status:
            return "Produkt-ID, SKU, Titel und Status sind Pflicht.", no_update
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return "Chemieprodukt nicht gefunden.", no_update
        try:
            chemical_safety = _build_chemical_safety_payload(ghs_pictograms, signal_word, adr_pictograms, hazard_class, environmentally_hazardous)
            normalized_wgk = normalize_wgk(wgk) if wgk else None
            normalized_storage_class = normalize_storage_class(storage_class) if storage_class else None
            chemical_safety = build_chem_safety_payload(
                {**(detail.get("chemical_safety_json") or {}), **chemical_safety},
                wgk=normalized_wgk,
                storage_class=normalized_storage_class,
            )
            normalized_hazard_class = (hazard_class or "").strip() or (chemical_safety.get("adr_class") if "ADR_8" in (adr_pictograms or []) else None)
            normalized_adr_relevant = bool(adr_relevant or adr_pictograms)
            update_product(
                session,
                int(product_id),
                ProductUpdate(
                    sku=sku,
                    title=title,
                    description=detail.get("description"),
                    brand_name=brand_name,
                    status=status,
                    source_language=(source_language or detail.get("source_language") or "en").strip(),
                    category_channel_code=detail.get("category_channel_code") or DEFAULT_CATEGORY_CHANNEL_CODE,
                    category_ids=detail.get("category_ids") or [],
                    is_chemical=bool(is_chemical),
                    chemical_type=(chemical_type or "").strip() or None,
                    ufi=(ufi or "").strip() or None,
                    voc_content_percent=(voc_content_percent or "").strip() or None,
                    cas_number=(cas_number or "").strip() or None,
                    ec_number=(ec_number or "").strip() or None,
                    un_number=(un_number or "").strip() or None,
                    hazard_class=str(normalized_hazard_class or "").strip() or None,
                    packing_group=(packing_group or "").strip() or None,
                    adr_relevant=normalized_adr_relevant,
                    ghs_pictograms="|".join(ghs_pictograms or []) or None,
                    signal_word=_signal_word_for_legacy_storage(signal_word),
                    chemical_safety_json=chemical_safety,
                    hazard_statements=(hazard_statements or "").strip() or None,
                    precautionary_statements=(precautionary_statements or "").strip() or None,
                    wgk=normalized_wgk,
                    wgk_label=wgk_label(normalized_wgk),
                    storage_class=normalized_storage_class,
                    storage_class_label=storage_class_label(normalized_storage_class),
                    sds_available=bool(sds_available),
                    sds_url=(sds_url or "").strip() or None,
                    sds_asset_id=_int_or_none(sds_asset_id),
                    density=(density or "").strip() or None,
                    color=(color or "").strip() or None,
                    odor=(odor or "").strip() or None,
                    ph_value=(ph_value or "").strip() or None,
                    flash_point=(flash_point or "").strip() or None,
                    boiling_point=(boiling_point or "").strip() or None,
                    viscosity=(viscosity or "").strip() or None,
                    solubility=(solubility or "").strip() or None,
                    business_only=bool(business_only),
                    age_check_required=bool(age_check_required),
                    shippable=bool(shippable),
                    limited_quantity=(limited_quantity or "").strip() or None,
                    hazard_shipping_note=(hazard_shipping_note or "").strip() or None,
                    shop_active=bool(shop_active),
                ),
            )
        except ValueError as exc:
            return str(exc), no_update
        return f"Chemiedaten für Produkt {product_id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-enrichment-status", "children", allow_duplicate=True),
        Input("chemistry-enrichment-run-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-enrichment-reference-urls", "value"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-enrichment-run-button", "disabled"), True, False),
            (Output("chemistry-enrichment-apply-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def run_chemical_enrichment_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        product_id: int | None,
        reference_urls: str | None,
    ):
        if not product_id:
            status = _render_chemical_internet_status("error", "Kein Chemieprodukt ausgewählt.")
            return "Kein Chemieprodukt ausgewählt.", no_update, status
        try:
            result = run_product_chemical_enrichment(
                session,
                int(product_id),
                [value.strip() for value in str(reference_urls or "").splitlines() if value.strip()],
            )
        except ValueError as exc:
            status = _render_chemical_internet_status("error", "Internet-Anreicherung konnte nicht gestartet werden.", details=[str(exc)])
            return str(exc), no_update, status
        except Exception as exc:
            status = _render_chemical_internet_status("error", "Internet-Anreicherung fehlgeschlagen.", details=[str(exc)])
            return f"Internet-Anreicherung fehlgeschlagen: {exc}", no_update, status
        documents = result.get("documents") or []
        warnings = result.get("warnings") or result.get("warnings_json") or []
        status = _render_chemical_internet_status(
            "success" if str(result.get("status") or "").lower() not in {"failed", "error"} else "error",
            f"Internet-Anreicherung abgeschlossen: {result.get('status') or '-'}",
            details=[
                f"Produkt-ID: {product_id}",
                f"Dokumente/PDF-Links: {len(documents)}",
                f"Warnungen: {len(warnings)}" if isinstance(warnings, list) else "Warnungen: -",
            ],
        )
        return (
            f"Internet-Anreicherung abgeschlossen: {result.get('status')} · Dokumente: {len(result.get('documents') or [])}",
            (refresh_token or 0) + 1,
            status,
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-enrichment-status", "children", allow_duplicate=True),
        Input("chemistry-enrichment-apply-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-enrichment-overwrite-mode", "value"),
        State("chemistry-enrichment-suggestions-grid", "selectedRows"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-enrichment-run-button", "disabled"), True, False),
            (Output("chemistry-enrichment-apply-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def apply_chemical_enrichment_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        product_id: int | None,
        overwrite_mode: str | None,
        selected_rows: list[dict] | None,
    ):
        if not product_id:
            status = _render_chemical_internet_status("error", "Kein Chemieprodukt ausgewählt.")
            return "Kein Chemieprodukt ausgewählt.", no_update, status
        try:
            selected_fields = [str(row.get("field")) for row in (selected_rows or []) if row.get("field")]
            result = apply_product_chemical_enrichment_suggestions(
                session,
                int(product_id),
                selected_fields=selected_fields,
                overwrite_existing=(overwrite_mode == "overwrite"),
            )
        except ValueError as exc:
            if "Keine Vorschläge" not in str(exc):
                status = _render_chemical_internet_status("error", "Vorschläge konnten nicht übernommen werden.", details=[str(exc)])
                return str(exc), no_update, status
            result = apply_product_chemical_enrichment(
                session,
                int(product_id),
                overwrite_existing=(overwrite_mode == "overwrite"),
            )
        applied_fields = result.get("applied_fields") or []
        status = _render_chemical_internet_status(
            "success",
            "Anreicherung übernommen.",
            details=[
                f"Geänderte Felder: {len(applied_fields)}",
                f"Modus: {'überschreiben' if overwrite_mode == 'overwrite' else 'nur leere Felder'}",
            ],
        )
        return (
            f"Anreicherung übernommen. Geänderte Felder: {len(result.get('applied_fields') or [])}",
            (refresh_token or 0) + 1,
            status,
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-protocol-store", "data", allow_duplicate=True),
        Input("chemistry-sdb-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-sdb-refresh-token", "data"),
        State("chemistry-sdb-protocol-store", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-sdb-source-url", "value"),
        State("chemistry-sdb-pdf-url", "value"),
        State("chemistry-sdb-source-asset-id", "value"),
        State("chemistry-sdb-review-status", "value"),
        State("chemistry-sdb-version-label", "value"),
        State("chemistry-sdb-effective-date", "value"),
        State("chemistry-sdb-document-title", "value"),
        State("chemistry-sdb-issuer-name", "value"),
        State("chemistry-sdb-issuer-address-line1", "value"),
        State("chemistry-sdb-issuer-address-line2", "value"),
        State("chemistry-sdb-issuer-postal-code", "value"),
        State("chemistry-sdb-issuer-city", "value"),
        State("chemistry-sdb-issuer-country-code", "value"),
        State("chemistry-sdb-issuer-phone", "value"),
        State("chemistry-sdb-issuer-email", "value"),
        State("chemistry-sdb-raw-text", "value"),
        State({"type": "chemistry-sdb-section", "index": ALL}, "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_chemical_sdb_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sdb_refresh_token: int,
        protocol_entries: list[dict] | None,
        product_id: int | None,
        source_url: str | None,
        pdf_url: str | None,
        source_asset_id: int | None,
        review_status: str | None,
        version_label: str | None,
        effective_date: str | None,
        document_title: str | None,
        issuer_name: str | None,
        issuer_address_line1: str | None,
        issuer_address_line2: str | None,
        issuer_postal_code: str | None,
        issuer_city: str | None,
        issuer_country_code: str | None,
        issuer_phone: str | None,
        issuer_email: str | None,
        raw_text: str | None,
        section_values: list[str] | None,
    ):
        if not product_id:
            return "Kein Chemieprodukt ausgewählt.", no_update, no_update, no_update
        product = session.get(Product, int(product_id))
        if product is None:
            return "Chemieprodukt nicht gefunden.", no_update, no_update, no_update
        stored_sdb = get_product_sdb(session, int(product_id))
        product_detail = get_product_detail(session, int(product_id)) or {}
        product_context = {
            "un_number": product_detail.get("un_number") or product.un_number,
            "hazard_class": product_detail.get("hazard_class") or product.hazard_class,
            "packing_group": product_detail.get("packing_group") or product.packing_group,
            "hazard_shipping_note": product_detail.get("hazard_shipping_note") or product.hazard_shipping_note,
            "ufi": product_detail.get("ufi") or product.ufi,
            "voc_content_percent": product_detail.get("voc_content_percent") or product.voc_content_percent,
            "density": product_detail.get("density") or product.density,
            "color": product_detail.get("color") or product.color,
            "odor": product_detail.get("odor") or product.odor,
            "ph_value": product_detail.get("ph_value") or product.ph_value,
            "flash_point": product_detail.get("flash_point") or product.flash_point,
            "boiling_point": product_detail.get("boiling_point") or product.boiling_point,
            "viscosity": product_detail.get("viscosity") or product.viscosity,
            "solubility": product_detail.get("solubility") or product.solubility,
        }
        normalized_pdf_url = (pdf_url or "").strip() or None
        normalized_raw_text = (raw_text or "").strip() or None
        sections_json = _merge_sdb_ui_sections(
            stored_sdb.get("sections_json"),
            section_values,
            issuer_name=(issuer_name or "").strip() or None,
            issuer_address_line1=(issuer_address_line1 or "").strip() or None,
            issuer_address_line2=(issuer_address_line2 or "").strip() or None,
            issuer_postal_code=(issuer_postal_code or "").strip() or None,
            issuer_city=(issuer_city or "").strip() or None,
            issuer_country_code=(issuer_country_code or "").strip() or None,
            issuer_phone=(issuer_phone or "").strip() or None,
            issuer_email=(issuer_email or "").strip() or None,
            product_context=product_context,
        )
        should_autoparse_pdf = bool(
            normalized_pdf_url
            and not normalized_raw_text
            and not any(section.get("content") for section in sections_json.values())
        )
        parser_status = "manual"
        effective_source_asset_id = _int_or_none(source_asset_id)
        if should_autoparse_pdf:
            try:
                parsed_pdf = ingest_product_sdb_pdf(session, int(product_id), normalized_pdf_url)
            except Exception as exc:
                return (
                    f"SDB-PDF konnte nicht geladen oder geparst werden: {exc}",
                    no_update,
                    no_update,
                    _append_sdb_protocol(protocol_entries, "SDB speichern", "fehler", f"Automatisches Einlesen aus PDF fehlgeschlagen: {exc}"),
                )
            normalized_raw_text = str(parsed_pdf.get("raw_text") or "").strip() or None
            sections_json = parsed_pdf.get("sections_json") or sections_json
            effective_source_asset_id = _int_or_none(parsed_pdf.get("source_asset_id"))
            parser_status = str(parsed_pdf.get("parser_status") or "parsed")
        upsert_product_sdb(
            session,
            int(product_id),
            ProductSDBUpdate(
                source_url=(source_url or "").strip() or None,
                pdf_url=normalized_pdf_url,
                source_asset_id=effective_source_asset_id,
                parser_status=parser_status,
                review_status=(review_status or "").strip() or None,
                version_label=(version_label or "").strip() or None,
                effective_date=_normalize_sdb_effective_date_for_storage(effective_date),
                document_title=(document_title or "").strip() or None,
                issuer_name=(issuer_name or "").strip() or None,
                issuer_address_line1=(issuer_address_line1 or "").strip() or None,
                issuer_address_line2=(issuer_address_line2 or "").strip() or None,
                issuer_postal_code=(issuer_postal_code or "").strip() or None,
                issuer_city=(issuer_city or "").strip() or None,
                issuer_country_code=(issuer_country_code or "").strip() or None,
                issuer_phone=(issuer_phone or "").strip() or None,
                issuer_email=(issuer_email or "").strip() or None,
                action_log_json=_append_sdb_protocol(
                    protocol_entries,
                    "SDB speichern",
                    "ok",
                    "SDB-Metadaten und Abschnitte wurden gespeichert."
                    + (" PDF wurde beim Speichern deterministisch übernommen." if should_autoparse_pdf else ""),
                ),
                raw_text=normalized_raw_text,
                sections_json=sections_json,
            ),
        )
        if normalized_pdf_url:
            product.sds_url = normalized_pdf_url or product.sds_url
            product.sds_available = True
        if effective_source_asset_id:
            product.sds_asset_id = effective_source_asset_id
            product.sds_available = True
        session.flush()
        return (
            f"SDB für Produkt {product_id} gespeichert.",
            (refresh_token or 0) + 1,
            (sdb_refresh_token or 0) + 1,
            get_product_sdb(session, int(product_id)).get("action_log_json") or [],
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-parser-status", "children", allow_duplicate=True),
        Output("chemistry-sdb-raw-text", "value", allow_duplicate=True),
        Output({"type": "chemistry-sdb-section", "index": ALL}, "value", allow_duplicate=True),
        Output("chemistry-sdb-pdf-link", "children", allow_duplicate=True),
        Output("chemistry-sdb-protocol-store", "data", allow_duplicate=True),
        Input("chemistry-sdb-clear-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-sdb-refresh-token", "data"),
        State("chemistry-sdb-protocol-store", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-sdb-source-url", "value"),
        State("chemistry-sdb-pdf-url", "value"),
        State("chemistry-sdb-source-asset-id", "value"),
        State("chemistry-sdb-review-status", "value"),
        State("chemistry-sdb-version-label", "value"),
        State("chemistry-sdb-effective-date", "value"),
        State("chemistry-sdb-document-title", "value"),
        State("chemistry-sdb-issuer-name", "value"),
        State("chemistry-sdb-issuer-address-line1", "value"),
        State("chemistry-sdb-issuer-address-line2", "value"),
        State("chemistry-sdb-issuer-postal-code", "value"),
        State("chemistry-sdb-issuer-city", "value"),
        State("chemistry-sdb-issuer-country-code", "value"),
        State("chemistry-sdb-issuer-phone", "value"),
        State("chemistry-sdb-issuer-email", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def reparse_chemical_sdb_pdf_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sdb_refresh_token: int,
        protocol_entries: list[dict] | None,
        product_id: int | None,
        source_url: str | None,
        pdf_url: str | None,
        source_asset_id: int | None,
        review_status: str | None,
        version_label: str | None,
        effective_date: str | None,
        document_title: str | None,
        issuer_name: str | None,
        issuer_address_line1: str | None,
        issuer_address_line2: str | None,
        issuer_postal_code: str | None,
        issuer_city: str | None,
        issuer_country_code: str | None,
        issuer_phone: str | None,
        issuer_email: str | None,
    ):
        if not product_id:
            return "Kein Chemieprodukt ausgewählt.", no_update, no_update, no_update, no_update, no_update, no_update, no_update
        upsert_product_sdb(
            session,
            int(product_id),
            ProductSDBUpdate(
                source_url=(source_url or "").strip() or None,
                pdf_url=(pdf_url or "").strip() or None,
                source_asset_id=_int_or_none(source_asset_id),
                parser_status="cleared",
                review_status=(review_status or "").strip() or None,
                version_label=(version_label or "").strip() or None,
                effective_date=_normalize_sdb_effective_date_for_storage(effective_date),
                document_title=(document_title or "").strip() or None,
                issuer_name=(issuer_name or "").strip() or None,
                issuer_address_line1=(issuer_address_line1 or "").strip() or None,
                issuer_address_line2=(issuer_address_line2 or "").strip() or None,
                issuer_postal_code=(issuer_postal_code or "").strip() or None,
                issuer_city=(issuer_city or "").strip() or None,
                issuer_country_code=(issuer_country_code or "").strip() or None,
                issuer_phone=(issuer_phone or "").strip() or None,
                issuer_email=(issuer_email or "").strip() or None,
                action_log_json=_append_sdb_protocol(protocol_entries, "Rohtext + Abschnitte leeren", "ok", "Rohtext, Abschnitte und generiertes PDF wurden für den nächsten deterministischen Import zurückgesetzt."),
                raw_text=None,
                sections_json={f"section_{index}": {"title": SDB_SECTION_TITLES[index], "content": ""} for index in SDB_SECTION_TITLES},
                generated_pdf_path=None,
            ),
        )
        session.flush()
        return (
            f"Rohtext und Abschnitte für Produkt {product_id} geleert.",
            (refresh_token or 0) + 1,
            (sdb_refresh_token or 0) + 1,
            "Parser-Status: cleared",
            "",
            [""] * len(SDB_SECTION_TITLES),
            html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"}),
            get_product_sdb(session, int(product_id)).get("action_log_json") or [],
        )


    def _sdb_section_no_update() -> list[object]:
        return [no_update] * len(SDB_SECTION_TITLES)

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-source-asset-id", "value", allow_duplicate=True),
        Output("chemistry-sdb-parser-status", "children", allow_duplicate=True),
        Output("chemistry-sdb-raw-text", "value", allow_duplicate=True),
        Output({"type": "chemistry-sdb-section", "index": ALL}, "value", allow_duplicate=True),
        Output("chemistry-sdb-pdf-link", "children", allow_duplicate=True),
        Output("chemistry-sdb-protocol-store", "data", allow_duplicate=True),
        Input("chemistry-sdb-import-from-source-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-sdb-refresh-token", "data"),
        State("chemistry-sdb-protocol-store", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-sdb-source-url", "value"),
        State("chemistry-sdb-pdf-url", "value"),
        State("chemistry-sdb-source-asset-id", "value"),
        State("chemistry-sdb-documents-grid", "selectedRows"),
        State("chemistry-sdb-review-status", "value"),
        State("chemistry-sdb-version-label", "value"),
        State("chemistry-sdb-effective-date", "value"),
        State("chemistry-sdb-document-title", "value"),
        State("chemistry-sdb-issuer-name", "value"),
        State("chemistry-sdb-issuer-address-line1", "value"),
        State("chemistry-sdb-issuer-address-line2", "value"),
        State("chemistry-sdb-issuer-postal-code", "value"),
        State("chemistry-sdb-issuer-city", "value"),
        State("chemistry-sdb-issuer-country-code", "value"),
        State("chemistry-sdb-issuer-phone", "value"),
        State("chemistry-sdb-issuer-email", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def import_chemical_sdb_from_source_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sdb_refresh_token: int,
        protocol_entries: list[dict] | None,
        product_id: int | None,
        source_url: str | None,
        pdf_url: str | None,
        source_asset_id: int | None,
        selected_sdb_document_rows: list[dict] | None,
        review_status: str | None,
        version_label: str | None,
        effective_date: str | None,
        document_title: str | None,
        issuer_name: str | None,
        issuer_address_line1: str | None,
        issuer_address_line2: str | None,
        issuer_postal_code: str | None,
        issuer_city: str | None,
        issuer_country_code: str | None,
        issuer_phone: str | None,
        issuer_email: str | None,
    ):
        if not product_id:
            return "Kein Chemieprodukt ausgewählt.", no_update, no_update, no_update, no_update, no_update, _sdb_section_no_update(), no_update, no_update
        product = session.get(Product, int(product_id))
        if product is None:
            return "Chemieprodukt nicht gefunden.", no_update, no_update, no_update, no_update, no_update, _sdb_section_no_update(), no_update, no_update

        normalized_pdf_url = (pdf_url or "").strip() or None
        normalized_source_url = (source_url or "").strip() or None
        selected_sdb_document = (selected_sdb_document_rows or [{}])[0] if selected_sdb_document_rows else {}
        effective_source_asset_id = _int_or_none(source_asset_id) or _int_or_none(selected_sdb_document.get("asset_id"))
        if effective_source_asset_id is None:
            selected_file_url = str(selected_sdb_document.get("file_url") or selected_sdb_document.get("pdf_url") or "").strip()
            match = re.search(r"/asset-file/(\d+)", selected_file_url)
            if match:
                effective_source_asset_id = _int_or_none(match.group(1))

        try:
            if effective_source_asset_id and (not normalized_pdf_url or normalized_pdf_url.startswith("/asset-file/")):
                parsed_pdf = ingest_product_sdb_asset(session, int(product_id), int(effective_source_asset_id))
                parsed_pdf, suva_report = _add_suva_suggestions_to_parsed_sdb(session, parsed_pdf, product)
                section_values = [
                    str(((parsed_pdf.get("sections_json") or {}).get(f"section_{index}") or {}).get("content") or "")
                    for index in SDB_SECTION_TITLES
                ]
                action_log = _append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "ok", f"Quell-Asset wurde geparst und als strukturierte SDB-Basis übernommen. Asset: {parsed_pdf.get('source_asset_id') or '-'}")
                action_log = _append_sdb_protocol(action_log, "SUVA-Grenzwerte prüfen", "ok" if suva_report.get("status") != "no_suva_source" else "hinweis", _suva_import_protocol_message(suva_report))
                upsert_product_sdb(
                    session,
                    int(product_id),
                    ProductSDBUpdate(
                        source_url=normalized_source_url,
                        pdf_url=str(parsed_pdf.get("pdf_url") or ""),
                        source_asset_id=_int_or_none(parsed_pdf.get("source_asset_id")),
                        parser_status=str(parsed_pdf.get("parser_status") or "parsed"),
                        review_status=(review_status or "").strip() or None,
                        version_label=(version_label or "").strip() or None,
                        effective_date=_normalize_sdb_effective_date_for_storage(effective_date),
                        document_title=(document_title or "").strip() or None,
                        issuer_name=(issuer_name or "").strip() or None,
                        issuer_address_line1=(issuer_address_line1 or "").strip() or None,
                        issuer_address_line2=(issuer_address_line2 or "").strip() or None,
                        issuer_postal_code=(issuer_postal_code or "").strip() or None,
                        issuer_city=(issuer_city or "").strip() or None,
                        issuer_country_code=(issuer_country_code or "").strip() or None,
                        issuer_phone=(issuer_phone or "").strip() or None,
                        issuer_email=(issuer_email or "").strip() or None,
                        action_log_json=action_log,
                        raw_text=str(parsed_pdf.get("raw_text") or "").strip() or None,
                        sections_json=parsed_pdf.get("sections_json") or {},
                        generated_pdf_path=None,
                    ),
                )
                product.sds_url = str(parsed_pdf.get("pdf_url") or "") or product.sds_url
                product.sds_asset_id = _int_or_none(parsed_pdf.get("source_asset_id"))
                product.sds_available = True
                session.flush()
                return (
                    f"SDB aus Asset {effective_source_asset_id} für Produkt {product_id} übernommen.",
                    (refresh_token or 0) + 1,
                    (sdb_refresh_token or 0) + 1,
                    _int_or_none(parsed_pdf.get("source_asset_id")),
                    f"Parser-Status: {parsed_pdf.get('parser_status') or 'parsed'}",
                    str(parsed_pdf.get("raw_text") or ""),
                    section_values,
                    html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"}),
                    get_product_sdb(session, int(product_id)).get("action_log_json") or [],
                )

            if normalized_pdf_url:
                parsed_pdf = ingest_product_sdb_pdf(session, int(product_id), normalized_pdf_url, force_download=True)
                parsed_pdf, suva_report = _add_suva_suggestions_to_parsed_sdb(session, parsed_pdf, product)
                section_values = [
                    str(((parsed_pdf.get("sections_json") or {}).get(f"section_{index}") or {}).get("content") or "")
                    for index in SDB_SECTION_TITLES
                ]
                action_log = _append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "ok", f"PDF-Quelle wurde neu geladen, geparst und als strukturierte SDB-Basis übernommen. Asset: {parsed_pdf.get('source_asset_id') or '-'}")
                action_log = _append_sdb_protocol(action_log, "SUVA-Grenzwerte prüfen", "ok" if suva_report.get("status") != "no_suva_source" else "hinweis", _suva_import_protocol_message(suva_report))
                upsert_product_sdb(
                    session,
                    int(product_id),
                    ProductSDBUpdate(
                        source_url=normalized_source_url,
                        pdf_url=normalized_pdf_url,
                        source_asset_id=_int_or_none(parsed_pdf.get("source_asset_id")),
                        parser_status=str(parsed_pdf.get("parser_status") or "parsed"),
                        review_status=(review_status or "").strip() or None,
                        version_label=(version_label or "").strip() or None,
                        effective_date=_normalize_sdb_effective_date_for_storage(effective_date),
                        document_title=(document_title or "").strip() or None,
                        issuer_name=(issuer_name or "").strip() or None,
                        issuer_address_line1=(issuer_address_line1 or "").strip() or None,
                        issuer_address_line2=(issuer_address_line2 or "").strip() or None,
                        issuer_postal_code=(issuer_postal_code or "").strip() or None,
                        issuer_city=(issuer_city or "").strip() or None,
                        issuer_country_code=(issuer_country_code or "").strip() or None,
                        issuer_phone=(issuer_phone or "").strip() or None,
                        issuer_email=(issuer_email or "").strip() or None,
                        action_log_json=action_log,
                        raw_text=str(parsed_pdf.get("raw_text") or "").strip() or None,
                        sections_json=parsed_pdf.get("sections_json") or {},
                        generated_pdf_path=None,
                    ),
                )
                product.sds_url = normalized_pdf_url
                product.sds_asset_id = _int_or_none(parsed_pdf.get("source_asset_id"))
                product.sds_available = True
                session.flush()
                return (
                    f"SDB aus PDF für Produkt {product_id} neu übernommen.",
                    (refresh_token or 0) + 1,
                    (sdb_refresh_token or 0) + 1,
                    _int_or_none(parsed_pdf.get("source_asset_id")),
                    f"Parser-Status: {parsed_pdf.get('parser_status') or 'parsed'}",
                    str(parsed_pdf.get("raw_text") or ""),
                    section_values,
                    html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"}),
                    get_product_sdb(session, int(product_id)).get("action_log_json") or [],
                )

            if not normalized_source_url:
                return (
                    "Keine Quell-URL oder PDF-/SDB-URL vorhanden.",
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    no_update,
                    _sdb_section_no_update(),
                    no_update,
                    _append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "fehler", "Es ist weder eine Quell-URL noch eine PDF-/SDB-URL vorhanden."),
                )

            if normalized_source_url.lower().endswith(".pdf"):
                parsed_pdf = ingest_product_sdb_pdf(session, int(product_id), normalized_source_url, force_download=True)
                parsed_pdf, suva_report = _add_suva_suggestions_to_parsed_sdb(session, parsed_pdf, product)
                section_values = [
                    str(((parsed_pdf.get("sections_json") or {}).get(f"section_{index}") or {}).get("content") or "")
                    for index in SDB_SECTION_TITLES
                ]
                action_log = _append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "ok", f"Quell-PDF wurde deterministisch übernommen und geparst. Asset: {parsed_pdf.get('source_asset_id') or '-'}")
                action_log = _append_sdb_protocol(action_log, "SUVA-Grenzwerte prüfen", "ok" if suva_report.get("status") != "no_suva_source" else "hinweis", _suva_import_protocol_message(suva_report))
                upsert_product_sdb(
                    session,
                    int(product_id),
                    ProductSDBUpdate(
                        source_url=normalized_source_url,
                        pdf_url=normalized_source_url,
                        source_asset_id=_int_or_none(parsed_pdf.get("source_asset_id")),
                        parser_status=str(parsed_pdf.get("parser_status") or "parsed"),
                        review_status=(review_status or "").strip() or None,
                        version_label=(version_label or "").strip() or None,
                        effective_date=_normalize_sdb_effective_date_for_storage(effective_date),
                        document_title=(document_title or "").strip() or None,
                        issuer_name=(issuer_name or "").strip() or None,
                        issuer_address_line1=(issuer_address_line1 or "").strip() or None,
                        issuer_address_line2=(issuer_address_line2 or "").strip() or None,
                        issuer_postal_code=(issuer_postal_code or "").strip() or None,
                        issuer_city=(issuer_city or "").strip() or None,
                        issuer_country_code=(issuer_country_code or "").strip() or None,
                        issuer_phone=(issuer_phone or "").strip() or None,
                        issuer_email=(issuer_email or "").strip() or None,
                        action_log_json=action_log,
                        raw_text=str(parsed_pdf.get("raw_text") or "").strip() or None,
                        sections_json=parsed_pdf.get("sections_json") or {},
                        generated_pdf_path=None,
                    ),
                )
                product.sds_url = normalized_source_url
                product.sds_asset_id = _int_or_none(parsed_pdf.get("source_asset_id"))
                product.sds_available = True
                session.flush()
                return (
                    f"SDB aus Quell-PDF für Produkt {product_id} neu übernommen.",
                    (refresh_token or 0) + 1,
                    (sdb_refresh_token or 0) + 1,
                    _int_or_none(parsed_pdf.get("source_asset_id")),
                    f"Parser-Status: {parsed_pdf.get('parser_status') or 'parsed'}",
                    str(parsed_pdf.get("raw_text") or ""),
                    section_values,
                    html.Div("Noch kein generiertes SDB-PDF.", style={"color": "#64748b"}),
                    get_product_sdb(session, int(product_id)).get("action_log_json") or [],
                )

            result = run_product_chemical_enrichment(session, int(product_id), [normalized_source_url])
            current_sdb = get_product_sdb(session, int(product_id))
            upsert_product_sdb(
                session,
                int(product_id),
                ProductSDBUpdate(
                    source_url=normalized_source_url,
                    pdf_url=(pdf_url or "").strip() or None,
                    source_asset_id=current_sdb.get("source_asset_id"),
                    parser_status=current_sdb.get("parser_status"),
                    review_status=current_sdb.get("review_status"),
                    version_label=current_sdb.get("version_label"),
                    effective_date=current_sdb.get("effective_date"),
                    document_title=(document_title or current_sdb.get("document_title") or "").strip() or None,
                    issuer_name=current_sdb.get("issuer_name"),
                    issuer_address_line1=current_sdb.get("issuer_address_line1"),
                    issuer_address_line2=current_sdb.get("issuer_address_line2"),
                    issuer_postal_code=current_sdb.get("issuer_postal_code"),
                    issuer_city=current_sdb.get("issuer_city"),
                    issuer_country_code=current_sdb.get("issuer_country_code"),
                    issuer_phone=current_sdb.get("issuer_phone"),
                    issuer_email=current_sdb.get("issuer_email"),
                    action_log_json=_append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "ok", f"HTML-/Referenzquelle wurde verarbeitet. Status: {result.get('status') or '-'} · Dokumente: {len(result.get('documents') or [])}"),
                    raw_text=current_sdb.get("raw_text"),
                    sections_json=current_sdb.get("sections_json") or {},
                    generated_pdf_path=current_sdb.get("generated_pdf_path"),
                ),
            )
            session.flush()
            return (
                f"Quelle für Produkt {product_id} neu übernommen. Status: {result.get('status') or '-'} · Dokumente: {len(result.get('documents') or [])}",
                (refresh_token or 0) + 1,
                (sdb_refresh_token or 0) + 1,
                no_update,
                no_update,
                no_update,
                _sdb_section_no_update(),
                no_update,
                get_product_sdb(session, int(product_id)).get("action_log_json") or [],
            )
        except Exception as exc:
            return (
                f"Neuübernahme fehlgeschlagen: {exc}",
                no_update,
                no_update,
                no_update,
                no_update,
                no_update,
                _sdb_section_no_update(),
                no_update,
                _append_sdb_protocol(protocol_entries, "Quelle/PDF deterministisch übernehmen", "fehler", f"Deterministische Übernahme fehlgeschlagen: {exc}"),
            )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("chemistry-sdb-llm-status", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-protocol-store", "data", allow_duplicate=True),
        Input("chemistry-sdb-llm-normalize-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-sdb-refresh-token", "data"),
        State("chemistry-sdb-protocol-store", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-sdb-llm-quality-mode", "value"),
        prevent_initial_call=True,
        running=[
            (Output("chemistry-sdb-save-button", "disabled"), True, False),
            (Output("chemistry-sdb-clear-button", "disabled"), True, False),
            (Output("chemistry-sdb-import-from-source-button", "disabled"), True, False),
            (Output("chemistry-sdb-llm-normalize-button", "disabled"), True, False),
            (Output("chemistry-sdb-generate-pdf-button", "disabled"), True, False),
        ],
    )
    @_with_session
    def normalize_chemical_sdb_llm_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sdb_refresh_token: int,
        protocol_entries: list[dict] | None,
        product_id: int | None,
        quality_mode: str | None,
    ):
        if not product_id:
            status = _render_chemical_internet_status("error", "Keine SDB-KI-Normierung gestartet.", details=["Kein Chemieprodukt ausgewählt."])
            return "Kein Chemieprodukt ausgewählt.", status, no_update, no_update, no_update
        try:
            result = run_product_sdb_llm_normalization(session, int(product_id), quality_mode=quality_mode)
        except Exception as exc:
            status = _render_chemical_internet_status(
                "error",
                "SDB-KI-Normierung fehlgeschlagen.",
                details=[str(exc), "Die Buttons sind wieder freigegeben. Prüfe OpenAI-Konfiguration, Rohtext und Protokoll."],
            )
            return (
                f"LLM-Normierung fehlgeschlagen: {exc}",
                status,
                no_update,
                no_update,
                _append_sdb_protocol(
                    protocol_entries,
                    "SDB mit ChatGPT normieren (Fallback)",
                    "fehler",
                    f"LLM-Fallback konnte nicht ausgeführt werden: {exc}",
                ),
            )
        warning_count = len(result.get("warnings") or [])
        suffix = f" · Warnungen: {warning_count}" if warning_count else ""
        outcome = "ok" if result.get("status") == "completed" else "hinweis" if result.get("status") == "missing_api_key" else "fehler"
        status_kind = "success" if result.get("status") == "completed" else "error" if outcome == "fehler" else "info"
        status_message = _render_chemical_internet_status(
            status_kind,
            "SDB-KI-Normierung abgeschlossen." if result.get("status") == "completed" else "SDB-KI-Normierung nicht vollständig abgeschlossen.",
            details=[
                f"Status: {result.get('status') or '-'}",
                f"Modell: {result.get('model') or '-'}",
                f"Qualität: {result.get('quality_mode') or quality_mode or '-'}",
                f"Reasoning: {result.get('reasoning_effort') or '-'}",
                f"Fokuslauf 13-16: {'ja' if result.get('focused_sections_applied') else 'nein'}",
                f"Warnungen: {warning_count}",
                str(result.get("message") or "").strip() or "Ergebnis wurde ins SDB-Protokoll geschrieben.",
            ],
        )
        current_sdb = get_product_sdb(session, int(product_id))
        upsert_product_sdb(
            session,
            int(product_id),
            ProductSDBUpdate(
                source_url=current_sdb.get("source_url"),
                pdf_url=current_sdb.get("pdf_url"),
                source_asset_id=current_sdb.get("source_asset_id"),
                parser_status=current_sdb.get("parser_status"),
                review_status=current_sdb.get("review_status"),
                version_label=current_sdb.get("version_label"),
                effective_date=current_sdb.get("effective_date"),
                document_title=current_sdb.get("document_title"),
                issuer_name=current_sdb.get("issuer_name"),
                issuer_address_line1=current_sdb.get("issuer_address_line1"),
                issuer_address_line2=current_sdb.get("issuer_address_line2"),
                issuer_postal_code=current_sdb.get("issuer_postal_code"),
                issuer_city=current_sdb.get("issuer_city"),
                issuer_country_code=current_sdb.get("issuer_country_code"),
                issuer_phone=current_sdb.get("issuer_phone"),
                issuer_email=current_sdb.get("issuer_email"),
                action_log_json=_append_sdb_protocol(
                    protocol_entries,
                    "SDB mit ChatGPT normieren (Fallback)",
                    outcome,
                    f"LLM-Fallback ausgeführt. Status: {result.get('status') or '-'} · Modell: {result.get('model') or '-'}"
                    + (f" · Qualität: {result.get('quality_mode') or quality_mode or '-'}" if result.get("quality_mode") or quality_mode else "")
                    + (f" · Reasoning: {result.get('reasoning_effort') or '-'}" if result.get("reasoning_effort") else "")
                    + (" · Fokuslauf 13-16" if result.get("focused_sections_applied") else "")
                    + (f" · Warnungen: {warning_count}" if warning_count else ""),
                ),
                raw_text=current_sdb.get("raw_text"),
                sections_json=current_sdb.get("sections_json") or {},
                generated_pdf_path=current_sdb.get("generated_pdf_path"),
            ),
        )
        return (
            f"{result.get('message') or 'SDB normalisiert.'} · Status: {result.get('status') or '-'} · Modell: {result.get('model') or '-'} · Qualität: {result.get('quality_mode') or quality_mode or '-'}{suffix}",
            status_message,
            (refresh_token or 0) + 1,
            (sdb_refresh_token or 0) + 1,
            get_product_sdb(session, int(product_id)).get("action_log_json") or [],
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-refresh-token", "data", allow_duplicate=True),
        Output("chemistry-sdb-protocol-store", "data", allow_duplicate=True),
        Input("chemistry-sdb-generate-pdf-button", "n_clicks"),
        State("refresh-token", "data"),
        State("chemistry-sdb-refresh-token", "data"),
        State("chemistry-sdb-protocol-store", "data"),
        State("chemistry-product-id", "value"),
        State("chemistry-product-title", "value"),
        State("chemistry-product-sku", "value"),
        State("chemistry-product-brand", "value"),
        State("chemistry-cas-number", "value"),
        State("chemistry-ec-number", "value"),
        State("chemistry-un-number", "value"),
        State("chemistry-signal-word", "value"),
        State("chemistry-ghs-pictograms", "value"),
        State("chemistry-sdb-source-url", "value"),
        State("chemistry-sdb-pdf-url", "value"),
        State("chemistry-sdb-source-asset-id", "value"),
        State("chemistry-sdb-review-status", "value"),
        State("chemistry-sdb-version-label", "value"),
        State("chemistry-sdb-effective-date", "value"),
        State("chemistry-sdb-document-title", "value"),
        State("chemistry-sdb-issuer-name", "value"),
        State("chemistry-sdb-issuer-address-line1", "value"),
        State("chemistry-sdb-issuer-address-line2", "value"),
        State("chemistry-sdb-issuer-postal-code", "value"),
        State("chemistry-sdb-issuer-city", "value"),
        State("chemistry-sdb-issuer-country-code", "value"),
        State("chemistry-sdb-issuer-phone", "value"),
        State("chemistry-sdb-issuer-email", "value"),
        State("chemistry-sdb-raw-text", "value"),
        State({"type": "chemistry-sdb-section", "index": ALL}, "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def generate_chemical_sdb_pdf_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sdb_refresh_token: int,
        protocol_entries: list[dict] | None,
        product_id: int | None,
        product_title: str | None,
        sku: str | None,
        brand_name: str | None,
        cas_number: str | None,
        ec_number: str | None,
        un_number: str | None,
        signal_word: str | None,
        ghs_pictograms: list[str] | str | None,
        source_url: str | None,
        pdf_url: str | None,
        source_asset_id: int | None,
        review_status: str | None,
        version_label: str | None,
        effective_date: str | None,
        document_title: str | None,
        issuer_name: str | None,
        issuer_address_line1: str | None,
        issuer_address_line2: str | None,
        issuer_postal_code: str | None,
        issuer_city: str | None,
        issuer_country_code: str | None,
        issuer_phone: str | None,
        issuer_email: str | None,
        raw_text: str | None,
        section_values: list[str] | None,
    ):
        if not product_id or not sku:
            return "Kein Chemieprodukt ausgewählt.", no_update, no_update, no_update
        stored_sdb = get_product_sdb(session, int(product_id))
        product_detail = get_product_detail(session, int(product_id)) or {}
        product_context = {
            "product_name": product_title or sku,
            "product_title": product_title or sku,
            "title": product_title or sku,
            "sku": sku,
            "un_number": un_number,
            "hazard_class": product_detail.get("hazard_class"),
            "packing_group": product_detail.get("packing_group"),
            "hazard_shipping_note": product_detail.get("hazard_shipping_note"),
            "ufi": product_detail.get("ufi"),
            "voc_content_percent": product_detail.get("voc_content_percent"),
            "density": product_detail.get("density"),
            "color": product_detail.get("color"),
            "odor": product_detail.get("odor"),
            "ph_value": product_detail.get("ph_value"),
            "flash_point": product_detail.get("flash_point"),
            "boiling_point": product_detail.get("boiling_point"),
            "viscosity": product_detail.get("viscosity"),
            "solubility": product_detail.get("solubility"),
        }
        stored_sections_json = stored_sdb.get("sections_json") or {}
        sections_json = _merge_sdb_ui_sections(
            stored_sections_json,
            section_values,
            issuer_name=(issuer_name or stored_sdb.get("issuer_name") or "").strip() or None,
            issuer_address_line1=(issuer_address_line1 or stored_sdb.get("issuer_address_line1") or "").strip() or None,
            issuer_address_line2=(issuer_address_line2 or stored_sdb.get("issuer_address_line2") or "").strip() or None,
            issuer_postal_code=(issuer_postal_code or stored_sdb.get("issuer_postal_code") or "").strip() or None,
            issuer_city=(issuer_city or stored_sdb.get("issuer_city") or "").strip() or None,
            issuer_country_code=(issuer_country_code or stored_sdb.get("issuer_country_code") or "").strip() or None,
            issuer_phone=(issuer_phone or stored_sdb.get("issuer_phone") or "").strip() or None,
            issuer_email=(issuer_email or stored_sdb.get("issuer_email") or "").strip() or None,
            product_context=product_context,
        )
        effective_source_url = (source_url or stored_sdb.get("source_url") or "").strip() or None
        effective_pdf_url = (pdf_url or stored_sdb.get("pdf_url") or "").strip() or None
        effective_source_asset_id = _int_or_none(source_asset_id) or _int_or_none(stored_sdb.get("source_asset_id"))
        effective_raw_text = (raw_text or stored_sdb.get("raw_text") or "").strip() or None
        effective_review_status = (review_status or stored_sdb.get("review_status") or "").strip() or None
        effective_version_label = (version_label or stored_sdb.get("version_label") or "").strip() or None
        effective_effective_date = _normalize_sdb_effective_date_for_storage(effective_date) or (stored_sdb.get("effective_date") or "").strip() or None
        effective_document_title = (document_title or stored_sdb.get("document_title") or product_title or sku or "").strip() or None
        effective_issuer_name = (issuer_name or stored_sdb.get("issuer_name") or "").strip() or None
        effective_issuer_address_line1 = (issuer_address_line1 or stored_sdb.get("issuer_address_line1") or "").strip() or None
        effective_issuer_address_line2 = (issuer_address_line2 or stored_sdb.get("issuer_address_line2") or "").strip() or None
        effective_issuer_postal_code = (issuer_postal_code or stored_sdb.get("issuer_postal_code") or "").strip() or None
        effective_issuer_city = (issuer_city or stored_sdb.get("issuer_city") or "").strip() or None
        effective_issuer_country_code = (issuer_country_code or stored_sdb.get("issuer_country_code") or "").strip() or None
        effective_issuer_phone = (issuer_phone or stored_sdb.get("issuer_phone") or "").strip() or None
        effective_issuer_email = (issuer_email or stored_sdb.get("issuer_email") or "").strip() or None
        validation = validate_sdb_sections(
            sections_json,
            review_status=effective_review_status,
            issuer_name=effective_issuer_name,
            issuer_address_line1=effective_issuer_address_line1,
            issuer_address_line2=effective_issuer_address_line2,
            issuer_postal_code=effective_issuer_postal_code,
            issuer_city=effective_issuer_city,
            issuer_country_code=effective_issuer_country_code,
            issuer_phone=effective_issuer_phone,
            issuer_email=effective_issuer_email,
            product_context=product_context,
        )
        if not validation["is_valid"]:
            upsert_product_sdb(
                session,
                int(product_id),
                ProductSDBUpdate(
                    source_url=effective_source_url,
                    pdf_url=effective_pdf_url,
                    source_asset_id=effective_source_asset_id,
                    parser_status=stored_sdb.get("parser_status") or "manual",
                    review_status=effective_review_status,
                    version_label=effective_version_label,
                    effective_date=effective_effective_date,
                    document_title=effective_document_title,
                    issuer_name=effective_issuer_name,
                    issuer_address_line1=effective_issuer_address_line1,
                    issuer_address_line2=effective_issuer_address_line2,
                    issuer_postal_code=effective_issuer_postal_code,
                    issuer_city=effective_issuer_city,
                    issuer_country_code=effective_issuer_country_code,
                    issuer_phone=effective_issuer_phone,
                    issuer_email=effective_issuer_email,
                    action_log_json=_append_sdb_protocol(
                        protocol_entries,
                        "SDB deterministisch validieren + PDF generieren",
                        "fehler",
                        "Validierung vor PDF-Generierung fehlgeschlagen: " + " | ".join(validation["errors"]),
                    ),
                    raw_text=effective_raw_text,
                    sections_json=sections_json,
                    generated_pdf_path=stored_sdb.get("generated_pdf_path"),
                ),
            )
            return (
                "SDB-Validierung fehlgeschlagen: " + " | ".join(validation["errors"]),
                no_update,
                no_update,
                get_product_sdb(session, int(product_id)).get("action_log_json") or [],
            )
        render_sections = validation["sections"]
        render_sections = prepare_sdb_sections_for_render(
            render_sections,
            review_status=effective_review_status,
            issuer_name=effective_issuer_name,
            issuer_address_line1=effective_issuer_address_line1,
            issuer_address_line2=effective_issuer_address_line2,
            issuer_postal_code=effective_issuer_postal_code,
            issuer_city=effective_issuer_city,
            issuer_country_code=effective_issuer_country_code,
            issuer_phone=effective_issuer_phone,
            issuer_email=effective_issuer_email,
            product_context=product_context,
        )
        output_path = get_pim_settings().asset_storage_root / "generated_sdb" / f"product-{product_id}-sdb.pdf"
        render_sdb_pdf(
            document_title=effective_document_title,
            product_title=product_title or sku,
            brand_name=brand_name,
            sku=sku,
            cas_number=cas_number,
            ec_number=ec_number,
            un_number=un_number,
            signal_word=signal_word,
            ghs_pictograms="|".join(ghs_pictograms or []) if isinstance(ghs_pictograms, list) else (ghs_pictograms or None),
            review_status=effective_review_status,
            version_label=effective_version_label,
            effective_date=effective_effective_date,
            issuer_name=effective_issuer_name,
            issuer_address_line1=effective_issuer_address_line1,
            issuer_address_line2=effective_issuer_address_line2,
            issuer_postal_code=effective_issuer_postal_code,
            issuer_city=effective_issuer_city,
            issuer_country_code=effective_issuer_country_code,
            sections=render_sections,
            output_path=output_path,
        )
        upsert_product_sdb(
            session,
            int(product_id),
            ProductSDBUpdate(
                source_url=effective_source_url,
                pdf_url=effective_pdf_url,
                source_asset_id=effective_source_asset_id,
                parser_status=stored_sdb.get("parser_status") or "manual",
                review_status=effective_review_status,
                version_label=effective_version_label,
                effective_date=effective_effective_date,
                document_title=effective_document_title,
                issuer_name=effective_issuer_name,
                issuer_address_line1=effective_issuer_address_line1,
                issuer_address_line2=effective_issuer_address_line2,
                issuer_postal_code=effective_issuer_postal_code,
                issuer_city=effective_issuer_city,
                issuer_country_code=effective_issuer_country_code,
                issuer_phone=effective_issuer_phone,
                issuer_email=effective_issuer_email,
                action_log_json=_append_sdb_protocol(
                    protocol_entries,
                    "SDB deterministisch validieren + PDF generieren",
                    "ok",
                    f"Validierung erfolgreich. PDF wurde deterministisch erzeugt unter {output_path}.",
                ),
                raw_text=effective_raw_text,
                sections_json=render_sections,
                generated_pdf_path=str(output_path),
            ),
        )
        working_document = sync_product_sdb_working_document(session, int(product_id))
        document_suffix = f" · SDB-Version ID {working_document.get('id')}" if working_document else ""
        return (
            f"SDB-PDF für Produkt {product_id} generiert{document_suffix}.",
            (refresh_token or 0) + 1,
            (sdb_refresh_token or 0) + 1,
            get_product_sdb(session, int(product_id)).get("action_log_json") or [],
        )

    @app.callback(
        Output("main-tabs", "value", allow_duplicate=True),
        Output("last-product-clicked-id", "data", allow_duplicate=True),
        Input("chemistry-open-product-button", "n_clicks"),
        State("chemistry-product-id", "value"),
        prevent_initial_call=True,
    )
    def open_product_from_chemistry(_: int | None, product_id: int | None):
        if not product_id:
            return no_update, no_update
        return "products", int(product_id)

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("asset-move-up-button", "n_clicks"),
        Input("asset-move-down-button", "n_clicks"),
        Input("asset-delete-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-detail-assets", "selectedRows"),
        prevent_initial_call=True,
    )
    @_with_session
    def manage_asset_callback(
        session: Session,
        _: int | None,
        __: int | None,
        ___: int | None,
        refresh_token: int,
        selected_rows: list[dict] | None,
    ):
        if not selected_rows:
            return "Kein Asset ausgewählt.", no_update
        asset_id = selected_rows[0].get("id")
        if asset_id is None:
            return "Kein Asset ausgewählt.", no_update
        if ctx.triggered_id == "asset-delete-button":
            delete_asset(session, int(asset_id))
            return f"Asset {asset_id} gelöscht.", (refresh_token or 0) + 1
        if ctx.triggered_id == "asset-move-up-button":
            move_asset(session, int(asset_id), "up")
            return f"Asset {asset_id} nach oben verschoben.", (refresh_token or 0) + 1
        if ctx.triggered_id == "asset-move-down-button":
            move_asset(session, int(asset_id), "down")
            return f"Asset {asset_id} nach unten verschoben.", (refresh_token or 0) + 1
        return no_update, no_update

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input({"type": "asset-delete-direct", "asset_id": ALL}, "n_clicks"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_asset_direct_callback(
        session: Session,
        clicks: list[int] | None,
        refresh_token: int,
    ):
        triggered = ctx.triggered_id
        if not isinstance(triggered, dict):
            return no_update, no_update
        if not clicks or not any(clicks):
            return no_update, no_update
        asset_id = triggered.get("asset_id")
        if asset_id is None:
            return no_update, no_update
        delete_asset(session, int(asset_id))
        return f"Asset {asset_id} gelöscht.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-create-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-sku", "value"),
        State("product-title", "value"),
        State("product-brand", "value"),
        State("product-status", "value"),
        State("product-source-language", "value"),
        State("product-is-chemical", "value"),
        State("product-short-description", "value"),
        State("product-description", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def create_product_callback(
        session: Session,
        _: int,
        refresh_token: int,
        sku: str | None,
        title: str | None,
        brand_name: str | None,
        status: str | None,
        source_language: str | None,
        is_chemical: bool | None,
        short_description: str | None,
        description: str | None,
    ):
        if not sku or not title:
            return "SKU und Titel sind Pflicht.", no_update
        product, _variant = create_product(
            session,
            ProductCreate(
                sku=sku,
                title=title,
                brand_name=brand_name,
                status=status or "draft",
                source_language=(source_language or "en").strip(),
                description=description,
                is_chemical=bool(is_chemical),
            ),
            VariantCreate(sku=sku, variant_title=title),
        )
        if short_description:
            set_product_translation_short_description(
                session,
                product.id,
                (source_language or "en").strip(),
                title,
                short_description,
            )
        return f"Produkt {product.sku} angelegt.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-id", "value"),
        State("product-sku", "value"),
        State("product-title", "value"),
        State("product-brand", "value"),
        State("product-status", "value"),
        State("product-source-language", "value"),
        State("product-is-chemical", "value"),
        State("product-category-channel-code", "value"),
        State("product-categories", "value"),
        State("product-short-description", "value"),
        State("product-description", "value"),
        State("product-source-url", "value"),
        State("product-source-url-final", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_product_callback(
        session: Session,
        _: int,
        refresh_token: int,
        product_id: int | None,
        sku: str | None,
        title: str | None,
        brand_name: str | None,
        status: str | None,
        source_language: str | None,
        is_chemical: bool | None,
        category_channel_code: str | None,
        category_ids: list[int] | None,
        short_description: str | None,
        description: str | None,
        source_url: str | None,
        source_url_final: str | None,
    ):
        if not product_id or not title or not status:
            return "Produkt-ID, Titel und Status sind Pflicht.", no_update
        try:
            update_product(
                session,
                product_id,
                ProductUpdate(
                    sku=sku,
                    title=title,
                    description=description,
                    source_url=(source_url or "").strip() or None,
                    source_url_final=(source_url_final or "").strip() or None,
                    brand_name=brand_name,
                    status=status,
                    source_language=(source_language or "en").strip(),
                    is_chemical=bool(is_chemical),
                    category_channel_code=category_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
                    category_ids=category_ids or [],
                ),
            )
            set_product_translation_short_description(
                session,
                int(product_id),
                (source_language or "en").strip(),
                title,
                short_description,
            )
        except ValueError as exc:
            return str(exc), no_update
        return (
            f"Produkt {product_id} gespeichert. Kategorien für {(category_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)} aktualisiert.",
            (refresh_token or 0) + 1,
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("products-grid", "cellValueChanged"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_product_grid_edit_callback(
        session: Session,
        event: dict | None,
        refresh_token: int,
    ):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        product_id = row.get("id")
        if not product_id:
            return no_update, no_update
        detail = get_product_detail(session, int(product_id))
        if detail is None:
            return "Produkt nicht gefunden.", no_update
        update_product(
            session,
            int(product_id),
            ProductUpdate(
                title=(row.get("title") or detail["title"] or "").strip(),
                description=detail.get("description"),
                brand_name=row.get("brand") or detail.get("brand_name"),
                status=row.get("status") or detail.get("status") or "draft",
                source_language=(row.get("source_language") or detail.get("source_language") or "en").strip(),
                category_channel_code=detail.get("category_channel_code") or DEFAULT_CATEGORY_CHANNEL_CODE,
                category_ids=detail.get("category_ids") or [],
            ),
        )
        return f"Produkt {product_id} direkt gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-archive-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-id", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def archive_product_callback(session: Session, _: int, refresh_token: int, product_id: int | None):
        if not product_id:
            return "Kein Produkt gewählt.", no_update
        archive_product(session, product_id)
        return f"Produkt {product_id} archiviert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("product-detail-variants", "selectedRows", allow_duplicate=True),
        Input("product-detail-variant-archive-button", "n_clicks"),
        State("product-id", "value"),
        State("product-detail-variants", "selectedRows"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def archive_product_detail_variants_callback(
        session: Session,
        n_clicks: int | None,
        product_id: int | None,
        selected_rows: list[dict] | None,
        refresh_token: int | None,
    ):
        if not n_clicks:
            return no_update, no_update, no_update
        ids = [int(row.get("id")) for row in (selected_rows or []) if row.get("id") is not None]
        if not product_id:
            return "Kein Produkt gewählt.", no_update, no_update
        if not ids:
            return "Keine Variante im Produktdetail ausgewählt.", no_update, no_update
        count = archive_variants(session, ids)
        return f"{count} Variante(n) für Produkt {product_id} archiviert.", (refresh_token or 0) + 1, []

    @app.callback(
        Output("product-detail-variant-delete-confirm", "displayed"),
        Input("product-detail-variant-delete-button", "n_clicks"),
        State("product-detail-variants", "selectedRows"),
        prevent_initial_call=True,
    )
    def ask_product_detail_variant_delete_confirm(n_clicks: int | None, selected_rows: list[dict] | None):
        if not n_clicks or not selected_rows:
            return False
        return True

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("product-detail-variants", "selectedRows", allow_duplicate=True),
        Input("product-detail-variant-delete-confirm", "submit_n_clicks"),
        State("product-id", "value"),
        State("product-detail-variants", "selectedRows"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_product_detail_variants_callback(
        session: Session,
        submit_n_clicks: int | None,
        product_id: int | None,
        selected_rows: list[dict] | None,
        refresh_token: int | None,
    ):
        if not submit_n_clicks:
            return no_update, no_update, no_update
        ids = [int(row.get("id")) for row in (selected_rows or []) if row.get("id") is not None]
        if not product_id:
            return "Kein Produkt gewählt.", no_update, no_update
        if not ids:
            return "Keine Variante im Produktdetail ausgewählt.", no_update, no_update
        result = delete_or_archive_variants(session, ids)
        deleted = result.get("deleted", 0)
        archived = result.get("archived_due_to_relations", 0)
        if archived and not deleted:
            message = f"{archived} Variante(n) für Produkt {product_id} wegen abhängiger Daten archiviert."
        elif archived:
            message = f"{deleted} Variante(n) gelöscht, {archived} wegen abhängiger Daten archiviert."
        else:
            message = f"{deleted} Variante(n) gelöscht."
        return message, (refresh_token or 0) + 1, []

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("sales-channel-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("sales-channel-form-id", "value"),
        State("sales-channel-form-code", "value"),
        State("sales-channel-form-name", "value"),
        State("sales-channel-form-is-active", "value"),
        State("sales-channel-form-sort-order", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_sales_channel_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        channel_id: int | None,
        code: str | None,
        name: str | None,
        is_active: bool | None,
        sort_order: int | None,
    ):
        if not name:
            return "Name des Vertriebskanals ist Pflicht.", no_update
        if channel_id:
            create_or_update_sales_channel(
                session,
                SalesChannelUpdate(name=name, is_active=bool(is_active), sort_order=_int_or_zero(sort_order)),
                channel_id=int(channel_id),
            )
            return f"Vertriebskanal {channel_id} gespeichert.", (refresh_token or 0) + 1
        if not code:
            return "Code des Vertriebskanals ist Pflicht.", no_update
        channel = create_or_update_sales_channel(
            session,
            SalesChannelCreate(code=code, name=name, is_active=bool(is_active), sort_order=_int_or_zero(sort_order)),
        )
        return f"Vertriebskanal {channel.code} angelegt.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("channel-export-result", "children"),
        Input("channel-export-run-button", "n_clicks"),
        State("channel-export-code", "value"),
        State("channel-export-language", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_channel_export_callback(
        session: Session,
        _: int | None,
        sales_channel_code: str | None,
        language_code: str | None,
    ):
        if not sales_channel_code:
            return "Vertriebskanal für Export wählen.", no_update
        export_root = get_pim_settings().asset_storage_root.parent / "channel_exports"
        try:
            result = export_channel_rows(
                session,
                sales_channel_code=sales_channel_code,
                language_code=(language_code or "").strip() or None,
                output_dir=export_root,
            )
        except Exception as exc:
            return f"Kanal-Export fehlgeschlagen: {exc}", no_update
        link = html.A(
            f"{result['filename']} öffnen",
            href=f"/channel-export-file/{result['filename']}",
            target="_blank",
        )
        summary = html.Div(
            [
                html.Div(f"Kanal: {result['sales_channel_code']} · Sprache: {result.get('language_code') or '-'}"),
                html.Div(f"Exportierte Zeilen: {result['row_count']}"),
                html.Div(link, style={"marginTop": "6px"}),
            ]
        )
        return f"Kanal-Export {result['filename']} erzeugt.", summary

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("sales-channels-grid", "cellValueChanged"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_sales_channel_grid_callback(session: Session, event: dict | None, refresh_token: int):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        channel_id = row.get("id")
        if not channel_id:
            return no_update, no_update
        create_or_update_sales_channel(
            session,
            SalesChannelUpdate(
                name=row.get("name"),
                is_active=bool(row.get("is_active")),
                sort_order=_int_or_zero(row.get("sort_order")),
            ),
            channel_id=int(channel_id),
        )
        return f"Vertriebskanal {channel_id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("channel-category-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("channel-category-form-sales-channel-id", "value"),
        State("channel-category-form-external-id", "value"),
        State("channel-category-form-name", "value"),
        State("channel-category-form-path", "value"),
        State("channel-category-form-required-attributes", "value"),
        State("channel-category-form-is-active", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_channel_category_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        sales_channel_id: int | None,
        external_id: str | None,
        name: str | None,
        external_path: str | None,
        required_attributes: str | None,
        is_active: bool | None,
    ):
        if not sales_channel_id or not external_id or not name:
            return "Kanal, externe Kategorie-ID und Name sind Pflicht.", no_update
        parsed_required = []
        text = (required_attributes or "").strip()
        if text:
            try:
                import json
                parsed_required = json.loads(text)
            except Exception:
                return "Pflichtattribute JSON ist ungültig.", no_update
        row = upsert_channel_category(
            session,
            ChannelCategoryUpsert(
                sales_channel_id=int(sales_channel_id),
                external_category_id=external_id,
                external_path=external_path,
                name=name,
                required_attributes_json=parsed_required,
                is_active=bool(is_active),
            ),
        )
        return f"Kanal-Kategorie {row.external_category_id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-detail-channel-listings", "cellValueChanged"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_product_channel_listing_callback(session: Session, event: dict | None, refresh_token: int):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        product_id = event.get("context", {}).get("product_id") or row.get("product_id")
        if not product_id:
            product_id = None
        current_product_id = row.get("product_id")
        if current_product_id:
            product_id = current_product_id
        if not product_id:
            return no_update, no_update
        upsert_product_channel_listing(
            session,
            ProductChannelListingUpdate(
                product_id=int(product_id),
                sales_channel_id=int(row.get("sales_channel_id")),
                allowed=bool(row.get("allowed")),
                is_active=bool(row.get("is_active")),
                active_from=row.get("active_from"),
                active_until=row.get("active_until"),
                publication_status=row.get("publication_status") or "draft",
            ),
        )
        return "Produkt-Listing gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-channel-mapping-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-id", "value"),
        State("product-channel-mapping-sales-channel-id", "value"),
        State("product-channel-mapping-channel-category-id", "value"),
        State("product-channel-mapping-is-primary", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_product_channel_mapping_callback(
        session: Session,
        _: int | None,
        refresh_token: int,
        product_id: int | None,
        sales_channel_id: int | None,
        channel_category_id: int | None,
        is_primary: bool | None,
    ):
        if not product_id or not sales_channel_id or not channel_category_id:
            return "Produkt, Vertriebskanal und Kanal-Kategorie sind Pflicht.", no_update
        upsert_product_category_mapping(
            session,
            ProductCategoryMappingUpsert(
                product_id=int(product_id),
                sales_channel_id=int(sales_channel_id),
                channel_category_id=int(channel_category_id),
                is_primary=bool(is_primary),
            ),
        )
        return "Kanal-Kategorie-Mapping gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-detail-variant-channel-listings", "cellValueChanged"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_variant_channel_listing_callback(session: Session, event: dict | None, refresh_token: int):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        variant_id = row.get("variant_id")
        sales_channel_id = row.get("sales_channel_id")
        if not variant_id or not sales_channel_id:
            return no_update, no_update
        upsert_variant_channel_listing(
            session,
            VariantChannelListingUpdate(
                variant_id=int(variant_id),
                sales_channel_id=int(sales_channel_id),
                allowed=bool(row.get("allowed")),
                is_active=bool(row.get("is_active")),
                publication_status=row.get("publication_status") or "draft",
                price_enabled=bool(row.get("price_enabled")),
                shippable=bool(row.get("shippable")),
                hazardous_goods=bool(row.get("hazardous_goods")),
                limited_quantity=row.get("limited_quantity"),
                channel_sku=row.get("channel_sku"),
                channel_ean=row.get("channel_ean"),
            ),
        )
        return "Varianten-Listing gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("variant-id", "value"),
        Output("variant-title", "value"),
        Output("variant-option-name", "value"),
        Output("variant-option-value", "value"),
        Output("variant-packaging", "value"),
        Output("variant-price", "value"),
        Output("variant-cost-price", "value"),
        Output("variant-currency", "value"),
        Output("variant-cost-currency", "value"),
        Output("variant-stock", "value"),
        Output("variant-barcode", "value"),
        Output("variant-tier-grid", "rowData"),
        Input("variants-grid", "selectedRows"),
        Input("refresh-token", "data"),
        State("variants-grid", "rowData"),
    )
    def select_variant(selected_rows: list[dict] | None, _: int | None, row_data: list[dict] | None):
        if not selected_rows:
            return None, None, None, None, None, None, None, None, None, None, None, []
        selected_id = selected_rows[0].get("id")
        current_rows = row_data or []
        row = next((item for item in current_rows if item.get("id") == selected_id), selected_rows[0])
        return (
            row["id"],
            row["variant_title"],
            row.get("option_name"),
            row.get("option_value"),
            row.get("packaging"),
            row["price"],
            row.get("cost_price"),
            row["currency"],
            row.get("cost_currency"),
            row["stock_qty"],
            row["barcode"],
            [{**tier, "delete_action": "Löschen"} for tier in row.get("price_tiers", [])],
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variant-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("variant-id", "value"),
        State("variant-title", "value"),
        State("variant-option-name", "value"),
        State("variant-option-value", "value"),
        State("variant-packaging", "value"),
        State("variant-price", "value"),
        State("variant-cost-price", "value"),
        State("variant-currency", "value"),
        State("variant-cost-currency", "value"),
        State("variant-stock", "value"),
        State("variant-barcode", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_variant_callback(
        session: Session,
        _: int,
        refresh_token: int,
        variant_id: int | None,
        title: str | None,
        option_name: str | None,
        option_value: str | None,
        packaging: str | None,
        price: float | None,
        cost_price: float | None,
        currency: str | None,
        cost_currency: str | None,
        stock_qty: int | None,
        barcode: str | None,
    ):
        if not variant_id:
            return "Keine Variante gewählt.", no_update
        update_variant(
            session,
            variant_id,
            VariantUpdate(
                variant_title=title,
                option_name=option_name,
                option_value=option_value,
                packaging=packaging,
                price=_float_or_none(price),
                cost_price=_float_or_none(cost_price),
                currency=currency,
                cost_currency=cost_currency,
                stock_qty=_int_or_zero(stock_qty),
                barcode=barcode,
                status=None,
            ),
        )
        return f"Variante {variant_id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variants-grid", "cellValueChanged"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_variant_grid_edit_callback(
        session: Session,
        event: dict | None,
        refresh_token: int,
    ):
        if not event:
            return no_update, no_update
        row = event.get("data") or {}
        variant_id = row.get("id")
        if not variant_id:
            return no_update, no_update
        update_variant(
            session,
            int(variant_id),
            VariantUpdate(
                variant_title=row.get("variant_title"),
                option_name=row.get("option_name"),
                option_value=row.get("option_value"),
                packaging=row.get("packaging"),
                price=_float_or_none(row.get("price")),
                cost_price=_float_or_none(row.get("cost_price")),
                currency=row.get("currency"),
                cost_currency=row.get("cost_currency"),
                stock_qty=_int_or_zero(row.get("stock_qty")),
                barcode=row.get("barcode"),
                status=row.get("status"),
            ),
        )
        return f"Variante {variant_id} direkt gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("tier-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("variant-id", "value"),
        State("tier-price-type", "value"),
        State("tier-min-qty", "value"),
        State("tier-max-qty", "value"),
        State("tier-price", "value"),
        State("tier-currency", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_tier_callback(
        session: Session,
        _: int,
        refresh_token: int,
        variant_id: int | None,
        price_type: str | None,
        min_qty: int | None,
        max_qty: int | None,
        price: float | None,
        currency: str | None,
    ):
        if not variant_id or price is None or not currency:
            return "Variante, Preis und Währung sind Pflicht.", no_update
        upsert_variant_price_tier(
            session,
            VariantPriceTierCreate(
                variant_id=variant_id,
                price_type=price_type or "sale",
                min_qty=min_qty or 1,
                max_qty=max_qty,
                price=price,
                currency=currency,
            ),
        )
        return f"Staffelpreis für Variante {variant_id} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variant-tier-grid", "cellValueChanged"),
        State("refresh-token", "data"),
        State("variant-id", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_tier_grid_edit_callback(
        session: Session,
        event: dict | list[dict] | None,
        refresh_token: int,
        current_variant_id: int | None,
    ):
        if not event or not current_variant_id:
            return no_update, no_update
        event_payload = event[-1] if isinstance(event, list) else event
        row = event_payload.get("data") or {}
        tier_id = row.get("id")
        if not tier_id:
            return no_update, no_update
        update_variant_price_tier(
            session,
            int(tier_id),
            VariantPriceTierCreate(
                variant_id=int(current_variant_id),
                price_type=(row.get("price_type") or "sale"),
                min_qty=_int_or_zero(row.get("min_qty")) or 1,
                max_qty=_int_or_none(row.get("max_qty")),
                price=_float_or_none(row.get("price")) or 0,
                currency=(row.get("currency") or "EUR"),
            ),
        )
        return f"Staffelpreis {tier_id} direkt gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variant-tier-grid", "cellClicked"),
        State("refresh-token", "data"),
        State("variant-tier-grid", "rowData"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_tier_from_grid_callback(
        session: Session,
        event: dict | None,
        refresh_token: int,
        rows: list[dict] | None,
    ):
        if not event:
            return no_update, no_update
        if event.get("colId") != "delete_action":
            return no_update, no_update
        row_index = event.get("rowIndex")
        row_list = rows or []
        row = row_list[row_index] if isinstance(row_index, int) and 0 <= row_index < len(row_list) else {}
        tier_id = row.get("id")
        if not tier_id:
            return no_update, no_update
        delete_variant_price_tier(session, int(tier_id))
        return f"Staffelpreis {tier_id} gelöscht.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("translation-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("product-id", "value"),
        State("translation-language", "value"),
        State("translation-title", "value"),
        State("translation-short-description", "value"),
        State("translation-description", "value"),
        State("translation-seo-title", "value"),
        State("translation-seo-description", "value"),
        State("translation-slug", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_translation_callback(
        session: Session,
        _: int,
        refresh_token: int,
        product_id: int | None,
        language_code: str | None,
        title: str | None,
        short_description: str | None,
        description: str | None,
        seo_title: str | None,
        seo_description: str | None,
        slug: str | None,
    ):
        if not product_id or not language_code or not title:
            return "Produkt, Sprache und Titel sind Pflicht.", no_update
        create_or_update_translation(
            session,
            ProductTranslationCreate(
                product_id=product_id,
                language_code=language_code,
                title=title,
                short_description=short_description,
                description=description,
                seo_title=seo_title,
                seo_description=seo_description,
                slug=slug,
            ),
        )
        return f"Übersetzung {language_code} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variant-translation-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("variant-translation-id", "value"),
        State("variant-translation-variant-id", "value"),
        State("variant-translation-language", "value"),
        State("variant-translation-title", "value"),
        State("variant-translation-option-label-override", "value"),
        State("variant-translation-package-label", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_variant_translation_callback(
        session: Session,
        _: int,
        refresh_token: int,
        translation_id: int | str | None,
        variant_id: int | None,
        language_code: str | None,
        title: str | None,
        option_label_override: str | None,
        package_label: str | None,
    ):
        if not variant_id or not language_code or not title:
            return "Variante, Sprache und Titel sind Pflicht.", no_update
        payload = VariantTranslationCreate(
            variant_id=int(variant_id),
            language_code=language_code,
            title=title,
            option_label_override=option_label_override,
            package_label=package_label,
        )
        if translation_id not in (None, ""):
            update_variant_translation_by_id(session, int(translation_id), payload)
        else:
            create_or_update_variant_translation(session, payload)
        return f"Varianten-Übersetzung {language_code} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("asset-upload-status", "children"),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("asset-upload", "contents"),
        State("refresh-token", "data"),
        State("asset-upload", "filename"),
        State("product-id", "value"),
        State("product-title", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def upload_asset_callback(
        session: Session,
        contents: str | None,
        refresh_token: int,
        filename: str | None,
        product_id: int | None,
        product_title: str | None,
    ):
        if not contents or not filename or not product_id:
            return "Produkt und Datei sind Pflicht.", "Produkt und Datei sind Pflicht.", no_update
        try:
            _header, encoded = contents.split(",", 1)
            payload = base64.b64decode(encoded)
            storage_root = get_pim_settings().asset_storage_root / f"product-{product_id}"
            storage_root.mkdir(parents=True, exist_ok=True)
            target = storage_root / filename
            counter = 2
            while target.exists():
                target = storage_root / f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
                counter += 1
            target.write_bytes(payload)
            create_asset_record(session, target, product_id=product_id, alt_text=product_title)
        except Exception as exc:
            message = f"Asset-Upload fehlgeschlagen: {exc}"
            return message, message, no_update
        message = f"Asset {target.name} für Produkt {product_id} gespeichert."
        return message, message, (refresh_token or 0) + 1

    @app.callback(
        Output("r2-config-enabled", "value"),
        Output("r2-config-provider", "value"),
        Output("r2-config-endpoint", "value"),
        Output("r2-config-bucket", "value"),
        Output("r2-config-region", "value"),
        Output("r2-config-public-base-url", "value"),
        Output("r2-config-path-prefix", "value"),
        Output("r2-config-storage-class", "value"),
        Output("r2-config-max-upload-size-mb", "value"),
        Output("r2-config-allowed-file-types", "value"),
        Output("r2-config-notes", "value"),
        Output("r2-config-access-key-status", "children"),
        Output("r2-config-secret-status", "children"),
        Output("r2-config-status", "children"),
        Input("snapshot-store", "data"),
    )
    @_with_session
    def load_r2_config_callback(session: Session, _snapshot: dict | None):
        data = serialize_r2_config(get_or_create_r2_config(session))
        access_status = (
            f"Access Key gespeichert: ja ({data['access_key_id_masked']})"
            if data["access_key_configured"]
            else "Access Key gespeichert: nein"
        )
        secret_status = "Secret gespeichert: ja" if data["secret_configured"] else "Secret gespeichert: nein"
        return (
            data["enabled"],
            data["provider"],
            data["endpoint"],
            data["bucket"],
            data["region"],
            data["public_base_url"],
            data["path_prefix"],
            data["storage_class"],
            data["max_upload_size_mb"],
            data["allowed_file_types"],
            data["notes"],
            access_status,
            secret_status,
            _render_r2_config_status(data),
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("r2-config-status", "children", allow_duplicate=True),
        Output("r2-config-access-key-id", "value"),
        Output("r2-config-secret-key", "value"),
        Input("r2-config-save-button", "n_clicks"),
        State("r2-config-enabled", "value"),
        State("r2-config-provider", "value"),
        State("r2-config-endpoint", "value"),
        State("r2-config-bucket", "value"),
        State("r2-config-region", "value"),
        State("r2-config-access-key-id", "value"),
        State("r2-config-secret-key", "value"),
        State("r2-config-public-base-url", "value"),
        State("r2-config-path-prefix", "value"),
        State("r2-config-storage-class", "value"),
        State("r2-config-max-upload-size-mb", "value"),
        State("r2-config-allowed-file-types", "value"),
        State("r2-config-notes", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_r2_config_callback(
        session: Session,
        _clicks: int | None,
        enabled: bool | None,
        provider: str | None,
        endpoint: str | None,
        bucket: str | None,
        region: str | None,
        access_key_id: str | None,
        secret_key: str | None,
        public_base_url: str | None,
        path_prefix: str | None,
        storage_class: str | None,
        max_upload_size_mb: int | None,
        allowed_file_types: str | None,
        notes: str | None,
    ):
        try:
            config = save_r2_config(
                session,
                {
                    "enabled": bool(enabled),
                    "provider": provider,
                    "endpoint": endpoint,
                    "bucket": bucket,
                    "region": region,
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_key,
                    "public_base_url": public_base_url,
                    "path_prefix": path_prefix,
                    "storage_class": storage_class,
                    "max_upload_size_mb": max_upload_size_mb,
                    "allowed_file_types": allowed_file_types,
                    "notes": notes,
                },
            )
            message = "R2-Konfiguration gespeichert. Secret-Felder wurden nicht zurückgegeben."
            return message, _render_r2_config_status(serialize_r2_config(config)), "", ""
        except Exception as exc:
            message = f"R2-Konfiguration konnte nicht gespeichert werden: {exc}"
            return message, message, no_update, no_update

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("r2-config-status", "children", allow_duplicate=True),
        Input("r2-config-test-button", "n_clicks"),
        prevent_initial_call=True,
    )
    @_with_session
    def test_r2_config_callback(session: Session, _clicks: int | None):
        result = test_r2_connection(session)
        message = str(result.get("message") or "Verbindungstest abgeschlossen.")
        if result.get("status") == "ok":
            return message, html.Div(message, style={"color": "#166534", "fontWeight": "600"})
        return f"R2-Verbindungstest fehlgeschlagen: {message}", html.Div(message, style={"color": "#b91c1c", "fontWeight": "600"})

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("r2-asset-upload-result", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("assets-grid", "selectedRows", allow_duplicate=True),
        Output("selected-asset-ids", "data", allow_duplicate=True),
        Input("assets-send-to-uploader-button", "n_clicks"),
        State("refresh-token", "data"),
        State("selected-asset-ids", "data"),
        prevent_initial_call=True,
    )
    @_with_session
    def send_selected_assets_to_uploader_callback(
        session: Session,
        _clicks: int | None,
        refresh_token: int,
        selected_ids: list[int] | None,
    ):
        if not selected_ids:
            return "Keine Assets ausgewählt.", no_update, no_update, no_update, no_update
        try:
            r2_storage = build_r2_storage(session)
            r2_options = get_r2_upload_options(session)
        except Exception as exc:
            message = f"Object Storage ist nicht vollständig konfiguriert. Bitte unter Assets -> R2 Speicher -> Konfiguration prüfen. Detail: {exc}"
            return message, message, no_update, no_update, no_update
        result = upload_selected_assets_to_r2(
            session,
            [int(asset_id) for asset_id in selected_ids],
            storage=r2_storage,
            max_upload_size_mb=int(r2_options["max_upload_size_mb"]),
            path_prefix=str(r2_options["path_prefix"]),
            allowed_file_types=str(r2_options["allowed_file_types"]),
        )
        message = (
            f"Ausgewählte Assets verarbeitet: {result['uploaded_count']} hochgeladen, "
            f"{result['skipped_count']} übersprungen, {result['error_count']} Fehler."
        )
        should_refresh = int(result["uploaded_count"]) > 0
        return message, _render_asset_uploader_selection_result(result), (refresh_token or 0) + 1 if should_refresh else no_update, [], []

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("r2-asset-upload-result", "children"),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("r2-asset-upload-button", "n_clicks"),
        State("refresh-token", "data"),
        State("r2-asset-upload", "contents"),
        State("r2-asset-upload", "filename"),
        State("r2-asset-type", "value"),
        State("r2-product-id", "value"),
        State("r2-language-code", "value"),
        State("r2-asset-title", "value"),
        State("r2-asset-description", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def upload_r2_assets_callback(
        session: Session,
        _clicks: int | None,
        refresh_token: int,
        contents: list[str] | str | None,
        filenames: list[str] | str | None,
        asset_type: str | None,
        product_id: int | None,
        language_code: str | None,
        title: str | None,
        description: str | None,
    ):
        content_list = contents if isinstance(contents, list) else ([contents] if contents else [])
        filename_list = filenames if isinstance(filenames, list) else ([filenames] if filenames else [])
        if not content_list or not filename_list:
            return "Keine Datei für R2-Upload ausgewählt.", "Keine Datei ausgewählt.", no_update
        uploaded: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []
        protocol_items: list[html.Li] = []
        try:
            r2_storage = build_r2_storage(session)
            r2_options = get_r2_upload_options(session)
        except Exception as exc:
            message = f"Object Storage ist nicht vollständig konfiguriert. Bitte unter Assets -> R2 Speicher -> Konfiguration prüfen. Detail: {exc}"
            return message, message, no_update
        for content, filename in zip(content_list, filename_list):
            try:
                _header, encoded = str(content).split(",", 1)
                payload = base64.b64decode(encoded)
                asset, log_entries = upload_r2_asset_from_bytes(
                    session,
                    payload,
                    str(filename),
                    asset_type=str(asset_type or "other"),
                    product_id=int(product_id) if product_id else None,
                    language_code=str(language_code or "").strip() or None,
                    title=str(title or "").strip() or None,
                    description=str(description or "").strip() or None,
                    storage=r2_storage,
                    max_upload_size_mb=int(r2_options["max_upload_size_mb"]),
                    path_prefix=str(r2_options["path_prefix"]),
                    allowed_file_types=str(r2_options["allowed_file_types"]),
                )
                uploaded.append(
                    {
                        "filename": asset.original_filename,
                        "asset_id": asset.id,
                        "object_key": asset.object_key,
                        "bucket": asset.bucket,
                        "mime_type": asset.mime_type,
                        "file_size": asset.file_size,
                        "public_url": asset.public_url,
                    }
                )
                protocol_items.extend(html.Li(f"{asset.original_filename}: {entry['message']}") for entry in log_entries)
            except Exception as exc:
                errors.append({"filename": filename, "message": str(exc)})
                protocol_items.append(html.Li(f"{filename}: Fehler: {exc}", style={"color": "#b91c1c"}))
        rows = []
        for item in uploaded:
            link = html.A("Öffnen", href=item["public_url"] or f"/asset-file/{item['asset_id']}", target="_blank")
            rows.append(
                html.Tr(
                    [
                        html.Td(str(item["filename"])),
                        html.Td(str(item["mime_type"])),
                        html.Td(str(item["file_size"])),
                        html.Td(str(item["bucket"])),
                        html.Td(str(item["object_key"])),
                        html.Td(item["public_url"] or "Keine öffentliche Asset-Domain konfiguriert"),
                        html.Td(link),
                    ]
                )
            )
        summary_text = f"R2-Upload: {len(uploaded)} erfolgreich, {len(errors)} Fehler."
        result = html.Div(
            [
                html.Div(summary_text, style={"fontWeight": "700", "marginBottom": "8px"}),
                html.Table(
                    [
                        html.Thead(html.Tr([html.Th("Datei"), html.Th("MIME"), html.Th("Bytes"), html.Th("Bucket"), html.Th("Object Key"), html.Th("Public URL"), html.Th("Aktion")])),
                        html.Tbody(rows),
                    ],
                    className="summary-table",
                ) if rows else html.Div("Keine Datei erfolgreich hochgeladen."),
                html.Div([html.Strong("Protokoll"), html.Ul(protocol_items)], style={"marginTop": "10px"}),
            ]
        )
        return summary_text, result, (refresh_token or 0) + 1 if uploaded else no_update

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("category-save-button", "n_clicks"),
        State("refresh-token", "data"),
        State("category-detail-id", "value"),
        State("category-detail-name", "value"),
        State("category-detail-parent-id", "value"),
        State("category-detail-language-code", "value"),
        State("category-detail-sort-order", "value"),
        State("categories-sales-channel-code", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def save_category_detail_callback(
        session: Session,
        _: int,
        refresh_token: int,
        category_id: int | None,
        name: str | None,
        parent_id: int | None,
        language_code: str | None,
        sort_order: int | None,
        sales_channel_code: str | None,
    ):
        if not category_id:
            return "Keine Kategorie ausgewählt.", no_update
        if not name:
            return "Kategorie-Name fehlt.", no_update
        try:
            update_category(
                session,
                int(category_id),
                name=name,
                parent_id=_int_or_none(parent_id),
                language_code=(language_code or "de"),
                sort_order=_int_or_zero(sort_order),
                sales_channel_code=sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
            )
        except ValueError as exc:
            return str(exc), no_update
        return f"Kanal-Kategorie {category_id} für {(sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)} gespeichert.", (refresh_token or 0) + 1

    @app.callback(
        Output("category-delete-confirm", "displayed"),
        Input("category-delete-button", "n_clicks"),
        State("category-detail-id", "value"),
        prevent_initial_call=True,
    )
    def confirm_category_delete(_: int | None, category_id: int | None):
        if not category_id:
            return False
        return True

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("categories-grid", "selectedRows", allow_duplicate=True),
        Output("selected-category-id", "data", allow_duplicate=True),
        Input("category-delete-confirm", "submit_n_clicks"),
        State("refresh-token", "data"),
        State("category-detail-id", "value"),
        State("categories-sales-channel-code", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def delete_category_callback(
        session: Session,
        submit_n_clicks: int | None,
        refresh_token: int,
        category_id: int | None,
        sales_channel_code: str | None,
    ):
        if not submit_n_clicks or not category_id:
            return no_update, no_update, no_update, no_update
        try:
            delete_category(session, int(category_id), sales_channel_code=sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)
        except ValueError as exc:
            return str(exc), no_update, no_update, no_update
        return (
            f"Kanal-Kategorie {category_id} aus {(sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)} gelöscht.",
            (refresh_token or 0) + 1,
            [],
            None,
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("category-create-button", "n_clicks"),
        State("refresh-token", "data"),
        State("category-name", "value"),
        State("category-parent-id", "value"),
        State("category-language-code", "value"),
        State("category-sort-order", "value"),
        State("categories-sales-channel-code", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def create_category_callback(
        session: Session,
        _: int,
        refresh_token: int,
        name: str | None,
        parent_id: int | None,
        language_code: str | None,
        sort_order: int | None,
        sales_channel_code: str | None,
    ):
        if not name:
            return "Kategorie-Name fehlt.", no_update
        try:
            create_category(
                session,
                name=name,
                parent_id=parent_id,
                language_code=(language_code or "de").strip(),
                sort_order=sort_order or 0,
                sales_channel_code=sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE,
            )
        except ValueError as exc:
            return str(exc), no_update
        return f"Kanal-Kategorie {name} für {(sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE)} angelegt.", (refresh_token or 0) + 1

    @app.callback(
        Output("import-clean-file", "value"),
        Output("flash-message", "children", allow_duplicate=True),
        Input("import-clean-upload", "contents"),
        State("import-clean-upload", "filename"),
        prevent_initial_call=True,
    )
    def upload_clean_import_callback(contents: str | None, filename: str | None):
        if not contents or not filename:
            return no_update, "Keine Datei ausgewählt."
        try:
            saved_path = save_uploaded_import_file(contents, filename)
        except ValueError as exc:
            return no_update, str(exc)
        return str(saved_path), f"Importdatei gespeichert: {saved_path.name}"

    @app.callback(
        Output("pim-import-job-store", "data", allow_duplicate=True),
        Output("pim-import-job-poll", "disabled", allow_duplicate=True),
        Output("import-status", "children", allow_duplicate=True),
        Output("flash-message", "children", allow_duplicate=True),
        Input("import-run-button", "n_clicks"),
        State("import-clean-file", "value"),
        State("import-source-name", "value"),
        State("import-mapping-config", "value"),
        State("import-sales-channel-code", "value"),
        State("import-dry-run", "value"),
        prevent_initial_call=True,
    )
    def run_import_callback(
        _: int,
        clean_file: str | None,
        source_name: str | None,
        mapping_path: str | None,
        sales_channel_code: str | None,
        dry_values: list[str] | None,
    ):
        if not clean_file:
            return no_update, no_update, "Pfad zur Clean-Datei fehlt.", "Pfad zur Clean-Datei fehlt."
        clean_path = str(Path(clean_file))
        job_id = uuid.uuid4().hex[:12]
        resolved_source_name = source_name or Path(clean_file).name
        dry_run = "dry" in (dry_values or [])
        resolved_sales_channel_code = (sales_channel_code or DEFAULT_CATEGORY_CHANNEL_CODE).strip() or DEFAULT_CATEGORY_CHANNEL_CODE
        _set_pim_import_run(
            job_id,
            status="queued",
            source_name=resolved_source_name,
            clean_file=clean_path,
            dry_run=dry_run,
            sales_channel_code=resolved_sales_channel_code,
        )
        thread = threading.Thread(
            target=_run_pim_import_background,
            args=(job_id, clean_path, resolved_source_name, mapping_path, dry_run, resolved_sales_channel_code),
            daemon=True,
        )
        thread.start()
        return (
            {"job_id": job_id},
            False,
            f"PIM-Import läuft: {resolved_source_name} · Kanal {resolved_sales_channel_code}",
            f"Import gestartet: {resolved_source_name} · Kanal {resolved_sales_channel_code}",
        )

    @app.callback(
        Output("import-status", "children", allow_duplicate=True),
        Output("flash-message", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Output("pim-import-job-store", "data", allow_duplicate=True),
        Output("pim-import-job-poll", "disabled", allow_duplicate=True),
        Input("pim-import-job-poll", "n_intervals"),
        State("pim-import-job-store", "data"),
        State("refresh-token", "data"),
        prevent_initial_call=True,
    )
    def poll_import_callback(_: int, job_store: dict | None, refresh_token: int):
        job_id = (job_store or {}).get("job_id") if job_store else None
        if not job_id:
            return no_update, no_update, no_update, no_update, True
        payload = _get_pim_import_run(job_id)
        if not payload:
            return "Importstatus nicht gefunden.", "Importstatus nicht gefunden.", refresh_token, None, True
        status = str(payload.get("status") or "queued")
        source_name = str(payload.get("source_name") or job_id)
        sales_channel_code = str(payload.get("sales_channel_code") or payload.get("summary", {}).get("sales_channel_code") or DEFAULT_CATEGORY_CHANNEL_CODE)
        if status in {"queued", "running"}:
            return f"PIM-Import läuft: {source_name} · Kanal {sales_channel_code}", no_update, no_update, job_store, False
        if status == "completed":
            summary = payload.get("summary")
            return (
                f"Import abgeschlossen ({sales_channel_code}): {summary}",
                f"Import abgeschlossen ({sales_channel_code}): {summary}",
                (refresh_token or 0) + 1,
                None,
                True,
            )
        error = payload.get("error") or "Unbekannter Fehler"
        return (
            f"Import fehlgeschlagen ({sales_channel_code}): {error}",
            f"Import fehlgeschlagen ({sales_channel_code}): {error}",
            refresh_token,
            None,
            True,
        )

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("product-enrich-status", "children"),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("product-enrich-run-button", "n_clicks"),
        State("refresh-token", "data"),
        State("products-grid", "selectedRows"),
        State("product-enrich-seed-url", "value"),
        State("product-enrich-supplier-name", "value"),
        State("product-enrich-resolver-mode", "value"),
        State("product-enrich-listing-url", "value"),
        State("product-enrich-max-pages", "value"),
        State("product-enrich-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_product_enrichment_callback(
        session: Session,
        _: int,
        refresh_token: int,
        selected_rows: list[dict] | None,
        seed_url: str | None,
        supplier_name: str | None,
        resolver_mode: str | None,
        resolver_listing_url: str | None,
        max_pages: int | None,
        option_values: list[str] | None,
    ):
        product_ids = [int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None]
        if not product_ids:
            message = "Keine Produkte markiert."
            return message, _website_crawler_error_box(message), no_update
        try:
            with process_guard(
                "Website-Crawler für Produkte",
                options={
                    "supplier": supplier_name or "Tintolav",
                    "resolver": resolver_mode or "generic_crawl",
                    "seed_url": seed_url or "",
                    "listing_url": resolver_listing_url or "",
                    "max_pages": int(max_pages or 0),
                    "selected_options": option_values or [],
                },
                selection={"products": len(product_ids), "product_ids": product_ids[:20]},
                progress_total=len(product_ids),
            ):
                update_process(message=f"{len(product_ids)} Produkt(e) werden verarbeitet.", progress_current=0)
                summary = run_selected_website_enrichment(
                    session=session,
                    options=_enrichment_options(seed_url, supplier_name, max_pages, option_values, resolver_mode, resolver_listing_url),
                    product_ids=product_ids,
                )
                counters = {
                    "discovered_urls": int(summary.get("discovered_urls", 0) or 0),
                    "matched_products": int(summary.get("matched_products", 0) or 0),
                    "direct_updated_fields": int(summary.get("direct_updated_fields", summary.get("updated_fields", 0)) or 0),
                    "candidate_fields": int(summary.get("candidate_fields", 0) or 0),
                    "errors": int(summary.get("errors", 0) or 0),
                }
                message = _website_crawler_message("Website-Crawler für Produkte", summary)
                finish_process(
                    status="success" if counters["errors"] == 0 else "partial_success",
                    message=message,
                    counters=counters,
                )
        except ValueError as exc:
            message = str(exc)
            fail_process(message)
            return message, _website_crawler_error_box(message), no_update
        except ProcessAlreadyRunning as exc:
            message = str(exc)
            return message, _website_crawler_error_box(message), no_update
        except Exception as exc:
            message = f"Website-Crawler für Produkte fehlgeschlagen: {exc}"
            fail_process(message)
            return message, _website_crawler_error_box(message), no_update
        return message, _website_crawler_result_box("Website-Crawler für Produkte", summary), (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("variant-enrich-status", "children"),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("variant-enrich-run-button", "n_clicks"),
        State("refresh-token", "data"),
        State("variants-grid", "selectedRows"),
        State("variant-enrich-seed-url", "value"),
        State("variant-enrich-supplier-name", "value"),
        State("variant-enrich-resolver-mode", "value"),
        State("variant-enrich-listing-url", "value"),
        State("variant-enrich-max-pages", "value"),
        State("variant-enrich-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_variant_enrichment_callback(
        session: Session,
        _: int,
        refresh_token: int,
        selected_rows: list[dict] | None,
        seed_url: str | None,
        supplier_name: str | None,
        resolver_mode: str | None,
        resolver_listing_url: str | None,
        max_pages: int | None,
        option_values: list[str] | None,
    ):
        variant_ids = [int(row["id"]) for row in (selected_rows or []) if row.get("id") is not None]
        if not variant_ids:
            return "Keine Varianten markiert.", "Keine Varianten markiert.", no_update
        try:
            summary = run_selected_website_enrichment(
                session=session,
                options=_enrichment_options(seed_url, supplier_name, max_pages, option_values, resolver_mode, resolver_listing_url),
                variant_ids=variant_ids,
            )
        except ValueError as exc:
            return str(exc), str(exc), no_update
        message = _website_crawler_message("Website-Crawler für Varianten", summary)
        return message, message, (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("enrich-status", "children"),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("enrich-selected-run-button", "n_clicks"),
        State("refresh-token", "data"),
        State("selected-product-ids", "data"),
        State("selected-variant-ids", "data"),
        State("enrich-seed-url", "value"),
        State("enrich-supplier-name", "value"),
        State("enrich-resolver-mode", "value"),
        State("enrich-listing-url", "value"),
        State("enrich-max-pages", "value"),
        State("enrich-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_selected_enrichment_callback(
        session: Session,
        _: int,
        refresh_token: int,
        product_ids: list[int] | None,
        variant_ids: list[int] | None,
        seed_url: str | None,
        supplier_name: str | None,
        resolver_mode: str | None,
        resolver_listing_url: str | None,
        max_pages: int | None,
        option_values: list[str] | None,
    ):
        if not (product_ids or variant_ids):
            return "Keine Produkte oder Varianten markiert.", "Keine Produkte oder Varianten markiert.", no_update
        try:
            summary = run_selected_website_enrichment(
                session=session,
                options=_enrichment_options(seed_url, supplier_name, max_pages, option_values, resolver_mode, resolver_listing_url),
                product_ids=product_ids or [],
                variant_ids=variant_ids or [],
            )
        except ValueError as exc:
            return str(exc), str(exc), no_update
        message = _website_crawler_message("Website-Crawler für Auswahl", summary)
        return message, message, (refresh_token or 0) + 1

    @app.callback(
        Output("flash-message", "children", allow_duplicate=True),
        Output("enrich-status", "children", allow_duplicate=True),
        Output("refresh-token", "data", allow_duplicate=True),
        Input("enrich-run-button", "n_clicks"),
        State("refresh-token", "data"),
        State("enrich-seed-url", "value"),
        State("enrich-supplier-name", "value"),
        State("enrich-resolver-mode", "value"),
        State("enrich-listing-url", "value"),
        State("enrich-max-pages", "value"),
        State("enrich-options", "value"),
        prevent_initial_call=True,
    )
    @_with_session
    def run_enrichment_callback(
        session: Session,
        _: int,
        refresh_token: int,
        seed_url: str | None,
        supplier_name: str | None,
        resolver_mode: str | None,
        resolver_listing_url: str | None,
        max_pages: int | None,
        option_values: list[str] | None,
    ):
        try:
            summary = run_website_enrichment(
                session=session,
                options=_enrichment_options(seed_url, supplier_name, max_pages, option_values, resolver_mode, resolver_listing_url),
            )
        except ValueError as exc:
            return str(exc), str(exc), no_update
        message = _website_crawler_message("Website-Anreicherung", summary)
        return message, message, (refresh_token or 0) + 1


def configure_dash_app(app: Dash) -> Dash:
    app.index_string = """
    <!DOCTYPE html>
    <html>
      <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
          body { font-family: Arial, sans-serif; margin: 0; background: #f6f7fb; color: #1f2937; }
          .page { padding: 20px; }
          .page-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
          .page-body { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 20px; align-items: start; }
          .page-body-collapsed { grid-template-columns: 56px minmax(0, 1fr); }
          .page-title { margin: 0; font-size: 32px; line-height: 1.1; }
          .toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
          .toolbar-link-button { display: inline-block; padding: 8px 12px; border: 1px solid #1f2937; background: white; border-radius: 4px; cursor: pointer; color: #111827; text-decoration: none; line-height: 1.2; }
          .sidebar-shell { display: grid; gap: 10px; position: sticky; top: 20px; align-self: start; }
          .sidebar-toggle-row { display: flex; justify-content: flex-end; }
          .sidebar-toggle-button { width: 40px; min-width: 40px; padding: 8px 0; font-size: 18px; line-height: 1; }
          .sidebar-nav { display: grid; gap: 8px; }
          .sidebar-shell-collapsed .sidebar-toggle-row { justify-content: center; }
          .sidebar-nav-collapsed { display: none; }
          .sidebar-nav-button { text-align: left; width: 100%; padding: 10px 12px; border: 1px solid #d5d9e2; background: white; border-radius: 8px; cursor: pointer; font-weight: 600; }
          .sidebar-nav-button-active { background: #1f2937; color: white; border-color: #1f2937; }
          .sidebar-shell-collapsed { width: 56px; }
          .main-tabs-panel { min-width: 0; }
          .metrics { display: grid; grid-template-columns: repeat(4, minmax(140px, 1fr)); gap: 12px; margin-top: 18px; margin-bottom: 16px; }
          .metric-card, .panel { background: white; border: 1px solid #d5d9e2; border-radius: 8px; padding: 12px; }
          .metric-card-button { text-align: left; width: 100%; }
          .metric-title { font-size: 12px; text-transform: uppercase; color: #6b7280; }
          .metric-value { font-size: 28px; font-weight: 700; margin-top: 8px; }
          .form-grid { display: grid; gap: 10px; margin: 12px 0 20px; }
          .form-section-heading { margin: 4px 0 0; font-size: 16px; color: #111827; }
          .variant-editor-layout { display: grid; grid-template-columns: minmax(280px, 360px) minmax(0, 1fr); gap: 12px; align-items: start; margin-top: 12px; }
          .variant-form-card { display: grid; gap: 10px; margin: 12px 0 20px; max-width: 980px; background: white; border: 1px solid #d5d9e2; border-radius: 8px; padding: 12px; }
          .variant-form-row { display: grid; grid-template-columns: repeat(3, minmax(180px, 1fr)); gap: 12px; }
          .variant-form-row--wide { grid-template-columns: 140px minmax(280px, 1fr); }
          .variant-form-row--compact { grid-template-columns: 160px minmax(220px, 1fr) 180px; }
          .variant-form-card label { display: block; font-size: 12px; font-weight: 700; color: #4b5563; margin-bottom: 4px; }
          .category-tree-description { margin: 0 0 14px; color: #6b7280; font-size: 14px; }
          .category-form-card { display: grid; gap: 10px; margin: 12px 0 20px; max-width: 760px; background: white; border: 1px solid #d5d9e2; border-radius: 8px; padding: 12px; }
          .category-form-row { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) minmax(180px, 220px); gap: 12px; }
          .category-form-row--compact { grid-template-columns: 180px 180px; }
          .category-form-card label { display: block; font-size: 12px; font-weight: 700; color: #4b5563; margin-bottom: 4px; }
          .button-row { display: flex; gap: 8px; }
          .detail-columns { display: grid; grid-template-columns: repeat(3, minmax(260px, 1fr)); gap: 12px; margin-top: 12px; }
          .translation-layout { display: grid; gap: 12px; }
          .translation-row { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(520px, 2fr); gap: 12px; align-items: start; }
          .detail-tabs { margin-top: 8px; }
          .detail-tab { padding: 10px 14px !important; border: 1px solid #d5d9e2 !important; border-bottom: none !important; background: #f7f8fb !important; border-radius: 8px 8px 0 0 !important; font-weight: 600; }
          .detail-tab-selected { background: white !important; color: #111827 !important; border-color: #d5d9e2 !important; }
          .detail-summary-grid { display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 10px 18px; }
          .flash { min-height: 24px; margin: 8px 0 16px; color: #0f5132; font-weight: 600; }
          .selection-summary { margin: 0 0 12px; color: #4b5563; font-weight: 600; }
          .crawler-modal-panel { background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%); border-radius: 18px; padding: 0; width: min(1180px, 100%); max-height: 92vh; overflow: auto; box-shadow: 0 24px 70px rgba(15, 23, 42, 0.26); border: 1px solid #e5e7eb; }
          .crawler-modal-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; padding: 24px 26px 18px; border-bottom: 1px solid #e5e7eb; background: radial-gradient(circle at top left, #e0f2fe 0, rgba(224, 242, 254, 0) 36%), #ffffff; }
          .crawler-modal-title { margin: 0; font-size: 26px; letter-spacing: -0.02em; color: #0f172a; }
          .crawler-modal-subtitle { margin: 8px 0 0; color: #475569; max-width: 760px; line-height: 1.45; }
          .crawler-close-button { border-color: #cbd5e1; color: #334155; background: #ffffff; border-radius: 999px; }
          .crawler-modal-body { display: grid; gap: 16px; padding: 20px 26px 26px; }
          .crawler-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05); }
          .crawler-card-title { margin: 0 0 14px; color: #0f172a; font-size: 16px; letter-spacing: -0.01em; }
          .crawler-card-heading-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
          .crawler-source-grid { display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 14px; }
          .crawler-field { display: grid; gap: 6px; }
          .crawler-field-wide { grid-column: 1 / -1; }
          .crawler-field label { font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em; color: #475569; }
          .crawler-field-hint { font-size: 12px; color: #64748b; }
          .crawler-input { width: 100%; box-sizing: border-box; }
          .crawler-number-input { max-width: 180px; }
          .crawler-options-grid { display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 10px 18px; }
          .crawler-options-grid label { display: flex; gap: 8px; align-items: center; padding: 9px 10px; border: 1px solid #e2e8f0; border-radius: 10px; background: #f8fafc; font-weight: 600; color: #334155; }
          .crawler-button-grid { display: flex; flex-wrap: wrap; gap: 10px; }
          .crawler-primary-button { background: #0f172a; color: #ffffff; border-color: #0f172a; border-radius: 10px; padding: 11px 18px; font-weight: 800; display: inline-flex; align-items: center; gap: 8px; }
          .crawler-secondary-button { border-color: #cbd5e1; color: #0f172a; background: #ffffff; border-radius: 10px; font-weight: 700; }
          .crawler-muted-button { border-color: #fed7aa; color: #9a3412; background: #fff7ed; border-radius: 10px; font-weight: 700; }
          .crawler-selection-summary { padding: 8px 12px; border-radius: 999px; background: #e0f2fe; color: #075985; font-weight: 800; }
          .crawler-start-card { border-color: #bfdbfe; background: linear-gradient(180deg, #ffffff 0%, #eff6ff 100%); }
          .crawler-start-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
          .crawler-running-hint { margin-top: 10px; color: #1e3a8a; font-weight: 700; }
          .crawler-running-subtext { margin-top: 4px; color: #475569; font-weight: 600; }
          .crawler-spinner-inline { width: 14px; height: 14px; border: 2px solid rgba(255, 255, 255, 0.45); border-top-color: #ffffff; border-radius: 999px; display: inline-block; animation: crawler-spin 0.8s linear infinite; }
          .crawler-status-placeholder { color: #64748b; padding: 12px; border: 1px dashed #cbd5e1; border-radius: 10px; background: #f8fafc; }
          .crawler-result-card { display: grid; gap: 14px; border: 1px solid #dbeafe; border-radius: 14px; background: #ffffff; padding: 16px; }
          .crawler-result-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
          .crawler-result-title { color: #475569; font-weight: 800; }
          .crawler-result-status { display: inline-flex; align-items: center; border-radius: 999px; padding: 7px 11px; font-weight: 900; font-size: 13px; }
          .crawler-result-status-success { background: #dcfce7; color: #166534; }
          .crawler-result-status-error { background: #fee2e2; color: #991b1b; }
          .crawler-result-grid { display: grid; grid-template-columns: repeat(3, minmax(150px, 1fr)); gap: 10px; }
          .crawler-result-metric { display: grid; gap: 5px; padding: 12px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; }
          .crawler-result-metric span { color: #64748b; font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.04em; }
          .crawler-result-metric strong { color: #0f172a; font-size: 18px; }
          .crawler-result-hint { padding: 10px 12px; border-radius: 10px; background: #fffbeb; color: #92400e; font-weight: 700; }
          .crawler-result-error { color: #991b1b; font-weight: 700; }
          .crawler-details { color: #475569; }
          .crawler-log-block { margin: 10px 0 0; padding: 12px; background: #0f172a; color: #e2e8f0; border-radius: 10px; overflow: auto; max-height: 260px; font-size: 12px; }
          .unified-enrichment-subtitle { margin: 8px 0 0; color: #475569; max-width: 860px; line-height: 1.45; }
          .unified-enrichment-layout { display: grid; gap: 16px; }
          .unified-enrichment-card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 18px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05); }
          .unified-enrichment-title { margin: 0 0 8px; font-size: 17px; color: #0f172a; letter-spacing: -0.01em; }
          .unified-enrichment-grid { grid-template-columns: repeat(3, minmax(220px, 1fr)); align-items: start; }
          .unified-enrichment-wide { grid-column: 1 / -1; }
          .product-enrichment-warning-list { display: grid; gap: 6px; padding: 10px 12px; border-radius: 10px; background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa; }
          .product-enrichment-details { margin-top: 8px; color: #475569; font-weight: 600; }
          .product-enrichment-technical-details { display: grid; gap: 5px; margin-top: 8px; padding: 10px 12px; border-radius: 10px; background: #f8fafc; border: 1px solid #e2e8f0; color: #475569; font-size: 12px; font-weight: 500; }
          @keyframes crawler-spin { to { transform: rotate(360deg); } }
          input, textarea { padding: 8px; border: 1px solid #c7ccd8; border-radius: 4px; }
          button { padding: 8px 12px; border: 1px solid #1f2937; background: white; border-radius: 4px; cursor: pointer; }
          @media (max-width: 960px) { .page-header, .page-body, .metrics, .detail-columns, .detail-summary-grid, .variant-editor-layout, .variant-form-row, .variant-form-row--wide, .variant-form-row--compact, .category-form-row, .category-form-row--compact, .translation-row, .crawler-source-grid, .crawler-options-grid, .crawler-result-grid, .unified-enrichment-grid { grid-template-columns: 1fr; } .page-header { display: grid; } .sidebar-shell { position: static; } .page-body-collapsed { grid-template-columns: 1fr; } .crawler-modal-header { display: grid; } .crawler-field-wide { grid-column: auto; } }
        </style>
      </head>
      <body>
        {%app_entry%}
        <footer>
          {%config%}
          {%scripts%}
          {%renderer%}
        </footer>
      </body>
    </html>
    """
    return app


app = configure_dash_app(create_dash_app())
server = app.server


def main() -> None:
    settings = get_pim_settings()
    app.run(host=settings.app_host, port=settings.app_port, debug=settings.debug)


if __name__ == "__main__":
    main()
