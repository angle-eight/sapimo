# Cognito サポート

sapimo の Cognito サポートは **2 つの独立した機能** で構成されます。

| 機能 | 対象 | 仕組み |
|------|------|--------|
| **Cognito UserPool/Client モック** | Lambda から boto3 で Cognito を呼ぶコード | moto の in-process モック |
| **JWT パス処理** | API Gateway に Cognito Authorizer が付いているルート | JWT を署名検証せずデコードし claims を Lambda に注入 |

---

## 1. JWT パス処理（APIの認証挙動）

`AuthType: COGNITO_USER_POOLS` または `AuthType: JWT` が設定されたルートへのリクエストを sapimo が受け取ったとき、以下のように動作します。

```
フロントエンド
  ↓  Authorization: Bearer <jwt-token>
sapimo (localhost:8000)
  ↓  jwt.get_unverified_claims(token)  ← 署名検証なし・デコードのみ
Lambda (event["requestContext"]["authorizer"]["jwt"]["claims"])
```

**重要**: sapimo は JWT の署名を検証しません。どのような秘密鍵で署名されたトークンでも、JWT として正しい形式であれば claims が通ります。

### ヘッダーあり / なし の挙動の違い

| Authorization ヘッダー | Lambda event への影響 |
|------------------------|----------------------|
| `Bearer <有効なJWT>` | `requestContext.authorizer.jwt.claims` に JWT の claims が注入される |
| なし | `requestContext.authorizer` キー自体が存在しない |
| `Bearer <不正なJWT>` | `requestContext.authorizer` キー自体が存在しない（パース失敗時はスキップ） |

Lambda コードが `event["requestContext"]["authorizer"]["jwt"]["claims"]` を参照している場合、ヘッダーなしで呼び出すと KeyError になります。ローカルテスト時はトークンを必ず付けてください。

---

## 2. フロントエンド側の設定

### API 呼び出し

API のベース URL を `localhost:8000` に変更するだけで動作します。

```javascript
// 例: axios の場合
const apiClient = axios.create({ baseURL: "http://localhost:8000" });

// リクエスト時に Bearer トークンを必ず付ける
apiClient.get("/items", {
  headers: { Authorization: `Bearer ${token}` }
});
```

### Cognito 認証（サインイン・トークン取得）

**sapimo は Cognito の HTTP エンドポイントをプロキシしません。**

フロントエンドの Cognito SDK（Amplify / amazon-cognito-identity-js 等）は `https://cognito-idp.{region}.amazonaws.com/` に直接 HTTP リクエストを送りますが、sapimo はこれを代替しません。

ローカル開発でのトークン取得手段は以下の 3 択です:

#### 選択肢 A: 本物の AWS Cognito を使い続ける（推奨）

フロントエンドの Cognito SDK の設定（`userPoolId`, `userPoolWebClientId` 等）は変更せず、本物の AWS Cognito に対してサインインしてトークンを取得します。取得したトークンを API 呼び出し時に使えば sapimo が通過させます。

```javascript
// Amplify Auth はそのまま本物の Cognito に向ける
const token = (await Auth.currentSession()).getIdToken().getJwtToken();

// API 呼び出しだけ localhost に向ける
fetch("http://localhost:8000/items", {
  headers: { Authorization: `Bearer ${token}` }
});
```

#### 選択肢 B: ダミー JWT を生成して使う

ローカル開発中に Cognito への接続が不要な場合は、任意の claims を持つ JWT を直接生成して使えます。sapimo は署名を検証しません。

```python
# Python で生成する例
from jose import jwt
token = jwt.encode(
    {"sub": "local-user-id", "email": "dev@example.com", "cognito:groups": ["admin"]},
    "dummy-secret",
    algorithm="HS256"
)
print(token)  # → フロントエンドに貼り付けて使う
```

```javascript
// npm jose でブラウザから生成する例
import { SignJWT } from "jose";
const key = new TextEncoder().encode("dummy-secret");
const token = await new SignJWT({ sub: "local-user-id", email: "dev@example.com" })
  .setProtectedHeader({ alg: "HS256" })
  .sign(key);
```

#### 選択肢 C: cognito-local を使う

既存のフロントエンドコードを変更せずに Cognito 認証フローを完全にローカルで再現したい場合は [`cognito-local`](https://github.com/jagregory/cognito-local) 等のサードパーティツールを使用してください。sapimo はこのツールの管理は行いません。

---

## 3. sapimo 側の設定

### config.yaml への Cognito セクション追加

`sapimo init` を実行するとテンプレートから自動生成されますが、手動追加する場合は以下の形式で `api_mock/config.yaml` に記述します。

```yaml
# ─── Cognito UserPool 定義 ───
cognito:
  my-pool:
    PoolName: my-pool
    AutoVerifiedAttributes:
      - email
    Clients:
      - ClientName: web-client
        ExplicitAuthFlows:
          - USER_PASSWORD_AUTH

# ─── 認証が必要なルートに AuthType を設定 ───
paths:
  /api/items:
    get:
      Properties:
        Handler: items.app.lambda_handler
        CodeUri: ./lambda/items
        Runtime: python3.12
        EventType: APIGW_V2
        AuthType: COGNITO_USER_POOLS
        Environment:
          Variables:
            # ${cognito:...} プレースホルダーは起動時に実際の moto ID に自動置換される
            USER_POOL_ID: ${cognito:my-pool:PoolId}
            CLIENT_ID: ${cognito:my-pool:ClientId:web-client}
```

### 環境変数プレースホルダー

Lambda の環境変数に `${cognito:...}` 形式のプレースホルダーを書くと、起動時に moto が割り当てた実際の ID に自動置換されます。

| プレースホルダー | 置換後 |
|----------------|--------|
| `${cognito:<pool-name>:PoolId}` | moto が生成した UserPool ID |
| `${cognito:<pool-name>:ClientId:<client-name>}` | moto が生成した UserPoolClient ID |

### 初期ユーザーデータ

`data/cognito/<pool-name>/data.json` にユーザーリストを置くと、起動時に自動で作成・確認済み状態になります。

```json
[
  {
    "username": "testuser",
    "password": "TestPass1!",
    "email": "test@example.com"
  }
]
```

終了時（`sapimo stop`）に moto 上の現在のユーザー状態がこのファイルに書き戻されるため、Lambda 経由で追加したユーザーも次回起動時に引き継がれます。

---

## 4. Lambda 側での claims 参照

JWT パス処理後、Lambda の event には以下の形式で claims が注入されます（APIGW_V2 フォーマット）。

```python
def lambda_handler(event, context):
    # JWT の claims を参照する
    claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
    user_id = claims["sub"]
    email = claims.get("email")
```

---

## 5. Cognito UserPool の boto3 呼び出し

Lambda コード内から `boto3.client("cognito-idp")` を使って Cognito を操作する場合（ユーザー登録・認証等）、moto がその呼び出しを自動的にモックします。設定・操作方法は通常の boto3 コードと同一です。

```python
import boto3
import os

def lambda_handler(event, context):
    client = boto3.client("cognito-idp", region_name="us-east-1")

    # 環境変数には ${cognito:...} プレースホルダーが解決済みの値が入っている
    pool_id = os.environ["USER_POOL_ID"]
    client_id = os.environ["CLIENT_ID"]

    # 通常通りの boto3 コールで動作する（AWS への実際の通信は発生しない）
    response = client.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    return response["AuthenticationResult"]["AccessToken"]
```
