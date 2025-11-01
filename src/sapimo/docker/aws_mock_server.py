#!/usr/bin/env python3
"""
AWS Mock Server
moto ベースのAWSサービスモック専用サーバー
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# プロジェクトパスを追加
sys.path.insert(0, "/workspace")

from sapimo.docker.mock_manager import DockerMockManager
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)

# 設定ファイルパス（デフォルト）
DEFAULT_CONFIG_PATH = Path("/workspace/api_mock/config.yaml")


class AWSMockServer:
    """AWS Mock専用サーバー"""

    def __init__(self, config_path: Path = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.mock_manager = None
        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        """FastAPIアプリケーション作成"""
        app = FastAPI(
            title="Sapimo AWS Mock Server",
            description="AWS Services Mock using moto",
            version="1.0.0",
        )

        # CORS設定
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # イベントハンドラー
        app.add_event_handler("startup", self.startup)
        app.add_event_handler("shutdown", self.shutdown)

        # ヘルスチェックエンドポイント
        @app.get("/health")
        async def health_check():
            """ヘルスチェック"""
            if self.mock_manager:
                return {"status": "healthy", "services": "aws-mock"}
            return {"status": "unhealthy"}, 503

        # AWS Mock管理エンドポイント
        @app.get("/aws-mock/status")
        async def mock_status():
            """AWS Mock状態取得"""
            if not self.mock_manager:
                return {"error": "Mock manager not initialized"}, 500

            status = self.mock_manager.get_docker_mock_status()
            return status

        @app.post("/aws-mock/sync")
        async def force_sync():
            """強制同期"""
            if not self.mock_manager:
                return {"error": "Mock manager not initialized"}, 500

            try:
                self.mock_manager.sync()
                return {"status": "synced"}
            except Exception as e:
                logger.error(f"Sync failed: {e}")
                return {"error": str(e)}, 500

        @app.post("/aws-mock/cleanup")
        async def cleanup_data():
            """データ清掃"""
            if not self.mock_manager:
                return {"error": "Mock manager not initialized"}, 500

            try:
                success = self.mock_manager.cleanup_persistent_data()
                if success:
                    return {"status": "cleaned"}
                else:
                    return {"error": "Cleanup failed"}, 500
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")
                return {"error": str(e)}, 500

        # Lambda実行エンドポイント
        @app.post("/lambda/execute")
        async def execute_lambda(request: dict):
            """Lambda実行"""
            try:
                if not self.mock_manager:
                    raise HTTPException(
                        status_code=500, detail="Mock manager not initialized"
                    )

                # リクエスト情報から簡単なレスポンスを生成
                method = request.get("method", "GET")
                path = request.get("path", "/")

                # 基本的なヘルスチェックレスポンス
                if path == "/health" or "health" in path:
                    response_body = {
                        "status": "ok",
                        "message": "Health check successful",
                        "timestamp": datetime.now().isoformat(),
                    }
                else:
                    response_body = {
                        "message": f"Mock Lambda response for {method} {path}",
                        "request": request,
                        "timestamp": datetime.now().isoformat(),
                    }

                return {
                    "statusCode": 200,
                    "body": json.dumps(response_body),
                    "headers": {"Content-Type": "application/json"},
                }

            except Exception as e:
                logger.error(f"Lambda execution failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        # moto互換エンドポイント
        @app.get("/")
        async def moto_root():
            """moto互換ルートエンドポイント"""
            return {"message": "Sapimo AWS Mock Server", "version": "1.0.0"}

        return app

    async def startup(self):
        """サーバー起動時処理"""
        logger.info("Starting AWS Mock Server...")

        try:
            # 設定ファイル確認
            if not self.config_path.exists():
                logger.warning(f"Config file not found: {self.config_path}")
                # デフォルト設定作成
                await self._create_default_config()

            # MockManager初期化
            self.mock_manager = DockerMockManager(self.config_path)
            self.mock_manager.start()

            logger.info("AWS Mock Server started successfully")

        except Exception as e:
            logger.error(f"Failed to start AWS Mock Server: {e}")
            raise

    async def shutdown(self):
        """サーバー終了時処理"""
        logger.info("Shutting down AWS Mock Server...")

        if self.mock_manager:
            try:
                self.mock_manager.sync()
                self.mock_manager.stop()
                logger.info("AWS Mock Server shutdown complete")
            except Exception as e:
                logger.error(f"Error during shutdown: {e}")

    async def _create_default_config(self):
        """デフォルト設定ファイル作成"""
        default_config = {
            "s3": {"default-bucket": {"BucketName": "default-bucket"}},
            "dynamodb": {
                "default-table": {
                    "TableName": "default-table",
                    "AttributeDefinitions": [
                        {"AttributeName": "id", "AttributeType": "S"}
                    ],
                    "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
                    "BillingMode": "PAY_PER_REQUEST",
                }
            },
        }

        # ディレクトリ作成
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        # YAML形式で保存
        import yaml

        with open(self.config_path, "w") as f:
            yaml.dump(default_config, f, default_flow_style=False)

        logger.info(f"Created default config: {self.config_path}")


async def main():
    """メイン関数"""
    # 環境変数から設定取得
    host = os.getenv("AWS_MOCK_HOST", "0.0.0.0")
    port = int(os.getenv("AWS_MOCK_PORT", "4566"))
    config_path = os.getenv("CONFIG_PATH")

    if config_path:
        config_path = Path(config_path)

    # サーバー作成
    server = AWSMockServer(config_path)

    # uvicornサーバー起動
    config = uvicorn.Config(
        server.app, host=host, port=port, log_level="info", access_log=True
    )

    server_instance = uvicorn.Server(config)
    await server_instance.serve()


if __name__ == "__main__":
    asyncio.run(main())
