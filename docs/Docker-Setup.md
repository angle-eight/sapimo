# Sapimo Docker セットアップガイド

この文書は、**Docker がインストール済みの環境で `sapimo` を使い始めるユーザー向け**の手順です。

---

## 1. 通常利用（推奨）

### 前提
- Docker / Docker Compose が利用可能
- AWS SAM の `template.yaml` または CDK の CloudFormation 出力がある
- Python 3.12+

### インストール
```bash
pip install sapimo
```

### 利用開始
```bash
# template.yaml から api_mock/config.yaml と api_mock/docker-compose.yml を生成
sapimo init

# api_mock/app.py を自動生成（必要な場合のみ）
sapimo generate

# 起動
sapimo start
```

起動後は `http://localhost:3000` にアクセスします。

---

## 2. リポジトリ開発者向け（補足）

`pip install` ではなくリポジトリを直接触る場合は次の手順です。

```bash
git clone <repository>
cd sapimo
python -m sapimo init
python -m sapimo generate
python -m sapimo start
```

---

## 3. 設定ファイル例

### `api_mock/config.yaml`
```yaml
paths:
  "/test":
    get:
      Properties:
        CodeUri: lambda/test/
        Handler: app.lambda_handler
        Runtime: python3.12
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

---

## 4. トラブルシューティング

### `sapimo` コマンドが見つからない
```bash
python -m sapimo --help
```

### `sapimo init` で設定生成できない
- `template.yaml` または `cdk.out/*.template.json` が存在するか確認
- 生成先は `api_mock/` 配下

### コンテナが起動しない
```bash
cd api_mock
docker compose logs sapimo-gateway
docker compose logs sapimo-aws-mock
docker compose up --build
```

### Lambda関数が実行されない
- `api_mock/config.yaml` の `Properties.CodeUri` と `Properties.Handler` を確認
- `api_mock/docker-compose.yml` が存在するか確認（`sapimo init` で生成）
