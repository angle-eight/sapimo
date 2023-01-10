from abc import ABC, abstractmethod
import hashlib
from pathlib import Path
import os
import json
import logging

import boto3
from botocore.exceptions import ClientError
from moto import mock_s3, mock_dynamodb, mock_sqs, mock_sns, mock_ses

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
    def sync():
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


class SnsMock(AwsMock):
    service_name = "sns"

    def __init__(self, config: dict):
        self._mock = mock_sns()
        self._config = config


class SesMock(AwsMock):
    service_name = "ses"

    def __init__(self, config: dict):
        self._mock = mock_ses()
        self._config = config


class SqsMock(AwsMock):
    service_name = "sqs"

    def __init__(self, config: dict):
        self._mock = mock_sqs()
        self._config = config
        self._sqs_local_path = WORKING_DIR / "sqs"
        self._last_messages = {}

        # create local dir, if not exist
        self._sqs_local_path.mkdir(exist_ok=True)

    def init_data(self):
        """
            create sqs queue and upload message
        """
        self._client = boto3.client("sqs")
        self._url_map = {}
        for key, value in self._config.items():
            name = value.pop("QueueName", key)
            tags = {t["Key"]: t["Value"] for t in value.pop("Tags", [])}
            atrs = ["DelaySeconds", "MaximumMessageSize",
                    "MessageRetentionPeriod", "ReceiveMessageWaitTimeSeconds",
                    "RedrivePolicy"]
            attributes = {k: v for k, v in value.items() if k in atrs}
            url = self._client.create_queue(QueueName=name,
                                            Attributes=attributes,
                                            tags=tags)["QueueUrl"]
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

    def sync(self):
        """
            sync  (sqs message -> local dir)
        """
        for queue in self._config.keys():
            res = self._client.receive_message(QueueUrl=self._url_map[queue],
                                               VisibilityTimeout=0,
                                               MaxNumberOfMessages=10)
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
                with open(queue_path / (str(i).zfill(4)+".txt"), "w") as f:
                    f.write(body)
            self._last_messages[queue] = msgs


class S3Mock(AwsMock):
    service_name = "s3"

    def __init__(self, config: dict):
        self._mock = mock_s3()
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
        self._s3 = boto3.resource("s3")
        self._client = boto3.client("s3")

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

    def sync(self):
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

                if key not in self._hashes[bucket_name]\
                        or self._hashes[bucket_name][key] != hash:
                    # if s3 file is updated/created, update/create local file
                    target_path = str(bucket_path) + "/" + key
                    with open(target_path, "wb") as f:
                        f.write(data)
                    updated.append(key)
            if updated:
                res_updated[bucket_name] = updated

            # remove deleted file
            deleted = set(
                self._hashes[bucket_name].keys()) - set(new_hashes.keys())
            for key in deleted:
                target_path = str(bucket_path) + "/" + key
                if os.path.exists(target_path):
                    os.remove(target_path)
            self._hashes[bucket_name] = new_hashes
            if deleted:
                res_deleted[bucket_name] = list(deleted)
        return res_updated, res_deleted


class DynamoMock(AwsMock):
    service_name = "dynamodb"

    def __init__(self, config: dict):
        self._mock = mock_dynamodb()
        self._config = config
        self._local_dynamo_path = WORKING_DIR / "dynamodb"

        # create local dir, if not exist
        self._local_dynamo_path.mkdir(exist_ok=True)
        for table in self._config.keys():
            table_path = self._local_dynamo_path / table
            table_path.mkdir(exist_ok=True)

    def init_data(self):
        self._dynamodb = boto3.resource('dynamodb')
        for name, props in self._config.items():
            self._dynamodb.create_table(
                **props  # ok?
                # TableName=name,
                # KeySchema=props["KeySchema"],
                # AttributeDefinitions=props["AttributeDefinitions"],
                # ProvisionedThroughput=props["ProvisionedThroughput"]
            )
            table = self._dynamodb.Table(name)
            file = self._local_dynamo_path / name / "data.json"
            if file.exists():
                with open(file, "r") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    data = [data]
                try:
                    with table.batch_writer() as batch:
                        for row in data:
                            batch.put_item(Item=row)

                except ClientError as e:
                    logger.exception("dynamo init data error")
                    # TODO

    def sync(self):
        changed_table = []
        for name in self._config.keys():
            table = self._dynamodb.Table(name)
            items = table.scan().get("Items", [])
            file: Path = self._local_dynamo_path / name / "data.json"
            if len(items):
                local = []
                if file.exists():
                    with open(file, "r") as f:
                        local = json.load(f)

                if local != items:
                    print(items)
                    print(local)
                    changed_table.append(name)
                    with open(file, "w") as f:
                        json.dump(items, f, indent=4)
            else:
                if file.exists():
                    file.unlink()

        return changed_table


class MockManager():
    def __init__(self, config_file):
        config = ConfigParser(config_file)
        services = ["s3", "dynamodb", "sns", "sqs", "ses"]
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
        logger.info(
            f"start aws mock:{[m.service_name for m in self._services]}")

    def stop(self):
        for mock in self._services:
            mock.stop()
        logger.info(
            f"stop aws mock:{[m.service_name for m in self._services]}")

    def init_data(self):
        for mock in self._services:
            mock.init_data()

    def sync(self):
        for mock in self._services:
            self._changed[mock.service_name] = mock.sync()

    def get_change(self, service: str):
        return self._changed[service]
