"""Custom Lambda Authorizer (CUSTOM_TOKEN / CUSTOM_REQUEST / CUSTOM) のテスト"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import Request as FastAPIRequest


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
        "gateway_main_for_test_custom_auth", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
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


def _make_request(scope):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return FastAPIRequest(scope, receive=receive)


# --- _resolve_authorizer_lambda ---


def test_resolve_by_direct_key(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.authorizer_lambdas = {"MyAuth": {"handler": "auth.handler"}}

    assert gw._resolve_authorizer_lambda("MyAuth") == {"handler": "auth.handler"}


def test_resolve_by_arn(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.authorizer_lambdas = {"MyAuth": {"handler": "auth.handler"}}

    arn = "arn:aws:lambda:us-east-1:123456789012:function:MyAuth"
    assert gw._resolve_authorizer_lambda(arn) == {"handler": "auth.handler"}


def test_resolve_by_substring(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.authorizer_lambdas = {"MyAuth": {"handler": "auth.handler"}}

    assert gw._resolve_authorizer_lambda("SomePrefix-MyAuth-Suffix") == {
        "handler": "auth.handler"
    }


def test_resolve_returns_none_when_empty(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.authorizer_lambdas = {}

    assert gw._resolve_authorizer_lambda("anything") is None


def test_resolve_returns_none_when_no_match(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.authorizer_lambdas = {"SomeOther": {"handler": "other.handler"}}

    assert gw._resolve_authorizer_lambda("NoMatch") is None


# --- _build_token_authorizer_event ---


def test_token_event_default_authorization_header(monkeypatch):
    gw = _create_gateway(monkeypatch)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "query_string": b"",
        "headers": [(b"authorization", b"my-token-value")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    route_info = {"path": "/api/test", "auth_source": None}
    headers = {"Authorization": "my-token-value"}

    event = gw._build_token_authorizer_event(route_info, headers, request, "api/test")

    assert event is not None
    assert event["type"] == "TOKEN"
    assert event["authorizationToken"] == "my-token-value"
    assert "methodArn" in event
    assert "GET" in event["methodArn"]


def test_token_event_custom_auth_source_header(monkeypatch):
    gw = _create_gateway(monkeypatch)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/test",
        "query_string": b"",
        "headers": [(b"x-custom-auth", b"secret123")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    route_info = {"path": "/api/test", "auth_source": {"Header": "x-custom-auth"}}
    headers = {"X-Custom-Auth": "secret123"}

    event = gw._build_token_authorizer_event(route_info, headers, request, "api/test")

    assert event is not None
    assert event["authorizationToken"] == "secret123"


def test_token_event_returns_none_when_header_missing(monkeypatch):
    gw = _create_gateway(monkeypatch)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    route_info = {"path": "/api/test", "auth_source": None}
    headers = {}

    event = gw._build_token_authorizer_event(route_info, headers, request, "api/test")

    assert event is None


# --- _build_request_authorizer_event ---


def test_request_event_structure(monkeypatch):
    gw = _create_gateway(monkeypatch)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/items",
        "query_string": b"page=1",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    route_info = {"path": "/api/{id}"}
    headers = {"Content-Type": "application/json"}

    event = gw._build_request_authorizer_event(
        route_info, headers, request, "api/items"
    )

    assert event["type"] == "REQUEST"
    assert "methodArn" in event
    assert event["headers"] == headers
    assert event["queryStringParameters"] == {"page": "1"}
    assert event["requestContext"]["httpMethod"] == "POST"
    assert event["requestContext"]["stage"] == "Prod"


# --- _build_method_arn ---


def test_method_arn_format(monkeypatch):
    gw = _create_gateway(monkeypatch)
    scope = {
        "type": "http",
        "method": "DELETE",
        "path": "/items/123",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    route_info = {"path": "/items/{id}"}

    arn = gw._build_method_arn(route_info, request, "items/123")
    assert arn == (
        "arn:aws:execute-api:us-east-1:123456789012:sapimo/Prod/DELETE/items/123"
    )


# --- Integration: _build_authorizer_context with CUSTOM_TOKEN ---


@pytest.mark.anyio
async def test_custom_token_authorizer_injects_context(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    authorizer_result = {
        "principalId": "user-123",
        "policyDocument": {
            "Statement": [{"Effect": "Allow", "Action": "execute-api:Invoke"}]
        },
        "context": {"userId": "user-123", "role": "admin"},
    }
    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value=authorizer_result)
    gw.local_lambda_runner = mock_runner
    gw.authorizer_lambdas = {
        "MyAuthFunction": {
            "function_name": "MyAuthFunction",
            "handler": "auth.handler",
            "code_uri": "./auth",
            "environment": {},
            "layers": [],
            "runtime": "python3.9",
        }
    }

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/protected",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer my-token")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    headers = {"Authorization": "Bearer my-token"}

    route_info = {
        "path": "/protected",
        "auth_type": "CUSTOM_TOKEN",
        "authorizer": "arn:aws:lambda:us-east-1:123456789012:function:MyAuthFunction",
        "auth_source": None,
    }

    context = await gw._build_authorizer_context(
        route_info, headers, request, "protected"
    )

    assert context == {"userId": "user-123", "role": "admin"}

    # Verify the authorizer Lambda was called with TOKEN event
    call_args = mock_runner.execute.call_args
    auth_event = call_args[0][1]
    assert auth_event["type"] == "TOKEN"
    assert auth_event["authorizationToken"] == "Bearer my-token"


@pytest.mark.anyio
async def test_custom_request_authorizer_injects_context(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    authorizer_result = {
        "principalId": "api-user",
        "policyDocument": {"Statement": [{"Effect": "Allow"}]},
        "context": {"tenant": "acme"},
    }
    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value=authorizer_result)
    gw.local_lambda_runner = mock_runner
    gw.authorizer_lambdas = {
        "ReqAuth": {
            "function_name": "ReqAuth",
            "handler": "auth.req_handler",
            "code_uri": "./auth",
            "environment": {},
            "layers": [],
            "runtime": "python3.9",
        }
    }

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/data",
        "query_string": b"",
        "headers": [(b"x-api-key", b"key-123")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    headers = {"X-Api-Key": "key-123"}

    route_info = {
        "path": "/api/data",
        "auth_type": "CUSTOM_REQUEST",
        "authorizer": "ReqAuth",
        "auth_source": None,
    }

    context = await gw._build_authorizer_context(
        route_info, headers, request, "api/data"
    )

    assert context == {"tenant": "acme"}

    call_args = mock_runner.execute.call_args
    auth_event = call_args[0][1]
    assert auth_event["type"] == "REQUEST"
    assert auth_event["headers"] == headers


@pytest.mark.anyio
async def test_custom_token_authorizer_deny_raises_403(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    authorizer_result = {
        "principalId": "bad-user",
        "policyDocument": {
            "Statement": [{"Effect": "Deny", "Action": "execute-api:Invoke"}]
        },
        "context": {},
    }
    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value=authorizer_result)
    gw.local_lambda_runner = mock_runner
    gw.authorizer_lambdas = {
        "MyAuth": {
            "function_name": "MyAuth",
            "handler": "auth.handler",
            "code_uri": "./",
            "environment": {},
            "layers": [],
            "runtime": "python3.9",
        }
    }

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/denied",
        "query_string": b"",
        "headers": [(b"authorization", b"bad-token")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    headers = {"Authorization": "bad-token"}

    route_info = {
        "path": "/denied",
        "auth_type": "CUSTOM_TOKEN",
        "authorizer": "MyAuth",
        "auth_source": None,
    }

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await gw._build_authorizer_context(route_info, headers, request, "denied")

    assert exc_info.value.status_code == 403


@pytest.mark.anyio
async def test_custom_token_no_authorizer_ref_returns_none(monkeypatch):
    gw = _create_gateway(monkeypatch)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": [(b"authorization", b"token")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)
    headers = {"Authorization": "token"}

    route_info = {
        "path": "/test",
        "auth_type": "CUSTOM_TOKEN",
        "authorizer": None,
        "auth_source": None,
    }

    context = await gw._build_authorizer_context(route_info, headers, request, "test")
    assert context is None


@pytest.mark.anyio
async def test_custom_token_event_in_v1_lambda_event(monkeypatch):
    """CUSTOM_TOKEN auth context ends up in the v1 Lambda event requestContext."""
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    authorizer_result = {
        "principalId": "user-1",
        "policyDocument": {"Statement": [{"Effect": "Allow"}]},
        "context": {"org": "myorg"},
    }
    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value=authorizer_result)
    gw.local_lambda_runner = mock_runner
    gw.authorizer_lambdas = {
        "Auth": {
            "function_name": "Auth",
            "handler": "auth.handler",
            "code_uri": "./",
            "environment": {},
            "layers": [],
            "runtime": "python3.9",
        }
    }

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/items",
        "query_string": b"",
        "headers": [(b"authorization", b"Bearer tok")],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)

    route_info = {
        "path": "/items",
        "auth_type": "CUSTOM_TOKEN",
        "authorizer": "Auth",
        "auth_source": None,
        "event_type": "APIGW",
    }

    event = await gw._build_lambda_event(request, "items", route_info)

    assert event["version"] == "1.0"
    assert event["requestContext"]["authorizer"] == {"org": "myorg"}
