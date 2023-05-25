import uvicorn
from typing import Callable
import sys
from pathlib import Path
import click

from sapimo.parser.config_parser import ConfigParser
from sapimo.parser.sam_parser import SamParser
from sapimo.parser.cdk_parser import CdkCfParser
from sapimo.utils import create_config_template, LogManager
from sapimo.constants import CONFIG_FILE, API_FILE, WORKING_DIR
logger = LogManager.setup_logger(__file__)


@click.group()
def main():
    pass


@main.command()
@click.option(
    "--template",
    type=str,
    default="",
    help="AWS SAM's template file or AWS CDK's cloudformation file",
    show_default=True,
)
@click.option("--cdk", is_flag=True, help="true if CDK cloudformation file",)
def init(template, cdk):
    if template == "":
        create_config_default()
    else:
        template_path = Path(template).resolve()
        parser = SamParser if not cdk else CdkCfParser
        if not create_config(template_path, parse_class=parser,
                             overwrite=False):
            print(f"{template.name} file not found.\
                dummy config.yaml is created.\
                you need to change it.")
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
                create_config(file.resolve(), parse_class=CdkCfParser,
                              overwrite=False)
                break
        else:
            logger.warning("template.yaml or cdk cf file is not exist")
            exit(0)


def create_config(template: Path, parse_class: Callable,
                  overwrite: bool):
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
            return True
        except:
            logger.exception("config parse error")
            return False


@main.command()
@click.option(
    "--host",
    type=str,
    default="127.0.0.1",
    help="Bind socket to this host.",
    show_default=True,
)
@click.option(
    "--port",
    type=int,
    default=3000,
    help="Bind socket to this port.",
    show_default=True,
)
def run(host: str, port: int):
    if not CONFIG_FILE.exists():
        create_config_default()

    # already update app.py
    generate_api(API_FILE)

    lambda_path = Path.cwd()
    sys.path.append(str(lambda_path))

    uvicorn.run(".mock_api.app:api", host=host, port=port, reload=True)


@main.command()
def generate():
    if not CONFIG_FILE.exists():
        create_config_default()
    generate_api(API_FILE)


def generate_api(filepath: Path):
    implemented = []
    if filepath.exists():
        with open(filepath, "r") as f:
            implemented = [d for d in f.readlines() if d.startswith("@api")]

    config = ConfigParser(CONFIG_FILE)
    with open(filepath, "a", encoding="utf-8", newline="\n")as f:
        if not implemented:  # new file
            f.write("from sapimo.mock import api\n\n\n")
        else:
            f.write("\n\n")

        for path, value in config.apis.items():
            for method in value.keys():
                deco = "@api."+method + "('" + path + "')\n"
                if deco in implemented:
                    continue
                func_name = path.replace("-", "_")\
                                .replace("/", "_")\
                                .replace("{", "p_")\
                                .replace("}", "_p")
                if func_name.startswith("_"):
                    func_name = func_name[1:]
                define = "async def " + func_name + "_" + method + "():\n"
                f.writelines([deco, define, "    return\n\n\n"])
