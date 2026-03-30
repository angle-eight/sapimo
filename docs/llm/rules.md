# 開発ルール

このドキュメントは Sapimo に変更を加える際に守るべきルールと、確認すべき事項をまとめています。

---

## 1. 不変条件

以下は Sapimo の設計上の不変条件です。変更によってこれらが破られないことを常に確認してください。

### アーキテクチャの不変条件

1. **単一コンテナ設計**: ランタイムは `sapimo` サービス 1 つのみ。AWS モック（moto）は同一プロセスで動作する
2. **ライブラリ配布前提**: `pip install sapimo` で他リポジトリから使われる。`workspace/src` の存在を前提にしない
3. **runtime import は同梱資産で完結**: コンテナ内での `sapimo` パッケージの import は `api_mock/docker/sapimo/` にコピーされた資産で解決する
4. **再生成可能**: `api_mock/docker/` 配下の資産はすべて `sapimo init` で再生成可能であること
5. **Compose project 名の一意化**: プロジェクトパスの SHA1 ダイジェストで一意化し、複数リポジトリ間の衝突を回避

### 方針の不変条件

1. **fail-fast**: 異常をフォールバックで隠蔽しない。`api_mock/docker/` 不在は異常として扱う
2. **後方互換は不要**: 開発中でユーザーなし。デッドコードや互換のための冗長コードは無くす
3. **フォールバック追加は要熟考**: エラー時にフォールバック処理を入れるなら「そのフォールバックでアプリ機能への影響がないと断言できるか」を自問する。エラーで落ちるのは望ましい挙動。エラーを握りつぶして動き続けるのは最悪の挙動

---

## 2. 変更時チェックリスト

変更を入れたら以下をすべて確認してください:

### 機能面

- [ ] 外部プロジェクト利用前提を壊していないか（`workspace/src` 依存等のハードコードパスがないか）
- [ ] `sapimo init` 後に必要資産がすべて揃うか
- [ ] CLI コマンド (`init/generate/start/status/clean`) の挙動が一貫しているか
- [ ] 例外を握りつぶすフォールバックを追加していないか
- [ ] コンテナ内の `PYTHONPATH` と import 経路が正しいか

### コード品質

- [ ] デッドコードが増えていないか（後方互換のためのコード、到達不能コード）
- [ ] 場当たり的な修正ではなく、問題の根本原因に対処しているか
- [ ] テンプレートファイル（`src/sapimo/docker/templates/`）に変更が必要な場合、それが `sapimo init` の再実行で反映されるか

### テスト

```bash
# 最低限: 全ユニットテスト
python -m pytest tests/unit -q

# 変更箇所に応じた個別テスト
python -m pytest tests/unit/test_single_compose_generator.py -q
python -m pytest tests/unit/test_main_single_container_flow.py -q
python -m pytest tests/unit/test_cdk_parser.py -q
python -m pytest tests/unit/test_cf_resource_parser.py -q
python -m pytest tests/unit/test_local_lambda_runner.py -q
python -m pytest tests/unit/test_gateway_change_input_compat.py -q
python -m pytest tests/unit/test_gateway_jwt_authorizer_passthrough.py -q
python -m pytest tests/unit/test_gateway_openapi_example.py -q
python -m pytest tests/unit/test_gateway_options_mode.py -q
python -m pytest tests/unit/test_mock_handler_path_params.py -q
```

---

## 3. 典型障害と正しい対処

| エラー | 原因 | 正しい対処 |
|--------|------|-----------|
| `ModuleNotFoundError: sapimo` | コンテナ内の import 経路が壊れている | `api_mock/docker/sapimo` の同梱・PYTHONPATH を確認。**フォールバック追加は不可** |
| `python: can't open file '/workspace/main.py'` | entrypoint のパスが bind mount で隠れている | Dockerfile と compose の volume mount を確認 |
| `port is already allocated` | 旧コンテナ残骸 or Compose project 名の衝突 | `docker compose down` で停止。project 名の一意化を確認 |
| `config.yaml not found` | `sapimo init` 未実行 | `sapimo init` を実行させる。自動生成するフォールバックは入れない |
| Lambda 実行時に `sys.path` が汚染される | `_temporary_syspath` の保存・復元が不完全 | `LocalLambdaRunner` の context manager を確認 |

---

## 4. 新機能追加時のガイド

### 新しい AWS サービスモックを追加する場合

1. `src/sapimo/mock/mock_manager.py` に `AwsMock` のサブクラスを追加
2. `AwsMock.CreateMock()` に分岐を追加
3. `cf_resource_parser.py` の `_classification()` でリソースタイプを分類
4. `cf_resource_parser.py` の `_get_config_dict()` で config.yaml に出力
5. `cf_resource_parser.py` の `_get_ref_and_attr()` で Ref/Arn を定義
6. 必要に応じてデータ同期ディレクトリ（`api_mock/<サービス名>/`）を追加

### 新しい CLI コマンドを追加する場合

1. `src/sapimo/main.py` に `@main.command()` で定義
2. `WORKING_DIR` / `CONFIG_FILE` 等の定数を活用
3. コンテナ操作が必要なら `docker compose -p {project_name} exec ...` パターンに従う

### Gateway にエンドポイントを追加する場合

1. `src/sapimo/docker/templates/gateway/main.py` を編集
2. **注意**: テンプレートファイルなので、変更は `sapimo init` で `api_mock/docker/` に再コピーされて初めて反映される
3. テストは `tests/unit/test_gateway_*.py` パターンで追加

---

## 5. 技術スタック

| 技術 | 用途 | バージョン要件 |
|------|------|-------------|
| Python | 実行環境 | >=3.12 |
| click | CLI フレームワーク | >=8.3.1 |
| FastAPI | HTTP Gateway | >=0.135.2 |
| uvicorn | ASGI サーバー | FastAPI に同梱 |
| moto | AWS サービスモック | >=5.1.22 |
| boto3 | AWS SDK（moto 経由で使用） | >=1.42.75 |
| httpx | HTTP クライアント（マルチコンテナ用） | >=0.28.1 |
| python-jose | JWT デコード | >=3.5.0 |
| PyYAML | YAML パース | >=6.0.3 |
| hatchling | ビルドシステム | pyproject.toml で定義 |
| pytest | テストフレームワーク | dev dependency |

---

## 6. コンテナ内のファイルレイアウト

```
/workspace/                         ← プロジェクトルート（bind mount）
├── api_mock/
│   ├── config.yaml
│   ├── app.py
│   ├── docker/
│   │   ├── gateway/
│   │   │   ├── main.py           ← entrypoint
│   │   │   ├── mock_handler.py
│   │   │   └── openapi_example_resolver.py
│   │   ├── sapimo/               ← sapimo パッケージのコピー
│   │   └── single/
│   ├── s3/
│   ├── dynamodb/
│   └── sqs/
├── lambda/                         ← ユーザーの Lambda ソースコード
│   ├── func_a/app.py
│   └── func_b/app.py
└── (その他プロジェクトファイル)

PYTHONPATH=/workspace:/workspace/api_mock/docker
```

**重要**: `/workspace/api_mock/docker/sapimo/` がコンテナ内の sapimo パッケージ。
ホスト側で `pip install` した sapimo とは別物（`sapimo init` 時にコピーされたスナップショット）。
sapimo パッケージを更新したら `sapimo init` の再実行が必要。

---

## 7. ドキュメント更新ルール

| 変更の種類 | 更新が必要なドキュメント |
|-----------|---------------------|
| 実装方針の変更 | `README.md`, `docs/Docker-Setup.md` |
| アーキテクチャ変更 | `docs/Docker-Architecture.md` |
| モジュール構造の変更 | `docs/llm/codebase-map.md` |
| config.yaml フォーマット変更 | `docs/llm/config-format.md` |
| Mock 仕様の変更 | `docs/llm/mock-system.md` |
| 不変条件・ルールの変更 | `docs/llm/rules.md` |
| 上記全般 | `docs/LLM-Guide.md` |
