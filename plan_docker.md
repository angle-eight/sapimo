# Sapimo 残タスクまとめ

## 📊 **現在の実装状況 (2025/11/01)**

### ✅ **完了済み**
- **Multi-Container Architecture**: Gateway + AWS-Mock + Lambda containers
- **FastAPI Gateway**: Mock + Lambda unified routing with priority handling
- **Mock API System**: @api decorators with dynamic reloading
- **CLI Integration**: `init`, `generate`, `start`, `status`, `clean` commands
- **Docker Compose Generator**: Dynamic generation from config.yaml
- **Python Runtime Updates**: 3.9 → 3.12 migration

### 🎯 **現在の動作確認**
```bash
uv run python -m sapimo --help
# Commands: clean, generate, init, start, status
```

## 2. マルチコンテナ アーキテクチャ設計

## 🚨 **残タスク（優先順位順）**

### **🔴 Priority 1: 基本機能の修正**

#### **1.1 CLI Integration Issues** ⚠️
```bash
# 現在のエラー: ModuleNotFoundError: No module named 'src'
# 原因: entry point設定の問題
```
- [ ] `pyproject.toml`のentry point修正
- [ ] パッケージ構造の調整
- [ ] `uv run sapimo`コマンドの動作確認

#### **1.2 Docker Compose Integration** 🐳
```bash
# 現在: docker-compose.yml.bak (バックアップのみ)
# 必要: api_mock内への動的生成
```
- [ ] `compose_generator.py`の動作テスト
- [ ] `init`コマンドでのcompose生成確認
- [ ] `start`コマンドの作業ディレクトリ修正

#### **1.3 Mock API File Generation** 📄
```bash
# 現在: api_mock/app.py が存在しない
# 必要: generateコマンドでの自動生成
```
- [ ] `generate`コマンドの動作確認
- [ ] `api_mock/app.py`生成テスト
- [ ] Mock decorator動作確認

### **🟡 Priority 2: 機能強化**

#### **2.1 Enhanced Path Parameter Support** 🔧
- [ ] 複雑なパスパラメータパターンの対応
- [ ] OpenAPI仕様に基づく型推論改善
- [ ] パラメータ名の衝突回避

#### **2.2 Error Handling & Validation** ⚠️
- [ ] config.yaml検証強化
- [ ] Docker環境エラーハンドリング
- [ ] Lambda実行エラーの適切な表示

#### **2.3 Development Experience** 🚀
- [ ] ホットリロード機能の改善
- [ ] ログ表示の最適化
- [ ] デバッグモードの追加

### **🟢 Priority 3: 完成度向上**

#### **3.1 Documentation** 📚
- [ ] README更新（新CLI構成反映）
- [ ] Docker Architecture文書更新
- [ ] サンプルプロジェクト作成

#### **3.2 Testing & Quality** 🧪
- [ ] 統合テストの追加
- [ ] CI/CD パイプライン修正
- [ ] コードカバレッジ向上

#### **3.3 Advanced Features** ⭐
- [ ] 複数Pythonランタイム対応
- [ ] AWS SDK mock強化
- [ ] パフォーマンス最適化

## 🎯 **Next Actions**

### **直近の作業順序**
1. **CLI修正**: `uv run sapimo`コマンドを動作させる
2. **基本フロー確認**: `init` → `generate` → `start`の動作テスト
