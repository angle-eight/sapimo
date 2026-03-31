"""
FastAPI 個別ルート登録 & /docs 自動生成テスト
TC-DOCS-001: 個別ルートが /openapi.json に反映される
TC-DOCS-002: タグがパスプレフィックスで自動グルーピングされる
TC-DOCS-003: 個別ルート経由で Mock → Lambda フォールスルー
TC-DOCS-004: 個別ルート経由で Mock dict 返却
TC-DOCS-005: 未登録パスが catch-all → 404
TC-DOCS-006: 未登録パスで options.mode=="mock" → デフォルトレスポンス
TC-DOCS-007: LambdaResponse が /openapi.json の schemas に反映
TC-DOCS-008: /docs が 200 を返す
TC-DOCS-009: ユーザー OpenAPI spec の requestBody/responses がマージされる
TC-DOCS-010: ユーザー OpenAPI spec の components/schemas がマージされる
TC-DOCS-011: ユーザー OpenAPI spec がない場合はマージされない
TC-DOCS-012: ユーザー spec に存在しないパスは変更されない
"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.responses import JSONResponse

from sapimo.mock.api import options


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
        "gateway_main_for_test_docs", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_gateway_with_routes(monkeypatch, routes: dict[str, dict] | None = None):
    """lambda_routes を指定して LambdaGateway を生成"""
    gateway_module = _load_gateway_module()

    monkeypatch.setattr(
        gateway_module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(
        gateway_module.MockHandler, "start_file_watcher", lambda self: None
    )

    if routes is not None:
        # _load_configuration を差し替え、lambda_routes を手動設定
        original_load = gateway_module.LambdaGateway._load_configuration

        def patched_load(self):
            self.lambda_routes = dict(routes)
            for route_info in self.lambda_routes.values():
                fn = route_info["function_name"]
                self.lambda_containers[fn] = f"lambda-{fn}"

        monkeypatch.setattr(
            gateway_module.LambdaGateway, "_load_configuration", patched_load
        )

    gateway = gateway_module.LambdaGateway()
    return gateway


def _sample_routes():
    """テスト用ルート定義"""
    return {
        "GET:/users/{user_id}": {
            "function_name": "users_user_id_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/users",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/users/{user_id}",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
        "POST:/users": {
            "function_name": "users_post",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/users",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "POST",
            "path": "/users",
            "auth_type": "JWT",
            "authorizer": None,
            "auth_source": None,
        },
        "GET:/items": {
            "function_name": "items_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/items",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/items",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
    }


async def _get(gateway, path: str):
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.anyio
async def test_tc_docs_001_routes_in_openapi_json(monkeypatch):
    """個別ルートが /openapi.json に反映される"""
    gateway = _create_gateway_with_routes(monkeypatch, _sample_routes())

    response = await _get(gateway, "/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    paths = schema["paths"]

    assert "/users/{user_id}" in paths
    assert "get" in paths["/users/{user_id}"]

    assert "/users" in paths
    assert "post" in paths["/users"]

    assert "/items" in paths
    assert "get" in paths["/items"]


@pytest.mark.anyio
async def test_tc_docs_002_tags_auto_grouped_by_prefix(monkeypatch):
    """タグがパスプレフィックスで自動グルーピングされる"""
    gateway = _create_gateway_with_routes(monkeypatch, _sample_routes())

    response = await _get(gateway, "/openapi.json")

    schema = response.json()
    paths = schema["paths"]

    # /users/{user_id} GET と /users POST は同じ "users" タグ
    users_id_tags = paths["/users/{user_id}"]["get"].get("tags", [])
    users_post_tags = paths["/users"]["post"].get("tags", [])
    items_tags = paths["/items"]["get"].get("tags", [])

    assert "users" in users_id_tags
    assert "users" in users_post_tags
    assert "items" in items_tags


@pytest.mark.anyio
async def test_tc_docs_003_mock_none_falls_through_to_lambda(monkeypatch):
    """個別ルート経由で Mock が None → Lambda フォールスルー"""
    hello_route = {
        "GET:/hello": {
            "function_name": "hello_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/hello",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/hello",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
    }
    gateway = _create_gateway_with_routes(monkeypatch, hello_route)

    # MockHandler: has_mock_definition → True, handle_mock_request → None
    monkeypatch.setattr(
        gateway.mock_handler, "has_mock_definition", lambda method, path: True
    )

    async def mock_returns_none(method, path, request):
        return None

    monkeypatch.setattr(gateway.mock_handler, "handle_mock_request", mock_returns_none)

    # Lambda 実行をモックして呼出を記録
    lambda_called = []

    async def fake_invoke_lambda(route_info, request, path):
        lambda_called.append(route_info["function_name"])
        return JSONResponse(content={"flow": "lambda"}, status_code=200)

    monkeypatch.setattr(gateway, "_invoke_lambda", fake_invoke_lambda)

    response = await _get(gateway, "/hello")

    assert response.status_code == 200
    assert response.json() == {"flow": "lambda"}
    assert lambda_called == ["hello_get"]


@pytest.mark.anyio
async def test_tc_docs_004_mock_dict_returned(monkeypatch):
    """個別ルート経由で Mock dict 返却"""
    hello_route = {
        "GET:/hello": {
            "function_name": "hello_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/hello",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/hello",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
    }
    gateway = _create_gateway_with_routes(monkeypatch, hello_route)

    monkeypatch.setattr(
        gateway.mock_handler, "has_mock_definition", lambda method, path: True
    )

    async def mock_returns_dict(method, path, request):
        return {"msg": "mock"}

    monkeypatch.setattr(gateway.mock_handler, "handle_mock_request", mock_returns_dict)

    response = await _get(gateway, "/hello")

    assert response.status_code == 200
    assert response.json() == {"msg": "mock"}


@pytest.mark.anyio
async def test_tc_docs_005_unregistered_path_returns_404(monkeypatch):
    """未登録パスが catch-all フォールバック → 404"""
    options.set_api_mode()
    hello_route = {
        "GET:/hello": {
            "function_name": "hello_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/hello",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/hello",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
    }
    gateway = _create_gateway_with_routes(monkeypatch, hello_route)

    monkeypatch.setattr(
        gateway.mock_handler, "has_mock_definition", lambda method, path: False
    )

    response = await _get(gateway, "/nonexistent")

    assert response.status_code == 404


@pytest.mark.anyio
async def test_tc_docs_006_unregistered_path_mock_mode_returns_default(monkeypatch):
    """未登録パスで options.mode=="mock" → デフォルトレスポンス"""
    options.set_mock_mode(200)
    hello_route = {
        "GET:/hello": {
            "function_name": "hello_get",
            "handler": "app.lambda_handler",
            "code_uri": "lambda/hello",
            "environment": {},
            "layers": [],
            "runtime": "python3.12",
            "method": "GET",
            "path": "/hello",
            "auth_type": "NONE",
            "authorizer": None,
            "auth_source": None,
        },
    }
    gateway = _create_gateway_with_routes(monkeypatch, hello_route)

    monkeypatch.setattr(
        gateway.mock_handler, "has_mock_definition", lambda method, path: False
    )

    response = await _get(gateway, "/nonexistent")

    assert response.status_code == 200
    assert response.json() == {"message": "Default mock response"}

    # クリーンアップ
    options.set_api_mode()


@pytest.mark.anyio
async def test_tc_docs_007_lambda_response_in_schema(monkeypatch):
    """LambdaResponse が /openapi.json の schemas に反映される"""
    gateway = _create_gateway_with_routes(monkeypatch, _sample_routes())

    response = await _get(gateway, "/openapi.json")

    schema = response.json()
    schemas = schema.get("components", {}).get("schemas", {})

    assert "LambdaResponse" in schemas
    props = schemas["LambdaResponse"]["properties"]
    assert "statusCode" in props
    assert "body" in props
    assert "headers" in props


@pytest.mark.anyio
async def test_tc_docs_008_docs_endpoint_returns_200(monkeypatch):
    """/docs エンドポイントが 200 を返す（Swagger UI HTML）"""
    gateway = _create_gateway_with_routes(monkeypatch, _sample_routes())

    response = await _get(gateway, "/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def _create_gateway_with_user_spec(monkeypatch, routes, user_spec):
    """ルート定義 + ユーザー OpenAPI spec 付きで LambdaGateway を生成"""
    gateway_module = _load_gateway_module()

    monkeypatch.setattr(
        gateway_module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(
        gateway_module.MockHandler, "start_file_watcher", lambda self: None
    )

    original_load = gateway_module.LambdaGateway._load_configuration

    def patched_load(self):
        self.lambda_routes = dict(routes)
        for route_info in self.lambda_routes.values():
            fn = route_info["function_name"]
            self.lambda_containers[fn] = f"lambda-{fn}"

    monkeypatch.setattr(
        gateway_module.LambdaGateway, "_load_configuration", patched_load
    )

    # _load_spec をモックしてユーザー spec を返す
    import openapi_example_resolver

    monkeypatch.setattr(
        openapi_example_resolver, "_load_spec", lambda path=None: user_spec
    )

    gateway = gateway_module.LambdaGateway()
    return gateway


@pytest.mark.anyio
async def test_tc_docs_009_user_spec_request_response_merged(monkeypatch):
    """ユーザー OpenAPI spec の requestBody/responses がマージされる"""
    user_spec = {
        "paths": {
            "/users": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/CreateUserRequest"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "User created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/UserResponse"
                                    }
                                }
                            },
                        },
                        "400": {"description": "Bad Request"},
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "CreateUserRequest": {
                    "type": "object",
                    "required": ["name", "email"],
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string", "format": "email"},
                    },
                },
                "UserResponse": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                },
            }
        },
    }

    gateway = _create_gateway_with_user_spec(monkeypatch, _sample_routes(), user_spec)
    response = await _get(gateway, "/openapi.json")

    schema = response.json()
    post_op = schema["paths"]["/users"]["post"]

    # requestBody がマージされている
    assert "requestBody" in post_op
    assert post_op["requestBody"]["required"] is True
    ref = post_op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/CreateUserRequest"

    # responses がマージされている
    assert "200" in post_op["responses"]
    assert post_op["responses"]["200"]["description"] == "User created"
    assert "400" in post_op["responses"]

    # schemas がマージされている
    schemas = schema["components"]["schemas"]
    assert "CreateUserRequest" in schemas
    assert "UserResponse" in schemas
    assert schemas["CreateUserRequest"]["required"] == ["name", "email"]


@pytest.mark.anyio
async def test_tc_docs_010_user_spec_schemas_only(monkeypatch):
    """ユーザー OpenAPI spec の components/schemas のみでもマージされる"""
    user_spec = {
        "paths": {},
        "components": {
            "schemas": {
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "integer"},
                        "message": {"type": "string"},
                    },
                }
            }
        },
    }

    gateway = _create_gateway_with_user_spec(monkeypatch, _sample_routes(), user_spec)
    response = await _get(gateway, "/openapi.json")

    schema = response.json()
    schemas = schema["components"]["schemas"]

    assert "ErrorResponse" in schemas
    assert "LambdaResponse" in schemas  # 自動生成分も残存


@pytest.mark.anyio
async def test_tc_docs_011_no_user_spec_no_merge(monkeypatch):
    """ユーザー OpenAPI spec がない場合は自動生成のまま"""
    gateway_module = _load_gateway_module()

    monkeypatch.setattr(
        gateway_module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(
        gateway_module.MockHandler, "start_file_watcher", lambda self: None
    )

    def patched_load(self):
        self.lambda_routes = dict(_sample_routes())
        for route_info in self.lambda_routes.values():
            fn = route_info["function_name"]
            self.lambda_containers[fn] = f"lambda-{fn}"

    monkeypatch.setattr(
        gateway_module.LambdaGateway, "_load_configuration", patched_load
    )

    import openapi_example_resolver

    monkeypatch.setattr(openapi_example_resolver, "_load_spec", lambda path=None: None)

    gateway = gateway_module.LambdaGateway()
    response = await _get(gateway, "/openapi.json")

    schema = response.json()

    # 自動生成の LambdaResponse のみ存在
    schemas = schema.get("components", {}).get("schemas", {})
    assert "LambdaResponse" in schemas
    # ユーザー定義スキーマは無い
    assert "CreateUserRequest" not in schemas


@pytest.mark.anyio
async def test_tc_docs_012_unmatched_paths_not_affected(monkeypatch):
    """ユーザー spec に存在するがルート登録されていないパスは無視される"""
    user_spec = {
        "paths": {
            "/users": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"schema": {"type": "object"}}}
                    }
                }
            },
            "/nonexistent": {
                "get": {"responses": {"200": {"description": "Should not appear"}}}
            },
        },
    }

    gateway = _create_gateway_with_user_spec(monkeypatch, _sample_routes(), user_spec)
    response = await _get(gateway, "/openapi.json")

    schema = response.json()

    # /users POST は正しくマージ
    assert "requestBody" in schema["paths"]["/users"]["post"]

    # /nonexistent は登録されていないのでスキーマに現れない
    assert "/nonexistent" not in schema["paths"]
