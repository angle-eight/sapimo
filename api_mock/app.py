from sapimo.mock import api, change_input

# サンプルMock定義


@api.get("/test")
async def test_get_mock():
    """バリデーションのみでLambda実行"""
    return None  # Lambda実行にフォールバック


@api.get("/mock-test")
async def mock_test_get():
    """固定値を返すスタブ"""
    return {"message": "This is a mock response!", "source": "mock"}


@api.get("/override-test")
async def override_test_get():
    """入力すり替えでLambda実行"""
    return change_input(test_param="overridden_value")


# パラメータ検証のサンプル
@api.get("/hello/{user_id}")
async def hello_user_mock(user_id: int):
    """user_idをint検証してからLambda実行"""
    # user_idが整数でない場合は422エラー
    return None  # Lambda実行にフォールバック
