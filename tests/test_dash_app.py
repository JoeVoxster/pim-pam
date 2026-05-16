import base64

from app.ui.dash_app import (
    _channel_bulk_category_dropdown_state,
    _dedupe_group_selection_state,
    _dedupe_select_group_rows,
    create_dash_app,
    save_uploaded_import_file,
)


def test_save_uploaded_import_file_writes_csv(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(tmp_path / "assets"))
    encoded = base64.b64encode(b"sku,title\nA-1,Demo\n").decode("ascii")

    saved_path = save_uploaded_import_file(f"data:text/csv;base64,{encoded}", "products_clean.csv")

    assert saved_path.exists()
    assert saved_path.read_text(encoding="utf-8") == "sku,title\nA-1,Demo\n"
    assert saved_path.parent == tmp_path / "import_uploads"


def test_save_uploaded_import_file_rejects_unknown_suffix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(tmp_path / "assets"))
    encoded = base64.b64encode(b"demo").decode("ascii")

    try:
        save_uploaded_import_file(f"data:text/plain;base64,{encoded}", "products_clean.txt")
    except ValueError as exc:
        assert "csv" in str(exc).lower() or "xlsx" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for unsupported suffix")


def test_dash_app_contains_combined_enrichment_button() -> None:
    app = create_dash_app()
    layout_repr = repr(app.layout)

    assert "enrich-selected-run-button" in layout_repr
    assert "selected-product-ids" in layout_repr
    assert "selected-variant-ids" in layout_repr
    assert "product-channel-actions" in layout_repr
    assert "variant-channel-actions" in layout_repr
    assert "variant-list-status-filter" in layout_repr
    assert "Ausgewählte Varianten archivieren" in layout_repr
    assert "variant-delete-confirm" in layout_repr
    assert "product-channel-include-variants" in layout_repr
    assert "Zugehörige Varianten ebenfalls auswählen" in layout_repr
    assert "Kanal-Aktionen" in layout_repr
    assert "Produkt-Listings" in layout_repr
    assert "Kanal-Kategorien" in layout_repr
    assert "Varianten-Listings" in layout_repr
    assert "ProductPhotoCell" in layout_repr
    assert "photo_asset_id" in layout_repr
    assert "channel-bulk-modal" in layout_repr
    assert "channel-bulk-confirm" in layout_repr
    assert "channel-bulk-action" in layout_repr
    assert "channel-bulk-sales-channel-id" in layout_repr
    assert "channel-bulk-channel-category-id" in layout_repr
    assert "channel-bulk-category-status" in layout_repr
    assert "product-select-all-button" in layout_repr
    assert "product-select-filtered-button" in layout_repr
    assert "product-select-page-button" in layout_repr
    assert "variant-select-all-button" in layout_repr
    assert "variant-select-filtered-button" in layout_repr
    assert "variant-select-page-button" in layout_repr
    assert "product-enrich-resolver-mode" in layout_repr
    assert "variant-enrich-resolver-mode" in layout_repr
    assert "enrich-resolver-mode" in layout_repr
    assert "product-enrich-status" in layout_repr
    assert "variant-enrich-status" in layout_repr
    assert "enrich-status" in layout_repr
    assert "nav-sales-channels" in layout_repr
    assert "nav-channel-categories" in layout_repr
    assert "categories-sales-channel-code" in layout_repr
    assert "product-category-channel-code" in layout_repr
    assert "category-products-grid" in layout_repr
    assert "category-products-status" in layout_repr
    assert "Externe Kanal-Kategorien" in layout_repr
    assert "product-detail-channel-listings" in layout_repr
    assert "product-detail-category-mappings" in layout_repr
    assert "product-detail-variant-channel-listings" in layout_repr
    assert "product-detail-variant-category-mappings" in layout_repr
    assert "Produktdetail" not in layout_repr
    assert "Beschreibung" in layout_repr
    assert "product-short-description" in layout_repr
    assert "product-description" in layout_repr
    assert "product-source-url" in layout_repr
    assert "product-source-url-final" in layout_repr
    assert "product-list-status-filter" in layout_repr
    assert "Archivierte Produkte" in layout_repr
    assert "channel-export-run-button" in layout_repr
    assert "channel-export-code" in layout_repr
    assert "channel-export-result" in layout_repr
    assert "import-sales-channel-code" in layout_repr
    assert "sales-channels-grid" in layout_repr
    assert "channel-categories-grid" in layout_repr
    assert "channel-category-tree-sales-channel-id" in layout_repr
    assert "channel-category-tree-grid" in layout_repr
    assert "channel-category-products-grid" in layout_repr
    assert "channel-category-tree-expand-all-button" in layout_repr
    assert "channel-category-tree-collapse-all-button" in layout_repr
    assert "translation-short-description" in layout_repr
    assert "translation-seo-title" in layout_repr
    assert "translation-seo-description" in layout_repr
    assert "translation-slug" in layout_repr
    assert "translation-include-variants" in layout_repr
    assert "Zugehörige Varianten mitübersetzen" in layout_repr
    assert "product-detail-variant-translations" in layout_repr
    assert "product-translation-open-button" in layout_repr
    assert "product-translation-prompts-button" in layout_repr
    assert "product-data-enrichment-open-button" in layout_repr
    assert "Fehlende Produktdaten anreichern" in layout_repr
    assert "product-asset-enrichment-run-button" in layout_repr
    assert "Fehlende Produkt-Assets anreichern" in layout_repr
    assert "product-asset-enrichment-status" in layout_repr
    assert "medusa-product-selection-mode" in layout_repr
    assert "Markierte Produkte aus Produktliste" in layout_repr
    assert "Nur Produkte ohne Medusa-ID" in layout_repr
    assert "medusa-product-limit" in layout_repr
    assert "product-medusa-export-language" not in layout_repr
    assert "product-medusa-export-button" not in layout_repr
    assert "product-medusa-export-result" not in layout_repr
    assert "Medusa-Export für markierte Produkte" not in layout_repr
    assert "product-data-enrichment-modal" in layout_repr
    assert "product-data-enrichment-suggestions-grid" in layout_repr
    assert "product-is-chemical" in layout_repr
    assert "product-detail-variant-archive-button" in layout_repr
    assert "product-detail-variant-delete-button" in layout_repr
    assert "product-detail-variant-delete-confirm" in layout_repr
    assert "product-detail-variant-status-filter" in layout_repr
    assert "rules-product-enrichment-preview-button" in layout_repr
    assert "rules-product-enrichment-suggestions-grid" in layout_repr
    assert "Alle sicheren Vorschläge übernehmen" in layout_repr
    assert "translation-bulk-modal" in layout_repr
    assert "translation-prompt-modal" in layout_repr
    assert "languages-grid" in layout_repr
    assert "variant-translation-variant-id" in layout_repr
    assert "variant-translation-save-button" in layout_repr
    assert "selected-asset-ids" in layout_repr
    assert "assets-bulk-actions" in layout_repr
    assert "assets-bulk-delete-confirm" in layout_repr
    assert "assets-select-visible-button" in layout_repr
    assert "assets-deselect-visible-button" in layout_repr
    assert "assets-visible-delete-button" in layout_repr
    assert "Markierte Assets löschen" in layout_repr
    assert "assets-clear-selection-button" in layout_repr
    assert "assets-send-to-uploader-button" in layout_repr
    assert "Ausgewählte lokale Assets nach R2 hochladen" in layout_repr
    assert "Upload zu Object Storage starten" in layout_repr
    assert "Bunny Storage" in layout_repr
    assert "assets-bulk-type-button" in layout_repr
    assert "r2-config-toggle-button" in layout_repr
    assert "R2-Speicher Conf" in layout_repr
    assert "r2-config-enabled" in layout_repr
    assert "r2-config-save-button" in layout_repr
    assert "r2-config-test-button" in layout_repr
    assert "Secret Access Key setzen" in layout_repr
    assert "chemistry-sdb-documents-grid" in layout_repr
    assert "multiRow" in layout_repr
    assert "chemistry-sdb-translation-warning" in layout_repr
    assert "chemistry-sdb-translation-generate-button" in layout_repr
    assert "chemistry-sdb-prompts-grid" in layout_repr
    assert "chemistry-sdb-prompt-new-button" in layout_repr
    assert "chemistry-sdb-document-edit-text" in layout_repr
    assert "chemistry-sdb-document-save-button" in layout_repr
    assert "chemistry-sdb-llm-quality-mode" in layout_repr
    assert "Sehr gründlich" in layout_repr
    assert "chemistry-sdb-document-delete-button" in layout_repr
    assert "Gewählte Version löschen" in layout_repr
    assert "chemistry-sdb-review-issues-grid" in layout_repr
    assert "chemistry-adr-pictograms" in layout_repr
    assert "chemistry-symbol-preview" in layout_repr
    assert "ADR Klasse 8" in layout_repr
    assert "GHS05 · Ätzend" in layout_repr
    assert "Sicherheitsdatenblätter sind rechtlich relevante Dokumente" in layout_repr
    assert "nav-dedupe" in layout_repr
    assert "Dubletten / Produkt-Merge" in layout_repr
    assert "dedupe-groups-grid" in layout_repr
    assert "dedupe-preview-button" in layout_repr
    assert "dedupe-merge-confirm" in layout_repr
    assert "dedupe-set-master-button" in layout_repr
    assert "dedupe-select-group-button" in layout_repr
    assert "dedupe-deselect-group-button" in layout_repr
    assert "Alle in dieser Gruppe auswählen" in layout_repr
    assert "dedupe-group-selection-status" in layout_repr


def test_dedupe_group_select_marks_all_products() -> None:
    group_rows = [{"product_id": 1, "title": "Master"}, {"product_id": 2, "title": "Dublette"}]

    selected = _dedupe_select_group_rows(group_rows, [], selected=True)

    assert [row["product_id"] for row in selected] == [1, 2]
    assert _dedupe_group_selection_state(group_rows, selected) == "all"


def test_channel_bulk_category_dropdown_filters_selected_channel() -> None:
    snapshot = {
        "sales_channels": [{"id": 1, "code": "voxster", "name": "voxster.ch"}],
        "channel_category_options": [
            {"label": "voxster · Reiniger · 10", "value": 10, "sales_channel_id": "1"},
            {"label": "other · Archiv · 20", "value": 20, "sales_channel_id": 2},
        ],
    }

    options, value, disabled, message = _channel_bulk_category_dropdown_state(1, snapshot)

    assert options == [{"label": "voxster · Reiniger · 10", "value": 10, "sales_channel_id": "1"}]
    assert value is None
    assert disabled is False
    assert "1 Kanal-Kategorien verfügbar" in message


def test_channel_bulk_category_dropdown_reports_empty_channel() -> None:
    snapshot = {
        "sales_channels": [{"id": 1, "code": "voxster", "name": "voxster.ch"}],
        "channel_category_options": [],
    }

    options, value, disabled, message = _channel_bulk_category_dropdown_state(1, snapshot)

    assert options == []
    assert value is None
    assert disabled is True
    assert "Keine Kanal-Kategorien für voxster.ch (voxster) gefunden" in message


def test_channel_bulk_category_dropdown_handles_invalid_channel_id() -> None:
    options, value, disabled, message = _channel_bulk_category_dropdown_state("bad", {})

    assert options == []
    assert value is None
    assert disabled is True
    assert "ungültige Vertriebskanal-ID" in message


def test_dedupe_group_deselect_removes_only_group_products() -> None:
    group_rows = [{"product_id": 1}, {"product_id": 2}]
    selected_rows = [{"product_id": 1}, {"product_id": 2}, {"product_id": 99}]

    selected = _dedupe_select_group_rows(group_rows, selected_rows, selected=False)

    assert [row["product_id"] for row in selected] == [99]
    assert _dedupe_group_selection_state(group_rows, selected) == "none"


def test_dedupe_group_partial_state_from_single_row_selection() -> None:
    group_rows = [{"product_id": 1}, {"product_id": 2}, {"product_id": 3}]

    assert _dedupe_group_selection_state(group_rows, [{"product_id": 2}]) == "partial"


def test_dedupe_group_selection_keeps_other_group_selections() -> None:
    group_a = [{"product_id": 1}, {"product_id": 2}]
    selected_rows = [{"product_id": 20}]

    selected = _dedupe_select_group_rows(group_a, selected_rows, selected=True)

    assert [row["product_id"] for row in selected] == [20, 1, 2]
