from __future__ import annotations

import socket
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import ProductChannelListing
from app.schemas.pim import ProductCreate, VariantCreate
from app.services.pim_service import create_product, ensure_default_sales_channels
from sqlalchemy import create_engine


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1):
                return
        except (OSError, URLError) as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Dash server did not start at {url}") from last_error


def test_product_bulk_listing_confirm_dialog_browser_flow(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "pim-e2e.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ASSET_STORAGE_PATH", str(tmp_path / "assets"))

    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        ensure_default_sales_channels(session)
        product, _variant = create_product(
            session,
            ProductCreate(sku="E2E-BULK-1", title="E2E Bulk Product", brand_name="VOXSTER", status="ready"),
            VariantCreate(sku="E2E-BULK-1-A", variant_title="Default"),
        )
        product_id = product.id
        session.commit()

    from app.ui.dash_app import create_dash_app

    app = create_dash_app()
    port = _free_port()
    thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_server(base_url)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base_url, wait_until="networkidle")

        page.locator("#nav-products").click()
        expect(page.locator("#products-grid .ag-center-cols-container .ag-row").first).to_be_visible(timeout=10000)
        page.locator("#products-grid .ag-center-cols-container .ag-row").first.click()
        expect(page.locator("#product-channel-actions")).to_be_visible(timeout=5000)
        page.locator("#product-channel-include-variants input").check()
        expect(page.locator("#product-channel-action-count")).to_contain_text("1 Varianten", timeout=5000)

        page.locator("#product-listings-action-open-button").click()
        expect(page.locator("#channel-bulk-modal")).to_be_visible(timeout=5000)
        expect(page.locator("#channel-bulk-summary")).to_contain_text("1 Varianten", timeout=5000)
        page.locator("#channel-bulk-sales-channel-id").click()
        page.locator("#channel-bulk-modal").get_by_text("voxster.ch (voxster)").click()
        page.locator("#channel-bulk-run-button").click()
        expect(page.locator("#flash-message")).to_contain_text("Kanal-Aktion ausgeführt", timeout=10000)
        browser.close()

    with Session(engine) as session:
        listing = session.scalar(
            select(ProductChannelListing).where(ProductChannelListing.product_id == product_id)
        )
    assert listing is not None
    assert listing.allowed is True
    assert listing.is_active is True
    assert listing.publication_status == "published"
