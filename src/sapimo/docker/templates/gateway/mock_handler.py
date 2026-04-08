"""
Mock Handler for Gateway
api_mock/app.py の動的読み込みと Mock 処理

MockRouter (FastAPI APIRouter サブクラス) にルート定義を委譲し、
パラメータ解決は FastAPI の標準メカニズムを使用する。
"""

import sys
import importlib.util
import asyncio
import json
from pathlib import Path
from typing import Any, Optional
from fastapi import FastAPI, Request, HTTPException
from starlette.routing import Match
import traceback

from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class MockHandler:
    """Mock API 処理クラス"""

    def __init__(self):
        self._mock_app: FastAPI | None = None
        self.app_py_path = Path("/workspace/api_mock/app.py")
        self.last_modified = None
        self._mock_module = None

    def has_mock_definition(self, method: str, path: str) -> bool:
        """指定されたメソッド・パスに Mock 定義があるかチェック"""
        if not self._mock_app:
            return False

        scope = {"type": "http", "path": path, "method": method.upper()}
        for route in self._mock_app.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return True
        return False

    def reload_mock_definitions(self) -> bool:
        """api_mock/app.py を動的リロード"""
        try:
            if not self.app_py_path.exists():
                logger.debug("app.py not found, clearing mock app")
                self._mock_app = None
                return False

            current_modified = self.app_py_path.stat().st_mtime
            if self.last_modified == current_modified and self._mock_app is not None:
                return True

            spec = importlib.util.spec_from_file_location("app_mock", self.app_py_path)
            if spec and spec.loader:
                self._mock_module = importlib.util.module_from_spec(spec)

                if "app_mock" in sys.modules:
                    del sys.modules["app_mock"]
                sys.modules["app_mock"] = self._mock_module

                from sapimo.mock.api import api as mock_router
                from sapimo.mock.api import monkeypatch

                mock_router.clear_routes()
                monkeypatch.clear()

                spec.loader.exec_module(self._mock_module)

                # MockRouter のルートから内部 FastAPI アプリを構築
                self._mock_app = FastAPI()
                self._mock_app.include_router(mock_router)

                self.last_modified = current_modified
                route_count = len(mock_router.routes)
                logger.info(f"Loaded {route_count} mock routes from app.py")
                for route in mock_router.routes:
                    if hasattr(route, "methods") and hasattr(route, "path"):
                        logger.debug(f"  {route.methods} {route.path}")

                return True

        except Exception as e:
            logger.error(f"Failed to reload mock definitions: {e}")
            logger.debug(traceback.format_exc())
            return False

        return False

    async def handle_mock_request(
        self, method: str, path: str, request: Request
    ) -> Optional[Any]:
        """Mock リクエストを処理。FastAPI のパラメータ解決を経由して Mock 関数を呼び出す。"""
        if not self._mock_app:
            return None

        from sapimo.mock.api import api as mock_router

        mock_router._captured_result = type(mock_router)._UNSET_SENTINEL

        # 元リクエストのボディをキャッシュ（複数回読み取り対応）
        body = await request.body()

        # ASGI scope を構築
        scope = dict(request.scope)
        scope.pop("path_params", None)

        response_status = None
        response_body = b""

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            nonlocal response_status, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body":
                response_body += message.get("body", b"")

        try:
            await self._mock_app(scope, receive, send)
        except Exception as e:
            logger.error(f"Mock function execution failed: {e}")
            logger.debug(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Mock execution error: {str(e)}"
            )

        # FastAPI バリデーションエラー等をそのまま伝播
        if response_status is not None and response_status >= 400:
            try:
                error_body = json.loads(response_body)
                detail = error_body.get("detail", error_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = f"Mock error (status {response_status})"
            raise HTTPException(status_code=response_status, detail=detail)

        captured, result = mock_router.get_captured_result()
        if not captured:
            return None
        return result

    def start_file_watcher(self):
        """ファイル監視を開始"""
        try:
            asyncio.get_running_loop().create_task(self._watch_app_py())
        except RuntimeError:
            logger.debug("No running event loop; skip watcher startup")

    async def _watch_app_py(self):
        """app.py の変更を監視"""
        while True:
            try:
                self.reload_mock_definitions()
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"File watcher error: {e}")
                await asyncio.sleep(5)
