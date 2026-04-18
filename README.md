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

## コンテナ型 Lambda（PackageType: Image）のサポート

`PackageType: Image` で定義されたコンテナ型 Lambda は、以下の **前提条件を満たす場合のみ** sapimo で動作します。

### 前提条件

| 条件 | 内容 |
|------|------|
| **Python バージョン** | Lambda コードが sapimo 本体と同じ Python バージョンで実行可能なこと。Dockerfile の `FROM public.ecr.aws/lambda/python:X.Y` のバージョンと、sapimo を動かす Python バージョンが一致していることを推奨します |
| **純粋 Python 依存** | `pip install` で賄えるパッケージのみに依存していること。`apt-get install` 等でインストールするシステムレベルのライブラリ（libpq-dev、libssl-dev 等）には対応していません |
| **シングルステージビルド** | マルチステージビルドには対応していません |

### 動作の仕組み

sapimo はコンテナをビルド・起動する代わりに、以下の方法でコンテナ型 Lambda を実行します。

1. ZIP 型 Lambda と同様に `LocalLambdaRunner` で **in-process 実行**します
2. Dockerfile の `RUN pip install ...` を解析し、専用の仮想環境（`api_mock/.lambda_venvs/{関数名}/`）を自動作成します
3. ZIP 型 Lambda と同じ moto モックが透過的に機能します

### 制限事項

- moto AWS モックは同一プロセスでのみ動作します。前提条件を逸脱したコンテナ型 Lambda には対応していません
- Dockerfile に記述されたコードのビルドステップ（`RUN pip compile` 等）は再現されません
- Lambda に `CMD` が設定されていない場合、ハンドラは `app.lambda_handler` と仮定されます。実際のハンドラが異なる場合は `api_mock/config.yaml` を手動で修正してください

## 詳細ドキュメント

1. [Docker セットアップ](docs/Docker-Setup.md)
2. [Docker アーキテクチャ](docs/Docker-Architecture.md)
3. [LLM 向け実装ガイド](docs/LLM-Guide.md)