from __future__ import annotations

from app.suppliers.base import BaseSupplierExtractor, GenericSupplierExtractor


EXTRACTOR_MODULES = (
    "app.suppliers.tintolav",
)


def supplier_extractors() -> list[BaseSupplierExtractor]:
    extractors: list[BaseSupplierExtractor] = []
    for module_name in EXTRACTOR_MODULES:
        try:
            module = __import__(module_name, fromlist=["SupplierExtractor"])
        except ModuleNotFoundError:
            continue
        extractor_class = getattr(module, "SupplierExtractor", None)
        if extractor_class is not None:
            extractors.append(extractor_class())
    extractors.append(GenericSupplierExtractor())
    return extractors


def find_extractor_for_url(url: str | None) -> BaseSupplierExtractor:
    for extractor in supplier_extractors():
        if extractor.can_handle(url or ""):
            return extractor
    return GenericSupplierExtractor()

