from pathlib import Path

from PIL import Image


def test_local_chemical_symbol_svgs_exist_and_start_with_svg() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "public/chem/ghs/GHS05.svg",
        root / "public/chem/ghs/GHS07.svg",
        root / "public/chem/ghs/GHS09.svg",
        root / "public/chem/adr/ADR_8.svg",
        root / "public/chem/adr/ADR_pollution.svg",
    ]

    for path in files:
        assert path.exists(), path
        content = path.read_text(encoding="utf-8", errors="ignore")
        assert content.lstrip().startswith("<svg"), path
        assert "viewBox" in content or ("width" in content and "height" in content), path


def test_local_adr_png_symbols_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "public/chem/adr/ADR_3.png",
        root / "public/chem/adr/ADR_5.1.png",
    ]

    for path in files:
        assert path.exists(), path
        assert path.stat().st_size > 1000
        with path.open("rb") as handle:
            assert handle.read(8) == b"\x89PNG\r\n\x1a\n"


def test_local_adr_lq_symbol_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "public/chem/adr/ADR_LQ.jpg"

    assert path.exists(), path
    assert path.stat().st_size > 1000
    with Image.open(path) as image:
        assert image.width > 0
        assert image.height > 0


def test_pdf_renderer_has_stable_ghs07_png_fallback() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "app/assets/ghs/GHS07.png"

    assert path.exists(), path
    assert path.stat().st_size > 1000


def test_ghs07_black_symbol_stays_inside_white_diamond() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "app/assets/ghs/GHS07.png"

    image = Image.open(path).convert("RGBA")
    width, height = image.size
    assert width == height
    center = width / 2
    inner_half = width * 0.34
    safety_margin = width * 0.018
    black_pixels = []
    red_pixels = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = image.getpixel((x, y))
            if a < 128:
                continue
            if r < 40 and g < 40 and b < 40:
                black_pixels.append((x, y))
            if r > 180 and g < 60 and b < 60:
                red_pixels.append((x, y))

    assert black_pixels
    assert red_pixels
    assert all(abs(x - center) + abs(y - center) <= inner_half - safety_margin for x, y in black_pixels)
