from __future__ import annotations

import importlib
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from playwright.sync_api import Page
from slugify import slugify

from app.models import AssetReference, ProductInputRow, ScrapedData
from app.scraping.extractors import extract_generic_product_data


@dataclass(slots=True)
class CrawlConfig:
    preferred_url_substrings: tuple[str, ...] = ()
    blocked_url_substrings: tuple[str, ...] = ()
    product_url_patterns: tuple[str, ...] = ()
    pagination_markers: tuple[str, ...] = ("?p=", "&p=")
    allow_cross_scope: bool = False

    def matches_product_url(self, url: str) -> bool:
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in self.product_url_patterns)


@dataclass(frozen=True, slots=True)
class SupplierAssetCandidate:
    asset_url: str
    asset_type: str
    title: str | None = None
    filename: str | None = None
    language: str | None = None
    region: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class SupplierExtractionResult:
    supplier_key: str
    supplier_name: str
    source_url: str
    source_domain: str
    detected_language: str | None = None
    source_locale: str | None = None
    product_code: str | None = None
    sku: str | None = None
    product_name: str | None = None
    short_description: str | None = None
    description: str | None = None
    specifications: str | None = None
    how_to_use: str | None = None
    quantity_for_use: str | None = None
    warning: str | None = None
    ingredients: str | None = None
    ingredient_search: str | None = None
    function: str | None = None
    packaging: str | None = None
    pdfs: list[SupplierAssetCandidate] = field(default_factory=list)
    images: list[SupplierAssetCandidate] = field(default_factory=list)
    raw_sections: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_scraped_data(self) -> ScrapedData:
        asset_references = [
            AssetReference(
                url=item.asset_url,
                asset_type="pdf",
                role=item.role or item.asset_type,
                label=item.title or item.filename or item.role,
                page_url=self.source_url,
            )
            for item in self.pdfs
        ]
        asset_references.extend(
            AssetReference(
                url=item.asset_url,
                asset_type="image",
                role="image",
                label=item.title or item.filename,
                page_url=self.source_url,
            )
            for item in self.images
        )
        specifications = [self.specifications] if self.specifications else []
        technical_features = [
            value
            for value in (
                f"How To Use: {self.how_to_use}" if self.how_to_use else None,
                f"Ingredients: {self.ingredients}" if self.ingredients else None,
                f"Ingredient Search: {self.ingredient_search}" if self.ingredient_search else None,
                f"Function: {self.function}" if self.function else None,
            )
            if value
        ]
        return ScrapedData(
            source_url_final=self.source_url,
            supplier_sku=self.product_code or self.sku,
            product_name=self.product_name,
            product_title=self.product_name,
            description=self.description or self.short_description,
            specifications=specifications,
            technical_features=technical_features,
            image_urls=[item.asset_url for item in self.images],
            pdf_urls=[item.asset_url for item in self.pdfs],
            datasheet_urls=[item.asset_url for item in self.pdfs if item.role == "technical_datasheet"],
            sds_urls=[item.asset_url for item in self.pdfs if item.role == "sds"],
            asset_references=asset_references,
            page_title=self.product_name,
            is_product_candidate=bool(self.product_name or self.product_code or self.description),
            has_product_view=True,
            extra_fields={
                "supplier_key": self.supplier_key,
                "supplier_name": self.supplier_name,
                "detected_language": self.detected_language,
                "source_locale": self.source_locale,
                "supplier_extraction_result": asdict(self),
            },
        )


class BaseSupplierExtractor(ABC):
    supplier_key = "generic"
    supplier_name = "Generic"
    supported_domains: tuple[str, ...] = ()

    def can_handle(self, url: str) -> bool:
        host = urlparse(url or "").netloc.lower()
        return bool(host and any(domain in host for domain in self.supported_domains))

    @abstractmethod
    def extract(self, page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
        raise NotImplementedError

    def crawl_config(self, start_url: str | None = None) -> CrawlConfig:
        return CrawlConfig()

    def classify_product_candidate(self, page_url: str, scraped: ScrapedData) -> bool:
        return scraped.is_product_candidate


class GenericSupplierExtractor(BaseSupplierExtractor):
    supplier_key = "generic"

    def extract(self, page: Page, source_url: str, row: ProductInputRow) -> ScrapedData:
        return extract_generic_product_data(page, source_url or row.source_url or page.url, row)


def get_supplier_extractor(supplier_name: str | None, source_url: str | None = None) -> BaseSupplierExtractor:
    module_candidates: list[str] = []
    if supplier_name:
        module_candidates.append(slugify(supplier_name, separator="_"))
    if source_url:
        host = urlparse(source_url).netloc.lower()
        if "voxster.ch" in host:
            module_candidates.append("voxster")
    if not module_candidates:
        return GenericSupplierExtractor()
    seen: set[str] = set()
    unique_candidates = [candidate for candidate in module_candidates if candidate and not (candidate in seen or seen.add(candidate))]
    for module_name in unique_candidates:
        try:
            module = importlib.import_module(f"app.suppliers.{module_name}")
        except ModuleNotFoundError:
            continue
        extractor_class = getattr(module, "SupplierExtractor", None)
        if extractor_class is not None:
            return extractor_class()
    return GenericSupplierExtractor()
