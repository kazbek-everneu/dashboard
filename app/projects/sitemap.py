from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import ElementTree

from app.core.exceptions import UnprocessableError
from app.core.url_security import (
    normalize_project_child_url,
    normalize_project_url,
    validate_public_url,
)

logger = logging.getLogger(__name__)

# WordPress sitemap XML namespaces
_NS_SITEMAP = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Matches e.g. "product-sitemap.xml", "page-sitemap2.xml"
_CPT_RE = re.compile(r"^([a-z0-9_-]+?)-sitemap\d*\.xml$", re.IGNORECASE)
_LOCAL_SITEMAP_FILENAME = "local-sitemap.xml"
_MAX_REDIRECTS = 5


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    cpt: str
    lastmod: datetime | None
    order: int


def _extract_cpt(sitemap_url: str) -> str:
    """Derive CPT name from a child sitemap filename.

    Examples:
      product-sitemap.xml   -> "product"
      page-sitemap.xml      -> "page"
      post-sitemap2.xml     -> "post"
      /some/path/foo-sitemap.xml -> "foo"
    """
    filename = urlparse(sitemap_url).path.rstrip("/").rsplit("/", 1)[-1]
    match = _CPT_RE.match(filename)
    if match:
        return match.group(1).lower()
    logger.warning(
        "Could not extract CPT from sitemap URL %r; defaulting to 'page'",
        sitemap_url,
    )
    return "page"


def _basename(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].lower()


def _should_skip_sitemap_url(url: str) -> bool:
    return _basename(url) == _LOCAL_SITEMAP_FILENAME


def _tag(local: str) -> str:
    return f"{{{_NS_SITEMAP}}}{local}"


def _parse_sitemap_index(root: ElementTree.Element) -> list[str]:
    """Return child sitemap <loc> URLs from a <sitemapindex> element."""
    return [
        loc.text.strip()
        for sm in root.findall(_tag("sitemap"))
        for loc in sm.findall(_tag("loc"))
        if loc.text and not _should_skip_sitemap_url(loc.text.strip())
    ]


def _parse_lastmod(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_urlset(root: ElementTree.Element) -> list[tuple[str, datetime | None]]:
    """Return page <loc> URLs from a <urlset> element with optional lastmod."""
    parsed: list[tuple[str, datetime | None]] = []
    for url in root.findall(_tag("url")):
        loc = url.find(_tag("loc"))
        if loc is None or not loc.text:
            continue
        normalized_loc = loc.text.strip()
        if _should_skip_sitemap_url(normalized_loc):
            continue
        lastmod = _parse_lastmod(url.findtext(_tag("lastmod")))
        parsed.append((normalized_loc, lastmod))
    return parsed


async def _get_with_safe_redirects(
    client: httpx.AsyncClient, url: str
) -> httpx.Response:
    """GET *url*, following redirects only to public http(s) addresses.

    httpx's own ``follow_redirects`` would fetch any target, so a public site
    could bounce the request to an internal host (Redis, cloud metadata, ...).
    Every hop is re-validated with the same check as the project URL.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        response = await client.get(current, follow_redirects=False, timeout=30)
        if not response.is_redirect:
            return response
        target = urljoin(current, response.headers["location"])
        try:
            current = await validate_public_url(target)
        except UnprocessableError as exc:
            raise RuntimeError(
                f"Refusing redirect from {current} to {target}: {exc.detail}"
            ) from exc
    raise RuntimeError(f"Too many redirects fetching {url}")


async def _fetch_xml(client: httpx.AsyncClient, url: str) -> ElementTree.Element:
    """Fetch a URL and parse as XML. Raises on HTTP or parse errors."""
    try:
        response = await _get_with_safe_redirects(client, url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"HTTP {exc.response.status_code} fetching {url}"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error fetching {url}: {exc}") from exc

    if len(response.content) > 10 * 1024 * 1024:
        raise RuntimeError(f"Sitemap response too large (>10 MB) at {url}")

    try:
        return ElementTree.fromstring(response.text)
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"XML parse error for {url}: {exc}") from exc


async def fetch_sitemap_urls(base_url: str) -> list[SitemapEntry]:
    """Fetch and parse WordPress sitemap.

    Tries <base_url>/sitemap_index.xml first, then <base_url>/sitemap.xml.
    Raises RuntimeError on any fetch or parse failure — never silently drops URLs.
    """
    safe_base_url = await normalize_project_url(base_url)
    index_urls = [
        urljoin(safe_base_url.rstrip("/") + "/", "sitemap_index.xml"),
        urljoin(safe_base_url.rstrip("/") + "/", "sitemap.xml"),
        urljoin(safe_base_url.rstrip("/") + "/", "wp-sitemap.xml"),  # WordPress
    ]

    for index_url in index_urls:
        try:
            logger.info("Trying sitemap URL: %s", index_url)
            headers = {"User-Agent": "QA-Screenshot-Bot/1.0"}
            async with httpx.AsyncClient(headers=headers) as client:
                root = await _fetch_xml(client, index_url)

                # Determine document type
                if root.tag == _tag("sitemapindex"):
                    child_urls = _parse_sitemap_index(root)
                    if not child_urls:
                        raise RuntimeError(
                            f"Sitemap index at {index_url} contains no child sitemaps"
                        )
                    logger.info("Found %d child sitemaps", len(child_urls))
                elif root.tag == _tag("urlset"):
                    # Single urlset at the index URL — treat as CPT "page"
                    logger.info(
                        "Sitemap is a urlset (single sitemap), treating CPT as 'page'"
                    )
                    urls = _parse_urlset(root)
                    logger.info("Parsed %d URLs", len(urls))
                    entries: list[SitemapEntry] = []
                    for order, (url, lastmod) in enumerate(urls):
                        safe_url = await normalize_project_child_url(safe_base_url, url)
                        entries.append(
                            SitemapEntry(
                                url=safe_url,
                                cpt="page",
                                lastmod=lastmod,
                                order=order,
                            )
                        )
                    return entries
                else:
                    raise RuntimeError(
                        f"Unexpected root element {root.tag!r} at {index_url}. "
                        "Expected <sitemapindex> or <urlset>."
                    )

                # Fetch each child sitemap
                results: list[SitemapEntry] = []
                order = 0
                for child_url in child_urls:
                    cpt = _extract_cpt(child_url)
                    logger.info("Fetching child sitemap %s (cpt=%s)", child_url, cpt)
                    try:
                        safe_child_url = await normalize_project_child_url(
                            safe_base_url,
                            child_url,
                        )
                        child_root = await _fetch_xml(client, safe_child_url)
                    except RuntimeError as exc:
                        logger.error(
                            "Failed to fetch child sitemap %s: %s",
                            child_url,
                            exc,
                        )
                        raise

                    if child_root.tag != _tag("urlset"):
                        raise RuntimeError(
                            f"Expected <urlset> in child sitemap {safe_child_url}, "
                            f"got {child_root.tag!r}"
                        )

                    page_urls = _parse_urlset(child_root)
                    if not page_urls:
                        logger.warning(
                            "Child sitemap %s contained no URLs",
                            safe_child_url,
                        )

                    for url, lastmod in page_urls:
                        safe_url = await normalize_project_child_url(
                            safe_base_url,
                            url,
                        )
                        results.append(
                            SitemapEntry(
                                url=safe_url,
                                cpt=cpt,
                                lastmod=lastmod,
                                order=order,
                            )
                        )
                        order += 1
                    logger.info(
                        "Parsed %d URLs from %s",
                        len(page_urls),
                        safe_child_url,
                    )

                logger.info("Total URLs parsed: %d", len(results))
                return results
        except RuntimeError:
            if index_url == index_urls[-1]:
                raise
            continue
    else:
        raise RuntimeError(f"No sitemap found at {index_urls}")
