# Sapimo Docker Architecture

## 現在のコンテナ構成

Sapimoは以下の3つのコンテナで構成されています：

### 1. Gateway Container (`sapimo-gateway`)
- **役割**: APIルーティングとLambdaコンテナ調整
- **ポート**: 3000
- **技術**: FastAPI + uvicorn
- **機能**:
  - HTTP リクエストを受信
  - Lambda コンテナへのルーティング
  - AWS Lambda Runtime API 経由での通信
  - API Gateway v2 形式のイベント構築

### 2. AWS Mock Container (`sapimo-aws-mock`)
- **役割**: AWS サービスのローカル模擬
- **ポート**: 4566 (LocalStack互換)
- **技術**: moto + FastAPI
- **機能**:
  - S3, DynamoDB, SQS, SNS, SES の模擬
  - データの永続化 (`./data` ボリューム)
  - Lambda関数からのAWS SDK呼び出し処理

### 3. Lambda Runtime Containers (`lambda-{function-name}`)
- **役割**: Lambda関数の実行環境
- **ポート**: 8080 (内部通信用)
- **技術**: AWS Lambda Python Runtime
- **機能**:
  - 独立したPython実行環境
  - AWS Lambda Runtime API の提供
  - 環境変数とレイヤーの管理

## コンテナ間通信

```
Client Request → Gateway (3000) → Lambda Container (8080)
                     ↓
                 AWS Mock (4566) ← Lambda Function (boto3)
```

- **ネットワーク**: `sapimo-network` (172.20.0.0/16)
- **サービス探索**: Dockerの内部DNS
- **プロトコル**: HTTP/REST (全て)

## Lambda関数の追加方法

1. `api_mock/config.yaml` に関数定義を追加
2. `lambda/{function-name}/` ディレクトリを作成
3. `app.py` (Lambda関数) と `Dockerfile` を配置
4. `docker-compose.yml` にコンテナ定義を追加
5. `docker compose up --build` で起動

## ファイル構成

```
sapimo/
├── docker-compose.yml          # コンテナ編成
├── docker/
│   ├── gateway/
│   │   ├── main.py            # Gateway実装
│   │   └── Dockerfile         # Gateway用
│   └── aws-mock/
│       └── Dockerfile         # AWS Mock用
├── lambda/
│   └── {function-name}/
│       ├── app.py            # Lambda関数
│       └── Dockerfile        # Lambda用
└── api_mock/
    └── config.yaml           # 関数とルーティング定義
```

## 開発フロー

1. **起動**: `docker compose up -d`
2. **テスト**: `curl http://localhost:3000/{path}`
3. **ログ確認**: `docker compose logs {service-name}`
4. **停止**: `docker compose down`

このアーキテクチャにより、各Lambda関数が完全に隔離された環境で実行され、実際のAWSと同様の動作を再現できます。