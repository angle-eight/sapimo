"""
Mock Handler for Gateway
api_mock/app.py の動的読み込みとMock処理
"""

import sys
import importlib.util
import asyncio
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from fastapi import Request, HTTPException
import inspect
import traceback

from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class MockHandler:
    """Mock API 処理クラス"""

    def __init__(self):
        self.mock_routes: Dict[str, Dict[str, Any]] = {}
        self.app_py_path = Path("/workspace/api_mock/app.py")
        self.last_modified = None
        self._mock_module = None

    def _find_route_match(
        self, method: str, path: str
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """
        リクエストのメソッドとパスに一致するルートキーとパスパラメータを返す。
        完全一致を優先し、次にパターンマッチを試みる。
        Returns: (route_key or None, path_params dict)
        """
        exact_key = f"{method.upper()}:{path}"
        if exact_key in self.mock_routes:
            return exact_key, {}

        for route_key, route_info in self.mock_routes.items():
            parts = route_key.split(":", 1)
            if len(parts) != 2:
                continue
            route_method, route_path = parts
            if route_method != method.upper():
                continue

            param_names = re.findall(r"\{([^}]+)\}", route_path)
            if not param_names:
                continue

            regex_pattern = route_path
            for param_name in param_names:
                regex_pattern = regex_pattern.replace(
                    f"{{{param_name}}}", f"(?P<{param_name}>[^/]+)"
                )
            regex_pattern = f"^{regex_pattern}$"

            m = re.match(regex_pattern, path)
            if m:
                return route_key, m.groupdict()

        return None, {}

    def has_mock_definition(self, method: str, path: str) -> bool:
        """指定されたメソッド・パスにMock定義があるかチェック（パターンマッチ対応）"""
        route_key, _ = self._find_route_match(method, path)
        return route_key is not None

    def reload_mock_definitions(self) -> bool:
        """api_mock/app.py を動的リロード"""
        try:
            if not self.app_py_path.exists():
                logger.debug("app.py not found, clearing mock routes")
                self.mock_routes.clear()
                return False

            current_modified = self.app_py_path.stat().st_mtime
            if self.last_modified == current_modified and self.mock_routes:
                return True

            spec = importlib.util.spec_from_file_location("app_mock", self.app_py_path)
            if spec and spec.loader:
                self._mock_module = importlib.util.module_from_spec(spec)

                if "app_mock" in sys.modules:
                    del sys.modules["app_mock"]
                sys.modules["app_mock"] = self._mock_module

                spec.loader.exec_module(self._mock_module)

                from sapimo.mock.api import api as mock_router

                self.mock_routes = mock_router.route_info.copy()

                self.last_modified = current_modified
                logger.info(f"Loaded {len(self.mock_routes)} mock routes from app.py")

                for route_key, route_info in self.mock_routes.items():
                    logger.debug(f"  {route_key} -> {route_info['function'].__name__}")

                return True

        except Exception as e:
            logger.error(f"Failed to reload mock definitions: {e}")
            logger.debug(traceback.format_exc())
            return False

        return False

    async def handle_mock_request(
        self, method: str, path: str, request: Request
    ) -> Optional[Any]:
        """Mock リクエストを処理"""
        route_key, path_params = self._find_route_match(method, path)

        if route_key is None:
            return None

        route_info = self.mock_routes[route_key]
        mock_func = route_info["function"]
        signature = route_info["signature"]

        try:
            params = await self._prepare_parameters(
                signature, path, request, path_params=path_params
            )

            if asyncio.iscoroutinefunction(mock_func):
                result = await mock_func(**params)
            else:
                result = mock_func(**params)

            logger.debug(f"Mock function returned: {type(result)} {result}")
            return result

        except Exception as e:
            logger.error(f"Mock function execution failed: {e}")
            logger.debug(traceback.format_exc())
            raise HTTPException(
                status_code=500, detail=f"Mock execution error: {str(e)}"
            )

    async def _prepare_parameters(
        self,
        signature: inspect.Signature,
        path: str,
        request: Request,
        path_params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Mock関数のパラメータを準備"""
        params = {}

        if path_params is None:
            path_params = {}

        for param_name, param in signature.parameters.items():
            if param_name == "request":
                params[param_name] = request
            elif param_name in path_params:
                value = path_params[param_name]
                if param.annotation != inspect.Parameter.empty:
                    try:
                        if param.annotation is int:
                            value = int(value)
                        elif param.annotation is float:
                            value = float(value)
                        elif param.annotation is bool:
                            value = value.lower() in ("true", "1", "yes")
                    except (ValueError, TypeError) as e:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Invalid type for parameter {param_name}: {e}",
                        )
                params[param_name] = value

        return params

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
