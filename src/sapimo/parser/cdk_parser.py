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

    def __init__(self, filepath: Path, region: str = "us-east-1"):
        self._cdk_path = filepath.parent
        self._repo_path = self._cdk_path.parent

        # Calculate all file's md5 hashes for asset resolution
        self._md5s = {}

        def save_hash(directory: Path, d: dict) -> None:
            """Recursively calculate MD5 hashes for all files in the directory"""
            try:
                for file in directory.iterdir():
                    if not file.exists():
                        continue

                    code_uri: str = str(file).replace(str(self._repo_path) + "/", "")

                    # Skip hidden files and CDK output directory
                    if code_uri.startswith(".") or self._cdk_path.name in code_uri:
                        continue

                    if file.is_dir():
                        save_hash(file, d)
                    elif file.is_file():
                        try:
                            with open(file, "rb") as f:
                                hash_value = hashlib.md5(f.read()).hexdigest()
                            d[hash_value] = code_uri
                        except (IOError, OSError) as e:
                            logger.warning(f"Could not read file {file}: {e}")

            except (PermissionError, OSError) as e:
                logger.warning(f"Could not access directory {directory}: {e}")

        logger.info(f"Calculating file hashes in {self._repo_path}")
        save_hash(self._repo_path, self._md5s)
        logger.info(f"Found {len(self._md5s)} files for asset resolution")

        # Validate CDK structure before processing
        super().__init__(filepath, region)
        self._validate_cdk_structure()

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

    def _classification(self, name: str, val: dict):
        """
        resource classification ->
            {apis, buckets, tables, lambdas, others}
        """
        props: dict = deepcopy(val.get("Properties", {}))
        tp = val["Type"]

        # Handle newer CDK constructs
        if tp == "AWS::ApiGatewayV2::Route":
            method, api_path = props["RouteKey"].split(" ")
            if api_path not in self._apis:
                self._apis[api_path] = {}
            method = method.lower()
            integration_key = props["Target"].replace("integrations/", "")
            integration_key = self._treat(integration_key)
            lambda_key: dict = (
                self._integrations_map.get(integration_key, {})
                .get("Properties", {})
                .get("IntegrationUri", "")
            )
            auth_type = props.get("AuthorizationType", "")
            lambda_key = self._treat(lambda_key)
            lambda_ = self._lambdas_map.get(lambda_key, {})
            code_uri = lambda_.get("Metadata", {}).get("aws:asset:path", "")
            l_props = lambda_.get("Properties", {})
            atrs = ["Environment", "Handler", "Layers", "Runtime", "TimeOut"]
            api_props = {}
            for k in atrs:
                if k in l_props:
                    api_props[k] = self._treat(l_props[k])
            handler_file = api_props.get("Handler", "").split(".")[0]
            api_props["CodeUri"] = self._search_code_uri(code_uri, handler_file)
            api_props["EventType"] = EventType.APIGW_V2.name
            atype = AuthType[auth_type] if auth_type else AuthType.NONE
            api_props["AuthType"] = atype.name
            layers = self._treat(l_props.get("Layers", []))
            if layers:
                ls = []
                for layer_key in layers:
                    layer = self._layers_map.get(layer_key, {})
                    layer = self._treat(layer)
                    layer_uri = layer.get("Metadata", {}).get("aws:asset:path", "")
                    ls.append(self._search_layer_uri(layer_uri))
                api_props["Layers"] = ls
            self._apis[api_path][method] = {"Properties": api_props}

        elif tp == "AWS::ApiGateway::Method":
            method = props["HttpMethod"].lower()
            api_resource_key = props["ResourceId"]
            api_resource_key = self._treat(api_resource_key)
            api_resource = self._api_resources_map.get(api_resource_key)
            if not api_resource:
                logger.warning(
                    f"API Resource {api_resource_key} not found for method {name}"
                )
                return
            path_parts = (
                api_resource.get("Metadata", {}).get("aws:cdk:path", "").split("/")
            )
            path_parts: list = path_parts
            path_parts = path_parts[path_parts.index("Default") + 1 : -1]
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
                logger.warning(
                    f"Lambda function {lambda_key} not found for API Gateway method {name}"
                )
                return
            api_props = self._api_props_from_lambda(lambda_, auth_type, False)
            self._apis[api_path][method] = {"Properties": api_props}

        elif tp == "AWS::ApiGateway::RestApi":
            # Handle REST API definitions
            self._api_resources_map[name] = val

        elif tp == "AWS::ApiGatewayV2::Api":
            # Handle HTTP API definitions
            self._api_resources_map[name] = val

        elif tp == "AWS::ApiGateway::Authorizer":
            # Handle API Gateway authorizers
            self._api_resources_map[name] = val

        elif tp == "AWS::ApiGatewayV2::Authorizer":
            # Handle HTTP API authorizers
            self._api_resources_map[name] = val

        elif tp == "AWS::Events::Rule":
            # Handle EventBridge rules for Lambda triggers
            self._others[name] = val

        elif tp == "AWS::StepFunctions::StateMachine":
            # Handle Step Functions
            self._others[name] = val

        elif tp == "AWS::Lambda::Permission":
            # Handle Lambda permissions
            self._others[name] = val

        elif tp == "AWS::Lambda::Alias":
            # Handle Lambda aliases
            self._lambdas_map[name] = val

        elif tp == "AWS::Lambda::Version":
            # Handle Lambda versions
            self._lambdas_map[name] = val

        elif tp == "AWS::CloudWatch::Alarm":
            # Handle CloudWatch alarms
            self._others[name] = val

        elif tp == "AWS::Logs::LogGroup":
            # Handle CloudWatch log groups
            self._others[name] = val

        elif tp == "AWS::IAM::Role":
            # Handle IAM roles
            self._others[name] = val

        elif tp == "AWS::IAM::Policy":
            # Handle IAM policies
            self._others[name] = val

        else:
            super()._classification(name, val)

    def _api_props_from_lambda(
        self, lambda_: dict, auth_type: str, is_apigw_v2: bool
    ) -> dict:
        """Extract API properties from Lambda function configuration"""
        code_uri = lambda_.get("Metadata", {}).get("aws:asset:path", "")
        l_props = lambda_.get("Properties", {})

        # Handle both ZIP and container package types
        package_type = l_props.get("PackageType", "Zip")

        atrs = [
            "Environment",
            "Handler",
            "Layers",
            "Runtime",
            "Timeout",
            "MemorySize",
            "ReservedConcurrentExecutions",
        ]
        api_props = {}

        for k in atrs:
            if k in l_props:
                api_props[k] = self._treat(l_props[k])

        # Handle container images
        if package_type == "Image":
            api_props["PackageType"] = "Image"
            image_uri = l_props.get("Code", {}).get("ImageUri", "")
            api_props["CodeUri"] = image_uri or code_uri
            # For container images, handler might be in CMD
            if "Handler" not in api_props:
                api_props["Handler"] = "app.lambda_handler"  # default
        else:
            handler_file = (
                api_props.get("Handler", "").split(".")[0]
                if "Handler" in api_props
                else ""
            )
            api_props["CodeUri"] = self._search_code_uri(code_uri, handler_file)

        api_props["EventType"] = (
            EventType.APIGW_V2.name if is_apigw_v2 else EventType.APIGW.name
        )

        # Enhanced auth type handling
        try:
            atype = AuthType[auth_type] if auth_type else AuthType.NONE
        except KeyError:
            logger.warning(f"Unknown auth type: {auth_type}, defaulting to NONE")
            atype = AuthType.NONE
        api_props["AuthType"] = atype.name

        # Handle layers
        layers = self._treat(l_props.get("Layers", []))
        if layers:
            ls = []
            for layer_key in layers:
                layer = self._layers_map.get(layer_key, {})
                layer = self._treat(layer)
                layer_uri = layer.get("Metadata", {}).get("aws:asset:path", "")
                ls.append(self._search_layer_uri(layer_uri))
            api_props["Layers"] = ls

        return api_props

    def _get_config_dict(self) -> dict:
        """override: add api paths and CDK-specific configurations"""
        config = super()._get_config_dict()
        config["paths"] = self._apis

        # Add CDK-specific metadata
        cdk_metadata = self._extract_cdk_metadata()
        if cdk_metadata:
            config["cdk"] = cdk_metadata

        # Add CloudFormation outputs
        outputs = self._handle_cfn_outputs()
        if outputs:
            config["outputs"] = outputs

        # Add triggered Lambda functions
        if self._triggered:
            config["triggered"] = self._triggered

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
            return {"Ref": name, "Arn": name}  # use pass name instead of arn
        elif tp == "AWS::Lambda::LayerVersion":
            self._layers_map[name] = resource
            return {"Ref": name, "Arn": name}  # pass name instead of arn
        elif tp == "AWS::ApiGateway::Resource":
            self._api_resources_map[name] = resource
            return {"Ref": name, "Arn": name}
        else:
            return super()._get_ref_and_attr(name, resource)

    def _handle_cfn_outputs(self) -> dict:
        """Handle CloudFormation outputs from CDK synthesis"""
        outputs = self._whole.get("Outputs", {})
        processed_outputs = {}

        for name, output in outputs.items():
            value = output.get("Value", "")
            description = output.get("Description", "")
            processed_outputs[name] = {
                "Value": self._treat(value),
                "Description": description,
            }

        return processed_outputs

    def _extract_cdk_metadata(self) -> dict:
        """Extract CDK-specific metadata from the template"""
        template_metadata = self._whole.get("Metadata", {})
        cdk_metadata = {}

        # Extract CDK version info from template level
        if "aws:cdk:path" in template_metadata:
            cdk_metadata["cdkPath"] = template_metadata["aws:cdk:path"]

        # Extract construct tree information
        if "aws:asset:path" in template_metadata:
            cdk_metadata["assetPath"] = template_metadata["aws:asset:path"]

        # Extract CDK metadata from resources
        for resource_name, resource in self._resources.items():
            resource_metadata = resource.get("Metadata", {})
            if resource_metadata:
                cdk_metadata[resource_name] = resource_metadata

        return cdk_metadata

    def _validate_cdk_structure(self) -> bool:
        """Validate that this is a valid CDK CloudFormation template"""
        # Look for CDK markers in resources
        cdk_markers = ["aws:cdk:path", "aws:asset:path", "aws:asset:property"]

        has_cdk_markers = False

        # Check all classified resources for CDK markers
        all_resources = {}
        all_resources.update(self._resources)
        all_resources.update(self._others)

        # Also check the original template resources
        original_resources = self._whole.get("Resources", {})

        for resource in original_resources.values():
            resource_metadata = resource.get("Metadata", {})
            if any(marker in resource_metadata for marker in cdk_markers):
                has_cdk_markers = True
                break

        if not has_cdk_markers:
            logger.warning("Template appears to be standard CloudFormation, not CDK")

        return has_cdk_markers

    def _search_layer_uri(self, cdk_resource_path: str):
        return self._search_code_uri(cdk_resource_path + "/python", handler_file="")

    def _search_code_uri(self, cdk_code_uri: str, handler_file: str = "") -> str:
        """
        Search directory that contains the same file as in cdk_code_uri
        Enhanced to handle various CDK deployment scenarios
        """
        if not cdk_code_uri:
            logger.warning("Empty CDK code URI provided")
            return ""

        origin_dir = self._cdk_path / cdk_code_uri

        # Check if the directory exists
        if not origin_dir.exists():
            logger.warning(f"CDK asset directory not found: {origin_dir}")
            return self._cdk_path.name + "/" + cdk_code_uri

        # Find handler file if not specified
        if not handler_file:
            python_files = [
                f
                for f in origin_dir.iterdir()
                if f.is_file() and f.name != "__init__.py" and f.name.endswith(".py")
            ]
            if python_files:
                handler_file = python_files[0].name
            else:
                # Look for common entry points
                common_names = ["app.py", "lambda_function.py", "index.py", "main.py"]
                for name in common_names:
                    if (origin_dir / name).exists():
                        handler_file = name
                        break
        else:
            if not handler_file.endswith(".py"):
                handler_file += ".py"

        if not handler_file:
            logger.warning(f"No Python handler file found in {origin_dir}")
            return self._cdk_path.name + "/" + cdk_code_uri

        handler_path = origin_dir / handler_file

        if not handler_path.exists():
            logger.warning(f"Handler file not found: {handler_path}")
            return self._cdk_path.name + "/" + cdk_code_uri

        try:
            with open(handler_path, "rb") as f:
                hash_value = hashlib.md5(f.read()).hexdigest()

            code_uri = self._md5s.get(hash_value, "")
            if code_uri:
                return "/".join(code_uri.split("/")[:-1])
            else:
                logger.warning(f"Hash not found for {handler_path}, using CDK path")
                return self._cdk_path.name + "/" + cdk_code_uri

        except Exception as e:
            logger.error(f"Error reading handler file {handler_path}: {e}")
            return self._cdk_path.name + "/" + cdk_code_uri
