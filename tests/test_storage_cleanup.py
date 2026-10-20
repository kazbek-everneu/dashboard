import uuid
from pathlib import Path
from unittest.mock import patch

from app.screenshots import storage


def _make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


async def test_delete_task_files_removes_task_dir_and_archive(tmp_path: Path) -> None:
    project_id, task_id, other_task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with patch.object(storage.settings, "DATA_DIR", str(tmp_path)):
        shot = _make_file(storage.task_dir(project_id, task_id) / "root" / "1x1_chrome.jpg")
        kept = _make_file(storage.task_dir(project_id, other_task) / "root" / "a.jpg")
        archive = _make_file(storage.archive_path(task_id))

        await storage.delete_task_files(project_id, task_id)

    assert not shot.exists()
    assert not storage.task_dir(project_id, task_id).exists()
    assert not archive.exists()
    assert kept.exists()


async def test_delete_project_files_removes_project_dir_and_archives(
    tmp_path: Path,
) -> None:
    project_id, task_id = uuid.uuid4(), uuid.uuid4()
    with patch.object(storage.settings, "DATA_DIR", str(tmp_path)):
        _make_file(storage.task_dir(project_id, task_id) / "root" / "a.jpg")
        archive = _make_file(storage.archive_path(task_id))
        other_archive = _make_file(storage.archive_path(uuid.uuid4()))

        await storage.delete_project_files(project_id, [task_id])

        assert not storage.project_dir(project_id).exists()
    assert not archive.exists()
    assert other_archive.exists()


async def test_missing_files_are_ignored(tmp_path: Path) -> None:
    with patch.object(storage.settings, "DATA_DIR", str(tmp_path)):
        await storage.delete_task_files(uuid.uuid4(), uuid.uuid4())


def test_remove_paths_refuses_anything_outside_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = _make_file(tmp_path / "outside" / "keep.txt")
    with patch.object(storage.settings, "DATA_DIR", str(data_dir)):
        storage.remove_paths([outside, data_dir / ".." / "outside", data_dir])

    assert outside.exists()
    assert data_dir.exists()
