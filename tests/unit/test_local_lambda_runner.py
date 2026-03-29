from pathlib import Path
import asyncio

import pytest

from sapimo.docker.local_lambda_runner import LocalLambdaRunner


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_local_runner_executes_handler_and_restores_env(tmp_path: Path):
    code_dir = tmp_path / "lambda" / "hello"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "app.py").write_text(
        "import os\n"
        "def lambda_handler(event, context):\n"
        "    return {'statusCode': 200, 'body': os.getenv('TEST_ENV', 'missing')}\n",
        encoding="utf-8",
    )

    runner = LocalLambdaRunner(tmp_path)
    before_value = "original"
    import os

    os.environ["TEST_ENV"] = before_value

    route_info = {
        "handler": "app.lambda_handler",
        "code_uri": "lambda/hello",
        "environment": {"TEST_ENV": "from_lambda"},
        "layers": [],
    }

    result = await runner.execute(route_info, {"key": "value"})

    assert result["statusCode"] == 200
    assert result["body"] == "from_lambda"
    assert os.environ["TEST_ENV"] == before_value


@pytest.mark.anyio
async def test_local_runner_loads_layer_python_path(tmp_path: Path):
    code_dir = tmp_path / "lambda" / "hello"
    layer_python_dir = tmp_path / "layers" / "common" / "python"
    code_dir.mkdir(parents=True, exist_ok=True)
    layer_python_dir.mkdir(parents=True, exist_ok=True)

    (layer_python_dir / "shared_module.py").write_text(
        "VALUE = 'ok'\n", encoding="utf-8"
    )
    (code_dir / "app.py").write_text(
        "from shared_module import VALUE\n"
        "def lambda_handler(event, context):\n"
        "    return {'statusCode': 200, 'body': VALUE}\n",
        encoding="utf-8",
    )

    runner = LocalLambdaRunner(tmp_path)
    route_info = {
        "handler": "app.lambda_handler",
        "code_uri": "lambda/hello",
        "environment": {},
        "layers": ["layers/common"],
    }

    result = await runner.execute(route_info, {})

    assert result["statusCode"] == 200
    assert result["body"] == "ok"


@pytest.mark.anyio
async def test_local_runner_parallel_invocations_keep_env_isolated(tmp_path: Path):
    code_dir = tmp_path / "lambda" / "parallel"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "app.py").write_text(
        "import os\n"
        "import asyncio\n"
        "async def lambda_handler(event, context):\n"
        "    first = os.getenv('ROUTE_MARK')\n"
        "    await asyncio.sleep(0.01)\n"
        "    second = os.getenv('ROUTE_MARK')\n"
        "    return {'statusCode': 200, 'body': {'first': first, 'second': second}}\n",
        encoding="utf-8",
    )

    runner = LocalLambdaRunner(tmp_path)

    route_a = {
        "handler": "app.lambda_handler",
        "code_uri": "lambda/parallel",
        "environment": {"ROUTE_MARK": "A"},
        "layers": [],
    }
    route_b = {
        "handler": "app.lambda_handler",
        "code_uri": "lambda/parallel",
        "environment": {"ROUTE_MARK": "B"},
        "layers": [],
    }

    res_a, res_b = await asyncio.gather(
        runner.execute(route_a, {}), runner.execute(route_b, {})
    )

    assert res_a["body"]["first"] == "A"
    assert res_a["body"]["second"] == "A"
    assert res_b["body"]["first"] == "B"
    assert res_b["body"]["second"] == "B"
