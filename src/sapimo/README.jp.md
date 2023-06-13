# これは何？
AWS SAMを利用してlambda+APIGatewayでAPIを作っているときに使えるMockAPIを生成するためのモジュールです。コンテナを使用せずローカル環境で動作します。
フロント開発用APIモックの多機能版といった感じ。
`sam local start-api`の代わりとして使うために作成しました。
(ちなみに大部分がFastAPIとmotoの機能で成り立ってます。)

特徴
- 高速な起動と呼び出し (コンテナビルドないので)
- 手元でlambdaコード変更しても即時反映　(sam build不要)
- lambdaコードを書き換えずにAPIの動作を変更可能
- S3およびDynamoDBも裏側でモック
- S3およびDynamoDB内のデータをローカルで確認可能

`sam local start-api`に比べ明らかに劣る点
- AWS実環境との環境差異が大きい
- 今のところ動作環境はあらかじめ用意しておく必要がある
- python+SAMでしか使えない

Webアプリ作成時に細かな環境差異は気にせずフロントとAPIのコードを連携させて動作を確認しながら開発を進める時用という位置づけです。

結合テストでの利用は想定してません。

# 使い方
lambdaコード実行に必要な仮想環境は起動しておいてください。

`pip install sapimo`
(template.yamlのあるディレクトリで)
`python -m sapimo run --port 3000`
これで3000番ポートでmockAPIが起動します。
(portを省略した場合3000番ポートで起動します)

# (option) API編集
api_mock下のappを編集することでAPIの動作を変更できます。

### パラメータの検証
FAST apiの機能でAPIのパラメータを検証できます。
``` python
@ lambda_mock.get("/hello/{date}")
async def hello_get_mock(date: int):
    return
```
dateに文字列など入って呼び出された場合に,lambdaコード実行前にエラーを返すようになります。

### 入力すり替え
呼び出し時のパラメータを無視して、指定の値でlambdaコードを実行できます。
``` python
from sapimo import change_input
@ lambda_mock.get("/hello/{date}")
async def hello_get_mock(date: int):
    return change_input(date=3)
```
呼び出し時の値に関わらずdate=3でlambdaコードが実行されます。


### なにもしないmockとして動作
紐づけたlambdaコードを実行せずに指定した値を返すことができます。
``` python
@ lambda_mock.get("/hello/{date}")
async def hello_get_mock(date: int):
    return {"message":"hello 11"}
```
returnでなんらかの値(dict,str)を返した場合、lambdaコードは実行されず、値がそのまま返ります

### コードを指定して(Open API定義連携時) exampleを返す
紐づけたlambdaコードを実行せずにOpenAPIで定義されたダミーを返す
``` python
@ lambda_mock.get("/hello/{date}")
async def hello_get_mock(date: int):
    return 200
```
responsesの200もしくは2xxで最初に見つかったexampleを返します。
見つからない場合ステータスコードのみ返します。

### 一括設定
一部のAPIで入力値すり替えや、exampleを返すようにしたものの、すべて普通にlambdaコードを実行させるようにしたい場合
``` py
options.set(mode.api)
```

すべてのAPIでlambdaコードを使わずmockかexampleを返したい。設定してないものは{status:200}だけ返してくれればいい場合
```py
options.set(mode.mock)
```

すべてのAPIでlambdaコードを使わずmockかexampleのエラーを返したい。設定してないものは{status:400}だけ返してくれればいい場合
```py
options.set(mode.mock, status=400)
```
(以下、未実装)
## AWS-CDKを使っている場合
`cdk synth`をしてから`python -m sapimo run`

## SAMやCDKを使っていない場合
`sapimock init`
api_mock/config.yamlのダミーファイルが生成されるので編集する。
APIと実行ファイルを紐づける。

```yaml
paths:
  "/hello-world":             # API path
    get:                     # method：(post/get/put/delete)
      handler:
        "hello_world.app.lambda_handler" # python code path (dir.dir.file.func)
  "/hello/{date}":      # "date" is path parameter
    get:
      handler:
        "hello_get.app.lambda_handler"
      layer:               # if lambda uses same layer,
        - "my_layer/"      # you should set layer path
    post:
      handler:
        "hello_post.app.lambda_handler"
```

# 起動
`sapimock run  [-s ./swagger.yaml]`
-s でOpenAPIの定義ファイルを指定するとexampleなど一部情報を利用できる。



# sam local start-apiに比べ
## pros
### 起動が高速

### 変更が即反映

### s3, dynamoのmock込


## cons
### 複雑な構成だとconfig.yamlの手編集が必要
sapimockではtemplate.yamlをパースしてconfig.yamlを作成します。
基本的にAPIのパスと実行ファイルを紐づけるだけなのでシンプルなzipタイプのlambdaであれば問題なく生成できるのですが、少し複雑になると場合によってはconfig.yamlがおかしくなる可能性があります。
具体的にはImageタイプのラムダでdocker file内でいろいろやってたり、zipタイプでも別のとこからlayerを参照してたりとかの場合はうまくいきません。(未検証)
この場合はconfig.yamlを自分で編集してあげる必要があります。

### 実行環境はあらかじめ用意しておく必要がある。
sam localではコンテナで実行環境をまるっと用意してくれますが、sapimockではlambdaのコードはそのまま呼び出されるため、コード実行に必要な環境は用意して有効化しておく必要があります。
一つの仮想環境でまとめて開発、テストなどしてる場合はそのまま使えるので問題はないですが、lambdaごとに実行環境を別に用意して開発してる場合などはちょっと面倒かもしれません。

### lambdaごとにpythonのバージョンが違うと使えない
sam localではlambdaごとに環境が用意されますが、sapimockでは すべてのlambdaが同じ環境で動くため、pythonバージョン依存のコード多くある場合は利用が難しいです。そこまで厳密ではなければ基本的に新しいバージョンで環境を用意しておいて、バージョン差異が出る部分だけsam local で確認するという使い方になると思います。

### pythonじゃないと使えない。
lambdaがpythonじゃないと使えません。これは