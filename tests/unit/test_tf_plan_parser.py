"""Unit tests for TerraformPlanParser.

Tests cover:
- Basic resource classification (S3, DynamoDB, SQS, SNS, SES)
- Lambda CodeUri resolution via data.archive_file
- HTTP API V2 route → integration → Lambda chain
- REST API V1 resource path building and integration → Lambda chain
- Cognito user pool and client linkage
- Child-module resource collection
- Error handling (invalid JSON, non-plan file)
- config.yaml file generation and merging
"""

import json
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest
import yaml

from sapimo.parser.tf_plan_parser import TerraformPlanParser
from sapimo.exceptions import TerraformPlanParseError


# ── fixture helpers ──────────────────────────────────────────────────────────


def _make_plan(
    resources: list[dict] | None = None,
    child_modules: list[dict] | None = None,
    config_resources: list[dict] | None = None,
    module_calls: dict | None = None,
) -> dict:
    """Return a minimal terraform plan JSON structure."""
    return {
        "format_version": "1.2",
        "terraform_version": "1.7.0",
        "planned_values": {
            "root_module": {
                "resources": resources or [],
                "child_modules": child_modules or [],
            }
        },
        "configuration": {
            "root_module": {
                "resources": config_resources or [],
                "module_calls": module_calls or {},
            }
        },
    }


def _managed(address: str, rtype: str, values: dict, mode: str = "managed") -> dict:
    parts = address.split(".")
    return {
        "address": address,
        "mode": mode,
        "type": rtype,
        "name": parts[-1],
        "values": values,
    }


def _data(address: str, rtype: str, values: dict) -> dict:
    return _managed(address, rtype, values, mode="data")


def _cfg(address: str, expressions: dict) -> dict:
    return {"address": address, "expressions": expressions}


def _ref(address: str) -> dict:
    """Create a configuration expression that references another resource."""
    parts = address.split(".")
    attr_ref = f"{address}.id"
    return {"references": [attr_ref, address]}


def _make_parser(plan: dict) -> TerraformPlanParser:
    """Instantiate TerraformPlanParser from a dict (bypasses file I/O)."""
    parser = TerraformPlanParser.__new__(TerraformPlanParser)
    with patch("builtins.open", mock_open(read_data=json.dumps(plan))):
        with patch("pathlib.Path.exists", return_value=True):
            return TerraformPlanParser(Path("plan.json"))


# ── error handling ───────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_raises_on_invalid_json(self, tmp_path: Path):
        bad_file = tmp_path / "plan.json"
        bad_file.write_text("not json", encoding="utf-8")
        with pytest.raises(TerraformPlanParseError, match="Failed to parse"):
            TerraformPlanParser(bad_file)

    def test_raises_on_non_plan_json(self, tmp_path: Path):
        other_json = tmp_path / "plan.json"
        other_json.write_text(json.dumps({"some": "data"}), encoding="utf-8")
        with pytest.raises(TerraformPlanParseError, match="does not appear to be"):
            TerraformPlanParser(other_json)


# ── basic resource classification ─────────────────────────────────────────────


class TestBasicResources:
    def test_s3_bucket_classified(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_s3_bucket.my_bucket", "aws_s3_bucket", {"bucket": "my-bucket"}
                )
            ]
        )
        p = _make_parser(plan)
        assert "my-bucket" in p._buckets
        assert p._buckets["my-bucket"]["BucketName"] == "my-bucket"

    def test_s3_bucket_name_falls_back_to_resource_name(self):
        plan = _make_plan(
            resources=[_managed("aws_s3_bucket.my_bucket", "aws_s3_bucket", {})]
        )
        p = _make_parser(plan)
        assert "my_bucket" in p._buckets

    def test_dynamodb_provisioned_billing(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_dynamodb_table.users",
                    "aws_dynamodb_table",
                    {
                        "name": "users-table",
                        "billing_mode": "PROVISIONED",
                        "hash_key": "userId",
                        "range_key": None,
                        "read_capacity": 5,
                        "write_capacity": 10,
                        "attribute": [{"name": "userId", "type": "S"}],
                    },
                )
            ]
        )
        p = _make_parser(plan)
        t = p._tables["users-table"]
        assert t["BillingMode"] == "PROVISIONED"
        assert t["ProvisionedThroughput"]["ReadCapacityUnits"] == 5
        assert t["ProvisionedThroughput"]["WriteCapacityUnits"] == 10
        assert t["KeySchema"] == [{"AttributeName": "userId", "KeyType": "HASH"}]

    def test_dynamodb_pay_per_request_no_throughput(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_dynamodb_table.orders",
                    "aws_dynamodb_table",
                    {
                        "name": "orders-table",
                        "billing_mode": "PAY_PER_REQUEST",
                        "hash_key": "orderId",
                        "range_key": "createdAt",
                        "attribute": [
                            {"name": "orderId", "type": "S"},
                            {"name": "createdAt", "type": "N"},
                        ],
                    },
                )
            ]
        )
        p = _make_parser(plan)
        t = p._tables["orders-table"]
        assert t["BillingMode"] == "PAY_PER_REQUEST"
        assert "ProvisionedThroughput" not in t
        assert len(t["KeySchema"]) == 2

    def test_sqs_queue(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_sqs_queue.jobs",
                    "aws_sqs_queue",
                    {
                        "name": "jobs-queue",
                        "delay_seconds": 5,
                        "visibility_timeout_seconds": 30,
                    },
                )
            ]
        )
        p = _make_parser(plan)
        q = p._sqss["jobs-queue"]
        assert q["QueueName"] == "jobs-queue"
        assert q["DelaySeconds"] == 5
        assert q["VisibilityTimeout"] == 30

    def test_sns_topic(self):
        plan = _make_plan(
            resources=[
                _managed("aws_sns_topic.alerts", "aws_sns_topic", {"name": "alerts"})
            ]
        )
        p = _make_parser(plan)
        assert "alerts" in p._snss
        assert p._snss["alerts"]["TopicName"] == "alerts"

    def test_ses_email_identity(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_ses_email_identity.notify",
                    "aws_ses_email_identity",
                    {"email": "notify@example.com"},
                )
            ]
        )
        p = _make_parser(plan)
        assert "notify@example.com" in p._sess
        assert p._sess["notify@example.com"]["EmailIdentity"] == "notify@example.com"

    def test_data_sources_are_skipped(self):
        plan = _make_plan(
            resources=[
                _data(
                    "data.archive_file.fn",
                    "archive_file",
                    {
                        "output_path": "./build/fn.zip",
                        "source_dir": "./src/fn",
                    },
                )
            ]
        )
        p = _make_parser(plan)
        assert p._buckets == {}
        assert p._tables == {}

    def test_unknown_resource_type_ignored(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_iam_role.lambda_role", "aws_iam_role", {"name": "my-role"}
                )
            ]
        )
        p = _make_parser(plan)
        assert p._buckets == {}
        assert p._tables == {}


# ── Lambda CodeUri resolution ─────────────────────────────────────────────────


class TestCodeUriResolution:
    def test_resolves_code_uri_from_archive_file(self):
        plan = _make_plan(
            resources=[
                _data(
                    "data.archive_file.hello",
                    "archive_file",
                    {
                        "output_path": "./build/hello.zip",
                        "source_dir": "./src/hello",
                    },
                ),
                _managed(
                    "aws_lambda_function.hello",
                    "aws_lambda_function",
                    {
                        "function_name": "hello-fn",
                        "handler": "app.lambda_handler",
                        "runtime": "python3.12",
                        "filename": "./build/hello.zip",
                        "timeout": 30,
                        "environment": [{"variables": {"TABLE": "my-table"}}],
                    },
                ),
            ]
        )
        p = _make_parser(plan)
        props = p._build_lambda_props("aws_lambda_function.hello")
        assert props["CodeUri"] == "./src/hello/"
        assert props["Handler"] == "app.lambda_handler"
        assert props["Runtime"] == "python3.12"
        assert props["Timeout"] == 30
        assert props["Environment"]["Variables"]["TABLE"] == "my-table"

    def test_empty_code_uri_when_no_archive_file_match(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_lambda_function.fn",
                    "aws_lambda_function",
                    {
                        "handler": "app.handler",
                        "runtime": "python3.12",
                        "filename": "./build/fn.zip",
                    },
                ),
            ]
        )
        p = _make_parser(plan)
        props = p._build_lambda_props("aws_lambda_function.fn")
        assert props["CodeUri"] == ""

    def test_empty_code_uri_when_s3_deployment(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_lambda_function.fn",
                    "aws_lambda_function",
                    {
                        "handler": "app.handler",
                        "runtime": "python3.12",
                        "s3_bucket": "my-deploy-bucket",
                        "s3_key": "functions/fn.zip",
                    },
                ),
            ]
        )
        p = _make_parser(plan)
        props = p._build_lambda_props("aws_lambda_function.fn")
        assert props["CodeUri"] == ""


# ── HTTP API V2 ───────────────────────────────────────────────────────────────


class TestHttpApiV2:
    def _plan_with_http_api(
        self,
        route_key: str = "GET /hello/{id}",
        auth_type: str = "NONE",
    ) -> dict:
        return _make_plan(
            resources=[
                _data(
                    "data.archive_file.hello",
                    "archive_file",
                    {
                        "output_path": "./build/hello.zip",
                        "source_dir": "./src/hello",
                    },
                ),
                _managed(
                    "aws_lambda_function.hello",
                    "aws_lambda_function",
                    {
                        "handler": "app.lambda_handler",
                        "runtime": "python3.12",
                        "filename": "./build/hello.zip",
                    },
                ),
                _managed(
                    "aws_apigatewayv2_integration.hello",
                    "aws_apigatewayv2_integration",
                    {
                        "integration_type": "AWS_PROXY",
                        "integration_uri": None,  # computed
                        "payload_format_version": "2.0",
                    },
                ),
                _managed(
                    "aws_apigatewayv2_route.hello",
                    "aws_apigatewayv2_route",
                    {
                        "route_key": route_key,
                        "authorization_type": auth_type,
                        "target": None,  # computed
                    },
                ),
            ],
            config_resources=[
                _cfg(
                    "aws_apigatewayv2_integration.hello",
                    {
                        "integration_uri": {
                            "references": [
                                "aws_lambda_function.hello.invoke_arn",
                                "aws_lambda_function.hello",
                            ]
                        }
                    },
                ),
                _cfg(
                    "aws_apigatewayv2_route.hello",
                    {
                        "target": {
                            "references": [
                                "aws_apigatewayv2_integration.hello.id",
                                "aws_apigatewayv2_integration.hello",
                            ]
                        }
                    },
                ),
            ],
        )

    def test_route_produces_correct_path_and_method(self):
        p = _make_parser(self._plan_with_http_api("GET /hello/{id}"))
        assert "/hello/{id}" in p._apis
        assert "get" in p._apis["/hello/{id}"]

    def test_route_event_type_is_apigw_v2(self):
        p = _make_parser(self._plan_with_http_api())
        props = p._apis["/hello/{id}"]["get"]["Properties"]
        assert props["EventType"] == "APIGW_V2"

    def test_route_resolves_lambda_handler(self):
        p = _make_parser(self._plan_with_http_api())
        props = p._apis["/hello/{id}"]["get"]["Properties"]
        assert props["Handler"] == "app.lambda_handler"
        assert props["CodeUri"] == "./src/hello/"

    def test_route_auth_none(self):
        p = _make_parser(self._plan_with_http_api(auth_type="NONE"))
        props = p._apis["/hello/{id}"]["get"]["Properties"]
        assert props["AuthType"] == "NONE"

    def test_route_auth_jwt(self):
        p = _make_parser(self._plan_with_http_api(auth_type="JWT"))
        props = p._apis["/hello/{id}"]["get"]["Properties"]
        assert props["AuthType"] == "JWT"

    def test_route_auth_aws_iam(self):
        p = _make_parser(self._plan_with_http_api(auth_type="AWS_IAM"))
        props = p._apis["/hello/{id}"]["get"]["Properties"]
        assert props["AuthType"] == "AWS_IAM"

    def test_skips_default_route(self):
        p = _make_parser(self._plan_with_http_api("$default"))
        assert p._apis == {}

    def test_skips_route_without_resolvable_integration(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_apigatewayv2_route.hello",
                    "aws_apigatewayv2_route",
                    {
                        "route_key": "GET /hello",
                        "authorization_type": "NONE",
                        "target": None,
                    },
                ),
            ],
            # No config expressions → integration reference unresolvable
        )
        p = _make_parser(plan)
        assert p._apis == {}


# ── REST API V1 ───────────────────────────────────────────────────────────────


class TestRestApiV1:
    def _plan_with_rest_api(self, auth: str = "NONE") -> dict:
        """
        REST API with path /users/{userId} and a single GET handler.
        Resource chain: rest_api → resource.users → resource.user_id
        """
        return _make_plan(
            resources=[
                _data(
                    "data.archive_file.fn",
                    "archive_file",
                    {
                        "output_path": "./build/fn.zip",
                        "source_dir": "./src/fn",
                    },
                ),
                _managed(
                    "aws_lambda_function.fn",
                    "aws_lambda_function",
                    {
                        "handler": "app.handler",
                        "runtime": "python3.12",
                        "filename": "./build/fn.zip",
                    },
                ),
                _managed(
                    "aws_api_gateway_rest_api.api",
                    "aws_api_gateway_rest_api",
                    {"name": "my-api"},
                ),
                _managed(
                    "aws_api_gateway_resource.users",
                    "aws_api_gateway_resource",
                    {"path_part": "users"},
                ),
                _managed(
                    "aws_api_gateway_resource.user_id",
                    "aws_api_gateway_resource",
                    {"path_part": "{userId}"},
                ),
                _managed(
                    "aws_api_gateway_method.get_user",
                    "aws_api_gateway_method",
                    {
                        "http_method": "GET",
                        "authorization": auth,
                        "resource_id": None,
                    },
                ),
                _managed(
                    "aws_api_gateway_integration.get_user",
                    "aws_api_gateway_integration",
                    {
                        "http_method": "GET",
                        "type": "AWS_PROXY",
                        "uri": None,
                        "resource_id": None,
                    },
                ),
            ],
            config_resources=[
                _cfg(
                    "aws_api_gateway_resource.users",
                    {
                        "parent_id": {
                            "references": [
                                "aws_api_gateway_rest_api.api.root_resource_id",
                                "aws_api_gateway_rest_api.api",
                            ]
                        },
                        "path_part": {"constant_value": "users"},
                    },
                ),
                _cfg(
                    "aws_api_gateway_resource.user_id",
                    {
                        "parent_id": {
                            "references": [
                                "aws_api_gateway_resource.users.id",
                                "aws_api_gateway_resource.users",
                            ]
                        },
                        "path_part": {"constant_value": "{userId}"},
                    },
                ),
                _cfg(
                    "aws_api_gateway_method.get_user",
                    {
                        "resource_id": {
                            "references": [
                                "aws_api_gateway_resource.user_id.id",
                                "aws_api_gateway_resource.user_id",
                            ]
                        },
                    },
                ),
                _cfg(
                    "aws_api_gateway_integration.get_user",
                    {
                        "resource_id": {
                            "references": [
                                "aws_api_gateway_resource.user_id.id",
                                "aws_api_gateway_resource.user_id",
                            ]
                        },
                        "uri": {
                            "references": [
                                "aws_lambda_function.fn.invoke_arn",
                                "aws_lambda_function.fn",
                            ]
                        },
                    },
                ),
            ],
        )

    def test_nested_resource_path_resolved(self):
        p = _make_parser(self._plan_with_rest_api())
        assert "/users/{userId}" in p._apis

    def test_method_registered(self):
        p = _make_parser(self._plan_with_rest_api())
        assert "get" in p._apis["/users/{userId}"]

    def test_event_type_is_apigw(self):
        p = _make_parser(self._plan_with_rest_api())
        props = p._apis["/users/{userId}"]["get"]["Properties"]
        assert props["EventType"] == "APIGW"

    def test_lambda_handler_resolved(self):
        p = _make_parser(self._plan_with_rest_api())
        props = p._apis["/users/{userId}"]["get"]["Properties"]
        assert props["Handler"] == "app.handler"
        assert props["CodeUri"] == "./src/fn/"

    def test_auth_none(self):
        p = _make_parser(self._plan_with_rest_api(auth="NONE"))
        props = p._apis["/users/{userId}"]["get"]["Properties"]
        assert props["AuthType"] == "NONE"

    def test_auth_aws_iam(self):
        p = _make_parser(self._plan_with_rest_api(auth="AWS_IAM"))
        props = p._apis["/users/{userId}"]["get"]["Properties"]
        assert props["AuthType"] == "AWS_IAM"

    def test_auth_cognito_user_pools(self):
        p = _make_parser(self._plan_with_rest_api(auth="COGNITO_USER_POOLS"))
        props = p._apis["/users/{userId}"]["get"]["Properties"]
        assert props["AuthType"] == "COGNITO_USER_POOLS"

    def test_non_proxy_integration_skipped(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_api_gateway_integration.mock",
                    "aws_api_gateway_integration",
                    {
                        "http_method": "GET",
                        "type": "MOCK",  # not AWS_PROXY
                        "resource_id": None,
                    },
                ),
            ],
        )
        p = _make_parser(plan)
        assert p._apis == {}

    def test_path_cache_memoization(self):
        p = _make_parser(self._plan_with_rest_api())
        # Resolve twice — second call must return memoized result
        path1 = p._resolve_api_gw_resource_path("aws_api_gateway_resource.user_id")
        path2 = p._resolve_api_gw_resource_path("aws_api_gateway_resource.user_id")
        assert path1 == path2 == "/users/{userId}"


# ── Cognito ───────────────────────────────────────────────────────────────────


class TestCognito:
    def _plan_with_cognito(self) -> dict:
        return _make_plan(
            resources=[
                _managed(
                    "aws_cognito_user_pool.main",
                    "aws_cognito_user_pool",
                    {
                        "name": "main-pool",
                        "auto_verified_attributes": ["email"],
                    },
                ),
                _managed(
                    "aws_cognito_user_pool_client.app",
                    "aws_cognito_user_pool_client",
                    {
                        "name": "app-client",
                        "user_pool_id": None,  # computed
                        "explicit_auth_flows": [
                            "USER_PASSWORD_AUTH",
                            "ALLOW_REFRESH_TOKEN_AUTH",
                        ],
                    },
                ),
            ],
            config_resources=[
                _cfg(
                    "aws_cognito_user_pool_client.app",
                    {
                        "user_pool_id": {
                            "references": [
                                "aws_cognito_user_pool.main.id",
                                "aws_cognito_user_pool.main",
                            ]
                        }
                    },
                ),
            ],
        )

    def test_pool_created(self):
        p = _make_parser(self._plan_with_cognito())
        cognito = p._build_cognito_config()
        assert "main-pool" in cognito
        assert cognito["main-pool"]["PoolName"] == "main-pool"

    def test_pool_auto_verified_attributes(self):
        p = _make_parser(self._plan_with_cognito())
        cognito = p._build_cognito_config()
        assert cognito["main-pool"]["AutoVerifiedAttributes"] == ["email"]

    def test_client_linked_to_pool(self):
        p = _make_parser(self._plan_with_cognito())
        cognito = p._build_cognito_config()
        clients = cognito["main-pool"]["Clients"]
        assert len(clients) == 1
        assert clients[0]["ClientName"] == "app-client"
        assert "USER_PASSWORD_AUTH" in clients[0]["ExplicitAuthFlows"]

    def test_pool_without_clients_gets_default(self):
        plan = _make_plan(
            resources=[
                _managed(
                    "aws_cognito_user_pool.solo",
                    "aws_cognito_user_pool",
                    {"name": "solo-pool"},
                ),
            ]
        )
        p = _make_parser(plan)
        cognito = p._build_cognito_config()
        assert len(cognito["solo-pool"]["Clients"]) == 1
        assert cognito["solo-pool"]["Clients"][0]["ClientName"] == "default"

    def test_cognito_appears_in_config_dict(self):
        p = _make_parser(self._plan_with_cognito())
        config = p._get_config_dict()
        assert "cognito" in config


# ── child modules ─────────────────────────────────────────────────────────────


class TestChildModules:
    def test_resources_in_child_module_are_collected(self):
        plan = _make_plan(
            child_modules=[
                {
                    "address": "module.storage",
                    "resources": [
                        {
                            "address": "module.storage.aws_s3_bucket.assets",
                            "mode": "managed",
                            "type": "aws_s3_bucket",
                            "name": "assets",
                            "values": {"bucket": "assets-bucket"},
                        }
                    ],
                    "child_modules": [],
                }
            ],
        )
        p = _make_parser(plan)
        assert "assets-bucket" in p._buckets

    def test_module_prefix_applied_to_config_exprs(self):
        plan = _make_plan(
            child_modules=[
                {
                    "address": "module.api",
                    "resources": [
                        {
                            "address": "module.api.aws_apigatewayv2_route.hello",
                            "mode": "managed",
                            "type": "aws_apigatewayv2_route",
                            "name": "hello",
                            "values": {
                                "route_key": "GET /hello",
                                "authorization_type": "NONE",
                                "target": None,
                            },
                        }
                    ],
                    "child_modules": [],
                }
            ],
            module_calls={
                "api": {
                    "module": {
                        "resources": [
                            {
                                "address": "aws_apigatewayv2_route.hello",
                                "expressions": {
                                    "target": {
                                        "references": [
                                            "aws_apigatewayv2_integration.hello.id",
                                            "aws_apigatewayv2_integration.hello",
                                        ]
                                    }
                                },
                            }
                        ],
                        "module_calls": {},
                    }
                }
            },
        )
        p = _make_parser(plan)
        # The config expression should be stored with full module prefix
        assert "module.api.aws_apigatewayv2_route.hello" in p._config_exprs


# ── get_config_dict completeness ─────────────────────────────────────────────


class TestGetConfigDict:
    def test_empty_plan_produces_empty_config(self):
        p = _make_parser(_make_plan())
        assert p._get_config_dict() == {}

    def test_all_sections_present(self):
        plan = _make_plan(
            resources=[
                _managed("aws_s3_bucket.b", "aws_s3_bucket", {"bucket": "b"}),
                _managed(
                    "aws_dynamodb_table.t",
                    "aws_dynamodb_table",
                    {
                        "name": "t",
                        "billing_mode": "PAY_PER_REQUEST",
                        "hash_key": "id",
                        "range_key": None,
                        "attribute": [{"name": "id", "type": "S"}],
                    },
                ),
                _managed("aws_sqs_queue.q", "aws_sqs_queue", {"name": "q"}),
                _managed("aws_sns_topic.n", "aws_sns_topic", {"name": "n"}),
                _managed(
                    "aws_ses_email_identity.e",
                    "aws_ses_email_identity",
                    {"email": "e@example.com"},
                ),
                _managed(
                    "aws_cognito_user_pool.p", "aws_cognito_user_pool", {"name": "p"}
                ),
            ]
        )
        p = _make_parser(plan)
        config = p._get_config_dict()
        assert "s3" in config
        assert "dynamodb" in config
        assert "sqs" in config
        assert "sns" in config
        assert "ses" in config
        assert "cognito" in config


# ── create_config_file ────────────────────────────────────────────────────────


class TestCreateConfigFile:
    def test_writes_yaml_to_path(self, tmp_path: Path):
        plan = _make_plan(
            resources=[
                _managed("aws_s3_bucket.b", "aws_s3_bucket", {"bucket": "test-bucket"})
            ]
        )
        config_path = tmp_path / "config.yaml"
        p = _make_parser(plan)
        p.create_config_file(config_path, overwrite=True)

        assert config_path.exists()
        data = yaml.safe_load(config_path.read_text())
        assert data["s3"]["test-bucket"]["BucketName"] == "test-bucket"

    def test_merges_with_existing_when_overwrite_false(self, tmp_path: Path):
        existing = {"paths": {"/existing": {"get": {"Properties": {}}}}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(existing), encoding="utf-8")

        plan = _make_plan(
            resources=[
                _managed("aws_s3_bucket.b", "aws_s3_bucket", {"bucket": "new-bucket"})
            ]
        )
        p = _make_parser(plan)
        p.create_config_file(config_path, overwrite=False)

        data = yaml.safe_load(config_path.read_text())
        # Existing paths preserved, new s3 added
        assert "/existing" in data.get("paths", {})
        assert "new-bucket" in data.get("s3", {})

    def test_overwrites_when_overwrite_true(self, tmp_path: Path):
        existing = {"s3": {"old-bucket": {"BucketName": "old-bucket"}}}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(existing), encoding="utf-8")

        plan = _make_plan(
            resources=[
                _managed("aws_s3_bucket.b", "aws_s3_bucket", {"bucket": "new-bucket"})
            ]
        )
        p = _make_parser(plan)
        p.create_config_file(config_path, overwrite=True)

        data = yaml.safe_load(config_path.read_text())
        assert "new-bucket" in data["s3"]
        assert "old-bucket" not in data["s3"]
