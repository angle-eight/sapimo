from pathlib import Path

import yaml

from sapimo import main as cli_main


def test_create_config_generates_single_container_compose(tmp_path: Path, monkeypatch):
    api_mock_dir = tmp_path / "api_mock"
    api_mock_dir.mkdir(parents=True, exist_ok=True)
    config_file = api_mock_dir / "config.yaml"

    monkeypatch.setattr(cli_main, "WORKING_DIR", api_mock_dir)
    monkeypatch.setattr(cli_main, "CONFIG_FILE", config_file)

    template_file = tmp_path / "template.yaml"
    template_file.write_text("Resources: {}\n", encoding="utf-8")

    class DummyParser:
        def __init__(self, template: Path):
            assert template == template_file

        def create_config_file(self, out_path: Path, overwrite: bool):
            assert overwrite is False
            out_path.write_text("paths: {}\n", encoding="utf-8")

    created = cli_main.create_config(
        template_file, parse_class=DummyParser, overwrite=False
    )

    assert created is True
    compose_file = api_mock_dir / "docker-compose.yml"
    assert compose_file.exists()

    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    assert set(compose["services"].keys()) == {"sapimo"}
    assert compose["services"]["sapimo"]["command"] == [
        "python",
        "/workspace/api_mock/docker/gateway/main.py",
    ]
    assert (
        compose["services"]["sapimo"]["environment"]["PYTHONPATH"]
        == "/workspace:/workspace/api_mock/docker"
    )
    assert (
        compose["services"]["sapimo"]["environment"]["SAPIMO_SINGLE_CONTAINER"] == "1"
    )


def test_start_uses_single_service_compose_command(tmp_path: Path, monkeypatch):
    api_mock_dir = tmp_path / "api_mock"
    api_mock_dir.mkdir(parents=True, exist_ok=True)
    (api_mock_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(cli_main, "WORKING_DIR", api_mock_dir)
    monkeypatch.setattr(cli_main, "_compose_project_name", lambda: "sapimo-test")

    calls: dict[str, object] = {}

    def fake_run(cmd, env=None, check=False):
        calls["cmd"] = cmd
        calls["env"] = env
        calls["check"] = check
        return 0

    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)

    before_cwd = Path.cwd()
    cli_main.start.callback(host="127.0.0.1", port=9010, build=False, detach=True)
    after_cwd = Path.cwd()

    assert calls["cmd"] == [
        "docker",
        "compose",
        "-p",
        "sapimo-test",
        "up",
        "--remove-orphans",
        "-d",
    ]
    assert calls["check"] is True
    assert calls["env"]["SAPIMO_HOST"] == "127.0.0.1"
    assert calls["env"]["SAPIMO_PORT"] == "9010"
    assert before_cwd == after_cwd
