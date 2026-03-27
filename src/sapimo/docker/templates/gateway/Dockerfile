# FastAPI Gateway Container
# 軽量なAPIルーティングとLambda連携専用
FROM python:3.12-slim

LABEL maintainer="sapimo" \
    description="FastAPI Gateway for Lambda container coordination"

# システム依存関係（最小限）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリ
WORKDIR /workspace

# Gateway専用の依存関係
COPY api_mock/docker/gateway/requirements.txt /workspace/
RUN pip install --no-cache-dir -r requirements.txt

# Gateway アプリケーションファイルをコピー
COPY api_mock/docker/gateway/main.py /workspace/
COPY api_mock/docker/gateway/mock_handler.py /workspace/
COPY api_mock/docker/gateway/openapi_example_resolver.py /workspace/
COPY api_mock/docker/sapimo/ /workspace/sapimo/
COPY api_mock/ /workspace/api_mock/

# ログディレクトリ作成
RUN mkdir -p /workspace/logs /workspace/api_mock/log && \
    chmod -R 755 /workspace/logs /workspace/api_mock/log

# Gateway専用の環境変数
ENV SAPIMO_MODE=gateway \
    PYTHONPATH=/workspace \
    LAMBDA_DISCOVERY_NETWORK=sapimo-network \
    AWS_MOCK_ENDPOINT=http://sapimo-aws-mock:4566 \
    LOG_DIR=/workspace/logs

# ヘルスチェック
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

# ポート公開
EXPOSE 3000

# Gateway起動コマンド
CMD ["python", "/workspace/main.py"]
