"""
OpenAPI example resolver
api_mock/swagger.yaml または api_mock/openapi.yaml から
指定パス・ステータスコードの example を解決する
"""

from pathlib import Path
from typing import Any, Optional
import yaml


CANDIDATE_PATHS = [
    "/workspace/api_mock/swagger.yaml",
    "/workspace/api_mock/openapi.yaml",
    # テスト時のパス（プロジェクトルート相対）
    str(Path(__file__).resolve().parents[2] / "api_mock" / "swagger.yaml"),
    str(Path(__file__).resolve().parents[2] / "api_mock" / "openapi.yaml"),
]


def _load_spec(spec_path: Optional[str] = None) -> Optional[dict]:
    """OpenAPI定義ファイルを読み込む"""
    candidates = [spec_path] if spec_path else CANDIDATE_PATHS
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            with open(candidate, encoding="utf-8") as f:
                return yaml.safe_load(f)
    return None


def resolve_example(
    api_path: str,
    method: str,
    status_code: int,
    spec_path: Optional[str] = None,
) -> tuple[Optional[Any], int]:
    """
    OpenAPI定義から example を解決して返す。
    Returns: (example_content, actual_status_code)
    example_content が None の場合は定義が見つからなかった。
    """
    spec = _load_spec(spec_path)
    if spec is None:
        return None, status_code

    paths = spec.get("paths", {})
    path_item = paths.get(api_path)
    if path_item is None:
        return None, status_code

    operation = path_item.get(method.lower())
    if operation is None:
        return None, status_code

    responses = operation.get("responses", {})

    def _extract_from_response(resp_obj: dict) -> Optional[Any]:
        """responses エントリから application/json example を探す"""
        if resp_obj is None:
            return None
        content = resp_obj.get("content", {})
        json_content = content.get("application/json", {})

        # 優先1: .example
        example = json_content.get("example")
        if example is not None:
            return example

        # 優先2: .examples.*.value の先頭
        examples = json_content.get("examples", {})
        for ex_obj in examples.values():
            val = ex_obj.get("value")
            if val is not None:
                return val

        return None

    # 優先1: 完全一致ステータスコード
    exact = responses.get(str(status_code)) or responses.get(status_code)
    if exact:
        result = _extract_from_response(exact)
        if result is not None:
            return result, status_code

    # 優先2: 2xx フォールバック
    for code_str, resp_obj in responses.items():
        code_str = str(code_str)
        if code_str.startswith("2") or code_str.lower() in ["2xx", "default"]:
            result = _extract_from_response(resp_obj)
            if result is not None:
                return result, status_code

    # 見つからなかった → None を返す（呼び出し側でステータスのみ返す）
    return None, status_code
