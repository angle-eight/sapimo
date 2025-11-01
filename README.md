# Sapimo - AWS Local Development Environment

本プロジェクトはAWSのクラウドサービスを利用したアプリケーションをローカル環境で動作させるためのPythonライブラリです。

## 概要

moto及びFastAPIを利用し、AWS SAMやCDKが生成する設定ファイルを読み込み、ローカル環境内に擬似的なAWS環境を構築します。

### サポートサービス
- **S3** - オブジェクトストレージ
- **DynamoDB** - NoSQLデータベース
- **API Gateway** - RESTful API
- **Lambda** - サーバーレス関数
- **SNS** - 通知サービス
- **SQS** - メッセージキュー
- **SES** - メール送信

## Docker版クイックスタート

```bash
# コンテナを起動
docker compose up -d

# Lambda関数をテスト
curl http://localhost:3000/test

# AWS Mock APIを確認
curl http://localhost:4566
```

詳細は [Docker セットアップガイド](docs/Docker-Setup.md) を参照してください。

## アーキテクチャ

- **Gateway Container** (port 3000): APIルーティング
- **AWS Mock Container** (port 4566): AWS サービス模擬
- **Lambda Runtime Containers**: 各Lambda関数の実行環境

詳細は [Docker アーキテクチャ](docs/Docker-Architecture.md) を参照してください。