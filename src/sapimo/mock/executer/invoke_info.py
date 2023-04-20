import datetime
import uuid
import json
import base64

from jose import jwt
from jose.utils import base64url_decode
from fastapi.requests import Request

from sapimo.constants import EventType
from sapimo.constants import AuthType
from sapimo.utils import LogManager
logger = LogManager.setup_logger(__file__)


class InvokeInfo:
    def __init__(self, src: dict):
        self._props = src["Properties"]
        dirs = [d for d in self._props["CodeUri"].split("/") if d]
        handler_prefix = ".".join(dirs)
        handler = handler_prefix + "." + self._props["Handler"]
        self.code_uri = self._props["CodeUri"]
        self.import_path = ".".join(handler.split(".")[:-1])
        self.func = handler.split(".")[-1]
        self.layers = self._props.get("Layers", [])
        self.runtime = self._props.get("Runtime", "")
        self.environ = self._props.get("Environment", {}).get("Variables", {})
        self.event_type = EventType[self._props.get("EventType", "APIGW")]

    async def to_event(self, reqOrStr):
        pass


class ApiInfo(InvokeInfo):
    def __init__(self, path: str, method: str, src: dict):
        super().__init__(src)
        self.auth = AuthType[self._props["AuthType"]]
        self.authorizer = self._props.get("Authorizer", None)
        self.path = path
        self.method = method
        self.auth_res_context = None

        responses = src.get("responses", {})
        self.responses = {}
        succeed = None
        redirection = None
        client_error = None
        server_error = None
        for k, v in responses.items():
            res = ApiResponse(k, v)
            self.responses[k] = res
            if not succeed and str(k).startswith("20"):
                succeed = res
            elif not redirection and str(k).startswith("30"):
                redirection = res
            elif not client_error and str(k).startswith("40"):
                client_error = res
            elif not server_error and str(k).startswith("50"):
                server_error = res
        self.responses.setdefault(200, succeed or ApiResponse(200, {}))
        self.responses.setdefault(300, redirection or ApiResponse(300, {}))
        self.responses.setdefault(400, client_error or ApiResponse(400, {}))
        self.responses.setdefault(500, server_error or ApiResponse(500, {}))

    async def to_event(self, req: Request):
        """
        convert request to lambda event
        """
        src = await EventSource.wrap(req, self.auth)

        request_context = {
            "accountId": src.dummy_ac_id,
            "apiId": src.dummy_api_id,
            "domainName": src.domain_name,
            "extendedRequestId": None,
            "httpMethod": src.http_method,
            "identity": {
                "accountId": None,
                "apiKey": None,
                "caller": None,
                "cognitoAuthenticationProvider": None,
                "cognitoAuthenticationType": None,
                "cognitoIdentityPoolId": None,
                "sourceIp": src.scope_id,
                "user": None,
                "userAgent": src.dummy_ua,
                "userArn": None
            },
            "path": src.template_path,
            "protocol": src.protocol,
            "requestId": src.request_id,
            "requestTime": src.request_time,
            "requestTimeEpoch": src.request_epoch,
            "resourceId": src.dummy_res_id,
            "resourcePath": src.template_path,
            "stage": "Prod"
        }

        msg = {
            "body": src.body,
            "headers": src.headers,
            "httpMethod": src.http_method,
            "multiValueHeaders": src.multi_headers,
            "multiValueQueryStringParameters": src.multi_query,
            "path": src.path,
            "pathParameters": src.path_params,
            "queryStringParameters": src.query_params,
            "requestContext": request_context,
            "resource": src. template_path,
            "stageVariables": None,  #
            "isBase64Encoded": False,  #
            "version": "1.0",  #
        }
        return msg


class ApiV2Info(ApiInfo):
    async def to_event(self, req: Request):
        """
        convert request to lambda event
            isBase64Encoded, stageVariables, version etc. is invalid(dummy)
        """
        # headers
        src = await EventSource.wrap(req, self.auth)

        msg = {
            "version": "2.0",
            "routeKey": src.route_key,
            "rawPath": src.raw_path,
            "rawQueryString": src.raw_query_strings,
            "cookies": src.cookies,
            "body": src.body,
            "headers": src.headers,
            "queryStringParameters": src.query_params,
            "pathParameters": src.path_params,
            "requestContext": {
                "routeKey": src.route_key,
                "accountId": src.dummy_ac_id,
                "stage": src.dummy_stage,
                "requestId": src.request_id,
                "apiId": src.dummy_api_id,
                "authentication": src.authentication,
                "domainName": src.domain_name,
                "domainPrefix": "id",
                "time": src.request_time,
                "timeEpoch": src.request_epoch,
                "http": {
                    "method": src.http_method,
                    "path": src.path,
                    "protocol": src.protocol,
                    "sourceIp": "IP",
                    "userAgent": src.dummy_ua
                }
            },
            "stageVariables": {},
            "isBase64Encoded": False
        }
        if src.authorizer:
            msg["requestContext"]["authorizer"] = src.authorizer
        elif self.auth_res_context:
            msg["requestContext"]["authorizer"] = self.auth_res_context
        return msg


class TokenAuthorizerInfo(InvokeInfo):
    def __init__(self, auth_header="Authorization"):
        self._auth_header = auth_header

    async def to_event(self, req: Request):
        src = await EventSource.wrap(req, AuthType.NONE)
        token = src.headers.get(self._auth_header)
        if not token:
            msg = f"authorization token header ({self._auth_header}) not found"
            logger.warning(msg)
            return None
        else:
            return {
                "type": "TOKEN",
                "authorizationToken": token,
                "methodArn": "dummy"
            }


class RequestAuthorizerInfo(ApiV2Info):
    async def to_event(req: Request):
        res = super().to_event(req)
        src = await EventSource.wrap(req, AuthType.NONE)
        res.pop("body")
        res["type"] = "REQUEST"
        # for v1 (coarse compatible)
        res["httpMethod"] = res["requestContext"]["http"]["method"]
        res["resourcePath"] = src.template_path


class ApiResponse:
    def __init__(self, code: int, src: dict):
        self.code = code
        self._example = self._dig_out(src, "example")

    def example(self):
        return {
            "statusCode": self.code,
            "body": json.dumps(self._example),
        }

    def _dig_out(self, d: dict, key: str) -> dict:
        for k, v in d.items():
            if k == key:
                return v
            elif isinstance(v, dict):
                return self._dig_out(v)
        else:
            return {}


class EventSource:
    dummy_ac_id = "123456789012"
    dummy_api_id = "1234567890"
    dummy_ua = "Custom User Agent String"
    dummy_res_id = "123456"
    dummy_stage = "Prod"

    def __init__(self):
        self.request: Request
        self.body: str
        self.template_path: str
        self.headers: dict
        self.multi_headers: dict
        self.authorizer: dict = {}
        self.query_params: dict
        self.multi_query: dict
        self.request_time: str
        self.request_epoch: int
        self.protocol: str
        self.domain_name: str
        self.http_method: str
        self.path: str
        self.request_id: str
        self.path_params: dict

        self.raw_path: str
        self.route_key: str
        self.raw_query_strings: str
        self.cookies: dict
        self.authentication: dict
        self.scope_id: str

    @classmethod
    async def wrap(cls, req: Request, authType: AuthType):
        self = EventSource()
        self.request = req
        body_data = await req.body()
        self.body = body_data.decode("utf-8")
        self.template_path = req.scope["route"].path
        # headers
        header_dict = dict(req.headers)
        self.headers = {}
        self.multi_headers = {}
        for key, value in header_dict.items():
            k = "-".join([w.capitalize() for w in key.split("-")])
            if isinstance(value, list):
                value = value[0]
                values = value
            else:
                values = [value]
            self.headers[k] = value
            self.multi_headers[k] = values

        # no verify!
        if authType in [AuthType.JWT, AuthType.COGNITO_USER_POOLS]:
            # only extract claims
            try:
                token = header_dict["authorization"].replace(
                    "Bearer ", "").strip()
                claims = jwt.get_unverified_claims(token)
                self.authorizer = {"jwt": {"claims": claims, "scopes": None}}
            except:
                self.authorizer = None
        elif authType == AuthType.AWS_IAM:
            # set dummy authorizer (as cognito credentials)
            self.authorizer = {
                "iam": {
                    "accessKey": "AKIAXXXXXXXXXXXXXXXX",
                    "accountId": "1234567890",
                    "callerId": "XXXXXXXXXXXXXXX:CognitoIdentityCredentials",
                    "cognitoIdentity": {
                        "amr": ["foo"],
                        "identityId": "us-east-1:identity-id",
                        "identityPoolId": "us-east-1:pool-id"
                    },
                    "principalOrgId": "principal-org-id",
                    "userArn": "arn:aws:iam::1234567890:user/Admin",
                    "userId": "XXXXXXXXXXXXXXXX"
                }
            }
        elif authType in [AuthType.CUSTOM, AuthType.CUSTOM_REQUEST, AuthType.CUSTOM_TOKEN]:
            # work in lambda invoker
            pass

        # query param
        self.query_params = {}
        self.multi_query = {}
        for key, value in req.query_params.items():
            if isinstance(value, list):
                value = value[0]
                values = value
            else:
                values = [value]
            self.query_params[key] = value
            self.multi_query[key] = values

        # dummy request context
        now = datetime.datetime.now(datetime.timezone.utc)
        self.request_time = now.strftime("%d/%b/%Y:%H:%M:%S %z")
        self.request_epoch = int(now.timestamp())
        self.protocol = "HTTP/" + req.scope["http_version"] \
            if req.scope["type"] == "http" else req.scope["type"]
        self.domain_name = req.url.netloc
        self.http_method = req.method
        self.path = req.url.path
        self.request_id = str(uuid.uuid4())
        self.path_params = dict(req.path_params)
        self.raw_path = req.url._url
        self.scope_id = req.scope["client"][0]

        # for apigw2
        self.route_key = req.method.upper() + " " + self.path
        self.raw_query_strings = self.raw_path.split("?")[-1]
        self.cookies = [k+"="+v for k, v in req.cookies.items()]
        self.authentication = {
            "clientCert": {
                "clientCertPem": "-----BEGIN CERTIFICATE-----\nxxxxxx...",
                "issuerDN": "C=US,ST=Washington,L=Seattle,O=Amazon Web Services,OU=Security,CN=My Private CA",
                "serialNumber": "1",
                "subjectDN": "C=US,ST=Washington,L=Seattle,O=Amazon Web Services,OU=Security,CN=My Client",
                "validity": {
                    "notAfter": "Sep 20 11:26:57 2123 GMT",
                    "notBefore": "Nov 8 11:26:57 2023 GMT"
                }
            }
        }
        return self
