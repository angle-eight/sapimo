"""
Mock API デコレータモジュール
FastAPI APIRouter のサブクラスで Mock 定義を可能にする
"""

from typing import Dict, Callable, Any
from contextlib import contextmanager
import inspect
import asyncio
from functools import wraps
from unittest.mock import patch as _mock_patch

from fastapi import APIRouter


class InputOverride:
    """入力値すり替え用のマーカークラス"""

    def __init__(self, data: Dict[str, Any] | None = None, **kwargs: Any):
        if data is None:
            data = {}
        elif not isinstance(data, dict):
            raise TypeError("data must be a dict")

        if kwargs:
            data = {**data, **kwargs}

        self.data = data


class MockRouter(APIRouter):
    """FastAPI APIRouter ベースの Mock ルーター

    ルート登録は FastAPI の標準メカニズムを使用し、
    パラメータ解決（パス・クエリ・ボディの型変換 + Pydantic バリデーション）
    を FastAPI に完全委譲する。
    Mock 関数の生の戻り値はキャプチャされ、Gateway が解釈する。
    """

    _UNSET_SENTINEL = object()

    def __init__(self):
        super().__init__()
        self._captured_result: Any = self._UNSET_SENTINEL

    def add_api_route(self, path: str, endpoint: Callable, **kwargs) -> None:
        """エンドポイントをラップして戻り値キャプチャを挟む"""
        wrapped = self._wrap_endpoint(endpoint)
        super().add_api_route(path, wrapped, **kwargs)

    def _wrap_endpoint(self, func: Callable) -> Callable:
        """Mock 関数をラップし、戻り値をキャプチャする"""
        router = self

        @wraps(func)
        async def wrapper(*args, **kwargs):
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            router._captured_result = result
            # FastAPI のレスポンスシリアライズに安全な値を返す
            if result is None or isinstance(result, (dict, list, str, float, bool)):
                return result
            if isinstance(result, int):
                return result
            return {}  # InputOverride 等のシリアライズ不可型

        wrapper.__signature__ = inspect.signature(func)
        return wrapper

    def get_captured_result(self) -> tuple[bool, Any]:
        """直近の Mock 呼び出し結果を取得しリセットする。

        Returns:
            (captured, result): captured=False なら Mock 関数は呼ばれなかった
        """
        if self._captured_result is self._UNSET_SENTINEL:
            return False, None
        result = self._captured_result
        self._captured_result = self._UNSET_SENTINEL
        return True, result

    def clear_routes(self):
        """ルート定義をクリア（リロード用）"""
        self.routes.clear()
        self._captured_result = self._UNSET_SENTINEL


# グローバルインスタンス
api = MockRouter()


def change_input(**kwargs) -> InputOverride:
    """Lambda実行時の入力値を変更"""
    return InputOverride(kwargs)


class MockOptions:
    """Mock動作オプション"""

    def __init__(self):
        self.mode = "api"  # "api", "mock"
        self.default_status = 200

    def set_api_mode(self):
        """通常のLambda実行モード"""
        self.mode = "api"

    def set_mock_mode(self, status: int = 200):
        """Mock優先モード"""
        self.mode = "mock"
        self.default_status = status

    def set(self, mode_val, status: int = 200):
        """旧API互換: options.set(mode.api) / options.set(mode.mock, status=400)"""
        if isinstance(mode_val, str):
            if mode_val in ("api", "ApiMode"):
                self.set_api_mode()
            elif mode_val in ("mock", "MockMode"):
                self.set_mock_mode(status)
        else:
            # Enum互換: mode_val.name で判定
            val = getattr(mode_val, "name", str(mode_val)).lower()
            if val in ("api", "apimode"):
                self.set_api_mode()
            elif val in ("mock", "mockmode"):
                self.set_mock_mode(status)


# グローバルオプション
options = MockOptions()


class Monkeypatch:
    """pytest-like monkeypatch for Lambda execution.

    Lambda 実行時に指定したモジュール属性を差し替える。
    unittest.mock.patch を内部で使用し、実行後に自動復元する。

    Usage in app.py::

        from sapimo.mock import monkeypatch

        # デコレータ形式
        @monkeypatch.setattr("my_module.call_bedrock")
        def mock_call_bedrock(prompt, model_id):
            return {"content": [{"text": "Mock AI response"}]}

        # 命令形式 (pytest 風)
        monkeypatch.setattr("my_module.get_timestamp", lambda: "2024-01-01")
    """

    def __init__(self):
        self._patches: list[tuple[str, Any]] = []

    def setattr(self, target: str, replacement: Any = None):
        """Register a monkeypatch.

        Args:
            target: Dotted path to the attribute (e.g. ``"my_module.func"``).
            replacement: Replacement value/function.
                If omitted, returns a decorator.

        Returns:
            When used as decorator (replacement is None), returns a decorator.
            Otherwise returns replacement as-is.
        """
        if replacement is not None:
            self._patches.append((target, replacement))
            return replacement

        def decorator(func):
            self._patches.append((target, func))
            return func

        return decorator

    def clear(self):
        """Clear all registered patches (called on app.py reload)."""
        self._patches.clear()

    @contextmanager
    def apply(self):
        """Context manager that activates all registered patches.

        Patches are applied in registration order and reverted on exit.
        """
        active = []
        try:
            for target, replacement in self._patches:
                p = _mock_patch(target, replacement)
                p.start()
                active.append(p)
            yield
        finally:
            for p in reversed(active):
                p.stop()


# グローバルインスタンス
monkeypatch = Monkeypatch()
