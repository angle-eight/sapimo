"""Single-container docker-compose generator for Sapimo."""

from pathlib import Path
from typing import Any
import shutil
import yaml

from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class SingleContainerComposeGenerator:
    """Generate docker-compose.yml for single-container runtime."""

    def __init__(self, config_path: Path, output_path: Path | None = None):
        self.config_path = config_path
        self.output_path = output_path or (config_path.parent / "docker-compose.yml")

    def _ensure_docker_templates(self) -> None:
        templates_root = Path(__file__).parent / "templates"
        target_root = self.output_path.parent / "docker"

        if target_root.exists():
            shutil.rmtree(target_root)

        shutil.copytree(templates_root, target_root)
        self._copy_runtime_sapimo_package(target_root)

    def _copy_runtime_sapimo_package(self, target_root: Path) -> None:
        import sapimo

        source_dir = Path(sapimo.__file__).resolve().parent
        target_dir = target_root / "sapimo"

        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    def generate_compose_config(self) -> dict[str, Any]:
        return {
            "services": {
                "sapimo": {
                    "image": "sapimo-single:latest",
                    "build": {
                        "context": "..",
                        "dockerfile": "api_mock/docker/single/Dockerfile",
                    },
                    "command": ["python", "/workspace/api_mock/docker/gateway/main.py"],
                    "ports": ["${SAPIMO_PORT:-8000}:3000"],
                    "environment": {
                        "SAPIMO_MODE": "single-container",
                        "SAPIMO_SINGLE_CONTAINER": "1",
                        "SAPIMO_HOST": "0.0.0.0",
                        "SAPIMO_PORT": "3000",
                        "PYTHONPATH": "/workspace:/workspace/api_mock/docker",
                    },
                    "volumes": [
                        "..:/workspace:rw",
                    ],
                    "restart": "unless-stopped",
                }
            }
        }

    def generate_compose_file(self) -> Path:
        self._ensure_docker_templates()
        compose_config = self.generate_compose_config()

        with open(self.output_path, "w") as f:
            yaml.dump(compose_config, f, default_flow_style=False, indent=2)

        logger.info("Generated single-container docker-compose: %s", self.output_path)
        return self.output_path
