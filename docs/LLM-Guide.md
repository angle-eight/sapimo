# LLM Guide for Sapimo

この文書は、Sapimo の実装変更・機能追加を行う LLM / 自動化エージェントのための包括的ガイドです。
この文書群を読めば、プロジェクト全体を理解し最短で作業に取りかかれることを目指しています。

---

## 1. Sapimo とは何か

Sapimo は、AWS バックエンドのローカル開発環境を手軽に再現する Python CLI ライブラリです。

**解決する課題**: AWS SAM や CDK でバックエンド開発をしているとき、ローカルで API を叩いて動作確認したい。
**提供する価値**:
- フロントエンド側はエンドポイント URL を切り替えるだけで、AWS にデプロイした時と同じ感覚で開発できる
- 単一コンテナ設計による素早い起動と、コード変更時のホットリロード
- S3・DynamoDB 等の AWS データをローカルファイルとして視認・編集できる

**配布形態**: `pip install sapimo` でインストールし、別リポジトリ（利用先プロジェクト）で使う。

---

## 2. 利用フロー全体像

```
利用先プロジェクト/
├── template.yaml (SAM) or cdk.out/*.template.json (CDK)
├── lambda/                    ← Lambda 関数ソースコード
│   ├── greeting/app.py
│   └── users/app.py
└── api_mock/                  ← sapimo init で生成される
    ├── config.yaml            ← テンプレートから変換された設定
    ├── app.py                 ← sapimo generate で生成。Mock 定義をここに書く
    ├── docker-compose.yml     ← 単一コンテナ compose
    ├── docker/                ← runtime 資産（テンプレート + sapimo パッケージ同梱）
    │   ├── gateway/           ← FastAPI ゲートウェイ (main.py, mock_handler.py 等)
    │   ├── single/            ← 単一コンテナ Dockerfile
    │   ├── sapimo/            ← sapimo パッケージのコピー（コンテナ内 import 用）
    │   └── ...
    ├── s3/                    ← S3 バケットのローカルミラー（ファイルで確認可能）
    ├── dynamodb/              ← DynamoDB テーブルのローカルミラー（JSON で確認可能）
    ├── sqs/                   ← SQS キューのローカルミラー
    └── log/
```

### CLI コマンド

| コマンド | 動作 |
|---------|------|
| `sapimo init` | テンプレート解析 → `config.yaml` + `docker-compose.yml` + runtime 資産を `api_mock/` に生成 |
| `sapimo init --template <file>` | 指定テンプレートから生成（`--cdk` で CDK CF 指定） |
| `sapimo generate` | `config.yaml` から `app.py` に Mock 関数のひな形を追記 |
| `sapimo start` | `api_mock/` で `docker compose up` を実行。`--build` `--detach` オプションあり |
| `sapimo status` | コンテナ内の AWS モックデータ状況を表示 |
| `sapimo clean` | AWS モックデータを削除（`--service s3,dynamodb` でサービス指定可能） |

---

## 3. アーキテクチャ概要

### 単一コンテナ設計

```
クライアント (localhost:8000)
  ↓
┌─────────────────────────────────────────────────────────┐
│  sapimo コンテナ（単一プロセス）                            │
│                                                         │
│  FastAPI Gateway (main.py)                              │
│    ├─ MockHandler: app.py を動的リロード (1秒間隔で監視)    │
│    │   ├─ Mock 関数が値を返す → そのまま JSON レスポンス    │
│    │   ├─ Mock 関数が None → Lambda 実行へフォールスルー    │
│    │   ├─ Mock 関数が int → OpenAPI example を返却        │
│    │   └─ Mock 関数が InputOverride → 入力変更して Lambda  │
│    │                                                     │
│    └─ LocalLambdaRunner: Lambda をインプロセス実行          │
│        ├─ CodeUri + Layers → 一時的に sys.path に追加     │
│        ├─ 環境変数を呼び出し単位で保存→適用→復元            │
│        └─ handler(event, context) を実行                  │
│                                                         │
│  AWS Mock (moto + mock_aws)                             │
│    ├─ S3Mock: ローカルファイル ↔ moto S3 双方向同期        │
│    ├─ DynamoMock: data.json / results.csv ↔ moto DynamoDB│
│    ├─ SqsMock: テキストファイル ↔ moto SQS               │
│    ├─ SnsMock / SesMock: 初期化のみ                      │
│    └─ 同期先: api_mock/{s3,dynamodb,sqs}/                │
└─────────────────────────────────────────────────────────┘
```

**設計の核心**:
- moto の `mock_aws` は同一プロセスでのみ有効。だから単一コンテナ＋インプロセス実行が必須。
- Lambda コードから `boto3.client("s3")` を呼ぶと、moto が透過的にモック応答する。
- Lambda 実行後に moto ↔ ローカルファイルを同期することで、データ変更をファイルとして視認できる。

---

## 4. 詳細リファレンス

以下のサブドキュメントで各領域の詳細を記載しています:

| ドキュメント | 内容 |
|------------|------|
| [コードベースマップ](llm/codebase-map.md) | 全モジュールの責務・主要クラス・関数一覧 |
| [config.yaml 仕様](llm/config-format.md) | 設定ファイルの構造・生成元・消費先 |
| [Mock システム](llm/mock-system.md) | Mock Router / AWS Mock / データ同期の仕組み |
| [開発ルール](llm/rules.md) | 不変条件・変更時チェックリスト・テスト要件 |

---

## 5. 実装変更時のクイックリファレンス

### よくある変更パターンと関連ファイル

| やりたいこと | 主に触るファイル |
|------------|---------------|
| CLI コマンドの追加・変更 | `src/sapimo/main.py` |
| テンプレート解析ロジックの修正 | `src/sapimo/parser/sam_parser.py`, `cdk_parser.py`, `cf_resource_parser.py` |
| config.yaml のフォーマット変更 | `src/sapimo/parser/config_parser.py`, `cf_resource_parser.py` |
| Mock API デコレータの変更 | `src/sapimo/mock/api.py`, `src/sapimo/mock/__init__.py` |
| Gateway のルーティング・リクエスト処理 | `src/sapimo/docker/templates/gateway/main.py` |
| Mock 関数の動的リロード | `src/sapimo/docker/templates/gateway/mock_handler.py` |
| Lambda 実行ロジック | `src/sapimo/docker/local_lambda_runner.py` |
| AWS モック（S3/DynamoDB等）のデータ同期 | `src/sapimo/mock/mock_manager.py` |
| Docker 環境固有のモック管理 | `src/sapimo/docker/mock_manager.py` |
| docker-compose.yml の生成ロジック | `src/sapimo/docker/single_compose_generator.py` |
| コンテナ内の Dockerfile | `src/sapimo/docker/templates/single/Dockerfile` |
| OpenAPI example 返却 | `src/sapimo/docker/templates/gateway/openapi_example_resolver.py` |
| 新しい AWS サービスモック追加 | `src/sapimo/mock/mock_manager.py` の `AwsMock` サブクラス追加 |

### テスト実行

```bash
# 最低限（変更確認用）
python -m pytest tests/unit -q

# 個別実行
python -m pytest tests/unit/test_single_compose_generator.py -q
python -m pytest tests/unit/test_main_single_container_flow.py -q
python -m pytest tests/unit/test_cf_resource_parser.py -q
python -m pytest tests/unit/test_cdk_parser.py -q
```

---

## 6. ドキュメント更新ルール

| 変更の種類 | 更新が必要なドキュメント |
|-----------|---------------------|
| 実装方針の変更 | `README.md`, `docs/Docker-Setup.md` |
| アーキテクチャ変更 | `docs/Docker-Architecture.md` |
| モジュール構造の変更 | `docs/llm/codebase-map.md` |
| config.yaml フォーマット変更 | `docs/llm/config-format.md` |
| Mock 仕様の変更 | `docs/llm/mock-system.md` |
| 不変条件・ルールの変更 | `docs/llm/rules.md` |
| 上記全般 | この文書 (`docs/LLM-Guide.md`) |
