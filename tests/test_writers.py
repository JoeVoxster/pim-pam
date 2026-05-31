from pathlib import Path

import pandas as pd

from app.io.writers import write_asset_mapping, write_channel_export_rows, write_medusa_products, write_odoo_products, write_products
from app.models import DownloadedAsset, ProductOutputRow


def test_write_asset_mapping_exports_expected_columns(tmp_path: Path) -> None:
    output = tmp_path
    path = write_asset_mapping(
        output,
        [
            DownloadedAsset(
                supplier_sku="sku-1",
                asset_type="image",
                source_url="https://example.com/a.jpg",
                local_path="/tmp/a.jpg",
                file_name="a.jpg",
                label="front",
            )
        ],
    )
    frame = pd.read_csv(path)
    assert list(frame.columns) == [
        "supplier_sku",
        "asset_type",
        "role",
        "source_url",
        "page_url",
        "local_path",
        "file_name",
        "label",
        "context_text",
        "product_name",
        "product_title",
        "extracted_text",
    ]


def test_write_medusa_products_exports_variant_columns(tmp_path: Path) -> None:
    path = write_medusa_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="base-sku",
                variant_sku="variant-sku",
                supplier_name="Tintolav",
                ean="1234567890123",
                barcode="1234567890123",
                variant_title="Reiniger X 400 ml",
                variant_option_1_name="Pack Size",
                variant_option_1_value="400 ml",
                source_url="https://example.com/product",
                source_url_final="https://example.com/product",
                product_name="Reiniger X",
                product_title="Reiniger X",
                description="Beschreibung",
                specifications="Packaging: 400 ml",
                technical_features="foo | bar",
                image_urls="https://example.com/1.png | https://example.com/2.png",
                datasheet_urls="https://example.com/datasheet.pdf",
                sds_urls="https://example.com/sds.pdf",
                extra_fields={
                    "medusa_shipping_profile_id": "sp_123",
                    "medusa_product_categories": "pcat_1|pcat_2",
                    "medusa_product_sales_channels": "sc_1|sc_2",
                    "medusa_variant_price_eur": "12.90",
                },
                status="ok",
            )
        ],
    )
    frame = pd.read_csv(path)
    assert frame.loc[0, "Product Handle"] == "reiniger-x"
    assert frame.loc[0, "Variant SKU"] == "variant-sku"
    assert str(frame.loc[0, "Variant EAN"]) == "1234567890123"
    assert frame.loc[0, "Variant Option 1 Value"] == "400 ml"
    assert frame.loc[0, "Product Image 2"] == "https://example.com/2.png"
    assert frame.loc[0, "Shipping Profile Id"] == "sp_123"
    assert frame.loc[0, "Product Category 1"] == "pcat_1"
    assert frame.loc[0, "Product Sales Channel 2"] == "sc_2"
    assert str(frame.loc[0, "Variant Price EUR"]) == "12.9"
    assert str(frame.loc[0, "Variant Manage Inventory"]).lower() == "false"
    assert str(frame.loc[0, "Variant Allow Backorder"]).lower() == "false"


def test_write_products_serializes_extra_fields_as_json(tmp_path: Path) -> None:
    write_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="SKU-1",
                status="ok",
                extra_fields={"Artikel": "A01-000K", "Preis": 45.99},
            )
        ],
    )

    frame = pd.read_csv(tmp_path / "products_clean.csv")
    assert frame.loc[0, "extra_fields"] == '{"Artikel": "A01-000K", "Preis": 45.99}'
    assert frame.loc[0, "Artikel"] == "A01-000K"
    assert str(frame.loc[0, "Preis"]) == "45.99"


def test_write_channel_export_rows_writes_expected_columns(tmp_path: Path) -> None:
    path = write_channel_export_rows(
        tmp_path,
        [
            {
                "sales_channel_code": "voxster",
                "product_id": 1,
                "variant_id": 2,
                "product_sku": "CHEM-1",
                "variant_sku": "CHEM-1-25KG",
                "variant_ean": "7610000000001",
                "product_title": "Natriumhypochlorit 14 %",
                "short_description": "Kurztext",
                "description": "Langtext",
                "slug": "natriumhypochlorit-14",
                "variant_title": "25 kg",
                "external_category_id": "chem-001",
                "external_category_path": "Chemie > Laugen",
                "publication_status": "published",
                "price_enabled": True,
                "shippable": True,
                "hazardous_goods": True,
                "limited_quantity": "1L",
                "language_code": "de-CH",
            }
        ],
        sales_channel_code="voxster",
        language_code="de-CH",
    )

    frame = pd.read_csv(path)

    assert path.name == "channel_export_voxster_de-CH.csv"
    assert frame.loc[0, "sales_channel_code"] == "voxster"
    assert frame.loc[0, "external_category_id"] == "chem-001"
    assert frame.loc[0, "language_code"] == "de-CH"


def test_write_medusa_products_dedupes_rows_and_adds_price(tmp_path: Path) -> None:
    product = ProductOutputRow(
        supplier_sku="A10-025Q",
        variant_sku="A10-025Q",
        product_name="Tonsil 25 kg.",
        product_title="Tonsil 25 kg.",
        variant_title="Tonsil 25 kg.",
        extra_fields={"sales_price": 12.5, "purchase_currency": "EUR"},
        status="ok",
    )

    path = write_medusa_products(tmp_path, [product, product])
    frame = pd.read_csv(path)

    assert len(frame) == 1
    assert str(frame.loc[0, "Variant Price EUR"]) == "12.5"


def test_write_medusa_products_normalizes_mojibake_and_quotes_commas(tmp_path: Path) -> None:
    path = write_medusa_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="A13-000M",
                variant_sku="A13-000M",
                product_name="P3 Pure Power Perc Box (2, 23 kg)",
                product_title="P3 Pure Power Perc Box (2, 23 kg)",
                variant_title="P3 Pure Power Perc Box (2, 23 kg)",
                description="Medium Filter Cartridge Â½ Maxi",
                status="ok",
            )
        ],
    )

    content = path.read_text(encoding="utf-8")
    frame = pd.read_csv(path)

    assert '"P3 Pure Power Perc Box (2, 23 kg)"' in content
    assert frame.loc[0, "Product Description"] == "Medium Filter Cartridge ½ Maxi"


def test_write_medusa_products_prefers_cleaner_duplicate_description(tmp_path: Path) -> None:
    path = write_medusa_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="A39-515K",
                variant_sku="A39-515K",
                product_name="Black Premium 10 kg. Deodetergent Darks",
                product_title="Black Premium 10 kg. Deodetergent Darks",
                variant_title="Black Premium 10 kg. Deodetergent Darks",
                description="Highly concentrated liquid deodetergent that protects black and dark colours A39-518Dx12 - BioMusk 12 x 1 lt.",
                extra_fields={"sales_price": 16.97, "purchase_currency": "EUR"},
                status="ok",
            ),
            ProductOutputRow(
                supplier_sku="A39-515K",
                variant_sku="A39-515K",
                product_name="Black Premium 10 kg. Deodetergent Darks",
                product_title="Black Premium 10 kg. Deodetergent Darks",
                variant_title="Black Premium 10 kg. Deodetergent Darks",
                description="Highly concentrated liquid deodetergent that protects black and dark colours",
                extra_fields={"sales_price": 22.78, "purchase_currency": "EUR"},
                status="ok",
            ),
        ],
    )

    frame = pd.read_csv(path)

    assert len(frame) == 1
    assert frame.loc[0, "Product Description"] == "Highly concentrated liquid deodetergent that protects black and dark colours"
    assert str(frame.loc[0, "Variant Price EUR"]) == "22.78"


def test_write_medusa_products_uses_only_allowed_headers(tmp_path: Path) -> None:
    path = write_medusa_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="SKU-1",
                variant_sku="SKU-1",
                product_name="Demo Product",
                product_title="Demo Product",
                description="Simple product",
                extra_fields={
                    "medusa_product_metadata": {"supplier": "Demo"},
                    "medusa_variant_metadata": {"variant": "SKU-1"},
                    "medusa_variant_price_eur": 19.99,
                },
                status="ok",
            )
        ],
    )

    frame = pd.read_csv(path)
    allowed = {
        "Product Id",
        "Product Handle",
        "Product Title",
        "Product Subtitle",
        "Product Status",
        "Product Description",
        "Product External Id",
        "Product Thumbnail",
        "Product Collection Id",
        "Product Type Id",
        "Product Discountable",
        "Product Height",
        "Product HS Code",
        "Product Length",
        "Product Material",
        "Product MID Code",
        "Product Origin Country",
        "Product Weight",
        "Product Width",
        "Product Metadata",
        "Shipping Profile Id",
        "Product Is Giftcard",
        "Variant Id",
        "Variant Title",
        "Variant SKU",
        "Variant UPC",
        "Variant EAN",
        "Variant HS Code",
        "Variant MID Code",
        "Variant Manage Inventory",
        "Variant Allow Backorder",
        "Variant Barcode",
        "Variant Height",
        "Variant Length",
        "Variant Material",
        "Variant Metadata",
        "Variant Origin Country",
        "Variant Variant Rank",
        "Variant Width",
        "Variant Weight",
        "Variant Option 1 Name",
        "Variant Option 1 Value",
        "Variant Price EUR",
    }
    assert set(frame.columns).issubset(allowed | {"Product Image 1"})
    assert frame.loc[0, "Product Metadata"] == '{"supplier": "Demo"}'
    assert frame.loc[0, "Variant Metadata"] == '{"variant": "SKU-1"}'


def test_write_medusa_products_exports_color_variant_options_without_group_key(tmp_path: Path) -> None:
    path = write_medusa_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="B01-045AR",
                variant_sku="B01-045AR",
                product_name="Marking Tape Rolls 24 mm. Orange 6 pz.",
                product_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
                variant_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
                extra_fields={"sales_price": 5.5},
                status="ok",
            ),
            ProductOutputRow(
                supplier_sku="B01-045BL",
                variant_sku="B01-045BL",
                product_name="Marking Tape Rolls 24 mm. Blue 6 pz.",
                product_title="Marking Tape Rolls 24 mm. Blue 6 pz.",
                variant_title="Marking Tape Rolls 24 mm. Blue 6 pz.",
                extra_fields={"sales_price": 5.5},
                status="ok",
            ),
        ],
    )

    frame = pd.read_csv(path)

    assert len(frame) == 2
    assert set(frame["Product Handle"]) == {"marking-tape-rolls-24-mm-orange-6-pz", "marking-tape-rolls-24-mm-blue-6-pz"}
    assert set(frame["Variant Option 1 Name"]) == {"Color"}
    assert set(frame["Variant Option 1 Value"]) == {"Orange", "Blue"}


def test_write_odoo_products_exports_template_and_variant_attribute_rows(tmp_path: Path) -> None:
    template_path, variant_path = write_odoo_products(
        tmp_path,
        [
            ProductOutputRow(
                supplier_sku="B01-045AR",
                variant_sku="B01-045AR",
                supplier_name="Tintolav",
                brand="Tintolav",
                product_name="Marking Tape Rolls 24 mm. Orange 6 pz.",
                product_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
                variant_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
                extra_fields={"sales_price": 8.14, "purchase_price": 8.14, "purchase_currency": "EUR"},
                status="ok",
            ),
            ProductOutputRow(
                supplier_sku="B01-045AZ",
                variant_sku="B01-045AZ",
                supplier_name="Tintolav",
                brand="Tintolav",
                product_name="Marking Tape Rolls 24 mm. Blue 6 pz.",
                product_title="Marking Tape Rolls 24 mm. Blue 6 pz.",
                variant_title="Marking Tape Rolls 24 mm. Blue 6 pz.",
                extra_fields={"sales_price": 8.14, "purchase_price": 8.14, "purchase_currency": "EUR"},
                status="ok",
            ),
        ],
    )

    templates = pd.read_csv(template_path)
    variants = pd.read_csv(variant_path)

    assert len(templates) == 2
    assert set(templates["Template External ID"]) == {"B01-045AR", "B01-045AZ"}
    assert set(templates["Attribute 1 Name"]) == {"Color"}
    assert len(variants) == 2
    assert set(variants["Attribute 1 Value"]) == {"Orange", "Blue"}
