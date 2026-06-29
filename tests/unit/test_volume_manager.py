from pathlib import Path

from sapimo.docker.volume_manager import VolumeManager


def make_manager(tmp_path: Path) -> VolumeManager:
    manager = VolumeManager()
    manager.workspace_path = tmp_path / "workspace"
    manager.api_mock_path = tmp_path / "api_mock"
    manager.data_path = tmp_path / "data"
    return manager


def test_setup_volumes_creates_required_directories_and_default_app(tmp_path: Path):
    manager = make_manager(tmp_path)

    assert manager.setup_volumes() is True

    app_py = manager.api_mock_path / "app.py"
    assert manager.api_mock_path.is_dir()
    assert manager.data_path.is_dir()
    assert app_py.exists()
    assert "from sapimo.mock import api" in app_py.read_text(encoding="utf-8")


def test_setup_volumes_preserves_existing_app_py(tmp_path: Path):
    manager = make_manager(tmp_path)
    manager.api_mock_path.mkdir(parents=True)
    app_py = manager.api_mock_path / "app.py"
    app_py.write_text("# custom app\n", encoding="utf-8")

    assert manager.setup_volumes() is True

    assert app_py.read_text(encoding="utf-8") == "# custom app\n"


def test_sync_api_mock_files_reports_relative_paths_sizes_and_count(tmp_path: Path):
    manager = make_manager(tmp_path)
    (manager.api_mock_path / "nested").mkdir(parents=True)
    (manager.api_mock_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (manager.api_mock_path / "nested" / "handler.py").write_text(
        "def handler(): pass\n",
        encoding="utf-8",
    )

    status = manager.sync_api_mock_files()

    assert status["api_mock_path"] == str(manager.api_mock_path)
    assert status["total_files"] == 2
    files_by_path = {item["path"]: item for item in status["files"]}
    assert files_by_path["app.py"]["size"] == len("print('hi')\n")
    assert files_by_path["nested/handler.py"]["size"] == len("def handler(): pass\n")


def test_get_data_usage_and_cleanup_data(tmp_path: Path):
    manager = make_manager(tmp_path)
    (manager.data_path / "s3").mkdir(parents=True)
    (manager.data_path / "dynamodb").mkdir(parents=True)
    (manager.data_path / "s3" / "object.txt").write_text("abc", encoding="utf-8")
    (manager.data_path / "dynamodb" / "table.json").write_text(
        "{}",
        encoding="utf-8",
    )

    usage = manager.get_data_usage()

    assert usage["total_size"] == 5
    assert usage["directories"]["s3"]["size"] == 3
    assert usage["directories"]["dynamodb"]["size"] == 2

    assert manager.cleanup_data(["s3"]) is True
    assert not (manager.data_path / "s3").exists()
    assert (manager.data_path / "dynamodb").exists()

    assert manager.cleanup_data() is True
    assert manager.data_path.exists()
    assert list(manager.data_path.iterdir()) == []


def test_get_volume_status_reflects_rebased_paths(tmp_path: Path):
    manager = make_manager(tmp_path)
    manager.workspace_path.mkdir()
    manager.api_mock_path.mkdir()

    status = manager.get_volume_status()

    assert status["workspace"]["exists"] is True
    assert status["workspace"]["readable"] is True
    assert status["api_mock"]["exists"] is True
    assert status["api_mock"]["writable"] is True
    assert status["data"]["exists"] is False
    assert status["data"]["writable"] is False
