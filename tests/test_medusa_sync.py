from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Asset, ChannelCategory, MedusaConnectionConfig, MedusaSyncMapping, MedusaSyncRunItem, Product, ProductCategoryMapping, ProductTranslation, ProductVariant, R2StorageConfig, SalesChannel
from app.services.medusa.client import MedusaAdminApiClient, MedusaAuthError
from app.services.medusa.config_service import get_or_create_medusa_connection, save_medusa_connection
from app.services.medusa.mappers import MedusaPricingMapper, MedusaProductMapper, MedusaVariantMapper, normalize_handle
from app.services.medusa.sync_service import MedusaSyncService


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pim.db'}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)()


def test_medusa_admin_url_composition_and_missing_token(tmp_path) -> None:
    session = _session(tmp_path)
    config = save_medusa_connection(
        session,
        {"enabled": True, "base_url": "http://localhost:9000/", "admin_path": "admin", "api_token": ""},
    )
    client = MedusaAdminApiClient(config)
    assert client.admin_url == "http://localhost:9000/admin"
    with pytest.raises(MedusaAuthError):
        client.request("GET", "/products")


def test_medusa_secret_api_key_uses_basic_authorization_header() -> None:
    config = MedusaConnectionConfig(base_url="http://localhost:9000", admin_path="/admin", auth_type="api_token", api_token_secret="sk_test_1234567890")
    headers = MedusaAdminApiClient(config)._headers()
    assert headers["Authorization"] == "Basic sk_test_1234567890"
    assert "Bearer" not in headers["Authorization"]


def test_default_connection_prefers_enabled_connection(tmp_path) -> None:
    session = _session(tmp_path)
    disabled = MedusaConnectionConfig(name="default", enabled=False, base_url="http://disabled.test", admin_path="/admin")
    enabled = MedusaConnectionConfig(name="live", enabled=True, base_url="http://enabled.test", admin_path="/admin")
    session.add_all([disabled, enabled])
    session.commit()
    config = get_or_create_medusa_connection(session)
    assert config.name == "live"


def test_connection_rejects_html_frontend_response(tmp_path) -> None:
    session = _session(tmp_path)
    config = MedusaConnectionConfig(base_url="http://localhost:9000", admin_path="/app", api_token_secret="token")

    class HtmlSession:
        def request(self, *args, **kwargs):
            class Response:
                status_code = 200
                text = "<html>Medusa Admin App</html>"

                def json(self):
                    raise ValueError("html")

            return Response()

    with pytest.raises(Exception, match="keine Produkt-API-Antwort"):
        MedusaAdminApiClient(config, session=HtmlSession()).test_connection()


def test_save_medusa_connection_rejects_pasted_form_text_as_token(tmp_path) -> None:
    session = _session(tmp_path)
    with pytest.raises(ValueError, match="ungültige Sonderzeichen"):
        save_medusa_connection(
            session,
            {
                "enabled": True,
                "base_url": "http://localhost:9000",
                "admin_path": "/admin",
                "api_token": "Ja Name default Timeout Sekunden 30 − +",
            },
        )


def test_handle_normalization_is_stable() -> None:
    assert normalize_handle(" Jolly Smak – Vorentflecker für PER! ") == "jolly-smak-vorentflecker-fur-per"
    assert normalize_handle("") == "produkt"


def test_product_and_variant_payload_mapping(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    product = Product(id=1, sku="A01-000K", handle="jolly-smak", title="Jolly Smak", status="active", source_language="en")
    variant = ProductVariant(id=10, product_id=1, sku="A01-000K", variant_title="Jolly Smak 10kg", price=Decimal("74.40"), currency="CHF")
    channel = SalesChannel(id=1, code="voxster", name="voxster.ch")
    channel_category = ChannelCategory(id=2, sales_channel_id=1, external_category_id="detergents", external_path="Shop > Detergents", name="Detergents")
    mapping = ProductCategoryMapping(product_id=1, sales_channel_id=1, channel_category_id=2, position=70)
    mapping.sales_channel = channel
    mapping.channel_category = channel_category
    product.variants = [variant]
    product.channel_category_mappings = [mapping]
    product_payload = MedusaProductMapper(config).map_product(product, translation=None, image_urls=["https://media.voxster.ch/img.jpg"])
    variant_payload = MedusaVariantMapper(config).map_variant(variant, translation=None)
    assert product_payload.payload["handle"] == "jolly-smak"
    assert product_payload.payload["metadata"]["pim_product_id"] == 1
    assert product_payload.payload["metadata"]["pim_category_sort_positions"] == [
        {
            "sales_channel": "voxster.ch",
            "sales_channel_code": "voxster",
            "channel_category_id": 2,
            "external_category_id": "detergents",
            "category_handle": "detergents",
            "category_path": "Shop > Detergents",
            "position": 70,
        }
    ]
    assert product_payload.payload["images"] == [{"url": "https://media.voxster.ch/img.jpg"}]
    assert product_payload.payload["options"] == [{"title": "Variante", "values": ["Jolly Smak 10kg"]}]
    assert variant_payload.payload["sku"] == "A01-000K"
    assert variant_payload.payload["metadata"]["pim_variant_id"] == 10
    assert variant_payload.payload["options"] == {"Variante": "Jolly Smak 10kg"}


def test_product_payload_includes_medusa_option_values(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    product = Product(id=4, sku="A07-010XX", handle="tintoflor", title="Tintoflor", status="active")
    product.variants = [
        ProductVariant(id=5, product_id=4, sku="A07-010D", variant_title="Tintoflor 1 kg", option_name="Packaging", option_value="1 kg", packaging="1 kg"),
        ProductVariant(id=6, product_id=4, sku="A07-010H", variant_title="Tintoflor 5 kg", option_name="Packaging", option_value="5 kg", packaging="5 kg"),
    ]

    payload = MedusaProductMapper(config).map_product(product, translation=None, image_urls=[]).payload

    assert payload["options"] == [{"title": "Packaging", "values": ["1 kg", "5 kg"]}]


def test_product_payload_exports_short_description_as_subtitle_and_markdown_description(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    product = Product(id=1294, sku="DRYPAD-3", handle="drypad-3c", title="Drypad 3C", status="active")
    translation = ProductTranslation(
        product_id=1294,
        language_code="de-CH",
        title="Drypad 3C",
        short_description="Fertigkonfektionierter gepolsterter Überzug für Absaug-/Blas-Saug-Bügeltische mit HR3-Überzug.",
        description="Komplett fertigkonfektionierter Überzug.\n\n### Eigenschaften\n\n- Direkt einsetzbar",
    )

    payload = MedusaProductMapper(config).map_product(product, translation=translation, image_urls=[]).payload

    assert payload["subtitle"] == translation.short_description
    assert payload["description"] == translation.description
    assert "### Eigenschaften" in payload["description"]


def test_product_payload_prefers_base_fields_for_source_language_translation(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    product = Product(
        id=1404,
        sku="A15-030",
        handle="d1-schweiss-fleckenentferner",
        title="D1 Schweiss Fleckenentferner",
        description="Basisbeschreibung Deutsch",
        status="active",
        source_language="de-CH",
    )
    stale_translation = ProductTranslation(
        product_id=1404,
        language_code="de-CH",
        title="D1 Sweat Fleckenentferner",
        short_description="Kurzbeschreibung aus de-CH Translation",
        description="Veraltete Übersetzungsbeschreibung",
        slug="d1-sweat-fleckenentferner",
        seo_title="D1 Sweat Fleckenentferner",
    )

    payload = MedusaProductMapper(config).map_product(product, translation=stale_translation, image_urls=[]).payload

    assert payload["title"] == "D1 Schweiss Fleckenentferner"
    assert payload["description"] == "Basisbeschreibung Deutsch"
    assert payload["handle"] == "d1-schweiss-fleckenentferner"
    assert payload["subtitle"] == "Kurzbeschreibung aus de-CH Translation"


def test_price_mapper_uses_medusa_v2_major_unit_amounts(tmp_path) -> None:
    session = _session(tmp_path)
    variant = ProductVariant(id=10, product_id=1, sku="SKU", price=Decimal("74.40"), currency="CHF")
    prices = MedusaPricingMapper().variant_prices(variant)
    assert prices[0]["amount"] == 74.4
    assert prices[0]["currency_code"] == "chf"


class FakeMedusaClient:
    writes: list[tuple[str, dict]]

    def __init__(self, _config):
        self.writes = []

    def find_product_by_handle(self, _handle):
        return None

    def find_product_by_external_id_or_metadata(self, _pim_product_id):
        return None

    def create_product(self, payload):
        self.writes.append(("create_product", payload))
        return {"product": {"id": "prod_1", "handle": payload["handle"]}}

    def update_product(self, product_id, payload):
        self.writes.append(("update_product", payload))
        return {"product": {"id": product_id, "handle": payload.get("handle", "demo-product")}}

    def get_product(self, product_id, fields=None):
        return {"product": {"id": product_id, "variants": []}}

    def list_product_variants(self, _product_id):
        return []

    def find_variant_by_sku(self, _product_id, _sku):
        return None

    def create_variant(self, _product_id, payload):
        self.writes.append(("create_variant", payload))
        return {"variant": {"id": "variant_1", "sku": payload["sku"]}}

    def get_variant(self, variant_id):
        return {"variant": {"id": variant_id}}

    def update_variant(self, product_id, variant_id, payload):
        self.writes.append(("update_variant", payload))
        return {"variant": {"id": variant_id}}

    def upsert_variant_prices(self, product_id, variant_id, prices_payload):
        self.writes.append(("prices", prices_payload))
        return {"prices": prices_payload["prices"]}

    def upsert_translation(self, reference, reference_id, locale_code, translations):
        self.writes.append(("translation", translations))
        return {"translation": {"id": f"tr_{reference_id}_{locale_code}"}}


class MissingTranslationRouteClient(FakeMedusaClient):
    def upsert_translation(self, reference, reference_id, locale_code, translations):
        from app.services.medusa.client import MedusaApiError

        raise MedusaApiError("Cannot POST /admin/pim-sync/translations", status_code=404)


def test_dry_run_does_not_call_write_methods(tmp_path) -> None:
    session = _session(tmp_path)
    product = Product(sku="SKU-1", handle="demo-product", title="Demo Product", status="active", source_language="de-CH")
    variant = ProductVariant(sku="SKU-1", variant_title="Default", price=Decimal("10.00"), currency="CHF")
    product.variants.append(variant)
    session.add(product)
    session.commit()
    clients: list[FakeMedusaClient] = []

    def factory(config):
        client = FakeMedusaClient(config)
        clients.append(client)
        return client

    result = MedusaSyncService(session, client_factory=factory).dry_run_product(product.id)
    assert result["status"] == "success"
    assert clients and clients[0].writes == []


def test_medusa_product_selection_modes(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    active_a = Product(sku="SKU-A", handle="sku-a", title="A", status="active", source_language="de-CH")
    active_b = Product(sku="SKU-B", handle="sku-b", title="B", status="active", source_language="de-CH")
    archived = Product(sku="SKU-C", handle="sku-c", title="C", status="archived", source_language="de-CH")
    session.add_all([active_a, active_b, archived])
    session.commit()
    session.add(
        MedusaSyncMapping(
            connection_id=config.id,
            entity_type="product",
            local_entity_id=active_a.id,
            medusa_id="prod_a",
            status="active",
        )
    )
    session.commit()
    service = MedusaSyncService(session, client_factory=lambda config: FakeMedusaClient(config))

    assert service.resolve_product_ids(selection_mode="single", product_id=active_a.id) == [active_a.id]
    assert service.resolve_product_ids(selection_mode="selected", selected_product_ids=[active_b.id, active_a.id, active_b.id]) == [active_b.id, active_a.id]
    assert service.resolve_product_ids(selection_mode="all_active", limit=10) == [active_a.id, active_b.id]
    assert service.resolve_product_ids(selection_mode="without_mapping", limit=10) == [active_b.id]


def test_medusa_batch_dry_run_exports_selected_products(tmp_path) -> None:
    session = _session(tmp_path)
    product_a = Product(sku="SKU-A", handle="sku-a", title="A", status="active", source_language="de-CH")
    product_a.variants.append(ProductVariant(sku="SKU-A", variant_title="A", price=Decimal("10.00"), currency="CHF"))
    product_b = Product(sku="SKU-B", handle="sku-b", title="B", status="active", source_language="de-CH")
    product_b.variants.append(ProductVariant(sku="SKU-B", variant_title="B", price=Decimal("20.00"), currency="CHF"))
    session.add_all([product_a, product_b])
    session.commit()

    result = MedusaSyncService(session, client_factory=lambda config: FakeMedusaClient(config)).export_products(
        [product_a.id, product_b.id],
        dry_run=True,
    )

    assert result["status"] == "success"
    assert result["product_count"] == 2
    assert result["product_ids"] == [product_a.id, product_b.id]
    assert len(result["run_ids"]) == 2


def test_medusa_export_uses_active_storage_public_base_url_when_connection_has_none(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    config.public_asset_base_url = None
    session.add(
        R2StorageConfig(
            enabled=True,
            provider="bunny_storage",
            bucket="voxster-media",
            public_base_url="https://media.voxster.online",
        )
    )
    product = Product(sku="SKU-IMG", handle="sku-img", title="Image Product", status="active", source_language="de-CH")
    product.assets.append(
        Asset(
            filename="image.jpg",
            original_filename="image.jpg",
            mime_type="image/jpeg",
            file_size=123,
            storage_path="r2://voxster-media/prod/assets/products/1/images/image.jpg",
            storage_provider="bunny_storage",
            object_key="prod/assets/products/1/images/image.jpg",
            public_url="https://media.voxster.ch/prod/assets/products/1/images/image.jpg",
            asset_type="product_image",
        )
    )
    session.add(product)
    session.commit()

    result = MedusaSyncService(session, client_factory=lambda config: FakeMedusaClient(config)).dry_run_product(product.id)

    assert result["status"] == "success"
    item = session.scalar(select(MedusaSyncRunItem).where(MedusaSyncRunItem.run_id == result["run_id"], MedusaSyncRunItem.entity_type == "product"))
    assert item.request_payload["thumbnail"] == "https://media.voxster.online/prod/assets/products/1/images/image.jpg"
    assert item.request_payload["images"] == [{"url": "https://media.voxster.online/prod/assets/products/1/images/image.jpg"}]


def test_export_writes_medusa_mappings(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    config.api_token_secret = "token"
    product = Product(sku="SKU-1", handle="demo-product", title="Demo Product", status="active", source_language="de-CH")
    variant = ProductVariant(sku="SKU-1", variant_title="Default", price=Decimal("10.00"), currency="CHF")
    product.variants.append(variant)
    session.add(product)
    session.commit()
    clients: list[FakeMedusaClient] = []

    def factory(config):
        client = FakeMedusaClient(config)
        clients.append(client)
        return client

    result = MedusaSyncService(session, client_factory=factory).export_product(product.id, dry_run=False)
    assert result["status"] == "success"
    product_mapping = session.scalar(select(MedusaSyncMapping).where(MedusaSyncMapping.entity_type == "product"))
    variant_mapping = session.scalar(select(MedusaSyncMapping).where(MedusaSyncMapping.entity_type == "variant"))
    assert product_mapping.medusa_id == "prod_1"
    assert variant_mapping.medusa_id == "variant_1"
    create_variant_payload = next(payload for action, payload in clients[0].writes if action == "create_variant")
    assert create_variant_payload["prices"] == [
        {
            "currency_code": "chf",
            "amount": 10,
            "min_quantity": None,
            "max_quantity": None,
            "price_list_code": None,
            "metadata": {"pim_variant_id": variant.id, "source": "pim-pam", "price_type": "sale"},
        }
    ]


def test_export_skips_variant_without_price_with_clear_message(tmp_path) -> None:
    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    config.api_token_secret = "token"
    product = Product(sku="SKU-NOPRICE", handle="no-price", title="No Price Product", status="active", source_language="de-CH")
    variant = ProductVariant(sku="SKU-NOPRICE", variant_title="Default", price=None, currency="CHF")
    product.variants.append(variant)
    session.add(product)
    session.commit()
    clients: list[FakeMedusaClient] = []

    def factory(config):
        client = FakeMedusaClient(config)
        clients.append(client)
        return client

    result = MedusaSyncService(session, client_factory=factory).export_product(product.id, dry_run=False)

    assert result["status"] == "partial_success"
    variant_item = session.scalar(select(MedusaSyncRunItem).where(MedusaSyncRunItem.run_id == result["run_id"], MedusaSyncRunItem.entity_type == "variant"))
    assert variant_item.status == "validation_error"
    assert variant_item.error_message == f"Variante {variant.id} nicht exportiert: Verkaufspreis fehlt."
    assert not any(action == "create_variant" for action, _payload in clients[0].writes)


def test_missing_translation_route_falls_back_to_metadata(tmp_path) -> None:
    from app.db.models import ProductTranslation

    session = _session(tmp_path)
    config = get_or_create_medusa_connection(session)
    config.api_token_secret = "sk_test_1234567890"
    product = Product(sku="SKU-1", handle="demo-product", title="Demo Product", status="active", source_language="de-CH")
    product.translations.append(ProductTranslation(language_code="en", title="Demo Product EN", description="English text"))
    product.variants.append(ProductVariant(sku="SKU-1", variant_title="Default", price=Decimal("10.00"), currency="CHF"))
    session.add(product)
    session.commit()

    result = MedusaSyncService(session, client_factory=lambda config: MissingTranslationRouteClient(config)).export_product(product.id, dry_run=False)
    assert result["status"] == "success"
    assert any("translation:fallback_metadata:success" in key for key in result["summary"]["counts"])
