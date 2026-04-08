"""Single-container docker-compose generator for Sapimo."""

from pathlib import Path
from typing import Any
import shutil
import yaml

from sapimo.constants import SUPPORTED_PYTHON_VERSIONS, DEFAULT_PYTHON_VERSION
from sapimo.utils import LogManager, force_rmtree

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
            force_rmtree(target_root)

        shutil.copytree(templates_root, target_root)
        self._copy_runtime_sapimo_package(target_root)

    def _copy_runtime_sapimo_package(self, target_root: Path) -> None:
        import sapimo

        source_dir = Path(sapimo.__file__).resolve().parent
        target_dir = target_root / "sapimo"

        if target_dir.exists():
            force_rmtree(target_dir)

        shutil.copytree(
            source_dir,
            target_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    def _resolve_python_version(self) -> str:
        """config.yaml 内の全 Lambda Runtime から最新の対応 Python バージョンを決定する。"""
        if not self.config_path.exists():
            return DEFAULT_PYTHON_VERSION

        with open(self.config_path) as f:
            config = yaml.safe_load(f) or {}

        versions: list[tuple[int, int]] = []
        for path_methods in config.get("paths", {}).values():
            for method_config in path_methods.values():
                runtime = method_config.get("Properties", {}).get("Runtime", "") or ""
                ver = self._parse_python_runtime(runtime)
                if ver:
                    versions.append(ver)

        for trigger_config in config.get("triggered", {}).values():
            runtime = trigger_config.get("Properties", {}).get("Runtime", "") or ""
            ver = self._parse_python_runtime(runtime)
            if ver:
                versions.append(ver)

        if not versions:
            return DEFAULT_PYTHON_VERSION

        supported = {
            tuple(int(x) for x in v.split(".")): v for v in SUPPORTED_PYTHON_VERSIONS
        }
        # Lambda Runtime のうちサポート対象のバージョンだけを抽出
        compatible = [v for v in versions if v in supported]
        if compatible:
            best = max(compatible)
            return supported[best]

        # サポート対象のバージョンがない場合はデフォルトを使用
        return DEFAULT_PYTHON_VERSION

    @staticmethod
    def _parse_python_runtime(runtime: str) -> tuple[int, int] | None:
        """'python3.12' のような文字列を (3, 12) に変換する。非Python Runtimeは None。"""
        normalized = runtime.strip().lower()
        if not normalized.startswith("python"):
            return None
        version_str = normalized[len("python") :]
        parts = version_str.split(".")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return (int(parts[0]), int(parts[1]))
        return None

    def generate_compose_config(self) -> dict[str, Any]:
        python_version = self._resolve_python_version()
        logger.info("Using Python %s for single-container runtime", python_version)
        return {
            "services": {
                "sapimo": {
                    "image": "sapimo-single:latest",
                    "build": {
                        "context": "..",
                        "dockerfile": "api_mock/docker/single/Dockerfile",
                        "args": {
                            "PYTHON_VERSION": python_version,
                        },
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
