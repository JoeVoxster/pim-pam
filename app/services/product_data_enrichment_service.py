from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Asset, Product, ProductEnrichmentLog, ProductTranslation
from app.pdf.parser import extract_pdf_text
from app.services.product_translation_service import _call_openai_json, get_translation_config_status
from app.services.product_text_enrichment_service import format_markdown_description
from app.services.supplier_extraction_service import save_enrichment_candidates, serialize_asset_candidate, serialize_enrichment_candidate
from app.suppliers.registry import find_extractor_for_url


SUPPORTED_FIELDS = (
    "title",
    "short_description",
    "description",
    "seo_title",
    "seo_description",
    "technical_features_text",
    "specifications_text",
    "source_url_final",
    "slug",
)
DEFAULT_DOMAINS = ("voxster.ch", "voxer.ch", "tintolav.com", "tintolove.ch", "tintolav.ch")
ASSET_DATASHEET_TYPES = {"datasheet", "technical_datasheet", "technical_data_sheet", "product_sheet"}
ASSET_SDS_TYPES = {"sds", "sdb", "safety_data_sheet", "safety_data_sheet_pdf"}
ASSET_TEXT_MAX_CHARS = 9000
DEFAULT_TEXT_QUALITY_OPTIONS = {
    "markdown",
    "structure_sections",
    "b2b_smooth",
    "clean_supplier_text",
    "derive_seo",
}


@dataclass(frozen=True)
class PageExtract:
    url: str
    title: str | None
    meta_description: str | None
    h1: str | None
    paragraphs: tuple[str, ...]
    search_method: str = "url"
    search_query: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str | None
    snippet: str | None
    query: str


def preview_product_data_enrichment(
    session: Session,
    product_ids: list[int],
    *,
    fields: list[str] | None = None,
    overwrite_existing: bool = False,
    sources: list[str] | None = None,
    text_quality_options: list[str] | None = None,
    max_sources: int = 5,
    target_locale: str | None = None,
) -> dict[str, object]:
    target_fields = _normalize_fields(fields)
    source_modes = set(sources or ["final_url", "source_url", "configured_domains"])
    quality_options = set(text_quality_options or DEFAULT_TEXT_QUALITY_OPTIONS)
    results: list[dict[str, object]] = []
    for product_id in product_ids:
        product = _load_product(session, int(product_id))
        if product is None:
            results.append({"product_id": product_id, "status": "error", "errors": ["Produkt nicht gefunden"], "suggestions": []})
            continue
        result = _preview_one_product(
            session,
            product,
            fields=target_fields,
            overwrite_existing=overwrite_existing,
            source_modes=source_modes,
            text_quality_options=quality_options,
            max_sources=max_sources,
            target_locale=target_locale,
        )
        results.append(result)
    return {
        "status": "completed",
        "products_checked": len(results),
        "products_with_suggestions": sum(1 for row in results if row.get("suggestions")),
        "results": results,
    }


def apply_product_data_enrichment(
    session: Session,
    accepted_suggestions: list[dict[str, object]],
    *,
    overwrite_existing: bool = False,
    created_by: str | None = None,
) -> dict[str, object]:
    applied: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    for suggestion in _dedupe_apply_suggestions(accepted_suggestions):
        suggestion = dict(suggestion)
        if created_by:
            suggestion["created_by"] = created_by
        product_id = int(suggestion.get("product_id") or 0)
        field_name = str(suggestion.get("field_name") or "")
        value = str(suggestion.get("suggested_value") or "").strip()
        if str(suggestion.get("status") or "") in {"needs_translation", "language_mismatch"}:
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": "Fremdsprachiger Kandidat muss zuerst übersetzt werden"})
            continue
        if field_name not in SUPPORTED_FIELDS or not value:
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": "ungueltiger Vorschlag"})
            continue
        product = _load_product(session, product_id)
        if product is None:
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": "Produkt nicht gefunden"})
            continue
        target_locale = str(suggestion.get("target_locale") or product.source_language or "de-CH").strip()
        language_error = _language_mismatch_reason(suggestion, value, target_locale)
        if language_error:
            old_value = _current_field_value(product, field_name, target_locale=target_locale)
            suggestion["error_message"] = language_error
            _log_suggestion(session, product, field_name, old_value, value, suggestion, status="rejected")
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": language_error})
            continue
        old_value = _current_field_value(product, field_name, target_locale=target_locale)
        if _has_value(old_value) and not overwrite_existing:
            _log_suggestion(session, product, field_name, old_value, value, suggestion, status="rejected")
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": "bestehender Wert nicht überschrieben"})
            continue
        try:
            _set_product_field(session, product, field_name, value, target_locale=target_locale)
        except ValueError as exc:
            skipped.append({"product_id": product_id, "field_name": field_name, "reason": str(exc)})
            continue
        _log_suggestion(session, product, field_name, old_value, value, suggestion, status="accepted")
        applied.append({"product_id": product_id, "field_name": field_name})
    session.flush()
    return {"applied_count": len(applied), "skipped_count": len(skipped), "applied": applied, "skipped": skipped}


def _dedupe_apply_suggestions(accepted_suggestions: list[dict[str, object]]) -> list[dict[str, object]]:
    selected: dict[tuple[int, str, str], dict[str, object]] = {}
    for suggestion in accepted_suggestions:
        product_id = int(suggestion.get("product_id") or 0)
        field_name = str(suggestion.get("field_name") or "")
        target_locale = str(suggestion.get("target_locale") or "").strip()
        key = (product_id, field_name, target_locale)
        if key not in selected or _suggestion_rank(suggestion) > _suggestion_rank(selected[key]):
            selected[key] = suggestion
    return list(selected.values())


def _suggestion_rank(suggestion: dict[str, object]) -> tuple[int, int, float]:
    status = str(suggestion.get("status") or "")
    source_language = str(suggestion.get("source_language") or "").split("-", 1)[0].lower()
    target_language = str(suggestion.get("target_locale") or "").split("-", 1)[0].lower()
    method = str(suggestion.get("search_method") or "")
    confidence = float(str(suggestion.get("confidence") or "0").replace(",", "."))
    language_score = 1 if source_language and target_language and source_language == target_language else 0
    method_score = 1 if method.endswith("_extractor") and method not in {"generic_extractor"} else 0
    status_score = 1 if status in {"suggested", "translated"} else 0
    return (status_score + language_score, method_score, confidence)


def _language_mismatch_reason(suggestion: dict[str, object], value: str, target_locale: str) -> str | None:
    if str(suggestion.get("field_name") or "") not in LANGUAGE_VALIDATED_FIELDS:
        return None
    target_language = _language_base(target_locale)
    if not target_language:
        return None
    text_language = _detect_text_language(value)
    source_language = _language_base(str(suggestion.get("source_language") or ""))
    effective_language = text_language or source_language
    if not effective_language or effective_language == target_language:
        return None
    if target_language == "en" and effective_language == "de":
        return "Vorschlag ist deutsch, Zielübersetzung ist en. Übernahme blockiert."
    return f"Quellsprache {effective_language} weicht von Ziel {target_locale} ab. Bitte Übersetzungsvorschlag verwenden."


def _preview_one_product(
    session: Session,
    product: Product,
    *,
    fields: tuple[str, ...],
    overwrite_existing: bool,
    source_modes: set[str],
    text_quality_options: set[str],
    max_sources: int,
    target_locale: str | None,
) -> dict[str, object]:
    warnings: list[str] = []
    errors: list[str] = []
    sources_checked: list[dict[str, object]] = []
    suggestions: list[dict[str, object]] = []
    effective_target_locale = (target_locale or product.source_language or "de-CH").strip()
    missing_fields = [
        field
        for field in fields
        if overwrite_existing or not _has_value(_current_field_value(product, field, target_locale=effective_target_locale))
    ]
    if not missing_fields:
        return _result(product, "no_missing_fields", fields, [], [], [], ["Keine Felder fehlen."])
    if product.is_chemical:
        warnings.append("Chemische Sicherheits- und Gefahrstoffangaben dürfen nicht automatisch aus allgemeinen Produkttexten übernommen werden.")

    if {"asset_datasheet", "asset_sds"} & source_modes:
        asset_result = _asset_text_suggestions(
            session,
            product,
            missing_fields,
            source_modes=source_modes,
            text_quality_options=text_quality_options,
            target_locale=effective_target_locale,
            overwrite_existing=overwrite_existing,
        )
        suggestions.extend(asset_result["suggestions"])
        sources_checked.extend(asset_result["sources_checked"])
        warnings.extend(asset_result["warnings"])
        errors.extend(asset_result["errors"])

    extracts: list[PageExtract] = []
    for url, search_method, search_query in _candidate_urls(product, source_modes)[:max_sources]:
        try:
            supplier_extractor = find_extractor_for_url(url)
            if supplier_extractor.supplier_key != "generic":
                response = requests.get(
                    url,
                    timeout=20,
                    headers={"User-Agent": "PIM-PAM Product Enrichment/1.0"},
                )
                response.raise_for_status()
                extraction_result = supplier_extractor.extract_from_html(url, response.text)  # type: ignore[attr-defined]
                text_candidates, asset_candidates = save_enrichment_candidates(
                    session,
                    product,
                    extraction_result,
                    target_locales=_target_locales_for_product(product, effective_target_locale, extraction_result.detected_language),
                    translate=_auto_translate_supplier_candidates(),
                )
                supplier_result = {
                    "status": "extracted",
                    "supplier_key": extraction_result.supplier_key,
                    "source_url": extraction_result.source_url,
                    "product_name": extraction_result.product_name,
                    "detected_language": extraction_result.detected_language,
                    "warnings": extraction_result.warnings,
                    "candidates": [serialize_enrichment_candidate(row) for row in text_candidates],
                    "asset_candidates": [serialize_asset_candidate(row) for row in asset_candidates],
                }
                sources_checked.append(
                    {
                        "url": url,
                        "status": supplier_result.get("status"),
                        "search_method": f"{supplier_extractor.supplier_key}_extractor",
                        "search_query": search_query,
                        "supplier_key": supplier_result.get("supplier_key"),
                        "detected_language": supplier_result.get("detected_language"),
                    }
                )
                warnings.extend(str(item) for item in (supplier_result.get("warnings") or []))
                suggestions.extend(
                    _suggestions_from_supplier_candidates(
                        product,
                        missing_fields,
                        supplier_result,
                        target_locale=effective_target_locale,
                        allow_source_language_fallback=overwrite_existing,
                    )
                )
                if suggestions:
                    continue
            extract = _fetch_and_extract(url, search_method=search_method, search_query=search_query)
            extracts.append(extract)
            sources_checked.append(
                {"url": url, "status": "loaded", "search_method": search_method, "search_query": search_query, "warnings": list(extract.warnings)}
            )
            warnings.extend(extract.warnings)
        except Exception as exc:
            sources_checked.append({"url": url, "status": "error", "search_method": search_method, "search_query": search_query, "error": str(exc)})
            errors.append(f"{url}: {exc}")
            for field in missing_fields:
                _log_error(session, product, field, url, search_method, search_query, str(exc))

    if "configured_domains" in source_modes:
        queries = _diagnostic_search_queries(product)[:max_sources]
        for query in queries:
            sources_checked.append({"url": None, "status": "search_hint", "search_method": "configured_domain_query", "search_query": query})
        if not _has_relevant_extract(product, extracts):
            for query in queries:
                try:
                    search_results = _search_configured_domain(query, max_results=2)
                except Exception as exc:
                    sources_checked.append({"url": None, "status": "search_error", "search_method": "configured_domain_search", "search_query": query, "error": str(exc)})
                    errors.append(f"{query}: {exc}")
                    continue
                for search_result in search_results:
                    sources_checked.append(
                        {
                            "url": search_result.url,
                            "status": "search_result",
                            "search_method": "configured_domain_search",
                            "search_query": query,
                            "title": search_result.title,
                        }
                    )
                    snippet_extract = _extract_from_search_result(search_result, product)
                    if snippet_extract is not None:
                        extracts.append(snippet_extract)
                        continue
                    try:
                        extract = _fetch_and_extract(search_result.url, search_method="configured_domain_search", search_query=query)
                    except Exception as exc:
                        sources_checked.append({"url": search_result.url, "status": "error", "search_method": "configured_domain_search", "search_query": query, "error": str(exc)})
                        errors.append(f"{search_result.url}: {exc}")
                        continue
                    extracts.append(extract)
                    warnings.extend(extract.warnings)
                if _has_relevant_extract(product, extracts):
                    break

    if suggestions and not extracts:
        return _result(product, "suggested", fields, missing_fields, sources_checked, suggestions, warnings, errors)
    if not extracts:
        return _result(product, "no_source_found", fields, missing_fields, sources_checked, [], warnings + ["Keine passende Quelle gefunden."], errors)

    for field in missing_fields:
        if _has_suggestion_for_field(suggestions, product, field, effective_target_locale):
            continue
        suggestion = _suggest_field(product, field, extracts)
        if suggestion is None:
            warnings.append(f"Kein sicherer Vorschlag für {field} gefunden.")
            continue
        suggestion["target_locale"] = effective_target_locale
        suggestion["suggested_value"] = _apply_text_quality_to_field(
            field,
            str(suggestion.get("suggested_value") or ""),
            text_quality_options=text_quality_options,
            target_locale=effective_target_locale,
        )
        source_language = str(suggestion.get("source_language") or "")
        suggestion["status"] = _preview_status_for_language(str(suggestion.get("suggested_value") or ""), source_language, effective_target_locale)
        suggestion["warning"] = _preview_warning_for_language(str(suggestion.get("suggested_value") or ""), source_language, effective_target_locale)
        suggestion["current_value"] = _current_field_value(product, field, target_locale=effective_target_locale) or ""
        suggestions.append(suggestion)
        _log_suggestion(
            session,
            product,
            field,
            _current_field_value(product, field),
            str(suggestion["suggested_value"]),
            suggestion,
            status="suggested",
        )
    return _result(product, "suggested" if suggestions else "no_suggestions", fields, missing_fields, sources_checked, suggestions, warnings, errors)


def _has_suggestion_for_field(suggestions: list[dict[str, object]], product: Product, field: str, target_locale: str) -> bool:
    for suggestion in suggestions:
        if int(suggestion.get("product_id") or 0) != product.id:
            continue
        if suggestion.get("field_name") != field:
            continue
        suggestion_target = str(suggestion.get("target_locale") or target_locale).strip()
        if suggestion_target == target_locale:
            return True
    return False


def _asset_text_suggestions(
    session: Session,
    product: Product,
    fields: list[str],
    *,
    source_modes: set[str],
    text_quality_options: set[str],
    target_locale: str,
    overwrite_existing: bool,
) -> dict[str, list]:
    selected_assets = _product_text_assets(session, product, source_modes=source_modes, target_locale=target_locale)
    sources_checked: list[dict[str, object]] = []
    warnings: list[str] = []
    errors: list[str] = []
    if not selected_assets:
        return {
            "suggestions": [],
            "sources_checked": [{"url": None, "status": "no_matching_assets", "search_method": "asset_text", "search_query": target_locale}],
            "warnings": ["Keine passenden Datasheet-/SDS-Assets mit verwertbarem PDF-Text gefunden."],
            "errors": [],
        }
    for asset, source_kind, _text in selected_assets:
        sources_checked.append(
            {
                "url": f"asset:{asset.id}",
                "status": "loaded",
                "search_method": f"asset_{source_kind}",
                "search_query": f"Asset {asset.id} · {asset.filename}",
                "asset_id": asset.id,
                "asset_language": asset.language_code,
            }
        )
    if "asset_ai" in source_modes:
        try:
            ai_rows = _ai_asset_text_suggestions(
                product,
                fields,
                selected_assets,
                target_locale=target_locale,
                overwrite_existing=overwrite_existing,
                text_quality_options=text_quality_options,
            )
        except Exception as exc:
            ai_rows = []
            warnings.append(f"KI-Vorschläge aus Assets konnten nicht erzeugt werden; deterministischer Fallback verwendet. Fehler: {exc}")
        if ai_rows:
            for row in ai_rows:
                _log_suggestion(
                    session,
                    product,
                    str(row.get("field_name") or ""),
                    _current_field_value(product, str(row.get("field_name") or ""), target_locale=target_locale),
                    str(row.get("suggested_value") or ""),
                    row,
                    status="suggested",
                )
            return {"suggestions": ai_rows, "sources_checked": sources_checked, "warnings": warnings, "errors": errors}
        warnings.append("OpenAI ist nicht konfiguriert oder lieferte keine Asset-Textvorschläge; Fallback-Vorschläge wurden erzeugt.")
    rows: list[dict[str, object]] = []
    for field in fields:
        value = _asset_fallback_field_value(
            product,
            field,
            selected_assets,
            target_locale=target_locale,
            text_quality_options=text_quality_options,
        )
        if not value:
            continue
        first_asset, first_kind, _text = selected_assets[0]
        source_language = first_asset.language_code or _detect_text_language(value)
        source_summary = _asset_source_summary(selected_assets)
        suggestion = {
            "product_id": product.id,
            "sku": product.sku,
            "title": product.title,
            "field_name": field,
            "current_value": _current_field_value(product, field, target_locale=target_locale) or "",
            "suggested_value": value,
            "original_value": source_summary,
            "source_url": f"asset:{first_asset.id}",
            "source_domain": "Asset",
            "search_method": "asset_text_fallback",
            "search_query": f"{first_kind} Asset {first_asset.id}",
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 0.78 if first_kind == "datasheet" else 0.68,
            "status": _preview_status_for_language(value, source_language, target_locale),
            "source_language": source_language,
            "target_locale": target_locale,
            "section_name": "Asset Text",
            "warning": _preview_warning_for_language(value, source_language, target_locale),
        }
        rows.append(suggestion)
        _log_suggestion(
            session,
            product,
            field,
            _current_field_value(product, field, target_locale=target_locale),
            str(suggestion["suggested_value"]),
            suggestion,
            status="suggested",
        )
    return {"suggestions": rows, "sources_checked": sources_checked, "warnings": warnings, "errors": errors}


def _product_text_assets(
    session: Session,
    product: Product,
    *,
    source_modes: set[str],
    target_locale: str,
) -> list[tuple[Asset, str, str]]:
    include_datasheet = "asset_datasheet" in source_modes
    include_sds = "asset_sds" in source_modes
    assets = list(
        session.scalars(
            select(Asset)
            .where(Asset.product_id == product.id, Asset.mime_type == "application/pdf")
            .order_by(Asset.sort_order.asc(), Asset.id.asc())
        )
    )
    rows: list[tuple[Asset, str, str]] = []
    target_base = _language_base(target_locale)
    for asset in assets:
        source_kind = _asset_text_kind(asset)
        if source_kind == "datasheet" and not include_datasheet:
            continue
        if source_kind == "sds" and not include_sds:
            continue
        if source_kind == "other":
            continue
        text = _extract_asset_text(asset)
        if len(text) < 80:
            continue
        rows.append((asset, source_kind, text))
    rows.sort(key=lambda item: (_asset_kind_rank(item[1]), _asset_language_rank(item[0], target_locale), item[0].sort_order or 0, item[0].id or 0))
    return rows[:4]


def _asset_text_kind(asset: Asset) -> str:
    haystack = f"{asset.asset_type or ''} {asset.filename or ''} {asset.original_filename or ''} {asset.title or ''}".lower()
    if any(token in haystack for token in ASSET_DATASHEET_TYPES) or "data-sheet" in haystack or "datasheet" in haystack or "datenblatt" in haystack:
        return "datasheet"
    if any(token in haystack for token in ASSET_SDS_TYPES) or "safety data sheet" in haystack or "sicherheitsdatenblatt" in haystack:
        return "sds"
    return "other"


def _asset_kind_rank(kind: str) -> int:
    return {"datasheet": 0, "sds": 1}.get(kind, 9)


def _asset_language_rank(asset: Asset, target_locale: str) -> int:
    asset_language = _language_base(asset.language_code)
    target_language = _language_base(target_locale)
    if asset_language and target_language and asset_language == target_language:
        return 0
    if not asset_language:
        return 1
    return 2


def _extract_asset_text(asset: Asset) -> str:
    try:
        text = extract_pdf_text(asset.storage_path or "")
    except Exception:
        return ""
    return _trim_asset_context(text)


def _trim_asset_context(text: str) -> str:
    cleaned = re.sub(r"\s+\n", "\n", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if len(cleaned) <= ASSET_TEXT_MAX_CHARS:
        return cleaned
    return cleaned[:ASSET_TEXT_MAX_CHARS].rsplit("\n", 1)[0].strip()


def _asset_page_extract(asset: Asset, source_kind: str, text: str) -> PageExtract:
    paragraphs = tuple(row.strip() for row in re.split(r"\n\s*\n|(?<=\.)\s+(?=[A-Z])", text) if len(row.strip()) >= 40)
    return PageExtract(
        url=f"asset:{asset.id}",
        title=asset.title or asset.original_filename or asset.filename,
        meta_description=paragraphs[0] if paragraphs else None,
        h1=asset.title,
        paragraphs=paragraphs[:8],
        search_method=f"asset_{source_kind}",
        search_query=f"Asset {asset.id} · {asset.filename}",
    )


def _asset_source_summary(
    selected_assets: list[tuple[Asset, str, str]],
    *,
    source_ids: list[object] | None = None,
) -> str:
    selected_ids = {str(item) for item in source_ids or [] if str(item or "").strip()}
    assets = [
        (asset, kind, text)
        for asset, kind, text in selected_assets
        if not selected_ids or str(asset.id) in selected_ids
    ]
    if not assets:
        assets = selected_assets
    asset_lines = [
        f"Asset {asset.id} · {kind} · Sprache: {asset.language_code or '-'} · Datei: {asset.filename or asset.original_filename or '-'}"
        for asset, kind, _text in assets[:4]
    ]
    excerpt = _asset_source_excerpt("\n\n".join(text for _asset, _kind, text in assets))
    parts = ["Quelle: " + "; ".join(asset_lines)] if asset_lines else ["Quelle: Asset"]
    if excerpt:
        parts.append(f"Ausgangstext-Auszug: {excerpt}")
    return "\n\n".join(parts)


def _asset_source_excerpt(value: str, max_chars: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip() + " ..."


def _asset_fallback_field_value(
    product: Product,
    field: str,
    selected_assets: list[tuple[Asset, str, str]],
    *,
    target_locale: str,
    text_quality_options: set[str],
) -> str:
    combined_text = "\n\n".join(text for _asset, _kind, text in selected_assets)
    if not combined_text.strip():
        return ""
    description = _asset_markdown_description(product, selected_assets, target_locale=target_locale, text_quality_options=text_quality_options)
    if field == "description":
        return description
    if field == "short_description":
        intro = _asset_intro_text(product, selected_assets)
        return _trim_field("short_description", _summary_from_asset_intro(intro or description))
    if field == "seo_title":
        return _trim_field("seo_title", product.title or _asset_title_from_text(combined_text) or product.sku)
    if field == "seo_description":
        return _trim_field("seo_description", _summary_from_asset_intro(_asset_intro_text(product, selected_assets) or description))
    if field == "title":
        return _trim_field("title", product.title or _asset_title_from_text(combined_text))
    if field == "slug":
        return _slug_candidate(product.title or _asset_title_from_text(combined_text), product.sku)
    return ""


def _asset_markdown_description(
    product: Product,
    selected_assets: list[tuple[Asset, str, str]],
    *,
    target_locale: str,
    text_quality_options: set[str],
) -> str:
    preferred_text = next((text for _asset, kind, text in selected_assets if kind == "datasheet"), selected_assets[0][2])
    sds_text = next((text for _asset, kind, text in selected_assets if kind == "sds"), "")
    intro = _asset_intro_text(product, selected_assets)
    usage = _extract_asset_section(preferred_text, ("how to use", "use:", "application", "anwendung", "mode d'emploi", "modalita d'uso", "modalità d'uso"))
    function = _extract_asset_section(preferred_text, ("function", "funktion", "product function"))
    ingredients = _extract_asset_section(sds_text or preferred_text, ("ingredients", "composition", "inhaltsstoffe", "zusammensetzung", "composizione"))
    labels = _asset_section_labels(target_locale)
    blocks = [intro] if intro else []
    if "structure_sections" in text_quality_options and usage:
        blocks.append(f"### {labels['usage']}\n\n{_format_asset_steps_or_text(usage)}")
    if "structure_sections" in text_quality_options and function:
        blocks.append(f"### {labels['function']}\n\n{_plain_asset_line(function)}")
    if "structure_sections" in text_quality_options and ingredients:
        formatted_ingredients = _format_asset_ingredients(ingredients)
        if formatted_ingredients:
            blocks.append(f"### {labels['ingredients']}\n\n{formatted_ingredients}")
    if not blocks:
        return format_markdown_description(preferred_text)
    description = "\n\n".join(block for block in blocks if block.strip())
    return _apply_text_quality_to_field("description", description, text_quality_options=text_quality_options, target_locale=target_locale)


def _asset_section_labels(target_locale: str) -> dict[str, str]:
    if _language_base(target_locale) == "de":
        return {"usage": "Anwendung", "function": "Funktion", "ingredients": "Inhaltsstoffe"}
    if _language_base(target_locale) == "fr":
        return {"usage": "Application", "function": "Fonction", "ingredients": "Ingrédients"}
    if _language_base(target_locale) == "it":
        return {"usage": "Uso", "function": "Funzione", "ingredients": "Ingredienti"}
    return {"usage": "How to use", "function": "Function", "ingredients": "Ingredients"}


def _asset_intro_text(product: Product, selected_assets: list[tuple[Asset, str, str]]) -> str:
    text = next((value for _asset, kind, value in selected_assets if kind == "datasheet"), selected_assets[0][2])
    description = _extract_asset_section(text, ("product description", "description", "produktbeschreibung", "descrizione prodotto"))
    if not description:
        description = text
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", description).strip())
    intro = " ".join(sentence for sentence in sentences[:3] if sentence).strip()
    intro = re.sub(r"^(?:[A-Z0-9 ]+)?PRODUCT DESCRIPTION:\s*", "", intro, flags=re.I)
    return _trim_field("description", intro)


def _summary_from_asset_intro(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    sentence_match = re.match(r"^(.{80,250}?[.!?])(?:\s|$)", cleaned)
    if sentence_match:
        return sentence_match.group(1).strip()
    return cleaned[:250].rsplit(" ", 1)[0].strip(" .,;:")


def _asset_title_from_text(text: str) -> str:
    first_line = next((line.strip() for line in str(text or "").splitlines() if line.strip()), "")
    first_line = re.sub(r"\b(SAFETY DATA SHEET|TECHNICAL DATA SHEET|PRODUCT DATA SHEET)\b", "", first_line, flags=re.I)
    return re.sub(r"\s+", " ", first_line).strip(" -:")[:120]


def _extract_asset_section(text: str, labels: tuple[str, ...]) -> str:
    normalized = str(text or "")
    if not normalized.strip():
        return ""
    label_pattern = "|".join(re.escape(label) for label in labels)
    stop_pattern = (
        r"product description|description|how to use|application|function|ingredients|composition|warning|"
        r"storage|packaging|technical data|logistic information|clp regulation|section\s+\d+|1\.\d+|2\.\d+|3\.\d+|4\.\d+"
    )
    match = re.search(rf"(?:^|\n|\b)({label_pattern})\s*:?\s*(.+?)(?=\n\s*(?:{stop_pattern})\s*:|\Z)", normalized, flags=re.I | re.S)
    if not match:
        return ""
    return _trim_asset_section(match.group(2))


def _trim_asset_section(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" :-")
    cleaned = re.split(
        r"\b(?:LOGISTIC INFORMATION|CLP REGULATION|SECTION\s+4|First aid measures|Description of first aid measures|Geowin SDS)\b",
        cleaned,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" :-")
    return cleaned[:1800].rsplit(" ", 1)[0].strip(" .,;:")


def _plain_asset_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" .") + ("." if text and not str(text).strip().endswith(".") else "")


def _format_asset_steps_or_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\bUse:\s*(?=\d+[.)])", "", value, flags=re.I).strip()
    parts = re.split(r"\s(?=\d+\.\s)|\s(?=\d+\)\s)", value)
    steps = []
    for part in parts:
        cleaned = re.sub(r"^\d+[.)]\s*", "", part).strip()
        cleaned = cleaned.split("Attention", 1)[0].strip()
        cleaned = re.split(r"\b(?:Attention:|LOGISTIC INFORMATION|CLP REGULATION)\b", cleaned, maxsplit=1, flags=re.I)[0].strip()
        cleaned = re.sub(r"\bUse:\s*$", "", cleaned, flags=re.I).strip()
        cleaned = re.sub(r"^Use:\s*$", "", cleaned, flags=re.I).strip()
        if len(cleaned) >= 8:
            steps.append(cleaned.rstrip("."))
    if len(steps) >= 2:
        return "\n".join(f"{index}. {step}." for index, step in enumerate(steps[:8], start=1))
    return _plain_asset_line(value)


def _format_asset_ingredients(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" .")
    value = re.sub(r"^/information on ingredients\s*", "", value, flags=re.I)
    substance_match = re.search(
        r"\b(phosphoric acid\s+\.\.\.\s*%)\s+Note:\s*B\s*(>=\s*\d+\s*<\s*\d+%)",
        value,
        flags=re.I,
    )
    if substance_match:
        return f"{substance_match.group(1)}: {substance_match.group(2)}."
    if any(token in value.lower() for token in ("refer to paragraph", "note b", "classification index", "first aid")):
        return ""
    return _plain_asset_line(value[:700].rsplit(" ", 1)[0])


def _ai_asset_text_suggestions(
    product: Product,
    fields: list[str],
    selected_assets: list[tuple[Asset, str, str]],
    *,
    target_locale: str,
    overwrite_existing: bool,
    text_quality_options: set[str],
) -> list[dict[str, object]]:
    config = get_translation_config_status()
    if config["provider"] != "openai" or not config["enabled"]:
        return []
    requested_fields = [field for field in fields if field in {"title", "short_description", "description", "seo_title", "seo_description", "slug"}]
    if not requested_fields:
        return []
    context_blocks = []
    for asset, source_kind, text in selected_assets:
        context_blocks.append(
            f"Asset-ID: {asset.id}\nTyp: {source_kind}\nSprache: {asset.language_code or '-'}\nDatei: {asset.filename}\nText:\n{text}"
        )
    system_prompt = (
        "Du bist ein PIM/PAM-Redakteur für B2B-Produktdaten. "
        "Erzeuge nur sachliche, belegbare Produkttexte aus Datasheets und SDS/SDB. "
        "Datasheets sind die Hauptquelle für Marketing-/Beschreibungstexte; SDS/SDB dienen nur für sachliche Sicherheits- und Anwendungshinweise. "
        "Erfinde keine technischen Daten, Zertifikate, Gefahrenangaben oder Leistungsversprechen. "
        "Gib ausschliesslich gültiges JSON zurück."
    )
    user_prompt = (
        f"Produkt-ID: {product.id}\nSKU: {product.sku}\nAktueller Titel: {product.title}\n"
        f"Zielsprache/Locale: {target_locale}\nGewünschte Felder: {', '.join(requested_fields)}\n"
        f"Bestehende Werte überschreiben: {'ja' if overwrite_existing else 'nein'}\n\n"
        "Aufgabe:\n"
        "- Formuliere schöne, klare PIM-taugliche Texte in der Zielsprache.\n"
        "- short_description: maximal 250 Zeichen, ein sachlicher Satz.\n"
        "- description: Markdown verwenden. Zuerst 1-3 kurze Absätze, danach wenn aus den Assets belegbar Abschnitte mit Überschriften.\n"
        "- Für de/de-CH in description exakt diese Überschriften verwenden: ### Anwendung, ### Funktion, ### Inhaltsstoffe.\n"
        "- Für en in description exakt diese Überschriften verwenden: ### How to use, ### Function, ### Ingredients.\n"
        "- Abschnitt Inhaltsstoffe nur aus SDS/SDB oder Datasheet übernehmen, nicht erfinden.\n"
        "- seo_title: maximal 60 Zeichen.\n"
        "- seo_description: maximal 160 Zeichen.\n"
        "- slug: kleingeschrieben, Bindestriche, keine SKU.\n"
        "- Wenn ein Feld nicht belastbar aus den Assets ableitbar ist, gib leeren String zurück.\n\n"
        f"Textqualitätsoptionen: {', '.join(sorted(text_quality_options)) or 'keine'}.\n"
        "Wenn markdown/structure_sections aktiv ist, müssen Markdown-Überschriften und Listen mit Leerzeilen sauber getrennt sein.\n\n"
        "Antwortformat:\n"
        '{"title":"","short_description":"","description":"","seo_title":"","seo_description":"","slug":"","source_asset_ids":[],"notes":""}\n\n'
        "Asset-Kontext:\n\n"
        + "\n\n---\n\n".join(context_blocks)
    )
    payload = _call_openai_json(system_prompt, user_prompt, str(config["model"]), str(os.getenv("OPENAI_API_KEY") or "").strip())
    first_asset, first_kind, _text = selected_assets[0]
    source_ids = payload.get("source_asset_ids") if isinstance(payload.get("source_asset_ids"), list) else [first_asset.id]
    source_summary = _asset_source_summary(selected_assets, source_ids=source_ids)
    rows: list[dict[str, object]] = []
    for field in requested_fields:
        raw_value = payload.get(field, "")
        value = _trim_field(field, raw_value if isinstance(raw_value, str) else "")
        if field == "seo_title" and not value:
            fallback_title = (
                payload.get("title")
                or _current_field_value(product, "title", target_locale=target_locale)
                or product.title
                or product.sku
                or ""
            )
            value = _trim_field("seo_title", str(fallback_title))
        value = _apply_text_quality_to_field(field, value, text_quality_options=text_quality_options, target_locale=target_locale)
        if not value:
            continue
        rows.append(
            {
                "product_id": product.id,
                "sku": product.sku,
                "title": product.title,
                "field_name": field,
                "current_value": _current_field_value(product, field, target_locale=target_locale) or "",
                "suggested_value": value,
                "original_value": source_summary,
                "source_url": f"asset:{','.join(str(item) for item in source_ids)}",
                "source_domain": "Asset",
                "search_method": "asset_ai",
                "search_query": f"{first_kind} Asset {first_asset.id}",
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "confidence": 0.88 if first_kind == "datasheet" else 0.78,
                "status": "suggested",
                "source_language": target_locale,
                "target_locale": target_locale,
                "section_name": "Asset KI",
                "warning": str(payload.get("notes") or "").strip() or None,
            }
        )
    return rows


def _normalize_asset_ai_description(value: str, *, target_locale: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    labels = _asset_section_labels(target_locale)
    if "###" not in text:
        text = format_markdown_description(text)
    replacements = {
        "### How to use": f"### {labels['usage']}",
        "### Anwendung": f"### {labels['usage']}",
        "### Function": f"### {labels['function']}",
        "### Funktion": f"### {labels['function']}",
        "### Ingredients": f"### {labels['ingredients']}",
        "### Inhaltsstoffe": f"### {labels['ingredients']}",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _apply_text_quality_to_field(field: str, value: str, *, text_quality_options: set[str], target_locale: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "clean_supplier_text" in text_quality_options:
        text = _clean_supplier_generated_text(text)
    if field == "description":
        if "markdown" in text_quality_options or "structure_sections" in text_quality_options:
            text = _normalize_markdown_spacing(text, target_locale=target_locale)
        if "markdown" in text_quality_options and "###" not in text:
            text = format_markdown_description(text)
            text = _normalize_markdown_spacing(text, target_locale=target_locale)
    elif field in {"short_description", "seo_description", "seo_title", "title"}:
        text = re.sub(r"\s+", " ", text).strip()
    if field == "seo_title":
        text = _trim_field("seo_title", text)
    if field == "seo_description":
        text = _trim_field("seo_description", text)
    return text


def _normalize_markdown_spacing(text: str, *, target_locale: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"\s+(###\s+)", r"\n\n\1", value)
    labels = _asset_section_labels(target_locale)
    known_headings = {
        "Anwendung",
        "Funktion",
        "Inhaltsstoffe",
        "How to use",
        "Function",
        "Ingredients",
        labels["usage"],
        labels["function"],
        labels["ingredients"],
    }
    for heading in sorted(known_headings, key=len, reverse=True):
        value = re.sub(rf"###\s+{re.escape(heading)}\s+", f"### {heading}\n\n", value, flags=re.I)
    value = re.sub(r"(?<!\n)(\d+\.\s+)", r"\n\1", value)
    value = re.sub(r"(?<!\n)(-\s+)", r"\n\1", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = _normalize_asset_ai_description(value, target_locale=target_locale)
    return value.strip()


def _clean_supplier_generated_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\s+", " ", value).strip() if "\n" not in value else value.strip()
    value = value.replace("Spotting‑", "Spotting-").replace("Vor‑", "Vor-").replace("Nach‑", "Nach-")
    return value


def _suggestions_from_supplier_candidates(
    product: Product,
    fields: list[str],
    supplier_result: dict[str, object],
    *,
    target_locale: str,
    allow_source_language_fallback: bool = False,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    visible_fields = set(fields) | {
        "sku",
        "packaging",
        "specifications",
        "how_to_use",
        "quantity_for_use",
        "warning",
        "ingredients",
        "ingredient_search",
        "function",
    }
    source_language_fields = {
        str(candidate.get("field_name") or "")
        for candidate in supplier_result.get("candidates", [])
        if isinstance(candidate, dict)
        and str(candidate.get("field_name") or "") in TRANSLATABLE_FIELDS
        and (
            _language_base(str(candidate.get("target_locale") or "")) == _language_base(str(candidate.get("source_language") or ""))
            or not str(candidate.get("target_locale") or "").strip()
        )
        and (str(candidate.get("suggested_value") or "").strip() or str(candidate.get("source_value") or "").strip())
    }
    for candidate in supplier_result.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        field = str(candidate.get("field_name") or "")
        if field not in visible_fields or field in seen:
            continue
        source_value = str(candidate.get("source_value") or "").strip()
        suggested_value = str(candidate.get("suggested_value") or "").strip()
        if not source_value:
            continue
        status = str(candidate.get("status") or "suggested")
        warning = candidate.get("warning")
        candidate_target_locale = str(candidate.get("target_locale") or "").strip()
        use_source_locale = (
            allow_source_language_fallback
            and field in source_language_fields
            and (
                _language_base(candidate_target_locale) == _language_base(str(candidate.get("source_language") or ""))
                or not candidate_target_locale
            )
        )
        if use_source_locale:
            row_target_locale = candidate_target_locale or str(candidate.get("source_language") or "").strip()
            if not suggested_value:
                suggested_value = source_value
            if status in {"new", "needs_translation"}:
                status = "suggested"
        elif candidate_target_locale == target_locale:
            row_target_locale = target_locale
        else:
            continue
        if not suggested_value and status == "needs_translation" and allow_source_language_fallback:
            suggested_value = source_value
            status = "language_mismatch"
            language_note = (
                f"Quelle ist {candidate.get('source_language') or '-'}, Ziel ist {target_locale}; "
                "Originaltext wird angezeigt, aber nicht automatisch in eine abweichende Locale übernommen."
            )
            warning = f"{warning} {language_note}".strip() if warning else language_note
        rows.append(
            {
                "product_id": product.id,
                "sku": product.sku,
                "title": product.title,
                "field_name": field,
                "current_value": _current_field_value(product, field, target_locale=target_locale) or "",
                "suggested_value": suggested_value,
                "original_value": source_value,
                "source_url": candidate.get("source_url"),
                "source_domain": candidate.get("source_domain"),
                "search_method": f"{candidate.get('supplier_key') or 'supplier'}_extractor",
                "search_query": candidate.get("source_url"),
                "searched_at": datetime.now(timezone.utc).isoformat(),
                "confidence": candidate.get("confidence") or 0,
                "status": status if field in SUPPORTED_FIELDS else "candidate_only",
                "candidate_id": candidate.get("id"),
                "source_language": candidate.get("source_language"),
                "target_locale": row_target_locale,
                "section_name": candidate.get("section_name"),
                "warning": warning,
            }
        )
        seen.add(field)
    source_language = str(supplier_result.get("detected_language") or "")
    source_locale_target = source_language if allow_source_language_fallback and _language_base(source_language) else target_locale
    if "title" in fields and "title" not in seen:
        title_value = _trim_field("title", str(supplier_result.get("product_name") or ""))
        if title_value:
            rows.append(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "field_name": "title",
                    "current_value": _current_field_value(product, "title", target_locale=source_locale_target) or "",
                    "suggested_value": title_value,
                    "original_value": title_value,
                    "source_url": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "source_domain": (supplier_result.get("candidates") or [{}])[0].get("source_domain") if supplier_result.get("candidates") else None,
                    "search_method": f"{supplier_result.get('supplier_key') or 'supplier'}_extractor",
                    "search_query": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "searched_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.86,
                    "status": _preview_status_for_language(title_value, source_language, source_locale_target),
                    "candidate_id": None,
                    "source_language": source_language,
                    "target_locale": source_locale_target,
                    "section_name": "Title",
                    "warning": _preview_warning_for_language(title_value, source_language, source_locale_target),
                }
            )
            seen.add("title")
    if "seo_title" in fields and "seo_title" not in seen:
        seo_title = _trim_field("seo_title", str(supplier_result.get("product_name") or product.title or ""))
        if seo_title:
            rows.append(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "field_name": "seo_title",
                    "current_value": _current_field_value(product, "seo_title", target_locale=source_locale_target) or "",
                    "suggested_value": seo_title,
                    "original_value": seo_title,
                    "source_url": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "source_domain": (supplier_result.get("candidates") or [{}])[0].get("source_domain") if supplier_result.get("candidates") else None,
                    "search_method": f"{supplier_result.get('supplier_key') or 'supplier'}_extractor",
                    "search_query": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "searched_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.82,
                    "status": _preview_status_for_language(seo_title, source_language, source_locale_target),
                    "candidate_id": None,
                    "source_language": source_language,
                    "target_locale": source_locale_target,
                    "section_name": "SEO",
                    "warning": _preview_warning_for_language(seo_title, source_language, source_locale_target),
                }
            )
            seen.add("seo_title")
    if "seo_description" in fields and "seo_description" not in seen:
        seo_description = next(
            (
                str(row.get("suggested_value") or row.get("source_value") or "").strip()
                for row in rows
                if row.get("field_name") in {"short_description", "description"}
            ),
            "",
        )
        seo_description = _trim_field("seo_description", seo_description)
        if seo_description:
            rows.append(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "field_name": "seo_description",
                    "current_value": _current_field_value(product, "seo_description", target_locale=source_locale_target) or "",
                    "suggested_value": seo_description,
                    "original_value": seo_description,
                    "source_url": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "source_domain": (supplier_result.get("candidates") or [{}])[0].get("source_domain") if supplier_result.get("candidates") else None,
                    "search_method": f"{supplier_result.get('supplier_key') or 'supplier'}_extractor",
                    "search_query": (supplier_result.get("candidates") or [{}])[0].get("source_url") if supplier_result.get("candidates") else None,
                    "searched_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.82,
                    "status": _preview_status_for_language(seo_description, source_language, source_locale_target),
                    "candidate_id": None,
                    "source_language": source_language,
                    "target_locale": source_locale_target,
                    "section_name": "SEO",
                    "warning": _preview_warning_for_language(seo_description, source_language, source_locale_target),
                }
            )
            seen.add("seo_description")
    if "slug" in fields and "slug" not in seen:
        slug_source = _supplier_slug_source(supplier_result, product)
        slug_value = _slug_candidate(slug_source, product.sku)
        if slug_value:
            first_candidate = (supplier_result.get("candidates") or [{}])[0] if supplier_result.get("candidates") else {}
            rows.append(
                {
                    "product_id": product.id,
                    "sku": product.sku,
                    "title": product.title,
                    "field_name": "slug",
                    "current_value": _current_field_value(product, "slug", target_locale=source_locale_target) or "",
                    "suggested_value": slug_value,
                    "original_value": slug_source,
                    "source_url": first_candidate.get("source_url"),
                    "source_domain": first_candidate.get("source_domain"),
                    "search_method": f"{supplier_result.get('supplier_key') or 'supplier'}_extractor",
                    "search_query": first_candidate.get("source_url"),
                    "searched_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.86,
                    "status": "suggested",
                    "candidate_id": None,
                    "source_language": source_language,
                    "target_locale": source_locale_target,
                    "section_name": "Slug",
                    "warning": "Slug-Kandidat: erst nach expliziter Übernahme speichern; veröffentlichte URLs danach nicht automatisch ändern.",
                }
            )
            seen.add("slug")
    return rows


def _target_locales_for_product(product: Product, target_locale: str | None = None, detected_language: str | None = None) -> list[str]:
    preferred = [
        target_locale,
        product.source_language or "de-CH",
        detected_language,
        "de-CH",
        "fr-CH",
        "it-CH",
    ]
    result: list[str] = []
    for value in preferred:
        code = str(value or "").strip()
        if code and code not in result:
            result.append(code)
    return result


def _auto_translate_supplier_candidates() -> bool:
    return str(os.getenv("PIM_ENRICHMENT_AUTO_TRANSLATE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _language_base(value: str | None) -> str:
    return str(value or "").strip().split("-", 1)[0].lower()


def _detect_text_language(value: str | None) -> str | None:
    text = f" {str(value or '').strip().lower()} "
    if len(text.strip()) < 12:
        return None
    german_score = 0
    english_score = 0
    german_tokens = (
        " der ",
        " die ",
        " das ",
        " ist ",
        " und ",
        " mit ",
        " für ",
        " auf ",
        " ein ",
        " eine ",
        " einem ",
        " einer ",
        " hervorragend",
        " wasser",
        " flecken",
        " reinigung",
        " sortiment",
        " ausrüster",
        " beschreibung",
        " gebrauch",
    )
    english_tokens = (
        " the ",
        " and ",
        " with ",
        " for ",
        " to be ",
        " used ",
        " dry-cleaning",
        " pre-spotter",
        " stains",
        " fabrics",
        " spraying",
        " brush",
        " excellent",
        " water-based",
        " suitable",
        " ingredients",
        " warning",
    )
    german_score += sum(1 for token in german_tokens if token in text)
    english_score += sum(1 for token in english_tokens if token in text)
    if re.search(r"[äöüß]", text):
        german_score += 2
    if german_score >= english_score + 1 and german_score >= 2:
        return "de"
    if english_score >= german_score + 1 and english_score >= 2:
        return "en"
    return None


def _is_bad_seo_title(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    blocked_fragments = (
        "ausrüster der professionellen textilreinigung",
        "sortiment für profis",
        "home about",
        "mission and values",
        "quality brands certifications",
    )
    if any(fragment in text for fragment in blocked_fragments):
        return True
    return text.count(".") >= 2 and len(text) > 80


def _preview_status_for_language(value: str, source_language: str | None, target_locale: str) -> str:
    reason = _language_mismatch_reason({"field_name": "description", "source_language": source_language}, value, target_locale)
    return "language_mismatch" if reason else "suggested"


def _preview_warning_for_language(value: str, source_language: str | None, target_locale: str) -> str | None:
    return _language_mismatch_reason({"field_name": "description", "source_language": source_language}, value, target_locale)


def _slug_candidate(value: str | None, sku: str | None = None) -> str:
    source = str(value or "").strip()
    if not source:
        source = str(sku or "product").strip()
    source = re.sub(r"\b[A-Z]\d{2}-\d{3}[A-Z0-9]*\b", "", source, flags=re.I)
    source = re.sub(r"\s*[-|]\s*Tintolav.*$", "", source, flags=re.I).strip()
    slug = slugify(source, separator="-") or slugify(str(sku or "product"), separator="-") or "product"
    return slug[:180].strip("-") or "product"


def _supplier_slug_source(supplier_result: dict[str, Any], product: Product) -> str:
    product_name = str(supplier_result.get("product_name") or product.title or product.sku or "").strip()
    function_value = ""
    for candidate in supplier_result.get("candidates") or []:
        if str(candidate.get("field_name") or "").strip().lower() == "function":
            function_value = str(candidate.get("suggested_value") or candidate.get("source_value") or "").strip()
            break
    if function_value and function_value.lower() not in product_name.lower():
        return f"{product_name} {function_value}"
    url_slug = _slug_source_from_url(str(supplier_result.get("source_url") or ""))
    if url_slug:
        return url_slug
    return product_name


def _slug_source_from_url(source_url: str) -> str:
    path = unquote(urlparse(source_url).path or "")
    last = path.rsplit("/", 1)[-1]
    last = re.sub(r"\.html?$", "", last, flags=re.I).strip("-_ ")
    if not last or last.lower() in {"product", "products"}:
        return ""
    return last.replace("-", " ").replace("_", " ")


def _unique_product_handle(session: Session, desired: str, product_id: int | None = None) -> str:
    base_handle = _slug_candidate(desired)
    handle = base_handle
    suffix = 2
    while True:
        stmt = select(Product).where(Product.handle == handle)
        if product_id is not None:
            stmt = stmt.where(Product.id != product_id)
        existing = session.scalar(stmt)
        if existing is None:
            return handle
        handle = f"{base_handle}-{suffix}"
        suffix += 1


def _result(
    product: Product,
    status: str,
    fields: tuple[str, ...],
    missing_fields: list[str],
    sources_checked: list[dict[str, object]],
    suggestions: list[dict[str, object]],
    warnings: list[str],
    errors: list[str] | None = None,
) -> dict[str, object]:
    return {
        "product_id": product.id,
        "sku": product.sku,
        "title": product.title,
        "status": status,
        "searched_fields": list(fields),
        "missing_fields": missing_fields,
        "sources_checked": sources_checked,
        "suggestions": suggestions,
        "warnings": warnings,
        "errors": errors or [],
    }


def _load_product(session: Session, product_id: int) -> Product | None:
    return session.scalar(
        select(Product)
        .options(joinedload(Product.brand), joinedload(Product.translations))
        .where(Product.id == product_id)
    )


def _candidate_urls(product: Product, source_modes: set[str]) -> list[tuple[str, str, str]]:
    urls: list[tuple[str, str, str]] = []
    use_known_urls = "configured_domains" in source_modes
    if ("final_url" in source_modes or use_known_urls) and product.source_url_final:
        urls.extend((url, "final_url", url) for url in _split_source_urls(product.source_url_final))
    if ("source_url" in source_modes or use_known_urls) and product.source_url:
        urls.extend((url, "source_url", url) for url in _split_source_urls(product.source_url))
    # Ohne Search-API werden konfigurierte Domains nur als dokumentierte Suchhinweise genutzt, nicht blind gecrawlt.
    return _dedupe_url_tuples(urls)


def _search_configured_domain(query: str, *, max_results: int) -> list[SearchResult]:
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    response = requests.get(search_url, timeout=12, headers={"User-Agent": "PIM-PAM-Enrichment/1.0"})
    response.raise_for_status()
    html_text = response.text
    titles = re.findall(r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, flags=re.I | re.S)
    snippets = [
        _clean_text(snippet) or ""
        for snippet in re.findall(r'<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>|<div[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</div>', html_text, flags=re.I | re.S)
        for snippet in (snippet if isinstance(snippet, tuple) else (snippet,))
        if snippet
    ]
    results: list[SearchResult] = []
    expected_domain = _domain_from_site_query(query)
    for index, (raw_url, raw_title) in enumerate(titles):
        url = _unwrap_search_url(raw_url)
        if not url.startswith(("http://", "https://")):
            continue
        if expected_domain and expected_domain not in urlparse(url).netloc:
            continue
        title = _clean_text(raw_title)
        snippet = snippets[index] if index < len(snippets) else None
        results.append(SearchResult(url=url, title=title, snippet=snippet, query=query))
        if len(results) >= max_results:
            break
    return results


def _unwrap_search_url(raw_url: str) -> str:
    url = html.unescape(raw_url)
    parsed = urlparse(url)
    if parsed.query:
        query_values = parse_qs(parsed.query)
        if query_values.get("uddg"):
            return unquote(query_values["uddg"][0])
    return url


def _domain_from_site_query(query: str) -> str | None:
    match = re.search(r"site:([^\s]+)", query)
    return match.group(1).strip().lower() if match else None


def _extract_from_search_result(search_result: SearchResult, product: Product) -> PageExtract | None:
    snippet = _clean_text(search_result.snippet)
    if not snippet or not _is_product_relevant_text(product, snippet):
        return None
    return PageExtract(
        url=search_result.url,
        title=search_result.title,
        meta_description=snippet,
        h1=search_result.title,
        paragraphs=(snippet,),
        search_method="configured_domain_search",
        search_query=search_result.query,
    )


def _split_source_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        part.strip()
        for part in re.split(r"[\n\r,;]+", str(value))
        if part.strip().startswith(("http://", "https://"))
    ]


def _fetch_and_extract(url: str, *, search_method: str = "url", search_query: str | None = None) -> PageExtract:
    response = requests.get(url, timeout=12, headers={"User-Agent": "PIM-PAM-Enrichment/1.0"})
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "<html" not in response.text[:500].lower():
        raise ValueError("Quelle ist keine HTML-Produktseite")
    text = response.text
    title = _first_match(text, r"<title[^>]*>(.*?)</title>")
    meta_description = _meta_content(text, "description") or _meta_property(text, "og:description")
    h1 = _first_match(text, r"<h1[^>]*>(.*?)</h1>")
    body = _strip_scripts(text)
    paragraph_candidates = _description_block_candidates(body)
    seen_candidates = set(paragraph_candidates)
    paragraph_candidates.extend(
        row
        for row in (_clean_text(match) for match in re.findall(r"<p[^>]*>(.*?)</p>", body, flags=re.I | re.S))
        if row and len(row) >= 40 and row not in seen_candidates and not _is_boilerplate_text(row)
    )
    json_ld_description = _json_ld_description(text)
    if json_ld_description and not _is_boilerplate_text(json_ld_description):
        paragraph_candidates.insert(0, json_ld_description)
    for body_candidate in _body_text_candidates(body):
        if paragraph_candidates and len(body_candidate) > 600:
            continue
        if body_candidate not in paragraph_candidates and not _is_boilerplate_text(body_candidate):
            paragraph_candidates.append(body_candidate)
    paragraphs = tuple(paragraph_candidates)
    warnings = []
    if not paragraphs and not meta_description:
        warnings.append("Quelle geladen, aber keine verwertbaren Produkttexte gefunden.")
    return PageExtract(
        url=url,
        title=_clean_text(title),
        meta_description=_clean_text(meta_description),
        h1=_clean_text(h1),
        paragraphs=paragraphs[:8],
        search_method=search_method,
        search_query=search_query,
        warnings=tuple(warnings),
    )


def _suggest_field(product: Product, field: str, extracts: list[PageExtract]) -> dict[str, object] | None:
    for index, extract in enumerate(extracts):
        base_confidence = Decimal("0.88") if index == 0 else Decimal("0.76")
        if field == "title":
            value = extract.h1 or extract.title
        elif field == "short_description":
            value = _summary_text(product, extract)
        elif field == "description":
            value = _description_text(product, extract) or extract.meta_description
        elif field == "seo_title":
            value = extract.title or extract.h1 or product.title
        elif field == "seo_description":
            value = _summary_text(product, extract)
        elif field == "slug":
            value = _slug_candidate(extract.h1 or extract.title or product.title, product.sku)
        elif field in {"technical_features_text", "specifications_text"}:
            value = _technical_text(extract) or "\n\n".join(extract.paragraphs[:4])
        elif field == "source_url_final":
            value = extract.url
        else:
            value = None
        value = _trim_field(field, value)
        if not value:
            continue
        if field == "seo_title" and _is_bad_seo_title(value):
            continue
        if field not in {"seo_title", "source_url_final", "slug"} and not (
            _is_product_relevant_text(product, value) or _is_likely_product_description_text(value)
        ):
            continue
        confidence = base_confidence
        if _contains_sku_or_title(product, extract):
            confidence += Decimal("0.04")
        confidence = min(confidence, Decimal("0.95"))
        source_language = _detect_text_language(value)
        target_locale = product.source_language or "de-CH"
        return {
            "product_id": product.id,
            "sku": product.sku,
            "title": product.title,
            "field_name": field,
            "current_value": _current_field_value(product, field) or "",
            "suggested_value": value,
            "source_url": extract.url,
            "source_domain": urlparse(extract.url).netloc,
            "search_method": extract.search_method,
            "search_query": extract.search_query or extract.url,
            "searched_at": datetime.now(timezone.utc).isoformat(),
            "confidence": float(confidence),
            "status": _preview_status_for_language(value, source_language, target_locale),
            "source_language": source_language,
            "target_locale": target_locale,
            "warning": _preview_warning_for_language(value, source_language, target_locale),
        }
    return None


TRANSLATABLE_FIELDS = {"title", "short_description", "description", "seo_title", "seo_description", "slug"}
LANGUAGE_VALIDATED_FIELDS = {"title", "short_description", "description", "seo_title", "seo_description"}
BASE_PRODUCT_FIELDS = {"title", "description", "technical_features_text", "specifications_text", "source_url_final"}


def _current_field_value(product: Product, field_name: str, *, target_locale: str | None = None) -> str | None:
    language = (target_locale or product.source_language or "de-CH").strip()
    if field_name == "slug":
        translation = _translation_for_locale(product, language)
        if language == (product.source_language or "de-CH"):
            return product.handle or (translation.slug if translation is not None else None)
        return translation.slug if translation is not None else None
    if field_name in TRANSLATABLE_FIELDS and language != (product.source_language or "de-CH"):
        translation = _translation_for_locale(product, language)
        return getattr(translation, field_name, None) if translation is not None else None
    translation = _source_translation(product)
    if field_name in BASE_PRODUCT_FIELDS:
        return getattr(product, field_name, None)
    if translation is None:
        return None
    return getattr(translation, field_name, None)


def _set_product_field(session: Session, product: Product, field_name: str, value: str, *, target_locale: str | None = None) -> None:
    language = (target_locale or product.source_language or "de-CH").strip()
    if field_name == "slug":
        slug_value = _unique_product_handle(session, value, product.id) if language == (product.source_language or "de-CH") else _slug_candidate(value, product.sku)
        translation = _get_or_create_translation(session, product, language)
        translation.slug = slug_value
        if language == (product.source_language or "de-CH"):
            product.handle = slug_value
        return
    if field_name in TRANSLATABLE_FIELDS and language != (product.source_language or "de-CH"):
        translation = _get_or_create_translation(session, product, language)
        setattr(translation, field_name, value)
        return
    if field_name in BASE_PRODUCT_FIELDS:
        if language != (product.source_language or "de-CH"):
            raise ValueError(f"{field_name} kann nicht in {language} gespeichert werden; Feld ist kein separates Übersetzungsfeld.")
        setattr(product, field_name, value)
        if field_name in TRANSLATABLE_FIELDS:
            translation = _get_or_create_translation(session, product, language)
            setattr(translation, field_name, value)
        return
    translation = _get_or_create_translation(session, product, language)
    setattr(translation, field_name, value)


def _source_translation(product: Product) -> ProductTranslation | None:
    language = product.source_language or "de-CH"
    return _translation_for_locale(product, language)


def _translation_for_locale(product: Product, language: str) -> ProductTranslation | None:
    return next((row for row in product.translations if row.language_code == language), None)


def _get_or_create_translation(session: Session, product: Product, language: str) -> ProductTranslation:
    translation = session.scalar(
        select(ProductTranslation).where(
            ProductTranslation.product_id == product.id,
            ProductTranslation.language_code == language,
        )
    )
    if translation is None:
        translation = ProductTranslation(
            product_id=product.id,
            language_code=language,
            title=product.title,
            translation_status="reviewed",
            source_language_code=product.source_language,
        )
        session.add(translation)
        session.flush()
    return translation


def _log_suggestion(
    session: Session,
    product: Product,
    field_name: str,
    old_value: str | None,
    new_value: str,
    suggestion: dict[str, object],
    *,
    status: str,
) -> None:
    session.add(
        ProductEnrichmentLog(
            product_id=product.id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            source_url=str(suggestion.get("source_url") or "") or None,
            source_domain=str(suggestion.get("source_domain") or "") or None,
            search_query=str(suggestion.get("search_query") or "") or None,
            search_method=str(suggestion.get("search_method") or "") or None,
            confidence=Decimal(str(suggestion.get("confidence") or "0")),
            status=status,
            error_message=str(suggestion.get("error_message") or "") or None,
            dry_run=status == "suggested",
            language_code=str(suggestion.get("target_locale") or product.source_language or "") or None,
            created_by=str(suggestion.get("created_by") or "") or None,
        )
    )


def _log_error(
    session: Session,
    product: Product,
    field_name: str,
    source_url: str | None,
    search_method: str,
    search_query: str | None,
    error_message: str,
) -> None:
    session.add(
        ProductEnrichmentLog(
            product_id=product.id,
            field_name=field_name,
            old_value=_current_field_value(product, field_name),
            new_value=None,
            source_url=source_url,
            source_domain=urlparse(source_url).netloc if source_url else None,
            search_query=search_query,
            search_method=search_method,
            confidence=Decimal("0"),
            status="error",
            error_message=error_message,
            dry_run=True,
            language_code=product.source_language,
        )
    )


def _normalize_fields(fields: list[str] | None) -> tuple[str, ...]:
    selected = tuple(field for field in (fields or list(SUPPORTED_FIELDS)) if field in SUPPORTED_FIELDS)
    return selected or SUPPORTED_FIELDS


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        normalized = str(url or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _dedupe_url_tuples(urls: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str, str]] = []
    for url, method, query in urls:
        normalized = str(url or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((normalized, method, query))
    return result


def _first_paragraph(extract: PageExtract) -> str | None:
    return extract.paragraphs[0] if extract.paragraphs else None


def _description_text(product: Product, extract: PageExtract) -> str | None:
    first = _first_paragraph(extract)
    if first and _is_likely_product_description_text(first):
        return first
    relevant = [
        row
        for row in extract.paragraphs
        if _is_product_relevant_text(product, row) and not _is_boilerplate_text(row)
    ]
    if not relevant:
        return None
    first = relevant[0]
    if len(first) >= 120:
        return first
    return "\n\n".join(relevant[:4])


def _summary_text(product: Product, extract: PageExtract) -> str | None:
    candidates = [
        extract.meta_description,
        _first_paragraph(extract),
        _description_text(product, extract),
    ]
    for candidate in candidates:
        cleaned = _trim_field("short_description", candidate)
        if cleaned and (_is_product_relevant_text(product, cleaned) or _is_likely_product_description_text(cleaned)):
            return cleaned
    return None


def _trim_field(field: str, value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    limit = 500 if field in {"title", "seo_title", "source_url_final"} else 320 if field in {"short_description", "seo_description"} else 4000
    return cleaned[:limit].rstrip()


def _technical_text(extract: PageExtract) -> str | None:
    keywords = (
        "technische daten",
        "spezifikation",
        "eigenschaften",
        "anwendung",
        "dosierung",
        "gebrauchsanweisung",
        "specifications",
        "how to use",
        "ingredients",
        "function",
    )
    rows = [row for row in extract.paragraphs if any(keyword in row.lower() for keyword in keywords)]
    return "\n\n".join(rows[:4]) if rows else None


def _description_block_candidates(body: str) -> list[str]:
    candidates = _tintolav_product_blocks(body)
    class_patterns = (
        "short-description",
        "main-description",
        "product-description",
        "description",
        "product-details",
    )
    for class_name in class_patterns:
        pattern = rf"<(?:div|section|article)[^>]+class=[\"'][^\"']*{re.escape(class_name)}[^\"']*[\"'][^>]*>(.*?)</(?:div|section|article)>"
        for match in re.findall(pattern, body, flags=re.I | re.S):
            cleaned = _clean_text(match)
            if cleaned and len(cleaned) >= 30 and not _is_boilerplate_text(cleaned) and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def _tintolav_product_blocks(body: str) -> list[str]:
    candidates: list[str] = []
    description = _first_match(
        body,
        r"<div[^>]+id=[\"']dacshop_product_description_main[\"'][^>]*>(.*?)</div>",
    )
    cleaned_description = _clean_text(description)
    if cleaned_description:
        cleaned_description = re.sub(r"^Description\s+", "", cleaned_description, flags=re.I).strip()
    if cleaned_description and len(cleaned_description) >= 30 and not _is_boilerplate_text(cleaned_description):
        candidates.append(cleaned_description)

    custom_info = _first_match(
        body,
        r"<div[^>]+id=[\"']dacshop_product_custom_info_main[\"'][^>]*>(.*?)</div>\s*</div>\s*<div[^>]+id=[\"']dacshop_product_files_main",
    )
    if custom_info:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", custom_info, flags=re.I | re.S)
        spec_lines: list[str] = []
        for row in rows:
            label = _clean_text(_first_match(row, r"<label[^>]*>(.*?)</label>"))
            value = _clean_text(_first_match(row, r"<td[^>]*>\s*<span[^>]+class=[\"'][^\"']*dacshop_product_custom_value[^\"']*[\"'][^>]*>(.*?)</span>\s*</td>"))
            if label and value and not _is_boilerplate_text(value):
                spec_lines.append(f"{label}: {value}")
        if spec_lines:
            candidates.append("Specifications\n" + "\n".join(spec_lines))
    return candidates


def _diagnostic_search_queries(product: Product) -> list[str]:
    terms = [
        product.sku,
        product.title,
        f"{product.sku} {product.title}",
        f"{product.title} {product.brand.name}" if product.brand else "",
    ]
    result: list[str] = []
    for domain in DEFAULT_DOMAINS:
        for term in terms:
            cleaned = str(term or "").strip()
            if cleaned:
                result.append(f'site:{domain} "{cleaned}"')
    return result


def _contains_sku_or_title(product: Product, extract: PageExtract) -> bool:
    haystack = " ".join(row for row in [extract.title, extract.h1, extract.meta_description, *extract.paragraphs] if row).lower()
    sku = re.sub(r"[^a-z0-9]+", "", product.sku.lower())
    title_words = [word for word in re.findall(r"[a-z0-9äöüß]+", product.title.lower()) if len(word) >= 4]
    return (sku and sku in re.sub(r"[^a-z0-9]+", "", haystack)) or sum(1 for word in title_words if word in haystack) >= 2


def _has_relevant_extract(product: Product, extracts: list[PageExtract]) -> bool:
    for extract in extracts:
        values = [extract.meta_description, extract.h1, *extract.paragraphs]
        if any(value and _is_product_relevant_text(product, value) for value in values):
            return True
    return False


def _is_product_relevant_text(product: Product, value: str) -> bool:
    normalized = value.lower()
    if _is_boilerplate_text(value):
        return False
    sku = re.sub(r"[^a-z0-9]+", "", product.sku.lower())
    if sku and sku in re.sub(r"[^a-z0-9]+", "", normalized):
        return True
    title_words = [word for word in re.findall(r"[a-z0-9äöüß]+", product.title.lower()) if len(word) >= 4]
    return sum(1 for word in title_words if word in normalized) >= 1


def _is_likely_product_description_text(value: str | None) -> bool:
    text = (value or "").strip()
    lowered = text.lower()
    if len(text) < 60 or _is_boilerplate_text(text):
        return False
    navigation_markers = (" home ", " about ", " back ", " mission ", " certifications ", " showcase ", " products ")
    if sum(1 for marker in navigation_markers if marker in f" {lowered} ") >= 2:
        return False
    product_markers = (
        "pre-spotter",
        "dry-cleaning",
        "water-based stains",
        "fabrics",
        "detergent",
        "cleaning",
        "description",
        "wirksamkeit",
        "flecken",
        "textil",
        "reinigung",
        "kleidungsstücke",
    )
    return any(marker in lowered for marker in product_markers)


def _is_boilerplate_text(value: str | None) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return True
    boilerplate_markers = (
        "javascript scheint in ihrem browser deaktiviert",
        "javascript in ihrem browser aktivieren",
        "another custom cms block",
        "custom cms block displayed as a tab",
        "enable javascript",
        "cookie",
        "newsletter",
        "warenkorb",
        "mein konto",
        "zur kasse",
        "datenschutz",
        "agb",
        "lorem ipsum",
        "handelsregister",
        "mwst-nr",
    )
    marker_hits = sum(1 for marker in boilerplate_markers if marker in text)
    if marker_hits >= 1 and len(text) < 700:
        return True
    if marker_hits >= 2:
        return True
    return False


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.I | re.S)
    return match.group(1) if match else None


def _meta_content(text: str, name: str) -> str | None:
    pattern = rf"<meta[^>]+name=[\"']{re.escape(name)}[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>"
    return _first_match(text, pattern)


def _meta_property(text: str, prop: str) -> str | None:
    pattern = rf"<meta[^>]+property=[\"']{re.escape(prop)}[\"'][^>]+content=[\"'](.*?)[\"'][^>]*>"
    return _first_match(text, pattern)


def _strip_scripts(text: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    return re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)


def _json_ld_description(text: str) -> str | None:
    for raw in re.findall(r"<script[^>]+type=[\"']application/ld\\+json[\"'][^>]*>(.*?)</script>", text, flags=re.I | re.S):
        cleaned = html.unescape(raw).strip()
        if not cleaned:
            continue
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            continue
        description = _find_json_description(payload)
        if description:
            return _clean_text(str(description))
    return None


def _find_json_description(payload: object) -> str | None:
    if isinstance(payload, dict):
        if str(payload.get("@type") or "").lower() == "product" and payload.get("description"):
            return str(payload["description"])
        if payload.get("description") and any(key in payload for key in ("sku", "offers", "brand", "name")):
            return str(payload["description"])
        for value in payload.values():
            found = _find_json_description(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_json_description(item)
            if found:
                return found
    return None


def _body_text_candidates(body: str) -> list[str]:
    visible = _clean_text(body) or ""
    if not visible:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", visible)
    candidates: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if not sentence:
            continue
        current.append(sentence)
        joined = " ".join(current).strip()
        if len(joined) >= 120:
            candidates.append(joined)
            current = []
        if len(candidates) >= 3:
            break
    if not candidates and len(visible) >= 80:
        candidates.append(visible[:800].strip())
    return candidates


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None
