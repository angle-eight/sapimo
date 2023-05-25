import os
import importlib
import sys
from pathlib import Path
import json
from typing import Union

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from sapimo.parser.config_parser import ConfigParser
from sapimo.utils import LogManager
from sapimo.mock.executer.invoke_info import \
    ApiInfo, ApiV2Info, InvokeInfo, TokenAuthorizerInfo, RequestAuthorizerInfo
from sapimo.constants import EventType, AuthType
from sapimo.exceptions import LambdaInvokeError, EventConvertError
from logging import DEBUG

logger = LogManager.setup_logger(__file__, level=DEBUG)


class LambdaInvoker:
    """
        - retain path config
        - setup s3 and dynamodb in local dir (if required)
        - setup and execute lambda python code
    """

    def __init__(self, path: Path):
        """
            - set config
            - setup s3 and dynamodb
        """
        self._config = ConfigParser(path)

    def _get_api_info(self, req: Request):
        path = req.scope["route"].path
        method = req.method.lower()

        try:
            src = self._config.apis[path][method]
            event_type = EventType[src["Properties"].get("EventType", "APIGW")]
            if event_type == EventType.APIGW:
                return ApiInfo(path, method, src)
            elif event_type == EventType.APIGW_V2:
                return ApiV2Info(path, method, src)
        except:
            logger.exception("")
            logger.warning(f"{path}:{method} execute info is not found")
            return None

    async def run_by_trigger(self, updated: dict, deleted: dict):
        """
            lambda execution when s3 file is updated
            - interpret trigger rules
        """
        if not self._config.triggered:
            return
        raise NotImplementedError()

    async def auth_api(self, props: ApiInfo, req: Request):
        """
        if api has a lambda authorizer, get additional info
        """
        if props.auth == AuthType.CUSTOM:
            # TODO: Check this flow!
            auth_props = RequestAuthorizerInfo(req)
            res = await self._lambda_exec(auth_props, req)
            props.auth_res_context = res.get("context")
        elif props.auth == AuthType.CUSTOM_REQUEST:
            pass
        elif props.auth == AuthType.CUSTOM_TOKEN:
            auth_props = TokenAuthorizerInfo(req)
            res = await self._lambda_exec(auth_props, req)
            props.auth_res_context = res.get("context")
        else:
            props.auth_res_context = None
            return

    async def run_by_api(self, req: Request):
        """
            api call
            Return:
                API response
        """
        props: ApiInfo = self._get_api_info(req)
        await self.auth_api(props, req)

        try:
            lambda_res = await self._lambda_exec(props, req)
            if(lambda_res is not None):
                status = lambda_res.get("statusCode", 500)
                body = lambda_res.get("body")
            else:
                status = 500
                body = "No response from lambda"
            try:
                if not isinstance(body, dict):
                    body = json.loads(body)
                return JSONResponse(status_code=status, content=body)
            except:
                return Response(status_code=status, content=body)
        except ModuleNotFoundError as e:
            err_msg = "lambda code import error: " + str(e) + "\n"\
                "- check 'CodeUri' or 'Layers'"\
                f" of {props.path}.{props.method} "\
                " in .mock_api/config.yaml\n"\
                "- check if the required modules are installed\n"\
                "- check import section in your code\n"
            logger.error(err_msg)
            return Response(status_code=500, content=err_msg)
        except EventConvertError as e:
            logger.exception("")  # FIXME
            logger.error("request convert error")
            return Response(status_code=400, content=e.message)
        except LambdaInvokeError as e:
            logger.error(e.message)
            logger.exception(e.message)  # FIXME
            return Response(status_code=500, content=e.message)
        except Exception as e:
            logger.exception("lambda error")  # FIXME
            return Response(status_code=500, content=str(e))

    async def _lambda_exec(self, props: InvokeInfo,
                           event_src: Union[Request, str]):
        """
            common process of lambda execution
            - set(change) env
            - import required layer
            - execute lambda code

            Return:
                result of lambda handler
        """
        if not props:
            raise LambdaInvokeError("lambda info is not exist")

        # set env
        self._change_env(props.environ)

        # import layer
        with LayerImporter([*props.layers, props.code_uri]):
            # request to event
            try:
                event = await props.to_event(event_src)
            except Exception as e:
                logger.exception("lambda event convert error")
                raise EventConvertError()


            # import lambda code
            app = importlib.import_module(props.import_path)

            # check handler
            if not props.func in dir(app):
                err_msg = f"lambda entrypoint({props.func}) is\
                            not exist in {props.import_path}"
                raise LambdaInvokeError(err_msg)

            # lambda execution
            try:
                logger.info("--------- PARAMS --------")
                qs =event.get("queryStringParameters", "")
                bd = event.get("body", "")
                logger.info(f"QueryStrings: {qs}")
                logger.info(f"Body: {bd}")
                logger.info("--------- ALL EVENT --------")
                logger.info(event)
                if hasattr(app, "logger"):
                    lam_logger = app.logger
                    log_changer = LogManager(lam_logger)
                    logger.info("--------- LAMBDA LOG --------")
                lambda_res = eval("app."+props.func)(event, None)
                logger.info("---------- RESPONSE ---------")
                logger.info(lambda_res)
                if hasattr(app, "logger"):
                    log_changer.deinit()
                logger.info("-----------------------------")
                return lambda_res
            except Exception as e:
                logger.exception("lambda execute error")
                raise LambdaInvokeError(str(e))

    async def get_example(self, req: Request, status: int):
        props = self._get_api_info(req)
        if "responses" not in props:
            return None
        if not props.responses:
            return None
        example = props.responses[status]

        def search_example(di: dict):
            for key, value in di.items():
                if key == "example":
                    return value
                elif isinstance(value, dict):
                    res = search_example(value)
                    if res:
                        return res
            else:
                return None
        return search_example(example)

    def _change_env(self, env: dict):
        def_env = {
            "HOSTNAME": "fae95fa3f3cb",  # dummy
            "AWS_LAMBDA_FUNCTION_VERSION": "$LATEST",
            "AWS_SAM_LOCAL": "false",
            "AWS_SESSION_TOKEN": "",
            "AWS_SECRET_ACCESS_KEY": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "LANG": "en_US.UTF-8",
            "AWS_ACCESS_KEY_ID": "AKIAXXXXXXXXXXXXXXXX",
            "SHLVL": "0",
            "HOME": "",
            "AWS_REGION": "us-east-1",
            "AWS_DEFAULT_REGION": "us-east-1",
            # FIXME---
            # "LD_LIBRARY_PATH": "/var/lang/lib:/lib64:/usr/lib64:/var/runtime:/var/runtime/lib:/var/task:/var/task/lib:/opt/lib",
            # "PWD": "/var/task",
            # "LAMBDA_TASK_ROOT": "/var/task",
            # "LAMBDA_RUNTIME_DIR": "/var/runtime",
            # "TZ": ":/etc/localtime",
            # "AWS_ACCOUNT_ID": "123456789012",
            # "_HANDLER": "app.lambda_handler",
            # "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "128",
            # "PYTHONPATH": "/var/runtime",
            # "AWS_LAMBDA_FUNCTION_TIMEOUT": "3",
            # "AWS_LAMBDA_LOG_GROUP_NAME": "aws/lambda/dummy", # GreetingFunction",
            # "AWS_LAMBDA_RUNTIME_API": "127.0.0.1:9001",
            # "AWS_LAMBDA_LOG_STREAM_NAME": "$LATEST",
            # "AWS_EXECUTION_ENV": "AWS_Lambda_python3.9",
            # "AWS_LAMBDA_FUNCTION_NAME": "DummyFunction",
            # "PATH": "/var/lang/bin:/usr/local/bin:/usr/bin/:/bin:/opt/bin",
            # "AWS_LAMBDA_FUNCTION_HANDLER": "app.lambda_handler",
        }
        def_env.update(env)

        # delete one by one for avoid memory leak
        for k in os.environ.keys():
            del os.environ[k]
        for k, v in def_env.items():
            os.environ[k] = v

        # TODO:restore env: unnecessary?


class LayerImporter:
    """ append lambda layer's code uri to sys.path """

    def __init__(self, layers: list[str]):
        self._layers = layers
        self._count = len(layers)

    def __enter__(self):
        if self._count == 0:
            return

        for layer in self._layers:
            sys.path.append(layer)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._count == 0:
            return
        remove_targets = set(sys.path[-self._count:])
        if remove_targets != set(self._layers):
            logger.warning("sys.path is changed by lambda function. \
                            layers path can't be removed")
            return
        else:
            for _ in range(self._count):
                sys.path.pop()
