"""
Gateway Event Format テスト
- v1 (APIGW) / v2 (APIGW_V2) event 形式の生成
- requestContext の動的フィールド
- Headers の Capitalize 正規化
"""

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_gateway_module():
    gateway_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "sapimo"
        / "docker"
        / "templates"
        / "gateway"
    )
    if str(gateway_dir) not in sys.path:
        sys.path.insert(0, str(gateway_dir))
    module_path = gateway_dir / "main.py"
    spec = importlib.util.spec_from_file_location(
        "gateway_main_for_test_event_format", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_gateway(monkeypatch):
    module = _load_gateway_module()
    monkeypatch.setattr(
        module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)
    return module.LambdaGateway()


def _make_request(
    method="GET", path="/hello", query_string=b"", headers=None, body=b""
):
    """テスト用の ASGI Request を構築"""
    from starlette.testclient import TestClient
    from fastapi import Request

    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))
    else:
        raw_headers = [(b"host", b"localhost:3000")]

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string,
        "headers": raw_headers,
        "root_path": "",
        "server": ("localhost", 3000),
        "client": ("127.0.0.1", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ─── v2 Event (APIGW_V2) テスト ───


@pytest.mark.anyio
async def test_v2_event_has_correct_structure(monkeypatch):
    """APIGW_V2 の event が正しい v2 構造を持つ"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/hello/{name}",
        "event_type": "APIGW_V2",
        "auth_type": "NONE",
    }

    request = _make_request(method="GET", path="/hello/world")
    event = await gateway._build_lambda_event(request, "hello/world", route_info)

    assert event["version"] == "2.0"
    assert event["routeKey"] == "GET /hello/{name}"
    assert event["rawPath"] == "/hello/world"
    assert event["pathParameters"] == {"name": "world"}
    assert event["cookies"] == []
    assert "stageVariables" in event


@pytest.mark.anyio
async def test_v2_event_request_context_dynamic(monkeypatch):
    """v2 event の requestContext が動的に生成される"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/test",
        "event_type": "APIGW_V2",
        "auth_type": "NONE",
    }

    before = int(time.time())
    request = _make_request()
    event = await gateway._build_lambda_event(request, "test", route_info)
    after = int(time.time())

    ctx = event["requestContext"]
    assert ctx["requestId"] != "mock-request-id"  # ハードコードでない
    assert len(ctx["requestId"]) == 36  # UUID format
    assert before <= ctx["timeEpoch"] <= after
    assert "/" in ctx["time"]  # 時刻フォーマット
    assert ctx["stage"] == "Prod"
    assert ctx["domainName"] == "localhost:3000"
    assert ctx["http"]["sourceIp"] == "127.0.0.1"


# ─── v1 Event (APIGW) テスト ───


@pytest.mark.anyio
async def test_v1_event_has_correct_structure(monkeypatch):
    """APIGW の event が正しい v1 構造を持つ"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/users/{user_id}",
        "event_type": "APIGW",
        "auth_type": "NONE",
    }

    request = _make_request(
        method="POST",
        path="/users/42",
        body=b'{"name": "test"}',
    )
    event = await gateway._build_lambda_event(request, "users/42", route_info)

    assert event["version"] == "1.0"
    assert event["httpMethod"] == "POST"
    assert event["path"] == "/users/42"
    assert event["resource"] == "/users/{user_id}"
    assert event["pathParameters"] == {"user_id": "42"}
    assert event["body"] == '{"name": "test"}'
    assert "multiValueHeaders" in event
    assert "multiValueQueryStringParameters" in event


@pytest.mark.anyio
async def test_v1_event_request_context_matches_old_code(monkeypatch):
    """v1 event の requestContext が旧コードの構造に合致する"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/hello",
        "event_type": "APIGW",
        "auth_type": "NONE",
    }

    request = _make_request()
    event = await gateway._build_lambda_event(request, "hello", route_info)

    ctx = event["requestContext"]
    assert ctx["httpMethod"] == "GET"
    assert ctx["resourcePath"] == "/hello"
    assert ctx["path"] == "/hello"
    assert ctx["stage"] == "Prod"
    assert "identity" in ctx
    assert ctx["identity"]["sourceIp"] == "127.0.0.1"


@pytest.mark.anyio
async def test_v1_event_does_not_have_v2_fields(monkeypatch):
    """v1 event に v2 固有フィールドが混入していない"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/test",
        "event_type": "APIGW",
        "auth_type": "NONE",
    }

    request = _make_request()
    event = await gateway._build_lambda_event(request, "test", route_info)

    assert "routeKey" not in event
    assert "rawPath" not in event
    assert "rawQueryString" not in event
    assert "cookies" not in event


# ─── Headers 正規化テスト ───


@pytest.mark.anyio
async def test_headers_are_capitalized(monkeypatch):
    """Headers キーが Content-Type 形式に正規化される"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/test",
        "event_type": "APIGW_V2",
        "auth_type": "NONE",
    }

    request = _make_request(
        headers={
            "content-type": "application/json",
            "x-custom-header": "value",
            "host": "localhost:3000",
        }
    )
    event = await gateway._build_lambda_event(request, "test", route_info)

    assert "Content-Type" in event["headers"]
    assert "X-Custom-Header" in event["headers"]
    assert "Host" in event["headers"]
    # 小文字キーが残っていないこと
    assert "content-type" not in event["headers"]
    assert "x-custom-header" not in event["headers"]


@pytest.mark.anyio
async def test_v1_multi_value_headers(monkeypatch):
    """v1 event で multiValueHeaders が生成される"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/test",
        "event_type": "APIGW",
        "auth_type": "NONE",
    }

    request = _make_request(headers={"accept": "application/json", "host": "localhost"})
    event = await gateway._build_lambda_event(request, "test", route_info)

    assert "multiValueHeaders" in event
    assert event["multiValueHeaders"]["Accept"] == ["application/json"]


# ─── EventType デフォルト値テスト ───


@pytest.mark.anyio
async def test_default_event_type_is_v2(monkeypatch):
    """event_type 未指定時は v2 形式がデフォルト"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/test",
        "auth_type": "NONE",
    }

    request = _make_request()
    event = await gateway._build_lambda_event(request, "test", route_info)

    assert event["version"] == "2.0"


# ─── Query String テスト ───


@pytest.mark.anyio
async def test_query_params_in_v1_event(monkeypatch):
    """v1 event で queryStringParameters と multiValueQueryStringParameters が設定される"""
    gateway = _create_gateway(monkeypatch)
    route_info = {
        "path": "/search",
        "event_type": "APIGW",
        "auth_type": "NONE",
    }

    request = _make_request(path="/search", query_string=b"q=hello&page=1")
    event = await gateway._build_lambda_event(request, "search", route_info)

    assert event["queryStringParameters"]["q"] == "hello"
    assert event["queryStringParameters"]["page"] == "1"
    assert event["multiValueQueryStringParameters"]["q"] == ["hello"]
