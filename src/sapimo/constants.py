from pathlib import Path
from enum import Enum

WORKING_DIR = Path.cwd() / "api_mock"
API_FILE = WORKING_DIR / "app.py"
CONFIG_FILE = WORKING_DIR / "config.yaml"


class EventType(Enum):
    APIGW = 1
    APIGW_V2 = 2
    EVENTBRIDGE = 3
    S3 = 4
    DYNAMODB = 5
    SQS = 6
    SNS = 7


class AuthType(Enum):
    """Authorization Type in AWS::APIGateway(V1 and V2)"""

    NONE = 0
    JWT = 1
    AWS_IAM = 2
    CUSTOM = 3  # apigw_v2 lambda auth
    CUSTOM_TOKEN = 4
    CUSTOM_REQUEST = 5
    COGNITO_USER_POOLS = 6
    # Additional auth types for modern CDK
    OAUTH2 = 7
    OPENID_CONNECT = 8
    API_KEY = 9
    RESOURCE_POLICY = 10
