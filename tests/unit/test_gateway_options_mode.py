import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.responses import JSONResponse

from sapimo.mock.api import options


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _load_gateway_module():
    gateway_dir = Path(__file__).resolve().parents[2] / "docker" / "gateway"
    if str(gateway_dir) not in sys.path:
        sys.path.insert(0, str(gateway_dir))

    module_path = gateway_dir / "main.py"
    spec = importlib.util.spec_from_file_location("gateway_main_for_test", module_path)
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

    gateway = gateway_module.LambdaGateway()
    monkeypatch.setattr(
        gateway.mock_handler,
        "has_mock_definition",
        lambda method, path: False,
    )
    return gateway


async def _request(gateway, path: str):
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_tc_opt_001_api_mode_undefined_mock_route_falls_back_to_lambda(
    monkeypatch,
):
    options.set_api_mode()
    gateway = _create_gateway(monkeypatch)

    monkeypatch.setattr(
        gateway,
        "_find_matching_route",
        lambda method, path: {"function_name": "dummy", "path": "/dummy"},
    )

    async def fake_invoke_lambda(route_info, request, path):
        return JSONResponse(content={"flow": "lambda"}, status_code=201)

    monkeypatch.setattr(gateway, "_invoke_lambda", fake_invoke_lambda)

    response = await _request(gateway, "/undefined-route")

    assert response.status_code == 201
    assert response.json() == {"flow": "lambda"}


@pytest.mark.anyio
async def test_tc_opt_002_mock_mode_undefined_mock_route_returns_default_200(
    monkeypatch,
):
    options.set_mock_mode(200)
    gateway = _create_gateway(monkeypatch)

    async def fail_if_lambda_called(route_info, request, path):
        raise AssertionError("lambda path should not be called in mock mode")

    monkeypatch.setattr(gateway, "_invoke_lambda", fail_if_lambda_called)

    response = await _request(gateway, "/undefined-route")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_tc_opt_004_mock_mode_undefined_mock_route_returns_default_400(
    monkeypatch,
):
    options.set_mock_mode(400)
    gateway = _create_gateway(monkeypatch)

    async def fail_if_lambda_called(route_info, request, path):
        raise AssertionError("lambda path should not be called in mock mode")

    monkeypatch.setattr(gateway, "_invoke_lambda", fail_if_lambda_called)

    response = await _request(gateway, "/undefined-route")

    assert response.status_code == 400
