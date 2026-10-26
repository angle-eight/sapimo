from pathlib import Path

import pytest
from click.testing import CliRunner

from sapimo import main as cli_main


@pytest.fixture
def api_mock_dir(tmp_path: Path, monkeypatch) -> Path:
    api_mock = tmp_path / "api_mock"
    api_mock.mkdir()
    monkeypatch.setattr(cli_main, "WORKING_DIR", api_mock)
    monkeypatch.setattr(cli_main, "CONFIG_FILE", api_mock / "config.yaml")
    monkeypatch.chdir(tmp_path)
    return api_mock


def test_init_with_missing_template_fails_without_creating_config(api_mock_dir: Path):
    result = CliRunner().invoke(cli_main.main, ["init", "--template", "missing.yaml"])

    assert result.exit_code != 0
    assert "missing.yaml" in result.output
    assert "not found" in result.output
    assert not (api_mock_dir / "config.yaml").exists()


def test_init_with_missing_terraform_plan_fails_without_creating_config(api_mock_dir: Path):
    result = CliRunner().invoke(cli_main.main, ["init", "--terraform", "missing.json"])

    assert result.exit_code != 0
    assert "missing.json" in result.output
    assert not (api_mock_dir / "config.yaml").exists()


def test_init_with_unparsable_template_fails_without_creating_config(
    api_mock_dir: Path, tmp_path: Path
):
    template = tmp_path / "template.yaml"
    template.write_text("Resources:\n  Broken:\n    Properties: {}\n", encoding="utf-8")

    result = CliRunner().invoke(cli_main.main, ["init", "--template", str(template)])

    assert result.exit_code != 0
    assert "Failed to parse" in result.output
    assert "not found" not in result.output
    assert not (api_mock_dir / "config.yaml").exists()
