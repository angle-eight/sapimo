# Mock システム

Sapimo の Mock システムは 2 層構造です:
1. **Mock Router** — API レスポンスの Mock（ユーザーが `app.py` で定義）
2. **AWS Mock** — S3, DynamoDB 等の AWS サービスモック（moto ベース、自動管理）

---

## 1. Mock Router（api_mock/app.py）

### 概要

ユーザーは `api_mock/app.py` にデコレータを使って Mock 関数を定義します。
Gateway の `MockHandler` が 1 秒間隔で `app.py` の変更を検知し、動的にリロードします。

### Mock 関数の戻り値による分岐

| 戻り値 | 動作 |
|--------|------|
| `None` / `pass` | 実際の Lambda を実行する（Mock をスキップ） |
| `dict` / `list` / `str` | そのまま JSON レスポンスとして返す |
| `int` (200-599) | OpenAPI 定義から example を検索して返す。見つからない場合はステータスコードのみ返却 |
| `change_input(...)` | 指定パラメータで event を部分上書きして Lambda を実行 |

### コード例

```python
from sapimo.mock import api, change_input

# Mock データを直接返す
@api.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id, "name": "テストユーザー"}

# Lambda を実行する（pass = None を返す）
@api.post("/items")
async def create_item():
    pass

# Lambda の入力を変更して実行する
@api.get("/search")
async def search():
    return change_input(query="overridden_value")

# OpenAPI example を返す
@api.get("/products")
async def products():
    return 200  # swagger.yaml / openapi.yaml の example を返却
```

### パスパラメータの型変換

Mock 関数のシグネチャにある型アノテーションは自動的に変換されます:

```python
@api.get("/users/{user_id}")
async def get_user(user_id: int):  # ← str "123" → int 123 に自動変換
    ...
```

対応型: `int`, `float`, `bool`

### `change_input` の仕組み

`change_input(**kwargs)` は `InputOverride` を返し、Gateway が以下のロジックで event を上書きします:

1. キーが `pathParameters`, `queryStringParameters`, `headers`, `body` の場合 → 該当フィールドを直接更新
2. キーがルートパスの `{param}` に一致する場合 → `pathParameters` に設定
3. それ以外 → `queryStringParameters` に設定

### グローバルオプション（options）

```python
from sapimo.mock import options

# Mock 優先モード: Mock 定義がないパスにもデフォルトレスポンスを返す
options.set_mock_mode(status=200)

# API モード（デフォルト）: Mock 未定義のパスは Lambda 実行
options.set_api_mode()
```

### OpenAPI Example 返却

Mock 関数がステータスコード（int）を返した場合、`openapi_example_resolver.py` が以下の順序で example を検索:

1. `api_mock/swagger.yaml` or `api_mock/openapi.yaml` を読み込み
2. 指定パス・メソッド・ステータスコードに完全一致する response を検索
3. 見つからない場合は 2xx / default にフォールバック
4. `content.application/json.example` → `content.application/json.examples.*.value` の順で値を取得

---

## 2. AWS Mock（moto ベース）

### 概要

moto の `mock_aws` を使い、S3・DynamoDB 等の AWS API を同一プロセスでモック。
Lambda コードから `boto3.client("s3")` を呼ぶと、moto が透過的にモック応答します。

### ライフサイクル

```
MockManager.__init__(config_file)
  → ConfigParser で config.yaml を読み込み
  → 各サービスの AwsMock サブクラスをインスタンス化

MockManager.start()
  → 各モックの mock_aws().start() を呼び出し
  → moto のモックが有効化される

MockManager.init_data()
  → 各モックの init_data() を呼び出し
  → ローカルファイルからデータを投入

MockManager.sync()
  → 各モックの sync() を呼び出し
  → moto 上のデータをローカルファイルに書き戻し

MockManager.stop()
  → 各モックの mock_aws().stop() を呼び出し
```

### サービス別の詳細

#### S3Mock

- **初期化**: `api_mock/s3/<バケット名>/` 配下のファイルを moto S3 にアップロード
- **同期**: moto S3 のオブジェクトを `api_mock/s3/<バケット名>/` にダウンロード。MD5 ハッシュで変更検知
- **変更検知**: `sync()` が `{"updated": {bucket: [keys]}, "deleted": {bucket: [keys]}}` を返す

#### DynamoMock

- **初期化**: `api_mock/dynamodb/<テーブル名>/data.json` からアイテムを投入。`results.csv`（AWS DynamoDB エクスポート形式）も対応
- **同期**: テーブルの全アイテムを `data.json` に書き出し
- **CSV 形式**: AWS DynamoDB の結果 CSV（`{S:value}` 形式）を解釈

#### SqsMock

- **初期化**: `api_mock/sqs/<キュー名>/` 配下のテキストファイルをメッセージとして送信
- **同期**: キュー内のメッセージを `0000.txt`, `0001.txt`, ... としてファイル化

#### SnsMock / SesMock

- moto の mock_aws は有効化されるが、ローカルファイルとの同期は未実装
- Lambda コードからの SNS/SES API 呼び出しは moto が応答する

---

## 3. Gateway 内でのリクエスト処理フロー

```
HTTP Request → unified_router
  │
  ├─ MockHandler.has_mock_definition(method, path)?
  │   ├─ Yes → MockHandler.handle_mock_request()
  │   │   ├─ result is None → Lambda 実行へ
  │   │   ├─ result is InputOverride → _invoke_lambda_with_override()
  │   │   ├─ result is dict/list/str → JSONResponse で返却
  │   │   └─ result is int → OpenAPI example 検索 → 返却
  │   │
  │   └─ No → options.mode == "mock"?
  │       ├─ Yes → デフォルトモックレスポンス (status=options.default_status)
  │       └─ No → Lambda 実行へ
  │
  └─ Lambda 実行
      ├─ _find_matching_route() でルート検索
      ├─ _build_lambda_event() で API Gateway v2 形式の event を構築
      ├─ _build_authorizer_context() で認証コンテキストを追加
      │   ├─ JWT/COGNITO: Authorization ヘッダからトークンを取得しクレームを注入（検証なし）
      │   └─ AWS_IAM: ダミーの IAM コンテキストを注入
      ├─ LocalLambdaRunner.execute() でインプロセス実行
      └─ MockManager.sync() でデータ同期
```

### パスマッチング

1. 完全一致（`GET:/users/123` と `GET:/users/123`）を優先
2. パターンマッチ（`GET:/users/{user_id}` と `GET:/users/123`）をフォールバック
3. パスパラメータは正規表現 `[^/]+` で抽出

---

## 4. データファイルの構造

```
api_mock/
├── s3/
│   └── <バケット名>/
│       └── (S3 オブジェクトのミラー。ディレクトリ構造がキーに対応)
├── dynamodb/
│   └── <テーブル名>/
│       ├── data.json      ← アイテムの JSON 配列
│       └── results.csv    ← AWS DynamoDB エクスポート形式（代替入力）
├── sqs/
│   └── <キュー名>/
│       ├── 0000.txt       ← メッセージ本文
│       ├── 0001.txt
│       └── ...
├── sns/                   ← (将来対応用。現在は空)
└── ses/                   ← (将来対応用。現在は空)
```

これらのファイルは以下の 2 つの目的で使われます:
1. **入力**: コンテナ起動時に `init_data()` でモックサービスにデータを投入する
2. **出力**: Lambda 実行後に `sync()` でモックサービス内のデータをファイルに書き戻す
