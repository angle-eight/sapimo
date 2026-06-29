from pathlib import Path

from sapimo.docker.lambda_execution_logger import LambdaExecutionLogger
from sapimo.docker.log_reader import _parse_entries, read_snapshot


def test_lambda_execution_logger_writes_parseable_log_and_snapshot(
    tmp_path: Path,
    capsys,
):
    code_dir = tmp_path / "lambda" / "hello"
    code_dir.mkdir(parents=True)
    (code_dir / "app.py").write_text(
        "def lambda_handler(event, context):\n"
        "    return {'statusCode': 201, 'body': 'created'}\n",
        encoding="utf-8",
    )

    logger = LambdaExecutionLogger(tmp_path / "api_mock" / "log")
    log_file = logger.get_log_file("HelloFunction", code_dir, "app")
    logger.log_execution(
        log_file=log_file,
        function_name="HelloFunction",
        handler="app.lambda_handler",
        event={"path": "/hello"},
        result={"statusCode": 201, "body": "created"},
        captured_output="handler output\n",
        duration_ms=12.34,
    )

    entries = _parse_entries(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0].function_name == "HelloFunction"
    assert entries[0].status_code == 201
    assert entries[0].duration_ms == 12.3
    assert entries[0].output == "handler output"
    assert entries[0].error is None

    read_snapshot(tmp_path / "api_mock" / "log", None, tail=10)

    out = capsys.readouterr().out
    assert "HelloFunction" in out
    assert "201" in out
    assert "12.3ms" in out
    assert "handler output" in out


def test_lambda_execution_logger_rotates_when_code_hash_changes(tmp_path: Path):
    code_dir = tmp_path / "lambda" / "hello"
    code_dir.mkdir(parents=True)
    source_file = code_dir / "app.py"
    source_file.write_text("VALUE = 1\n", encoding="utf-8")
    logger = LambdaExecutionLogger(tmp_path / "api_mock" / "log")

    first = logger.get_log_file("HelloFunction", code_dir, "app")
    logger.log_execution(
        log_file=first,
        function_name="HelloFunction",
        handler="app.lambda_handler",
        event={},
        result={"statusCode": 200},
        captured_output="",
        duration_ms=1.0,
    )
    second = logger.get_log_file("HelloFunction", code_dir, "app")
    source_file.write_text("VALUE = 2\n", encoding="utf-8")
    third = logger.get_log_file("HelloFunction", code_dir, "app")

    assert second == first
    assert third != first
    assert first.name.endswith("_001.log")
    assert third.name.endswith("_002.log")


def test_log_reader_snapshot_sorts_entries_across_function_directories(
    tmp_path: Path,
    capsys,
):
    log_dir = tmp_path / "log"
    (log_dir / "B").mkdir(parents=True)
    (log_dir / "A").mkdir(parents=True)
    separator = "=" * 60
    (log_dir / "B" / "2026-01-01_001.log").write_text(
        "\n".join(
            [
                separator,
                "[2026-01-01 00:00:02.000] B (app.handler)",
                "Duration: 2.0ms",
                "----------------------------------------",
                "── RESPONSE ──",
                '{"statusCode": 202}',
                separator,
                "",
            ]
        ),
        encoding="utf-8",
    )
    (log_dir / "A" / "2026-01-01_001.log").write_text(
        "\n".join(
            [
                separator,
                "[2026-01-01 00:00:01.000] A (app.handler)",
                "Duration: 1.0ms",
                "----------------------------------------",
                "── OUTPUT ──",
                "first",
                "── RESPONSE ──",
                '{"statusCode": 200}',
                separator,
                "",
            ]
        ),
        encoding="utf-8",
    )

    read_snapshot(log_dir, None, tail=2)

    out = capsys.readouterr().out
    assert out.index("─── A ") < out.index("─── B ")
    assert "first" in out
    assert "200" in out
    assert "202" in out


def test_log_reader_snapshot_filters_function_name(tmp_path: Path, capsys):
    log_dir = tmp_path / "log"
    (log_dir / "A").mkdir(parents=True)
    (log_dir / "B").mkdir(parents=True)
    separator = "=" * 60
    for name in ("A", "B"):
        (log_dir / name / "2026-01-01_001.log").write_text(
            "\n".join(
                [
                    separator,
                    f"[2026-01-01 00:00:00.000] {name} (app.handler)",
                    "Duration: 1.0ms",
                    "----------------------------------------",
                    "── OUTPUT ──",
                    name,
                    separator,
                    "",
                ]
            ),
            encoding="utf-8",
        )

    read_snapshot(log_dir, "B", tail=10)

    out = capsys.readouterr().out
    assert "─── B " in out
    assert "─── A " not in out
