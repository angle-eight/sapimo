from pathlib import Path
import json

import yaml

from sapimo.utils import setup_logger
logger = setup_logger(__file__)


class ConfigParser:
    """
        read config.yaml and convert to useful form
    """

    def __init__(self, path: Path):
        try:
            if not path.exists():
                raise FileNotFoundError(f"{path.name} is not found")

            with open(path) as f:
                if path.name.endswith(".json"):
                    obj = json.load(f)
                elif path.name.endswith(".yaml") or path.name.endswith(".yml"):
                    obj = yaml.safe_load(f)
                else:
                    raise Exception("config file must be json or yaml")

            if "paths" not in obj:
                raise Exception("paths key dose not exist in config file")
            # paths = {}
            self.apis: dict[str, dict[str, dict]] = {}
            for path, val in obj["paths"].items():
                # method_props = {}
                self.apis[path] = {}
                for k, v in val.items():
                    method = k.lower()
                    self.apis[path][method] = v
            self.triggered = obj.get("triggered", {})
        except:
            logger.exception("config parse error")
            raise Exception("config parse error")

        self.all_resource = obj

    def get_service_config(self, service: str):
        return self.all_resource.get(service, {})
