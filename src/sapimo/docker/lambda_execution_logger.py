"""Lambda 実行ログを関数ごとにファイル出力する。

ログファイルは api_mock/log/{function_name}/ に配置され、
以下のタイミングで新しいファイルにローテーションする:
  - Lambda ソースコードに変更があったとき
  - 日付が変わったとき
"""

from __future__ import annotations

import hashlib
import json
import sys
import io
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


class LambdaExecutionLogger:
    """関数ごとのLambda実行ログ管理。コード変更・日付変更でファイルをローテーション。"""

    def __init__(self, log_base_dir: Path):
        self.log_base_dir = log_base_dir
        self._current_files: dict[str, Path] = {}
        self._current_dates: dict[str, str] = {}
        self._code_hashes: dict[str, str] = {}

    def _compute_code_hash(self, code_path: Path, module_name: str) -> str:
        """Lambda エントリポイントのソースファイルハッシュを計算。"""
        source_file = code_path / (module_name.replace(".", "/") + ".py")
        if source_file.exists():
            return hashlib.md5(source_file.read_bytes()).hexdigest()
        return ""

    def _needs_rotation(self, function_name: str, code_hash: str) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        if function_name not in self._current_dates:
            return True
        if self._current_dates[function_name] != today:
            return True
        if self._code_hashes.get(function_name) != code_hash:
            return True
        return False

    def _rotate(self, function_name: str, code_hash: str) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        func_dir = self.log_base_dir / function_name
        func_dir.mkdir(parents=True, exist_ok=True)

        existing = sorted(func_dir.glob(f"{today}_*.log"))
        seq = len(existing) + 1
        log_file = func_dir / f"{today}_{seq:03d}.log"

        self._current_files[function_name] = log_file
        self._current_dates[function_name] = today
        self._code_hashes[function_name] = code_hash

        return log_file

    def get_log_file(
        self, function_name: str, code_path: Path, module_name: str
    ) -> Path:
        """現在のログファイルパスを取得。必要ならローテーションする。"""
        code_hash = self._compute_code_hash(code_path, module_name)
        if self._needs_rotation(function_name, code_hash):
            return self._rotate(function_name, code_hash)
        return self._current_files[function_name]

    @contextmanager
    def capture_stdout(self):
        """stdout/stderr をキャプチャするコンテキストマネージャ。"""
        captured = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = captured
        sys.stderr = captured
        try:
            yield captured
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def log_execution(
        self,
        log_file: Path,
        function_name: str,
        handler: str,
        event: dict[str, Any],
        result: dict[str, Any] | None,
        captured_output: str,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        """実行結果をログファイルに追記。"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{'=' * 60}\n")
            f.write(f"[{timestamp}] {function_name} ({handler})\n")
            f.write(f"Duration: {duration_ms:.1f}ms\n")
            f.write(f"{'─' * 40}\n")

            f.write("── EVENT ──\n")
            f.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")

            if captured_output.strip():
                f.write("── OUTPUT ──\n")
                f.write(captured_output)
                if not captured_output.endswith("\n"):
                    f.write("\n")

            if error:
                f.write("── ERROR ──\n")
                f.write(error)
                if not error.endswith("\n"):
                    f.write("\n")
            elif result is not None:
                f.write("── RESPONSE ──\n")
                f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

            f.write(f"{'=' * 60}\n\n")
