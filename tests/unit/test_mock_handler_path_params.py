"""
MockRouter (FastAPI APIRouter サブクラス) パラメータ解決テスト

TC-MOCK-001: パスパラメータ int 型変換 (FastAPI 経由)
TC-MOCK-002: パスパラメータ型エラー → 422
TC-MOCK-003: クエリパラメータ str/int 解決
TC-MOCK-004: 必須クエリパラメータ欠落 → 422
TC-MOCK-005: デフォルト値付きクエリパラメータ
TC-MOCK-006: Pydantic ボディバリデーション
TC-MOCK-007: None 戻り値のキャプチャ
TC-MOCK-008: InputOverride 戻り値のキャプチャ
TC-MOCK-009: int (ステータスコード) 戻り値のキャプチャ
TC-MOCK-010: has_mock_definition ルート一致/不一致
"""

import sys
from pathlib import Path

import pytest

src_dir = Path(__file__).resolve().parents[2] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from starlette.routing import Match

from sapimo.mock.api import MockRouter, InputOverride, change_input


# ---------- fixtures ----------


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _build_mock_app():
    """テスト用 MockRouter + FastAPI アプリを構築"""
    router = MockRouter()

    @router.get("/users/{user_id}")
    async def get_user(user_id: int):
        return {"id": user_id, "name": "test"}

    @router.get("/search")
    async def search(q: str, limit: int = 10):
        return {"query": q, "limit": limit}

    @router.get("/passthrough")
    async def passthrough():
        return None

    @router.get("/override")
    async def override():
        return change_input(key="value")

    @router.get("/status")
    async def status_code_mock():
        return 200

    class ItemCreate(BaseModel):
        name: str
        price: float

    @router.post("/items")
    async def create_item(item: ItemCreate):
        return {"name": item.name, "price": item.price}

    app = FastAPI()
    app.include_router(router)
    return router, app


# ---------- パスパラメータ ----------


@pytest.mark.anyio
async def test_tc_mock_001_path_param_int():
    """TC-MOCK-001: パスパラメータ int 型変換"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/users/42")
    assert resp.status_code == 200
    captured, result = router.get_captured_result()
    assert captured is True
    assert result == {"id": 42, "name": "test"}


@pytest.mark.anyio
async def test_tc_mock_002_path_param_type_error():
    """TC-MOCK-002: パスパラメータ型エラー → 422"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/users/abc")
    assert resp.status_code == 422


# ---------- クエリパラメータ ----------


@pytest.mark.anyio
async def test_tc_mock_003_query_params():
    """TC-MOCK-003: クエリパラメータ str/int 解決"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/search?q=hello&limit=25")
    assert resp.status_code == 200
    captured, result = router.get_captured_result()
    assert captured is True
    assert result == {"query": "hello", "limit": 25}


@pytest.mark.anyio
async def test_tc_mock_004_required_query_param_missing():
    """TC-MOCK-004: 必須クエリパラメータ欠落 → 422"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/search")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_tc_mock_005_default_query_param():
    """TC-MOCK-005: デフォルト値付きクエリパラメータはデフォルト値が使われる"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/search?q=hello")
    assert resp.status_code == 200
    captured, result = router.get_captured_result()
    assert captured is True
    assert result == {"query": "hello", "limit": 10}


# ---------- Pydantic ボディバリデーション ----------


@pytest.mark.anyio
async def test_tc_mock_006_pydantic_body():
    """TC-MOCK-006: Pydantic ボディバリデーション"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/items", json={"name": "Widget", "price": 9.99})
    assert resp.status_code == 200
    captured, result = router.get_captured_result()
    assert captured is True
    assert result == {"name": "Widget", "price": 9.99}


# ---------- 戻り値キャプチャ ----------


@pytest.mark.anyio
async def test_tc_mock_007_none_return():
    """TC-MOCK-007: None 戻り値のキャプチャ"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/passthrough")
    captured, result = router.get_captured_result()
    assert captured is True
    assert result is None


@pytest.mark.anyio
async def test_tc_mock_008_input_override_return():
    """TC-MOCK-008: InputOverride 戻り値のキャプチャ"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/override")
    captured, result = router.get_captured_result()
    assert captured is True
    assert isinstance(result, InputOverride)
    assert result.data == {"key": "value"}


@pytest.mark.anyio
async def test_tc_mock_009_int_status_return():
    """TC-MOCK-009: int (ステータスコード) 戻り値のキャプチャ"""
    router, app = _build_mock_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/status")
    captured, result = router.get_captured_result()
    assert captured is True
    assert result == 200
    assert isinstance(result, int)


# ---------- has_mock_definition 相当のルートマッチング ----------


def test_tc_mock_010_route_matching():
    """TC-MOCK-010: 登録済みパスは Match.FULL、未登録パスは不一致"""
    _, app = _build_mock_app()

    # 登録済みパス: GET /users/42
    scope_match = {"type": "http", "path": "/users/42", "method": "GET"}
    found = any(route.matches(scope_match)[0] == Match.FULL for route in app.routes)
    assert found

    # 未登録パス
    scope_no_match = {"type": "http", "path": "/nonexistent", "method": "GET"}
    found = any(route.matches(scope_no_match)[0] == Match.FULL for route in app.routes)
    assert not found

    # メソッド不一致: POST /users/42 (GET のみ登録)
    scope_wrong_method = {"type": "http", "path": "/users/42", "method": "POST"}
    found = any(
        route.matches(scope_wrong_method)[0] == Match.FULL for route in app.routes
    )
    assert not found
