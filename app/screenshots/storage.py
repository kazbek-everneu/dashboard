import asyncio
import hashlib
import hmac
import logging
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

ARCHIVES_DIRNAME = "archives"
# The per-URL directory name: at most this long, the hash suffix included.
_URL_DIR_MAX_LENGTH = 100
_URL_HASH_LENGTH = 8


def _sanitise_url_path(url: str) -> str:
    """Convert a page URL into a filesystem-safe single directory name.

    Path separators are replaced with underscores so the entire URL path maps
    to one flat directory name, keeping the on-disk layout simple. The fragment
    is discarded. A URL with a query string, or whose name had to be cut to
    100 characters, ends in ``-{sha1(url)[:8]}`` instead, so ``/list?page=1``
    and ``/list?page=2`` (or two long paths with the same start) do not share
    a directory. Simple paths keep their plain name, so existing rows and
    files keep matching.

    Examples:
        https://example.com/           -> "root"
        https://example.com/products   -> "products"
        https://example.com/a/b/c      -> "a_b_c"
        https://example.com/foo?bar=1  -> "foo-<8 hex>"
        https://example.com/?page=2    -> "root-<8 hex>"
        https://example.com/who-we-help -> "who-we-help"
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    # Flatten path separators, then replace every unsafe char with "_"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", path.replace("/", "_"))
    # Collapse runs of underscores that may result from the substitutions
    safe = re.sub(r"_+", "_", safe).strip("_")
    # "." / ".." would name the parent directory, never a page.
    lossy = bool(safe) and not safe.strip(".")
    if lossy:
        safe = ""
    truncated = len(safe) > _URL_DIR_MAX_LENGTH
    if not (parsed.query or truncated or lossy):
        return safe or "root"
    digest = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()
    digest = digest[:_URL_HASH_LENGTH]
    keep = _URL_DIR_MAX_LENGTH - _URL_HASH_LENGTH - 1
    return f"{safe[:keep].rstrip('_') or 'root'}-{digest}"


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


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically: into a temp file in the same
    directory, then ``os.replace`` it over *path*.

    Readers (the file route, the ZIP builder) see the old file or the new one,
    never a partial write; a failed write leaves the old file alone and removes
    the temp file. Blocking — call it through ``asyncio.to_thread``.
    """
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "xb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def project_dir(project_id: uuid.UUID | str) -> Path:
    """Directory holding every screenshot of a project."""
    return Path(settings.DATA_DIR) / str(project_id)


def task_dir(project_id: uuid.UUID | str, task_id: uuid.UUID | str) -> Path:
    """Directory holding every screenshot of a task."""
    return project_dir(project_id) / str(task_id)


def archives_dir() -> Path:
    return Path(settings.DATA_DIR) / ARCHIVES_DIRNAME


def archive_path(task_id: uuid.UUID | str) -> Path:
    """The cached ZIP archive of a task."""
    return archives_dir() / f"{task_id}.zip"


def _is_inside_data_dir(path: Path) -> bool:
    root = Path(settings.DATA_DIR).resolve()
    return root in path.resolve().parents


def remove_paths(paths: Iterable[Path]) -> None:
    """Delete files/directories under DATA_DIR; missing ones are skipped.

    Anything that resolves outside DATA_DIR is refused, and errors are logged
    rather than raised — the DB rows are already gone by the time this runs.
    """
    for path in paths:
        if not _is_inside_data_dir(path):
            logger.error("Refusing to delete %s: not inside DATA_DIR", path)
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete %s: %s", path, exc)


async def delete_task_files(
    project_id: uuid.UUID | str, task_id: uuid.UUID | str
) -> None:
    """Delete a task's screenshot files and its cached archive."""
    await asyncio.to_thread(
        remove_paths, [task_dir(project_id, task_id), archive_path(task_id)]
    )


async def delete_project_files(
    project_id: uuid.UUID | str, task_ids: Iterable[uuid.UUID | str]
) -> None:
    """Delete a project's screenshot files and its tasks' cached archives."""
    paths = [project_dir(project_id), *(archive_path(t) for t in task_ids)]
    await asyncio.to_thread(remove_paths, paths)


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
