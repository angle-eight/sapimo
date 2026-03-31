"""
Mock API デコレータモジュール
FastAPI APIRouter のサブクラスで Mock 定義を可能にする
"""

from typing import Dict, Callable, Any
import inspect
import asyncio
from functools import wraps

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
