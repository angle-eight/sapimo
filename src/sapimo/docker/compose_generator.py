"""
動的Docker Compose生成器
SAM/CDKテンプレートからマルチコンテナ構成を自動生成
"""

import shutil
import os
import yaml
from pathlib import Path
from typing import Dict, List, Any
from dataclasses import dataclass

from sapimo.parser.sam_parser import SamParser
from sapimo.parser.cdk_parser import CdkCfParser
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


@dataclass
class LambdaFunction:
    """Lambda関数の定義"""

    name: str
    handler: str
    runtime: str
    code_uri: str
    environment: Dict[str, str]
    layers: List[str]
    timeout: int = 30
    memory_size: int = 128


@dataclass
class ContainerConfig:
    """コンテナ設定"""

    name: str
    image: str
    ports: List[str] = None
    environment: Dict[str, str] = None
    volumes: List[str] = None
    depends_on: List[str] = None
    networks: List[str] = None


class DockerComposeGenerator:
    """Docker Compose動的生成器"""

    def __init__(self, template_path: Path, output_path: Path = None):
        self.template_path = template_path
        # api_mockディレクトリ内にdocker-compose.ymlを配置
        self.output_path = output_path or (template_path.parent / "docker-compose.yml")
        self.compose_dir = self.output_path.parent
        self.project_root = self.compose_dir.parent
        self.lambda_functions: List[LambdaFunction] = []
        self.aws_resources: Dict[str, Any] = {}

    def _to_compose_relative_path(self, host_path: Path) -> str:
        """composeファイル配置場所からの相対パスを返す"""
        rel_path = Path(os.path.relpath(host_path, self.compose_dir)).as_posix()
        return "." if rel_path == "." else rel_path

    def _resolve_code_uri_host_path(self, code_uri: str) -> str:
        """CodeUri（プロジェクトルート基準）をcompose基準の相対パスに変換"""
        normalized = (code_uri or "").strip()
        if not normalized or normalized in (".", "./"):
            code_path = self.project_root
        else:
            code_path = Path(normalized)
            if not code_path.is_absolute():
                code_path = self.project_root / code_path

        return self._to_compose_relative_path(code_path)

    def _resolve_lambda_python_tag(self, runtime: str) -> str:
        """Runtime文字列から Lambda base image 用のPythonタグを返す。"""
        normalized = (runtime or "").strip().lower()
        if normalized.startswith("python"):
            normalized = normalized[len("python") :]

        if normalized.count(".") == 1:
            major, minor = normalized.split(".", 1)
            if major.isdigit() and minor.isdigit():
                return f"{major}.{minor}"

        raise ValueError(f"Unsupported Lambda runtime: {runtime}")

    def _resolve_layer_host_path(self, layer_path: str) -> Path:
        """Layerパスをプロジェクトルート基準の絶対パスに変換。"""
        resolved = Path(layer_path)
        if not resolved.is_absolute():
            resolved = self.project_root / resolved
        return resolved

    def _ensure_docker_templates(self) -> None:
        """実行用Dockerテンプレートをapi_mock配下へ展開"""
        templates_root = Path(__file__).parent / "templates"
        target_root = self.output_path.parent / "docker"

        if target_root.exists():
            shutil.rmtree(target_root)

        shutil.copytree(templates_root, target_root)
        self._copy_runtime_sapimo_package(target_root)

    def _copy_runtime_sapimo_package(self, target_root: Path) -> None:
        """現在実行中のsapimoパッケージをテンプレート配下に複製"""
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

    def parse_template(self) -> None:
        """SAM/CDKテンプレートを解析"""
        logger.info(f"Parsing template: {self.template_path}")

        try:
            # 毎回再生成時にリセット
            self.lambda_functions = []
            self.aws_resources = {}

            if self.template_path.name in {"config.yaml", "config.yml"}:
                with open(self.template_path) as f:
                    config = yaml.safe_load(f) or {}
            else:
                # CDK出力か確認
                if self.template_path.name.endswith(".json") or "cdk.out" in str(
                    self.template_path
                ):
                    parser = CdkCfParser(self.template_path)
                else:
                    parser = SamParser(self.template_path)

                config = self._create_intermediate_config(parser)

            # Lambda関数を抽出
            self._extract_lambda_functions(config)

            # AWSリソースを抽出
            self._extract_aws_resources(config)

            logger.info(f"Found {len(self.lambda_functions)} Lambda functions")

        except Exception as e:
            logger.error(f"Failed to parse template: {e}")
            raise

    def _create_intermediate_config(self, parser) -> Dict[str, Any]:
        """SAM/CDKパーサーから共通config形式を生成して返す。"""
        temp_config = Path("/tmp/temp_config.yaml")
        parser.create_config_file(temp_config, overwrite=True)
        try:
            with open(temp_config) as f:
                return yaml.safe_load(f) or {}
        finally:
            temp_config.unlink(missing_ok=True)

    def _extract_lambda_functions(self, config: Dict[str, Any]) -> None:
        """config辞書からLambda関数を抽出"""
        for path, methods in config.get("paths", {}).items():
            for method, props in methods.items():
                func_props = props.get("Properties", {})

                # 関数名を生成
                func_name = f"{path.replace('/', '_').replace('{', '').replace('}', '')}_{method}"
                if func_name.startswith("_"):
                    func_name = func_name[1:]

                lambda_func = LambdaFunction(
                    name=func_name,
                    handler=func_props.get("Handler", "app.lambda_handler"),
                    runtime=func_props.get("Runtime", "python3.9"),
                    code_uri=func_props.get("CodeUri", "./"),
                    environment=func_props.get("Environment", {}).get("Variables", {}),
                    layers=func_props.get("Layers", []),
                    timeout=func_props.get("Timeout", 30),
                    memory_size=func_props.get("MemorySize", 128),
                )

                self.lambda_functions.append(lambda_func)

    def _extract_aws_resources(self, config: Dict[str, Any]) -> None:
        """config辞書からAWSリソースを抽出"""
        for service in ["s3", "dynamodb", "sqs", "sns", "ses"]:
            if service in config:
                self.aws_resources[service] = config[service]

    def generate_compose_config(self) -> Dict[str, Any]:
        """Docker Compose設定を生成"""
        compose_config = {
            "services": {},
            "networks": {
                "sapimo-network": {
                    "driver": "bridge",
                }
            },
            "volumes": {"sapimo-data": {}, "sapimo-logs": {}},
        }

        # 1. Gateway コンテナ
        compose_config["services"]["sapimo-gateway"] = self._generate_gateway_service()

        # 2. Lambda Runtime コンテナ（Pythonバージョン別にグループ化）
        lambda_services = self._generate_lambda_services()
        compose_config["services"].update(lambda_services)

        # 3. AWS Mock コンテナ
        compose_config["services"]["sapimo-aws-mock"] = (
            self._generate_aws_mock_service()
        )

        return compose_config

    def _generate_gateway_service(self) -> Dict[str, Any]:
        """Gateway サービス設定を生成"""
        return {
            "image": "sapimo-gateway:latest",
            "build": {
                "context": "..",
                "dockerfile": "api_mock/docker/gateway/Dockerfile",
            },
            "ports": ["${SAPIMO_PORT:-8000}:3000"],
            "environment": {
                "SAPIMO_MODE": "gateway",
                "LAMBDA_DISCOVERY_NETWORK": "sapimo-network",
            },
            "volumes": [".:/workspace/api_mock:ro"],
            "networks": ["sapimo-network"],
            "depends_on": ["sapimo-aws-mock"],
        }

    def _generate_lambda_services(self) -> Dict[str, Dict[str, Any]]:
        """Lambda Runtime サービス設定を生成（関数ごとのコンテナ）"""
        services = {}

        # 各Lambda関数ごとに個別コンテナを作成
        for func in self.lambda_functions:
            # サービス名を生成（関数名をサニタイズ）
            safe_name = self._sanitize_service_name(func.name)
            service_name = f"lambda-{safe_name}"
            compose_code_path = self._resolve_code_uri_host_path(func.code_uri)
            runtime_tag = self._resolve_lambda_python_tag(func.runtime)

            resolved_code_path = Path(func.code_uri)
            if not resolved_code_path.is_absolute():
                resolved_code_path = self.project_root / resolved_code_path
            if not resolved_code_path.exists():
                logger.warning(
                    "CodeUri path not found for '%s': %s",
                    func.name,
                    resolved_code_path,
                )

            layer_volumes: List[str] = []
            pythonpath_entries: List[str] = ["/var/task"]
            for idx, layer in enumerate(func.layers):
                resolved_layer_path = self._resolve_layer_host_path(layer)
                if not resolved_layer_path.exists():
                    logger.warning(
                        "Layer path not found for '%s': %s",
                        func.name,
                        resolved_layer_path,
                    )
                    continue

                compose_layer_path = self._to_compose_relative_path(resolved_layer_path)
                container_layer_base = f"/opt/sapimo_layers/{idx}"
                layer_volumes.append(f"{compose_layer_path}:{container_layer_base}:ro")
                # layer直下にモジュールがあるケースと、python/配下にあるケースの両方を解決
                pythonpath_entries.append(container_layer_base)
                pythonpath_entries.append(f"{container_layer_base}/python")

            user_pythonpath = func.environment.get("PYTHONPATH")
            if user_pythonpath:
                pythonpath_entries.extend(
                    [entry for entry in user_pythonpath.split(":") if entry]
                )

            deduped_pythonpath: List[str] = []
            for entry in pythonpath_entries:
                if entry not in deduped_pythonpath:
                    deduped_pythonpath.append(entry)

            lambda_env = {
                **func.environment,
                "PYTHONPATH": ":".join(deduped_pythonpath),
            }

            configured_region = (
                lambda_env.get("AWS_DEFAULT_REGION")
                or lambda_env.get("AWS_REGION")
                or "us-east-1"
            )
            if not str(configured_region).strip():
                configured_region = "us-east-1"

            lambda_env["AWS_REGION"] = configured_region
            lambda_env["AWS_DEFAULT_REGION"] = configured_region
            lambda_env.setdefault("AWS_MOCK_ENDPOINT", "http://sapimo-aws-mock:4566")
            lambda_env.setdefault("AWS_ENDPOINT_URL", lambda_env["AWS_MOCK_ENDPOINT"])
            lambda_env.setdefault("AWS_ACCESS_KEY_ID", "testing")
            lambda_env.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
            lambda_env.setdefault("AWS_SESSION_TOKEN", "testing")
            lambda_env.setdefault("AWS_EC2_METADATA_DISABLED", "true")

            services[service_name] = {
                "image": f"sapimo-lambda-{safe_name}:latest",
                "build": {
                    "context": "..",
                    "dockerfile": "api_mock/docker/lambda_runtime/Dockerfile",
                    "args": {
                        "PYTHON_VERSION": runtime_tag,
                    },
                },
                "command": func.handler,
                "environment": {
                    "SAPIMO_MODE": "lambda-runtime",
                    "LAMBDA_FUNCTION_NAME": func.name,
                    "LAMBDA_HANDLER": func.handler,
                    "LAMBDA_RUNTIME": func.runtime,
                    "AWS_LAMBDA_FUNCTION_VERSION": "$$LATEST",
                    **lambda_env,
                },
                "volumes": [
                    f"{compose_code_path}:/var/task:rw",  # コード即時反映
                    f"{self._to_compose_relative_path(self.project_root / 'data' / f'lambda-{safe_name}')}:/tmp/lambda:rw",  # 一時ファイル
                    ".:/workspace/api_mock:ro",  # 設定ファイル
                    *layer_volumes,
                ],
                "networks": ["sapimo-network"],
                "depends_on": ["sapimo-aws-mock"],
                "healthcheck": {
                    "test": ["CMD", "python", "-c", "import sys; sys.exit(0)"],
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                    "start_period": "10s",
                },
                "restart": "unless-stopped",
            }

        return services

    def _sanitize_service_name(self, name: str) -> str:
        """サービス名をDocker Composeで使用可能な形式に変換"""
        import re

        # 英数字とハイフンのみ許可、連続ハイフン除去
        sanitized = re.sub(r"[^a-zA-Z0-9\-]", "-", name.lower())
        sanitized = re.sub(r"-+", "-", sanitized)
        return sanitized.strip("-")

    def _generate_aws_mock_service(self) -> Dict[str, Any]:
        """AWS Mock サービス設定を生成"""
        return {
            "image": "sapimo-aws-mock:latest",
            "build": {
                "context": "..",
                "dockerfile": "api_mock/docker/aws_mock/Dockerfile",
            },
            "ports": ["4566:4566"],  # LocalStack互換
            "environment": {
                "SAPIMO_MODE": "aws-mock",
                "AWS_SERVICES": ",".join(self.aws_resources.keys()),
                "AWS_DEFAULT_REGION": "us-east-1",
            },
            "volumes": [
                f"{self._to_compose_relative_path(self.project_root / 'data')}:/data:rw",
                "sapimo-data:/persistent-data",
            ],
            "networks": ["sapimo-network"],
        }

    def generate_compose_file(self) -> Path:
        """Docker Compose ファイルを生成"""
        try:
            # 実行用Dockerテンプレートを展開
            self._ensure_docker_templates()

            # テンプレート解析
            self.parse_template()

            # Compose設定生成
            compose_config = self.generate_compose_config()

            # ファイル書き込み
            with open(self.output_path, "w") as f:
                yaml.dump(compose_config, f, default_flow_style=False, indent=2)

            logger.info(f"Generated docker-compose.yml: {self.output_path}")
            logger.info(f"Services: {list(compose_config['services'].keys())}")

            return self.output_path

        except Exception as e:
            logger.error(f"Failed to generate compose file: {e}")
            raise

    @staticmethod
    def auto_generate_from_project(project_path: Path = None) -> Path:
        """プロジェクトから自動生成"""
        if project_path is None:
            project_path = Path.cwd()

        # テンプレートファイルを検索
        template_candidates = [
            project_path / "template.yaml",
            project_path / "template.yml",
            project_path / "cdk.out" / "*.template.json",
        ]

        template_path = None
        for candidate in template_candidates:
            if candidate.exists():
                template_path = candidate
                break
            elif candidate.parent.exists() and "*" in str(candidate):
                # cdk.out内のJSONファイルを検索
                json_files = list(candidate.parent.glob("*.template.json"))
                if json_files:
                    template_path = json_files[0]
                    break

        if not template_path:
            raise FileNotFoundError("No SAM template or CDK output found")

        # 生成器を作成して実行
        generator = DockerComposeGenerator(template_path)
        return generator.generate_compose_file()


# CLI utility
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        template_path = Path(sys.argv[1])
        generator = DockerComposeGenerator(template_path)
        output_path = generator.generate_compose_file()
        print(f"Generated: {output_path}")
    else:
        output_path = DockerComposeGenerator.auto_generate_from_project()
        print(f"Auto-generated: {output_path}")
