# Sapimo Docker セットアップガイド

この文書は Docker 利用環境で `sapimo` を単一コンテナで使う手順です。

---

## 1. 前提

1. Docker / Docker Compose が利用可能
2. AWS SAM の `template.yaml` または CDK の CloudFormation 出力がある
3. Python 3.12+

---

## 2. インストール

```bash
pip install sapimo
```

---

## 3. 利用開始

```bash
# template.yaml から api_mock/config.yaml と単一コンテナ用 compose を生成
sapimo init

# api_mock/app.py を自動生成（必要な場合のみ）
sapimo generate

# 起動
sapimo start
```

起動後は `http://localhost:8000` にアクセスします。

> 注意: sapimo を更新した後は、`api_mock/docker/` runtime 資産更新のため `sapimo init` を再実行してください。

### fail-fast 方針

1. `api_mock/docker/` が存在しない状態は異常です
2. `status` / `clean` はこの異常状態で即失敗します
3. フォールバックで隠蔽せず、`sapimo init` で整合を回復してください

### 外部プロジェクト利用時の前提

1. 本ライブラリは `pip install` で他リポジトリから使う前提です
2. そのため `workspace/src` があることは前提にしません
3. ランタイム import は `api_mock/docker/` 配下の同梱資産で完結させます

---

## 4. リポジトリ開発者向け

```bash
git clone <repository>
cd sapimo
python -m sapimo init
python -m sapimo generate
python -m sapimo start
```

---

## 5. 設定ファイル例

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

## 6. トラブルシューティング

### `sapimo` コマンドが見つからない
```bash
python -m sapimo --help
```

### `sapimo init` で設定生成できない
1. `template.yaml` または `cdk.out/*.template.json` の存在を確認
2. 生成先は `api_mock/` 配下

### コンテナが起動しない
```bash
cd api_mock
docker compose logs sapimo
docker compose up --build
```

補足: CLI (`sapimo start`) は Compose project 名を内部で一意化して実行します。
手動で `docker compose` を叩く場合は、別リポジトリの同名 `api_mock` と衝突しないよう注意してください。

### Lambda関数が実行されない
1. `api_mock/config.yaml` の `Properties.CodeUri` と `Properties.Handler` を確認
2. `api_mock/docker-compose.yml` が存在するか確認（`sapimo init` で生成）
3. レイヤー利用時は `Properties.Layers` のパス実在を確認
4. `api_mock/docker/` が存在しない場合は `sapimo init` を再実行
