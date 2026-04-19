"""Parser for Terraform plan JSON output.

Usage:
    terraform plan -out=tfplan
    terraform show -json tfplan > plan.json
    sapimo init --terraform plan.json
"""

import json
from pathlib import Path
from typing import Optional

import yaml

from sapimo.constants import AuthType, EventType
from sapimo.exceptions import TerraformPlanParseError
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class TerraformPlanParser:
    """
    Parses ``terraform show -json tfplan`` (plan JSON) and converts it
    to sapimo's config.yaml format.

    The parser uses two sections of the plan:
    - ``planned_values``: constant resource attribute values (names, paths, etc.)
    - ``configuration``: raw expressions including cross-resource references
      (parent_id chains, integration → Lambda ARN refs, etc.)
    """

    def __init__(self, filepath: Path):
        self._root = filepath.parent
        plan = self._load_plan(filepath)

        # All resources keyed by their fully-qualified address
        self._all_resources: dict[str, dict] = {}
        self._collect_resources(plan.get("planned_values", {}).get("root_module", {}))

        # Configuration expressions keyed by fully-qualified address
        # (module prefix applied during recursive collection)
        self._config_exprs: dict[str, dict] = {}
        self._collect_config_exprs(plan.get("configuration", {}).get("root_module", {}))

        # {output_path: source_dir} built from data.archive_file.* resources
        self._archive_output_to_source: dict[str, str] = self._build_archive_file_map()

        # Memoization cache for REST API resource path resolution
        self._path_cache: dict[str, str] = {}

        self._parse_all()

    # ── loading ──────────────────────────────────────────────────────────────

    def _load_plan(self, filepath: Path) -> dict:
        try:
            with open(filepath, encoding="utf-8") as f:
                plan = json.load(f)
        except json.JSONDecodeError as e:
            raise TerraformPlanParseError(
                f"Failed to parse {filepath.name} as JSON: {e}"
            ) from e
        if "planned_values" not in plan:
            raise TerraformPlanParseError(
                f"{filepath.name} does not appear to be a terraform plan JSON file. "
                "Generate it with:\n"
                "  terraform plan -out=tfplan\n"
                "  terraform show -json tfplan > plan.json"
            )
        return plan

    # ── collection ───────────────────────────────────────────────────────────

    def _collect_resources(self, module: dict) -> None:
        """Recursively collect all planned resources, including those inside child modules."""
        for resource in module.get("resources", []):
            self._all_resources[resource["address"]] = resource
        for child in module.get("child_modules", []):
            self._collect_resources(child)

    def _collect_config_exprs(self, config_module: dict, prefix: str = "") -> None:
        """
        Recursively collect configuration expressions.
        Resources inside ``module_calls`` receive a module-path prefix so their
        keys match the fully-qualified addresses produced by ``_collect_resources``.
        """
        for resource in config_module.get("resources", []):
            full_address = prefix + resource["address"]
            self._config_exprs[full_address] = resource.get("expressions", {})
        for module_name, module_call in config_module.get("module_calls", {}).items():
            child_prefix = f"{prefix}module.{module_name}."
            self._collect_config_exprs(module_call.get("module", {}), child_prefix)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_module_prefix(address: str) -> str:
        """Return the module prefix of a resource address (e.g. ``'module.A.'`` or ``''``)."""
        parts = address.split(".")
        # Resource address: [module.X.]*<type>.<name>
        # Module prefix = everything except the last two parts
        if len(parts) <= 2:
            return ""
        return ".".join(parts[:-2]) + "."

    def _resolve_ref(self, expr_field: dict, from_address: str) -> Optional[str]:
        """
        Extract the first meaningful resource address from an expression's
        ``references`` list, applying the same module prefix as the source resource.

        The ``references`` array is ordered most-specific-first
        (e.g. ``["aws_lambda_function.fn.arn", "aws_lambda_function.fn"]``).
        We look for the first entry whose ``type.name`` form exists in ``_all_resources``.
        """
        refs = expr_field.get("references", [])
        prefix = self._get_module_prefix(from_address)
        for ref in refs:
            parts = ref.split(".")
            if len(parts) < 2:
                continue
            short_ref = f"{parts[0]}.{parts[1]}"
            # Try same module first, then root
            for candidate in (prefix + short_ref, short_ref):
                if candidate in self._all_resources:
                    return candidate
        return None

    def _get_values(self, address: str) -> dict:
        return self._all_resources.get(address, {}).get("values", {}) or {}

    def _get_exprs(self, address: str) -> dict:
        return self._config_exprs.get(address, {})

    # ── archive_file → CodeUri resolution ────────────────────────────────────

    def _build_archive_file_map(self) -> dict[str, str]:
        """Build ``{output_path: source_dir}`` from ``data.archive_file.*`` resources."""
        result: dict[str, str] = {}
        for address, res in self._all_resources.items():
            if res.get("mode") != "data" or res.get("type") != "archive_file":
                continue
            values = self._get_values(address)
            output_path = values.get("output_path") or ""
            source_dir = values.get("source_dir") or ""
            if output_path and source_dir:
                result[output_path] = source_dir
        return result

    def _resolve_code_uri(self, lambda_values: dict) -> str:
        """Resolve CodeUri for a Lambda function from its plan values."""
        filename = lambda_values.get("filename") or ""
        if filename:
            source_dir = self._archive_output_to_source.get(filename)
            if source_dir:
                return source_dir.rstrip("/") + "/"
            logger.warning(
                "Cannot resolve source directory for Lambda with filename=%s. "
                "No matching data.archive_file resource found. "
                "Edit api_mock/config.yaml manually.",
                filename,
            )
            return ""
        s3_bucket = lambda_values.get("s3_bucket") or ""
        if s3_bucket:
            logger.warning(
                "Lambda uses S3 deployment (s3_bucket=%s). "
                "CodeUri cannot be resolved from plan. Edit api_mock/config.yaml manually.",
                s3_bucket,
            )
        return ""

    # ── parsing ───────────────────────────────────────────────────────────────

    def _parse_all(self) -> None:
        self._buckets: dict = {}
        self._tables: dict = {}
        self._sqss: dict = {}
        self._snss: dict = {}
        self._sess: dict = {}
        self._cognito_pools: dict[str, dict] = {}  # address → values
        self._cognito_clients: dict[str, dict] = {}  # address → values
        self._lambda_map: dict[str, dict] = {}  # address → values

        for address, res in self._all_resources.items():
            if res.get("mode") == "data":
                continue  # data sources (archive_file etc.) are handled separately
            rtype = res.get("type", "")
            values = self._get_values(address)

            match rtype:
                case "aws_s3_bucket":
                    bucket_name = values.get("bucket") or address.split(".")[-1]
                    self._buckets[bucket_name] = {"BucketName": bucket_name}

                case "aws_dynamodb_table":
                    self._parse_dynamodb(values)

                case "aws_sqs_queue":
                    queue_name = values.get("name") or address.split(".")[-1]
                    entry: dict = {"QueueName": queue_name}
                    for tf_attr, config_key in [
                        ("delay_seconds", "DelaySeconds"),
                        ("visibility_timeout_seconds", "VisibilityTimeout"),
                        ("message_retention_seconds", "MessageRetentionPeriod"),
                    ]:
                        if values.get(tf_attr) is not None:
                            entry[config_key] = values[tf_attr]
                    self._sqss[queue_name] = entry

                case "aws_sns_topic":
                    topic_name = values.get("name") or address.split(".")[-1]
                    self._snss[topic_name] = {"TopicName": topic_name}

                case "aws_ses_email_identity":
                    email = values.get("email") or address.split(".")[-1]
                    self._sess[email] = {"EmailIdentity": email}

                case "aws_cognito_user_pool":
                    self._cognito_pools[address] = values

                case "aws_cognito_user_pool_client":
                    self._cognito_clients[address] = values

                case "aws_lambda_function":
                    self._lambda_map[address] = values

        self._apis = self._build_apis()

    def _parse_dynamodb(self, values: dict) -> None:
        table_name = values.get("name") or "unknown_table"
        hash_key = values.get("hash_key") or ""
        range_key = values.get("range_key") or None
        billing_mode = values.get("billing_mode") or "PROVISIONED"

        all_attrs = [
            {"AttributeName": a["name"], "AttributeType": a["type"]}
            for a in (values.get("attribute") or [])
        ]
        # Sapimo / CloudFormation convention: only include key attributes
        key_names = {hash_key} | ({range_key} if range_key else set())
        key_attrs = [
            a for a in all_attrs if a["AttributeName"] in key_names
        ] or all_attrs

        key_schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
        if range_key:
            key_schema.append({"AttributeName": range_key, "KeyType": "RANGE"})

        table_config: dict = {
            "TableName": table_name,
            "AttributeDefinitions": key_attrs,
            "KeySchema": key_schema,
            "BillingMode": billing_mode,
        }
        if billing_mode == "PROVISIONED":
            table_config["ProvisionedThroughput"] = {
                "ReadCapacityUnits": values.get("read_capacity") or 5,
                "WriteCapacityUnits": values.get("write_capacity") or 5,
            }
        self._tables[table_name] = table_config

    # ── API building ──────────────────────────────────────────────────────────

    def _build_apis(self) -> dict:
        apis: dict = {}
        self._build_http_api_v2(apis)
        self._build_rest_api_v1(apis)
        return apis

    def _build_lambda_props(self, lambda_address: str) -> dict:
        values = self._lambda_map[lambda_address]
        props: dict = {
            "Handler": values.get("handler") or "app.lambda_handler",
            "Runtime": values.get("runtime") or "python3.12",
            "CodeUri": self._resolve_code_uri(values),
        }
        env_vars: dict = {}
        for env_block in values.get("environment") or []:
            env_vars.update(env_block.get("variables") or {})
        if env_vars:
            props["Environment"] = {"Variables": env_vars}
        timeout = values.get("timeout")
        if timeout:
            props["Timeout"] = timeout
        memory = values.get("memory_size")
        if memory:
            props["MemorySize"] = memory
        return props

    def _build_http_api_v2(self, apis: dict) -> None:
        """Process ``aws_apigatewayv2_route`` → ``aws_apigatewayv2_integration`` → ``aws_lambda_function``."""
        # Step 1: integration address → lambda address (via configuration URI reference)
        integration_to_lambda: dict[str, str] = {}
        for address, res in self._all_resources.items():
            if res.get("type") != "aws_apigatewayv2_integration":
                continue
            exprs = self._get_exprs(address)
            lambda_addr = self._resolve_ref(exprs.get("integration_uri", {}), address)
            if lambda_addr and lambda_addr in self._lambda_map:
                integration_to_lambda[address] = lambda_addr

        # Step 2: route → integration → lambda
        for address, res in self._all_resources.items():
            if res.get("type") != "aws_apigatewayv2_route":
                continue
            values = self._get_values(address)
            route_key = values.get("route_key") or ""
            if not route_key or " " not in route_key:
                continue
            method, path = route_key.split(" ", 1)
            method = method.upper()
            if method.startswith("$"):
                continue  # $DEFAULT, $CONNECT, $DISCONNECT

            exprs = self._get_exprs(address)
            integration_addr = self._resolve_ref(exprs.get("target", {}), address)
            if not integration_addr or integration_addr not in integration_to_lambda:
                logger.warning(
                    "Cannot resolve Lambda for HTTP API route %s %s, skipping. "
                    "Edit api_mock/config.yaml manually.",
                    method,
                    path,
                )
                continue

            lambda_addr = integration_to_lambda[integration_addr]
            props = self._build_lambda_props(lambda_addr)
            props["EventType"] = EventType.APIGW_V2.name
            auth_type_str = (values.get("authorization_type") or "NONE").upper()
            try:
                props["AuthType"] = AuthType[auth_type_str].name
            except KeyError:
                props["AuthType"] = AuthType.NONE.name

            apis.setdefault(path, {})[method.lower()] = {"Properties": props}

    def _resolve_api_gw_resource_path(self, address: str) -> str:
        """
        Build the full URL path for an ``aws_api_gateway_resource`` by traversing
        the ``parent_id`` reference chain in the configuration block.
        Results are memoized.
        """
        if address in self._path_cache:
            return self._path_cache[address]

        values = self._get_values(address)
        path_part = values.get("path_part") or ""
        exprs = self._get_exprs(address)
        parent_addr = self._resolve_ref(exprs.get("parent_id", {}), address)

        if not parent_addr:
            result = f"/{path_part}"
        else:
            parent_type = self._all_resources.get(parent_addr, {}).get("type", "")
            if parent_type == "aws_api_gateway_rest_api":
                result = f"/{path_part}"
            else:
                parent_path = self._resolve_api_gw_resource_path(parent_addr)
                result = f"{parent_path}/{path_part}"

        self._path_cache[address] = result
        return result

    def _find_method_for_resource(
        self, resource_address: str, http_method: str
    ) -> Optional[str]:
        """Find the ``aws_api_gateway_method`` that targets the given resource and method."""
        for address, res in self._all_resources.items():
            if res.get("type") != "aws_api_gateway_method":
                continue
            exprs = self._get_exprs(address)
            method_resource_addr = self._resolve_ref(
                exprs.get("resource_id", {}), address
            )
            method_val = self._get_values(address).get("http_method", "")
            if (
                method_resource_addr == resource_address
                and method_val.upper() == http_method.upper()
            ):
                return address
        return None

    def _build_rest_api_v1(self, apis: dict) -> None:
        """Process ``aws_api_gateway_integration`` to build REST API V1 paths."""
        for address, res in self._all_resources.items():
            if res.get("type") != "aws_api_gateway_integration":
                continue
            values = self._get_values(address)
            integration_type = (values.get("type") or "").upper()
            if integration_type not in ("AWS_PROXY", "AWS"):
                continue

            exprs = self._get_exprs(address)

            # Resolve resource_id → path
            resource_addr = self._resolve_ref(exprs.get("resource_id", {}), address)
            if not resource_addr:
                logger.warning(
                    "Cannot resolve resource_id for REST API integration %s, skipping.",
                    address,
                )
                continue
            path = self._resolve_api_gw_resource_path(resource_addr)

            # Resolve URI → lambda (invoke_arn reference)
            lambda_addr = self._resolve_ref(exprs.get("uri", {}), address)
            if not lambda_addr or lambda_addr not in self._lambda_map:
                logger.warning(
                    "Cannot resolve Lambda for REST API integration %s, skipping.",
                    address,
                )
                continue

            method = (values.get("http_method") or "ANY").upper()

            # Auth from matching aws_api_gateway_method
            auth_type = AuthType.NONE.name
            method_addr = self._find_method_for_resource(resource_addr, method)
            if method_addr:
                method_values = self._get_values(method_addr)
                auth_str = (method_values.get("authorization") or "NONE").upper()
                try:
                    auth_type = AuthType[auth_str].name
                except KeyError:
                    auth_type = AuthType.NONE.name

            props = self._build_lambda_props(lambda_addr)
            props["EventType"] = EventType.APIGW.name
            props["AuthType"] = auth_type

            apis.setdefault(path, {})[method.lower()] = {"Properties": props}

    # ── Cognito ───────────────────────────────────────────────────────────────

    def _build_cognito_config(self) -> dict:
        cognito: dict = {}
        pool_addr_to_name: dict[str, str] = {}

        for address, values in self._cognito_pools.items():
            pool_name = values.get("name") or address.split(".")[-1]
            cognito[pool_name] = {"PoolName": pool_name, "Clients": []}
            auto_verified = values.get("auto_verified_attributes") or []
            if auto_verified:
                cognito[pool_name]["AutoVerifiedAttributes"] = auto_verified
            pool_addr_to_name[address] = pool_name

        for address, values in self._cognito_clients.items():
            client_name = values.get("name") or address.split(".")[-1]
            explicit_flows = values.get("explicit_auth_flows") or ["USER_PASSWORD_AUTH"]
            exprs = self._get_exprs(address)
            pool_addr = self._resolve_ref(exprs.get("user_pool_id", {}), address)
            pool_name = pool_addr_to_name.get(pool_addr) if pool_addr else None
            if pool_name and pool_name in cognito:
                cognito[pool_name]["Clients"].append(
                    {"ClientName": client_name, "ExplicitAuthFlows": explicit_flows}
                )
            else:
                logger.warning(
                    "Cannot resolve user_pool for Cognito client %s, skipping.", address
                )

        for pool_cfg in cognito.values():
            if not pool_cfg["Clients"]:
                pool_cfg["Clients"].append(
                    {
                        "ClientName": "default",
                        "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"],
                    }
                )
        return cognito

    # ── output ────────────────────────────────────────────────────────────────

    def _get_config_dict(self) -> dict:
        config: dict = {}
        if self._apis:
            config["paths"] = self._apis
        if self._buckets:
            config["s3"] = self._buckets
        if self._tables:
            config["dynamodb"] = self._tables
        if self._sqss:
            config["sqs"] = self._sqss
        if self._snss:
            config["sns"] = self._snss
        if self._sess:
            config["ses"] = self._sess
        if self._cognito_pools:
            config["cognito"] = self._build_cognito_config()
        return config

    def create_config_file(self, output_path: Path, overwrite: bool = True) -> None:
        """
        Write config.yaml.
        If *overwrite* is False and the file already exists, the new config is
        merged on top of the existing one (new keys win).
        """
        if not overwrite and output_path.exists():
            try:
                old_config = (
                    yaml.safe_load(output_path.read_text(encoding="utf-8")) or {}
                )
            except Exception:
                logger.exception("Failed to read existing config at %s", output_path)
                old_config = {}
        else:
            old_config = {}

        config_dict = self._get_config_dict()
        old_config.update(config_dict)

        no_alias_dumper = yaml.dumper.Dumper
        no_alias_dumper.ignore_aliases = lambda self, data: True
        yml = yaml.dump(old_config, Dumper=no_alias_dumper)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yml)
