from pathlib import Path

import yaml

from sapimo.docker.single_compose_generator import SingleContainerComposeGenerator


def test_generate_single_compose_file(tmp_path: Path):
    api_mock_dir = tmp_path / "api_mock"
    api_mock_dir.mkdir(parents=True, exist_ok=True)
    config_file = api_mock_dir / "config.yaml"
    config_file.write_text("paths: {}\n", encoding="utf-8")

    generator = SingleContainerComposeGenerator(config_file)
    output_path = generator.generate_compose_file()

    assert output_path.exists()

    compose = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert set(compose["services"].keys()) == {"sapimo"}

    service = compose["services"]["sapimo"]
    assert service["build"]["dockerfile"] == "api_mock/docker/single/Dockerfile"
    assert service["command"] == [
        "python",
        "/workspace/api_mock/docker/gateway/main.py",
    ]
    assert service["environment"]["SAPIMO_SINGLE_CONTAINER"] == "1"
    assert (
        service["environment"]["PYTHONPATH"] == "/workspace:/workspace/api_mock/docker"
    )
    assert "..:/workspace:rw" in service["volumes"]

    docker_root = api_mock_dir / "docker"
    assert (docker_root / "single" / "Dockerfile").exists()
    assert (docker_root / "single" / "requirements.txt").exists()
    assert (docker_root / "gateway" / "main.py").exists()

    dockerfile_content = (docker_root / "single" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        'CMD ["python", "/workspace/api_mock/docker/gateway/main.py"]'
        in dockerfile_content
    )
    assert "PYTHONPATH=/workspace:/workspace/api_mock/docker" in dockerfile_content


class TestResolvePythonVersion:
    """_resolve_python_version のテスト"""

    def _make_generator(self, tmp_path: Path, config_text: str):
        api_mock = tmp_path / "api_mock"
        api_mock.mkdir(parents=True, exist_ok=True)
        cfg = api_mock / "config.yaml"
        cfg.write_text(config_text, encoding="utf-8")
        return SingleContainerComposeGenerator(cfg)

    def test_picks_newest_supported_version(self, tmp_path: Path):
        """複数 Runtime がある場合、サポート対象で最も新しいバージョンを選ぶ"""
        config = (
            "paths:\n"
            "  /a:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: python3.9\n"
            "  /b:\n"
            "    post:\n"
            "      Properties:\n"
            "        Runtime: python3.13\n"
            "  /c:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: python3.12\n"
        )
        gen = self._make_generator(tmp_path, config)
        assert gen._resolve_python_version() == "3.13"

    def test_falls_back_to_default_when_no_supported(self, tmp_path: Path):
        """サポート外の Runtime のみの場合はデフォルトにフォールバック"""
        config = (
            "paths:\n"
            "  /a:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: python3.9\n"
            "  /b:\n"
            "    post:\n"
            "      Properties:\n"
            "        Runtime: python3.11\n"
        )
        gen = self._make_generator(tmp_path, config)
        assert gen._resolve_python_version() == "3.12"

    def test_defaults_when_no_runtime(self, tmp_path: Path):
        """Runtime 未指定の場合はデフォルト"""
        config = (
            "paths:\n  /a:\n    get:\n      Properties:\n        Handler: app.handler\n"
        )
        gen = self._make_generator(tmp_path, config)
        assert gen._resolve_python_version() == "3.12"

    def test_defaults_when_empty_config(self, tmp_path: Path):
        """空の config の場合はデフォルト"""
        gen = self._make_generator(tmp_path, "paths: {}\n")
        assert gen._resolve_python_version() == "3.12"

    def test_defaults_when_config_missing(self, tmp_path: Path):
        """config が存在しない場合はデフォルト"""
        gen = SingleContainerComposeGenerator(tmp_path / "nonexistent.yaml")
        assert gen._resolve_python_version() == "3.12"

    def test_triggered_runtime_included(self, tmp_path: Path):
        """triggered セクションの Runtime も考慮する"""
        config = (
            "paths:\n"
            "  /a:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: python3.12\n"
            "triggered:\n"
            "  my-bucket:\n"
            "    Properties:\n"
            "      Runtime: python3.13\n"
        )
        gen = self._make_generator(tmp_path, config)
        assert gen._resolve_python_version() == "3.13"

    def test_non_python_runtime_ignored(self, tmp_path: Path):
        """Python 以外の Runtime は無視"""
        config = (
            "paths:\n"
            "  /a:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: nodejs18.x\n"
            "  /b:\n"
            "    get:\n"
            "      Properties:\n"
            "        Runtime: python3.12\n"
        )
        gen = self._make_generator(tmp_path, config)
        assert gen._resolve_python_version() == "3.12"

    def test_compose_config_includes_build_arg(self, tmp_path: Path):
        """生成される compose config に PYTHON_VERSION build arg が含まれる"""
        config = (
            "paths:\n  /a:\n    get:\n      Properties:\n        Runtime: python3.13\n"
        )
        gen = self._make_generator(tmp_path, config)
        compose = gen.generate_compose_config()
        args = compose["services"]["sapimo"]["build"]["args"]
        assert args["PYTHON_VERSION"] == "3.13"


class TestParsePythonRuntime:
    """_parse_python_runtime のテスト"""

    def test_valid_runtime(self):
        assert SingleContainerComposeGenerator._parse_python_runtime("python3.12") == (
            3,
            12,
        )

    def test_uppercase(self):
        assert SingleContainerComposeGenerator._parse_python_runtime("Python3.9") == (
            3,
            9,
        )

    def test_non_python(self):
        assert (
            SingleContainerComposeGenerator._parse_python_runtime("nodejs18.x") is None
        )

    def test_empty(self):
        assert SingleContainerComposeGenerator._parse_python_runtime("") is None

    def test_malformed(self):
        assert SingleContainerComposeGenerator._parse_python_runtime("python3") is None
