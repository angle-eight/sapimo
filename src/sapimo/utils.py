from typing import Optional
from pathlib import Path
from copy import deepcopy
import logging
import sys


def setup_logger(name: str, level: int = logging.WARNING) -> logging.Logger:
    logger = logging.getLogger(name)
    formatter = logging.Formatter('[%(levelname)s] %(message)s')
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    logger.setLevel(level)
    return logger


logger = setup_logger(__file__)


def search_config() -> Optional[Path]:
    """ search config file """
    filenames = ["config.yml", "config.yaml", "config.json"]
    mock_dir = Path.cwd() / "mock_api"
    mock_dir.mkdir(exist_ok=True)
    for filename in filenames:
        config_filepath = mock_dir / filename
        if config_filepath.exists():
            return config_filepath

    else:
        logger.warning("config file not found")
        return None


def search_api_impl():
    """ search api implementation """
    filename = "app.py"
    mock_dir = Path.cwd() / "mock_api"
    mock_dir.mkdir(exist_ok=True)
    api_filename = mock_dir / filename
    if api_filename.exists():
        return api_filename
    else:
        logger.warning("Mock API implementation file not found")
        return None


def create_config_template(output_path: Path):
    t = """
paths:
  /hello_world: # your API path
    post:       # your API method
      Properties:  # this is Lambda Properties (like aws sam's template)
        CodeUri: lambda/greeting/     # required
        Handler: app.lambda_handler   # required
        Architectures:
        - x86_64
        Environment:
          Variables:
            BucketName: test-bucket
            TableName: test-table
        Layers:
        - my_layer/
        Runtime: python3.9
        Timeout: 3
s3:            # if your lambda uses s3 bucket, "s3" item is required.
  MyBucket:
    BucketName: MyBucket
dynamodb:      # if your lambda uses dynamoDB, "dynamodb" item is required.
  MyTable:
    TableName: MyTable
    AttributeDefinitions:
    - AttributeName: PartitionKey
      AttributeType: S
    - AttributeName: RangeKey
      AttributeType: S
    KeySchema:
    - AttributeName: PartitionKey
      KeyType: HASH
    - AttributeName: RangeKey
      KeyType: RANGE
    ProvisionedThroughput:
      ReadCapacityUnits: 10
      WriteCapacityUnits: 10
    """
    with open(output_path, "w") as f:
        f.write(t)
    return


def add_element(d1: dict, d2: dict):
    """ d1 has priority """
    for k, v in d1.items():
        if isinstance(v, dict) and isinstance(d2.get(k, {}), dict):
            add_element(v, d2.get(k, {}))
    for k, v in d2.items():
        d1.setdefault(k, v)


def dget(src: dict, keys: list[str]):
    d = src
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, {})
    return d
