"""The task ZIP mirrors the on-disk layout of the screenshot files."""

import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.screenshots import storage
from app.screenshots.models import Screenshot
from app.screenshots.storage import build_file_path
from app.tasks.router import _create_archive_sync


def _shot(task_id: uuid.UUID, url: str, file_path: str, **kwargs) -> Screenshot:
    values: dict = {
        "task_id": task_id,
        "url": url,
        "browser": "chrome",
        "width": 1280,
        "height": 720,
        "file_format": "jpeg",
        "file_path": file_path,
        "signed_url": "signed",
        "status": "ok",
    }
    values.update(kwargs)
    return Screenshot(**values)


def _archive(tmp_path: Path, task_id: uuid.UUID, shots: list[Screenshot]) -> list[str]:
    archives = tmp_path / "archives"
    archives.mkdir(exist_ok=True)
    with patch.object(storage.settings, "DATA_DIR", str(tmp_path)):
        for shot in shots:
            path = storage.abs_file_path(shot.file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"img")
        created = _create_archive_sync(str(task_id), shots, archives)
    with zipfile.ZipFile(created) as zf:
        return sorted(zf.namelist())


def test_folders_come_from_the_stored_file_path(tmp_path: Path) -> None:
    project_id, task_id = uuid.uuid4(), uuid.uuid4()
    urls = [
        "https://example.com/",
        "https://example.com/list?page=1",
        "https://example.com/list?page=2",
    ]
    shots = [
        _shot(
            task_id,
            url,
            build_file_path(project_id, task_id, url, 1280, 720, "chrome", "jpeg"),
        )
        for url in urls
    ]

    names = _archive(tmp_path, task_id, shots)

    # Same names as on disk: "root" (not "homepage"), and the two query URLs
    # land in their own folders instead of overwriting each other.
    expected = sorted(f"{task_id}/" + "/".join(s.file_path.split("/")[2:]) for s in shots)
    assert names == expected
    assert f"{task_id}/root/1280x720_chrome.jpg" in names
    assert len({name.split("/")[1] for name in names}) == 3


def test_rows_sharing_a_file_are_archived_once(tmp_path: Path) -> None:
    task_id = uuid.uuid4()
    path = f"p/{task_id}/about/1024x768_chrome.jpg"
    shots = [
        _shot(task_id, "https://example.com/about", path, device_type="desktop"),
        _shot(task_id, "https://example.com/about", path, device_type="tablet"),
    ]

    assert _archive(tmp_path, task_id, shots) == [f"{task_id}/about/1024x768_chrome.jpg"]


def test_unexpected_file_paths_fall_back_to_the_url(tmp_path: Path) -> None:
    task_id = uuid.uuid4()
    shots = [
        _shot(task_id, "https://example.com/a/b", "legacy/file.png", file_format="png"),
        _shot(task_id, "https://example.com/", f"p/{task_id}/../evil.jpg", width=390),
    ]

    names = _archive(tmp_path, task_id, shots)

    assert names == sorted(
        [
            f"{task_id}/a_b/1280x720_chrome.png",
            f"{task_id}/homepage/390x720_chrome.jpg",
        ]
    )
