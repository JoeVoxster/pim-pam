from pathlib import Path

import pandas as pd

from app.io.readers import _clean_string, list_excel_sheet_items, list_excel_sheets, read_products, resolve_excel_sheet_name, resolve_excel_sheet_selector


def test_clean_string_treats_nan_like_values_as_missing() -> None:
    assert _clean_string(float("nan")) is None
    assert _clean_string("nan") is None
    assert _clean_string(" NaT ") is None


def test_read_products_can_select_excel_sheet(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    with pd.ExcelWriter(input_path) as writer:
        pd.DataFrame([{"supplier_sku": "A-1", "title_raw": "Sheet A"}]).to_excel(writer, sheet_name="Alpha", index=False)
        pd.DataFrame([{"supplier_sku": "B-1", "title_raw": "Sheet B"}]).to_excel(writer, sheet_name="Beta", index=False)

    assert list_excel_sheets(input_path) == ["Alpha", "Beta"]
    assert list_excel_sheet_items(input_path) == [{"index": 0, "name": "Alpha"}, {"index": 1, "name": "Beta"}]
    rows = read_products(input_path, sheet_index=1)

    assert len(rows) == 1
    assert rows[0].supplier_sku == "B-1"
    assert rows[0].title_raw == "Sheet B"


def test_read_products_maps_common_excel_aliases(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    pd.DataFrame(
        [
            {
                "Artikel": "A01-000K",
                "Bezeichnung": "Jolly Smak 10 kg. Pre-Spotter",
                "Beschreibung": "Universal pre-spotter",
                "Preis": 45.99,
                "Einheit": "PZ",
            }
        ]
    ).to_excel(input_path, index=False)

    rows = read_products(input_path)

    assert len(rows) == 1
    assert rows[0].supplier_sku == "A01-000K"
    assert rows[0].title_raw == "Jolly Smak 10 kg. Pre-Spotter"
    assert rows[0].description_raw == "Universal pre-spotter"
    assert rows[0].extra_fields == {"Preis": 45.99, "Einheit": "PZ"}


def test_read_products_derives_purchase_price_for_supplier_lists(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    pd.DataFrame(
        [
            {
                "Artikel": "A01-000K",
                "Bezeichnung": "Jolly Smak 10 kg. Pre-Spotter",
                "Preis": 45.99,
                "import_kind": "supplier_price_list",
                "purchase_currency": "EUR",
                "supplier_name": "Tintolav",
            }
        ]
    ).to_excel(input_path, index=False)

    rows = read_products(input_path)

    assert rows[0].supplier_name == "Tintolav"
    assert rows[0].extra_fields["purchase_price"] == 45.99
    assert rows[0].extra_fields["purchase_currency"] == "EUR"


def test_resolve_excel_sheet_name_matches_trimmed_and_casefolded_names(tmp_path: Path) -> None:
    input_path = tmp_path / "products.xlsx"
    with pd.ExcelWriter(input_path) as writer:
        pd.DataFrame([{"supplier_sku": "A-1"}]).to_excel(writer, sheet_name=" Preiszeilen ", index=False)

    assert resolve_excel_sheet_name(input_path, "Preiszeilen") == " Preiszeilen "
    assert resolve_excel_sheet_name(input_path, "preiszeilen") == " Preiszeilen "
    assert resolve_excel_sheet_selector(input_path, requested_sheet_index=0) == 0
