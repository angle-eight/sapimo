"""
FastAPI アプリモード（app_module 設定）に関するユニットテスト。
- ConfigParser の app_module 対応
- SingleContainerComposeGenerator の _detect_app_module / SAPIMO_APP_MODULE 環境変数
- CLI `sapimo init --app` コマンド
- gateway/main.py の _forward_to_user_app インプロセス転送
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

GATEWAY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "sapimo"
    / "docker"
    / "templates"
    / "gateway"
)


def _load_gateway_module():
    if str(GATEWAY_DIR) not in sys.path:
        sys.path.insert(0, str(GATEWAY_DIR))
    spec = importlib.util.spec_from_file_location(
        "gateway_main_for_test", GATEWAY_DIR / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_gateway(monkeypatch, config_yaml: str, tmp_path: Path):
    """LambdaGateway インスタンスを最小セットアップで生成するヘルパー。"""
    module = _load_gateway_module()

    config_dir = tmp_path / "api_mock"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(config_yaml, encoding="utf-8")

    monkeypatch.setenv("SAPIMO_SINGLE_CONTAINER", "0")
    monkeypatch.setattr(
        module.MockHandler, "reload_mock_definitions", lambda self: None
    )
    monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)

    gw = object.__new__(module.LambdaGateway)
    gw.single_container_mode = False
    gw.project_root = tmp_path
    gw.config_path = config_file
    gw.mock_manager = None
    gw.local_lambda_runner = None
    gw.user_app = None
    gw.lambda_routes = {}
    gw.lambda_containers = {}
    gw.authorizer_lambdas = {}
    gw.triggered_lambdas = {}
    gw.mock_handler = module.MockHandler()

    from fastapi import FastAPI

    gw.app = FastAPI()
    gw._setup_middleware()
    gw._load_configuration()
    return module, gw


# ---------------------------------------------------------------------------
# ConfigParser — app_module のみ / paths + app_module の両方を受け入れる
# ---------------------------------------------------------------------------


class TestConfigParserAppModule:
    def test_app_module_only_config_is_accepted(self, tmp_path: Path):
        """paths なし・app_module のみの config.yaml を受け入れる。"""
        from sapimo.parser.config_parser import ConfigParser

        config_file = tmp_path / "config.yaml"
        config_file.write_text("app_module: myapp.main:app\n", encoding="utf-8")
        parser = ConfigParser(config_file)
        assert parser.apis == {}
        assert parser.all_resource["app_module"] == "myapp.main:app"

    def test_paths_and_app_module_config_is_accepted(self, tmp_path: Path):
        """paths + app_module の両方を持つ config.yaml を受け入れる。"""
        from sapimo.parser.config_parser import ConfigParser

        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "app_module: myapp.main:app\npaths:\n  /items:\n    get:\n      Properties:\n        Handler: app.handler\n        CodeUri: ./\n",
            encoding="utf-8",
        )
        parser = ConfigParser(config_file)
        assert "/items" in parser.apis
        assert parser.all_resource["app_module"] == "myapp.main:app"

    def test_neither_paths_nor_app_module_raises(self, tmp_path: Path):
        """paths も app_module も存在しない config.yaml はエラーになる。"""
        from sapimo.parser.config_parser import ConfigParser

        config_file = tmp_path / "config.yaml"
        config_file.write_text("s3:\n  bucket: {}\n", encoding="utf-8")
        with pytest.raises(Exception):
            ConfigParser(config_file)


# ---------------------------------------------------------------------------
# SingleContainerComposeGenerator — _detect_app_module / SAPIMO_APP_MODULE
# ---------------------------------------------------------------------------


class TestDetectAppModule:
    def _make_generator(self, tmp_path: Path, config_text: str):
        from sapimo.docker.single_compose_generator import (
            SingleContainerComposeGenerator,
        )

        api_mock = tmp_path / "api_mock"
        api_mock.mkdir(parents=True, exist_ok=True)
        cfg = api_mock / "config.yaml"
        cfg.write_text(config_text, encoding="utf-8")
        return SingleContainerComposeGenerator(cfg)

    def test_detect_app_module_returns_value(self, tmp_path: Path):
        gen = self._make_generator(tmp_path, "app_module: myapp.main:app\n")
        assert gen._detect_app_module() == "myapp.main:app"

    def test_detect_app_module_returns_none_when_absent(self, tmp_path: Path):
        gen = self._make_generator(tmp_path, "paths: {}\n")
        assert gen._detect_app_module() is None

    def test_compose_env_contains_sapimo_app_module(self, tmp_path: Path):
        gen = self._make_generator(tmp_path, "app_module: myapp.main:app\npaths: {}\n")
        config = gen.generate_compose_config()
        env = config["services"]["sapimo"]["environment"]
        assert env["SAPIMO_APP_MODULE"] == "myapp.main:app"

    def test_compose_env_excludes_sapimo_app_module_when_absent(self, tmp_path: Path):
        gen = self._make_generator(tmp_path, "paths: {}\n")
        config = gen.generate_compose_config()
        env = config["services"]["sapimo"]["environment"]
        assert "SAPIMO_APP_MODULE" not in env


# ---------------------------------------------------------------------------
# CLI — sapimo init --app
# ---------------------------------------------------------------------------


class TestInitAppCommand:
    def test_init_app_only_generates_config_and_compose(
        self, tmp_path: Path, monkeypatch
    ):
        """--app のみ指定時に config.yaml と docker-compose.yml が生成される。"""
        from sapimo import main as cli_main

        api_mock_dir = tmp_path / "api_mock"
        api_mock_dir.mkdir(parents=True, exist_ok=True)
        config_file = api_mock_dir / "config.yaml"

        monkeypatch.setattr(cli_main, "WORKING_DIR", api_mock_dir)
        monkeypatch.setattr(cli_main, "CONFIG_FILE", config_file)

        cli_main.create_config_for_fastapi_app("myapp.main:app")

        assert config_file.exists()
        config = yaml.safe_load(config_file.read_text())
        assert config["app_module"] == "myapp.main:app"

        compose_file = api_mock_dir / "docker-compose.yml"
        assert compose_file.exists()
        compose = yaml.safe_load(compose_file.read_text())
        env = compose["services"]["sapimo"]["environment"]
        assert env["SAPIMO_APP_MODULE"] == "myapp.main:app"

    def test_init_app_existing_config_does_not_overwrite(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        """既存の config.yaml がある場合は上書きせずにメッセージを表示する。"""
        from sapimo import main as cli_main

        api_mock_dir = tmp_path / "api_mock"
        api_mock_dir.mkdir(parents=True, exist_ok=True)
        config_file = api_mock_dir / "config.yaml"
        config_file.write_text("paths: {}\n", encoding="utf-8")

        monkeypatch.setattr(cli_main, "WORKING_DIR", api_mock_dir)
        monkeypatch.setattr(cli_main, "CONFIG_FILE", config_file)

        cli_main.create_config_for_fastapi_app("myapp.main:app")

        # 既存の内容が保持されている
        assert yaml.safe_load(config_file.read_text()) == {"paths": {}}
        captured = capsys.readouterr()
        assert "already exists" in captured.out

    def test_append_app_module_to_config(self, tmp_path: Path):
        """_append_app_module_to_config が app_module を既存 config.yaml に追記する。"""
        from sapimo import main as cli_main

        config_file = tmp_path / "config.yaml"
        config_file.write_text("paths: {}\n", encoding="utf-8")

        cli_main._append_app_module_to_config(config_file, "myapp.main:app")

        config = yaml.safe_load(config_file.read_text())
        assert config["app_module"] == "myapp.main:app"
        assert "paths" in config


# ---------------------------------------------------------------------------
# gateway/main.py — _forward_to_user_app インプロセス転送
# ---------------------------------------------------------------------------


class TestForwardToUserApp:
    def test_forward_request_to_user_app(self, monkeypatch, tmp_path: Path):
        """_forward_to_user_app が httpx.ASGITransport 経由でリクエストを転送する。"""
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from starlette.testclient import TestClient

        module = _load_gateway_module()

        user_app = FastAPI()

        @user_app.get("/hello")
        async def hello():
            return {"message": "from user app"}

        config_dir = tmp_path / "api_mock"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("paths: {}\n", encoding="utf-8")

        monkeypatch.setenv("SAPIMO_SINGLE_CONTAINER", "0")
        monkeypatch.setattr(
            module.MockHandler, "reload_mock_definitions", lambda self: None
        )
        monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)

        gw = object.__new__(module.LambdaGateway)
        gw.single_container_mode = False
        gw.project_root = tmp_path
        gw.config_path = config_file
        gw.mock_manager = None
        gw.local_lambda_runner = None
        gw.user_app = user_app
        gw.lambda_routes = {}
        gw.lambda_containers = {}
        gw.authorizer_lambdas = {}
        gw.triggered_lambdas = {}
        gw.mock_handler = module.MockHandler()

        from fastapi import FastAPI as _FastAPI

        gw.app = _FastAPI()
        gw._setup_middleware()
        gw._setup_routes()

        client = TestClient(gw.app, raise_server_exceptions=True)
        response = client.get("/hello")
        assert response.status_code == 200
        assert response.json() == {"message": "from user app"}

    def test_cors_headers_from_user_app_are_stripped(self, monkeypatch, tmp_path: Path):
        """_forward_to_user_app がユーザーアプリの CORS ヘッダーを除去する。"""
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from starlette.testclient import TestClient

        module = _load_gateway_module()

        user_app = FastAPI()
        user_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        @user_app.get("/data")
        async def data():
            return {"key": "value"}

        config_dir = tmp_path / "api_mock"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("paths: {}\n", encoding="utf-8")

        monkeypatch.setenv("SAPIMO_SINGLE_CONTAINER", "0")
        monkeypatch.setattr(
            module.MockHandler, "reload_mock_definitions", lambda self: None
        )
        monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)

        gw = object.__new__(module.LambdaGateway)
        gw.single_container_mode = False
        gw.project_root = tmp_path
        gw.config_path = config_file
        gw.mock_manager = None
        gw.local_lambda_runner = None
        gw.user_app = user_app
        gw.lambda_routes = {}
        gw.lambda_containers = {}
        gw.authorizer_lambdas = {}
        gw.triggered_lambdas = {}
        gw.mock_handler = module.MockHandler()

        from fastapi import FastAPI as _FastAPI

        gw.app = _FastAPI()
        gw._setup_middleware()
        gw._setup_routes()

        client = TestClient(gw.app)
        response = client.get("/data", headers={"origin": "http://localhost:3000"})
        assert response.status_code == 200
        # Gateway 側の CORS ミドルウェアが付与するヘッダーが存在する
        assert "access-control-allow-origin" in response.headers
        # ユーザーアプリの CORS ヘッダーが二重になっていないことは
        # _forward_to_user_app でフィルタした結果として確認済み（単体で返す値が1つ）
        values = response.headers.get_list("access-control-allow-origin")
        assert len(values) == 1

    def test_fallback_to_user_app_when_no_lambda_route(
        self, monkeypatch, tmp_path: Path
    ):
        """Lambda ルートにない URL が user_app へ fallback される。"""
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        module = _load_gateway_module()

        user_app = FastAPI()

        @user_app.get("/extra")
        async def extra():
            return {"extra": True}

        config_dir = tmp_path / "api_mock"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        # /items は Lambda ルートだが /extra は未定義
        config_file.write_text("paths: {}\n", encoding="utf-8")

        monkeypatch.setenv("SAPIMO_SINGLE_CONTAINER", "0")
        monkeypatch.setattr(
            module.MockHandler, "reload_mock_definitions", lambda self: None
        )
        monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)

        gw = object.__new__(module.LambdaGateway)
        gw.single_container_mode = False
        gw.project_root = tmp_path
        gw.config_path = config_file
        gw.mock_manager = None
        gw.local_lambda_runner = None
        gw.user_app = user_app
        gw.lambda_routes = {}
        gw.lambda_containers = {}
        gw.authorizer_lambdas = {}
        gw.triggered_lambdas = {}
        gw.mock_handler = module.MockHandler()

        from fastapi import FastAPI as _FastAPI

        gw.app = _FastAPI()
        gw._setup_middleware()
        gw._setup_routes()

        client = TestClient(gw.app, raise_server_exceptions=True)
        response = client.get("/extra")
        assert response.status_code == 200
        assert response.json() == {"extra": True}

    def test_no_user_app_returns_404(self, monkeypatch, tmp_path: Path):
        """user_app が未設定の場合、Lambda ルートにない URL は 404 になる。"""
        from starlette.testclient import TestClient

        module = _load_gateway_module()

        config_dir = tmp_path / "api_mock"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        config_file.write_text("paths: {}\n", encoding="utf-8")

        monkeypatch.setenv("SAPIMO_SINGLE_CONTAINER", "0")
        monkeypatch.setattr(
            module.MockHandler, "reload_mock_definitions", lambda self: None
        )
        monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)

        gw = object.__new__(module.LambdaGateway)
        gw.single_container_mode = False
        gw.project_root = tmp_path
        gw.config_path = config_file
        gw.mock_manager = None
        gw.local_lambda_runner = None
        gw.user_app = None
        gw.lambda_routes = {}
        gw.lambda_containers = {}
        gw.authorizer_lambdas = {}
        gw.triggered_lambdas = {}
        gw.mock_handler = module.MockHandler()

        from fastapi import FastAPI as _FastAPI

        gw.app = _FastAPI()
        gw._setup_middleware()
        gw._setup_routes()

        client = TestClient(gw.app, raise_server_exceptions=False)
        response = client.get("/unknown")
        assert response.status_code == 404
