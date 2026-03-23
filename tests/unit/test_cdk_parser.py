"""
Test cases for CDK CloudFormation parser
"""

import json
from pathlib import Path
from unittest.mock import patch, mock_open

try:
    import pytest
except ImportError:
    pytest = None

from sapimo.parser.cdk_parser import CdkCfParser
from sapimo.constants import EventType, AuthType


@pytest.fixture
def sample_cdk_template() -> dict:
    """Sample CDK CloudFormation template"""
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": {
            "TestLambda": {
                "Type": "AWS::Lambda::Function",
                "Properties": {
                    "FunctionName": "TestFunction",
                    "Runtime": "python3.9",
                    "Handler": "app.lambda_handler",
                    "Code": {
                        "ZipFile": "def lambda_handler(event, context): return {'statusCode': 200}"
                    },
                    "Environment": {"Variables": {"ENV_VAR": "test_value"}},
                },
                "Metadata": {"aws:asset:path": "lambda/test"},
            },
            "TestApi": {
                "Type": "AWS::ApiGatewayV2::Api",
                "Properties": {"Name": "TestApi", "ProtocolType": "HTTP"},
            },
            "TestRoute": {
                "Type": "AWS::ApiGatewayV2::Route",
                "Properties": {
                    "ApiId": {"Ref": "TestApi"},
                    "RouteKey": "GET /test",
                    "Target": {
                        "Fn::Join": ["", ["integrations/", {"Ref": "TestIntegration"}]]
                    },
                },
            },
            "TestIntegration": {
                "Type": "AWS::ApiGatewayV2::Integration",
                "Properties": {
                    "ApiId": {"Ref": "TestApi"},
                    "IntegrationType": "AWS_PROXY",
                    "IntegrationUri": {"Fn::GetAtt": ["TestLambda", "Arn"]},
                },
            },
        },
    }


@pytest.fixture
def temp_cdk_directory(tmp_path):
    """Create a temporary CDK directory structure"""
    cdk_out = tmp_path / "cdk.out"
    cdk_out.mkdir()

    # Create lambda source directory
    lambda_dir = tmp_path / "lambda" / "test"
    lambda_dir.mkdir(parents=True)

    # Create sample lambda file
    lambda_file = lambda_dir / "app.py"
    lambda_file.write_text("""
def lambda_handler(event, context):
    return {'statusCode': 200, 'body': 'Hello World'}
""")

    return tmp_path


def test_cdk_parser_initialization(temp_cdk_directory, sample_cdk_template):
    """Test CDK parser initialization"""
    template_file = temp_cdk_directory / "cdk.out" / "template.json"
    template_file.write_text(json.dumps(sample_cdk_template))

    parser = CdkCfParser(template_file)

    assert parser._cdk_path == template_file.parent
    assert parser._repo_path == temp_cdk_directory
    assert len(parser._md5s) > 0  # Should have calculated hashes


def test_apigateway_v2_route_parsing(temp_cdk_directory, sample_cdk_template):
    """Test parsing of API Gateway v2 routes"""
    template_file = temp_cdk_directory / "cdk.out" / "template.json"
    template_file.write_text(json.dumps(sample_cdk_template))

    parser = CdkCfParser(template_file)
    config = parser._get_config_dict()

    assert "paths" in config
    assert "/test" in config["paths"]
    assert "get" in config["paths"]["/test"]

    api_config = config["paths"]["/test"]["get"]["Properties"]
    assert api_config["EventType"] == EventType.APIGW_V2.name
    assert api_config["AuthType"] == AuthType.NONE.name


def test_lambda_container_image_support():
    """Test support for Lambda container images"""
    container_template = {
        "Resources": {
            "ContainerLambda": {
                "Type": "AWS::Lambda::Function",
                "Properties": {
                    "PackageType": "Image",
                    "Code": {
                        "ImageUri": "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-func:latest"
                    },
                },
                "Metadata": {"aws:asset:path": "lambda/container"},
            }
        }
    }

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "sapimo.parser.fn_resolver.open",
            mock_open(read_data=json.dumps(container_template)),
        ),
        patch(
            "sapimo.parser.fn_resolver.yaml_parse",
            return_value=container_template,
        ),
    ):
        parser = CdkCfParser(Path("test.json"))
        api_props = parser._api_props_from_lambda(
            container_template["Resources"]["ContainerLambda"], "", True
        )

        assert api_props["PackageType"] == "Image"
        # For container images, CodeUri should contain the image URI
        expected_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-func:latest"
        assert api_props["CodeUri"] == expected_uri


def test_enhanced_error_handling():
    """Test improved error handling"""
    invalid_template = {
        "Resources": {
            "InvalidMethod": {
                "Type": "AWS::ApiGateway::Method",
                "Properties": {
                    "HttpMethod": "GET",
                    "ResourceId": "NonexistentResource",
                },
            }
        }
    }

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "sapimo.parser.fn_resolver.open",
            mock_open(read_data=json.dumps(invalid_template)),
        ),
        patch(
            "sapimo.parser.fn_resolver.yaml_parse",
            return_value=invalid_template,
        ),
    ):
        parser = CdkCfParser(Path("test.json"))
        # Should not raise exception, but log warning
        assert len(parser._apis) == 0


def test_cdk_metadata_extraction(temp_cdk_directory):
    """Test extraction of CDK-specific metadata"""
    template_with_metadata = {
        "Metadata": {"aws:cdk:path": "TestStack/TestLambda"},
        "Resources": {
            "TestResource": {
                "Type": "AWS::S3::Bucket",
                "Metadata": {"aws:asset:path": "assets/bucket"},
            }
        },
    }

    template_file = temp_cdk_directory / "cdk.out" / "template.json"
    template_file.write_text(json.dumps(template_with_metadata))

    parser = CdkCfParser(template_file)
    metadata = parser._extract_cdk_metadata()

    assert "TestResource" in metadata
    assert metadata["TestResource"]["aws:asset:path"] == "assets/bucket"


def test_search_code_uri_edge_cases(temp_cdk_directory):
    """Test edge cases in code URI search"""
    template_file = temp_cdk_directory / "cdk.out" / "template.json"
    template_file.write_text('{"Resources": {}}')

    parser = CdkCfParser(template_file)

    # Test empty code URI
    result = parser._search_code_uri("", "")
    assert result == ""

    # Test nonexistent directory
    result = parser._search_code_uri("nonexistent", "")
    assert "nonexistent" in result

    # Test directory without Python files
    empty_dir = temp_cdk_directory / "empty"
    empty_dir.mkdir()
    result = parser._search_code_uri("../empty", "")
    assert "empty" in result


def test_validate_cdk_structure():
    """Test CDK structure validation"""
    # Create a mock parser instance for testing validation logic
    parser = CdkCfParser.__new__(CdkCfParser)

    # Test CDK template recognition
    cdk_template = {
        "Resources": {
            "TestResource": {
                "Type": "AWS::CloudWatch::Alarm",
                "Properties": {"AlarmName": "TestAlarm"},
                "Metadata": {"aws:cdk:path": "TestStack/TestAlarm"},
            }
        }
    }

    parser._whole = cdk_template
    parser._resources = {}
    parser._others = {}

    result = parser._validate_cdk_structure()
    assert result is True

    # Test standard CloudFormation template recognition
    standard_cf_template = {
        "Resources": {
            "TestResource": {
                "Type": "AWS::CloudWatch::Alarm",
                "Properties": {"AlarmName": "test-alarm"},
            }
        }
    }

    parser._whole = standard_cf_template
    result = parser._validate_cdk_structure()
    assert result is False


def test_split_space_method():
    """Test ImageInfo._split_space method for parsing Docker commands"""
    from sapimo.parser.image_info import ImageInfo

    src = 'ENV MY_NAME="Nori Asa" MY_DOG=Rex\\ The\\ Dog'
    expected = ["ENV", 'MY_NAME="Nori Asa"', "MY_DOG=Rex The Dog"]
    result = ImageInfo._split_space(src)
    assert result == expected
