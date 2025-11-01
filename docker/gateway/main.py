#!/usr/bin/env python3
"""
FastAPI Gateway メインアプリケーション
Lambda コンテナとのルーティング・連携を処理
"""

import os
import json
import asyncio
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import yaml

# Mock機能をインポート
import sys

sys.path.insert(0, "/workspace/src")
from mock_handler import MockHandler
from sapimo.mock.api import InputOverride


class LambdaGateway:
    """Lambda コンテナとの連携ゲートウェイ"""

    def __init__(self):
        self.app = FastAPI(
            title="Sapimo Lambda Gateway",
            description="FastAPI Gateway for Lambda container orchestration",
        )
        self.lambda_routes: dict[str, dict] = {}
        self.lambda_containers: dict[
            str, str
        ] = {}  # function_name -> container_service_name

        # Mock機能の初期化
        self.mock_handler = MockHandler()

        self._setup_middleware()
        self._load_configuration()
        self._setup_routes()

        # Mock定義の初回読み込みとファイル監視開始
        self.mock_handler.reload_mock_definitions()
        self.mock_handler.start_file_watcher()

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
            print("WARNING: config.yaml not found, using empty configuration")
            return

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            # パスとLambda関数の対応を構築
            for path, methods in config.get("paths", {}).items():
                for method, props in methods.items():
                    handler = props.get("Properties", {}).get(
                        "Handler", "app.lambda_handler"
                    )
                    code_uri = props.get("Properties", {}).get("CodeUri", "./")

                    # 関数名を生成
                    func_name = f"{path.replace('/', '_').replace('{', '').replace('}', '')}_{method}"
                    if func_name.startswith("_"):
                        func_name = func_name[1:]

                    # ルーティング情報を保存
                    route_key = f"{method.upper()}:{path}"
                    self.lambda_routes[route_key] = {
                        "function_name": func_name,
                        "handler": handler,
                        "code_uri": code_uri,
                        "method": method.upper(),
                        "path": path,
                    }

                    # コンテナ名を生成（compose_generator.pyと同じロジック）
                    container_name = f"lambda-{self._sanitize_service_name(func_name)}"
                    self.lambda_containers[func_name] = container_name

            print(f"Loaded {len(self.lambda_routes)} Lambda routes")
            for route, info in self.lambda_routes.items():
                print(
                    f"  {route} -> {info['function_name']} ({self.lambda_containers[info['function_name']]})"
                )

        except Exception as e:
            print(f"ERROR loading configuration: {e}")

    def _sanitize_service_name(self, name: str) -> str:
        """サービス名をDocker Composeで使用可能な形式に変換"""
        import re

        sanitized = re.sub(r"[^a-zA-Z0-9\-]", "-", name.lower())
        sanitized = re.sub(r"-+", "-", sanitized)
        return sanitized.strip("-")

    def _setup_routes(self):
        """動的ルーティングの設定"""

        @self.app.get("/health")
        async def health_check():
            """ヘルスチェック"""
            return {
                "status": "healthy",
                "service": "sapimo-gateway",
                "lambda_routes": len(self.lambda_routes),
                "containers": list(self.lambda_containers.values()),
            }

        @self.app.get("/routes")
        async def list_routes():
            """ルーティング一覧"""
            return {"routes": self.lambda_routes, "containers": self.lambda_containers}

        # 統合ルーティング（Mock → Lambda の優先順位）
        @self.app.api_route(
            "/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        )
        async def unified_router(path: str, request: Request):
            """統合ルーター: Mock → Lambda の順で処理"""
            method = request.method

            # 1. Mock定義をチェック
            if self.mock_handler.has_mock_definition(method, f"/{path}"):
                try:
                    mock_result = await self.mock_handler.handle_mock_request(
                        method, f"/{path}", request
                    )

                    # Mock関数の結果による分岐処理
                    if mock_result is None:
                        # None の場合: Lambda実行にフォールバック
                        pass  # 下のLambda処理に進む
                    elif isinstance(mock_result, InputOverride):
                        # 入力すり替えでLambda実行
                        return await self._invoke_lambda_with_override(
                            mock_result, method, path, request
                        )
                    elif isinstance(mock_result, (dict, str, list)):
                        # スタブとして固定値を返す
                        return JSONResponse(content=mock_result)
                    elif isinstance(mock_result, int) and 200 <= mock_result < 300:
                        # OpenAPI example を返す（TODO: 実装）
                        return JSONResponse(
                            content={
                                "status": mock_result,
                                "message": "OpenAPI example",
                            }
                        )
                    else:
                        # その他の戻り値もスタブとして処理
                        return JSONResponse(content=mock_result)

                except Exception as e:
                    # Mock処理でエラーが発生した場合
                    raise HTTPException(
                        status_code=500, detail=f"Mock processing error: {str(e)}"
                    )

            # 2. Lambda実行（Mock未定義 or None返却の場合）
            # 部分マッチングを試行（パラメータ付きパス）
            matched_route = self._find_matching_route(method, path)

            if not matched_route:
                raise HTTPException(
                    status_code=404,
                    detail=f"No Lambda function found for {method} /{path}",
                )

            # Lambda関数を呼び出し
            return await self._invoke_lambda(matched_route, request, path)

    def _find_matching_route(self, method: str, path: str) -> dict:
        """パスパターンマッチングでルートを検索"""
        # 完全一致を最初に試行
        exact_key = f"{method}:/{path}"
        if exact_key in self.lambda_routes:
            return self.lambda_routes[exact_key]

        # パラメータ付きパスをチェック
        for route_key, route_info in self.lambda_routes.items():
            route_method, route_path = route_key.split(":", 1)

            if route_method != method:
                continue

            # パスパラメータのマッチング（簡単な実装）
            if self._match_path_pattern(route_path, f"/{path}"):
                return route_info

        return None

    def _match_path_pattern(self, pattern: str, path: str) -> bool:
        """パスパターンマッチング"""
        import re

        # {param} を正規表現パターンに変換
        regex_pattern = re.sub(r"\{[^}]+\}", r"[^/]+", pattern)
        regex_pattern = f"^{regex_pattern}$"

        return re.match(regex_pattern, path) is not None

    async def _invoke_lambda(self, route_info: dict, request: Request, path: str):
        """Lambda コンテナを呼び出し"""
        function_name = route_info["function_name"]
        container_name = self.lambda_containers[function_name]

        try:
            # Lambda event を構築
            event = await self._build_lambda_event(request, path, route_info)

            # Lambda コンテナに HTTP リクエスト
            lambda_url = f"http://{container_name}:8080/2015-03-31/functions/function/invocations"

            async with httpx.AsyncClient() as client:
                response = await client.post(lambda_url, json=event, timeout=30.0)

                if response.status_code == 200:
                    lambda_result = response.json()
                    return JSONResponse(
                        content=lambda_result.get("body", lambda_result),
                        status_code=lambda_result.get("statusCode", 200),
                    )
                else:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Lambda container error: {response.text}",
                    )

        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail=f"Lambda container '{container_name}' not available",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def _invoke_lambda_with_override(
        self, input_override: InputOverride, method: str, path: str, request: Request
    ):
        """入力すり替えでLambda関数を実行"""
        # 元のルート情報を取得
        matched_route = self._find_matching_route(method, path)
        if not matched_route:
            raise HTTPException(
                status_code=404,
                detail=f"No Lambda function found for {method} /{path}",
            )

        # 元のイベントを構築
        original_event = await self._build_lambda_event(request, path, matched_route)

        # 入力値を上書き
        for key, value in input_override.data.items():
            if key in ["queryStringParameters", "pathParameters"]:
                original_event[key].update(value)
            else:
                original_event[key] = value

        # Lambda実行
        function_name = matched_route["function_name"]
        container_name = self.lambda_containers[function_name]

        try:
            lambda_url = f"http://{container_name}:8080/2015-03-31/functions/function/invocations"

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    lambda_url, json=original_event, timeout=30.0
                )

                if response.status_code == 200:
                    lambda_result = response.json()
                    return JSONResponse(
                        content=lambda_result.get("body", lambda_result),
                        status_code=lambda_result.get("statusCode", 200),
                    )
                else:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Lambda container error: {response.text}",
                    )

        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail=f"Lambda container '{container_name}' not available",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def _build_lambda_event(self, request: Request, path: str, route_info: dict):
        """AWS Lambda event オブジェクトを構築"""
        # リクエストボディ
        body = await request.body()

        # クエリパラメータ
        query_params = dict(request.query_params)

        # ヘッダー
        headers = dict(request.headers)

        # パスパラメータ（簡単な実装）
        path_params = self._extract_path_params(route_info["path"], f"/{path}")

        # API Gateway v2 形式のイベント
        event = {
            "version": "2.0",
            "routeKey": f"{request.method} {route_info['path']}",
            "rawPath": f"/{path}",
            "rawQueryString": str(request.url.query),
            "headers": headers,
            "queryStringParameters": query_params,
            "pathParameters": path_params,
            "body": body.decode() if body else None,
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

        return event

    def _extract_path_params(self, pattern: str, actual_path: str) -> dict[str, str]:
        """パスパラメータを抽出"""
        import re

        # パラメータ名を抽出
        param_names = re.findall(r"\{([^}]+)\}", pattern)

        if not param_names:
            return {}

        # パターンを正規表現に変換
        regex_pattern = pattern
        for param_name in param_names:
            regex_pattern = regex_pattern.replace(f"{{{param_name}}}", r"([^/]+)")

        # 値を抽出
        match = re.match(f"^{regex_pattern}$", actual_path)
        if match:
            return dict(zip(param_names, match.groups()))

        return {}


def main():
    """Gateway サーバーを起動"""
    gateway = LambdaGateway()

    host = "0.0.0.0"
    port = int(os.environ.get("GATEWAY_PORT", "3000"))

    print("=" * 50)
    print("Sapimo Lambda Gateway Starting")
    print("=" * 50)
    print(f"Listening on {host}:{port}")
    print(f"Lambda routes: {len(gateway.lambda_routes)}")

    uvicorn.run(gateway.app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
