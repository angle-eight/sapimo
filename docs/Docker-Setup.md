# Sapimo Docker セットアップガイド

## クイックスタート

```bash
# リポジトリをクローン
git clone <repository>
cd sapimo

# コンテナを起動
docker compose up -d

# テスト実行
curl http://localhost:3000/test
```

## 設定ファイル例

### `api_mock/config.yaml`
```yaml
paths:
  "/test":
    get:
      Properties:
        CodeUri: lambda/test/
        Handler: app.lambda_handler
        Runtime: python3.9
        Environment:
          Variables:
            BUCKET_NAME: test-bucket
            TABLE_NAME: test-table

s3:
  test-bucket:
    BucketName: test-bucket

dynamodb:
  test-table:
    TableName: test-table
    AttributeDefinitions:
    - AttributeName: id
      AttributeType: S
    KeySchema:
    - AttributeName: id
      KeyType: HASH
    BillingMode: PAY_PER_REQUEST
```

### `lambda/test/app.py`
```python
import json
import os

def lambda_handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "message": "Hello from Lambda!",
            "environment": {
                "bucket_name": os.environ.get('BUCKET_NAME'),
                "table_name": os.environ.get('TABLE_NAME')
            }
        })
    }
```

### `lambda/test/Dockerfile`
```dockerfile
FROM public.ecr.aws/lambda/python:3.9
COPY app.py ${LAMBDA_TASK_ROOT}
CMD ["app.lambda_handler"]
```

## トラブルシューティング

### コンテナが起動しない
```bash
# ログを確認
docker compose logs sapimo-gateway
docker compose logs sapimo-aws-mock

# 再ビルド
docker compose down
docker compose up --build
```

### Lambda関数が実行されない
- `api_mock/config.yaml` のパス設定を確認
- Lambda コンテナが正常に起動しているか確認
- Gateway のルーティングログを確認

### AWS Mock に接続できない
- ポート 4566 が利用可能か確認
- コンテナ間のネットワーク設定を確認