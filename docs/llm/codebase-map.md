# コードベースマップ

全モジュールの責務と主要なクラス・関数の一覧です。

---

## ディレクトリ構造概要

```
src/sapimo/
├── __init__.py          # バージョン export のみ
├── __main__.py          # python -m sapimo のエントリポイント
├── __version__.py       # VERSION = (0, 0, 1)
├── constants.py         # グローバル定数（WORKING_DIR, EventType, AuthType）
├── exceptions.py        # 例外クラス定義
├── main.py              # CLI 定義（click ベース）
├── utils.py             # ログ管理、ユーティリティ関数
│
├── parser/              # テンプレート解析 → config.yaml 生成
│   ├── yaml_loader.py   # YAML のカスタムローダー（CloudFormation 組込関数対応）
│   ├── fn_resolver.py   # CloudFormation 組込関数（Fn::*, Ref）の解決
│   ├── cf_resource_parser.py  # CF リソース分類（S3, DynamoDB, SQS 等）
│   ├── sam_parser.py    # AWS SAM テンプレート解析
│   ├── cdk_parser.py    # AWS CDK CloudFormation 出力解析
│   ├── config_parser.py # config.yaml の読み込み・パース
│   └── image_info.py    # Dockerfile 解析（Image PackageType の Lambda 用）
│
├── mock/                # Mock 定義とAWSサービスモック
│   ├── __init__.py      # api, change_input, options を export
│   ├── api.py           # MockRouter デコレータ（@api.get 等）
│   ├── mock_manager.py  # AWS モック管理（S3Mock, DynamoMock, SqsMock 等）
│   ├── mediator_route.py # FastAPI カスタム APIRoute（旧アーキテクチャ。現在は Gateway 側で処理）
│   └── executer/        # Lambda 実行関連（旧アーキテクチャ部分を含む）
│       ├── invoke_info.py
│       └── lambda_invoker.py
│
└── docker/              # Docker 関連の生成・管理
    ├── __init__.py      # VolumeManager, DockerConfigManager, DockerMockManager を export
    ├── single_compose_generator.py  # 単一コンテナ docker-compose.yml 生成
    ├── compose_generator.py         # マルチコンテナ compose 生成（現在は未使用）
    ├── local_lambda_runner.py       # Lambda インプロセス実行エンジン
    ├── mock_manager.py              # Docker 環境固有の AWS モック管理
    ├── aws_mock_server.py           # AWS Mock 専用サーバー（マルチコンテナ用。現在は未使用）
    ├── config_manager.py            # sapimo-docker.yml 設定読み込み
    ├── volume_manager.py            # ボリューム・ファイル管理
    │
    └── templates/       # コンテナイメージのソース（sapimo init で api_mock/docker/ にコピーされる）
        ├── gateway/
        │   ├── main.py              # FastAPI Gateway 本体（★ コンテナの entrypoint）
        │   ├── mock_handler.py      # app.py 動的リロードと Mock 処理
        │   ├── openapi_example_resolver.py  # OpenAPI 定義から example 返却
        │   ├── Dockerfile           # Gateway 用 Dockerfile
        │   └── requirements.txt
        ├── single/
        │   ├── Dockerfile           # 単一コンテナ用 Dockerfile（★ 現在のメイン）
        │   └── requirements.txt
        ├── aws_mock/                # AWS Mock 専用コンテナ（マルチコンテナ用）
        └── lambda_runtime/
            └── Dockerfile           # Lambda ランタイムコンテナ（マルチコンテナ用）
```

---

## 主要モジュール詳細

### `main.py` — CLI

click ベースの CLI。全コマンドのエントリポイント。

| 関数 | 責務 |
|------|------|
| `init(template, cdk)` | テンプレート解析 → `config.yaml` + `docker-compose.yml` + runtime 資産を生成 |
| `generate()` | `config.yaml` → `app.py` に Mock 関数ひな形を追記生成 |
| `start(host, port, build, detach)` | `api_mock/` で `docker compose up` を実行 |
| `status()` | コンテナ内で Mock データ状態を確認 |
| `clean(service, confirm)` | コンテナ内で Mock データを削除 |
| `_compose_project_name()` | プロジェクトルートのパスから一意な Compose プロジェクト名を生成 |

**注意**: `start/status/clean` はコンテナ内で Python コードを直接実行する（`docker compose exec ... python -c "..."`）。

### `constants.py` — 定数

| 定数 | 値 | 用途 |
|------|-----|------|
| `WORKING_DIR` | `Path.cwd() / "api_mock"` | api_mock ディレクトリの絶対パス |
| `API_FILE` | `WORKING_DIR / "app.py"` | Mock 定義ファイル |
| `CONFIG_FILE` | `WORKING_DIR / "config.yaml"` | 設定ファイル |
| `EventType` | Enum (APIGW, APIGW_V2, EVENTBRIDGE, S3, ...) | API Gateway バージョン等のイベント種別 |
| `AuthType` | Enum (NONE, JWT, AWS_IAM, CUSTOM, ...) | 認証タイプ |

---

### Parser モジュール群

テンプレート → config.yaml の変換パイプライン。継承階層:

```
FnResolver  ← CloudFormation 組込関数の解決
  └─ CfResourceParser  ← リソース分類（S3, DynamoDB 等）
       ├─ SamParser     ← SAM 固有のリソース（Serverless::Function 等）
       └─ CdkCfParser   ← CDK 固有のリソース解決（asset path, md5 hash マッチング）
```

#### `fn_resolver.py`
- `_treat(dic)`: 再帰的に `Fn::*` / `Ref` を解決する中核メソッド
- 対応関数: `Ref`, `Fn::GetAtt`, `Fn::FindInMap`, `Fn::Join`, `Fn::Select`, `Fn::Split`, `Fn::Sub`
- `_refs` dict に各リソースの Ref/Arn を事前登録し、参照解決に使用

#### `cf_resource_parser.py`
- `_classification(name, val)`: リソースタイプ別に `_buckets`, `_tables`, `_sqss`, `_snss`, `_sess` 等に振り分け
- `_get_config_dict()`: 振り分けたリソースを config.yaml 形式の dict に変換
- `create_config_file(output_path, overwrite)`: config.yaml を書き出し

#### `sam_parser.py`
- `_classification`: `AWS::Serverless::Function` のイベント（Api, HttpApi, S3 等）を解析
- `_apis` に API パス → メソッド → Properties のマッピングを構築
- Auth 情報（JWT, AWS_IAM, Cognito, Custom Lambda）も解析して `AuthType` に反映
- `PackageType: Image` の場合は `ImageInfo` で Dockerfile を解析

#### `cdk_parser.py`
- CDK 固有の課題: CodeUri が asset hash パスになる → ソースコードの MD5 ハッシュで逆引き
- `_search_code_uri()`: CDK asset path → 実際のソースコードディレクトリを特定
- `_integrations_map`, `_lambdas_map`, `_layers_map` で CDK リソース間の参照を解決
- `AWS::ApiGatewayV2::Route`, `AWS::ApiGateway::Method` を処理して API 定義を構築

#### `config_parser.py`
- config.yaml を読み込んで `apis` (パス→メソッド→設定) と `all_resource` を提供
- `get_service_config(service)`: 指定サービス（s3, dynamodb 等）の設定を取得

#### `image_info.py`
- `PackageType: Image` の Lambda 用。Dockerfile を解析して CodeUri, Handler, Layers, ENV を抽出
- COPY, ENV, CMD, ENTRYPOINT, WORKDIR を解釈

---

### Mock モジュール群

#### `mock/api.py` — Mock Router

利用者が `api_mock/app.py` で使うデコレータ API。

```python
from sapimo.mock import api, change_input

@api.get("/users/{user_id}")
async def get_user(user_id: int):
    return {"id": user_id, "name": "Mock User"}  # Mock 値を返す

@api.post("/items")
async def create_item():
    return None  # None → 実際の Lambda を実行

@api.get("/modified")
async def modified():
    return change_input(query="overridden")  # 入力を変更して Lambda 実行
```

| クラス/関数 | 責務 |
|-----------|------|
| `MockRouter` (グローバル `api`) | デコレータでルート定義を収集。`routes` / `route_info` に保持 |
| `InputOverride` | `change_input()` の戻り値。Lambda 呼び出し時に event を部分上書き |
| `MockOptions` (グローバル `options`) | `mode` (`"api"` / `"mock"`) の切り替え |

#### `mock/mock_manager.py` — AWS モック

moto ベースの AWS サービスモック。**コンテナ内で同一プロセスとして動作**する。

| クラス | 責務 |
|-------|------|
| `MockManager` | 全サービスモックのライフサイクル管理 (`start/stop/init_data/sync`) |
| `S3Mock` | S3 バケット作成、ローカルファイル→バケットアップロード、バケット→ローカル同期 |
| `DynamoMock` | テーブル作成、`data.json` / `results.csv` からデータ投入、テーブル→JSON 同期 |
| `SqsMock` | キュー作成、ローカルファイル→メッセージ送信、メッセージ→ファイル同期 |
| `CognitoMock` | UserPool/Client 作成、`data.json` からユーザー投入、`list_users` 同期、プレースホルダー解決 |
| `SnsMock` / `SesMock` | 初期化のみ（データ同期は未実装） |

#### `mock/mediator_route.py`

FastAPI の `APIRoute` を拡張したカスタムルート。旧アーキテクチャの名残で存在。現在のメインフローは Gateway 側の `mock_handler.py` が担当。

---

### Docker モジュール群

#### `docker/single_compose_generator.py` — Compose 生成（★ メイン）

`sapimo init` 時に呼ばれる。

1. `_ensure_docker_templates()`: `src/sapimo/docker/templates/` → `api_mock/docker/` にコピー
2. `_copy_runtime_sapimo_package()`: 実行中の sapimo パッケージ自体を `api_mock/docker/sapimo/` にコピー
3. `generate_compose_config()`: 単一サービス `sapimo` の compose 設定を生成
4. `generate_compose_file()`: YAML ファイルとして書き出し

生成される compose の構造:
- サービス名: `sapimo`
- イメージ: `sapimo-single:latest`
- Dockerfile: `api_mock/docker/single/Dockerfile`
- entrypoint: `python /workspace/api_mock/docker/gateway/main.py`
- ボリューム: `..:/workspace:rw` (プロジェクトルート全体をマウント)
- ポート: `${SAPIMO_PORT:-8000}:3000`

#### `docker/local_lambda_runner.py` — Lambda インプロセス実行

Gateway から呼ばれ、Lambda handler を同一プロセスで実行する。

| メソッド | 責務 |
|---------|------|
| `execute(route_info, event)` | handler をインポートして実行。asyncio Lock で直列化 |
| `_temporary_syspath(paths)` | CodeUri + Layers のパスを一時的に `sys.path` に追加・除去 |
| `_temporary_environ(new_env)` | 環境変数を一時的に置換・復元 |
| `_build_lambda_environment(route_info)` | AWS 認証情報等のダミー環境変数を構築 |

**重要**: 実行ごとに `sys.modules.pop(module_name)` して再 import するため、コード変更が即時反映される。

#### `docker/templates/gateway/main.py` — FastAPI Gateway

コンテナ内の entrypoint。`LambdaGateway` クラスが全体を統括。

| メソッド | 責務 |
|---------|------|
| `__init__` | config.yaml 読み込み、ルーティング構築、MockHandler/LocalLambdaRunner 初期化 |
| `unified_router` | 全 HTTP リクエストのハンドラ。Mock → Lambda の順で処理 |
| `_invoke_lambda` | LocalLambdaRunner 経由で Lambda を実行 |
| `_build_lambda_event` | HTTP リクエスト → API Gateway v2 イベント形式に変換 |
| `_build_authorizer_context` | JWT/IAM の authorizer context を構築（**検証なし**、claims 注入のみ） |
| `_initialize_local_mock_manager` | 単一コンテナモードで in-process AWS Mock を起動 |

**環境変数 `SAPIMO_SINGLE_CONTAINER=1`** のとき単一コンテナモード。LocalLambdaRunner と MockManager を使う。

#### `docker/templates/gateway/mock_handler.py` — Mock 動的リロード

| メソッド | 責務 |
|---------|------|
| `reload_mock_definitions()` | `app.py` を `importlib` で動的にリロード。`st_mtime` で変更検知 |
| `has_mock_definition(method, path)` | 指定パスに Mock 定義があるか判定（パターンマッチ対応） |
| `handle_mock_request(method, path, request)` | Mock 関数を実行。パスパラメータの型変換も実施 |
| `start_file_watcher()` | 1秒間隔で `app.py` の変更を監視する非同期タスクを起動 |

#### `docker/templates/gateway/openapi_example_resolver.py`

Mock 関数が HTTP ステータスコード (int) を返した場合に、OpenAPI 定義ファイル (`api_mock/swagger.yaml` or `openapi.yaml`) から対応する example を解決して返す。

#### `docker/compose_generator.py` — マルチコンテナ Compose 生成

**現在は未使用**（単一コンテナ設計が採用されているため）。Gateway + 個別 Lambda コンテナ + AWS Mock コンテナの構成を生成する機能。将来のマルチコンテナ対応用。

---

### テスト

```
tests/unit/
├── test_cdk_parser.py                    # CdkCfParser のテスト
├── test_cf_resource_parser.py            # CfResourceParser のテスト
├── test_cognito_mock.py                  # CognitoMock / プレースホルダー解決のテスト
├── test_compose_generator_docker_setup.py # DockerComposeGenerator のテスト
├── test_gateway_change_input_compat.py   # InputOverride 互換テスト
├── test_gateway_jwt_authorizer_passthrough.py # JWT passthrough テスト
├── test_gateway_openapi_example.py       # OpenAPI example 返却テスト
├── test_gateway_options_mode.py          # Options mode テスト
├── test_image_info.py                    # Dockerfile 解析テスト
├── test_local_lambda_runner.py           # LocalLambdaRunner テスト
├── test_main_single_container_flow.py    # CLI + 単一コンテナフローテスト
├── test_mock_handler_path_params.py      # パスパラメータ処理テスト
├── test_mock_package_import.py           # Mock パッケージ import テスト
└── test_single_compose_generator.py      # SingleContainerComposeGenerator テスト
```
