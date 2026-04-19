# FastAPI アプリモード 実装計画

## 背景・目的

現在の sapimo は「Lambda 関数を FastAPI gateway 経由でインプロセス実行する」ツールである。
本機能追加では、以下のユースケースをサポートする：

> バックエンドを FastAPI で実装しており（Lambda を使わない、または Lambda と併用する）、
> ストレージ・DB に S3/DynamoDB/Cognito 等の AWS リソースを使っているアプリのローカル開発環境を sapimo で構築する

---

## 動作モード定義

| モード | config.yaml の内容 | 動作 |
|--------|-------------------|------|
| **Lambda モード**（デフォルト） | `paths` のみ | 既存の挙動。変更なし。 |
| **FastAPI アプリモード** | `app_module` のみ、または `paths` + `app_module` | Gateway が起動。Lambda ルートは `paths` に従い実行。Lambda ルートに一致しないリクエストはユーザーの FastAPI アプリへ転送。 |

FastAPI のみモード（`paths` なし）とハイブリッドモード（`paths` + `app_module`）は**同一の実装**で賄う。
`paths` が 0 件の場合、全リクエストがユーザーアプリに転送されるため動作上の差異はない。

---

## アーキテクチャ

### Lambda モード（既存、変更なし）

```
Client → Gateway/main.py → MockHandler (app.py 監視) → LocalLambdaRunner → lambda_handler()
                                                               ↑
                                                        moto mock_aws（同一プロセス）
```

### FastAPI アプリモード（新規）

```
Client → Gateway/main.py → [paths に一致] → LocalLambdaRunner → lambda_handler()
                         ↓                        ↑
                   [一致なし]             moto mock_aws（同一プロセス、両者で共有）
                         ↓
               httpx.ASGITransport → ユーザーの FastAPI app（インプロセス転送）
```

**Gateway/main.py はそのまま使う**。別エントリーポイントや別テンプレートは不要。
`app_module` が設定されている場合のみ、Gateway の fallback ハンドラがユーザーアプリへ転送する挙動に変わる。

Lambda もユーザーアプリも同一プロセス内で動くため、moto の `mock_aws` パッチは両者に等しく適用される。

---

## ホットリロードの扱い

Lambda モードと**全く同じ方式**（asyncio ウォッチャー + importlib 動的再 import）でユーザーアプリのホットリロードを実現する。uvicorn の `--reload` は使わない。

| 対象 | 方式 | 既存との関係 |
|------|------|-------------|
| `app.py`（Mock 定義） | mock_handler の asyncio ウォッチャー（既存） | 変更なし |
| ユーザーの FastAPI アプリ | **新規** asyncio ウォッチャー（同じ仕組み） | 同一パターンを踏襲 |

### 仕組み

uvicorn `--reload` はプロセス全体を再起動するため、moto の `mock_aws` パッチが消える。
インプロセスで `importlib` によりモジュールだけを差し替えることで、moto パッチを維持したまま
ユーザーコードの変更を反映できる。

```
ユーザーのモジュールファイル mtime 変化（1秒ごとに検知）
  → importlib.import_module(mod_str) で再 import
  → self.user_app = getattr(mod, attr) で差し替え
  → 次のリクエストから新しいコードが使われる（moto パッチは継続）
```

### 実装場所

`gateway/main.py` の `LambdaGateway` に `_watch_user_app()` メソッドを追加し、
`start_file_watcher` 的な役割で asyncio タスクとして起動する。

```python
async def _watch_user_app(self):
    """ユーザーの FastAPI アプリモジュールの変更を監視して動的再ロードする。"""
    import importlib
    from pathlib import Path

    mod_str, attr = os.environ["SAPIMO_APP_MODULE"].rsplit(":", 1)
    # モジュールのソースファイルパスを取得
    mod = sys.modules.get(mod_str)
    if mod is None or not hasattr(mod, "__file__") or not mod.__file__:
        return
    watch_path = Path(mod.__file__)
    last_mtime = watch_path.stat().st_mtime

    while True:
        await asyncio.sleep(1)
        try:
            current_mtime = watch_path.stat().st_mtime
            if current_mtime == last_mtime:
                continue
            last_mtime = current_mtime
            # sys.modules から削除して強制的に再 import
            if mod_str in sys.modules:
                del sys.modules[mod_str]
            new_mod = importlib.import_module(mod_str)
            self.user_app = getattr(new_mod, attr)
            logger.info("Reloaded user FastAPI app: %s", mod_str)
        except Exception:
            logger.exception("Failed to reload user FastAPI app")
```

起動タイミングは mock_handler の `start_file_watcher()` と同様、
`LambdaGateway.__init__` の末尾（`mock_handler.start_file_watcher()` の直後）で行う。

```python
# __init__ 末尾に追加
if self.user_app is not None:
    asyncio.get_event_loop().create_task(self._watch_user_app())
```

ただし `asyncio.get_event_loop()` は uvicorn 起動後でないとイベントループが存在しないため、
mock_handler と同様に `try: asyncio.get_running_loop().create_task(...) except RuntimeError: pass` のパターンを使う。

---

## 変更ファイル一覧

### 1. `src/sapimo/docker/templates/gateway/main.py`

`LambdaGateway.__init__` でユーザーアプリを読み込み、fallback ハンドラで転送する。

#### `__init__` への追加

```python
import importlib
import sys

# 既存の初期化処理の末尾に追加
app_module_str = config.get("app_module") if config else None  # config は _load_configuration 後に参照
self.user_app = None
if app_module_str:
    sys.path.insert(0, "/workspace")
    mod_str, attr = app_module_str.rsplit(":", 1)
    self.user_app = getattr(importlib.import_module(mod_str), attr)
```

実際には `_load_configuration` の中で `config` を読んだタイミングで `self.user_app` をセットするのが自然。
`_load_configuration` の末尾に以下を追加する：

```python
# config の読み込み後に追記（_load_configuration メソッド末尾）
app_module_str = config.get("app_module")
if app_module_str:
    import importlib, sys
    sys.path.insert(0, str(self.project_root))
    mod_str, attr = app_module_str.rsplit(":", 1)
    self.user_app = getattr(importlib.import_module(mod_str), attr)
    logger.info("Loaded user FastAPI app: %s", app_module_str)
```

`self.user_app: Any = None` を `__init__` の先頭フィールド宣言に追加する。

#### `_setup_routes` の fallback ハンドラを差し替え

```python
# 既存
async def fallback_router(path: str, request: Request):
    method = request.method
    matched_route = self._find_matching_route(method, path)
    if not matched_route:
        if options.mode == "mock":
            return JSONResponse(...)
        raise HTTPException(status_code=404, ...)
    return await self._handle_request(matched_route, method, path, request)

# 変更後（user_app がある場合のみ転送を追加）
async def fallback_router(path: str, request: Request):
    method = request.method
    matched_route = self._find_matching_route(method, path)
    if not matched_route:
        if self.user_app is not None:
            return await self._forward_to_user_app(request)
        if options.mode == "mock":
            return JSONResponse(...)
        raise HTTPException(status_code=404, ...)
    return await self._handle_request(matched_route, method, path, request)
```

#### `_forward_to_user_app` メソッドを追加

```python
async def _forward_to_user_app(self, request: Request) -> Response:
    """
    Lambda ルートに一致しないリクエストをユーザーの FastAPI アプリへ転送する。
    httpx.ASGITransport を使うことでネットワークを介さずインプロセスで転送する。
    """
    body = await request.body()
    transport = httpx.ASGITransport(app=self.user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            content=body,
        )
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
    )
```

### 2. `src/sapimo/docker/single_compose_generator.py`

`generate_compose_config` の compose 設定に `SAPIMO_APP_MODULE` 環境変数を追加する。

```python
def generate_compose_config(self) -> dict[str, Any]:
    python_version = self._resolve_python_version()
    app_module = self._detect_app_module()

    env = {
        "SAPIMO_SINGLE_CONTAINER": "1",
        "SAPIMO_HOST": "0.0.0.0",
        "SAPIMO_PORT": "3000",
        "PYTHONPATH": "/workspace:/workspace/api_mock/docker",
    }
    if app_module:
        env["SAPIMO_APP_MODULE"] = app_module

    return {
        "services": {
            "sapimo": {
                "build": ...,  # 既存のまま（gateway テンプレート）
                "ports": ["3000:3000"],
                "environment": env,
                "volumes": ["..:/workspace:rw"],
                "restart": "unless-stopped",
            }
        }
    }

def _detect_app_module(self) -> str | None:
    """config.yaml に app_module が定義されていれば返す。"""
    if not self.config_path.exists():
        return None
    with open(self.config_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("app_module")
```

compose が使うテンプレートは既存の `gateway/` のまま。新規テンプレートディレクトリは不要。

### 3. `src/sapimo/parser/config_parser.py`

`paths` 必須バリデーションを `app_module` との OR 条件に変更する。

```python
# 変更前
if "paths" not in obj:
    raise Exception("paths key dose not exist in config file")

# 変更後
if "paths" not in obj and "app_module" not in obj:
    raise Exception("paths or app_module key must exist in config file")

# paths が存在しない場合は空 dict として扱う
self.apis: dict[str, dict[str, dict]] = {}
for p, val in obj.get("paths", {}).items():
    ...
```

### 4. `src/sapimo/main.py`

`init` コマンドに `--app` オプションを追加する。
Lambda テンプレートを持たない（`paths` なし）の config.yaml を生成する。

```python
@main.command()
@click.option("--template", ...)
@click.option("--cdk", ...)
@click.option("--terraform", ...)
@click.option(
    "--app",
    "app_module",
    type=str,
    default="",
    help="FastAPI app in 'module.path:attr' format (e.g. myapp.main:app). "
         "Enables FastAPI app mode. Combine with --template to run Lambda routes alongside.",
)
def init(template, cdk, tf_plan, app_module):
    if app_module and not template and not tf_plan:
        # FastAPI のみモード：paths なしの config.yaml を生成
        create_config_for_fastapi_app(app_module)
        return
    # 既存ロジック（template / tf_plan 処理）は変更なし
    # ただし、既存ロジックで生成した config.yaml に app_module を追記する処理を追加
    ...
    if app_module:
        _append_app_module_to_config(CONFIG_FILE, app_module)
```

```python
def create_config_for_fastapi_app(app_module: str):
    """FastAPI のみモード用の config.yaml と docker-compose.yml を生成する。"""
    WORKING_DIR.mkdir(exist_ok=True)
    config = {
        "app_module": app_module,
        # AWS サービス定義（ユーザーが編集する）
        "s3": {},
        "dynamodb": {},
        "cognito": {},
    }
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        click.echo(f"Generated {CONFIG_FILE}")
    else:
        click.echo(f"{CONFIG_FILE} already exists. Edit 'app_module' field manually.")
        return

    from sapimo.docker.single_compose_generator import SingleContainerComposeGenerator
    compose_gen = SingleContainerComposeGenerator(CONFIG_FILE)
    compose_gen.generate_compose_file()
    click.echo(f"Generated docker-compose.yml in {WORKING_DIR}")


def _append_app_module_to_config(config_file: Path, app_module: str):
    """既存 config.yaml に app_module フィールドを追記する。"""
    with open(config_file) as f:
        config = yaml.safe_load(f) or {}
    config["app_module"] = app_module
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    click.echo(f"Added app_module to {config_file}")
```

---

## config.yaml フォーマット

### FastAPI のみモード

```yaml
app_module: myapp.main:app

s3:
  my-bucket:
    region: us-east-1

dynamodb:
  users-table:
    partition_key: userId

cognito:
  UserPool:
    PoolName: my-pool
    Clients:
      - ClientName: web
```

### ハイブリッドモード（Lambda + FastAPI）

```yaml
app_module: myapp.main:app   # Lambda ルート以外のリクエストがここに流れる

paths:
  /items/{id}:
    get:
      Properties:
        Handler: items.app.lambda_handler
        CodeUri: ./lambda/items
        Runtime: python3.12

s3:
  my-bucket:
    region: us-east-1
```

`paths` に定義されたルートは Lambda が処理し、未定義のルートはユーザーの FastAPI アプリが処理する。

---

## ユーザーの利用フロー

```bash
# FastAPI のみモード
sapimo init --app myapp.main:app

# ハイブリッドモード（SAM テンプレートと併用）
sapimo init --template template.yaml --app myapp.main:app

# 起動（既存と同じ）
sapimo start
sapimo start --build
```

---

## 実装時の注意点

### moto スコープと import 順序

`mock_manager.start()` はプロセス全体に boto3 パッチを当てる。
ユーザーアプリが Gateway の `_load_configuration` 内（= MockManager.start() より後）で
import されるようにすること。`_forward_to_user_app` 経由でリクエスト時に呼ぶ形では
import タイミングが遅れすぎる可能性があるため、**コンテナ起動時点（`_load_configuration` 末尾）で import する**。

### httpx.ASGITransport の URL

`base_url="http://testserver"` は httpx の慣習的な表記。実際に testserver にアクセスするわけではなく、
ASGI インターフェースを介してインプロセスで呼び出す。URL のパスとクエリは `request.url` から引き継ぐ。

### ユーザーアプリの lifespan

ユーザーアプリが `@asynccontextmanager lifespan` を持つ場合、Gateway の uvicorn とは別に
ユーザーアプリの lifespan が呼ばれないことに注意。`httpx.ASGITransport` は
lifespan を自動で呼ばない。必要なら Gateway の startup/shutdown イベントでユーザーアプリの
lifespan を明示的に呼ぶか、ユーザー側で lifespan に依存しない設計にしてもらう。
初期実装ではこの問題は既知の制約として文書化にとどめる。

### ミドルウェアの二重実行（既知の制約）

`httpx.ASGITransport(app=self.user_app)` はユーザーアプリの ASGI スタック全体を呼び出すため、
ユーザーアプリに設定されたミドルウェアは Gateway のミドルウェアの**内側でさらに実行される**。

```
Client
  ↓ Gateway の CORS ミドルウェア
  ↓ fallback_router
  ↓ httpx.ASGITransport
  ↓ ユーザーアプリの CORS ミドルウェア  ← 二重実行
  ↓ ユーザーアプリのルーター
```

| ミドルウェア | 影響 | 対処 |
|---|---|---|
| **CORS** | Gateway にデフォルトで存在するため重複。ヘッダーが二重付与される | レスポンスから CORS ヘッダーを除去 ✅ |
| **GZip 圧縮** | Gateway にはデフォルトで存在しないため**問題なし** | 対処不要 |
| **認証・認可** | ロジックが二重に走るが機能上は問題なし | 対処不要 |
| **ロギング・tracing** | 二重ログが出るが機能上は問題なし | 対処不要 |

#### なぜハイブリッドモードでは根本解決が難しいか

ミドルウェアは `app` オブジェクト全体を包む層であり、ASGI の設計上「特定のリクエストだけ無効化」はできない。
ルート単位での制御（`Depends` 等）は可能だが、ミドルウェア層ではない。

- **FastAPI のみモード**（`paths` なし）: Gateway を通るリクエストが全部ユーザーアプリへ流れるため、
  Gateway の CORS ミドルウェアを無効化（`app_module` 設定時に `_setup_middleware` をスキップ）すれば解決できる。
- **ハイブリッドモード**（`paths` + `app_module`）: Lambda ルートには Gateway の CORS が必要なため、
  Gateway のミドルウェアを全無効化できない。根本解決策がない。

#### 推奨するユーザーへの指針（ドキュメント・README に記載する）

CORS は `_forward_to_user_app` 内で対処済みのため、ユーザーアプリ側の設定に制限はない。
認証・ロギング等のその他ミドルウェアも副作用が軽微であれば許容範囲。
GZip を含むその他のミドルウェアも Gateway 側に追加しない限り問題なし。

#### app.py へのミドルウェア追加禁止（README に明記する）

`api_mock/app.py` はモックレスポンス制御のためにユーザーが編集することを想定したファイルであるが、
技術的には Gateway の FastAPI インスタンスに対してミドルウェアを追加することも可能な状態にある。
FastAPI アプリモード（`app_module` 設定時）において `app.py` で Gateway にミドルウェアを追加すると
二重実行問題が発生し得るため、README に以下を明記する：

> **制約**: `api_mock/app.py` ではミドルウェアの追加（`app.add_middleware(...)` 等）を行わないこと。
> `app.py` はモックレスポンス定義（`@api.get` 等）専用ファイルである。
> ミドルウェアを追加した場合の動作は保証しない。

#### `_forward_to_user_app` での対処実装

二重実行の問題が発生するのは**Gateway とユーザーアプリの両側に同じミドルウェアが存在する場合のみ**。
Gateway のデフォルトミドルウェアは CORS のみであるため、対処が必要なのも CORS だけ。

**GZip は対処不要**：Gateway にはデフォルトで GZip ミドルウェアがない。ユーザーアプリ側だけに設定されている場合は正常に機能する。

**CORS**: レスポンスヘッダーからユーザーアプリが付与した CORS ヘッダーを除去することで、Gateway 側の CORS ミドルウェアのみが有効になる。

```python
CORS_HEADERS = {
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
    "access-control-expose-headers",
    "access-control-max-age",
}

async def _forward_to_user_app(self, request: Request) -> Response:
    body = await request.body()
    transport = httpx.ASGITransport(app=self.user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            content=body,
        )
    # ユーザーアプリの CORS ヘッダーを除去（Gateway 側が改めて付与する）
    filtered_headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in CORS_HEADERS
    }
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=filtered_headers,
    )
```

> **注意**: Gateway に手動で GZip 等の変換ミドルウェアを追加した場合は二重実行の問題が生じる。Gateway のミドルウェア構成はデフォルトから変更しないこと。

---

## 変更しないファイル

- `src/sapimo/docker/templates/gateway/` 内の `mock_handler.py`, `requirements.txt` 他 — `main.py` のみ変更
- `src/sapimo/mock/mock_manager.py` — FastAPI アプリモードでもそのまま使う（共有）
- `src/sapimo/docker/mock_manager.py` — 同上
- `tests/` — 既存テストはすべてパスし続けること。新規テストを追加する。

---

## テスト方針

追加するテストファイル: `tests/unit/test_fastapi_app_mode.py`

以下をカバーする：

1. `SingleContainerComposeGenerator._detect_app_module()` が `app_module` を正しく読む
2. `app_module` がある場合に compose の `environment` に `SAPIMO_APP_MODULE` が含まれる
3. `app_module` がない場合は `SAPIMO_APP_MODULE` が含まれない（既存テストとの整合）
4. `ConfigParser` が `app_module` のみの config.yaml を受け入れる（`paths` なしでエラーにならない）
5. `ConfigParser` が `paths` + `app_module` の両方を持つ config.yaml を受け入れる
6. `sapimo init --app myapp.main:app` が config.yaml と docker-compose.yml を生成する（`test_main_single_container_flow.py` に倣うスタイル）
7. `_forward_to_user_app` が `httpx.ASGITransport` を使ってリクエストを転送する（モック使用）
