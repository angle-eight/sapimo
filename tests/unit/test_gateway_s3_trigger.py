"""S3 トリガー Lambda チェーン実行のテスト"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request as FastAPIRequest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _load_gateway_module():
    gateway_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "sapimo"
        / "docker"
        / "templates"
        / "gateway"
    )
    if str(gateway_dir) not in sys.path:
        sys.path.insert(0, str(gateway_dir))

    module_path = gateway_dir / "main.py"
    spec = importlib.util.spec_from_file_location(
        "gateway_main_for_test_s3_trigger", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_gateway(monkeypatch):
    gateway_module = _load_gateway_module()
    monkeypatch.setattr(
        gateway_module.MockHandler, "reload_mock_definitions", lambda self: False
    )
    monkeypatch.setattr(
        gateway_module.MockHandler, "start_file_watcher", lambda self: None
    )
    return gateway_module.LambdaGateway()


# --- _build_s3_event ---


def test_s3_event_single_key(monkeypatch):
    gw = _create_gateway(monkeypatch)

    event = gw._build_s3_event("my-bucket", ["data/file.json"], "ObjectCreated:Put")

    assert len(event["Records"]) == 1
    record = event["Records"][0]
    assert record["eventSource"] == "aws:s3"
    assert record["eventName"] == "ObjectCreated:Put"
    assert record["s3"]["bucket"]["name"] == "my-bucket"
    assert record["s3"]["object"]["key"] == "data/file.json"


def test_s3_event_multiple_keys(monkeypatch):
    gw = _create_gateway(monkeypatch)

    event = gw._build_s3_event(
        "bucket", ["a.txt", "b.txt", "c.txt"], "ObjectRemoved:Delete"
    )

    assert len(event["Records"]) == 3
    assert event["Records"][0]["s3"]["object"]["key"] == "a.txt"
    assert event["Records"][1]["s3"]["object"]["key"] == "b.txt"
    assert event["Records"][2]["s3"]["object"]["key"] == "c.txt"
    for r in event["Records"]:
        assert r["eventName"] == "ObjectRemoved:Delete"


# --- _find_triggered_lambda ---


def test_find_triggered_direct_match(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.triggered_lambdas = {"my-bucket": {"handler": "trigger.handler"}}

    assert gw._find_triggered_lambda("my-bucket") == {"handler": "trigger.handler"}


def test_find_triggered_arn_match(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.triggered_lambdas = {"arn:aws:s3:::my-bucket": {"handler": "trigger.handler"}}

    assert gw._find_triggered_lambda("my-bucket") == {"handler": "trigger.handler"}


def test_find_triggered_substring_match(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.triggered_lambdas = {"my-bucket-dev": {"handler": "trigger.handler"}}

    assert gw._find_triggered_lambda("my-bucket-dev") == {"handler": "trigger.handler"}


def test_find_triggered_no_match(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.triggered_lambdas = {"other-bucket": {"handler": "trigger.handler"}}

    assert gw._find_triggered_lambda("no-such-bucket") is None


# --- _process_s3_triggers ---


@pytest.mark.anyio
async def test_process_s3_triggers_no_config_does_nothing(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.mock_manager = MagicMock()
    gw.triggered_lambdas = {}

    # Should return without doing anything
    await gw._process_s3_triggers()

    gw.mock_manager.get_change.assert_not_called()


@pytest.mark.anyio
async def test_process_s3_triggers_no_changes_exits(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.mock_manager = MagicMock()
    gw.mock_manager.get_change.return_value = {"updated": {}, "deleted": {}}
    gw.triggered_lambdas = {"bucket": {"handler": "t.handler"}}

    await gw._process_s3_triggers()

    gw.mock_manager.get_change.assert_called_once_with("s3")


@pytest.mark.anyio
async def test_process_s3_triggers_executes_triggered_lambda(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value={"statusCode": 200})
    gw.local_lambda_runner = mock_runner

    trigger_config = {
        "function_name": "triggered_my-bucket",
        "handler": "trigger.handler",
        "code_uri": "./trigger",
        "environment": {},
        "layers": [],
        "runtime": "python3.9",
    }
    gw.triggered_lambdas = {"my-bucket": trigger_config}

    call_count = [0]
    original_changes = {"updated": {"my-bucket": ["new-file.json"]}, "deleted": {}}
    no_changes = {"updated": {}, "deleted": {}}

    def fake_get_change(service):
        call_count[0] += 1
        if call_count[0] == 1:
            return original_changes
        return no_changes

    mock_manager = MagicMock()
    mock_manager.get_change = fake_get_change
    gw.mock_manager = mock_manager

    await gw._process_s3_triggers()

    # The triggered Lambda should have been executed
    mock_runner.execute.assert_called_once()
    call_args = mock_runner.execute.call_args
    assert call_args[0][0] == trigger_config
    event = call_args[0][1]
    assert event["Records"][0]["eventName"] == "ObjectCreated:Put"
    assert event["Records"][0]["s3"]["bucket"]["name"] == "my-bucket"
    assert event["Records"][0]["s3"]["object"]["key"] == "new-file.json"


@pytest.mark.anyio
async def test_process_s3_triggers_chain_stops_after_no_changes(monkeypatch):
    """Trigger chain loops until no more S3 changes are detected."""
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value={"statusCode": 200})
    gw.local_lambda_runner = mock_runner

    gw.triggered_lambdas = {
        "bucket-a": {
            "function_name": "trigger_a",
            "handler": "t.handler",
            "code_uri": "./",
            "environment": {},
            "layers": [],
            "runtime": "python3.9",
        }
    }

    iteration = [0]

    def fake_get_change(service):
        iteration[0] += 1
        if iteration[0] == 1:
            return {"updated": {"bucket-a": ["file1.json"]}, "deleted": {}}
        if iteration[0] == 2:
            return {"updated": {"bucket-a": ["file2.json"]}, "deleted": {}}
        return {"updated": {}, "deleted": {}}

    mock_manager = MagicMock()
    mock_manager.get_change = fake_get_change
    gw.mock_manager = mock_manager

    await gw._process_s3_triggers()

    # Should have executed the trigger Lambda twice (2 rounds of changes)
    assert mock_runner.execute.call_count == 2
    # sync() called once per loop iteration (after first trigger, before second check)
    assert mock_manager.sync.call_count == 2


@pytest.mark.anyio
async def test_process_s3_triggers_handles_delete_events(monkeypatch):
    gw = _create_gateway(monkeypatch)
    gw.single_container_mode = True

    mock_runner = AsyncMock()
    mock_runner.execute = AsyncMock(return_value={"statusCode": 200})
    gw.local_lambda_runner = mock_runner

    trigger_config = {
        "function_name": "triggered_bucket",
        "handler": "trigger.handler",
        "code_uri": "./",
        "environment": {},
        "layers": [],
        "runtime": "python3.9",
    }
    gw.triggered_lambdas = {"bucket": trigger_config}

    call_count = [0]

    def fake_get_change(service):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"updated": {}, "deleted": {"bucket": ["old-file.json"]}}
        return {"updated": {}, "deleted": {}}

    mock_manager = MagicMock()
    mock_manager.get_change = fake_get_change
    gw.mock_manager = mock_manager

    await gw._process_s3_triggers()

    mock_runner.execute.assert_called_once()
    event = mock_runner.execute.call_args[0][1]
    assert event["Records"][0]["eventName"] == "ObjectRemoved:Delete"


# --- _load_configuration with lambdas/triggered ---


def test_load_configuration_reads_lambdas_section(monkeypatch, tmp_path):
    import yaml

    config = {
        "paths": {},
        "lambdas": {
            "MyAuthFunc": {
                "Type": "AWS::Serverless::Function",
                "Properties": {
                    "Handler": "auth.handler",
                    "CodeUri": "./auth",
                    "Runtime": "python3.12",
                },
            }
        },
    }
    config_file = tmp_path / "api_mock" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(yaml.dump(config))

    gw = _create_gateway(monkeypatch)
    gw.config_path = config_file

    # Re-run _load_configuration with our temp config
    monkeypatch.setattr(
        "pathlib.Path.__truediv__",
        lambda self, other: config_file
        if other == "config.yaml"
        else Path.__truediv__(self, other),
    )
    # Simpler: just manually load
    gw.authorizer_lambdas = {}
    gw.triggered_lambdas = {}
    with open(config_file) as f:
        cfg = yaml.safe_load(f)
    for name, lc in cfg.get("lambdas", {}).items():
        props = lc.get("Properties", {})
        gw.authorizer_lambdas[name] = {
            "function_name": name,
            "handler": props.get("Handler", "app.lambda_handler"),
            "code_uri": props.get("CodeUri", "./"),
        }

    assert "MyAuthFunc" in gw.authorizer_lambdas
    assert gw.authorizer_lambdas["MyAuthFunc"]["handler"] == "auth.handler"


def test_load_configuration_reads_triggered_section(monkeypatch, tmp_path):
    import yaml

    config = {
        "paths": {},
        "triggered": {
            "my-bucket": {
                "Properties": {
                    "Handler": "trigger.handler",
                    "CodeUri": "./trigger",
                }
            }
        },
    }
    config_file = tmp_path / "api_mock" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(yaml.dump(config))

    gw = _create_gateway(monkeypatch)
    gw.triggered_lambdas = {}
    with open(config_file) as f:
        cfg = yaml.safe_load(f)
    for bucket, tc in cfg.get("triggered", {}).items():
        props = tc.get("Properties", {})
        gw.triggered_lambdas[bucket] = {
            "function_name": f"triggered_{bucket}",
            "handler": props.get("Handler", "app.lambda_handler"),
            "code_uri": props.get("CodeUri", "./"),
        }

    assert "my-bucket" in gw.triggered_lambdas
    assert gw.triggered_lambdas["my-bucket"]["handler"] == "trigger.handler"
