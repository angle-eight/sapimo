# Sapimo

Sapimo は、AWS を使うアプリケーションをローカルで再現するための Python ライブラリです。
SAM/CDK 由来の定義を入力に、`api_mock/config.yaml` を中心としたローカル実行環境を生成します。

## プロジェクトの性質

1. 配布形態はライブラリ（`pip install sapimo` 前提）です
2. 実行形態は単一コンテナです（`sapimo` サービスのみ）
3. AWS モックは同一プロセスで動作します（moto + mock_aws）
4. 失敗時の方針は fail-fast です（不整合をフォールバックで隠蔽しない）

## サポートサービス

1. S3
2. DynamoDB
3. API Gateway
4. Lambda
5. SNS
6. SQS
7. SES

## クイックスタート

```bash
pip install sapimo

# template.yaml または cdk.out/*.template.json から設定と runtime 資産を生成
sapimo init

# 任意: モック実装ひな形を生成
sapimo generate

# 単一コンテナ起動
sapimo start
```

起動後は `http://localhost:8000` にアクセスします。

## 重要な運用ルール

1. `sapimo` 本体を更新したら必ず `sapimo init` を再実行してください
2. `api_mock/docker/` は runtime 資産です。無い状態は異常として扱います
3. 複数リポジトリで同時利用しても衝突しないよう、Compose project 名は内部で一意化されます

## 詳細ドキュメント

1. [Docker セットアップ](docs/Docker-Setup.md)
2. [Docker アーキテクチャ](docs/Docker-Architecture.md)
3. [LLM 向け実装ガイド](docs/LLM-Guide.md)