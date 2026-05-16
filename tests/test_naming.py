from app.assets.naming import (
    build_asset_filename,
    build_descriptive_image_label,
    build_descriptive_pdf_label,
    clean_product_name,
    extract_packaging_hint,
    guess_pdf_label,
    safe_slug,
)


def test_safe_slug_builds_filesystem_safe_value() -> None:
    assert safe_slug("Jacke Schwarz / Größe L") == "jacke_schwarz_grosse_l"


def test_build_descriptive_image_label_uses_product_tokens() -> None:
    assert build_descriptive_image_label("Schuh Blau Leder", None, None, None, None, 1) == "schuh_blau_leder_1"


def test_build_asset_filename_uses_clean_pattern() -> None:
    assert build_asset_filename("SKU 1", "image", 2, ".jpg", "jacke_schwarz_vorne") == "jacke_schwarz_vorne.jpg"


def test_guess_pdf_label_classifies_manual() -> None:
    assert guess_pdf_label("https://example.com/files/manual_de.pdf") == "manual"


def test_extract_packaging_hint_detects_ml() -> None:
    assert extract_packaging_hint("deospray_muschiobianco_400ml.png") == "400 ml"


def test_build_descriptive_pdf_label_uses_packaging_when_available() -> None:
    assert (
        build_descriptive_pdf_label("DeoSpray Muschio Bianco", None, "Safety Data Sheet", "https://example.com/400ml.pdf", None)
        == "deospray_muschio_bianco_400_ml_sds"
    )


def test_clean_product_name_removes_trailing_sku() -> None:
    assert clean_product_name("DeoSpray Muschio Bianco A73-015QU") == "DeoSpray Muschio Bianco"


def test_build_descriptive_pdf_label_deduplicates_and_shortens() -> None:
    assert (
        build_descriptive_pdf_label(
            "DeoSpray Muschio Bianco A73-015QU",
            None,
            "Hygienfresh DeoSpray Muschio Bianco Safety Data Sheet",
            None,
            None,
        )
        == "deospray_muschio_bianco_hygienfresh_sds"
    )
