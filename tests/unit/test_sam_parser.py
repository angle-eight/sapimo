from pathlib import Path

import yaml

from sapimo.constants import AuthType, EventType
from sapimo.parser.sam_parser import SamParser


def write_template(tmp_path: Path, text: str) -> Path:
    template = tmp_path / "template.yaml"
    template.write_text(text, encoding="utf-8")
    return template


def test_sam_parser_accepts_zip_function_without_package_type(tmp_path: Path):
    template = write_template(
        tmp_path,
        """
Resources:
  HelloFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambda/hello/
      Handler: app.lambda_handler
      Runtime: python3.12
      Events:
        GetHello:
          Type: Api
          Properties:
            Path: /hello
            Method: get
""",
    )

    config = SamParser(template)._get_config_dict()

    route = config["paths"]["/hello"]["get"]["Properties"]
    assert route["CodeUri"] == "lambda/hello/"
    assert route["Handler"] == "app.lambda_handler"
    assert route["Runtime"] == "python3.12"
    assert route["EventType"] == EventType.APIGW.name
    assert route["AuthType"] == AuthType.NONE.name


def test_sam_parser_applies_globals_and_api_iam_authorizer(tmp_path: Path):
    template = write_template(
        tmp_path,
        """
Globals:
  Function:
    Runtime: python3.12
    Timeout: 10
Resources:
  PrivateApi:
    Type: AWS::Serverless::Api
    Properties:
      StageName: Prod
      Auth:
        DefaultAuthorizer: AWS_IAM
  HelloFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambda/hello/
      Handler: app.lambda_handler
      Events:
        GetHello:
          Type: Api
          Properties:
            Path: /hello
            Method: post
            RestApiId: !Ref PrivateApi
""",
    )

    config = SamParser(template)._get_config_dict()

    route = config["paths"]["/hello"]["post"]["Properties"]
    assert route["Runtime"] == "python3.12"
    assert route["Timeout"] == 10
    assert route["AuthType"] == AuthType.AWS_IAM.name


def test_sam_parser_extracts_s3_object_created_trigger(tmp_path: Path):
    template = write_template(
        tmp_path,
        """
Resources:
  UploadBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: uploads
  ProcessorFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambda/processor/
      Handler: app.lambda_handler
      Runtime: python3.12
      Events:
        UploadCreated:
          Type: S3
          Properties:
            Bucket: uploads
            Events: s3:ObjectCreated:*
            Filter:
              S3Key:
                Rules:
                  - Name: suffix
                    Value: .json
""",
    )

    config = SamParser(template)._get_config_dict()

    assert "uploads" in config["s3"]
    trigger = config["triggered"]["uploads"]["Properties"]
    assert trigger["CodeUri"] == "lambda/processor/"
    assert trigger["Filter"]["S3Key"]["Rules"][0]["Value"] == ".json"


def test_sam_parser_writes_config_file(tmp_path: Path):
    template = write_template(
        tmp_path,
        """
Resources:
  HelloFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: lambda/hello/
      Handler: app.lambda_handler
      Runtime: python3.12
      Events:
        GetHello:
          Type: HttpApi
          Properties:
            Path: /hello
            Method: get
            Auth:
              Authorizer: NONE
""",
    )
    output = tmp_path / "config.yaml"

    SamParser(template).create_config_file(output)

    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    route = config["paths"]["/hello"]["get"]["Properties"]
    assert route["EventType"] == EventType.APIGW_V2.name
    assert route["AuthType"] == AuthType.NONE.name
