import hashlib
import hmac
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings


def _sanitise_url_path(url: str) -> str:
    """Convert a page URL into a filesystem-safe single directory name.

    The query string and fragment are discarded.  Path separators are replaced
    with underscores so the entire URL path maps to one flat directory name,
    keeping the on-disk layout simple.

    Examples:
        https://example.com/           -> "root"
        https://example.com/products   -> "products"
        https://example.com/a/b/c      -> "a_b_c"
        https://example.com/foo?bar=1  -> "foo"
        https://example.com/who-we-help -> "who-we-help"
    """
    path = urlparse(url).path.strip("/")
    if not path:
        return "root"
    # Flatten path separators, then replace every unsafe char with "_"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", path.replace("/", "_"))
    # Collapse runs of underscores that may result from the substitutions
    safe = re.sub(r"_+", "_", safe).strip("_")
    # Guard against edge-cases that produce an empty string
    return safe[:100] or "root"


def build_file_path(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    url: str,
    width: int,
    height: int,
    browser: str,
    fmt: str,
) -> str:
    """Return the relative file path (under DATA_DIR) for a screenshot.

    Pattern:
        {project_id}/{task_id}/{sanitised_url_path}/{width}x{height}_{browser}.jpg|png
    """
    ext = "jpg" if fmt == "jpeg" else "png"
    url_dir = _sanitise_url_path(url)
    filename = f"{width}x{height}_{browser}.{ext}"
    # Use posix separators so the path is consistent on Linux (Docker target)
    return f"{project_id}/{task_id}/{url_dir}/{filename}"


def abs_file_path(relative_path: str) -> Path:
    """Resolve a relative storage path to an absolute filesystem path."""
    return Path(settings.DATA_DIR) / relative_path


def ensure_parent(relative_path: str) -> None:
    """Create all intermediate directories for relative_path if they don't exist."""
    abs_file_path(relative_path).parent.mkdir(parents=True, exist_ok=True)


def _build_token(message: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_signed_url(
    task_id: str,
    filename: str,
    expires_at: int | None = None,
) -> str:
    """Return an HMAC-SHA256-signed URL for serving a screenshot file.

    No expiry.  The server re-validates the HMAC on every request.
    ``expires_at`` is retained for old callers but no longer affects the URL.

    filename is the portion of the path after ``/files/{task_id}/``,
    i.e. ``{url_dir}/{width}x{height}_{browser}.{fmt}``.
    """
    token = _build_token(f"{task_id}:{filename}")
    return f"/files/{task_id}/{filename}?token={token}"


def verify_signed_url(
    task_id: str,
    filename: str,
    expires: int | str | None = None,
    token: str | None = None,
) -> bool:
    """Return True if *token* is the valid HMAC for (task_id, filename).

    Supports both permanent token-only URLs and legacy URLs signed with an
    ``expires`` value.  Legacy expiry timestamps are not time-limited anymore.
    Uses ``hmac.compare_digest`` to prevent timing attacks.
    """
    if token is None and isinstance(expires, str):
        token = expires
        expires = None

    if not token:
        return False

    expected = _build_token(f"{task_id}:{filename}")
    if hmac.compare_digest(expected, token):
        return True

    if expires is None:
        return False

    legacy_expected = _build_token(f"{task_id}:{filename}:{expires}")
    return hmac.compare_digest(legacy_expected, token)


def generate_archive_signed_url(task_id: str, expires_at: int | None = None) -> str:
    """Return an HMAC-signed URL for downloading a task archive ZIP.

    Uses a distinct message prefix (``archives/``) to avoid any token
    collision with screenshot signed URLs.  ``expires_at`` is retained for old
    callers but no longer affects the URL.
    """
    filename = f"{task_id}.zip"
    token = _build_token(f"archives/{filename}")
    return f"/archives/{filename}?token={token}"


def verify_archive_token(
    filename: str,
    expires: int | str | None = None,
    token: str | None = None,
) -> bool:
    """Return True if *token* is the valid HMAC for the given archive filename."""
    if token is None and isinstance(expires, str):
        token = expires
        expires = None

    if not token:
        return False

    expected = _build_token(f"archives/{filename}")
    if hmac.compare_digest(expected, token):
        return True

    if expires is None:
        return False

    legacy_expected = _build_token(f"archives/{filename}:{expires}")
    return hmac.compare_digest(legacy_expected, token)
