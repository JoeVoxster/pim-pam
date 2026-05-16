from __future__ import annotations

from contextlib import AbstractContextManager
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from app.config import Settings
from app.suppliers.base import CrawlConfig


class BrowserClient(AbstractContextManager["BrowserClient"]):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "BrowserClient":
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.settings.headless)
        self._context = self._browser.new_context(user_agent=self.settings.user_agent)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def open_page(self, url: str) -> Page:
        if not self._context:
            raise RuntimeError("Browser context not initialized")
        page = self._context.new_page()
        page.set_default_timeout(self.settings.browser_timeout_ms)
        target_url = _coerce_http_url(url)
        page.goto(target_url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        return page

    def crawl_site_urls(self, start_url: str, max_pages: int | None = None, crawl_config: CrawlConfig | None = None) -> list[str]:
        limit = max_pages or self.settings.max_crawl_pages
        crawl_config = crawl_config or CrawlConfig()
        seed = _normalize_url(start_url)
        parsed_seed = urlparse(seed)
        allowed_host = parsed_seed.netloc.lower()
        scope_prefix = _scope_prefix(parsed_seed.path)
        queue: list[str] = [seed]
        queued: set[str] = {seed}
        visited: set[str] = set()
        discovered: list[str] = []

        while queue and len(discovered) < limit:
            current = queue.pop(0)
            if current in visited:
                continue

            page = self.open_page(current)
            try:
                final_url = _normalize_url(page.url)
                if final_url in visited:
                    continue
                visited.add(final_url)
                discovered.append(final_url)
                for link in self._extract_internal_links(page, final_url, allowed_host, scope_prefix, crawl_config):
                    if link in visited or link in queued:
                        continue
                    queue.append(link)
                    queued.add(link)
            finally:
                page.close()

        return discovered

    def _extract_internal_links(
        self,
        page: Page,
        base_url: str,
        allowed_host: str,
        scope_prefix: str | None,
        crawl_config: CrawlConfig,
    ) -> list[str]:
        raw_links: list[dict[str, str]] = page.evaluate(
            """() => Array.from(document.querySelectorAll("a[href]"))
                .map((node) => ({
                    href: node.getAttribute("href") || "",
                    text: (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim(),
                    className: node.className || "",
                }))
                .filter((item) => item.href)"""
        )
        scored_links: list[tuple[tuple[int, int, int, str], str]] = []
        for item in raw_links:
            href = item.get("href", "")
            absolute = _normalize_url(urljoin(base_url, href))
            if not absolute:
                continue
            parsed = urlparse(absolute)
            if parsed.netloc.lower() != allowed_host:
                continue
            if _looks_like_binary_asset(parsed.path):
                continue
            if any(token.lower() in absolute.lower() for token in crawl_config.blocked_url_substrings):
                continue
            if scope_prefix and not crawl_config.allow_cross_scope and not _path_in_scope(parsed.path, scope_prefix):
                continue
            scored_links.append(
                (
                    _crawl_priority(
                        absolute,
                        link_text=item.get("text", ""),
                        class_name=item.get("className", ""),
                        scope_prefix=scope_prefix,
                        crawl_config=crawl_config,
                    ),
                    absolute,
                )
            )
        return _unique([url for _, url in sorted(scored_links, key=lambda item: item[0])])


def _normalize_url(url: str) -> str:
    parsed = urlparse(_coerce_http_url(url))
    if parsed.scheme not in {"http", "https"}:
        return ""
    cleaned = parsed._replace(fragment="")
    path = cleaned.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    cleaned = cleaned._replace(path=path)
    return urlunparse(cleaned)


def _coerce_http_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme in {"http", "https"}:
        return cleaned
    if cleaned.startswith("//"):
        return f"https:{cleaned}"
    return f"https://{cleaned.lstrip('/')}"


def _looks_like_binary_asset(path: str) -> bool:
    lower = path.lower()
    return any(
        lower.endswith(suffix)
        for suffix in (
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".bmp",
            ".svg",
            ".zip",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
        )
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _crawl_priority(
    url: str,
    link_text: str = "",
    class_name: str = "",
    scope_prefix: str | None = None,
    crawl_config: CrawlConfig | None = None,
) -> tuple[int, int, int, str]:
    crawl_config = crawl_config or CrawlConfig()
    lower = url.lower()
    path = urlparse(url).path.lower()
    classes = class_name.lower()
    text = link_text.lower()
    in_scope_rank = 0 if not scope_prefix or _path_in_scope(path, scope_prefix) else 1
    if crawl_config.matches_product_url(path):
        return (in_scope_rank, 0, -len(lower), lower)
    if any(token.lower() in lower for token in crawl_config.preferred_url_substrings):
        return (in_scope_rank, 1, -len(lower), lower)
    if "/product/" in lower:
        return (in_scope_rank, 2, -len(lower), lower)
    if (
        path.endswith(".html")
        and len([segment for segment in path.strip("/").split("/") if segment]) >= 2
        and not any(token in lower for token in ("?dir=", "?mode=", "checkout", "customer", "catalog/product_compare"))
    ):
        return (in_scope_rank, 3, -len(lower), lower)
    if any(token in classes for token in ("product-image", "product-name")):
        return (in_scope_rank, 4, -len(lower), lower)
    if any(marker in lower for marker in crawl_config.pagination_markers) or "next" in classes or "next" in text:
        return (in_scope_rank, 5, len(lower), lower)
    if "/products/" in lower:
        return (in_scope_rank, 6, -len(lower), lower)
    if any(token in lower for token in ("/download/", "/about/", "/news/", "/contacts", "/contact")):
        return (in_scope_rank, 8, len(lower), lower)
    return (in_scope_rank, 5, len(lower), lower)


def _scope_prefix(path: str) -> str | None:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if not segments:
        return None
    if len(segments) == 1 and segments[0].endswith(".html"):
        return f"/{segments[0].rsplit('.', 1)[0]}"
    return f"/{segments[0]}"


def _path_in_scope(path: str, scope_prefix: str) -> bool:
    normalized = path or "/"
    return normalized == scope_prefix or normalized.startswith(f"{scope_prefix}/") or normalized.startswith(f"{scope_prefix}.")
