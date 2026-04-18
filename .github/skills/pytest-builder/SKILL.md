---
name: pytest-builder
description: "Use when: writing pytest tests, adding unit tests, creating test fixtures, testing FastAPI gateway, testing parsers, testing Lambda runner, debugging test failures, improving test coverage for sapimo. Triggers: 'テストを書いて', 'test を追加', 'pytest', 'unit test', 'coverage', 'テスト失敗'."
argument-hint: "テストを追加したいモジュールまたは機能を指定してください"
---

# Pytest Builder — Sapimo ユニットテスト作成スキル

## 対象スコープ

`tests/unit/` 配下のユニットテスト。統合テスト・Docker テストは対象外。

---

## テスト設計の原則

1. **対象を理解してから書く**: テスト対象のソースコードを必ず先に読む
2. **pytest ネイティブ**: `unittest.TestCase` は使わない。`pytest.fixture`・`@pytest.mark.parametrize` を活用する
3. **モック最小化**: 外部依存（boto3, Docker, ファイルI/O）のみモック。内部ロジックはモックしない
4. **テスト名は仕様書**: `test_<何を><どんな状態で><何が起きるか>` の命名規則
5. **1テスト1アサーション原則**: 複数の関連する検証を1テストにまとめるのは許容するが、無関係な検証は分離する

---

## Step 1: テスト対象の把握

1. **対象モジュールを特定する**
   - パーサー系: `src/sapimo/parser/`
   - Gateway テンプレート: `src/sapimo/docker/templates/gateway/`
   - Mock 系: `src/sapimo/mock/`
   - Docker compose 生成系: `src/sapimo/docker/`
   - CLI エントリポイント: `src/sapimo/main.py`

2. **既存テストを確認する**
   - 同モジュールのテストが `tests/unit/test_<module>.py` に存在するか検索
   - 重複を避け、不足しているケースだけ追加する

3. **テスト方針を決める** → [テスト種別の選択](#step-2-テスト種別の選択) へ

---

## Step 2: テスト種別の選択

| 対象 | 推奨アプローチ |
|------|---------------|
| 純粋関数・変換処理 | 直接呼び出し＋`assert` |
| ファイル I/O を伴う処理 | `tmp_path` fixture を使用 |
| AWS サービス (boto3) | `@mock_aws` デコレータ (moto) |
| `sys.path` / 環境変数の変更 | `monkeypatch` fixture |
| FastAPI / ASGI ルーティング | `importlib` 動的ロード＋`starlette.testclient.TestClient` |
| 外部ファイル読み込み (`open`, YAML) | `unittest.mock.patch`, `mock_open` |
| クラスの依存注入が複雑 | `__new__` で直接インスタンス化し属性を手動セット |

---

## Step 3: ファイル・命名規則

```
tests/unit/test_<対象モジュール名>.py
```

- **関数形式**: `def test_<何を検証するか>():` — 独立した単一ケース
- **クラス形式**: `class Test<機能名>:` — 関連ケースをグループ化
- **fixture**: ファイルスコープの共通セットアップは `@pytest.fixture` で定義
- **ファイル冒頭**: docstring で「このファイルが何をテストするか」を1〜3行で記述

---

## Step 4: 実装パターン集

### 4-1. パーサーのテスト（`unittest.mock.patch` + `mock_open`）

```python
import json
from pathlib import Path
from unittest.mock import patch, mock_open
from sapimo.parser.cf_resource_parser import CfResourceParser

def test_my_parser_case():
    template = {"Resources": {"MyBucket": {"Type": "AWS::S3::Bucket", "Properties": {}}}}
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("sapimo.parser.fn_resolver.open", mock_open(read_data=json.dumps(template))),
        patch("sapimo.parser.fn_resolver.yaml_parse", return_value=template),
    ):
        parser = CfResourceParser(Path("template.json"))
        assert ...
```

### 4-2. ファイル生成テスト（`tmp_path`）

```python
import yaml
from pathlib import Path

def test_generates_file(tmp_path: Path):
    config = tmp_path / "api_mock" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("paths: {}\n", encoding="utf-8")

    from sapimo.docker.single_compose_generator import SingleContainerComposeGenerator
    gen = SingleContainerComposeGenerator(config)
    output = gen.generate_compose_file()

    assert output.exists()
    data = yaml.safe_load(output.read_text())
    assert "services" in data
```

### 4-3. AWS サービステスト（moto `@mock_aws`）

```python
import boto3
import pytest
from moto import mock_aws
from sapimo.mock.mock_manager import CognitoMock

@pytest.fixture
def tmp_working_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
    return tmp_path

class TestCognitoMock:
    @mock_aws
    def test_pool_created(self, tmp_working_dir):
        config = {"my-pool": {"PoolName": "my-pool", "Clients": []}}
        mock = CognitoMock(config)
        mock.setup()

        client = boto3.client("cognito-idp", region_name="us-east-1")
        pools = client.list_user_pools(MaxResults=10)["UserPools"]
        assert any(p["Name"] == "my-pool" for p in pools)
```

### 4-4. Gateway テンプレートのテスト（`importlib` 動的ロード）

```python
import importlib.util
import sys
from pathlib import Path

def _load_gateway_module():
    gateway_dir = Path(__file__).resolve().parents[2] / "src" / "sapimo" / "docker" / "templates" / "gateway"
    if str(gateway_dir) not in sys.path:
        sys.path.insert(0, str(gateway_dir))
    spec = importlib.util.spec_from_file_location("gateway_main_for_test", gateway_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_gateway_route(monkeypatch):
    module = _load_gateway_module()
    monkeypatch.setattr(module.MockHandler, "reload_mock_definitions", lambda self: False)
    monkeypatch.setattr(module.MockHandler, "start_file_watcher", lambda self: None)
    gw = module.LambdaGateway()
    # client = TestClient(gw.app)
    # response = client.get("/hello")
    # assert response.status_code == ...
```

### 4-5. `monkeypatch` で環境変数・定数を差し替える

```python
def test_with_env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.setattr("sapimo.constants.WORKING_DIR", Path("/fake/dir"))
    ...
```

---

## Step 5: テスト実行と確認

**pytest 設定** (`pyproject.toml`)
- `pythonpath = ["src"]` → `from sapimo.xxx import yyy` でインポート可能
- `addopts = "-v -x"` → 失敗したら即停止

```bash
# 変更したファイルだけ実行
python -m pytest tests/unit/test_<module>.py -q

# 全ユニットテスト（-x で最初の失敗で停止）
python -m pytest tests/unit -q
```

---

## Step 6: 完了チェックリスト

- [ ] テストが `tests/unit/` に配置されている
- [ ] `pytest` が `-x` で即座に止まるほど無関係なモジュールを import していない
- [ ] `mock_aws` `monkeypatch` `tmp_path` を適切な範囲でスコープしている
- [ ] フォールバックを検証するテストではなく、**正しい挙動を検証するテスト**になっている
- [ ] `python -m pytest tests/unit -q` が全件グリーンになっている
- [ ] デッドコード・不要な `print` が残っていない

---

## 重要原則（rules.md より）

- **fail-fast**: 異常をフォールバックで隠蔽するコードのテストを書かない。落ちるべきところで落ちることを確認する
- **後方互換不要**: 古い API の互換テストは追加しない
- **最小変更**: テスト対象外のコードをテストのために変更しない。変更が必要なら設計の問題として本体を修正する
