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
    assert "_find_route_match" in gateway_handler

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


def test_generate_compose_config_uses_project_relative_build_paths(tmp_path):
    generator = _create_generator(tmp_path)
    generator.lambda_functions = [
        LambdaFunction(
            name="Hello_Get",
            handler="app.lambda_handler",
            runtime="python3.12",
            code_uri="lambda/hello",
            environment={},
            layers=[],
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
    assert lambda_build["dockerfile"] == "docker/lambda-runtime/Dockerfile"

    lambda_volumes = compose_config["services"]["lambda-hello-get"]["volumes"]
    assert "../lambda/hello:/var/task:rw" in lambda_volumes
    assert "../data/lambda-hello-get:/tmp/lambda:rw" in lambda_volumes
    assert ".:/workspace/api_mock:ro" in lambda_volumes

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
