"""
Mock API デコレータモジュール
FastAPI風のデコレータでMock定義を可能にする
"""

from typing import Dict, Callable, Any
import inspect
from functools import wraps


class InputOverride:
    """入力値すり替え用のマーカークラス"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data


class MockRouter:
    """Mock API ルーター"""

    def __init__(self):
        self.routes: Dict[str, Callable] = {}
        self.route_info: Dict[str, Dict] = {}

    def get(self, path: str):
        """GETエンドポイント定義デコレータ"""
        return self._create_decorator("GET", path)

    def post(self, path: str):
        """POSTエンドポイント定義デコレータ"""
        return self._create_decorator("POST", path)

    def put(self, path: str):
        """PUTエンドポイント定義デコレータ"""
        return self._create_decorator("PUT", path)

    def delete(self, path: str):
        """DELETEエンドポイント定義デコレータ"""
        return self._create_decorator("DELETE", path)

    def patch(self, path: str):
        """PATCHエンドポイント定義デコレータ"""
        return self._create_decorator("PATCH", path)

    def _create_decorator(self, method: str, path: str):
        """デコレータ生成"""

        def decorator(func: Callable):
            route_key = f"{method}:{path}"

            # 関数のシグネチャを保存（pydanticバリデーション用）
            sig = inspect.signature(func)
            self.route_info[route_key] = {
                "function": func,
                "signature": sig,
                "method": method,
                "path": path,
            }

            @wraps(func)
            async def wrapper(*args, **kwargs):
                return await func(*args, **kwargs)

            self.routes[route_key] = wrapper
            return wrapper

        return decorator

    def clear_routes(self):
        """ルート定義をクリア（リロード用）"""
        self.routes.clear()
        self.route_info.clear()


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


# グローバルオプション
options = MockOptions()
