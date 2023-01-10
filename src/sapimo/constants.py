from pathlib import Path
from enum import Enum

WORKING_DIR = Path.cwd() / "mock_api"
API_FILE = WORKING_DIR / "app.py"
CONFIG_FILE = WORKING_DIR / "config.yaml"


class EventType(Enum):
    APIGW = 1
    APIGW_V2 = 2


class AuthType(Enum):
    """ Authorization Type in AWS::APIGateway(V1 and V2) """
    NONE = 0
    JWT = 1
    AWS_IAM = 2
    CUSTOM = 3  # apigw_v2 lambda auth
    CUSTOM_TOKEN = 4
    CUSTOM_REQUEST = 5
    COGNITO_USER_POOLS = 6
