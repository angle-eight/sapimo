#!/usr/bin/env python3
"""
FastAPI Gateway メインアプリケーション
Lambda コンテナとのルーティング・連携を処理
"""

import json
import os
from pathlib import Path
from typing import Any

import httpx
from jose import jwt
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import yaml

from mock_handler import MockHandler
from sapimo.mock.api import InputOverride, options
from sapimo.mock.mock_manager import MockManager
from sapimo.docker.local_lambda_runner import LocalLambdaRunner
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class LambdaResponse(BaseModel):
    """Lambda 関数の標準レスポンス形式"""

    statusCode: int = 200
    body: Any = None
    headers: dict[str, str] | None = None


class LambdaGateway:
    """Lambda コンテナとの連携ゲートウェイ"""

    def __init__(self):
        self.single_container_mode = os.getenv("SAPIMO_SINGLE_CONTAINER", "0") == "1"
        self.project_root = Path("/workspace")
        self.config_path = self.project_root / "api_mock" / "config.yaml"
        self.mock_manager: MockManager | None = None
        self.local_lambda_runner: LocalLambdaRunner | None = None

        self.app = FastAPI(
            title="Sapimo Lambda Gateway",
            description="FastAPI Gateway for Lambda container orchestration",
        )
        self.lambda_routes: dict[str, dict] = {}
        self.lambda_containers: dict[str, str] = {}

        self.mock_handler = MockHandler()

        self._setup_middleware()
        self._load_configuration()
        self._setup_routes()
        self._setup_openapi_schema_merge()

        if self.single_container_mode:
            self.local_lambda_runner = LocalLambdaRunner(self.project_root)
            self._initialize_local_mock_manager()
            self._register_lifecycle_handlers()

        self.mock_handler.reload_mock_definitions()
        self.mock_handler.start_file_watcher()

    def _initialize_local_mock_manager(self):
        if not self.config_path.exists():
            logger.warning("config.yaml not found for local mock manager")
            return

        try:
            self.mock_manager = MockManager(config_file=self.config_path)
            self.mock_manager.start()
            self.mock_manager.init_data()
            self._resolve_env_placeholders()
            logger.info("Initialized in-process AWS mocks for single-container mode")
        except Exception:
            logger.exception("Failed to initialize in-process AWS mocks")
            raise

    def _resolve_env_placeholders(self):
        """Resolve ${cognito:...} placeholders in all route environment variables."""
        if not self.mock_manager:
            return
        for route_info in self.lambda_routes.values():
            env = route_info.get("environment", {})
            if env:
                route_info["environment"] = self.mock_manager.resolve_placeholders(env)

    def _register_lifecycle_handlers(self):
        @self.app.on_event("shutdown")
        async def _shutdown_single_container_mocks():
            if not self.mock_manager:
                return
            try:
                self.mock_manager.sync()
                self.mock_manager.stop()
            except Exception:
                logger.exception("Failed to shutdown in-process AWS mocks")

    def _setup_middleware(self):
        """ミドルウェアの設定"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _load_configuration(self):
        """設定ファイルからLambda関数とルーティング情報を読み込み"""
        config_path = Path("/workspace/api_mock/config.yaml")

        if not config_path.exists():
            logger.warning("config.yaml not found, using empty configuration")
            return

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            for path, methods in config.get("paths", {}).items():
                for method, props in methods.items():
                    properties = props.get("Properties", {})
                    handler = properties.get("Handler", "app.lambda_handler")
                    code_uri = properties.get("CodeUri", "./")
                    auth_type = str(properties.get("AuthType", "NONE")).upper()
                    authorizer = properties.get("Authorizer")
                    auth_source = properties.get("AuthSource")

                    func_name = f"{path.replace('/', '_').replace('{', '').replace('}', '')}_{method}"
                    if func_name.startswith("_"):
                        func_name = func_name[1:]

                    route_key = f"{method.upper()}:{path}"
                    self.lambda_routes[route_key] = {
                        "function_name": func_name,
                        "handler": handler,
                        "code_uri": code_uri,
                        "environment": properties.get("Environment", {}).get(
                            "Variables", {}
                        ),
                        "layers": properties.get("Layers", []),
                        "runtime": properties.get("Runtime", "python3.9"),
                        "method": method.upper(),
                        "path": path,
                        "auth_type": auth_type,
                        "authorizer": authorizer,
                        "auth_source": auth_source,
                    }

                    container_name = f"lambda-{self._sanitize_service_name(func_name)}"
                    self.lambda_containers[func_name] = container_name

            logger.info(f"Loaded {len(self.lambda_routes)} Lambda routes")
            for route, info in self.lambda_routes.items():
                logger.info(
                    f"  {route} -> {info['function_name']} ({self.lambda_containers[info['function_name']]})"
                )

        except Exception as e:
            logger.exception(f"ERROR loading configuration: {e}")

    def _sanitize_service_name(self, name: str) -> str:
        """サービス名をDocker Composeで使用可能な形式に変換"""
        import re

        sanitized = re.sub(r"[^a-zA-Z0-9\-]", "-", name.lower())
        sanitized = re.sub(r"-+", "-", sanitized)
        return sanitized.strip("-")

    @staticmethod
    def _build_lambda_response(lambda_result: dict) -> Response:
        """Lambda の戻り値から HTTP レスポンスを構築する。

        Lambda は body を JSON 文字列で返すことが多い。
        そのまま JSONResponse に渡すと二重エンコードされるため、
        文字列なら json.loads してから返す。
        """
        status = lambda_result.get("statusCode", 200)
        body = lambda_result.get("body", lambda_result)
        if not isinstance(body, dict):
            try:
                body = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                return Response(status_code=status, content=str(body))
        return JSONResponse(status_code=status, content=body)

    def _setup_routes(self):
        """動的ルーティングの設定"""

        @self.app.get("/health", tags=["system"])
        async def health_check():
            """ヘルスチェック"""
            return {
                "status": "healthy",
                "service": "sapimo-gateway",
                "lambda_routes": len(self.lambda_routes),
                "containers": list(self.lambda_containers.values()),
            }

        @self.app.get("/routes", tags=["system"])
        async def list_routes():
            """ルーティング一覧"""
            return {"routes": self.lambda_routes, "containers": self.lambda_containers}

        # config.yaml の各エンドポイントを個別 FastAPI ルートとして登録
        for route_key, route_info in self.lambda_routes.items():
            handler = self._create_route_handler(route_info)
            tag = self._extract_tag(route_info["path"])
            self.app.add_api_route(
                route_info["path"],
                handler,
                methods=[route_info["method"]],
                summary=route_info["function_name"],
                description=(
                    f"Handler: {route_info['handler']}\n"
                    f"CodeUri: {route_info['code_uri']}\n"
                    f"AuthType: {route_info.get('auth_type', 'NONE')}"
                ),
                tags=[tag],
                response_model=LambdaResponse,
            )

        # 未登録ルート用 catch-all フォールバック
        @self.app.api_route(
            "/{path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
            include_in_schema=False,
        )
        async def fallback_router(path: str, request: Request):
            """未登録ルートのフォールバック"""
            method = request.method
            matched_route = self._find_matching_route(method, path)
            if not matched_route:
                if options.mode == "mock":
                    return JSONResponse(
                        content={"message": "Default mock response"},
                        status_code=options.default_status,
                    )
                raise HTTPException(
                    status_code=404,
                    detail=f"No Lambda function found for {method} /{path}",
                )
            return await self._handle_request(matched_route, method, path, request)

    def _extract_tag(self, path: str) -> str:
        """パスの第1セグメントをタグとして抽出"""
        segments = [s for s in path.split("/") if s and not s.startswith("{")]
        return segments[0] if segments else "root"

    def _setup_openapi_schema_merge(self):
        """ユーザー提供の OpenAPI spec があれば、自動生成スキーマにマージする"""
        from openapi_example_resolver import _load_spec

        user_spec = _load_spec()
        if user_spec is None:
            return

        user_paths = user_spec.get("paths", {})
        user_schemas = user_spec.get("components", {}).get("schemas", {})
        if not user_paths and not user_schemas:
            return

        original_openapi = self.app.openapi

        def merged_openapi():
            schema = original_openapi()

            # パスごとのリクエスト/レスポンス定義をマージ
            for path, methods in user_paths.items():
                if path not in schema.get("paths", {}):
                    continue
                for method, operation in methods.items():
                    method_lower = method.lower()
                    if method_lower not in schema["paths"][path]:
                        continue
                    target = schema["paths"][path][method_lower]
                    if "requestBody" in operation:
                        target["requestBody"] = operation["requestBody"]
                    if "parameters" in operation:
                        target["parameters"] = operation["parameters"]
                    if "responses" in operation:
                        target["responses"] = operation["responses"]

            # components/schemas をマージ
            if user_schemas:
                schema.setdefault("components", {}).setdefault("schemas", {}).update(
                    user_schemas
                )

            return schema

        self.app.openapi = merged_openapi
        logger.info(
            "Merged user OpenAPI spec into schema (%d paths, %d schemas)",
            len(user_paths),
            len(user_schemas),
        )

    def _create_route_handler(self, route_info: dict):
        """個別ルート用のハンドラを生成"""

        async def handler(request: Request):
            path = route_info["path"].lstrip("/")
            return await self._handle_request(route_info, request.method, path, request)

        return handler

    async def _handle_request(
        self, route_info: dict, method: str, path: str, request: Request
    ):
        """Mock → Lambda の共通処理パイプライン"""
        if self.mock_handler.has_mock_definition(method, f"/{path}"):
            try:
                mock_result = await self.mock_handler.handle_mock_request(
                    method, f"/{path}", request
                )

                if mock_result is None:
                    pass
                elif isinstance(mock_result, InputOverride):
                    return await self._invoke_lambda_with_override(
                        mock_result, method, path, request
                    )
                elif isinstance(mock_result, (dict, str, list)):
                    return JSONResponse(content=mock_result)
                elif isinstance(mock_result, int) and 200 <= mock_result < 600:
                    from openapi_example_resolver import resolve_example

                    api_path = f"/{path}"
                    example_content, resolved_status = resolve_example(
                        api_path, method, mock_result
                    )
                    if example_content is not None:
                        return JSONResponse(
                            content=example_content,
                            status_code=resolved_status,
                        )
                    else:
                        return JSONResponse(
                            content=None,
                            status_code=mock_result,
                        )
                else:
                    return JSONResponse(content=mock_result)

            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Mock processing error: {str(e)}"
                )

        if options.mode == "mock":
            return JSONResponse(
                content={"message": "Default mock response"},
                status_code=options.default_status,
            )

        return await self._invoke_lambda(route_info, request, path)

    def _find_matching_route(self, method: str, path: str) -> dict:
        """パスパターンマッチングでルートを検索"""
        exact_key = f"{method}:/{path}"
        if exact_key in self.lambda_routes:
            return self.lambda_routes[exact_key]

        for route_key, route_info in self.lambda_routes.items():
            route_method, route_path = route_key.split(":", 1)

            if route_method != method:
                continue

            if self._match_path_pattern(route_path, f"/{path}"):
                return route_info

        return None

    def _match_path_pattern(self, pattern: str, path: str) -> bool:
        """パスパターンマッチング"""
        import re

        regex_pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
        regex_pattern = f"^{regex_pattern}$"

        return re.match(regex_pattern, path) is not None

    async def _invoke_lambda(self, route_info: dict, request: Request, path: str):
        """Lambda コンテナを呼び出し"""
        function_name = route_info["function_name"]
        container_name = self.lambda_containers[function_name]

        try:
            event = await self._build_lambda_event(request, path, route_info)

            if self.single_container_mode:
                if not self.local_lambda_runner:
                    raise RuntimeError("Local lambda runner is not initialized")
                lambda_result = await self.local_lambda_runner.execute(
                    route_info, event
                )
                return self._build_lambda_response(lambda_result)

            lambda_url = f"http://{container_name}:8080/2015-03-31/functions/function/invocations"

            async with httpx.AsyncClient() as client:
                response = await client.post(lambda_url, json=event, timeout=30.0)

                if response.status_code == 200:
                    lambda_result = response.json()
                    return self._build_lambda_response(lambda_result)
                else:
                    logger.error(
                        "Lambda invocation returned non-200: function=%s container=%s method=%s path=/%s url=%s status=%s response=%s",
                        function_name,
                        container_name,
                        request.method,
                        path,
                        lambda_url,
                        response.status_code,
                        response.text,
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"Lambda container error: {response.text}",
                    )

        except httpx.ConnectError:
            logger.exception(
                "Lambda container unavailable: function=%s container=%s method=%s path=/%s url=%s",
                function_name,
                container_name,
                request.method,
                path,
                lambda_url,
            )
            raise HTTPException(
                status_code=503,
                detail=f"Lambda container '{container_name}' not available",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "Unexpected lambda invocation error: function=%s container=%s method=%s path=/%s",
                function_name,
                container_name,
                request.method,
                path,
            )
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if self.single_container_mode and self.mock_manager:
                try:
                    self.mock_manager.sync()
                except Exception:
                    logger.exception("Failed to sync in-process mock data")

    async def _invoke_lambda_with_override(
        self, input_override: InputOverride, method: str, path: str, request: Request
    ):
        """入力すり替えでLambda関数を実行"""
        matched_route = self._find_matching_route(method, path)
        if not matched_route:
            raise HTTPException(
                status_code=404,
                detail=f"No Lambda function found for {method} /{path}",
            )

        original_event = await self._build_lambda_event(request, path, matched_route)

        route_path = matched_route.get("path", "")
        for key, value in input_override.data.items():
            if key in ["pathParameters", "queryStringParameters", "headers", "body"]:
                if isinstance(value, dict):
                    original_event[key].update(value)
                else:
                    original_event[key] = value
            elif f"{{{key}}}" in route_path:
                original_event["pathParameters"][key] = str(value)
            else:
                original_event["queryStringParameters"][key] = str(value)

        function_name = matched_route["function_name"]
        container_name = self.lambda_containers[function_name]

        try:
            if self.single_container_mode:
                if not self.local_lambda_runner:
                    raise RuntimeError("Local lambda runner is not initialized")
                lambda_result = await self.local_lambda_runner.execute(
                    matched_route, original_event
                )
                return self._build_lambda_response(lambda_result)

            lambda_url = f"http://{container_name}:8080/2015-03-31/functions/function/invocations"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    lambda_url, json=original_event, timeout=30.0
                )

                if response.status_code == 200:
                    lambda_result = response.json()
                    return self._build_lambda_response(lambda_result)
                else:
                    logger.error(
                        "Lambda invocation returned non-200 with override: function=%s container=%s method=%s path=/%s url=%s status=%s response=%s",
                        function_name,
                        container_name,
                        method,
                        path,
                        lambda_url,
                        response.status_code,
                        response.text,
                    )
                    raise HTTPException(
                        status_code=502,
                        detail=f"Lambda container error: {response.text}",
                    )

        except httpx.ConnectError:
            logger.exception(
                "Lambda container unavailable with override: function=%s container=%s method=%s path=/%s url=%s",
                function_name,
                container_name,
                method,
                path,
                lambda_url,
            )
            raise HTTPException(
                status_code=503,
                detail=f"Lambda container '{container_name}' not available",
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(
                "Unexpected lambda invocation error with override: function=%s container=%s method=%s path=/%s",
                function_name,
                container_name,
                method,
                path,
            )
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if self.single_container_mode and self.mock_manager:
                try:
                    self.mock_manager.sync()
                except Exception:
                    logger.exception("Failed to sync in-process mock data")

    async def _build_lambda_event(self, request: Request, path: str, route_info: dict):
        """AWS Lambda event オブジェクトを構築"""
        body = await request.body()

        query_params = dict(request.query_params)

        headers = dict(request.headers)

        path_params = self._extract_path_params(route_info["path"], f"/{path}")

        event = {
            "version": "2.0",
            "routeKey": f"{request.method} {route_info['path']}",
            "rawPath": f"/{path}",
            "rawQueryString": str(request.url.query),
            "headers": headers,
            "queryStringParameters": query_params,
            "pathParameters": path_params,
            "body": body.decode() if body else None,
            "stageVariables": {},
            "isBase64Encoded": False,
            "requestContext": {
                "accountId": "123456789012",
                "apiId": "sapimo-mock",
                "domainName": "localhost",
                "http": {
                    "method": request.method,
                    "path": f"/{path}",
                    "protocol": "HTTP/1.1",
                    "sourceIp": "127.0.0.1",
                },
                "requestId": "mock-request-id",
                "stage": "prod",
                "time": "01/Jan/2025:00:00:00 +0000",
                "timeEpoch": 1704067200,
            },
        }

        authorizer_context = self._build_authorizer_context(route_info, headers)
        if authorizer_context:
            event["requestContext"]["authorizer"] = authorizer_context

        return event

    def _build_authorizer_context(
        self, route_info: dict, headers: dict[str, str]
    ) -> dict | None:
        """
        認証検証は行わず、AuthTypeに応じてrequestContext.authorizerを構築する。
        旧ローカル実行系（mock/executer）の振る舞いに合わせる。
        """
        auth_type = str(route_info.get("auth_type", "NONE")).upper()

        if auth_type in {"JWT", "COGNITO_USER_POOLS"}:
            authorization = headers.get("authorization") or headers.get("Authorization")
            if not authorization:
                return None

            token = authorization.replace("Bearer ", "").strip()
            if not token:
                return None

            try:
                claims = jwt.get_unverified_claims(token)
                return {"jwt": {"claims": claims, "scopes": None}}
            except Exception:
                return None

        if auth_type == "AWS_IAM":
            return {
                "iam": {
                    "accessKey": "AKIAXXXXXXXXXXXXXXXX",
                    "accountId": "1234567890",
                    "callerId": "XXXXXXXXXXXXXXX:CognitoIdentityCredentials",
                    "cognitoIdentity": {
                        "amr": ["foo"],
                        "identityId": "us-east-1:identity-id",
                        "identityPoolId": "us-east-1:pool-id",
                    },
                    "principalOrgId": "principal-org-id",
                    "userArn": "arn:aws:iam::1234567890:user/Admin",
                    "userId": "XXXXXXXXXXXXXXXX",
                }
            }

        return None

    def _extract_path_params(self, pattern: str, actual_path: str) -> dict[str, str]:
        """パスパラメータを抽出"""
        import re

        param_names = re.findall(r"\{([^}]+)\}", pattern)

        if not param_names:
            return {}

        regex_pattern = pattern
        for param_name in param_names:
            regex_pattern = regex_pattern.replace(f"{{{param_name}}}", r"([^/]+)")

        match = re.match(f"^{regex_pattern}$", actual_path)
        if match:
            return dict(zip(param_names, match.groups()))

        return {}


def main():
    """Gateway サーバーを起動"""
    gateway = LambdaGateway()

    host = os.environ.get("SAPIMO_HOST", "0.0.0.0")
    port = int(os.environ.get("SAPIMO_PORT", "3000"))

    print("=" * 50)
    print("Sapimo Lambda Gateway Starting")
    print("=" * 50)
    print(f"Listening on {host}:{port}")
    print(f"Lambda routes: {len(gateway.lambda_routes)}")

    uvicorn.run(gateway.app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
