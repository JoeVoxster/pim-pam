from app.scraping.extractors import _normalize_barcode


def test_normalize_barcode_extracts_digits() -> None:
    assert _normalize_barcode("(01)08054729630033") == "08054729630033"
