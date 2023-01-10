
from copy import deepcopy
from pathlib import Path

import yaml
from awscli.customizations.cloudformation.yamlhelper import yaml_parse

from sapimo.utils import setup_logger, add_element
from sapimo.parser.fn_resolver import FnResolver

logger = setup_logger(__file__)


class CfResourceParser(FnResolver):
    def __init__(self, filepath: Path, region="us-east-1"):
        super().__init__(filepath, region)
        for name, val in self._resources.items():
            self._classification(name, val)

    def _preprocess(self, filepath: Path, region: str):
        """ preprocess: this method is overridden from super class """
        super()._preprocess(filepath, region)

        # treat Fn and reflect global props
        self._buckets = {}  # key:bucket name
        self._tables = {}  # key:table name
        self._sqss = {}  # key:resource name
        self._snss = {}  # key:resource name
        self._sess = {}  # key:resource name
        self._others = {}  # key:resource name

    def _classification(self, name, val):
        """
            resource classification ->
              {buckets, tables, sqs.queue, sns.topic, ses.emailidentiry}
            this method is overridden from super class
        """
        props: dict = deepcopy(val.get("Properties", {}))
        if val["Type"] == "AWS::S3::Bucket":
            bucket_name = props.get("BucketName", name)
            self._buckets[bucket_name] = props
        elif val["Type"] == "AWS::DynamoDB::GlobalTable"\
                or val["Type"] == "AWS::DynamoDB::Table":
            table_name = props.get("TableName", name)
            self._tables[table_name] = props
        elif val["Type"] == "AWS::SQS::Queue":
            self._sqss[name] = props
        elif val["Type"] == "AWS::SNS::Topic":
            self._snss[name] = props
        elif val["Type"] == "AWS::SES::EmailIdentity":
            self._sess[name] = props
        else:
            self._others[name] = props

    def _get_config_dict(self) -> dict:
        """
            create resource parts of config.yaml
            this method is overridden from super class
        """
        config = {}
        if self._buckets:
            config["s3"] = self._buckets
        if self._tables:
            config["dynamodb"] = self._tables
        if self._sqss:
            config["sqs"] = self._sqss

        # sns mock, ses mock and event trigger are not implemented yet
        # if self._snss:
        #     config["sns"] = self._snss
        # if self._sess:
        #     config["ses"] = self._sess
        return config

    def create_config_file(self, output_path: Path, overwrite: bool = True):
        """ create config.yaml file"""
        if not overwrite and output_path.exists():
            try:
                yaml_str = open(output_path).read()
                old_config = self._treat(yaml_parse(yaml_str))
                logger.info(f'old_config_dict:{old_config}')
            except Exception as e:
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
        with open(output_path, "w")as f:
            f.write(yml)

    def _get_ref_and_attr(self, name: str, resource: dict):
        """ get Ref value and Attr value by resource type """
        tp = resource["Type"]
        props = resource["Properties"]
        if tp == "AWS::S3::Bucket":
            return {"Ref": props.get("BucketName", name)}
        elif tp == "AWS::DynamoDB::GlobalTable"\
                or tp == "AWS::DynamoDB::Table":
            return {"Ref": props.get("TableName", name),
                    "Arn": self._arn_tmp.format("dynamo", name)}
        # elif tp == "AWS::SQS::Queue":
        #     pass
        # elif tp == "AWS::SNS::Topic":
        #     pass
        # elif tp == "AWS::SES::EmailIdentity":
        #     pass
        else:
            return {"Ref": name, "Arn": self._arn_tmp.format("other", name)}
