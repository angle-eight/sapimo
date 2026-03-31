from pathlib import Path

import yaml

from sapimo.docker.compose_generator import DockerComposeGenerator, LambdaFunction


def _create_generator(tmp_path: Path) -> DockerComposeGenerator:
    api_mock_dir = tmp_path / "api_mock"
    api_mock_dir.mkdir(parents=True, exist_ok=True)
    config_file = api_mock_dir / "config.yaml"
    config_file.write_text("paths: {}\n", encoding="utf-8")
    return DockerComposeGenerator(config_file)


def test_ensure_docker_templates_copies_runtime_assets(tmp_path):
    generator = _create_generator(tmp_path)

    generator._ensure_docker_templates()

    docker_root = tmp_path / "api_mock" / "docker"
    assert (docker_root / "gateway" / "Dockerfile").exists()
    assert (docker_root / "aws_mock" / "Dockerfile").exists()
    assert (docker_root / "aws_mock" / "requirements.txt").exists()
    assert (docker_root / "lambda_runtime" / "Dockerfile").exists()

    # 実行中 sapimo パッケージが展開されること
    assert (docker_root / "sapimo" / "__init__.py").exists()

    gateway_dockerfile = (docker_root / "gateway" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "pip install --no-cache-dir sapimo" not in gateway_dockerfile
    assert "COPY api_mock/docker/sapimo/ /workspace/sapimo/" in gateway_dockerfile
    assert "COPY api_mock/docker/gateway/main.py /workspace/" in gateway_dockerfile

    gateway_main = (docker_root / "gateway" / "main.py").read_text(encoding="utf-8")
    assert "has_mock_definition" in gateway_main
    assert "_invoke_lambda_with_override" in gateway_main
    assert 'options.mode == "mock"' in gateway_main

    gateway_handler = (docker_root / "gateway" / "mock_handler.py").read_text(
        encoding="utf-8"
    )
    assert "reload_mock_definitions" in gateway_handler
    assert "has_mock_definition" in gateway_handler

    aws_mock_dockerfile = (docker_root / "aws_mock" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        "COPY api_mock/docker/aws_mock/aws_mock_server.py /workspace/"
        in aws_mock_dockerfile
    )

    aws_mock_requirements = (docker_root / "aws_mock" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    assert "python-jose[cryptography]>=3.5.0" in aws_mock_requirements

    aws_mock_server = (docker_root / "aws_mock" / "aws_mock_server.py").read_text(
        encoding="utf-8"
    )
    assert "add_event_handler(" not in aws_mock_server
    assert "lifespan=lifespan" in aws_mock_server

    lambda_runtime_dockerfile = (
        docker_root / "lambda_runtime" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "sitecustomize.py" not in lambda_runtime_dockerfile
    assert "sapimo_runtime_bootstrap" not in lambda_runtime_dockerfile


def test_generate_compose_config_uses_project_relative_build_paths(tmp_path):
    generator = _create_generator(tmp_path)
    layer_dir = tmp_path / "lambdafunc" / "libs"
    layer_dir.mkdir(parents=True, exist_ok=True)
    (layer_dir / "adaptor.py").write_text("x = 1\n", encoding="utf-8")

    generator.lambda_functions = [
        LambdaFunction(
            name="Hello_Get",
            handler="app.lambda_handler",
            runtime="python3.12",
            code_uri="lambda/hello",
            environment={},
            layers=["lambdafunc/libs"],
        )
    ]

    compose_config = generator.generate_compose_config()

    assert "version" not in compose_config
    assert "ipam" not in compose_config["networks"]["sapimo-network"]

    gateway_build = compose_config["services"]["sapimo-gateway"]["build"]
    assert gateway_build == {
        "context": "..",
        "dockerfile": "api_mock/docker/gateway/Dockerfile",
    }
    gateway_volumes = compose_config["services"]["sapimo-gateway"]["volumes"]
    assert ".:/workspace/api_mock:ro" in gateway_volumes

    aws_mock_build = compose_config["services"]["sapimo-aws-mock"]["build"]
    assert aws_mock_build == {
        "context": "..",
        "dockerfile": "api_mock/docker/aws_mock/Dockerfile",
    }

    lambda_build = compose_config["services"]["lambda-hello-get"]["build"]
    assert lambda_build["context"] == ".."
    assert lambda_build["dockerfile"] == "api_mock/docker/lambda_runtime/Dockerfile"
    assert lambda_build["args"]["PYTHON_VERSION"] == "3.12"

    lambda_command = compose_config["services"]["lambda-hello-get"]["command"]
    assert lambda_command == "app.lambda_handler"

    lambda_volumes = compose_config["services"]["lambda-hello-get"]["volumes"]
    assert "../lambda/hello:/var/task:rw" in lambda_volumes
    assert "../data/lambda-hello-get:/tmp/lambda:rw" in lambda_volumes
    assert ".:/workspace/api_mock:ro" in lambda_volumes
    assert "../lambdafunc/libs:/opt/sapimo_layers/0:ro" in lambda_volumes

    lambda_env = compose_config["services"]["lambda-hello-get"]["environment"]
    assert "/opt/sapimo_layers/0" in lambda_env["PYTHONPATH"]
    assert "/opt/sapimo_layers/0/python" in lambda_env["PYTHONPATH"]
    assert lambda_env["AWS_REGION"] == "us-east-1"
    assert lambda_env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert lambda_env["AWS_MOCK_ENDPOINT"] == "http://sapimo-aws-mock:4566"
    assert lambda_env["AWS_ENDPOINT_URL"] == "http://sapimo-aws-mock:4566"
    assert lambda_env["AWS_ACCESS_KEY_ID"] == "testing"
    assert lambda_env["AWS_SECRET_ACCESS_KEY"] == "testing"
    assert lambda_env["AWS_SESSION_TOKEN"] == "testing"
    assert lambda_env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert lambda_env["AWS_LAMBDA_FUNCTION_VERSION"] == "$$LATEST"

    aws_volumes = compose_config["services"]["sapimo-aws-mock"]["volumes"]
    assert "../data:/data:rw" in aws_volumes


def test_generate_compose_file_writes_yaml_and_deploys_templates(tmp_path, monkeypatch):
    generator = _create_generator(tmp_path)

    monkeypatch.setattr(generator, "parse_template", lambda: None)
    monkeypatch.setattr(
        generator,
        "generate_compose_config",
        lambda: {
            "services": {"sapimo-gateway": {"image": "sapimo-gateway:latest"}},
            "networks": {"sapimo-network": {"driver": "bridge"}},
            "volumes": {"sapimo-data": {}},
        },
    )

    output_path = generator.generate_compose_file()

    assert output_path.exists()
    loaded = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert loaded["services"]["sapimo-gateway"]["image"] == "sapimo-gateway:latest"

    docker_root = tmp_path / "api_mock" / "docker"
    assert (docker_root / "gateway" / "main.py").exists()
    assert (docker_root / "aws_mock" / "aws_mock_server.py").exists()
    assert (docker_root / "sapimo" / "docker" / "mock_manager.py").exists()


def test_parse_template_reads_existing_config_yaml(tmp_path):
    generator = _create_generator(tmp_path)
    config_file = tmp_path / "api_mock" / "config.yaml"
    config_file.write_text(
        """
paths:
    /simple-trades:
        post:
            Properties:
                CodeUri: lambdafunc/simple_trades_post
                Handler: index.handler
                Runtime: python3.9
s3:
    test-bucket:
        BucketName: test-bucket
""".strip()
        + "\n",
        encoding="utf-8",
    )

    generator.parse_template()

    assert len(generator.lambda_functions) == 1
    assert generator.lambda_functions[0].name == "simple-trades_post"
    assert generator.lambda_functions[0].handler == "index.handler"
    assert generator.lambda_functions[0].code_uri == "lambdafunc/simple_trades_post"
    assert "s3" in generator.aws_resources


def test_lambda_env_region_prefers_user_default_region(tmp_path):
    generator = _create_generator(tmp_path)
    generator.lambda_functions = [
        LambdaFunction(
            name="Region_Test_Post",
            handler="index.handler",
            runtime="python3.9",
            code_uri="lambda/region_test",
            environment={"AWS_DEFAULT_REGION": "ap-northeast-1"},
            layers=[],
        )
    ]

    compose_config = generator.generate_compose_config()
    env = compose_config["services"]["lambda-region-test-post"]["environment"]

    assert env["AWS_DEFAULT_REGION"] == "ap-northeast-1"
    assert env["AWS_REGION"] == "ap-northeast-1"


def test_lambda_env_respects_user_supplied_mock_endpoint(tmp_path):
    generator = _create_generator(tmp_path)
    generator.lambda_functions = [
        LambdaFunction(
            name="Endpoint_Test_Post",
            handler="index.handler",
            runtime="python3.9",
            code_uri="lambda/endpoint_test",
            environment={
                "AWS_MOCK_ENDPOINT": "http://custom-mock:4566",
                "AWS_ENDPOINT_URL": "http://custom-endpoint:4566",
            },
            layers=[],
        )
    ]

    compose_config = generator.generate_compose_config()
    env = compose_config["services"]["lambda-endpoint-test-post"]["environment"]

    assert env["AWS_MOCK_ENDPOINT"] == "http://custom-mock:4566"
    assert env["AWS_ENDPOINT_URL"] == "http://custom-endpoint:4566"
