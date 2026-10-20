from unittest.mock import patch

import pytest

from app.core.exceptions import UnprocessableError
from app.core.url_security import normalize_project_child_url, normalize_project_url


@patch("app.core.url_security._resolve_host_ips", return_value={"93.184.216.34"})
async def test_normalize_project_url_accepts_public_hosts(_mock_resolve) -> None:
    assert (
        await normalize_project_url("https://example.com/path")
        == "https://example.com/path"
    )


@patch("app.core.url_security._resolve_host_ips", return_value={"127.0.0.1"})
async def test_normalize_project_url_rejects_private_hosts(_mock_resolve) -> None:
    with pytest.raises(UnprocessableError, match="private or unsafe"):
        await normalize_project_url("https://example.com")


@patch("app.core.url_security._resolve_host_ips", return_value={"93.184.216.34"})
async def test_normalize_project_child_url_resolves_relative_urls(_mock_resolve) -> None:
    assert (
        await normalize_project_child_url("https://example.com/base", "/about")
        == "https://example.com/about"
    )


@patch("app.core.url_security._resolve_host_ips", return_value={"93.184.216.34"})
async def test_normalize_project_child_url_rejects_cross_host_urls(_mock_resolve) -> None:
    with pytest.raises(UnprocessableError, match="not allowed"):
        await normalize_project_child_url(
            "https://example.com", "https://evil.example.net"
        )
