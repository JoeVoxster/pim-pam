from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Asset
from app.schemas.pim import ProductCreate, VariantCreate
from app.services.pim_service import create_product
from app.services.product_asset_enrichment_service import (
    build_asset_filename,
    detect_pdf_language,
    enrich_missing_product_assets,
    _resolve_asset_download_dir,
)


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"", content_type: str = "text/html") -> None:
        self.text = text
        self._content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192):
        yield self._content


def test_asset_filename_rules_for_pdfs_and_images(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A15-070XX", title="D5 Tanin, Fleckenentferner", brand_name="Tintolav", status="active"),
            VariantCreate(sku="A15-070A", variant_title="500 ml", price=Decimal("8.73"), currency="CHF"),
        )

        assert build_asset_filename(product, "sdb", "https://example.test/d5_de-CH.pdf", language_code="de-CH") == (
            "tintolav-d5-tanin-fleckenentferner-a15-070xx-sdb-de-CH.pdf"
        )
        assert build_asset_filename(product, "sds", "https://example.test/d5_en.pdf", language_code="en") == (
            "tintolav-d5-tanin-fleckenentferner-a15-070xx-sds-en.pdf"
        )
        assert build_asset_filename(product, "datasheet", "https://example.test/d5.pdf") == (
            "tintolav-d5-tanin-fleckenentferner-a15-070xx-datasheet-unknown.pdf"
        )
        assert build_asset_filename(product, "product_image", "https://example.test/d5_de-CH.jpg", language_code="de-CH") == (
            "tintolav-d5-tanin-fleckenentferner-a15-070xx-product-image.jpg"
        )


def test_detect_pdf_language_from_url_and_unknown() -> None:
    assert detect_pdf_language("https://example.test/sdb_de-CH.pdf") == "de-CH"
    assert detect_pdf_language("https://example.test/safety-data-sheet-en.pdf") == "en"
    assert detect_pdf_language("https://example.test/document.pdf") == "unknown"


def test_enrich_missing_product_assets_downloads_and_deduplicates(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    png_payload = _png_payload(650, 650)
    pdf_payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    html = """
    <html><body>
      <a href="https://assets.example.test/tintolav-d5-sdb-de-CH.pdf">Sicherheitsdatenblatt Deutsch</a>
      <img src="https://assets.example.test/tintolav-d5-packaging.jpg" alt="Packaging image">
    </body></html>
    """

    def fake_get(url, *args, **kwargs):
        if url.endswith("/product.html"):
            return FakeResponse(text=html, content=html.encode("utf-8"), content_type="text/html")
        if url.endswith(".pdf"):
            return FakeResponse(content=pdf_payload, content_type="application/pdf")
        if url.endswith(".jpg"):
            return FakeResponse(content=png_payload, content_type="image/png")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("app.services.product_asset_enrichment_service.requests.get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(
                sku="A15-070XX",
                title="D5 Tanin, Fleckenentferner",
                brand_name="Tintolav",
                status="active",
            ),
            VariantCreate(sku="A15-070A", variant_title="500 ml"),
        )
        product.source_url_final = "https://assets.example.test/product.html"
        first = enrich_missing_product_assets(session, [product.id], storage_root=tmp_path / "assets")
        session.commit()
        second = enrich_missing_product_assets(session, [product.id], storage_root=tmp_path / "assets")
        session.commit()
        assets = session.query(Asset).filter(Asset.product_id == product.id).order_by(Asset.id).all()

    assert first["saved_count"] == 2
    assert second["saved_count"] == 0
    assert second["skipped_count"] == 2
    assert len(assets) == 2
    pdf = next(asset for asset in assets if asset.mime_type == "application/pdf")
    image = next(asset for asset in assets if asset.mime_type.startswith("image/"))
    assert pdf.language_code == "de-CH"
    assert pdf.filename.endswith("-sdb-de-CH.pdf")
    assert image.language_code is None
    assert "-de-CH" not in image.filename
    assert image.filename.endswith("-packaging-image.png")


def test_enrich_missing_product_assets_skips_tiny_and_layout_images(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    tiny_payload = _png_payload(65, 65)
    html = """
    <html><body>
      <img src="https://assets.example.test/media/catalog/product/cache/2/thumbnail/65x/f/w/fw180.jpg" alt="Tiny thumbnail">
      <img src="https://assets.example.test/media/catalog/product/cache/2/image/650x/f/w/fw180.jpg" alt="Product image">
      <img src="https://assets.example.test/skin/frontend/ultimo/default/images/menu_left_bg.png" alt="Menu background">
    </body></html>
    """

    def fake_get(url, *args, **kwargs):
        if url.endswith("/product.html"):
            return FakeResponse(text=html, content=html.encode("utf-8"), content_type="text/html")
        if "650x" in url:
            return FakeResponse(content=_png_payload(650, 650), content_type="image/png")
        if "65x" in url:
            return FakeResponse(content=tiny_payload, content_type="image/png")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("app.services.product_asset_enrichment_service.requests.get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="FW-180", title="Federbodenwagen", brand_name="Voxster", status="active"),
            VariantCreate(sku="FW-180", variant_title="Default"),
        )
        product.source_url_final = "https://assets.example.test/product.html"
        result = enrich_missing_product_assets(session, [product.id], storage_root=tmp_path / "assets")
        session.commit()
        assets = session.query(Asset).filter(Asset.product_id == product.id).all()

    assert result["saved_count"] == 1
    assert result["skipped_count"] == 0
    assert len(assets) == 1
    assert assets[0].width == 650
    assert "650x" in (assets[0].source_url or "")


def test_enrich_missing_product_assets_handles_download_errors(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    html = '<a href="https://assets.example.test/broken-sds-en.pdf">SDS</a>'

    def fake_get(url, *args, **kwargs):
        if url.endswith("/product.html"):
            return FakeResponse(text=html, content=html.encode("utf-8"), content_type="text/html")
        raise RuntimeError("download failed")

    monkeypatch.setattr("app.services.product_asset_enrichment_service.requests.get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="BROKEN", title="Broken Asset", status="active"),
            VariantCreate(sku="BROKEN-1", variant_title="Default"),
        )
        product.source_url_final = "https://assets.example.test/product.html"
        result = enrich_missing_product_assets(session, [product.id], storage_root=tmp_path / "assets")

    assert result["error_count"] == 1
    assert result["saved_count"] == 0


def test_enrich_missing_product_assets_skips_html_download_pages(tmp_path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    html = '<a href="https://assets.example.test/download.html">Download</a>'
    download_html = b"<!DOCTYPE html><html><body>Not a PDF</body></html>"

    def fake_get(url, *args, **kwargs):
        if url.endswith("/product.html"):
            return FakeResponse(text=html, content=html.encode("utf-8"), content_type="text/html")
        if url.endswith("/download.html"):
            return FakeResponse(content=download_html, content_type="text/html")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("app.services.product_asset_enrichment_service.requests.get", fake_get)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="HTML-DL", title="HTML Download Product", status="active"),
            VariantCreate(sku="HTML-DL-1", variant_title="Default"),
        )
        product.source_url_final = "https://assets.example.test/product.html"
        result = enrich_missing_product_assets(session, [product.id], storage_root=tmp_path / "assets")
        session.commit()
        assets = session.query(Asset).filter(Asset.product_id == product.id).all()

    assert result["saved_count"] == 0
    assert result["skipped_count"] == 1
    assert assets == []
    assert "HTML-/Downloadseite" in result["items"][0]["message"]


def test_discovery_uses_local_supplier_asset_mapping_for_matching_sku(tmp_path, monkeypatch) -> None:
    mapping = tmp_path / "asset_mapping.csv"
    mapping.write_text(
        "supplier_sku,asset_type,role,source_url,page_url,local_path,file_name,label,context_text,product_name,product_title,extracted_text\n"
        "a15-070ad,pdf,sds,https://tintolav.test/download/file_id-15891.html,https://tintolav.test/d5-tannin.html,,d5_sds.pdf,D5 Safety Data Sheet,,D5 Tannin,D5 Tannin 500 ml,SAFETY DATA SHEET\n"
        "a15-070ad,image,image,https://tintolav.test/images/a15-070ad5.png,https://tintolav.test/d5-tannin.html,,d5.png,D5 image,,D5 Tannin,D5 Tannin 500 ml,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.product_asset_enrichment_service.LOCAL_SUPPLIER_ASSET_MAPPING_PATHS", (mapping,))

    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        product, _variant = create_product(
            session,
            ProductCreate(sku="A15-070XX", title="D5 Tanin, Fleckenentferner", brand_name="Tintolav", status="active"),
            VariantCreate(sku="A15-070A", variant_title="500 ml"),
        )
        from app.services.product_asset_enrichment_service import discover_product_asset_candidates

        discoveries = discover_product_asset_candidates(product, timeout_seconds=1)

    assert {item.asset_type for item in discoveries} == {"sds", "product_image"}
    assert any(item.asset_url == "https://tintolav.test/download/file_id-15891.html" for item in discoveries)


def test_resolve_asset_download_dir_uses_fallback_when_product_dir_not_writable(tmp_path, monkeypatch) -> None:
    def fake_access(path, mode):
        return "_imports" in Path(path).parts

    monkeypatch.setattr("app.services.product_asset_enrichment_service.os.access", fake_access)

    resolved = _resolve_asset_download_dir(tmp_path, 16, "images")

    assert resolved == tmp_path / "_imports" / "product-16" / "images"
    assert resolved.exists()


def _png_payload(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()
