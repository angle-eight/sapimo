# LLM Guide for Sapimo

この文書は、Sapimo へ実装変更を入れる LLM/自動化エージェント向けの運用ルールです。

## 1. プロジェクトの前提

1. Sapimo はライブラリ配布 (`pip install`) 前提
2. 利用先は別リポジトリであることが通常
3. ランタイムは単一コンテナ (`sapimo` サービス)
4. AWS モックは同一プロセスで管理（moto + mock_aws）

## 2. 不変条件（変更時に守ること）

1. 異常をフォールバックで隠蔽しない（fail-fast）
2. `api_mock/docker/` 不在は異常として扱う
3. `workspace/src` の存在を前提にしない
4. 生成資産は `sapimo init` で再生成可能であること
5. 互換性維持のためのデッドコードを増やさない

## 3. 実行系の重要ファクト

1. Compose は単一サービスのみ
2. コンテナ起動 entrypoint は `api_mock/docker/gateway/main.py`
3. `sapimo` パッケージ import は `api_mock/docker/sapimo` 同梱資産で解決する
4. Compose project 名は CLI 内で一意化される（他リポジトリ衝突回避）

## 4. 主要コード位置

1. CLI: `src/sapimo/main.py`
2. 単一 compose 生成: `src/sapimo/docker/single_compose_generator.py`
3. Gateway template: `src/sapimo/docker/templates/gateway/main.py`
4. Local Lambda runner: `src/sapimo/docker/local_lambda_runner.py`
5. 単一コンテナ Dockerfile template: `src/sapimo/docker/templates/single/Dockerfile`

## 5. 変更時チェックリスト

1. 外部プロジェクト利用前提を壊していないか
2. ハードコードパスが `workspace/src` 依存になっていないか
3. `sapimo init` 後に必要資産が揃うか
4. CLI (`start/status/clean`) の挙動が一貫しているか
5. 例外を握りつぶすフォールバックを追加していないか

## 6. テスト実行の最低ライン

1. `python -m pytest tests/unit/test_single_compose_generator.py -q`
2. `python -m pytest tests/unit/test_main_single_container_flow.py -q`
3. `python -m pytest tests/unit -q`

## 7. 典型障害と正しい対処

1. `ModuleNotFoundError: sapimo`
   対処: runtime import 経路を確認。`api_mock/docker/sapimo` を前提に構成を修正。フォールバック追加は不可。
2. `python: can't open file '/workspace/main.py'`
   対処: bind mount で隠れない entrypoint を使う。
3. `port is already allocated`
   対処: 旧コンテナ残骸の可能性を確認。Compose project 衝突を避ける。

## 8. ドキュメント更新ルール

1. 実装方針を変えたら `README.md` と `docs/Docker-Setup.md` を同時更新
2. アーキテクチャ変更は `docs/Docker-Architecture.md` に反映
3. LLM 向け前提が変わったらこの文書も更新
