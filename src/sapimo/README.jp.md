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

デフォルトで `http://localhost:8000` で待ち受けます。

> 注意: `sapimo` 本体を更新した場合（`pip install -U sapimo` や `pip install -e .` 後）は、
> `api_mock/docker/` に展開される実行テンプレートを最新化するため **`sapimo init` を再実行** してください。

---

# Swagger APIドキュメント

起動後、ブラウザで以下のURLにアクセスすると、登録済みの全エンドポイントをSwagger UIで確認できます。

```
http://localhost:8000/docs
```

- `config.yaml` に定義されたルートが自動で一覧表示されます
- パスの先頭セグメント（例: `/users/{id}` → `users`）でタグ別にグルーピングされます
- **Try it out** ボタンから直接リクエストを送信してテストできます

OpenAPI JSON は `http://localhost:8000/openapi.json` で取得できます。

## リクエスト/レスポンス型の表示

`api_mock/swagger.yaml` または `api_mock/openapi.yaml` が存在する場合、
定義されている requestBody / responses / schemas が Swagger UI に自動マージされます。

```yaml
# api_mock/swagger.yaml の例
paths:
  /users:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
      responses:
        '200':
          description: User created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserResponse'
components:
  schemas:
    CreateUserRequest:
      type: object
      required: [name, email]
      properties:
        name:
          type: string
        email:
          type: string
          format: email
    UserResponse:
      type: object
      properties:
        id:
          type: string
        name:
          type: string
```

> この OpenAPI spec は [example 返却機能](#4-openapi-exampleを返す) と共用です。
> 一つのファイルで型情報と example の両方を管理できます。

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

# Lambda内の関数を差し替える（monkeypatch）

`monkeypatch.setattr` を使うと、Lambda実行時に特定の関数を差し替えることができます。
motoでサポートされていないAWSサービス（Bedrock等）の呼び出しを差し替える場合に便利です。

パッチはLambda実行中のみ有効で、実行後は自動的に元に戻ります。

```python
from sapimo.mock import api, monkeypatch
import io

# デコレータ形式: Bedrock の invoke_model を差し替え
@monkeypatch.setattr("app.bedrock_client.invoke_model")
def mock_invoke_model(**kwargs):
    import json
    return {
        "body": io.BytesIO(json.dumps({
            "content": [{"text": "これはモックレスポンスです"}]
        }).encode()),
        "contentType": "application/json",
    }

# Lambda は通常通り実行。invoke_model の呼び出しだけが差し替わる
@api.post("/chat")
async def chat():
    pass
```

命令形式（pytest風）も使えます:
```python
monkeypatch.setattr("app.bedrock_client.invoke_model",
    lambda **kwargs: {"body": io.BytesIO(b'{"content": [{"text": "mock"}]}'), "contentType": "application/json"})
```

**注意**: target はモジュール内で名前が**使われている場所**を指定します（`unittest.mock.patch` と同じルール）。

例えば Lambda コードが以下の場合:
```python
# lambda/chat/app.py
import boto3
bedrock_client = boto3.client("bedrock-runtime")

def lambda_handler(event, context):
    response = bedrock_client.invoke_model(modelId="anthropic.claude-v2", body=...)
```

target は `app.bedrock_client.invoke_model` になります。

---

# FastAPI アプリモード

バックエンドを FastAPI で実装している場合（Lambda を使わない、または Lambda と併用する）、
`--app` オプションを使うことで AWS モックを共有しながらローカル開発環境を構築できます。

## FastAPI のみモード

```bash
sapimo init --app myapp.main:app
sapimo start
```

全リクエストがユーザーの FastAPI アプリへ転送されます。

## ハイブリッドモード（Lambda + FastAPI）

```bash
sapimo init --template template.yaml --app myapp.main:app
sapimo start
```

`paths` に定義されたルートは Lambda が処理し、それ以外のリクエストはユーザーの FastAPI アプリへ転送されます。
Lambda もユーザーアプリも同一プロセス内で動くため、moto の AWS モックは両者で共有されます。

## 制約事項

- **`api_mock/app.py` へのミドルウェア追加禁止**: `api_mock/app.py` はモックレスポンス定義専用ファイルです。`app.add_middleware(...)` 等のミドルウェア追加は行わないでください。動作を保証しません
- **ユーザーアプリの lifespan**: `@asynccontextmanager lifespan` を持つユーザーアプリの lifespan イベントは、sapimo 経由では自動的に呼ばれません。lifespan に依存する初期化処理はモジュールロード時に行うか、lifespan なしの設計を推奨します

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