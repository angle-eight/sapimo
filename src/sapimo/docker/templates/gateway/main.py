#!/usr/bin/env python3
"""
FastAPI Gateway メインアプリケーション
Lambda コンテナとのルーティング・連携を処理
"""

import asyncio
import datetime
import json
import os
import uuid
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
        self.authorizer_lambdas: dict[str, dict] = {}
        self.triggered_lambdas: dict[str, dict] = {}

        self.user_app: Any = None
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

        if self.user_app is not None:
            try:
                asyncio.get_running_loop().create_task(self._watch_user_app())
            except RuntimeError:
                pass

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

                    event_type = properties.get("EventType", "APIGW_V2")

                    route_key = f"{method.upper()}:{path}"
                    self.lambda_routes[route_key] = {
                        "function_name": func_name,
                        "handler": handler,
                        "code_uri": code_uri,
                        "environment": properties.get("Environment", {}).get(
                            "Variables", {}
                        ),
                        "layers": properties.get("Layers", []),
                        "pip_packages": properties.get("PipPackages", []),
                        "runtime": properties.get("Runtime", "python3.9"),
                        "method": method.upper(),
                        "path": path,
                        "event_type": event_type,
                        "auth_type": auth_type,
                        "authorizer": authorizer,
                        "auth_source": auth_source,
                    }

                    container_name = f"lambda-{self._sanitize_service_name(func_name)}"
                    self.lambda_containers[func_name] = container_name

            # Load authorizer Lambda configs
            for name, lambda_config in config.get("lambdas", {}).items():
                props = lambda_config.get("Properties", {})
                self.authorizer_lambdas[name] = {
                    "function_name": name,
                    "handler": props.get("Handler", "app.lambda_handler"),
                    "code_uri": props.get("CodeUri", "./"),
                    "environment": props.get("Environment", {}).get("Variables", {}),
                    "layers": props.get("Layers", []),
                    "pip_packages": props.get("PipPackages", []),
                    "runtime": props.get("Runtime", "python3.9"),
                }

            # Load S3 trigger Lambda configs
            for bucket, trigger_config in config.get("triggered", {}).items():
                props = trigger_config.get("Properties", {})
                self.triggered_lambdas[bucket] = {
                    "function_name": f"triggered_{bucket}",
                    "handler": props.get("Handler", "app.lambda_handler"),
                    "code_uri": props.get("CodeUri", "./"),
                    "environment": props.get("Environment", {}).get("Variables", {}),
                    "layers": props.get("Layers", []),
                    "pip_packages": props.get("PipPackages", []),
                    "runtime": props.get("Runtime", "python3.9"),
                }

            logger.info(f"Loaded {len(self.lambda_routes)} Lambda routes")
            for route, info in self.lambda_routes.items():
                logger.info(
                    f"  {route} -> {info['function_name']} ({self.lambda_containers[info['function_name']]})"
                )

            app_module_str = config.get("app_module")
            if app_module_str:
                import importlib
                import sys

                sys.path.insert(0, str(self.project_root))
                mod_str, attr = app_module_str.rsplit(":", 1)
                self.user_app = getattr(importlib.import_module(mod_str), attr)
                logger.info("Loaded user FastAPI app: %s", app_module_str)

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
                if self.user_app is not None:
                    return await self._forward_to_user_app(request)
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

    _CORS_HEADERS = frozenset(
        {
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-allow-credentials",
            "access-control-expose-headers",
            "access-control-max-age",
        }
    )

    async def _forward_to_user_app(self, request: Request) -> Response:
        """Lambda \u30eb\u30fc\u30c8\u306b\u4e00\u81f4\u3057\u306a\u3044\u30ea\u30af\u30a8\u30b9\u30c8\u3092\u30e6\u30fc\u30b6\u30fc\u306e FastAPI \u30a2\u30d7\u30ea\u3078\u8ee2\u9001\u3059\u308b\u3002
        httpx.ASGITransport \u3092\u4f7f\u3046\u3053\u3068\u3067\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u3092\u4ecb\u3055\u305a\u30a4\u30f3\u30d7\u30ed\u30bb\u30b9\u3067\u8ee2\u9001\u3059\u308b\u3002
        Gateway \u5074\u306e CORS \u30df\u30c9\u30eb\u30a6\u30a7\u30a2\u304c\u518d\u5ea6\u4ed8\u4e0e\u3059\u308b\u305f\u3081\u3001\u30e6\u30fc\u30b6\u30fc\u30a2\u30d7\u30ea\u304c\u8fd4\u3057\u305f CORS \u30d8\u30c3\u30c0\u30fc\u3092\u9664\u53bb\u3059\u308b\u3002
        """
        body = await request.body()
        transport = httpx.ASGITransport(app=self.user_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.request(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                content=body,
            )
        filtered_headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in self._CORS_HEADERS
        }
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=filtered_headers,
        )

    async def _watch_user_app(self) -> None:
        """\u30e6\u30fc\u30b6\u30fc\u306e FastAPI \u30a2\u30d7\u30ea\u30e2\u30b8\u30e5\u30fc\u30eb\u306e\u5909\u66f4\u3092\u76e3\u8996\u3057\u3066\u52d5\u7684\u518d\u30ed\u30fc\u30c9\u3059\u308b\u3002"""
        import importlib
        import sys
        from pathlib import Path as _Path

        app_module_str = os.environ.get("SAPIMO_APP_MODULE", "")
        if not app_module_str:
            return

        mod_str, attr = app_module_str.rsplit(":", 1)
        mod = sys.modules.get(mod_str)
        if mod is None or not getattr(mod, "__file__", None):
            return

        watch_path = _Path(mod.__file__)
        last_mtime = watch_path.stat().st_mtime

        while True:
            await asyncio.sleep(1)
            try:
                current_mtime = watch_path.stat().st_mtime
                if current_mtime == last_mtime:
                    continue
                last_mtime = current_mtime
                if mod_str in sys.modules:
                    del sys.modules[mod_str]
                new_mod = importlib.import_module(mod_str)
                self.user_app = getattr(new_mod, attr)
                logger.info("Reloaded user FastAPI app: %s", mod_str)
            except Exception:
                logger.exception("Failed to reload user FastAPI app")

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
                    await self._process_s3_triggers()
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
                    await self._process_s3_triggers()
                except Exception:
                    logger.exception("Failed to sync in-process mock data")

    async def _build_lambda_event(self, request: Request, path: str, route_info: dict):
        """AWS Lambda event オブジェクトを構築。

        config の EventType に応じて v1 (APIGW) / v2 (APIGW_V2) 形式を生成する。
        """
        body = await request.body()
        body_str = body.decode() if body else None

        query_params = dict(request.query_params)

        # Headers を Capitalize 正規化 (content-type → Content-Type)
        raw_headers = dict(request.headers)
        headers = {}
        multi_value_headers = {}
        for key, value in raw_headers.items():
            normalized = "-".join(w.capitalize() for w in key.split("-"))
            headers[normalized] = value
            multi_value_headers[normalized] = [value]

        # multiValueQueryStringParameters
        multi_value_query = {}
        for key, value in request.query_params.items():
            multi_value_query[key] = [value]

        path_params = self._extract_path_params(route_info["path"], f"/{path}")

        # 動的 requestContext 共通値
        now = datetime.datetime.now(datetime.timezone.utc)
        request_time = now.strftime("%d/%b/%Y:%H:%M:%S %z")
        request_epoch = int(now.timestamp())
        request_id = str(uuid.uuid4())
        domain_name = request.url.netloc or "localhost"
        source_ip = request.client.host if request.client else "127.0.0.1"

        authorizer_context = await self._build_authorizer_context(
            route_info, headers, request, path
        )

        event_type = route_info.get("event_type", "APIGW_V2")

        if event_type == "APIGW":
            event = self._build_v1_event(
                request=request,
                path=path,
                route_info=route_info,
                body_str=body_str,
                headers=headers,
                multi_value_headers=multi_value_headers,
                query_params=query_params,
                multi_value_query=multi_value_query,
                path_params=path_params,
                request_time=request_time,
                request_epoch=request_epoch,
                request_id=request_id,
                domain_name=domain_name,
                source_ip=source_ip,
                authorizer_context=authorizer_context,
            )
        else:
            event = self._build_v2_event(
                request=request,
                path=path,
                route_info=route_info,
                body_str=body_str,
                headers=headers,
                query_params=query_params,
                path_params=path_params,
                request_time=request_time,
                request_epoch=request_epoch,
                request_id=request_id,
                domain_name=domain_name,
                source_ip=source_ip,
                authorizer_context=authorizer_context,
            )

        return event

    @staticmethod
    def _build_v1_event(
        *,
        request,
        path,
        route_info,
        body_str,
        headers,
        multi_value_headers,
        query_params,
        multi_value_query,
        path_params,
        request_time,
        request_epoch,
        request_id,
        domain_name,
        source_ip,
        authorizer_context,
    ) -> dict:
        """API Gateway v1 (REST API) 形式の Lambda event を構築する。"""
        template_path = route_info["path"]

        request_context = {
            "accountId": "123456789012",
            "apiId": "sapimo-mock",
            "domainName": domain_name,
            "extendedRequestId": None,
            "httpMethod": request.method,
            "identity": {
                "accountId": None,
                "apiKey": None,
                "caller": None,
                "cognitoAuthenticationProvider": None,
                "cognitoAuthenticationType": None,
                "cognitoIdentityPoolId": None,
                "sourceIp": source_ip,
                "user": None,
                "userAgent": headers.get("User-Agent", "Custom User Agent String"),
                "userArn": None,
            },
            "path": template_path,
            "protocol": "HTTP/1.1",
            "requestId": request_id,
            "requestTime": request_time,
            "requestTimeEpoch": request_epoch,
            "resourceId": "123456",
            "resourcePath": template_path,
            "stage": "Prod",
        }

        if authorizer_context:
            request_context["authorizer"] = authorizer_context

        return {
            "version": "1.0",
            "body": body_str,
            "headers": headers,
            "httpMethod": request.method,
            "multiValueHeaders": multi_value_headers,
            "multiValueQueryStringParameters": multi_value_query,
            "path": f"/{path}",
            "pathParameters": path_params,
            "queryStringParameters": query_params,
            "requestContext": request_context,
            "resource": template_path,
            "stageVariables": None,
            "isBase64Encoded": False,
        }

    @staticmethod
    def _build_v2_event(
        *,
        request,
        path,
        route_info,
        body_str,
        headers,
        query_params,
        path_params,
        request_time,
        request_epoch,
        request_id,
        domain_name,
        source_ip,
        authorizer_context,
    ) -> dict:
        """API Gateway v2 (HTTP API) 形式の Lambda event を構築する。"""
        template_path = route_info["path"]
        cookies = [f"{k}={v}" for k, v in request.cookies.items()]

        request_context = {
            "routeKey": f"{request.method} {template_path}",
            "accountId": "123456789012",
            "stage": "Prod",
            "requestId": request_id,
            "apiId": "sapimo-mock",
            "domainName": domain_name,
            "domainPrefix": "id",
            "time": request_time,
            "timeEpoch": request_epoch,
            "http": {
                "method": request.method,
                "path": f"/{path}",
                "protocol": "HTTP/1.1",
                "sourceIp": source_ip,
                "userAgent": headers.get("User-Agent", "Custom User Agent String"),
            },
        }

        if authorizer_context:
            request_context["authorizer"] = authorizer_context

        event = {
            "version": "2.0",
            "routeKey": f"{request.method} {template_path}",
            "rawPath": f"/{path}",
            "rawQueryString": str(request.url.query),
            "cookies": cookies,
            "headers": headers,
            "queryStringParameters": query_params,
            "pathParameters": path_params,
            "body": body_str,
            "requestContext": request_context,
            "stageVariables": {},
            "isBase64Encoded": False,
        }

        return event

    async def _build_authorizer_context(
        self,
        route_info: dict,
        headers: dict[str, str],
        request: Request | None = None,
        path: str | None = None,
    ) -> dict | None:
        """
        認証検証は行わず、AuthTypeに応じてrequestContext.authorizerを構築する。
        CUSTOM_TOKEN / CUSTOM_REQUEST / CUSTOM の場合は Authorizer Lambda を実行し、
        レスポンスの context を返す。
        """
        auth_type = str(route_info.get("auth_type", "NONE")).upper()

        if auth_type in {"JWT", "COGNITO_USER_POOLS"}:
            authorization = headers.get("Authorization")
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

        if auth_type in {"CUSTOM_TOKEN", "CUSTOM", "CUSTOM_REQUEST"}:
            authorizer_ref = route_info.get("authorizer")
            if not authorizer_ref:
                logger.warning(
                    "No authorizer reference for auth_type=%s on path=%s",
                    auth_type,
                    route_info.get("path"),
                )
                return None

            authorizer_config = self._resolve_authorizer_lambda(authorizer_ref)
            if not authorizer_config:
                logger.warning(
                    "Authorizer Lambda not found for reference: %s", authorizer_ref
                )
                return None

            if auth_type == "CUSTOM_TOKEN":
                auth_event = self._build_token_authorizer_event(
                    route_info, headers, request, path
                )
            else:
                auth_event = self._build_request_authorizer_event(
                    route_info, headers, request, path
                )

            if auth_event is None:
                return None

            result = await self._execute_authorizer_lambda(
                authorizer_config, auth_event
            )
            if not result:
                return None

            # Check authorizer policy for Deny
            policy = result.get("policyDocument", {})
            for statement in policy.get("Statement", []):
                if statement.get("Effect") == "Deny":
                    raise HTTPException(
                        status_code=403,
                        detail="Access denied by Lambda authorizer",
                    )

            return result.get("context", {})

        return None

    def _resolve_authorizer_lambda(self, authorizer_ref: str) -> dict | None:
        """Authorizer Lambda の参照を解決して設定を返す。"""
        if not authorizer_ref or not self.authorizer_lambdas:
            return None
        # Direct key match
        if authorizer_ref in self.authorizer_lambdas:
            return self.authorizer_lambdas[authorizer_ref]
        # ARN match: arn:aws:lambda:...:function:Name
        if ":function:" in authorizer_ref:
            func_name = authorizer_ref.rsplit(":", 1)[-1]
            if func_name in self.authorizer_lambdas:
                return self.authorizer_lambdas[func_name]
        # Substring match as fallback
        for name, config in self.authorizer_lambdas.items():
            if name in authorizer_ref:
                return config
        return None

    def _build_token_authorizer_event(
        self,
        route_info: dict,
        headers: dict,
        request: Request | None,
        path: str | None,
    ) -> dict | None:
        """TOKEN Authorizer 用のイベントを構築する。"""
        auth_source = route_info.get("auth_source")
        if isinstance(auth_source, dict):
            header_name = auth_source.get("Header", "Authorization")
        elif isinstance(auth_source, str):
            header_name = auth_source
        else:
            header_name = "Authorization"
        header_name = "-".join(w.capitalize() for w in header_name.split("-"))

        token = headers.get(header_name)
        if not token:
            logger.warning(
                "Token not found in header '%s' for TOKEN authorizer", header_name
            )
            return None

        method_arn = self._build_method_arn(route_info, request, path)
        return {
            "type": "TOKEN",
            "authorizationToken": token,
            "methodArn": method_arn,
        }

    def _build_request_authorizer_event(
        self,
        route_info: dict,
        headers: dict,
        request: Request | None,
        path: str | None,
    ) -> dict:
        """REQUEST Authorizer 用のイベントを構築する。"""
        method_arn = self._build_method_arn(route_info, request, path)
        query_params = dict(request.query_params) if request else {}
        path_params = (
            self._extract_path_params(route_info["path"], f"/{path}") if path else {}
        )
        return {
            "type": "REQUEST",
            "methodArn": method_arn,
            "headers": headers,
            "queryStringParameters": query_params,
            "pathParameters": path_params,
            "requestContext": {
                "accountId": "123456789012",
                "apiId": "sapimo-mock",
                "httpMethod": request.method if request else "GET",
                "resourcePath": route_info.get("path", ""),
                "stage": "Prod",
            },
        }

    @staticmethod
    def _build_method_arn(
        route_info: dict, request: Request | None, path: str | None
    ) -> str:
        """API Gateway メソッド ARN を構築する。"""
        method = request.method if request else "GET"
        resource = path or route_info.get("path", "")
        return f"arn:aws:execute-api:us-east-1:123456789012:sapimo/Prod/{method}/{resource}"

    async def _execute_authorizer_lambda(
        self, authorizer_config: dict, event: dict
    ) -> dict | None:
        """Authorizer Lambda を実行してレスポンスを返す。"""
        if self.single_container_mode:
            if not self.local_lambda_runner:
                logger.error(
                    "Local lambda runner not initialized for authorizer execution"
                )
                return None
            try:
                return await self.local_lambda_runner.execute(authorizer_config, event)
            except Exception:
                logger.exception("Authorizer Lambda execution failed")
                return None
        else:
            logger.warning(
                "Custom Lambda Authorizer is not supported in multi-container mode"
            )
            return None

    async def _process_s3_triggers(self):
        """Lambda 実行後の S3 変更を検知し、トリガー Lambda をチェーン実行する。"""
        if not self.mock_manager or not self.triggered_lambdas:
            return

        max_iterations = 10
        for _ in range(max_iterations):
            changes = self.mock_manager.get_change("s3")
            updated = changes.get("updated", {})
            deleted = changes.get("deleted", {})
            if not updated and not deleted:
                break

            for bucket, keys in updated.items():
                triggered_config = self._find_triggered_lambda(bucket)
                if not triggered_config:
                    logger.info(
                        "S3 changes in bucket '%s' but no trigger configured", bucket
                    )
                    continue
                event = self._build_s3_event(bucket, keys, "ObjectCreated:Put")
                await self._execute_triggered_lambda(triggered_config, event)

            for bucket, keys in deleted.items():
                triggered_config = self._find_triggered_lambda(bucket)
                if not triggered_config:
                    continue
                event = self._build_s3_event(bucket, keys, "ObjectRemoved:Delete")
                await self._execute_triggered_lambda(triggered_config, event)

            self.mock_manager.sync()
        else:
            logger.warning(
                "S3 trigger chain exceeded maximum iterations (%d)", max_iterations
            )

    def _find_triggered_lambda(self, bucket_name: str) -> dict | None:
        """バケット名からトリガー Lambda 設定を解決する。"""
        if bucket_name in self.triggered_lambdas:
            return self.triggered_lambdas[bucket_name]
        for key, config in self.triggered_lambdas.items():
            if (
                key.endswith(f":::{bucket_name}")
                or bucket_name in key
                or key in bucket_name
            ):
                return config
        return None

    @staticmethod
    def _build_s3_event(bucket: str, keys: list[str], event_name: str) -> dict:
        """S3 イベントオブジェクトを構築する。"""
        records = []
        for key in keys:
            records.append(
                {
                    "eventSource": "aws:s3",
                    "eventName": event_name,
                    "s3": {
                        "bucket": {"name": bucket},
                        "object": {"key": key, "size": 0},
                    },
                }
            )
        return {"Records": records}

    async def _execute_triggered_lambda(self, trigger_config: dict, event: dict):
        """トリガー Lambda を実行する。"""
        if not self.single_container_mode or not self.local_lambda_runner:
            logger.warning("S3 trigger Lambda execution requires single-container mode")
            return
        try:
            func_name = trigger_config.get("function_name", "unknown")
            logger.info("Executing S3 triggered Lambda: %s", func_name)
            await self.local_lambda_runner.execute(trigger_config, event)
        except Exception:
            logger.exception("S3 triggered Lambda execution failed")

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
