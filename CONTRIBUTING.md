# Sapimo 開発者ガイド

sapimo 本体の実装・改修・コントリビューションを行う方向けのドキュメントです。

## 開発環境のセットアップ

```bash
git clone https://github.com/angle-eight/sapimo.git
cd sapimo
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## テスト実行

```bash
# 全テスト
python -m pytest tests/unit -q

# 個別
python -m pytest tests/unit/test_single_compose_generator.py -q
```

## ドキュメント

| ドキュメント | 内容 |
|------------|------|
| [LLM 向け実装ガイド](docs/LLM-Guide.md) | アーキテクチャ全体像・モジュール構成・変更パターン一覧 |
| [Docker アーキテクチャ](docs/Docker-Architecture.md) | コンテナ構成・実行フローの詳細 |
| [config.yaml 仕様](docs/llm/config-format.md) | 設定ファイルの構造・生成元・消費先 |
| [コードベースマップ](docs/llm/codebase-map.md) | 全モジュールの責務・主要クラス・関数一覧 |
| [Mock システム](docs/llm/mock-system.md) | Mock Router / AWS Mock / データ同期の仕組み |
| [開発ルール](docs/llm/rules.md) | 不変条件・変更時チェックリスト |
