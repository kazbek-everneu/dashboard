"""Tests for WordPress sitemap parser."""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.projects.sitemap import (
    _NS_SITEMAP,
    _extract_cpt,
    _parse_sitemap_index,
    _parse_urlset,
    SitemapEntry,
    fetch_sitemap_urls,
)


@pytest.fixture(autouse=True)
def _public_dns():
    """Resolve every host to a public IP so the tests never hit real DNS."""
    with patch(
        "app.core.url_security._resolve_host_ips", return_value={"93.184.216.34"}
    ):
        yield


def _ns(tag: str) -> str:
    return f"{{{_NS_SITEMAP}}}{tag}"


def _make_urlset(*entries: str | tuple[str, str]) -> ET.Element:
    root = ET.Element(_ns("urlset"))
    for entry in entries:
        if isinstance(entry, tuple):
            loc, lastmod = entry
        else:
            loc, lastmod = entry, None
        url_el = ET.SubElement(root, _ns("url"))
        ET.SubElement(url_el, _ns("loc")).text = loc
        if lastmod is not None:
            ET.SubElement(url_el, _ns("lastmod")).text = lastmod
    return root


def _make_sitemapindex(*child_urls: str) -> ET.Element:
    root = ET.Element(_ns("sitemapindex"))
    for url in child_urls:
        sm = ET.SubElement(root, _ns("sitemap"))
        ET.SubElement(sm, _ns("loc")).text = url
    return root


# ---------------------------------------------------------------------------
# _extract_cpt
# ---------------------------------------------------------------------------

class TestExtractCpt:
    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/product-sitemap.xml", "product"),
        ("https://example.com/page-sitemap.xml", "page"),
        ("https://example.com/post-sitemap.xml", "post"),
        ("https://example.com/post-sitemap2.xml", "post"),
        ("https://example.com/custom-cpt-sitemap.xml", "custom-cpt"),
        ("/post-sitemap.xml", "post"),
    ])
    def test_known_patterns(self, url: str, expected: str) -> None:
        assert _extract_cpt(url) == expected

    def test_unrecognised_falls_back_to_page(self) -> None:
        assert _extract_cpt("https://example.com/sitemap.xml") == "page"


# ---------------------------------------------------------------------------
# _parse_urlset
# ---------------------------------------------------------------------------

class TestParseUrlset:
    def test_extracts_locs(self) -> None:
        root = _make_urlset(
            "https://example.com/page-1",
            "https://example.com/page-2",
        )
        assert _parse_urlset(root) == [
            ("https://example.com/page-1", None),
            ("https://example.com/page-2", None),
        ]

    def test_empty_urlset(self) -> None:
        root = ET.Element(_ns("urlset"))
        assert _parse_urlset(root) == []

    def test_strips_whitespace(self) -> None:
        root = ET.Element(_ns("urlset"))
        url_el = ET.SubElement(root, _ns("url"))
        ET.SubElement(url_el, _ns("loc")).text = "  https://example.com/  "
        assert _parse_urlset(root) == [("https://example.com/", None)]

    def test_extracts_lastmod_when_present(self) -> None:
        root = _make_urlset(("https://example.com/post-1", "2024-01-02T03:04:05Z"))
        assert _parse_urlset(root) == [
            ("https://example.com/post-1", datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
        ]

    def test_skips_local_sitemap_xml_urls(self) -> None:
        root = _make_urlset(
            "https://example.com/local-sitemap.xml",
            "https://example.com/about",
        )
        assert _parse_urlset(root) == [("https://example.com/about", None)]


# ---------------------------------------------------------------------------
# _parse_sitemap_index
# ---------------------------------------------------------------------------

class TestParseSitemapIndex:
    def test_extracts_child_urls(self) -> None:
        root = _make_sitemapindex(
            "https://example.com/post-sitemap.xml",
            "https://example.com/page-sitemap.xml",
        )
        assert _parse_sitemap_index(root) == [
            "https://example.com/post-sitemap.xml",
            "https://example.com/page-sitemap.xml",
        ]

    def test_empty_index(self) -> None:
        root = ET.Element(_ns("sitemapindex"))
        assert _parse_sitemap_index(root) == []

    def test_skips_local_sitemap_xml_children(self) -> None:
        root = _make_sitemapindex(
            "https://example.com/local-sitemap.xml",
            "https://example.com/post-sitemap.xml",
        )
        assert _parse_sitemap_index(root) == ["https://example.com/post-sitemap.xml"]


# ---------------------------------------------------------------------------
# fetch_sitemap_urls (integration — httpx mocked)
# ---------------------------------------------------------------------------

class TestFetchSitemapUrls:
    @pytest.mark.asyncio
    async def test_full_flow(self) -> None:
        index_xml = ET.tostring(
            _make_sitemapindex(
                "https://example.com/page-sitemap.xml",
                "https://example.com/post-sitemap.xml",
            ),
            encoding="unicode",
        )
        page_xml = ET.tostring(
            _make_urlset("https://example.com/about", "https://example.com/contact"),
            encoding="unicode",
        )
        post_xml = ET.tostring(
            _make_urlset("https://example.com/hello-world"),
            encoding="unicode",
        )

        def _response(url: str, **_kwargs) -> MagicMock:
            text_map = {
                "https://example.com/sitemap_index.xml": index_xml,
                "https://example.com/page-sitemap.xml": page_xml,
                "https://example.com/post-sitemap.xml": post_xml,
            }
            mock = MagicMock()
            mock.text = text_map[str(url)]
            mock.raise_for_status = MagicMock()
            return mock

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.projects.sitemap.httpx.AsyncClient", return_value=mock_client):
            results = await fetch_sitemap_urls("https://example.com")

        assert SitemapEntry("https://example.com/about", "page", None, 0) in results
        assert SitemapEntry("https://example.com/contact", "page", None, 1) in results
        assert SitemapEntry("https://example.com/hello-world", "post", None, 2) in results
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_skips_local_sitemap_everywhere(self) -> None:
        index_xml = ET.tostring(
            _make_sitemapindex(
                "https://example.com/local-sitemap.xml",
                "https://example.com/post-sitemap.xml",
            ),
            encoding="unicode",
        )
        post_xml = ET.tostring(
            _make_urlset(
                "https://example.com/local-sitemap.xml",
                ("https://example.com/latest-post", "2024-05-01T00:00:00Z"),
            ),
            encoding="unicode",
        )

        def _response(url: str, **_kwargs) -> MagicMock:
            text_map = {
                "https://example.com/sitemap_index.xml": index_xml,
                "https://example.com/post-sitemap.xml": post_xml,
            }
            mock = MagicMock()
            mock.text = text_map[str(url)]
            mock.raise_for_status = MagicMock()
            return mock

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.projects.sitemap.httpx.AsyncClient", return_value=mock_client):
            results = await fetch_sitemap_urls("https://example.com")

        assert results == [
            SitemapEntry(
                url="https://example.com/latest-post",
                cpt="post",
                lastmod=datetime(2024, 5, 1, 0, 0, tzinfo=timezone.utc),
                order=0,
            )
        ]

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError("not found", request=MagicMock(), response=mock_response)
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.projects.sitemap.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="HTTP 404"):
                await fetch_sitemap_urls("https://example.com")

    @pytest.mark.asyncio
    async def test_raises_on_wrong_root_element(self) -> None:
        bad_xml = "<rss><channel></channel></rss>"
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.text = bad_xml
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.projects.sitemap.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Unexpected root element"):
                await fetch_sitemap_urls("https://example.com")
