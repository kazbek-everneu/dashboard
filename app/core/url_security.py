import asyncio
import socket
from urllib.parse import urljoin, urlparse

from app.core.exceptions import UnprocessableError

_ALLOWED_SCHEMES = {"http", "https"}


async def _resolve_host_ips(hostname: str) -> set[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnprocessableError(f"Unable to resolve host {hostname!r}") from exc
    return {info[4][0] for info in infos}


def _is_public_ip(ip_text: str) -> bool:
    import ipaddress
    ip = ipaddress.ip_address(ip_text)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def validate_public_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise UnprocessableError(f"Invalid URL: {url!r}. Must be absolute http(s)://...")

    hostname = parsed.hostname.lower()
    if allowed_hosts is not None and hostname not in allowed_hosts:
        raise UnprocessableError(
            f"URL host {hostname!r} is not allowed for this project"
        )

    for ip_text in await _resolve_host_ips(hostname):
        if not _is_public_ip(ip_text):
            raise UnprocessableError(
                f"URL host {hostname!r} resolves to a private or unsafe address"
            )

    return parsed.geturl()


async def normalize_project_url(url: str) -> str:
    return await validate_public_url(url)


async def normalize_project_child_url(base_url: str, url: str) -> str:
    candidate = urljoin(base_url.rstrip("/") + "/", url)
    base_host = urlparse(base_url).hostname
    allowed_hosts = {base_host.lower()} if base_host else None
    return await validate_public_url(candidate, allowed_hosts=allowed_hosts)
