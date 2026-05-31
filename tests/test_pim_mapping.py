from decimal import Decimal

from app.etl.mapping import asset_paths, category_values, product_payload, variant_payload
from app.models import ProductOutputRow
from app.schemas.pim import ImportMappingConfig


def test_pim_mapping_builds_product_and_variant_payloads() -> None:
    product = ProductOutputRow(
        supplier_sku="SKU-100",
        variant_sku="SKU-100-A",
        supplier_name="Tintolav",
        brand="Demo Brand",
        product_name="Demo Produkt",
        product_title="Demo Produkt",
        description="Beschreibung",
        image_paths="/tmp/a.png|/tmp/b.png",
        extra_fields={"price": "12.50", "stock_qty": "7", "category": "Chemie > Reiniger"},
        status="ok",
    )
    config = ImportMappingConfig(price_column_candidates=["price"], category_columns=["category"])

    assert product_payload(product)["handle"] == "demo-produkt"
    assert variant_payload(product, config)["price"] == Decimal("12.50")
    assert variant_payload(product, config)["stock_qty"] == 7
    assert category_values(product, config) == ["Chemie > Reiniger"]
    assert asset_paths(product) == ["/tmp/a.png", "/tmp/b.png"]


def test_pim_mapping_keeps_product_sku_and_infers_color_option() -> None:
    product = ProductOutputRow(
        supplier_sku="B01-045AR",
        variant_sku="B01-045AR",
        supplier_name="Tintolav",
        product_name="Marking Tape Rolls 24 mm. Orange 6 pz.",
        product_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
        variant_title="Marking Tape Rolls 24 mm. Orange 6 pz.",
        status="ok",
    )

    payload = product_payload(product)
    variant = variant_payload(product, ImportMappingConfig())

    assert payload["sku"] == "B01-045AR"
    assert payload["title"] == "Marking Tape Rolls 24 mm. Orange 6 pz."
    assert variant["option_name"] == "Color"
    assert variant["option_value"] == "Orange"


def test_pim_mapping_infers_color_from_tintolav_sku_suffix() -> None:
    product = ProductOutputRow(
        supplier_sku="B10-056AZ",
        variant_sku="B10-056AZ",
        supplier_name="Tintolav",
        product_name="Fabric Cover Plus Felt Lining A",
        product_title="Fabric Cover Plus Felt Lining A",
        variant_title="Fabric Cover Plus Felt Lining A",
        status="ok",
    )

    payload = product_payload(product)
    variant = variant_payload(product, ImportMappingConfig())

    assert payload["sku"] == "B10-056AZ"
    assert variant["option_name"] == "Color"
    assert variant["option_value"] == "Blue"


def test_pim_mapping_prefers_packaging_over_color_words_for_weight_variants() -> None:
    product = ProductOutputRow(
        supplier_sku="A39-512K",
        variant_sku="A39-512K",
        supplier_name="Tintolav",
        product_name="White Xtra 10 kg. Deodetergent Whites",
        product_title="White Xtra 10 kg. Deodetergent Whites",
        variant_title="White Xtra 10 kg. Deodetergent Whites",
        status="ok",
    )

    payload = product_payload(product)
    variant = variant_payload(product, ImportMappingConfig())

    assert payload["sku"] == "A39-512K"
    assert payload["title"] == "White Xtra 10 kg. Deodetergent Whites"
    assert variant["option_name"] == "Packaging"
    assert variant["option_value"] == "10 kg"
    assert variant["packaging"] == "10 kg"


def test_pim_mapping_extracts_chemical_fields() -> None:
    product = ProductOutputRow(
        supplier_sku="CHEM-1",
        variant_sku="CHEM-1",
        supplier_name="Chemstore",
        product_name="Javelle Konzentrat 14%",
        product_title="Javelle Konzentrat 14%",
        status="ok",
        extra_fields={
            "category": "Chemikalien > Laugen",
            "CAS Nummer": "7681-52-9",
            "EG-Nummer": "231-668-3",
            "UN-Nummer": "1791",
            "Gefahrenhinweise": "Verursacht schwere Verätzungen.",
            "Sicherheitshinweise": "Schutzhandschuhe tragen.",
            "signalwort": "GEFAHR",
            "sds_url": "https://example.com/sds.pdf",
        },
    )

    payload = product_payload(product)

    assert payload["is_chemical"] is True
    assert payload["cas_number"] == "7681-52-9"
    assert payload["ec_number"] == "231-668-3"
    assert payload["un_number"] == "1791"
    assert payload["signal_word"] == "GEFAHR"
    assert payload["sds_url"] == "https://example.com/sds.pdf"
