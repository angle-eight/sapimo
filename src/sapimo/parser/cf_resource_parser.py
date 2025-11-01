from copy import deepcopy
from pathlib import Path

import yaml
from awscli.customizations.cloudformation.yamlhelper import yaml_parse

from sapimo.utils import LogManager
from sapimo.parser.fn_resolver import FnResolver

logger = LogManager.setup_logger(__name__)


class CfResourceParser(FnResolver):
    def __init__(self, filepath: Path, region: str = "us-east-1"):
        self._aws_region = region
        super().__init__(filepath, region)
        logger.info(
            f"Initializing CloudFormation parser for {len(self._resources)} resources"
        )
        for name, val in self._resources.items():
            self._classification(name, val)
        logger.info(
            f"Classified resources: S3={len(self._buckets)}, DynamoDB={len(self._tables)}, SQS={len(self._sqss)}, Others={len(self._others)}"
        )

    def _preprocess(self, filepath: Path, region: str):
        """preprocess: this method is overridden from super class"""
        super()._preprocess(filepath, region)

        # treat Fn and reflect global props
        self._buckets = {}  # key:bucket name
        self._tables = {}  # key:table name
        self._sqss = {}  # key:resource name
        self._snss = {}  # key:resource name
        self._sess = {}  # key:resource name
        # New AWS services support
        self._kinesis_streams = {}  # key:stream name
        self._kinesis_firehose = {}  # key:delivery stream name
        self._eventbridge_rules = {}  # key:rule name
        self._secrets = {}  # key:secret name
        self._parameters = {}  # key:parameter name
        self._cloudwatch_alarms = {}  # key:alarm name
        self._others = {}  # key:resource name

    def _classification(self, name: str, val: dict):
        """
        resource classification ->
          {buckets, tables, sqs.queue, sns.topic, ses.emailidentiry, etc.}
        Enhanced to support modern AWS services
        """
        props: dict = deepcopy(val.get("Properties", {}))
        resource_type = val["Type"]

        # Storage services
        if resource_type == "AWS::S3::Bucket":
            bucket_name = props.get("BucketName", name)
            self._buckets[bucket_name] = props

        # Database services
        elif resource_type in ["AWS::DynamoDB::GlobalTable", "AWS::DynamoDB::Table"]:
            table_name = props.get("TableName", name)
            self._tables[table_name] = props

        # Messaging and queuing services
        elif resource_type == "AWS::SQS::Queue":
            self._sqss[name] = props
        elif resource_type == "AWS::SNS::Topic":
            self._snss[name] = props

        # Email services
        elif resource_type == "AWS::SES::EmailIdentity":
            self._sess[name] = props

        # Streaming services
        elif resource_type == "AWS::Kinesis::Stream":
            stream_name = props.get("Name", name)
            self._kinesis_streams[stream_name] = props
        elif resource_type == "AWS::KinesisFirehose::DeliveryStream":
            delivery_stream_name = props.get("DeliveryStreamName", name)
            self._kinesis_firehose[delivery_stream_name] = props

        # Event and monitoring services
        elif resource_type == "AWS::Events::Rule":
            rule_name = props.get("Name", name)
            self._eventbridge_rules[rule_name] = props
        elif resource_type == "AWS::CloudWatch::Alarm":
            alarm_name = props.get("AlarmName", name)
            self._cloudwatch_alarms[alarm_name] = props

        # Security and secrets management
        elif resource_type == "AWS::SecretsManager::Secret":
            secret_name = props.get("Name", name)
            self._secrets[secret_name] = props
        elif resource_type == "AWS::SSM::Parameter":
            param_name = props.get("Name", name)
            self._parameters[param_name] = props

        else:
            self._others[name] = props

    def _get_config_dict(self) -> dict:
        """
        Create resource parts of config.yaml
        Enhanced to support modern AWS services
        """
        config = {}

        # Core supported services
        if self._buckets:
            config["s3"] = self._buckets
        if self._tables:
            config["dynamodb"] = self._tables
        if self._sqss:
            config["sqs"] = self._sqss

        # Additional services (ready for future implementation)
        if self._snss:
            config["sns"] = self._snss
        if self._sess:
            config["ses"] = self._sess
        if self._kinesis_streams:
            config["kinesis"] = self._kinesis_streams
        if self._kinesis_firehose:
            config["kinesisFirehose"] = self._kinesis_firehose
        if self._eventbridge_rules:
            config["eventbridge"] = self._eventbridge_rules
        if self._secrets:
            config["secretsManager"] = self._secrets
        if self._parameters:
            config["systemsManager"] = self._parameters
        if self._cloudwatch_alarms:
            config["cloudwatch"] = self._cloudwatch_alarms

        return config

    def create_config_file(self, output_path: Path, overwrite: bool = True):
        """create config.yaml file"""
        if not overwrite and output_path.exists():
            try:
                yaml_str = open(output_path).read()
                old_config = self._treat(yaml_parse(yaml_str))
                logger.info(f"old_config_dict:{old_config}")
            except Exception:
                logger.exception("old config yaml read error")
                old_config = {}
        else:
            old_config = {}

        config_dict = self._get_config_dict()
        old_config.update(config_dict)
        config_dict = old_config
        no_alias_dumper = yaml.dumper.Dumper
        no_alias_dumper.ignore_aliases = lambda self, data: True
        yml = yaml.dump(config_dict, Dumper=no_alias_dumper)
        with open(output_path, "w") as f:
            f.write(yml)

    def _get_ref_and_attr(self, name: str, resource: dict) -> dict:
        """Get Ref value and Attr value by resource type - Enhanced for modern AWS services"""
        tp = resource["Type"]
        props = resource.get("Properties", {})

        # Storage services
        if tp == "AWS::S3::Bucket":
            bucket_name = props.get("BucketName", name)
            return {
                "Ref": bucket_name,
                "Arn": f"arn:aws:s3:::{bucket_name}",
                "DomainName": f"{bucket_name}.s3.amazonaws.com",
            }

        # Database services
        elif tp in ["AWS::DynamoDB::GlobalTable", "AWS::DynamoDB::Table"]:
            table_name = props.get("TableName", name)
            return {
                "Ref": table_name,
                "Arn": self._arn_tmp.format("dynamodb:table", table_name),
                "StreamArn": self._arn_tmp.format(
                    "dynamodb:table", f"{table_name}/stream"
                ),
            }

        # Messaging services
        elif tp == "AWS::SQS::Queue":
            queue_name = props.get("QueueName", name)
            return {
                "Ref": f"https://sqs.{self._region}.amazonaws.com/{self._account_id}/{queue_name}",
                "Arn": self._arn_tmp.format("sqs", queue_name),
                "QueueName": queue_name,
            }

        elif tp == "AWS::SNS::Topic":
            topic_name = props.get("TopicName", name)
            return {
                "Ref": self._arn_tmp.format("sns", topic_name),
                "TopicName": topic_name,
            }

        # Streaming services
        elif tp == "AWS::Kinesis::Stream":
            stream_name = props.get("Name", name)
            return {
                "Ref": stream_name,
                "Arn": self._arn_tmp.format("kinesis:stream", stream_name),
            }

        elif tp == "AWS::KinesisFirehose::DeliveryStream":
            delivery_stream_name = props.get("DeliveryStreamName", name)
            return {
                "Ref": delivery_stream_name,
                "Arn": self._arn_tmp.format(
                    "firehose:deliverystream", delivery_stream_name
                ),
            }

        # Event services
        elif tp == "AWS::Events::Rule":
            rule_name = props.get("Name", name)
            return {
                "Ref": rule_name,
                "Arn": self._arn_tmp.format("events:rule", rule_name),
            }

        # Security services
        elif tp == "AWS::SecretsManager::Secret":
            secret_name = props.get("Name", name)
            return {
                "Ref": self._arn_tmp.format("secretsmanager:secret", secret_name),
                "SecretName": secret_name,
            }

        elif tp == "AWS::SSM::Parameter":
            param_name = props.get("Name", name)
            return {"Ref": param_name, "Value": props.get("Value", "")}

        # Monitoring services
        elif tp == "AWS::CloudWatch::Alarm":
            alarm_name = props.get("AlarmName", name)
            return {
                "Ref": alarm_name,
                "Arn": self._arn_tmp.format("cloudwatch:alarm", alarm_name),
            }

        # Email services
        elif tp == "AWS::SES::EmailIdentity":
            email_identity = props.get("EmailIdentity", name)
            return {"Ref": email_identity}

        # Default case
        else:
            return {"Ref": name, "Arn": self._arn_tmp.format("other", name)}

    @property
    def _account_id(self) -> str:
        """Get AWS account ID"""
        return "123456789012"  # Default dummy account ID

    @property
    def _region(self) -> str:
        """Get AWS region"""
        return getattr(self, "_aws_region", "us-east-1")
