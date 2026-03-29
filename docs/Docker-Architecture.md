# Sapimo Docker Architecture

## 現在のコンテナ構成

Sapimo は単一コンテナ (`sapimo`) で動作します。

### Single Container (`sapimo`)
- **役割**: API ルーティング / Mock 判定 / Lambda 実行 / AWS モック管理
- **ポート**: 8000 (host) → 3000 (container)
- **技術**: FastAPI + uvicorn + moto
- **機能**:
  - HTTP リクエスト受付とルーティング
  - `api_mock/app.py` の動的Mock定義読み込み
  - Lambda ハンドラのローカル実行
  - `mock_aws` による S3 / DynamoDB / SQS / SNS / SES 模擬
  - データ同期 (`data/`)

## 実行フロー

```
Client Request
  -> Unified Router (FastAPI)
    -> Mock 定義あり: Mockを返却 or InputOverride で Lambda 実行
    -> Mock 定義なし: Lambda 実行
      -> LocalLambdaRunner (同一プロセス)
        -> CodeUri + Layers を一時 sys.path に適用
        -> 呼び出し単位で環境変数を保存・適用・復元
        -> Lambda handler(event, context) を実行
  -> 必要時に mock データを同期
```

## 設計方針

1. moto の利点を活かすため、AWS モックは同一プロセスで管理する
2. コンテナ分離由来の設定複雑化を回避する
3. 旧実装の改善点は維持する
4. 危険なグローバル環境汚染は、呼び出し単位の保存/復元で抑制する

## 保持している改善仕様

1. JWT Authorizer passthrough（検証なし、claims 注入）
2. AWS_IAM ダミー authorizer 注入
3. InputOverride の互換挙動（辞書形式 + キーワード形式）
4. Path parameter 抽出
5. Options mode 切替
6. OpenAPI example 返却
7. HTTPException の再raise（502/503 を 500 で潰さない）

## 関連ファイル

1. `src/sapimo/docker/templates/gateway/main.py`
2. `src/sapimo/docker/local_lambda_runner.py`
3. `src/sapimo/mock/mock_manager.py`
4. `src/sapimo/mock/api.py`
5. `src/sapimo/docker/single_compose_generator.py`