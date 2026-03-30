# config.yaml 仕様

`api_mock/config.yaml` は Sapimo の中核となる設定ファイルです。
テンプレート（SAM/CDK）から自動生成され、コンテナ内の Gateway が読み込んで API ルーティングと AWS モック初期化に使用します。

---

## 生成と消費の流れ

```
template.yaml / cdk.out/*.template.json
  ↓ sapimo init
  ↓ SamParser / CdkCfParser → CfResourceParser._get_config_dict()
  ↓
api_mock/config.yaml
  ↓
  ├─ ConfigParser (config_parser.py): Gateway/CLI で読み込み
  ├─ MockManager (mock_manager.py): AWS モックの初期化に使用
  └─ SingleContainerComposeGenerator: 直接は読まない（生成のみ）
```

---

## スキーマ

```yaml
# ─── API 定義 ───
paths:
  <APIパス>:                    # 例: /users/{user_id}
    <HTTPメソッド>:             # get, post, put, delete, patch (小文字)
      Properties:
        CodeUri: <string>       # Lambda ソースコードのディレクトリ（プロジェクトルート相対）
        Handler: <string>       # Lambda ハンドラ（例: app.lambda_handler）
        Runtime: <string>       # Python ランタイム（例: python3.12）
        EventType: <string>     # APIGW | APIGW_V2（API Gateway バージョン）
        AuthType: <string>      # NONE | JWT | AWS_IAM | CUSTOM | CUSTOM_TOKEN |
                                # CUSTOM_REQUEST | COGNITO_USER_POOLS | OAUTH2 |
                                # OPENID_CONNECT | API_KEY | RESOURCE_POLICY
        Authorizer: <string>    # (オプション) Authorizer ARN
        AuthSource: <string>    # (オプション) Identity Source
        Environment:
          Variables:
            <KEY>: <VALUE>      # Lambda 環境変数
        Layers:                 # (オプション) Lambda レイヤーのパス（プロジェクトルート相対）
          - <string>
        Timeout: <int>          # (オプション) タイムアウト秒
        MemorySize: <int>       # (オプション)
        PackageType: <string>   # (オプション) Zip | Image

# ─── AWS リソース定義 ───
s3:
  <バケット名>:
    BucketName: <string>        # バケット名（通常キー名と同じ）

dynamodb:
  <テーブル名>:
    TableName: <string>
    AttributeDefinitions:
      - AttributeName: <string>
        AttributeType: S | N | B
    KeySchema:
      - AttributeName: <string>
        KeyType: HASH | RANGE
    BillingMode: <string>       # PAY_PER_REQUEST | PROVISIONED
    ProvisionedThroughput:      # BillingMode: PROVISIONED の場合
      ReadCapacityUnits: <int>
      WriteCapacityUnits: <int>

sqs:
  <キュー名>:
    QueueName: <string>
    # SQS の属性（DelaySeconds, MaximumMessageSize 等）

sns:
  <トピック名>:
    TopicName: <string>

ses:
  <ID名>:
    EmailIdentity: <string>

cognito:
  <プール名>:
    PoolName: <string>          # UserPool 名
    AutoVerifiedAttributes:     # (オプション) 自動検証属性
      - email
    Clients:
      - ClientName: <string>    # UserPoolClient 名
        ExplicitAuthFlows:      # (オプション) 認証フロー
          - USER_PASSWORD_AUTH

# ─── CDK 固有（自動生成のみ。手動編集不要） ───
cdk:
  <リソース名>:
    aws:cdk:path: <string>
    aws:asset:path: <string>

outputs:
  <出力名>:
    Value: <string>
    Description: <string>
```

---

## 実例

### SAM テンプレートから生成される config.yaml

```yaml
paths:
  /hello_world:
    post:
      Properties:
        CodeUri: lambda/greeting/
        Handler: app.lambda_handler
        Runtime: python3.12
        EventType: APIGW
        AuthType: NONE
        Environment:
          Variables:
            BucketName: test-bucket
            TableName: test-table
        Layers:
          - my_layer/
        Timeout: 3

s3:
  test-bucket:
    BucketName: test-bucket

dynamodb:
  test-table:
    TableName: test-table
    AttributeDefinitions:
      - AttributeName: id
        AttributeType: S
    KeySchema:
      - AttributeName: id
        KeyType: HASH
    BillingMode: PAY_PER_REQUEST

cognito:
  MyCognitoPool:
    PoolName: MyCognitoPool
    Clients:
      - ClientName: WebClient
        ExplicitAuthFlows:
          - USER_PASSWORD_AUTH
```

### CDK から生成される config.yaml の特徴

- `CodeUri` は CDK の asset path ではなく、MD5 ハッシュ照合で解決された**実際のソースディレクトリ**が入る
- `EventType` は CDK の API Gateway 種類に応じて `APIGW` or `APIGW_V2`
- `cdk` セクションにメタデータが追加される（runtime では使用しない）

---

## config.yaml の消費側での使われ方

### Gateway (main.py) での利用

`paths` セクションからルーティングテーブルを構築:

```python
route_key = f"{method.upper()}:{path}"
self.lambda_routes[route_key] = {
    "function_name": ...,
    "handler": properties["Handler"],
    "code_uri": properties["CodeUri"],
    "environment": properties.get("Environment", {}).get("Variables", {}),
    "layers": properties.get("Layers", []),
    "auth_type": properties.get("AuthType", "NONE"),
    ...
}
```

### MockManager での利用

`s3`, `dynamodb`, `sqs`, `sns`, `ses`, `cognito` セクションの設定をそのまま boto3 の引数として使い、moto でリソースを作成する:

```python
# DynamoDB の例
self._dynamodb.create_table(**props)  # config の dict をそのまま展開
```

#### Cognito プレースホルダー

Lambda 環境変数内の Cognito プレースホルダーは Gateway 起動時に自動解決される:

| プレースホルダー | 解決先 |
|-----------------|--------|
| `${cognito:<pool_name>:PoolId}` | moto が生成した UserPoolId |
| `${cognito:<pool_name>:ClientId:<client_name>}` | moto が生成した ClientId |

---

## 手動編集のガイドライン

- `sapimo init` で自動生成された後、手動でパスを追加したり環境変数を調整できる
- `sapimo init` を `--template` なしで再実行しても、既存の config.yaml は上書きされない（マージされる）
- `Properties` 配下の構造は AWS SAM の `AWS::Serverless::Function` の Properties に準拠
