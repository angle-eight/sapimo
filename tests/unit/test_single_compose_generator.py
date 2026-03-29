from pathlib import Path

import yaml

from sapimo.docker.single_compose_generator import SingleContainerComposeGenerator


def test_generate_single_compose_file(tmp_path: Path):
    api_mock_dir = tmp_path / "api_mock"
    api_mock_dir.mkdir(parents=True, exist_ok=True)
    config_file = api_mock_dir / "config.yaml"
    config_file.write_text("paths: {}\n", encoding="utf-8")

    generator = SingleContainerComposeGenerator(config_file)
    output_path = generator.generate_compose_file()

    assert output_path.exists()

    compose = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert set(compose["services"].keys()) == {"sapimo"}

    service = compose["services"]["sapimo"]
    assert service["build"]["dockerfile"] == "api_mock/docker/single/Dockerfile"
    assert service["command"] == [
        "python",
        "/workspace/api_mock/docker/gateway/main.py",
    ]
    assert service["environment"]["SAPIMO_SINGLE_CONTAINER"] == "1"
    assert (
        service["environment"]["PYTHONPATH"] == "/workspace:/workspace/api_mock/docker"
    )
    assert "..:/workspace:rw" in service["volumes"]

    docker_root = api_mock_dir / "docker"
    assert (docker_root / "single" / "Dockerfile").exists()
    assert (docker_root / "single" / "requirements.txt").exists()
    assert (docker_root / "gateway" / "main.py").exists()

    dockerfile_content = (docker_root / "single" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        'CMD ["python", "/workspace/api_mock/docker/gateway/main.py"]'
        in dockerfile_content
    )
    assert "PYTHONPATH=/workspace:/workspace/api_mock/docker" in dockerfile_content
