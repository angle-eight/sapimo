import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import Request as FastAPIRequest
from jose import jwt


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
        "gateway_main_for_test_auth_passthrough", module_path
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


@pytest.mark.anyio
async def test_jwt_authtype_injects_unverified_claims(monkeypatch):
    gateway = _create_gateway(monkeypatch)

    token = jwt.encode({"sub": "user-1", "scope": "read"}, "dummy", algorithm="HS256")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)

    route_info = {"path": "/hello", "auth_type": "JWT"}
    event = await gateway._build_lambda_event(request, "hello", route_info)

    assert "authorizer" in event["requestContext"]
    assert event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] == "user-1"


@pytest.mark.anyio
async def test_jwt_authtype_without_authorization_header_skips_authorizer(monkeypatch):
    gateway = _create_gateway(monkeypatch)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)

    route_info = {"path": "/hello", "auth_type": "JWT"}
    event = await gateway._build_lambda_event(request, "hello", route_info)

    assert "authorizer" not in event["requestContext"]


@pytest.mark.anyio
async def test_aws_iam_authtype_sets_dummy_iam_authorizer(monkeypatch):
    gateway = _create_gateway(monkeypatch)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/hello",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }
    request = _make_request(scope)

    route_info = {"path": "/hello", "auth_type": "AWS_IAM"}
    event = await gateway._build_lambda_event(request, "hello", route_info)

    assert event["requestContext"]["authorizer"]["iam"]["accountId"] == "1234567890"
