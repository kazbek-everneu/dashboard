"""Tests for HMAC signed URL generation and file path building."""
import hashlib
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.screenshots.storage import (
    _build_token,
    _sanitise_url_path,
    build_file_path,
    generate_archive_signed_url,
    generate_signed_url,
    verify_archive_token,
    verify_signed_url,
    write_bytes_atomic,
)

# ---------------------------------------------------------------------------
# Signed URL
# ---------------------------------------------------------------------------

class TestSignedUrl:
    def test_generate_returns_correct_format(self) -> None:
        task_id = str(uuid.uuid4())
        filename = "abc/1920x1080_chromium.jpeg"
        url = generate_signed_url(task_id, filename)
        assert url.startswith(f"/files/{task_id}/{filename}?token=")
        assert "expires=" not in url

    def test_verify_accepts_correct_token(self) -> None:
        task_id = str(uuid.uuid4())
        filename = "path/to/1280x800_firefox.jpeg"
        url = generate_signed_url(task_id, filename)
        query = url.split("?", 1)[1]
        parts = dict(part.split("=", 1) for part in query.split("&"))
        assert verify_signed_url(task_id, filename, parts["token"]) is True

    def test_verify_rejects_wrong_filename(self) -> None:
        task_id = str(uuid.uuid4())
        filename = "path/to/1280x800_firefox.jpeg"
        url = generate_signed_url(task_id, filename)
        query = url.split("?", 1)[1]
        parts = dict(part.split("=", 1) for part in query.split("&"))
        assert verify_signed_url(task_id, "other/file.jpeg", parts["token"]) is False

    def test_verify_rejects_wrong_task_id(self) -> None:
        task_id = str(uuid.uuid4())
        other_task_id = str(uuid.uuid4())
        filename = "path/to/file.jpeg"
        url = generate_signed_url(task_id, filename)
        query = url.split("?", 1)[1]
        parts = dict(part.split("=", 1) for part in query.split("&"))
        assert verify_signed_url(other_task_id, filename, parts["token"]) is False

    def test_verify_rejects_tampered_token(self) -> None:
        task_id = str(uuid.uuid4())
        filename = "path/to/file.jpeg"
        assert verify_signed_url(task_id, filename, "deadbeef") is False

    def test_verify_rejects_empty_token(self) -> None:
        task_id = str(uuid.uuid4())
        assert verify_signed_url(task_id, "file.jpeg", "") is False

    def test_verify_accepts_legacy_expired_token(self) -> None:
        task_id = str(uuid.uuid4())
        filename = "file.jpeg"
        expires = int(time.time()) - 1
        token = _build_token(f"{task_id}:{filename}:{expires}")
        assert verify_signed_url(task_id, filename, expires, token) is True

    def test_tokens_differ_for_different_inputs(self) -> None:
        task_a = str(uuid.uuid4())
        task_b = str(uuid.uuid4())
        filename = "file.jpeg"
        url_a = generate_signed_url(task_a, filename)
        url_b = generate_signed_url(task_b, filename)
        token_a = dict(
            part.split("=", 1) for part in url_a.split("?", 1)[1].split("&")
        )["token"]
        token_b = dict(
            part.split("=", 1) for part in url_b.split("?", 1)[1].split("&")
        )["token"]
        assert token_a != token_b


class TestArchiveSignedUrl:
    def test_generate_returns_correct_format(self) -> None:
        task_id = str(uuid.uuid4())
        url = generate_archive_signed_url(task_id)
        assert url.startswith(f"/archives/{task_id}.zip?token=")
        assert "expires=" not in url

    def test_verify_accepts_correct_token(self) -> None:
        task_id = str(uuid.uuid4())
        filename = f"{task_id}.zip"
        url = generate_archive_signed_url(task_id)
        query = url.split("?", 1)[1]
        parts = dict(part.split("=", 1) for part in query.split("&"))
        assert verify_archive_token(filename, parts["token"]) is True

    def test_verify_rejects_wrong_filename(self) -> None:
        task_id = str(uuid.uuid4())
        url = generate_archive_signed_url(task_id)
        query = url.split("?", 1)[1]
        parts = dict(part.split("=", 1) for part in query.split("&"))
        assert verify_archive_token(f"{uuid.uuid4()}.zip", parts["token"]) is False

    def test_verify_rejects_tampered_token(self) -> None:
        assert verify_archive_token(f"{uuid.uuid4()}.zip", "deadbeef") is False

    def test_verify_rejects_empty_token(self) -> None:
        assert verify_archive_token(f"{uuid.uuid4()}.zip", "") is False

    def test_verify_accepts_legacy_expired_token(self) -> None:
        task_id = str(uuid.uuid4())
        filename = f"{task_id}.zip"
        expires = int(time.time()) - 1
        token = _build_token(f"archives/{filename}:{expires}")
        assert verify_archive_token(filename, expires, token) is True


# ---------------------------------------------------------------------------
# URL path sanitisation
# ---------------------------------------------------------------------------

class TestSanitiseUrlPath:
    @pytest.mark.parametrize("url,expected", [
        ("https://example.com/", "root"),
        ("https://example.com", "root"),
        ("https://example.com/products", "products"),
        ("https://example.com/products/", "products"),
        ("https://example.com/a/b/c", "a_b_c"),
        ("https://example.com/who-we-help", "who-we-help"),
        ("https://example.com/shop/category/shoes", "shop_category_shoes"),
        ("https://example.com/foo#section", "foo"),
        ("https://example.com/" + "a" * 100, "a" * 100),
    ])
    def test_sanitise(self, url: str, expected: str) -> None:
        # Simple paths keep their plain name (existing files keep matching).
        assert _sanitise_url_path(url) == expected

    def test_long_path_is_truncated(self) -> None:
        long_path = "a" * 200
        result = _sanitise_url_path(f"https://example.com/{long_path}")
        assert len(result) <= 100

    @staticmethod
    def _digest(url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()[:8]

    @pytest.mark.parametrize(
        ("url", "stem"),
        [
            ("https://example.com/foo?bar=1&baz=2", "foo"),
            ("https://example.com/?page=2", "root"),
            ("https://example.com/a/b/?utm_source=x", "a_b"),
        ],
    )
    def test_query_string_adds_the_url_hash(self, url: str, stem: str) -> None:
        assert _sanitise_url_path(url) == f"{stem}-{self._digest(url)}"

    def test_query_strings_do_not_share_a_directory(self) -> None:
        names = {
            _sanitise_url_path("https://example.com/list"),
            _sanitise_url_path("https://example.com/list?page=1"),
            _sanitise_url_path("https://example.com/list?page=2"),
        }
        assert len(names) == 3
        assert "list" in names

    def test_truncated_names_get_the_hash_and_stay_distinct(self) -> None:
        url_a = "https://example.com/" + "a" * 150 + "/one"
        url_b = "https://example.com/" + "a" * 150 + "/two"
        name_a = _sanitise_url_path(url_a)
        name_b = _sanitise_url_path(url_b)

        assert name_a != name_b
        assert len(name_a) == len(name_b) == 100
        assert name_a == "a" * 91 + "-" + self._digest(url_a)

    @pytest.mark.parametrize("path", [".", "..", "./"])
    def test_dot_only_names_never_point_at_a_parent(self, path: str) -> None:
        url = f"https://example.com/{path}"
        assert _sanitise_url_path(url) == f"root-{self._digest(url)}"


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

class TestWriteBytesAtomic:
    def test_writes_and_replaces(self, tmp_path: Path) -> None:
        target = tmp_path / "shot.jpg"
        write_bytes_atomic(target, b"one")
        write_bytes_atomic(target, b"two")

        assert target.read_bytes() == b"two"
        assert [p.name for p in tmp_path.iterdir()] == ["shot.jpg"]

    def test_failure_keeps_the_old_file_and_removes_the_temp(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "shot.jpg"
        target.write_bytes(b"old")

        with (
            patch("app.screenshots.storage.os.replace", side_effect=OSError("boom")),
            pytest.raises(OSError, match="boom"),
        ):
            write_bytes_atomic(target, b"new")

        assert target.read_bytes() == b"old"
        assert [p.name for p in tmp_path.iterdir()] == ["shot.jpg"]

    def test_temp_file_lives_next_to_the_target(self, tmp_path: Path) -> None:
        target = tmp_path / "shot.jpg"
        seen: list[tuple[str, str]] = []
        real_replace = os.replace

        def spy(src: str, dst: str) -> None:
            seen.append((str(src), str(dst)))
            real_replace(src, dst)

        with patch("app.screenshots.storage.os.replace", side_effect=spy):
            write_bytes_atomic(target, b"x")

        (src, dst), = seen
        assert Path(src).parent == tmp_path
        assert Path(src).name.startswith(".shot.jpg.")
        assert dst == str(target)


# ---------------------------------------------------------------------------
# build_file_path
# ---------------------------------------------------------------------------

class TestBuildFilePath:
    def test_structure(self) -> None:
        project_id = uuid.uuid4()
        task_id = uuid.uuid4()
        path = build_file_path(
            project_id,
            task_id,
            "https://x.com/page",
            1920,
            1080,
            "chromium",
            "jpeg",
        )
        parts = path.replace("\\", "/").split("/")
        assert parts[0] == str(project_id)
        assert parts[1] == str(task_id)
        assert parts[2] == "page"
        assert parts[3] == "1920x1080_chromium.jpg"

    def test_root_url(self) -> None:
        path = build_file_path(
            uuid.uuid4(),
            uuid.uuid4(),
            "https://x.com/",
            390,
            844,
            "webkit",
            "png",
        )
        assert "root" in path
        assert path.endswith("390x844_webkit.png")

    def test_query_urls_get_their_own_directory(self) -> None:
        project_id, task_id = uuid.uuid4(), uuid.uuid4()
        page_1 = build_file_path(
            project_id, task_id, "https://x.com/list?page=1", 1280, 720, "chrome", "jpeg"
        )
        page_2 = build_file_path(
            project_id, task_id, "https://x.com/list?page=2", 1280, 720, "chrome", "jpeg"
        )

        assert page_1 != page_2
        assert page_1.split("/")[2].startswith("list-")
        assert page_1.endswith("/1280x720_chrome.jpg")
