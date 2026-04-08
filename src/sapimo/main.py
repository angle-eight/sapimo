from typing import Callable
from pathlib import Path
import click

import os
import subprocess
import hashlib
from sapimo.parser.sam_parser import SamParser
from sapimo.parser.cdk_parser import CdkCfParser
from sapimo.utils import create_config_template, LogManager
from sapimo.constants import CONFIG_FILE, WORKING_DIR

logger = LogManager.setup_logger(__file__)


def _compose_project_name() -> str:
    base_dir = WORKING_DIR.resolve().parent
    digest = hashlib.sha1(str(base_dir).encode("utf-8")).hexdigest()[:8]
    normalized = base_dir.name.replace("_", "-")
    return f"sapimo-{normalized}-{digest}"


@click.group()
def main():
    """Sapimo - SAM API Mock Server"""
    pass


@main.command()
@click.option(
    "--template",
    type=str,
    default="",
    help="AWS SAM's template file or AWS CDK's cloudformation file",
    show_default=True,
)
@click.option(
    "--cdk",
    is_flag=True,
    help="true if CDK cloudformation file",
)
def init(template, cdk):
    if template == "":
        create_config_default()
    else:
        template_path = Path(template).resolve()
        parser = SamParser if not cdk else CdkCfParser
        if not create_config(template_path, parse_class=parser, overwrite=False):
            print(
                f"{template_path.name} file not found.\
                dummy config.yaml is created.\
                you need to change it."
            )
            create_config_template(CONFIG_FILE)
            exit()


def create_config_default():
    template_path = Path("template.yaml").resolve()
    if template_path.exists():
        create_config(template_path, parse_class=SamParser, overwrite=False)
    else:
        cdk_out = Path("cdk.out").resolve()
        if not cdk_out.exists():
            logger.warning("template.yaml or cdk cf file is not exist")
            exit(0)

        files = [f for f in cdk_out.iterdir() if f.is_file()]
        for file in files:
            if file.name.endswith("template.json"):
                create_config(file.resolve(), parse_class=CdkCfParser, overwrite=False)
                break
        else:
            logger.warning("template.yaml or cdk cf file is not exist")
            exit(0)


def create_config(template: Path, parse_class: Callable, overwrite: bool):
    """
    parse template.yaml and convert to config.yaml
    """
    if not template.exists():
        return False
    else:
        WORKING_DIR.mkdir(exist_ok=True)
        try:
            parser = parse_class(template)
            parser.create_config_file(CONFIG_FILE, overwrite)

            # 単一コンテナ用 Docker Compose 自動生成
            from sapimo.docker.single_compose_generator import (
                SingleContainerComposeGenerator,
            )

            compose_gen = SingleContainerComposeGenerator(CONFIG_FILE)
            compose_gen.generate_compose_file()
            click.echo(f"Generated docker-compose.yml in {WORKING_DIR}")

            # 旧記法チェック：生成されたconfig.yamlに古いフォーマット（handler直下）がないか確認
            _warn_old_config_format(CONFIG_FILE)

            return True
        except Exception as e:
            logger.exception("config parse error: %s", e)
            return False


@main.command()
def generate():
    """Generate mock API endpoints from config.yaml"""
    if not CONFIG_FILE.exists():
        click.echo("config.yaml not found. Run 'sapimo init' first.")
        return

    generate_mock_api(WORKING_DIR / "app.py")


def generate_mock_api(filepath: Path):
    """Generate mock API endpoints with new Mock decorator syntax"""
    from sapimo.parser.config_parser import ConfigParser

    try:
        config = ConfigParser(CONFIG_FILE)
    except Exception as e:
        click.echo(f"Error reading config.yaml: {e}")
        return

    # 既存の実装をチェック
    implemented = []
    if filepath.exists():
        with open(filepath, "r") as f:
            content = f.read()
            # @api.method で始まる行をチェック
            for line in content.split("\n"):
                if line.strip().startswith("@api."):
                    implemented.append(line.strip())

    with open(filepath, "a", encoding="utf-8", newline="\n") as f:
        if not implemented:  # 新規ファイル
            f.write("from sapimo.mock import api, change_input\n\n")
            f.write("# Generated mock API endpoints\n")
            f.write("# Edit return values to customize mock behavior:\n")
            f.write("# - return None: Execute actual Lambda function\n")
            f.write("# - return {...}: Return mock data\n")
            f.write(
                "# - return change_input(...): Execute Lambda with modified input\n\n"
            )
        else:
            f.write("\n")

        for path, methods in config.apis.items():
            for method in methods.keys():
                # デコレータ定義
                deco = f'@api.{method.lower()}("{path}")'

                if deco in implemented:
                    continue

                # 関数名を生成
                func_name = (
                    path.replace("-", "_")
                    .replace("/", "_")
                    .replace("{", "p_")
                    .replace("}", "")
                )
                if func_name.startswith("_"):
                    func_name = func_name[1:]
                func_name = f"{func_name}_{method.lower()}_mock"

                # パスパラメータの抽出
                import re

                path_params = re.findall(r"\{([^}]+)\}", path)

                # 関数定義
                if path_params:
                    # パスパラメータがある場合
                    params = []
                    for param in path_params:
                        # 基本的な型推論（数値っぽいものはint）
                        if any(
                            word in param.lower()
                            for word in ["id", "number", "count", "index"]
                        ):
                            params.append(f"{param}: int")
                        else:
                            params.append(f"{param}: str")
                    param_str = ", ".join(params)
                    func_def = f"async def {func_name}({param_str}):"
                else:
                    func_def = f"async def {func_name}():"

                # コメントとデフォルト実装
                comment = f'    """Mock for {method.upper()} {path}"""'
                default_impl = "    pass  # Execute actual Lambda function"

                f.writelines(
                    [
                        "\n",
                        f"{deco}\n",
                        f"{func_def}\n",
                        f"{comment}\n",
                        f"{default_impl}\n",
                    ]
                )

    click.echo(f"Generated mock API endpoints in {filepath}")
    click.echo("Edit the return values in app.py to customize mock behavior.")


@main.command()
@click.option(
    "--host",
    type=str,
    default="0.0.0.0",
    help="Bind socket to this host.",
    show_default=True,
)
@click.option(
    "--port",
    type=int,
    default=8000,
    help="Bind socket to this port.",
    show_default=True,
)
@click.option(
    "--build",
    is_flag=True,
    help="Force rebuild the container image",
)
def start(host: str, port: int, build: bool):
    """Start Sapimo API mock server (runs in background)"""

    compose_file = WORKING_DIR / "docker-compose.yml"
    if not compose_file.exists():
        click.echo("❌ docker-compose.yml not found. Run 'sapimo init' first.")
        return

    original_cwd = os.getcwd()
    os.chdir(WORKING_DIR)
    try:
        cmd = [
            "docker",
            "compose",
            "-p",
            _compose_project_name(),
            "up",
            "--remove-orphans",
            "-d",
        ]

        if build:
            cmd.append("--build")

        env = os.environ.copy()
        env.update(
            {
                "SAPIMO_HOST": host,
                "SAPIMO_PORT": str(port),
            }
        )

        subprocess.run(cmd, env=env, check=True)
        click.echo(f"✅ Sapimo started at http://localhost:{port}")
        click.echo("   Run 'sapimo log' to see logs.")
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to start container")
    finally:
        os.chdir(original_cwd)


@main.command()
@click.option(
    "--tail",
    default=100,
    show_default=True,
    help="Number of lines to show from the end of logs",
)
@click.option(
    "--no-follow",
    is_flag=True,
    default=False,
    help="Print logs without following",
)
def log(tail: int, no_follow: bool):
    """Show Sapimo container logs"""

    compose_file = WORKING_DIR / "docker-compose.yml"
    if not compose_file.exists():
        click.echo("❌ docker-compose.yml not found. Run 'sapimo init' first.")
        return

    original_cwd = os.getcwd()
    os.chdir(WORKING_DIR)
    try:
        cmd = [
            "docker",
            "compose",
            "-p",
            _compose_project_name(),
            "logs",
            "--tail",
            str(tail),
        ]
        if not no_follow:
            cmd.append("-f")

        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to get logs. Make sure the server is running.")
    except KeyboardInterrupt:
        pass
    finally:
        os.chdir(original_cwd)


@main.command()
def stop():
    """Stop Sapimo API mock server"""

    compose_file = WORKING_DIR / "docker-compose.yml"
    if not compose_file.exists():
        click.echo("❌ docker-compose.yml not found. Run 'sapimo init' first.")
        return

    original_cwd = os.getcwd()
    os.chdir(WORKING_DIR)
    try:
        cmd = [
            "docker",
            "compose",
            "-p",
            _compose_project_name(),
            "down",
        ]
        subprocess.run(cmd, check=True)
        click.echo("✅ Sapimo stopped")
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to stop container")
    finally:
        os.chdir(original_cwd)


@main.command()
def status():
    """Show Sapimo running status"""

    compose_file = WORKING_DIR / "docker-compose.yml"
    if not compose_file.exists():
        click.echo("⏹  Not initialized. Run 'sapimo init' first.")
        return

    original_cwd = os.getcwd()
    os.chdir(WORKING_DIR)
    try:
        import json as _json

        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                _compose_project_name(),
                "ps",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            click.echo("❌ Failed to get status (is Docker running?)")
            return

        containers = _parse_compose_ps_json(result.stdout.strip())

        if not containers:
            click.echo("⏹  Stopped")
            return

        for container in containers:
            state = container.get("State", "").lower()
            status_text = container.get("Status", "")
            health = container.get("Health", "").lower()

            if state == "running":
                publishers = container.get("Publishers") or []
                port_info = ""
                for pub in publishers:
                    published = pub.get("PublishedPort", 0)
                    if published:
                        port_info = f"  →  http://localhost:{published}"
                        break

                if health == "unhealthy":
                    click.echo(f"⚠️  Unhealthy{port_info}")
                    click.echo("   Run 'sapimo log' to check for errors.")
                elif health == "starting":
                    click.echo(f"⏳ Starting...{port_info}")
                else:
                    click.echo(f"✅ Running{port_info}")
            elif state == "exited":
                click.echo(f"❌ Error (exited) - {status_text}")
                click.echo("   Run 'sapimo log' to check for errors.")
            elif state == "restarting":
                click.echo(f"🔄 Restarting - {status_text}")
                click.echo("   Run 'sapimo log' to check for errors.")
            else:
                click.echo(f"⏹  Stopped")
    finally:
        os.chdir(original_cwd)


@main.command()
@click.option(
    "--service",
    multiple=True,
    help="Specific services to clean (s3, dynamodb, sqs, sns, ses)",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Skip confirmation prompt",
)
def clean(service, confirm):
    """Clean AWS mock data"""

    if not confirm:
        services_str = ", ".join(service) if service else "all services"
        if not click.confirm(f"Are you sure you want to clean {services_str} data?"):
            click.echo("Cancelled")
            return

    try:
        services_arg = ",".join(service) if service else ""
        cmd = [
            "docker",
            "compose",
            "-p",
            _compose_project_name(),
            "exec",
            "sapimo",
            "python",
            "-c",
            f"""
import sys
from pathlib import Path
runtime_root = Path('/workspace/api_mock/docker')
if not runtime_root.exists():
    raise RuntimeError(
        "Missing runtime assets: /workspace/api_mock/docker. "
        "Run 'sapimo init' to regenerate docker assets."
    )
sys.path.insert(0, str(runtime_root))
try:
    from sapimo.docker.mock_manager import DockerMockManager
    from sapimo.constants import CONFIG_FILE

    if CONFIG_FILE.exists():
        mock_manager = DockerMockManager(CONFIG_FILE)
        services = "{services_arg}".split(",") if "{services_arg}" else None
        services = [s.strip() for s in services if s.strip()] if services else None

        if mock_manager.cleanup_persistent_data(services):
            print("✅ Mock data cleaned successfully")
        else:
            print("❌ Failed to clean mock data")
    else:
        print("❌ Config file not found")

except Exception as e:
    print(f"❌ Error: {{e}}")
""",
        ]

        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        click.echo("❌ Failed to clean data. Make sure the server is running.")


def _parse_compose_ps_json(output: str) -> list[dict]:
    """docker compose ps --format json の出力をパースする（JSON配列・NDJSON両対応）"""
    import json as _json

    if not output:
        return []
    try:
        data = _json.loads(output)
        return data if isinstance(data, list) else [data]
    except _json.JSONDecodeError:
        containers = []
        for line in output.splitlines():
            line = line.strip()
            if line:
                try:
                    containers.append(_json.loads(line))
                except _json.JSONDecodeError:
                    pass
        return containers


def _warn_old_config_format(config_path):
    """旧記法の config.yaml を使用している場合に警告を出す"""
    import yaml as _yaml

    try:
        if not Path(config_path).exists():
            return
        with open(config_path) as f:
            cfg = _yaml.safe_load(f)
        if not cfg:
            return
        paths = cfg.get("paths", {})
        for path, methods in paths.items():
            for method, props in methods.items():
                if "handler" in (props or {}):
                    click.echo(
                        f"⚠️  旧記法検出: '{path}.{method}.handler' は旧フォーマットです。\n"
                        "   新フォーマット: Properties.Handler / Properties.CodeUri を使用してください。\n"
                        "   詳細: docs/Docker-Setup.md を参照"
                    )
    except Exception:
        pass
