# AWS Mock Container
# moto ベースのAWSサービスモック専用コンテナ
FROM python:3.12-slim

LABEL maintainer="sapimo" \
    description="AWS Services Mock Container using moto"

# システム依存関係（最小限）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリ
WORKDIR /workspace

# AWS Mock専用の依存関係
COPY api_mock/docker/aws_mock/requirements.txt /workspace/
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションコード
COPY api_mock/docker/aws_mock/aws_mock_server.py /workspace/
COPY api_mock/docker/sapimo/ /workspace/sapimo/
COPY api_mock/ /workspace/api_mock/

# データ永続化用ディレクトリ
RUN mkdir -p /data /persistent-data /workspace/logs && \
    chmod -R 755 /workspace/logs /data /persistent-data

# AWS Mock環境変数
ENV SAPIMO_MODE=aws-mock \
    PYTHONPATH=/workspace \
    AWS_DEFAULT_REGION=us-east-1 \
    AWS_ACCESS_KEY_ID=testing \
    AWS_SECRET_ACCESS_KEY=testing \
    MOTO_HOST=0.0.0.0 \
    MOTO_PORT=4566 \
    DATA_PATH=/data \
    LOG_DIR=/workspace/logs

# ヘルスチェック
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:4566/health || exit 1

# ポート公開
EXPOSE 4566

# AWS Mock起動コマンド
CMD ["python", "/workspace/aws_mock_server.py"]
