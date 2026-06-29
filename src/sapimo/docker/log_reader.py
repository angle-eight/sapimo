"""Lambda 実行ログをホスト側から読み取り・フォロー表示する。

LambdaExecutionLogger が api_mock/log/{function_name}/*.log に書いた
実行エントリを読み取り表示する。

Lambda の実行は asyncio.Lock により常に直列化されているため、
同時に複数のエントリが書き込まれることはない。
この前提に基づき、表示は「関数名ヘッダー + 内容」の形式を採る。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_SEPARATOR = "=" * 60
_HEADER_WIDTH = 60


@dataclass
class LogEntry:
    timestamp: datetime
    function_name: str
    duration_ms: float
    status_code: int | None
    output: str
    error: str | None

    def format_block(self) -> str:
        """1実行を見やすいブロック形式で返す。

        ─── FunctionName ─── 2026-04-09 10:23:45 │ 200 │ 12.3ms
        <output lines>
        """
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        duration = f"{self.duration_ms:.1f}ms"
        if self.error:
            status = "ERROR"
        elif self.status_code is not None:
            status = str(self.status_code)
        else:
            status = "-"

        suffix = f" {ts} │ {status} │ {duration}"
        name_part = f"─── {self.function_name} "
        fill = max(1, _HEADER_WIDTH - len(name_part) - len(suffix))
        header = name_part + "─" * fill + suffix

        lines = [header]

        if self.output.strip():
            for line in self.output.rstrip("\n").splitlines():
                lines.append(line)

        if self.error:
            for line in self.error.rstrip("\n").splitlines():
                lines.append(line)

        return "\n".join(lines)


def _parse_entries(text: str) -> list[LogEntry]:
    """ログテキストから LogEntry のリストを生成する。"""
    entries: list[LogEntry] = []
    for block in text.split(_SEPARATOR + "\n"):
        block = block.strip()
        if not block:
            continue
        entry = _parse_block(block)
        if entry is not None:
            entries.append(entry)
    return entries


def _parse_block(block: str) -> LogEntry | None:
    """1実行ブロックを LogEntry に変換する。ヘッダーが不正なら None。"""
    lines = block.splitlines()
    if not lines:
        return None

    # 1行目: [timestamp] function_name (handler)
    header = lines[0]
    if not (header.startswith("[") and "] " in header):
        return None

    try:
        ts_end = header.index("]")
        ts_str = header[1:ts_end]
        timestamp = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
        rest = header[ts_end + 2 :]
    except (ValueError, IndexError):
        return None

    # "function_name (handler)" を分解（function_name のみ使用）
    if " (" in rest and rest.endswith(")"):
        function_name = rest[: rest.rindex(" (")]
    else:
        function_name = rest

    # 2行目: Duration: X.Xms
    duration_ms = 0.0
    if len(lines) > 1 and lines[1].startswith("Duration: "):
        try:
            duration_ms = float(lines[1].removeprefix("Duration: ").removesuffix("ms"))
        except ValueError:
            pass

    # セクション分解
    output = ""
    error = None
    status_code = None
    section: str | None = None
    section_lines: list[str] = []

    def _flush_section() -> None:
        nonlocal output, error, status_code
        if section == "RESPONSE":
            try:
                resp = json.loads("\n".join(section_lines))
                if isinstance(resp, dict):
                    status_code = resp.get("statusCode")
            except (ValueError, TypeError):
                pass
        elif section == "OUTPUT":
            output = "\n".join(section_lines)
        elif section == "ERROR":
            error = "\n".join(section_lines)

    for line in lines[3:]:  # 0=header, 1=Duration, 2=区切り線(─...)
        if line in ("── EVENT ──", "── OUTPUT ──", "── ERROR ──", "── RESPONSE ──"):
            _flush_section()
            section_lines = []
            section = line.strip("─ ")
        elif section is not None:
            section_lines.append(line)

    _flush_section()

    return LogEntry(
        timestamp=timestamp,
        function_name=function_name,
        duration_ms=duration_ms,
        status_code=status_code,
        output=output,
        error=error,
    )


def _latest_log_file(func_dir: Path) -> Path | None:
    """関数ログディレクトリ内の最新ファイルを返す。"""
    files = sorted(func_dir.glob("*.log"), key=lambda p: p.name)
    return files[-1] if files else None


def _iter_function_dirs(log_dir: Path, function_name: str | None) -> list[Path]:
    """監視対象の関数ディレクトリ一覧を返す。"""
    if not log_dir.exists():
        return []
    if function_name:
        target = log_dir / function_name
        return [target] if target.is_dir() else []
    return [d for d in log_dir.iterdir() if d.is_dir()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_snapshot(log_dir: Path, function_name: str | None, tail: int) -> None:
    """ログを静的に読み取り、タイムスタンプ順で末尾 tail 件を表示する。

    複数関数ディレクトリから読み取るためタイムスタンプソートを行う。
    Lambda 実行は直列化されているが、各関数のログファイルは分かれているため
    マージ時にソートが必要になる。
    """
    func_dirs = _iter_function_dirs(log_dir, function_name)
    if not func_dirs:
        _no_log_message(log_dir, function_name)
        return

    all_entries: list[LogEntry] = []
    for func_dir in func_dirs:
        log_file = _latest_log_file(func_dir)
        if log_file is None:
            continue
        all_entries.extend(_parse_entries(log_file.read_text(encoding="utf-8")))

    if not all_entries:
        _no_log_message(log_dir, function_name)
        return

    all_entries.sort(key=lambda e: e.timestamp)
    for entry in all_entries[-tail:]:
        print(entry.format_block())
        print()


def follow(
    log_dir: Path, function_name: str | None, poll_interval: float = 0.2
) -> None:
    """ログを継続監視し、新しい実行エントリをリアルタイムで表示する。

    Lambda 実行は asyncio.Lock で直列化されているため、
    エントリが同時に複数書き込まれることはない。
    複数関数ディレクトリを監視する場合でも実行順はタイムスタンプに反映される。

    poll_interval 秒ごとに全ファイルをチェックし、
    1サイクルで検出した新規エントリをタイムスタンプ順に出力する。
    ファイルローテーション（コード変更時）も自動追跡する。
    """
    # {func_dir: (current_file, バイト読み取り位置)}
    file_positions: dict[Path, tuple[Path, int]] = {}

    print("Watching Lambda logs (Ctrl+C to stop)...\n")

    try:
        while True:
            func_dirs = _iter_function_dirs(log_dir, function_name)
            new_entries: list[LogEntry] = []

            for func_dir in func_dirs:
                log_file = _latest_log_file(func_dir)
                if log_file is None:
                    continue

                current_file, pos = file_positions.get(func_dir, (None, 0))

                # ファイルローテーション検出: 新ファイルは先頭から読む
                if log_file != current_file:
                    pos = 0

                try:
                    file_size = log_file.stat().st_size
                except FileNotFoundError:
                    file_positions.pop(func_dir, None)
                    continue

                if file_size <= pos:
                    file_positions[func_dir] = (log_file, pos)
                    continue

                with log_file.open("r", encoding="utf-8") as f:
                    f.seek(pos)
                    new_text = f.read()

                # 末尾が "===...\n\n" で終わる完全なエントリだけを処理する。
                # 書き込み中の不完全ブロックを読まないようにするため、
                # 最後の完全エントリ末尾まで読み取り位置を進める。
                last_sep_end = new_text.rfind(_SEPARATOR + "\n\n")
                if last_sep_end == -1:
                    # 完全なエントリがまだない: 位置を据え置いて次回再試行
                    file_positions[func_dir] = (log_file, pos)
                    continue

                complete_text = new_text[: last_sep_end + len(_SEPARATOR) + 2]
                file_positions[func_dir] = (
                    log_file,
                    pos + len(complete_text.encode("utf-8")),
                )
                new_entries.extend(_parse_entries(complete_text))

            if new_entries:
                # 直列実行前提でも複数関数ディレクトリ間の順序を保証するためソート
                new_entries.sort(key=lambda e: e.timestamp)
                for entry in new_entries:
                    print(entry.format_block())
                    print()

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        pass


def _no_log_message(log_dir: Path, function_name: str | None) -> None:
    if function_name:
        print(f"No logs found for function '{function_name}' in {log_dir}")
    else:
        print(f"No Lambda logs found in {log_dir}")
    print("Make sure the server is running and has handled at least one request.")
