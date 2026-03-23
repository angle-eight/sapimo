# これは何？
AWS SAM/CDK の設定をもとに、Docker 上で API Gateway + Lambda + AWS モックをローカル実行するツールです。
`sam local start-api` の代替として、フロント連携を含む開発時のAPI確認を行う用途を想定しています。

サポート対象（モック）:
- API Gateway / Lambda
- S3 / DynamoDB / SQS / SNS / SES

---

# クイックスタート

## 前提
- Docker / Docker Compose が利用可能
- プロジェクトルートに `template.yaml`（または CDK の CloudFormation 出力）がある

## 起動手順
```bash
# 1) template から api_mock/config.yaml を生成
sapimo init

# 2) api_mock/app.py にモック雛形を生成（必要な場合のみ）
sapimo generate

# 3) Docker で起動
sapimo start
```

デフォルトで `http://localhost:3000` で待ち受けます。

---

# APIモックの編集

`api_mock/app.py` を編集することで、Lambdaコードを書き換えずにAPI挙動を制御できます。

```python
from sapimo.mock import api, change_input, options

@api.get("/hello/{date}")
async def hello_get_mock(date: int):
   return None
```

## 1) Lambdaを実行する（デフォルト）
`return None` を返すと、紐づいたLambdaを実行します。

## 2) スタブを返す
```python
@api.get("/hello/{date}")
async def hello_get_mock(date: int):
   return {"message": "hello mock"}
```

## 3) 入力をすり替えてLambdaを実行する
```python
@api.get("/hello/{date}")
async def hello_get_mock(date: int):
   return change_input(date=3)
```

明示的に指定する場合:
```python
return change_input(pathParameters={"date": "3"})
```

## 4) OpenAPI exampleを返す
```python
@api.get("/hello/{date}")
async def hello_get_mock(date: int):
   return 200
```

`api_mock/swagger.yaml` または `api_mock/openapi.yaml` が存在する場合、
該当パス/ステータスの example を返します。見つからない場合はステータスのみ返します。

## 5) 全体モードを切り替える
```python
options.set_api_mode()        # 通常モード
options.set_mock_mode(200)    # 全体Mockモード（デフォルト200）
options.set_mock_mode(400)    # 全体Mockモード（デフォルト400）
```

旧API互換:
```python
options.set("api")
options.set("mock", status=400)
```

---

# データの扱い

- S3/DynamoDB 等のモックデータは `data/` 配下に保持されます。
- コンテナ再起動後もデータを再利用できます（ボリューム設定による）。

---

# 旧記法との対応表（移行ガイド）

| 旧仕様 | 新仕様 |
|---|---|
| `lambda_mock.get(...)` | `api.get(...)` |
| `sapimock init` | `sapimo init` |
| `sapimock run` | `sapimo start` |
| `options.set(mode.api)` | `options.set_api_mode()` |
| `options.set(mode.mock, status=400)` | `options.set_mock_mode(400)` |
| `config.yaml: handler: "dir.file.func"` | `config.yaml: Properties.Handler / CodeUri` |

---

# 補足ドキュメント

- Dockerセットアップ: `docs/Docker-Setup.md`
- Dockerアーキテクチャ: `docs/Docker-Architecture.md`
- 開発者向けの実装計画・検証記録: `docs/Container-Feature-Recovery-Plan.md`