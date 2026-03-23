# Docker移行後 機能回復 修正計画

## 1. 目的
Docker化に伴って仕様差分が発生した機能を、旧利用者が期待する挙動へ優先順に回復する。
本書は「これだけ見れば実装とテスト設計に着手できる」ことを目的とする。

## 2. 対象範囲

### 2.1 対象（修正対象）
- P0-1: 一括モード切替（`options`）の未接続
- P0-2: `change_input(date=3)` 互換性崩れ
- P1-1: `return 200` の OpenAPI example 返却未実装
- P1-2: パスパラメータ抽出/型検証の不足
- P2-1: 旧記法利用時の移行支援（警告/ガイド）不足

### 2.2 非対象（今回やらない）
- Docker初回ビルド時間の短縮
- 新しいAWSサービス追加
- Python以外のランタイム正式対応

## 3. 優先度と実装順

1. **Phase A (P0)**
   1. `options` 全体モード接続
   2. `change_input` 互換修正
2. **Phase B (P1)**
   1. OpenAPI example返却実装
   2. パスパラメータ抽出/型検証強化
3. **Phase C (P2)**
   1. 旧記法移行支援（警告メッセージ・ドキュメント）

## 4. 詳細修正仕様

---

## P0-1 `options` 一括モード切替を有効化

### 現状
- `sapimo.mock.api` に `options` は存在するが、Gateway実行フローで参照されない。

### 要件
- APIごとのMock定義が無くても、全体モードで挙動を切替可能にする。
  - `api` モード: 既存優先順位（Mock定義あり→Mock、なし→Lambda）
  - `mock` モード: Mock定義なしでもデフォルトレスポンス返却

### 実装方針
- `docker/gateway/main.py` の統合ルーティングに `options.mode` 判定を追加。
- `mock` モード時のデフォルト応答は `options.default_status` を利用。
- ルート定義あり/なしでの挙動を明示的に分岐。

### 影響ファイル
- `docker/gateway/main.py`
- `docker/gateway/mock_handler.py`（必要時）
- `src/sapimo/mock/api.py`（インターフェース調整が必要なら）

### 受け入れ基準
- `options.set_api_mode()` で既存挙動と同等。
- `options.set_mock_mode(200)` で未定義APIは 200 のMock応答。
- `options.set_mock_mode(400)` で未定義APIは 400 のMock応答。

---

## P0-2 `change_input(date=3)` 互換回復

### 現状
- 現実装はイベントトップレベルを上書きするため、`date=3` が期待通り path/query に入らない。

### 要件
- 旧利用コード `change_input(date=3)` を極力そのまま動作させる。

### 実装方針
- 入力上書き前に「正規化ステップ」を追加。
- 正規化ルール:
  1. `pathParameters` / `queryStringParameters` / `headers` / `body` が明示指定されている場合はそのまま適用。
  2. それ以外のキーは、ルートパラメータ名と一致する場合 `pathParameters` に寄せる。
  3. ルートパラメータにないキーは `queryStringParameters` に寄せる（後方互換優先）。
- 数値/真偽値は最終的に文字列化（API Gatewayイベント整合性）。

### 影響ファイル
- `docker/gateway/main.py`（`_invoke_lambda_with_override` 付近）

### 受け入れ基準
- `change_input(date=3)` で `{date}` が `"3"` としてLambdaに入る。
- `change_input(pathParameters={"date":"5"})` は優先してその値が使われる。
- 既存の `change_input(body=...)` 利用が壊れない。

---

## P1-1 OpenAPI example返却実装 (`return 200`)

### 現状
- 2xx 整数返却時はプレースホルダーJSONを返すのみ。

### 要件
- Mock関数が `return <status_code>` を返した場合、OpenAPI `responses` の example を返却する。

### 実装方針
- OpenAPI定義読み込みをGatewayに追加（候補: `api_mock/swagger.yaml`, `api_mock/openapi.yaml`）。
- 優先順位:
  1. `responses[status].content.*.example`
  2. `responses[status].content.*.examples.*.value`
  3. `responses[2xx]` の先頭候補
  4. 見つからなければ空ボディでステータスのみ返却
- JSON以外のメディアタイプはPhase Bでは対象外（`application/json` 優先）。

### 影響ファイル
- `docker/gateway/main.py`
- （必要なら新規）`docker/gateway/openapi_example_resolver.py`

### 受け入れ基準
- `return 200` でOpenAPI exampleが返る。
- `return 201` なども対象。
- example未定義時は指定ステータスのみ返却。

---

## P1-2 パスパラメータ抽出/型検証強化

### 現状
- `MockHandler._extract_simple_path_params()` が未実装で空dictを返す。
- 型注釈による検証が実質機能していない。

### 要件
- `/hello/{date}` 形式でパラメータ抽出し、型注釈に従って変換失敗時は422。

### 実装方針
- `mock_routes` 登録時に path pattern を保持。
- リクエストURLと pattern を照合してパラメータ抽出。
- 対応型（Phase B）: `str`, `int`, `float`, `bool`。
- 失敗時はFastAPI互換に近い422レスポンスを返す。

### 影響ファイル
- `docker/gateway/mock_handler.py`

### 受け入れ基準
- `/hello/123` は `date:int` に成功。
- `/hello/abc` で422。
- パラメータ無しルートへの影響なし。

---

## P2-1 旧記法移行支援

### 現状
- 旧CLI/旧config記法を使った時のガイダンスが弱い。

### 要件
- ユーザーが旧記法を使った場合、修正先をすぐ理解できる。

### 実装方針
- 旧利用パターン検出時に明示ログ出力。
- READMEとセットアップ文書に「旧→新」対応表を追加。

### 影響ファイル
- `src/sapimo/main.py`（CLIメッセージ）
- `src/sapimo/README.jp.md`
- `docs/Docker-Setup.md`

### 受け入れ基準
- 旧記法で実行しても、次に打つべきコマンドが表示される。

## 5. テスト設計

## 5.1 テストレベル
- **Unit**: 変換ロジック・ルーティング分岐・example解決器
- **Integration**: Gateway経由HTTPのE2E（Lambda呼出/Mock返却）
- **Regression**: 既存parser系テストが壊れていないこと

## 5.2 追加テストファイル案
- `tests/unit/test_gateway_options_mode.py`
- `tests/unit/test_gateway_change_input_compat.py`
- `tests/unit/test_gateway_openapi_example.py`
- `tests/unit/test_mock_handler_path_params.py`

## 5.3 テストケース一覧（ID付き）

### P0-1 (`options`)
- **TC-OPT-001**: `api` モード + Mock未定義 → Lambdaへフォールバック
- **TC-OPT-002**: `mock` モード + Mock未定義 + status=200 → 200固定応答
- **TC-OPT-003**: `mock` モード + Mock定義あり(dict) → Mock定義優先
- **TC-OPT-004**: `mock` モード + status=400 → 400固定応答

### P0-2 (`change_input`)
- **TC-OVR-001**: `change_input(date=3)` + `{date}` ルート → `pathParameters.date == "3"`
- **TC-OVR-002**: `change_input(pathParameters={"date":"9"})` が優先
- **TC-OVR-003**: ルート未定義キーは `queryStringParameters` へ
- **TC-OVR-004**: `body` 上書きの既存挙動維持

### P1-1 (OpenAPI)
- **TC-OAS-001**: `responses.200.content.application/json.example` 返却
- **TC-OAS-002**: `examples.*.value` 返却
- **TC-OAS-003**: 指定コード無し + 2xxフォールバック
- **TC-OAS-004**: example無し → ステータスのみ

### P1-2 (Path param)
- **TC-PATH-001**: `/a/123` + `id:int` 成功
- **TC-PATH-002**: `/a/abc` + `id:int` で422
- **TC-PATH-003**: `value:float` / `flag:bool` 変換
- **TC-PATH-004**: パラメータ無しルートに副作用なし

## 5.4 実行コマンド
```bash
pytest -q
pytest tests/unit/test_gateway_options_mode.py -q
pytest tests/unit/test_gateway_change_input_compat.py -q
pytest tests/unit/test_gateway_openapi_example.py -q
pytest tests/unit/test_mock_handler_path_params.py -q
```

## 6. 実装タスク分解（作業チケット粒度）

### チケットA1: options接続
- ルーティング分岐追加
- mock modeデフォルト応答実装
- TC-OPT系を追加し通す

### チケットA2: change_input互換
- override正規化関数追加
- 既存上書き処理を正規化経由に置換
- TC-OVR系を追加し通す

### チケットB1: OpenAPI example
- example解決器を実装
- `return int` 分岐へ接続
- TC-OAS系を追加し通す

### チケットB2: path param検証
- パターンマッチャ実装
- 型変換と422整形
- TC-PATH系を追加し通す

### チケットC1: 移行支援
- 旧記法検知ログ
- ドキュメント更新

## 7. Definition of Done
- P0〜P1の全テストケースが自動テストでGreen
- 既存 `tests/unit/test_*parser*.py` がGreen
- README/Setup文書に仕様差分と使い方が反映済み
- 主要フロー手動確認（`sapimo init -> generate -> start`）で回帰なし

## 8. リスクと回避策
- **リスク**: 互換実装で曖昧な入力解釈が増える
  - **回避**: 明示指定（`pathParameters` など）を最優先し、暗黙変換は限定
- **リスク**: OpenAPI解釈差異
  - **回避**: 対応対象を `application/json` に限定し、非対応は明示ログ
- **リスク**: 既存利用コードへの副作用
  - **回避**: 互換テストを先に作成（Failing test先行）

## 9. 着手手順（最短）
1. チケットA1のFailing testを作成
2. 最小実装でA1をGreen化
3. A2を同様に実施
4. B1/B2を順次実施
5. C1で文書・警告を整備

この順で進めれば、P0の互換問題を最短で回復しつつ、P1を安全に追加できる。

---

## 10. 実装完了サマリー（DONE）

| チケット | 内容 | 状態 | テスト |
|---|---|---|---|
| A1 | `options` 一括モード切替接続 | ✅ 完了 | TC-OPT-001〜004 全通過 |
| A2 | `change_input` 互換回復 | ✅ 完了 | TC-OVR-001〜004 全通過 |
| B1 | OpenAPI example 返却実装 | ✅ 完了 | TC-OAS-001〜004 全通過 |
| B2 | パスパラメータ抽出/型検証 | ✅ 完了 | TC-PATH 全7件通過 |
| C1 | 旧記法移行支援 | ✅ 完了 | — |

**追加テストファイル**
- `tests/unit/test_gateway_options_mode.py`
- `tests/unit/test_gateway_change_input_compat.py`
- `tests/unit/test_gateway_openapi_example.py`
- `tests/unit/test_mock_handler_path_params.py`

**追加実装ファイル**
- `docker/gateway/openapi_example_resolver.py`

**修正ファイル**
- `docker/gateway/main.py`（options接続、change_input正規化、OpenAPI example呼出）
- `docker/gateway/mock_handler.py`（パスパターンマッチング、型変換、bool対応）
- `src/sapimo/mock/api.py`（options.set互換メソッド追加）
- `src/sapimo/main.py`（旧config記法検出警告追加）