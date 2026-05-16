from app.assets.naming import safe_slug


def test_slug_handles_empty_value() -> None:
    assert safe_slug("", default="fallback") == "fallback"
