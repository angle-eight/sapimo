"""
Test cases for CloudFormation resource parser
"""

import json
from pathlib import Path
from unittest.mock import patch, mock_open

from sapimo.parser.cf_resource_parser import CfResourceParser


def test_cf_parser_initialization():
    """Test CloudFormation parser initialization"""
    template = {
        "Resources": {
            "TestBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": "test-bucket"},
            }
        }
    }

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "sapimo.parser.fn_resolver.open", mock_open(read_data=json.dumps(template))
        ),
        patch(
            "sapimo.parser.fn_resolver.yaml_parse",
            return_value=template,
        ),
    ):
        parser = CfResourceParser(Path("template.json"))

        assert len(parser._buckets) == 1
        assert "test-bucket" in parser._buckets
        assert parser._buckets["test-bucket"]["BucketName"] == "test-bucket"


def test_s3_bucket_classification():
    """Test S3 bucket resource classification"""
    # Create a mock parser to test classification directly
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None  # Skip preprocess
    parser._buckets = {}
    parser._tables = {}
    parser._sqss = {}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    # Test named bucket
    bucket_resource = {
        "Type": "AWS::S3::Bucket",
        "Properties": {
            "BucketName": "my-test-bucket",
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    }
    parser._classification("MyBucket", bucket_resource)

    assert "my-test-bucket" in parser._buckets
    assert parser._buckets["my-test-bucket"]["BucketName"] == "my-test-bucket"

    # Test auto-named bucket (uses resource name)
    auto_named_bucket = {
        "Type": "AWS::S3::Bucket",
        "Properties": {"VersioningConfiguration": {"Status": "Enabled"}},
    }
    parser._classification("AutoNamedBucket", auto_named_bucket)

    assert "AutoNamedBucket" in parser._buckets
    assert "VersioningConfiguration" in parser._buckets["AutoNamedBucket"]


def test_dynamodb_table_classification():
    """Test DynamoDB table resource classification"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {}
    parser._tables = {}
    parser._sqss = {}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    # Test regular DynamoDB table
    table_resource = {
        "Type": "AWS::DynamoDB::Table",
        "Properties": {
            "TableName": "Users",
            "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    }
    parser._classification("UsersTable", table_resource)

    # Test Global table
    global_table_resource = {
        "Type": "AWS::DynamoDB::GlobalTable",
        "Properties": {
            "TableName": "GlobalUsers",
            "Replicas": [{"Region": "us-east-1"}, {"Region": "us-west-2"}],
        },
    }
    parser._classification("GlobalTable", global_table_resource)

    assert "Users" in parser._tables
    assert "GlobalUsers" in parser._tables
    assert parser._tables["Users"]["TableName"] == "Users"
    assert parser._tables["GlobalUsers"]["TableName"] == "GlobalUsers"


def test_sqs_classification():
    """Test SQS resource classification"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {}
    parser._tables = {}
    parser._sqss = {}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    sqs_resource = {
        "Type": "AWS::SQS::Queue",
        "Properties": {"QueueName": "my-test-queue", "VisibilityTimeoutSeconds": 30},
    }

    parser._classification("MyQueue", sqs_resource)

    assert "MyQueue" in parser._sqss
    assert parser._sqss["MyQueue"]["QueueName"] == "my-test-queue"
    assert parser._sqss["MyQueue"]["VisibilityTimeoutSeconds"] == 30


def test_modern_aws_services():
    """Test modern AWS services support"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {}
    parser._tables = {}
    parser._sqss = {}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    # Test each modern service
    services = [
        (
            "DataStream",
            {
                "Type": "AWS::Kinesis::Stream",
                "Properties": {"Name": "data-stream", "ShardCount": 1},
            },
        ),
        (
            "DeliveryStream",
            {
                "Type": "AWS::KinesisFirehose::DeliveryStream",
                "Properties": {"DeliveryStreamName": "delivery-stream"},
            },
        ),
        (
            "EventRule",
            {
                "Type": "AWS::Events::Rule",
                "Properties": {
                    "Name": "daily-trigger",
                    "ScheduleExpression": "rate(1 day)",
                },
            },
        ),
        (
            "Secret",
            {
                "Type": "AWS::SecretsManager::Secret",
                "Properties": {"Name": "api-key", "Description": "API key"},
            },
        ),
        (
            "Parameter",
            {
                "Type": "AWS::SSM::Parameter",
                "Properties": {"Name": "/app/config/database-url", "Type": "String"},
            },
        ),
        (
            "Alarm",
            {
                "Type": "AWS::CloudWatch::Alarm",
                "Properties": {
                    "AlarmName": "high-cpu-alarm",
                    "ComparisonOperator": "GreaterThanThreshold",
                },
            },
        ),
    ]

    for resource_name, resource_def in services:
        parser._classification(resource_name, resource_def)

    assert "data-stream" in parser._kinesis_streams
    assert "delivery-stream" in parser._kinesis_firehose
    assert "daily-trigger" in parser._eventbridge_rules
    assert "api-key" in parser._secrets
    assert "/app/config/database-url" in parser._parameters
    assert "high-cpu-alarm" in parser._cloudwatch_alarms


def test_get_config_dict():
    """Test configuration dictionary generation"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {"test-bucket": {"BucketName": "test-bucket"}}
    parser._tables = {"test-table": {"TableName": "test-table"}}
    parser._sqss = {"TestQueue": {"QueueName": "test-queue"}}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    config = parser._get_config_dict()

    assert "s3" in config
    assert "dynamodb" in config
    assert "sqs" in config

    assert "test-bucket" in config["s3"]
    assert "test-table" in config["dynamodb"]
    assert "TestQueue" in config["sqs"]


def test_get_ref_and_attr():
    """Test reference and attribute generation"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {"MyBucket": {"BucketName": "my-test-bucket"}}

    s3_resource = {
        "Type": "AWS::S3::Bucket",
        "Properties": {"BucketName": "my-test-bucket"},
    }

    result = parser._get_ref_and_attr("MyBucket", s3_resource)

    assert result["Ref"] == "my-test-bucket"  # BucketName property
    assert result["Arn"] == "arn:aws:s3:::my-test-bucket"
    assert result["DomainName"] == "my-test-bucket.s3.amazonaws.com"


def test_get_ref_and_attr_dynamodb():
    """Test reference and attribute generation for DynamoDB"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._arn_tmp = "arn:aws:lambda:us-east-1:123456789012:{0}:{1}"
    parser._aws_region = "us-east-1"
    parser._tables = {"TestTable": {"TableName": "my-table"}}

    dynamodb_resource = {
        "Type": "AWS::DynamoDB::Table",
        "Properties": {"TableName": "my-table"},
    }

    result = parser._get_ref_and_attr("TestTable", dynamodb_resource)

    assert result["Ref"] == "my-table"
    assert (
        "arn:aws:lambda:us-east-1:123456789012:dynamodb:table:my-table" in result["Arn"]
    )


def test_get_ref_and_attr_sqs():
    """Test reference and attribute generation for SQS"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._arn_tmp = "arn:aws:lambda:us-east-1:123456789012:{0}:{1}"
    parser._aws_region = "us-east-1"
    parser._sqss = {"TestQueue": {"QueueName": "my-queue"}}

    sqs_resource = {"Type": "AWS::SQS::Queue", "Properties": {"QueueName": "my-queue"}}

    result = parser._get_ref_and_attr("TestQueue", sqs_resource)

    assert "my-queue" in result["Ref"]
    assert result["QueueName"] == "my-queue"


def test_unknown_resource_type():
    """Test handling of unknown resource types"""
    parser = CfResourceParser.__new__(CfResourceParser)
    parser._preprocess = lambda *args: None
    parser._buckets = {}
    parser._tables = {}
    parser._sqss = {}
    parser._snss = {}
    parser._sess = {}
    parser._kinesis_streams = {}
    parser._kinesis_firehose = {}
    parser._eventbridge_rules = {}
    parser._secrets = {}
    parser._parameters = {}
    parser._cloudwatch_alarms = {}
    parser._others = {}

    unknown_resource = {
        "Type": "AWS::Unknown::Resource",
        "Properties": {"SomeProperty": "some-value"},
    }

    parser._classification("UnknownResource", unknown_resource)

    # Should be classified as "others"
    assert "UnknownResource" in parser._others
    assert parser._others["UnknownResource"]["SomeProperty"] == "some-value"


def test_config_file_creation(tmp_path):
    """Test configuration file creation"""
    template = {
        "Resources": {
            "TestBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": "config-test-bucket"},
            }
        }
    }

    config_file = tmp_path / "config.yaml"

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "sapimo.parser.fn_resolver.open", mock_open(read_data=json.dumps(template))
        ),
        patch(
            "sapimo.parser.fn_resolver.yaml_parse",
            return_value=template,
        ),
    ):
        parser = CfResourceParser(Path("template.json"))
        parser.create_config_file(config_file)

        assert config_file.exists()

        # Verify content
        content = config_file.read_text()
        assert "s3:" in content
        assert "config-test-bucket:" in content


def test_error_handling_in_config_creation(tmp_path):
    """Test error handling in configuration file creation"""
    template = {
        "Resources": {
            "TestBucket": {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": "error-test-bucket"},
            }
        }
    }

    config_file = tmp_path / "config.yaml"

    # Create existing config with invalid YAML
    config_file.write_text("invalid: yaml: content: [")

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "sapimo.parser.fn_resolver.open", mock_open(read_data=json.dumps(template))
        ),
        patch(
            "sapimo.parser.fn_resolver.yaml_parse",
            return_value=template,
        ),
    ):
        parser = CfResourceParser(Path("template.json"))

        # Should not raise exception, but create new config
        parser.create_config_file(config_file, overwrite=False)

        assert config_file.exists()
        content = config_file.read_text()
        assert "error-test-bucket" in content


def test_yaml_parse_supports_cloudformation_short_tags():
    """Test local yaml parser for CloudFormation short tags"""
    from sapimo.parser.yaml_loader import yaml_parse

    parsed = yaml_parse(
        """
Resources:
  SampleFunction:
    Type: AWS::Lambda::Function
Outputs:
  ApiUrl:
    Value: !Sub "https://${SampleApi}.execute-api.${AWS::Region}.amazonaws.com/"
  FunctionArn:
    Value: !GetAtt SampleFunction.Arn
"""
    )

    assert parsed["Outputs"]["ApiUrl"]["Value"] == {
        "Fn::Sub": "https://${SampleApi}.execute-api.${AWS::Region}.amazonaws.com/"
    }
    assert parsed["Outputs"]["FunctionArn"]["Value"] == {
        "Fn::GetAtt": ["SampleFunction", "Arn"]
    }
