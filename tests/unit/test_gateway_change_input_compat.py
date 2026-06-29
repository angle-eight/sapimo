"""
チケットA2：change_input 互換テスト
TC-OVR-001: change_input(date=3) + {date} ルート → event["pathParameters"]["date"] == "3"
TC-OVR-002: change_input(pathParameters={"date":"9"}) が優先
TC-OVR-003: ルート未定義キーは queryStringParameters へ
TC-OVR-004: body 上書きの既存挙動維持
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sapimo.mock.api import InputOverride


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
        "gateway_main_for_test_a2", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_gateway(monkeypatch):
    gateway_module = _load_gateway_module()
    monkeypatch.setattr(
        gateway_module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(
        gateway_module.MockHandler, "start_file_watcher", lambda self: None
    )
    return gateway_module.LambdaGateway()


def _base_event():
    """テスト用のベースイベント（_build_lambda_event の代替）"""
    return {
        "version": "2.0",
        "routeKey": "GET /test",
        "rawPath": "/test",
        "rawQueryString": "",
        "headers": {},
        "queryStringParameters": {},
        "pathParameters": {},
        "body": None,
        "isBase64Encoded": False,
        "requestContext": {},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_input_override_accepts_keyword_arguments_for_backward_compatibility():
    """旧ゲートウェイ互換: InputOverride(path=...) 形式を受け付ける"""
    override = InputOverride(path="/simple-trades", method="POST")

    assert override.data["path"] == "/simple-trades"
    assert override.data["method"] == "POST"


@pytest.mark.anyio
async def test_tc_ovr_001_short_key_maps_to_path_params(monkeypatch):
    """change_input(date=3) → pathParameters["date"] == "3" """
    gateway = _create_gateway(monkeypatch)

    # ルート情報をモック
    route_info = {"function_name": "hello_date_get", "path": "/hello/{date}"}
    monkeypatch.setattr(gateway, "_find_matching_route", lambda m, p: route_info)

    # _build_lambda_event をモック（request.body() が不要な形に）
    async def fake_build_event(request, path, ri):
        return _base_event()

    monkeypatch.setattr(gateway, "_build_lambda_event", fake_build_event)

    # lambda_containers にエントリを追加
    gateway.lambda_containers["hello_date_get"] = "lambda-hello-date-get"

    # _invoke_lambda_with_override を直接呼んで event を確認する
    from unittest.mock import MagicMock
    from fastapi import Request as FastAPIRequest

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello/99",
        "query_string": b"",
        "headers": [],
    }
    request = FastAPIRequest(scope)

    override = InputOverride({"date": 3})

    sent_events = []

    async def fake_http_post(self_client, url, json, timeout):
        sent_events.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"statusCode": 200, "body": "{}"}
        return mock_resp

    import httpx

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_post)

    await gateway._invoke_lambda_with_override(override, "GET", "hello/99", request)

    assert len(sent_events) == 1
    assert sent_events[0]["pathParameters"].get("date") == "3", (
        f"Expected pathParameters.date='3', got: {sent_events[0]}"
    )


@pytest.mark.anyio
async def test_tc_ovr_002_explicit_path_parameters_takes_priority(monkeypatch):
    """change_input(pathParameters={"date":"9"}) → event["pathParameters"]["date"] == "9" """
    gateway = _create_gateway(monkeypatch)

    route_info = {"function_name": "hello_date_get", "path": "/hello/{date}"}
    monkeypatch.setattr(gateway, "_find_matching_route", lambda m, p: route_info)

    async def fake_build_event(request, path, ri):
        return _base_event()

    monkeypatch.setattr(gateway, "_build_lambda_event", fake_build_event)
    gateway.lambda_containers["hello_date_get"] = "lambda-hello-date-get"

    sent_events = []

    from unittest.mock import MagicMock
    import httpx
    from fastapi import Request as FastAPIRequest

    async def fake_http_post(self_client, url, json, timeout):
        sent_events.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"statusCode": 200, "body": "{}"}
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_post)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello/1",
        "query_string": b"",
        "headers": [],
    }
    request = FastAPIRequest(scope)
    override = InputOverride({"pathParameters": {"date": "9"}})

    await gateway._invoke_lambda_with_override(override, "GET", "hello/1", request)

    assert sent_events[0]["pathParameters"].get("date") == "9"


@pytest.mark.anyio
async def test_tc_ovr_003_unknown_key_goes_to_query_params(monkeypatch):
    """ルートにないキーは queryStringParameters へ"""
    gateway = _create_gateway(monkeypatch)

    route_info = {"function_name": "hello_get", "path": "/hello"}
    monkeypatch.setattr(gateway, "_find_matching_route", lambda m, p: route_info)

    async def fake_build_event(request, path, ri):
        return _base_event()

    monkeypatch.setattr(gateway, "_build_lambda_event", fake_build_event)
    gateway.lambda_containers["hello_get"] = "lambda-hello-get"

    sent_events = []

    from unittest.mock import MagicMock
    import httpx
    from fastapi import Request as FastAPIRequest

    async def fake_http_post(self_client, url, json, timeout):
        sent_events.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"statusCode": 200, "body": "{}"}
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_post)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello",
        "query_string": b"",
        "headers": [],
    }
    request = FastAPIRequest(scope)
    override = InputOverride({"foo": "bar"})

    await gateway._invoke_lambda_with_override(override, "GET", "hello", request)

    assert sent_events[0]["queryStringParameters"].get("foo") == "bar"


@pytest.mark.anyio
async def test_tc_ovr_004_body_override_works(monkeypatch):
    """body 上書きが壊れていないこと"""
    gateway = _create_gateway(monkeypatch)

    route_info = {"function_name": "hello_post", "path": "/hello"}
    monkeypatch.setattr(gateway, "_find_matching_route", lambda m, p: route_info)

    async def fake_build_event(request, path, ri):
        return _base_event()

    monkeypatch.setattr(gateway, "_build_lambda_event", fake_build_event)
    gateway.lambda_containers["hello_post"] = "lambda-hello-post"

    sent_events = []

    from unittest.mock import MagicMock
    import httpx
    from fastapi import Request as FastAPIRequest

    async def fake_http_post(self_client, url, json, timeout):
        sent_events.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"statusCode": 200, "body": "{}"}
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_post)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/hello",
        "query_string": b"",
        "headers": [],
    }
    request = FastAPIRequest(scope)
    override = InputOverride({"body": '{"key":"value"}'})

    await gateway._invoke_lambda_with_override(override, "POST", "hello", request)

    assert sent_events[0]["body"] == '{"key":"value"}'


@pytest.mark.anyio
async def test_invoke_lambda_non_200_returns_http_502(monkeypatch):
    gateway = _create_gateway(monkeypatch)
    route_info = {"function_name": "simple-trades_post", "path": "/simple-trades"}

    async def fake_build_event(request, path, ri):
        return _base_event()

    monkeypatch.setattr(gateway, "_build_lambda_event", fake_build_event)
    gateway.lambda_containers["simple-trades_post"] = "lambda-simple-trades-post"

    from unittest.mock import MagicMock
    import httpx
    from fastapi import Request as FastAPIRequest

    async def fake_http_post(self_client, url, json, timeout):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = '{"errorMessage":"import failed"}'
        return mock_resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_http_post)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/simple-trades",
        "query_string": b"",
        "headers": [],
    }
    request = FastAPIRequest(scope)

    with pytest.raises(HTTPException) as exc_info:
        await gateway._invoke_lambda(route_info, request, "simple-trades")

    assert exc_info.value.status_code == 502
