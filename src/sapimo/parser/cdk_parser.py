import hashlib
from copy import deepcopy
from pathlib import Path


from sapimo.utils import LogManager
from sapimo.parser.cf_resource_parser import CfResourceParser
from sapimo.constants import EventType, AuthType

logger = LogManager.setup_logger(__file__)


class CdkCfParser(CfResourceParser):
    """
        for CDK repository
    """

    def __init__(self, filepath: Path, region="us-east-1"):
        self._cdk_path = filepath.parent
        self._repo_path = self._cdk_path.parent

        # calculate all file's md5
        self._md5s = {}

        def save_hash(directory: Path, d: dict):
            for file in directory.iterdir():
                code_uri: str = str(file).replace(str(self._repo_path)+"/", "")
                if code_uri.startswith(".") or self._cdk_path.name in code_uri:
                    continue
                if file.is_dir():
                    save_hash(file, d)
                else:
                    with open(file, "rb") as f:
                        hash = hashlib.md5(f.read()).hexdigest()
                    d[hash] = code_uri
        save_hash(self._repo_path, self._md5s)

        super().__init__(filepath, region)

    def _preprocess(self, filepath: Path, region: str):
        """
            override: extract global settings and declare additional member
        """
        # for config
        self._apis = {}
        self._triggered = {}

        # for inner process
        self._integrations_map = {}
        self._lambdas_map = {}
        self._layers_map = {}
        self._api_resources_map = {}

        super()._preprocess(filepath, region)

    def _classification(self, name, val):
        """
            resource classification ->
                {apis, buckets, tables, lambdas, others}
        """
        props: dict = deepcopy(val.get("Properties", {}))
        tp = val["Type"]
        if tp == "AWS::ApiGatewayV2::Route":
            method, api_path = props["RouteKey"].split(" ")
            if api_path not in self._apis:
                self._apis[api_path] = {}
            method = method.lower()
            integration_key = props["Target"].replace("integrations/", "")
            integration_key = self._treat(integration_key)
            lambda_key: dict = self._integrations_map.get(integration_key, {})\
                .get("Properties", {}).get("IntegrationUri", "")
            auth_type = props.get("AuthorizationType", "")
            lambda_key = self._treat(lambda_key)
            lambda_ = self._lambdas_map.get(lambda_key, {})
            code_uri = lambda_.get("Metadata", {}) .get("aws:asset:path", "")
            l_props = lambda_.get("Properties", {})
            atrs = ["Environment", "Handler", "Layers", "Runtime", "TimeOut"]
            api_props = {}
            for k in atrs:
                if k in l_props:
                    api_props[k] = self._treat(l_props[k])
            handler_file = api_props.get("Handler", "").split(".")[0]
            api_props["CodeUri"] = self._search_code_uri(
                code_uri, handler_file)
            api_props["EventType"] = EventType.APIGW_V2.name
            atype = AuthType[auth_type] if auth_type else AuthType.NONE
            api_props["AuthType"] = atype.name
            layers = self._treat(l_props.get("Layers", []))
            if layers:
                ls = []
                for layer_key in layers:
                    layer = self._layers_map.get(layer_key, {})
                    layer = self._treat(layer)
                    layer_uri = layer.get("Metadata", {})\
                        .get("aws:asset:path", "")
                    ls.append(self._search_layer_uri(layer_uri))
                api_props["Layers"] = ls
            self._apis[api_path][method] = {"Properties": api_props}

        elif tp == "AWS::ApiGateway::Method":
            method = props["HttpMethod"].lower()
            api_resource_key = props["ResourceId"]
            api_resource_key = self._treat(api_resource_key)
            api_resource = self._api_resources_map.get(api_resource_key)
            if not api_resource:
                raise Exception()  # FIXME
            path_parts = api_resource.get("Metadata", {})\
                .get("aws:cdk:path", "").split("/")
            path_parts: list = path_parts
            path_parts = path_parts[path_parts.index("Default")+1:-1]
            api_path = "/" + "/".join(path_parts)
            if api_path not in self._apis:
                self._apis[api_path] = {}
            auth_type = props.get("AuthorizationType", "")
            lambda_uri = props.get("Integration", {}).get("Uri", "")
            if not lambda_uri:
                # this api is not lambda integration
                return
            # uri = ~/~/~/LAMBDA_KEY/invocations
            lambda_key = self._treat(lambda_uri).split("/")[-2]
            lambda_ = self._lambdas_map.get(lambda_key)
            if not lambda_:
                # maybe cdk template is invalid
                raise Exception()  # FIXME
            api_props = self._api_props_from_lambda(lambda_, auth_type, False)
            self._apis[api_path][method] = {"Properties": api_props}

        else:
            super()._classification(name, val)

    def _api_props_from_lambda(self, lambda_, auth_type, is_apigw_v2):
        code_uri = lambda_.get("Metadata", {}) .get("aws:asset:path", "")
        l_props = lambda_.get("Properties", {})
        atrs = ["Environment", "Handler", "Layers", "Runtime", "TimeOut"]
        api_props = {}
        for k in atrs:
            if k in l_props:
                api_props[k] = self._treat(l_props[k])
        handler_file = api_props.get("Handler", "").split(".")[0]
        api_props["CodeUri"] = self._search_code_uri(
            code_uri, handler_file)
        api_props["EventType"] = EventType.APIGW_V2.name if is_apigw_v2 else EventType.APIGW.name
        atype = AuthType[auth_type] if auth_type else AuthType.NONE
        api_props["AuthType"] = atype.name
        layers = self._treat(l_props.get("Layers", []))
        if layers:
            ls = []
            for layer_key in layers:
                layer = self._layers_map.get(layer_key, {})
                layer = self._treat(layer)
                layer_uri = layer.get("Metadata", {})\
                    .get("aws:asset:path", "")
                ls.append(self._search_layer_uri(layer_uri))
            api_props["Layers"] = ls
        return api_props

    def _get_config_dict(self) -> dict:
        """ override: add api paths """
        config = super()._get_config_dict()
        config["paths"] = self._apis
        return config

    def _get_ref_and_attr(self, name: str, resource: dict):
        """
            override: for raw cloud formation
              and save all 'integration','lambda'
        """
        tp = resource["Type"]
        if tp == "AWS::ApiGatewayV2::Integration":
            self._integrations_map[name] = resource
            return {"Ref": name}
        elif tp == "AWS::Lambda::Function":
            self._lambdas_map[name] = resource
            return {"Ref": name,
                    "Arn": name}  # use pass name instead of arn
        elif tp == "AWS::Lambda::LayerVersion":
            self._layers_map[name] = resource
            return {"Ref": name,
                    "Arn": name}  # pass name instead of arn
        elif tp == "AWS::ApiGateway::Resource":
            self._api_resources_map[name] = resource
            return {"Ref": name, "Arn": name}
        else:
            return super()._get_ref_and_attr(name, resource)

    def _search_layer_uri(self, cdk_resource_path: str):
        return self._search_code_uri(cdk_resource_path+"/python", handler_file="")

    def _search_code_uri(self, cdk_code_uri: str, handler_file: str = ""):
        """
            search dir that contains same as *.py in cdk_code_uri
        """
        origin_dir = self._cdk_path / cdk_code_uri
        if not handler_file:
            for file in origin_dir.iterdir():
                if file.is_file and file.name != "__init__.py"\
                        and file.name.endswith(".py"):
                    handler_file = file.name
        else:
            if not handler_file.endswith(".py"):
                handler_file += ".py"

        if not handler_file:
            # not found: return cdk_code_uri added dirname ("cdk.out")
            return self._cdk_path.name + "/" + cdk_code_uri

        handler_path = origin_dir / handler_file

        with open(handler_path, "rb") as f:
            hash = hashlib.md5(f.read()).hexdigest()
        code_uri = self._md5s.get(hash, "")
        if code_uri:
            return "/".join(code_uri.split("/")[:-1])
        else:
            # not found: return cdk_code_uri added dirname ("cdk.out")
            return self._cdk_path.name + "/" + cdk_code_uri
