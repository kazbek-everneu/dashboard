from dataclasses import dataclass
from datetime import datetime, timezone

from app.projects.service import _default_enabled_sitemap_urls


@dataclass(frozen=True)
class _Entry:
    url: str
    cpt: str
    lastmod: datetime | None
    order: int


def test_latest_post_and_product_only_are_enabled_by_default() -> None:
    entries = [
        _Entry("https://example.com/about", "page", None, 0),
        _Entry("https://example.com/post-old", "post", datetime(2024, 1, 1, tzinfo=timezone.utc), 1),
        _Entry("https://example.com/post-new", "post", datetime(2024, 2, 1, tzinfo=timezone.utc), 2),
        _Entry("https://example.com/product-old", "product", datetime(2024, 3, 1, tzinfo=timezone.utc), 3),
        _Entry("https://example.com/product-new", "product", datetime(2024, 4, 1, tzinfo=timezone.utc), 4),
    ]

    assert _default_enabled_sitemap_urls(entries) == {
        "https://example.com/about",
        "https://example.com/post-new",
        "https://example.com/product-new",
    }


def test_falls_back_to_url_order_when_lastmod_missing() -> None:
    entries = [
        _Entry("https://example.com/post-1", "post", None, 0),
        _Entry("https://example.com/post-2", "post", None, 1),
        _Entry("https://example.com/product-1", "product", None, 2),
        _Entry("https://example.com/product-2", "product", None, 3),
    ]

    assert _default_enabled_sitemap_urls(entries) == {
        "https://example.com/post-2",
        "https://example.com/product-2",
    }
