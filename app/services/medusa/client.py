from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests

from app.db.models import MedusaConnectionConfig

LOGGER = logging.getLogger(__name__)


class MedusaApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, response_payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_payload = response_payload


class MedusaAuthError(MedusaApiError):
    pass


class MedusaAdminApiClient:
    """Small Admin REST client for Medusa v2 `/admin`.

    The client intentionally does not know PIM entities. It only handles URL composition,
    auth, retries and endpoint wrappers used by the sync service.
    """

    def __init__(self, config: MedusaConnectionConfig, *, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    @property
    def admin_url(self) -> str:
        base = (self.config.base_url or "").rstrip("/")
        path = "/" + (self.config.admin_path or "/admin").strip("/")
        if not base:
            raise MedusaApiError("Medusa base_url ist nicht konfiguriert.")
        return f"{base}{path}"

    def test_connection(self) -> dict[str, Any]:
        payload = self.request("GET", "/products", params={"limit": 1, "fields": "id,title,handle"})
        if not isinstance(payload, dict) or "products" not in payload:
            raise MedusaApiError(
                "Medusa Admin API Test hat keine Produkt-API-Antwort geliefert. "
                "Prüfe Base URL und Admin Path; für Admin REST muss der Pfad normalerweise /admin sein.",
                response_payload=payload,
            )
        return {"status": "ok", "message": "Verbindung zur Medusa Admin API erfolgreich.", "response": payload}

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = self._url(path)
        headers = self._headers()
        timeout = int(self.config.timeout_seconds or 30)
        retry_count = int(self.config.retry_count or 0)
        backoff = int(self.config.retry_backoff_seconds or 1)
        last_exc: Exception | None = None
        for attempt in range(retry_count + 1):
            try:
                self._log_request(method, url, params=params, json=json)
                response = self.session.request(
                    method.upper(),
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=timeout,
                    verify=bool(self.config.verify_ssl),
                )
                return self._handle_response(response)
            except MedusaAuthError:
                raise
            except MedusaApiError as exc:
                if exc.status_code in {429, 500, 502, 503, 504} and attempt < retry_count:
                    time.sleep(backoff * (attempt + 1))
                    last_exc = exc
                    continue
                raise
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retry_count:
                    time.sleep(backoff * (attempt + 1))
                    continue
                raise MedusaApiError(f"Medusa Request fehlgeschlagen: {exc}") from exc
        raise MedusaApiError(f"Medusa Request fehlgeschlagen: {last_exc}")

    def paginate(self, path: str, *, params: dict[str, Any] | None = None, item_key: str | None = None) -> list[dict[str, Any]]:
        limit = int((params or {}).get("limit") or 100)
        offset = int((params or {}).get("offset") or 0)
        rows: list[dict[str, Any]] = []
        while True:
            page_params = {**(params or {}), "limit": limit, "offset": offset}
            payload = self.request("GET", path, params=page_params)
            key = item_key or _first_collection_key(payload)
            page_rows = list(payload.get(key, [])) if isinstance(payload, dict) and key else []
            rows.extend(page_rows)
            count = payload.get("count") if isinstance(payload, dict) else None
            if not page_rows or count is None or len(rows) >= int(count):
                break
            offset += limit
        return rows

    def list_products(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", "/products", params=params)

    def get_product(self, product_id: str, fields: str | None = None) -> dict[str, Any]:
        params = {"fields": fields} if fields else None
        return self.request("GET", f"/products/{product_id}", params=params)

    def find_product_by_handle(self, handle: str) -> dict[str, Any] | None:
        payload = self.list_products({"handle": handle, "limit": 2})
        products = _extract_list(payload, "products")
        return products[0] if products else None

    def find_product_by_external_id_or_metadata(self, pim_product_id: int) -> dict[str, Any] | None:
        payload = self.list_products({"q": str(pim_product_id), "limit": 100})
        for product in _extract_list(payload, "products"):
            metadata = product.get("metadata") or {}
            if str(metadata.get("pim_product_id")) == str(pim_product_id):
                return product
            if str(product.get("external_id") or "") in {f"pim:{pim_product_id}", str(pim_product_id)}:
                return product
        return None

    def create_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/products", json=payload)

    def update_product(self, product_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/products/{product_id}", json=payload)

    def list_product_variants(self, product_id: str) -> list[dict[str, Any]]:
        payload = self.get_product(product_id, fields="*variants")
        product = payload.get("product", payload) if isinstance(payload, dict) else {}
        return list(product.get("variants") or payload.get("variants") or [])

    def get_variant(self, variant_id: str) -> dict[str, Any]:
        return self.request("GET", f"/product-variants/{variant_id}")

    def find_variant_by_sku(self, product_id: str, sku: str) -> dict[str, Any] | None:
        for variant in self.list_product_variants(product_id):
            if str(variant.get("sku") or "") == sku:
                return variant
        return None

    def create_variant(self, product_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/products/{product_id}/variants", json=payload)

    def update_variant(self, product_id: str, variant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/products/{product_id}/variants/{variant_id}", json=payload)

    def list_categories(self) -> dict[str, Any]:
        return self.request("GET", "/product-categories", params={"limit": 100})

    def create_or_update_category(self, payload: dict[str, Any]) -> dict[str, Any]:
        category_id = payload.pop("id", None)
        if category_id:
            return self.request("POST", f"/product-categories/{category_id}", json=payload)
        return self.request("POST", "/product-categories", json=payload)

    def list_collections(self) -> dict[str, Any]:
        return self.request("GET", "/collections", params={"limit": 100})

    def create_or_update_collection(self, payload: dict[str, Any]) -> dict[str, Any]:
        collection_id = payload.pop("id", None)
        if collection_id:
            return self.request("POST", f"/collections/{collection_id}", json=payload)
        return self.request("POST", "/collections", json=payload)

    def list_tags(self) -> dict[str, Any]:
        return self.request("GET", "/product-tags", params={"limit": 100})

    def create_or_update_tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        tag_id = payload.pop("id", None)
        if tag_id:
            return self.request("POST", f"/product-tags/{tag_id}", json=payload)
        return self.request("POST", "/product-tags", json=payload)

    def list_types(self) -> dict[str, Any]:
        return self.request("GET", "/product-types", params={"limit": 100})

    def create_or_update_type(self, payload: dict[str, Any]) -> dict[str, Any]:
        type_id = payload.pop("id", None)
        if type_id:
            return self.request("POST", f"/product-types/{type_id}", json=payload)
        return self.request("POST", "/product-types", json=payload)

    def list_price_lists(self) -> dict[str, Any]:
        return self.request("GET", "/price-lists", params={"limit": 100})

    def get_price_list(self, price_list_id: str) -> dict[str, Any]:
        return self.request("GET", f"/price-lists/{price_list_id}")

    def create_price_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/price-lists", json=payload)

    def update_price_list(self, price_list_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/price-lists/{price_list_id}", json=payload)

    def upsert_variant_prices(self, product_id: str, variant_id: str, prices_payload: dict[str, Any]) -> dict[str, Any]:
        return self.update_variant(product_id, variant_id, prices_payload)

    def list_locales(self) -> dict[str, Any]:
        return self.request("GET", "/locales", params={"limit": 100})

    def upsert_translation(self, reference: str, reference_id: str, locale_code: str, translations: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            "/pim-sync/translations",
            json={"reference": reference, "reference_id": reference_id, "locale_code": locale_code, "translations": translations},
        )

    def sync_category_product_positions(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/pim/category-product-positions", json=payload)

    def list_products_for_mapping_repair(self) -> list[dict[str, Any]]:
        return self.paginate("/products", params={"limit": 100, "fields": "id,title,handle,external_id,metadata,variants"}, item_key="products")

    def list_variants_for_mapping_repair(self, product_id: str) -> list[dict[str, Any]]:
        return self.list_product_variants(product_id)

    def _url(self, path: str) -> str:
        return f"{self.admin_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json", "x-no-compression": "true"}
        token = (self.config.api_token_secret or "").strip()
        if self.config.auth_type == "api_token":
            if not token:
                raise MedusaAuthError("Medusa Admin API Token fehlt.", status_code=401)
            headers["Authorization"] = f"Basic {token}"
        elif self.config.auth_type == "jwt":
            raise MedusaAuthError("JWT-Login ist vorbereitet, aber noch nicht sicher konfiguriert. Bitte API Token verwenden.", status_code=401)
        return headers

    def _handle_response(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except ValueError:
            payload = {"text": response.text[:2000]}
        if response.status_code in {401, 403}:
            raise MedusaAuthError("Medusa Authentifizierung fehlgeschlagen.", status_code=response.status_code, response_payload=payload)
        if response.status_code >= 400:
            raise MedusaApiError(
                f"Medusa API Fehler {response.status_code}: {_error_message(payload)}",
                status_code=response.status_code,
                response_payload=payload,
            )
        return payload

    def _log_request(self, method: str, url: str, *, params: dict[str, Any] | None, json: dict[str, Any] | None) -> None:
        safe_payload = _redact(json)
        query = f"?{urlencode(params)}" if params else ""
        LOGGER.debug("Medusa request %s %s%s payload=%s", method.upper(), url, query, safe_payload)


def _extract_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key)
        return list(value or []) if isinstance(value, list) else []
    return []


def _first_collection_key(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key, value in payload.items():
        if isinstance(value, list):
            return key
    return None


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)[:1000]
    return str(payload)[:1000]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***" if "token" in key.lower() or "secret" in key.lower() or "password" in key.lower() else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
