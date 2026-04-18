# コンテナ型 Lambda 対応 実装計画

## 背景

コンテナ型 Lambda（`PackageType: Image`）は、現行実装では以下の問題がある。

- **SAM**: `ImageInfo` が Dockerfile を字句解析して `COPY` からコードパスを逆算し、ZIP 型に偽装して config.yaml に書き込む。解析失敗ポイントが多く（マルチステージ・`ADD`・`ENTRYPOINT` 非対応）、壊れやすい
- **CDK**: `PackageType: Image` は config.yaml に残るが Gateway が完全に無視する
- **Gateway**: `PackageType` を読まず、コンテナ型でも ZIP 型と全く同じ in-process 実行フローになる

---

## 設計方針

### 核心原則：コンテナはパッケージ形式に過ぎない

コンテナ型 Lambda とは "デプロイ形式" の差異であり、中身は通常の Python コードである。
sapimo にとって意味があるのは「コードをどう実行するか」であり「コンテナで動かすか」ではない。

→ **ZIP 型と同じく `LocalLambdaRunner` で in-process 実行する。moto の in-process 原則を維持する。**

### 前提制約

> コンテナ型 Lambda は sapimo 本体と同じ Python バージョンで実行可能なコードでなければならない。

この制約下でのみ in-process 実行が成立する。制約を満たさない場合（例: Python 3.9 イメージを使った Lambda を Python 3.12 の sapimo で実行）は、実行時に明示的なエラーとして浮上する。サイレントな誤動作より好ましい。

### `RUN pip install` の依存パッケージ問題

コンテナ型 Lambda は Dockerfile 内の `RUN pip install` でパッケージを調達する。
これを sapimo で再現するため以下の方式をとる。

1. sapimo init 時に Dockerfile をパースして `RUN pip install` で指定されたパッケージを `PipPackages` として config.yaml に書き込む
2. Lambda 実行前（初回 or `PipPackages` 変化時）に `api_mock/.lambda_venvs/{function_name}/` に venv を作成してパッケージをインストールする
3. venv の `site-packages` を `sys.path` に一時追加して in-process 実行する（`LocalLambdaRunner` の既存 `_temporary_syspath` と同様）

#### この方式を選ぶ理由（別プロセス実行を採用しない理由）

別プロセス実行を採用すると:

- `mock_aws` パッチ（moto）は同一プロセスにしか届かないため、コンテナ型 Lambda からの `boto3.client("s3")` 等が実 AWS に飛ぶ、またはエラーになる
- ZIP 型 Lambda は moto が透過的に機能するが、コンテナ型だけ挙動が異なる（開発者体験の不整合）
- moto server を内部スレッドで立て `AWS_ENDPOINT_URL` をプロセスに注入すれば回避できるが、ZIP 型との非対称性が消えず、sapimo の設計思想から外れる

in-process 実行（`sys.path` 追加方式）は:

- moto が ZIP 型と完全に同じ挙動で機能する ✅
- 環境変数の扱いも ZIP 型と同一 ✅
- 唯一の懸念はパッケージ競合だが、「同 Python バージョン」制約下での既知の制限として明示すれば十分

---

## Dockerfile パース仕様（最小限）

現行 `ImageInfo` の主な問題はファイルコピーの解析（`COPY` 命令）でコードパスを逆算する複雑なロジックにある。
新実装ではこれを捨て、**実行に必要な情報だけを抽出する**最小限のパーサーに刷新する。

### 抽出対象ディレクティブ

| ディレクティブ | 取得情報 | 備考 |
|---|---|---|
| `ENV KEY=VALUE` | Lambda 環境変数 | 現行踏襲 |
| `CMD ["module.func"]` | Lambda ハンドラ | `ENTRYPOINT` は無視 |
| `RUN pip install pkg1 pkg2` | pip パッケージリスト | 新規追加 |
| `RUN pip install -r requirements.txt` | requirements.txt を展開して パッケージリスト化 | 新規追加 |
| `WORKDIR /path` | ハンドラ解決の作業ディレクトリ | 補助的に使用 |

### 無視するディレクティブ

- `FROM` — Python バージョン検証は行わない（前提制約として明示するのみ）
- `COPY` — コードパスは `DockerContext` を `CodeUri` として直接使用（後述）
- `ADD` — 非対応（現行と同じ、警告のみ）
- `ENTRYPOINT` — 無視（現行と同じ）
- `RUN` その他（pip 以外） — 無視

### コードパス（CodeUri）の決定

`DockerContext` をそのまま `CodeUri` とする。Dockerfile の `COPY` を辿ってコードパスを逆算する必要はない。

```yaml
# SAM template.yaml（Metadata に DockerContext がある）
Resources:
  MyFunction:
    Type: AWS::Serverless::Function
    Properties:
      PackageType: Image
    Metadata:
      DockerContext: lambda/my_func
      Dockerfile: Dockerfile
```

```yaml
# 生成される config.yaml
paths:
  /my-api:
    post:
      Properties:
        PackageType: Image
        CodeUri: lambda/my_func/   # ← DockerContext をそのまま使用
        Handler: app.lambda_handler # ← CMD から取得、未設定時はデフォルト値（要確認の警告ログ）
        PipPackages:               # ← RUN pip install から収集
          - requests>=2.28.0
          - boto3==1.26.0
        Environment:
          Variables:
            TABLE_NAME: items
```

### ハンドラの決定

- `CMD` に Lambda ハンドラが指定されていれば使用
- `CMD` なし → `"app.lambda_handler"` をデフォルトとしてセットし、**警告ログを出力**してユーザーに `config.yaml` の手動確認を促す

---

## config.yaml フォーマット変更

### 追加フィールド

`Properties` 配下に以下を追加:

| フィールド | 型 | 意味 |
|---|---|---|
| `PackageType` | `"Image"` | コンテナ型 Lambda であることを示す識別子 |
| `PipPackages` | `list[str]` | Dockerfile の `RUN pip install` から収集したパッケージ仕様 |

`Runtime` は省略される（コンテナ型には存在しない。sapimo は本体と同バージョンで実行する）。

### フォーマット例

```yaml
paths:
  /api/items:
    post:
      Properties:
        PackageType: Image
        CodeUri: lambda/items/
        Handler: app.lambda_handler
        PipPackages:
          - boto3==1.26.0
          - pydantic>=2.0
        Environment:
          Variables:
            TABLE_NAME: items
            REGION: ap-northeast-1
```

---

## 実装対象ファイル一覧

### 変更

| ファイル | 変更内容 |
|---|---|
| `src/sapimo/parser/sam_parser.py` | `PackageType == "Image"` のブロックを刷新。`ImageInfo` 呼び出しを廃止し、`ContainerLambdaDockerfileParser` で `Handler`・`PipPackages`・`envs` を取得。`CodeUri = DockerContext`、`PackageType: Image` を保持 |
| `src/sapimo/parser/cdk_parser.py` | `_api_props_from_lambda()` の Image ブロックを修正。`DockerBuild` or `aws:asset:path` を `CodeUri` として、`PackageType: Image` を保持 |
| `src/sapimo/docker/local_lambda_runner.py` | `PipPackages` 対応を追加。venv 作成・検証ロジックと、`site-packages` を `python_paths` に追加する処理を実装 |
| `src/sapimo/docker/templates/gateway/main.py` | `_load_configuration()` で `pip_packages` フィールドを `route_info` に読み込む（`LocalLambdaRunner` に渡す） |
| `docs/llm/config-format.md` | `PipPackages` フィールドの追記 |
| `docs/Docker-Architecture.md` | コンテナ型 Lambda の実行方式を追記 |

### 新規作成

| ファイル | 内容 |
|---|---|
| `src/sapimo/parser/container_lambda_parser.py` | `ContainerLambdaDockerfileParser` クラス。`RUN pip install`・`ENV`・`CMD` のみを解析する最小限パーサー |

### 削除

| ファイル | 理由 |
|---|---|
| `src/sapimo/parser/image_info.py` | `ContainerLambdaDockerfileParser` で完全置き換え |
| `tests/unit/test_image_info.py` | 上記削除に伴い不要 |
| `src/sapimo/docker/compose_generator.py` | codebase-map で「未使用」と記載されているデッドコード |
| `src/sapimo/docker/aws_mock_server.py` | 同上 |
| `src/sapimo/mock/executer/` | 旧アーキテクチャの残骸（codebase-map に記載済み） |

---

## `ContainerLambdaDockerfileParser` 設計

```python
@dataclass
class ContainerLambdaInfo:
    handler: str               # CMD から。未定義時は "app.lambda_handler"
    pip_packages: list[str]    # RUN pip install から収集したパッケージ仕様リスト
    envs: dict[str, str]       # ENV から収集した環境変数

class ContainerLambdaDockerfileParser:
    def __init__(self, docker_context: Path, dockerfile_name: str = "Dockerfile"):
        ...

    def parse(self) -> ContainerLambdaInfo:
        ...

    def _collect_pip_packages(self, run_args: str) -> list[str]:
        """RUN pip[3] install ... から -r requirements.txt を展開しつつパッケージを収集"""
        ...
```

---

## `LocalLambdaRunner` への venv 管理追加

```
api_mock/
  .lambda_venvs/          ← .gitignore 対象
    {function_name}/
      venv/               ← pip install 先の仮想環境
      packages.hash       ← PipPackages リストのハッシュ（変化時に再作成）
```

### 実行フロー（PipPackages がある場合）

```
LocalLambdaRunner.execute(route_info, event)
  ├─ pip_packages = route_info.get("pip_packages", [])
  ├─ if pip_packages:
  │    venv_path = _ensure_lambda_venv(function_name, pip_packages)
  │    site_packages = venv_path / "lib" / "pythonX.Y" / "site-packages"
  │    python_paths.insert(0, str(site_packages))   ← code_path より先に追加
  └─ (既存の _temporary_syspath, _temporary_environ, importlib 処理へ)
```

### 再作成判定

1. `packages.hash` が存在しない → 初回作成
2. `packages.hash` の内容が現在の `pip_packages` のハッシュと異なる → 再作成
3. 一致する → スキップ

---

## 既知の制約・制限

| 制約 | 内容 |
|---|---|
| **Python バージョン** | sapimo 本体と同じ Python バージョンで動作するコードに限る。バージョン不一致は実行時エラーとして顕在化する（サイレント誤動作はしない） |
| **パッケージ競合** | sapimo 本体のパッケージと名前・バージョンが競合する場合、`sys.path` の順序依存で予期しない挙動になる可能性がある。回避策: Lambda の依存を sapimo と合わせるか、venv の site-packages を lambda の code_uri より先に挿入する（上記設計のとおり） |
| **システムライブラリ依存** | Dockerfile の `RUN apt-get install` 等で追加したシステムライブラリは再現されない |
| **マルチステージビルド** | 非対応。パーサーは `RUN pip install` の簡易抽出のみを行う |
| **`pip install -e`** | 非対応 |

---

## 実装順序

1. `ContainerLambdaDockerfileParser` 新規作成（テスト先行）
2. `sam_parser.py` 修正（`ImageInfo` → `ContainerLambdaDockerfileParser`）
3. `cdk_parser.py` 修正
4. `local_lambda_runner.py` 修正（venv 管理追加）
5. `gateway/main.py` 修正（`pip_packages` フィールド読み込み）
6. `image_info.py`・デッドコード削除
7. ドキュメント更新（`config-format.md`, `Docker-Architecture.md`）
