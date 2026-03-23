"""
チケットB2：パスパラメータ抽出/型検証テスト
TC-PATH-001: /a/123 + id:int → 成功
TC-PATH-002: /a/abc + id:int → 422
TC-PATH-003: value:float, flag:bool の変換
TC-PATH-004: パラメータ無しルートに副作用なし
"""

import importlib.util
import inspect
import sys
from pathlib import Path
import pytest

gateway_dir = Path(__file__).resolve().parents[2] / "docker" / "gateway"
if str(gateway_dir) not in sys.path:
    sys.path.insert(0, str(gateway_dir))

src_dir = Path(__file__).resolve().parents[2] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))


def _load_mock_handler():
    module_path = gateway_dir / "mock_handler.py"
    spec = importlib.util.spec_from_file_location("mock_handler_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def handler():
    mod = _load_mock_handler()
    h = mod.MockHandler()
    return h


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ------- _find_route_match のユニットテスト -------


def test_tc_path_find_exact_match(handler):
    """完全一致ルートが見つかる"""
    # ルートを登録
    handler.mock_routes["GET:/hello"] = {
        "function": lambda: None,
        "signature": inspect.signature(lambda: None),
    }
    route_key, params = handler._find_route_match("GET", "/hello")
    assert route_key == "GET:/hello"
    assert params == {}


def test_tc_path_find_pattern_match(handler):
    """/hello/{id} パターンで /hello/42 にマッチし id が抽出される"""
    handler.mock_routes["GET:/hello/{id}"] = {
        "function": lambda id: None,
        "signature": inspect.signature(lambda id: None),
    }
    route_key, params = handler._find_route_match("GET", "/hello/42")
    assert route_key == "GET:/hello/{id}"
    assert params == {"id": "42"}


def test_tc_path_no_match(handler):
    """マッチするルートがない場合は None"""
    route_key, params = handler._find_route_match("GET", "/nonexistent")
    assert route_key is None
    assert params == {}


# ------- _prepare_parameters のユニットテスト -------


@pytest.mark.anyio
async def test_tc_path_001_int_conversion_success(handler):
    """TC-PATH-001: id:int → int に変換成功"""
    sig = inspect.signature(lambda id: None)
    # アノテーションを手動で付与
    import inspect as ins

    params_dict = dict(sig.parameters)
    params_dict["id"] = params_dict["id"].replace(annotation=int)
    sig = sig.replace(parameters=list(params_dict.values()))

    from unittest.mock import MagicMock
    from fastapi import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/a/123",
        "query_string": b"",
        "headers": [],
    }
    request = Request(scope)

    result = await handler._prepare_parameters(
        sig, "/a/123", request, path_params={"id": "123"}
    )
    assert result["id"] == 123
    assert isinstance(result["id"], int)


@pytest.mark.anyio
async def test_tc_path_002_int_conversion_failure_raises_422(handler):
    """TC-PATH-002: id:int に abc → 422"""
    import inspect as ins
    from fastapi import HTTPException, Request

    sig = inspect.signature(lambda id: None)
    params_dict = dict(sig.parameters)
    params_dict["id"] = params_dict["id"].replace(annotation=int)
    sig = sig.replace(parameters=list(params_dict.values()))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/a/abc",
        "query_string": b"",
        "headers": [],
    }
    request = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        await handler._prepare_parameters(
            sig, "/a/abc", request, path_params={"id": "abc"}
        )

    assert exc_info.value.status_code == 422


@pytest.mark.anyio
async def test_tc_path_003_float_and_bool_conversion(handler):
    """TC-PATH-003: float/bool 変換"""
    import inspect as ins
    from fastapi import Request

    def fn(value: float, flag: bool):
        pass

    sig = inspect.signature(fn)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x/1.5/true",
        "query_string": b"",
        "headers": [],
    }
    request = Request(scope)

    result = await handler._prepare_parameters(
        sig, "/x/1.5/true", request, path_params={"value": "1.5", "flag": "true"}
    )
    assert result["value"] == pytest.approx(1.5)
    assert isinstance(result["flag"], bool)


@pytest.mark.anyio
async def test_tc_path_004_no_param_route_unaffected(handler):
    """TC-PATH-004: パラメータ無しルートに副作用なし"""
    from fastapi import Request

    def fn():
        pass

    sig = inspect.signature(fn)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello",
        "query_string": b"",
        "headers": [],
    }
    request = Request(scope)

    result = await handler._prepare_parameters(sig, "/hello", request, path_params={})
    assert result == {}
