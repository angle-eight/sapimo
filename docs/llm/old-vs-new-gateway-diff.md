# 旧コード vs 新コード（Docker Gateway）仕様差分一覧

旧コード（`src/sapimo/mock/executer/` + `mediator_route.py`）と新コード（`src/sapimo/docker/templates/gateway/main.py` + 関連ファイル）の間で確認された仕様差分を網羅的にまとめる。

---

## 1. Lambda Event の構築

### 1-1. Event format（v1 / v2）の扱い

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| Event format | `EventType.APIGW`（v1）と `EventType.APIGW_V2`（v2）を config の `EventType` 設定で切り替え | **常に v2 形式のみ** 生成（`version: "2.0"` 固定） |
| Config 参照 | `EventType` enum をチェックして `ApiInfo` or `ApiV2Info` を使い分け | `EventType` を参照する箇所なし |
| 影響 | v1 形式を期待する Lambda（`event["httpMethod"]`, `event["resource"]` 等を使うコード）が動かない | |

**対策**: config の `EventType` が `APIGW`（v1）の場合に v1 形式の event を生成するロジックが必要。

### 1-2. v1 Event に含まれるが v2 Event に含まれないフィールド

旧コードの v1 event（`ApiInfo.to_event`）には以下のフィールドがあるが、新コードでは生成されない：

- `httpMethod`（トップレベル）
- `resource`
- `multiValueHeaders`
- `multiValueQueryStringParameters`
- `path`（トップレベル、v2 は `rawPath`）
- `requestContext.httpMethod`
- `requestContext.identity`（sourceIp, userAgent 等）
- `requestContext.resourceId`, `requestContext.resourcePath`

### 1-3. v2 Event フィールドの差異

| フィールド | 旧コード（ApiV2Info） | 新コード |
|-----------|---------------------|---------|
| `routeKey` | `"GET /actual/path"` | `"GET /template/{param}"` （テンプレートパス使用） |
| `rawPath` | `request.url._url`（フルURL） | `"/{path}"` (正しいパスのみ) |
| `rawQueryString` | `url.split("?")[-1]` | `str(request.url.query)` |
| `cookies` | `["k=v", ...]` 配列 | **なし** |
| `requestContext.stage` | `"Prod"` | `"prod"` （小文字） |
| `requestContext.time` | 動的生成（現在時刻） | `"01/Jan/2025:00:00:00 +0000"` **ハードコード** |
| `requestContext.timeEpoch` | 動的生成（現在epoch） | `1704067200` **ハードコード** |
| `requestContext.requestId` | UUID v4 動的生成 | `"mock-request-id"` **ハードコード** |
| `requestContext.domainName` | `request.url.netloc` | `"localhost"` **ハードコード** |
| `requestContext.authentication` | clientCert 情報あり | **なし** |

### 1-4. Headers の正規化

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| ヘッダキー正規化 | `Content-Type` 形式に Capitalize | **正規化なし**（生の小文字 `content-type` をそのまま渡す） |
| multiValueHeaders | 生成あり | 生成なし |

---

## 2. Lambda Authorizer

### 2-1. ~~Custom Lambda Authorizer の実行~~ <-- 修正済み

| 項目 | 旧コード | 新コード |
|------|---------|----------|
| `AuthType.CUSTOM` | Lambda authorizer を実行し `context` を event に注入 | ~~**未実装**~~ **修正済み**: `_build_authorizer_context` で REQUEST authorizer を実行 |
| `AuthType.CUSTOM_TOKEN` | Token authorizer を実行し `context` を event に注入 | ~~**未実装**~~ **修正済み**: `_build_authorizer_context` で TOKEN authorizer を実行 |
| `AuthType.CUSTOM_REQUEST` | プレースホルダ（pass） | **修正済み**: REQUEST authorizer を実行 |

**影響**: Lambda Authorizer を使う API で、authorizer の `context` が `requestContext` に入らない。

### 2-2. JWT / Cognito Authorizer のエラー処理

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| JWT 解析失敗時 | `self.authorizer = None`（authorizer キー自体が event に入らない） | `return None`（同等） |
| 差異 | なし（同等の挙動） | |

---

## 3. Lambda レスポンスのハンドリング

### 3-1. ~~レスポンス body の二重エンコード~~ <-- 修正済み

前回修正で `_build_lambda_response()` を追加し解決済み。

### 3-2. Lambda が None を返した場合

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| `lambda_res is None` | `status=500`, `body="No response from lambda"` を返す | `lambda_result.get("statusCode", 200)` → **200 を返してしまう** |
| 影響 | 旧コードはエラーとして扱うが、新コードは正常扱い | |

**対策**: `LocalLambdaRunner.execute()` は `None` を `{"statusCode": 200, "body": result}` に変換するのでトップレベルで None が来ることはないが、コンテナ呼び出しパスでは `response.json()` が dict でない可能性がある。

### 3-3. エラーハンドリングの粒度

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| `ModuleNotFoundError` | 専用エラーメッセージ（CodeUri/Layers/import を案内） | 汎用 500 エラー |
| `EventConvertError` | 400 Bad Request | ハンドリングなし |
| `LambdaInvokeError` | 500 + 詳細メッセージ | ハンドリングなし |
| 一般 Exception | 500 | HTTPException(500) |

**影響**: 新コードではデバッグ情報が少なく、エンドユーザーが原因を特定しにくい。

---

## 4. Lambda 実行環境

### 4-1. 環境変数の扱い

| 項目 | 旧コード | 新コード（LocalLambdaRunner） |
|------|---------|------|
| 方式 | 全環境変数をクリアしてダミー値で丸ごと置換 | `_temporary_environ` で一時的に上書き、実行後にリストア |
| Lambda 標準環境変数 | `AWS_LAMBDA_FUNCTION_VERSION`, `AWS_SAM_LOCAL` 等を設定 | 設定しない |
| OS からの環境変数 | すべて消える | 保持される |

新コードの方が安全（リストア処理がある）だが、Lambda 標準環境変数を期待するコードは動かない可能性がある。

### 4-2. Layer の扱い

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| sys.path への追加 | `sys.path.append(layer)` | `sys.path.insert(0, path)` + `layer/python` も追加 |
| クリーンアップ | `sys.path.pop()` で末尾から除去 | `_temporary_syspath` context manager でリストア |
| `/opt/python` パス | 追加なし | `layer_path / "python"` を追加 |

### 4-3. モジュールリロード

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| モジュールキャッシュ | `importlib.import_module` のキャッシュに依存（2回目以降は古いコード） | `sys.modules.pop(module_name, None)` で強制リロード |
| Lambda コード更新反映 | 再起動が必要 | ホットリロード可能 |

### 4-4. ハンドラ実行方式

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| 実行方法 | `eval("app." + props.func)(event, None)` | `getattr(module, func_name)(event, None)` |
| async 対応 | なし（同期実行のみ） | `inspect.isawaitable` で async handler 対応 |
| 並行実行制御 | なし | `asyncio.Lock` で直列化 |

---

## 5. Mock 判定・ルーティングフロー

### 5-1. Mock / Lambda 切り替えロジック

| 項目 | 旧コード（MediatorRoute） | 新コード（LambdaGateway） |
|------|---------|---------|
| 判定方法 | Mock 関数の戻り値を HTTP レスポンスとしてデコード。body が空 or `"null"` → Lambda | MockHandler が Mock 関数を実行。結果の **型** で判定 |
| None 判定 | body が `""` or `"null"` → Lambda 実行 | `mock_result is None` → Lambda 実行 |
| int 判定 | body が 3桁の数字文字列 → `{"message": "mock response"}` を返す | `isinstance(mock_result, int)` → OpenAPI example を解決 |
| Example モード | `ReturnMode.Example` でステータスコード指定の example 返却 | Mock 関数が int を返すと OpenAPI example 解決 → ない場合は content=None で返却 |

### 5-2. グローバル動作モード

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| モード切替 | `set_mode(ReturnMode.Lambda)` 等で全体のモードを切り替え | `options.mode` で "api" / "mock" を切り替え |
| Default モード | Mock 関数の結果で動的に判定 | 常に Mock → Lambda のフォールスルーパイプライン |
| Lambda 強制実行 | `ReturnMode.Lambda` 設定 | `options.mode = "api"` + Mock 関数が None を返す |

### 5-3. ~~S3 トリガー実行~~ <-- 修正済み

| 項目 | 旧コード | 新コード |
|------|---------|----------|
| Lambda 実行後の S3 変更検知 | `data_manager.get_change("s3")` で変更を検知し、S3 トリガー Lambda をループ実行 | ~~**未実装**~~ **修正済み**: `_process_s3_triggers` でチェーン実行 |

---

## 6. OpenAPI / Swagger Example

### 6-1. Example 解決の仕組み

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| 定義元 | config.yaml の `responses` セクション内 | 外部ファイル `swagger.yaml` / `openapi.yaml` |
| 解決ロジック | dict を再帰して `"example"` キーを探す | 構造化されたパス: `responses.{status}.content.application/json.example` / `.examples.*.value` |
| フォールバック | ステータスコードの先頭桁でグループ化（200番台→ 300番台→ …） | 完全一致 → 2xx フォールバック |

---

## 7. その他

### 7-1. CORS

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| CORS | 設定なし（FastAPI デフォルト） | `CORSMiddleware` で全オリジン許可 |

### 7-2. catch-all フォールバック

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| 未登録パス | FastAPI のデフォルト 404 | `/{path:path}` catch-all でパスパターンマッチング後、見つからなければ 404 / mock mode なら default 応答 |

### 7-3. ヘルスチェック / ルート一覧

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| `/health` | なし | ヘルスチェック endpoint あり |
| `/routes` | なし | ルート一覧 endpoint あり |

### 7-4. Lambda コードのロガー接続

| 項目 | 旧コード | 新コード |
|------|---------|---------|
| Lambda 内 `logger` | `LogManager` でキャプチャして sapimo ログに統合 | `LambdaExecutionLogger` で stdout をキャプチャしてファイル出力 |

---

## まとめ: 修正優先度

| 優先度 | 差分 | 影響 |
|-------|------|------|
| **高** | ~~v1 Event 未対応 (§1-1)~~ **修正済み** | EventType に応じて v1/v2 を切り替え |
| **高** | ~~requestContext 動的フィールド (§1-3)~~ **修正済み** | time/timeEpoch/requestId/domainName を動的生成 |
| **中** | ~~Headers の Capitalize 正規化なし (§1-4)~~ **修正済み** | Content-Type 形式に正規化 |
| **中** | ~~Custom Lambda Authorizer 未実装 (§2-1)~~ **修正済み** | Authorizer Lambda を実行し context を注入 |
| **中** | ~~S3 トリガー Lambda 未実装 (§5-3)~~ **修正済み** | S3 変更検知→トリガー Lambda チェーン実行 |
| **低** | cookies フィールドなし (§1-3) | cookie に依存する Lambda の誤動作 |
| **低** | エラーメッセージの粒度低下 (§3-3) | デバッグ体験が低下 |
| **情報** | stage が `"Prod"` → `"prod"` (§1-3) | stage 文字列に依存するコードの互換性 |
| **情報** | CORS デフォルト全許可 (§7-1) | 本番との挙動差だがローカル開発では問題なし |
