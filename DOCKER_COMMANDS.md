# Sapimo Docker Commands Quick Reference

## 基本コマンド

### 🚀 実行
```bash
# 基本実行
sapimo docker run

# ポート指定
sapimo docker run --port 3001

# バックグラウンド実行
sapimo docker run --detach

# 強制リビルド
sapimo docker run --build
```

### 📊 管理
```bash
# ステータス確認
sapimo docker status

# ログ表示
sapimo docker logs
sapimo docker logs --follow

# 停止
sapimo docker stop

# 環境情報
sapimo docker info
```

### 🔧 メンテナンス
```bash
# イメージビルド
sapimo docker build

# リソース清掃
sapimo docker clean
sapimo docker clean --volumes
sapimo docker clean --images

# シェル接続
sapimo docker shell
```

### ⚙️ 設定
```bash
# 設定確認
sapimo docker config

# デフォルト設定作成
sapimo docker config --create
```

## 設定ファイル (sapimo-docker.yml)

すべて **オプション** です。ファイルがなくても動作します。

```yaml
# ネットワーク設定
host: "127.0.0.1"
port: 3000

# Python設定
python:
  default_version: "3.12"
  versions: ["3.9", "3.10", "3.11", "3.12", "3.13"]

# AWS Mock設定
aws_mocks:
  persist_data: true

# 開発設定
development:
  auto_reload: true
  log_level: "INFO"
```

## 従来コマンドとの互換性

```bash
docker-compose up
# 従来（ローカル）
# (旧) sapimo run は廃止されました。代わりに Docker を使用してください。

# Docker版（推奨）
docker-compose up
```