
from copy import deepcopy
from pathlib import Path

from sapimo.utils import LogManager, add_element
from sapimo.parser.cf_resource_parser import CfResourceParser
from sapimo.constants import EventType, AuthType
from sapimo.exceptions import SamTemplateParseError, DockerFileParseError
from sapimo.parser.image_info import ImageInfo

logger =LogManager.setup_logger(__file__)


class SamParser(CfResourceParser):
    def __init__(self, filepath: Path, region="us-east-1"):
        super().__init__(filepath, region)

    def _preprocess(self, filepath: Path, region: str):
        """
            override: extract global settings and declare additional member
        """
        self._api_resources = {}
        self._http_api_resources = {}
        super()._preprocess(filepath, region)
        # extract global settings and resolve Fn
        g_props = self._whole.get("Globals", {})
        self._function_globals = self._treat(g_props.get("Function", {}))
        self._api_globals = self._treat(g_props.get("Api", {}))
        self._http_api_globals = self._treat(g_props.get("HttpApi", {}))
        # self._table_globals = self._treat(g_props.get("SimpleTable",{}))

        # additional member
        self._apis = {}  # key:api path,
        self._triggered = {}  # key:trigger bucket name
        self._lambdas = {}  # key: resource name

    def _classification(self, name: str, val: dict):
        """ override: Pick "serverless.function" and treat event """
        props: dict = deepcopy(val.get("Properties", {}))
        if val["Type"] == "AWS::Serverless::Function":
            add_element(props, self._function_globals)
            if props["PackageType"] == "Image":
                try:
                    image_info = ImageInfo(val["Metadata"], self._root)
                    props["CodeUri"] = image_info.code_uri
                    props["Handler"] = image_info.handler
                    if image_info.layers:
                        props["Layers"] = image_info.layers
                    props["Environment"] = {}
                    props["Environment"]["Variables"] = image_info.envs
                except DockerFileParseError as e:
                    logger.warning(e.message)
                    msg += "sapimo: sapimo can't interpret your Dockerfile.\n"\
                        "you must edit mock_api/config.yaml"
                    props["CodeUri"] = "edit here! (e.g. app/)"
                    props["Handler"] = "edit here! (e.g. app.lambda_handler)"
                    props["Environment"]["Variables"] = {"SAMPLE_ENV": "VAL"}
                    props["Layers"] = [
                        "if use outer dir, add here (e.g. /libs)"]
                    logger.warning(msg)
            events = props.pop("Events", {})
            if not events:
                # authorizer etc.
                self._lambdas[name] = val
            for event in events.values():
                if not isinstance(event, dict):
                    continue
                event_type = event.get("Type", "")

                if event_type == "Api":
                    # api integration
                    api_path = event.get("Properties", {}).get("Path", "")
                    method = event.get("Properties", {}).get("Method", "")
                    props["EventType"] = EventType.APIGW.name
                    props["AuthType"] = AuthType.NONE.name
                    if api_path and method:
                        if api_path in self._apis:
                            self._apis[api_path][method] = {
                                "Properties": props}
                        else:
                            self._apis[api_path] = {
                                method: {"Properties": props}}
                    api_id = event.get("Properties", {}).get("RestApiId", "")
                    if api_id:
                        api_rsc = self._api_resources.get(api_id, None)
                        api_rsc = self._treat(api_rsc)
                        if not api_rsc:
                            msg = f"'{api_id}' Api resource"\
                                f"( of function({name}) not found"\
                                "this is ignored"
                            logger.warning(msg)
                        auth = api_rsc.get("Properties", {}).get("Auth", None)
                        if not auth:
                            continue
                        def_auth = auth.get("DefaultAuthorizer", None)
                        if def_auth == "AWS_IAM":
                            props["AuthType"] = AuthType.AWS_IAM.name
                        elif def_auth:
                            authorizer = auth.get("Authorizers", {})\
                                .get(def_auth, {})
                            if "UserPoolArn" in authorizer:
                                props["AuthType"] =\
                                    AuthType.COGNITO_USER_POOLS.name
                                props["Authorizer"] = authorizer["UserPoolArn"]
                            elif "FunctionArn" in authorizer:
                                tp = authorizer.get("FunctionPayloadType", "")
                                if tp == "REQUEST":
                                    props["AuthType"] =\
                                        AuthType.CUSTOM_REQUEST.name
                                else:  # default=TOKEN
                                    props["AuthType"] \
                                        = AuthType.CUSTOM_TOKEN.name
                                    props["AuthSource"] =\
                                        authorizer.get("Identity")
                                props["Authorizer"] = authorizer["FunctionArn"]
                            else:
                                msg = f"'{def_auth}'authorizer is invalid"\
                                    "auth settings is ignored"
                                logger.warning(msg)

                elif event_type == "HttpApi":
                    # api integration
                    api_path = event.get("Properties", {}).get("Path", "")
                    method = event.get("Properties", {}).get("Method", "")
                    auth = event.get("Properties", {}).get("Auth", {})
                    auth_type = auth.get("Authorizer", "NONE").upper()
                    props["EventType"] = EventType.APIGW_V2.name
                    if api_path and method:
                        if api_path in self._apis:
                            self._apis[api_path][method] = {
                                "Properties": props}
                        else:
                            self._apis[api_path] = {
                                method: {"Properties": props}}
                        # set authtype
                        try:
                            props["AuthType"] = AuthType[auth_type].name
                        except KeyError:
                            props["AuthType"] = AuthType.NONE

                elif event_type == "S3":
                    # s3 trigger
                    ev_props = event.get("Properties", {})
                    if "ObjectCreated" in ev_props.get("Events", ""):
                        bucket = ev_props.get("Bucket", "")
                        filter_ = ev_props.get("Filter", None)
                        if bucket:
                            t_props = deepcopy(props)
                            if filter_:
                                t_props["Filter"] = filter_
                            self._triggered[bucket] = {"Properties": t_props}
                    else:
                        self._others[name] = val
                else:
                    # other event (unused)
                    self._others[name] = val
        else:
            super()._classification(name, val)

    def _get_config_dict(self) -> dict:
        """ override: add api paths """
        config = super()._get_config_dict()
        config["paths"] = self._apis
        if self._lambdas:
            config["lambdas"] = self._lambdas
        # if self._triggered:
        #     config["triggered"] = self._triggered
        return config

    def _get_ref_and_attr(self, name: str, resource: dict):
        """
            override: for "AWS::Serverless::~~
            retain api and httpAPI resources (for auth)
        """
        tp = resource["Type"]
        props = resource["Properties"]
        if tp == "AWS::Serverless::Function":
            return {"Ref": name, "Arn": self._arn_tmp.format("function", name)}
        elif tp == "AWS::Serverless::Api":
            props: dict = deepcopy(props)
            add_element(props, self._api_globals)
            self._api_resources[name] = props
            return {"Ref": name}
        elif tp == "AWS::Serverless::HttpApi":
            props: dict = deepcopy(props)
            add_element(props, self._http_api_globals)
            self._http_api_resources[name] = props
            return {"Ref": name}  # resource ip id
        elif tp == "AWS::Serverless::Application":
            return {"Ref": name,  # stack resource name
                    "Outputs.ApplicationOutputName": "dummyOutputName"}
        elif tp == "AWS::Serverless::LayerVersion":
            return {"Ref": props.get("ContentUri", name)}  # original
        elif tp == "AWS::Serverless::SimpleTable":
            return {"Ref": props.get("TableName", name)}
        elif tp == "AWS::Serverless::StateMachine":
            return {"Ref": self._arn_tmp.format("stateMachine", name)}
        else:
            return super()._get_ref_and_attr(name, resource)
