from abc import ABC, abstractmethod
import hashlib
from pathlib import Path
import os
import json
import logging
import base64
from typing import Union, List
from decimal import Decimal, InvalidOperation, Rounded

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

from sapimo.constants import WORKING_DIR
from sapimo.parser.config_parser import ConfigParser

logger = logging.getLogger(__file__)


class AwsMock(ABC):
    def start(self):
        self._mock.start()

    def stop(self):
        self._mock.stop()

    @abstractmethod
    def init_data():
        """
        this is called after 'mock.start'
        (__init__ is called before 'mock.start')
        """
        pass

    @abstractmethod
    def sync() -> dict:
        pass

    @staticmethod
    def CreateMock(name: str, config: dict):
        if name == "s3":
            return S3Mock(config)
        elif name == "dynamodb":
            return DynamoMock(config)
        elif name == "sqs":
            return SqsMock(config)
        elif name == "sns":
            return SnsMock(config)
        elif name == "ses":
            return SesMock(config)
        elif name == "cognito":
            return CognitoMock(config)


class CognitoMock(AwsMock):
    service_name = "cognito"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config
        self._local_path = WORKING_DIR / "cognito"
        self._pool_ids: dict[str, str] = {}  # pool_name -> UserPoolId
        self._client_ids: dict[
            str, dict[str, str]
        ] = {}  # pool_name -> {client_name -> ClientId}

        self._local_path.mkdir(exist_ok=True)
        for pool_name in self._config.keys():
            (self._local_path / pool_name).mkdir(exist_ok=True)

    def init_data(self):
        self._client = boto3.client("cognito-idp", region_name="us-east-1")

        for pool_name, pool_config in self._config.items():
            # Create User Pool
            pool_resp = self._client.create_user_pool(
                PoolName=pool_config.get("PoolName", pool_name),
                AutoVerifiedAttributes=pool_config.get("AutoVerifiedAttributes", []),
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": 8,
                        "RequireUppercase": False,
                        "RequireLowercase": False,
                        "RequireNumbers": False,
                        "RequireSymbols": False,
                    }
                },
            )
            pool_id = pool_resp["UserPool"]["Id"]
            self._pool_ids[pool_name] = pool_id
            self._client_ids[pool_name] = {}

            # Create User Pool Clients
            for client_config in pool_config.get("Clients", []):
                client_name = client_config.get("ClientName", "default")
                explicit_auth_flows = client_config.get(
                    "ExplicitAuthFlows", ["USER_PASSWORD_AUTH"]
                )
                # moto expects ALLOW_ prefix for auth flows
                auth_flows = []
                for flow in explicit_auth_flows:
                    if not flow.startswith("ALLOW_"):
                        auth_flows.append(f"ALLOW_{flow}")
                    else:
                        auth_flows.append(flow)

                client_resp = self._client.create_user_pool_client(
                    UserPoolId=pool_id,
                    ClientName=client_name,
                    ExplicitAuthFlows=auth_flows,
                    GenerateSecret=False,
                )
                client_id = client_resp["UserPoolClient"]["ClientId"]
                self._client_ids[pool_name][client_name] = client_id

            # Load initial users from data.json
            pool_path = self._local_path / pool_name
            data_file = pool_path / "data.json"
            if data_file.exists() and data_file.stat().st_size >= 2:
                with open(data_file, "r") as f:
                    users = json.load(f)
                if not isinstance(users, list):
                    users = [users]

                # Need a client_id for sign_up; use the first client
                first_client_id = next(iter(self._client_ids[pool_name].values()), None)
                if first_client_id:
                    for user in users:
                        username = user.get("username")
                        password = user.get("password")
                        if not username or not password:
                            continue
                        user_attributes = []
                        if "email" in user:
                            user_attributes.append(
                                {"Name": "email", "Value": user["email"]}
                            )
                        self._client.sign_up(
                            ClientId=first_client_id,
                            Username=username,
                            Password=password,
                            UserAttributes=user_attributes,
                        )
                        self._client.admin_confirm_sign_up(
                            UserPoolId=pool_id,
                            Username=username,
                        )

            logger.info(
                f"Cognito pool '{pool_name}' created: PoolId={pool_id}, "
                f"Clients={list(self._client_ids[pool_name].keys())}"
            )

    def sync(self) -> dict:
        changed_pools = []
        for pool_name, pool_id in self._pool_ids.items():
            resp = self._client.list_users(UserPoolId=pool_id)
            users = [
                {
                    "username": u["Username"],
                    "status": u["UserStatus"],
                    "attributes": {
                        a["Name"]: a["Value"] for a in u.get("Attributes", [])
                    },
                }
                for u in resp.get("Users", [])
            ]

            pool_path = self._local_path / pool_name
            data_file = pool_path / "data.json"
            with open(data_file, "w") as f:
                json.dump(users, f, indent=4, ensure_ascii=False)
            changed_pools.append(pool_name)

        return {"pools": changed_pools}

    def get_pool_id(self, pool_name: str) -> str | None:
        return self._pool_ids.get(pool_name)

    def get_client_id(self, pool_name: str, client_name: str) -> str | None:
        return self._client_ids.get(pool_name, {}).get(client_name)


class SnsMock(AwsMock):
    service_name = "sns"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config

    def init_data(self):
        """SNS初期化（現在は何もしない）"""
        pass

    def sync(self) -> dict:
        """SNS同期（現在は何もしない）"""
        return {}


class SesMock(AwsMock):
    service_name = "ses"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config

    def init_data(self):
        """SES初期化（現在は何もしない）"""
        pass

    def sync(self) -> dict:
        """SES同期（現在は何もしない）"""
        return {}


class SqsMock(AwsMock):
    service_name = "sqs"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config
        self._sqs_local_path = WORKING_DIR / "sqs"
        self._last_messages = {}

        # create local dir, if not exist
        self._sqs_local_path.mkdir(exist_ok=True)

    def init_data(self):
        """
        create sqs queue and upload message
        """
        self._client = boto3.client("sqs", region_name="us-east-1")
        self._url_map = {}
        for key, value in self._config.items():
            name = value.pop("QueueName", key)
            tags = {t["Key"]: t["Value"] for t in value.pop("Tags", [])}
            atrs = [
                "DelaySeconds",
                "MaximumMessageSize",
                "MessageRetentionPeriod",
                "ReceiveMessageWaitTimeSeconds",
                "RedrivePolicy",
            ]
            attributes = {k: v for k, v in value.items() if k in atrs}
            url = self._client.create_queue(
                QueueName=name, Attributes=attributes, tags=tags
            )["QueueUrl"]
            self._last_messages[key] = []
            self._url_map[key] = url

            # send message in local
            queue_path = self._sqs_local_path / key
            queue_path.mkdir(exist_ok=True)
            files = sorted([f for f in queue_path.iterdir() if f.is_file()])
            for file in files:
                with open(file, "r") as f:
                    msg = f.read()
                self._client.send_message(QueueUrl=url, MessageBody=msg)
                file.unlink()

    def sync(self) -> dict:
        """
        sync  (sqs message -> local dir)
        """
        for queue in self._config.keys():
            res = self._client.receive_message(
                QueueUrl=self._url_map[queue],
                VisibilityTimeout=0,
                MaxNumberOfMessages=10,
            )
            queue_path: Path = self._sqs_local_path / queue
            messages = res.get("Messages", [])
            if not messages:
                continue
            # msgs = {m["MessageId"]: m.get("Body", "") for m in messages }
            msgs = [m["Body"] for m in messages if "Body" in m]
            if msgs == self._last_messages[queue]:
                continue

            # detect change message
            for file in queue_path.iterdir():
                if file.is_file():
                    file.unlink()

            for i, body in enumerate(msgs):
                with open(queue_path / (str(i).zfill(4) + ".txt"), "w") as f:
                    f.write(body)
            self._last_messages[queue] = msgs
        return {}


class S3Mock(AwsMock):
    service_name = "s3"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config
        self._s3_local_path = WORKING_DIR / "s3"
        self._hashes = {}

        # create local dir, if not exist
        self._s3_local_path.mkdir(exist_ok=True)
        for bucket in self._config.keys():
            bucket_path = self._s3_local_path / bucket
            bucket_path.mkdir(exist_ok=True)

    def init_data(self):
        """
        upload file (local dir -> s3 bucket)
        """
        self._s3 = boto3.resource("s3", region_name="us-east-1")
        self._client = boto3.client("s3", region_name="us-east-1")

        for dir in self._s3_local_path.iterdir():
            if dir.is_file():
                continue  # regard dir as a bucket, file is ignored
            bucket_name = dir.name
            self._s3.create_bucket(Bucket=bucket_name)
            bucket = self._s3.Bucket(bucket_name)
            self._hashes[bucket_name] = {}
            bucket_path = self._s3_local_path / bucket_name
            for file in dir.glob("**/*"):
                if file.is_dir():
                    continue
                with open(file, "rb") as f:
                    data = f.read()
                    key = str(file).replace(str(bucket_path), "")[1:]
                    # print(key)
                    bucket.Object(key).put(Body=data)
                    hash = hashlib.md5(data).hexdigest()
                    self._hashes[bucket_name][key] = hash

    def sync(self) -> dict:
        """
        sync  (s3 bucket -> local dir)

        return ({ bucket:[updated_keys] },{ bucket:[deleted_keys] })
        """
        buckets = [m["Name"] for m in self._client.list_buckets()["Buckets"]]
        # print(buckets)
        res_updated = {}
        res_deleted = {}
        for bucket_name in buckets:
            bucket_path = self._s3_local_path / bucket_name
            if not bucket_path.exists():
                bucket_path.mkdir()
                self._hashes[bucket_name] = {}
            bucket = self._s3.Bucket(bucket_name)
            keys = [obj.key for obj in bucket.objects.all()]
            new_hashes = {}
            updated = []
            for key in keys:
                data = bucket.Object(key).get()["Body"].read()
                hash = hashlib.md5(data).hexdigest()
                new_hashes[key] = hash

                if (
                    key not in self._hashes[bucket_name]
                    or self._hashes[bucket_name][key] != hash
                ):
                    # if s3 file is updated/created, update/create local file
                    key_parts = key.split("/")
                    target_path: Path = bucket_path
                    for k in key_parts:
                        if not target_path.exists():
                            target_path.mkdir()
                        target_path = target_path / k
                    with open(target_path, "wb") as f:
                        f.write(data)
                    updated.append(key)
            if updated:
                res_updated[bucket_name] = updated

            # remove deleted file
            deleted = set(self._hashes[bucket_name].keys()) - set(new_hashes.keys())
            for key in deleted:
                target_path = str(bucket_path) + "/" + key
                if os.path.exists(target_path):
                    os.remove(target_path)
            self._hashes[bucket_name] = new_hashes
            if deleted:
                res_deleted[bucket_name] = list(deleted)
        return {"updated": res_updated, "deleted": res_deleted}


class DynamoMock(AwsMock):
    service_name = "dynamodb"

    def __init__(self, config: dict):
        self._mock = mock_aws()
        self._config = config
        self._local_dynamo_path = WORKING_DIR / "dynamodb"

        # create local dir, if not exist
        self._local_dynamo_path.mkdir(exist_ok=True)
        for table in self._config.keys():
            table_path = self._local_dynamo_path / table
            table_path.mkdir(exist_ok=True)

    def init_data(self):
        self._dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        for name, props in self._config.items():
            self._dynamodb.create_table(
                **props  # ok?
                # TableName=name,
                # KeySchema=props["KeySchema"],
                # AttributeDefinitions=props["AttributeDefinitions"],
                # ProvisionedThroughput=props["ProvisionedThroughput"]
            )
            table = self._dynamodb.Table(name)
            file: Path = self._local_dynamo_path / name / "data.json"
            csv_file: Path = self._local_dynamo_path / name / "results.csv"
            data = []

            if file.exists():
                if file.stat().st_size < 4:  # skip if empty file
                    print(f"{file} is empty.")
                else:
                    with open(file, "r") as f:
                        data = json.load(f, parse_float=Decimal)
                    if not isinstance(data, list):
                        data = [data]
            elif csv_file.exists():
                # for exported csv file from real AWS DynamoDB Table
                # (in this case, double quotation is removed)
                with open(csv_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                headers = [h.strip().strip('"') for h in lines[0].split(",")]
                data = []
                for rec in lines[1:]:
                    row = {col: d for col, d in zip(headers, self.read_record_csv(rec))}
                    data.append(row)
            else:
                continue

            try:
                with table.batch_writer() as batch:
                    for row in data:
                        batch.put_item(Item=row)

            except ClientError as e:
                logger.exception("dynamo init data error")
                # TODO

    def read_record_csv(self, row: str) -> List[Union[str, Decimal, list, dict, set]]:
        """read one row of results.csv (from aws dynamo db table)"""
        cells = []
        elms = row.split(",")
        tmp = ""
        for elm in elms:
            tmp = (tmp + "," + elm) if tmp != "" else elm
            if tmp.count("[") == tmp.count("]") and tmp.count("{") == tmp.count("}"):
                cells.append(self.interpret_dynamo_cell(tmp))
                tmp = ""
        return cells

    def interpret_dynamo_cell(self, elm: str) -> Union[str, Decimal, list, dict, set]:
        val = elm.strip().strip('"')
        if val.startswith("["):  # dynamo set or list
            val = val[1:-1]  # remove []
            if val:
                res = [self.interpret_dynamo_cell(n) for n in val.split(",")]
                return res if val.startswith("{") else set(res)
            else:
                return []
        if val.startswith("{"):  # dynamo map or list item
            pair = val[1:-1]  # remove {}
            if not pair:
                return {}
            key, *rest = pair.split(":")
            v = ":".join(rest)
            key = key.strip('"')
            v = v.strip('"')
            if key == "S":
                return v.strip('"')
            elif key == "N":
                return Decimal(v.strip('"'))
            elif key == "BOOL":
                return bool(val.strip('"'))
            elif key == "B":
                return base64.b64decode(v.strip('"'))
            elif key in ["SS", "NS", "BS", "L", "M"]:
                return self.interpret_dynamo_cell(v)
            else:
                return {key: self.interpret_dynamo_cell(v)}
        else:
            val = val.strip("'")
            try:
                val = Decimal(val)
                return val
            except InvalidOperation:
                return val

    def sync(self) -> dict:
        def obj_to_item(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            if isinstance(obj, set):
                return list(obj)

        changed_table = []
        for name in self._config.keys():
            table = self._dynamodb.Table(name)
            items = []
            response = table.scan()
            items.extend(response.get("Items", []))
            while "LastEvaluatedKey" in response:
                response = table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))
            file: Path = self._local_dynamo_path / name / "data.json"
            if len(items):
                local = []
                if file.exists():
                    with open(file, "r") as f:
                        local = json.load(f, parse_float=Decimal)

                if local != items:
                    changed_table.append(name)
                    with open(file, "w") as f:
                        json.dump(
                            items, f, indent=4, ensure_ascii=False, default=obj_to_item
                        )
            else:
                if file.exists():
                    file.unlink()

        return {"tables": changed_table}


class MockManager:
    def __init__(self, config_file):
        config = ConfigParser(config_file)
        services = ["s3", "dynamodb", "sns", "sqs", "ses", "cognito"]
        self._services = []
        self._changed = {}
        for service in services:
            service_config = config.get_service_config(service)
            if service_config:
                mock = AwsMock.CreateMock(service, service_config)
                self._services.append(mock)

    def start(self):
        for mock in self._services:
            mock.start()
        logger.info(f"start aws mock:{[m.service_name for m in self._services]}")

    def stop(self):
        for mock in self._services:
            mock.stop()
        logger.info(f"stop aws mock:{[m.service_name for m in self._services]}")

    def init_data(self):
        for mock in self._services:
            mock.init_data()

    def sync(self):
        for mock in self._services:
            self._changed[mock.service_name] = mock.sync()

    def get_change(self, service: str):
        return self._changed.get(service, {})

    def _get_cognito_mock(self) -> CognitoMock | None:
        for mock in self._services:
            if isinstance(mock, CognitoMock):
                return mock
        return None

    def get_cognito_pool_id(self, pool_name: str) -> str | None:
        cognito = self._get_cognito_mock()
        return cognito.get_pool_id(pool_name) if cognito else None

    def get_cognito_client_id(self, pool_name: str, client_name: str) -> str | None:
        cognito = self._get_cognito_mock()
        return cognito.get_client_id(pool_name, client_name) if cognito else None

    def resolve_placeholders(self, env: dict[str, str]) -> dict[str, str]:
        """Resolve ${cognito:...} placeholders in environment variables."""
        import re

        cognito = self._get_cognito_mock()
        if not cognito:
            return env

        pattern = re.compile(r"\$\{cognito:([^}]+)\}")
        resolved = {}
        for key, value in env.items():
            value_str = str(value)
            match = pattern.search(value_str)
            if match:
                parts = match.group(1).split(":")
                replacement = None
                if len(parts) == 2 and parts[1] == "PoolId":
                    replacement = cognito.get_pool_id(parts[0])
                elif len(parts) == 3 and parts[1] == "ClientId":
                    replacement = cognito.get_client_id(parts[0], parts[2])

                if replacement:
                    resolved[key] = pattern.sub(replacement, value_str)
                else:
                    logger.warning(f"Unresolved Cognito placeholder: {value_str}")
                    resolved[key] = value_str
            else:
                resolved[key] = value_str
        return resolved
