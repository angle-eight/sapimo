from pathlib import Path

import yaml

from sapimo.docker.config_manager import DockerConfigManager


def test_docker_config_manager_uses_defaults_when_file_missing(tmp_path: Path):
    manager = DockerConfigManager(tmp_path / "missing.yml")

    assert manager.get_python_config() == {
        "default_version": "3.12",
        "auto_install_missing": True,
        "versions": ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"],
    }
    assert manager.get_aws_mocks_config() == {
        "persist_data": True,
        "auto_create_buckets": True,
        "auto_create_tables": True,
    }
    assert manager.get_development_config() == {
        "auto_reload": True,
        "log_level": "INFO",
        "show_lambda_logs": True,
    }
    assert manager.get_network_config() == {"host": "127.0.0.1", "port": 3000}


def test_docker_config_manager_merges_user_config_with_defaults(tmp_path: Path):
    config_path = tmp_path / "sapimo-docker.yml"
    config_path.write_text(
        "\n".join(
            [
                "host: 0.0.0.0",
                "port: 8080",
                "python:",
                "  default_version: '3.13'",
                "aws_mocks:",
                "  persist_data: false",
                "development:",
                "  log_level: DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    manager = DockerConfigManager(config_path)

    assert manager.get_python_config()["default_version"] == "3.13"
    assert manager.get_python_config()["auto_install_missing"] is True
    assert manager.get_aws_mocks_config()["persist_data"] is False
    assert manager.get_aws_mocks_config()["auto_create_buckets"] is True
    assert manager.get_development_config()["log_level"] == "DEBUG"
    assert manager.get_development_config()["auto_reload"] is True
    assert manager.get_network_config() == {"host": "0.0.0.0", "port": 8080}


def test_docker_config_manager_invalid_yaml_falls_back_to_empty_config(
    tmp_path: Path,
):
    config_path = tmp_path / "sapimo-docker.yml"
    config_path.write_text("python: [unterminated\n", encoding="utf-8")

    manager = DockerConfigManager(config_path)

    assert manager.get_all_config()["raw_config"] == {}
    assert manager.get_python_config()["default_version"] == "3.12"


def test_create_default_config_respects_existing_file_and_force(tmp_path: Path):
    config_path = tmp_path / "sapimo-docker.yml"
    config_path.write_text("python:\n  default_version: '3.13'\n", encoding="utf-8")
    manager = DockerConfigManager(config_path)

    assert manager.create_default_config() is False
    assert "3.13" in config_path.read_text(encoding="utf-8")

    assert manager.create_default_config(force=True) is True
    generated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert generated["python"]["default_version"] == "3.13"
    assert generated["python"]["auto_install_missing"] is True
    assert generated["aws_mocks"]["auto_create_tables"] is True
    assert generated["development"]["show_lambda_logs"] is True
