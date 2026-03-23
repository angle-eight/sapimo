"""
チケットB1：OpenAPI example 返却テスト
TC-OAS-001: responses.200.content.application/json.example を返す
TC-OAS-002: examples.*.value を返す
TC-OAS-003: 完全一致なし → 2xx フォールバック
TC-OAS-004: example 未定義 → ステータスのみ（body は None）
"""

import sys
from pathlib import Path
import pytest
import yaml
import tempfile
import os

# openapi_example_resolver を直接インポートできるよう、gateway ディレクトリを追加
gateway_dir = Path(__file__).resolve().parents[2] / "docker" / "gateway"
if str(gateway_dir) not in sys.path:
    sys.path.insert(0, str(gateway_dir))

from openapi_example_resolver import resolve_example


def _write_spec(tmp_path: Path, spec: dict) -> str:
    spec_file = tmp_path / "swagger.yaml"
    spec_file.write_text(yaml.dump(spec), encoding="utf-8")
    return str(spec_file)


def test_tc_oas_001_exact_status_example(tmp_path):
    """responses.200.content.application/json.example を返す"""
    spec = {
        "paths": {
            "/hello": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"example": {"message": "hello"}}
                            }
                        }
                    }
                }
            }
        }
    }
    spec_path = _write_spec(tmp_path, spec)
    content, status = resolve_example("/hello", "get", 200, spec_path=spec_path)
    assert status == 200
    assert content == {"message": "hello"}


def test_tc_oas_002_examples_value(tmp_path):
    """examples.*.value を返す"""
    spec = {
        "paths": {
            "/hello": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "examples": {"ex1": {"value": {"data": "sample"}}}
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    spec_path = _write_spec(tmp_path, spec)
    content, status = resolve_example("/hello", "get", 200, spec_path=spec_path)
    assert status == 200
    assert content == {"data": "sample"}


def test_tc_oas_003_fallback_2xx(tmp_path):
    """指定コードなし → 2xx フォールバック"""
    spec = {
        "paths": {
            "/hello": {
                "get": {
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {"example": {"created": True}}
                            }
                        }
                    }
                }
            }
        }
    }
    spec_path = _write_spec(tmp_path, spec)
    # 200 を指定しても 201 の example にフォールバック
    content, status = resolve_example("/hello", "get", 200, spec_path=spec_path)
    assert content == {"created": True}
    assert status == 200  # status は元の指定値


def test_tc_oas_004_no_example_returns_none(tmp_path):
    """example 未定義 → (None, status_code)"""
    spec = {"paths": {"/hello": {"get": {"responses": {"200": {"description": "OK"}}}}}}
    spec_path = _write_spec(tmp_path, spec)
    content, status = resolve_example("/hello", "get", 200, spec_path=spec_path)
    assert content is None
    assert status == 200
