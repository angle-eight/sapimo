"""
Test cases for CognitoMock and MockManager cognito/placeholder features
"""

import json
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from sapimo.mock.mock_manager import CognitoMock, MockManager


@pytest.fixture
def cognito_config():
    return {
        "my-pool": {
            "PoolName": "my-pool",
            "Clients": [
                {
                    "ClientName": "web-client",
                    "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"],
                }
            ],
        }
    }


@pytest.fixture
def cognito_config_multi_client():
    return {
        "my-pool": {
            "PoolName": "my-pool",
            "Clients": [
                {
                    "ClientName": "web-client",
                    "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"],
                },
                {
                    "ClientName": "mobile-client",
                    "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"],
                },
            ],
        }
    }


@pytest.fixture
def tmp_working_dir(tmp_path, monkeypatch):
    """Patch WORKING_DIR to use a temp directory."""
    monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
    return tmp_path


class TestCognitoMockInitData:
    def test_pool_and_client_created(self, cognito_config, tmp_working_dir):
        """config から UserPool と Client が正しく作成されること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            pool_id = mock.get_pool_id("my-pool")
            client_id = mock.get_client_id("my-pool", "web-client")

            assert pool_id is not None
            assert client_id is not None

            # Verify pool exists via boto3
            client = boto3.client("cognito-idp", region_name="us-east-1")
            pool = client.describe_user_pool(UserPoolId=pool_id)
            assert pool["UserPool"]["Name"] == "my-pool"

            # Verify client exists
            pool_client = client.describe_user_pool_client(
                UserPoolId=pool_id, ClientId=client_id
            )
            assert pool_client["UserPoolClient"]["ClientName"] == "web-client"
        finally:
            mock.stop()

    def test_multi_client_created(self, cognito_config_multi_client, tmp_working_dir):
        """複数 Client が正しく作成されること"""
        mock = CognitoMock(cognito_config_multi_client)
        mock.start()
        try:
            mock.init_data()

            web_id = mock.get_client_id("my-pool", "web-client")
            mobile_id = mock.get_client_id("my-pool", "mobile-client")

            assert web_id is not None
            assert mobile_id is not None
            assert web_id != mobile_id
        finally:
            mock.stop()

    def test_initial_users_loaded(self, cognito_config, tmp_working_dir):
        """data.json のユーザーが sign_up + confirm され、認証可能なこと"""
        pool_dir = tmp_working_dir / "cognito" / "my-pool"
        pool_dir.mkdir(parents=True, exist_ok=True)
        users = [
            {
                "username": "testuser",
                "password": "TestPass1!",
                "email": "test@example.com",
            },
            {
                "username": "admin",
                "password": "AdminPass1!",
                "email": "admin@example.com",
            },
        ]
        (pool_dir / "data.json").write_text(json.dumps(users))

        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            client = boto3.client("cognito-idp", region_name="us-east-1")
            pool_id = mock.get_pool_id("my-pool")
            resp = client.list_users(UserPoolId=pool_id)
            usernames = [u["Username"] for u in resp["Users"]]
            assert "testuser" in usernames
            assert "admin" in usernames

            # Verify users are CONFIRMED and can authenticate
            client_id = mock.get_client_id("my-pool", "web-client")
            auth_resp = client.initiate_auth(
                ClientId=client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": "testuser", "PASSWORD": "TestPass1!"},
            )
            assert "AuthenticationResult" in auth_resp
            assert "AccessToken" in auth_resp["AuthenticationResult"]
        finally:
            mock.stop()

    def test_no_data_json(self, cognito_config, tmp_working_dir):
        """data.json がなくても Pool/Client は作成されエラーにならないこと"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            assert mock.get_pool_id("my-pool") is not None
            assert mock.get_client_id("my-pool", "web-client") is not None
        finally:
            mock.stop()

    def test_empty_data_json(self, cognito_config, tmp_working_dir):
        """空 data.json でもエラーにならないこと"""
        pool_dir = tmp_working_dir / "cognito" / "my-pool"
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / "data.json").write_text("[]")

        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()
            assert mock.get_pool_id("my-pool") is not None
        finally:
            mock.stop()


class TestCognitoAuth:
    def test_auth_success(self, cognito_config, tmp_working_dir):
        """USER_PASSWORD_AUTH でトークンが返ること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            client = boto3.client("cognito-idp", region_name="us-east-1")
            pool_id = mock.get_pool_id("my-pool")
            client_id = mock.get_client_id("my-pool", "web-client")

            # Sign up and confirm a user
            client.sign_up(
                ClientId=client_id,
                Username="newuser",
                Password="NewPass1!",
                UserAttributes=[{"Name": "email", "Value": "new@example.com"}],
            )
            client.admin_confirm_sign_up(UserPoolId=pool_id, Username="newuser")

            # Authenticate
            resp = client.initiate_auth(
                ClientId=client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": "newuser", "PASSWORD": "NewPass1!"},
            )
            assert "AccessToken" in resp["AuthenticationResult"]
            assert "IdToken" in resp["AuthenticationResult"]
            assert "RefreshToken" in resp["AuthenticationResult"]
        finally:
            mock.stop()

    def test_auth_failure_wrong_password(self, cognito_config, tmp_working_dir):
        """パスワード不一致で NotAuthorizedException が発生すること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            client = boto3.client("cognito-idp", region_name="us-east-1")
            pool_id = mock.get_pool_id("my-pool")
            client_id = mock.get_client_id("my-pool", "web-client")

            client.sign_up(
                ClientId=client_id,
                Username="user1",
                Password="CorrectPass1!",
            )
            client.admin_confirm_sign_up(UserPoolId=pool_id, Username="user1")

            with pytest.raises(ClientError) as exc_info:
                client.initiate_auth(
                    ClientId=client_id,
                    AuthFlow="USER_PASSWORD_AUTH",
                    AuthParameters={"USERNAME": "user1", "PASSWORD": "WrongPass!"},
                )
            assert "NotAuthorizedException" in str(exc_info.value)
        finally:
            mock.stop()


class TestCognitoSync:
    def test_sync_writes_users(self, cognito_config, tmp_working_dir):
        """sync 後に data.json にユーザーが反映されること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()

            # Add a user via Lambda-like code
            client = boto3.client("cognito-idp", region_name="us-east-1")
            pool_id = mock.get_pool_id("my-pool")
            client_id = mock.get_client_id("my-pool", "web-client")

            client.sign_up(
                ClientId=client_id,
                Username="lambda-user",
                Password="LambdaPass1!",
                UserAttributes=[{"Name": "email", "Value": "lambda@example.com"}],
            )
            client.admin_confirm_sign_up(UserPoolId=pool_id, Username="lambda-user")

            mock.sync()

            data_file = tmp_working_dir / "cognito" / "my-pool" / "data.json"
            assert data_file.exists()
            data = json.loads(data_file.read_text())
            usernames = [u["username"] for u in data]
            assert "lambda-user" in usernames
        finally:
            mock.stop()


class TestCognitoGetIds:
    def test_get_pool_id(self, cognito_config, tmp_working_dir):
        """正しい Pool ID が返ること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()
            pool_id = mock.get_pool_id("my-pool")
            assert pool_id is not None
            assert isinstance(pool_id, str)
            assert len(pool_id) > 0
        finally:
            mock.stop()

    def test_get_client_id(self, cognito_config, tmp_working_dir):
        """正しい Client ID が返ること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()
            client_id = mock.get_client_id("my-pool", "web-client")
            assert client_id is not None
            assert isinstance(client_id, str)
        finally:
            mock.stop()

    def test_get_nonexistent_pool_id(self, cognito_config, tmp_working_dir):
        """存在しない Pool 名で None が返ること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()
            assert mock.get_pool_id("nonexistent") is None
        finally:
            mock.stop()

    def test_get_nonexistent_client_id(self, cognito_config, tmp_working_dir):
        """存在しない Client 名で None が返ること"""
        mock = CognitoMock(cognito_config)
        mock.start()
        try:
            mock.init_data()
            assert mock.get_client_id("my-pool", "nonexistent") is None
        finally:
            mock.stop()


class TestResolvePlaceholders:
    def _make_manager_with_cognito(self, tmp_path, cognito_config):
        """Helper to create a MockManager with cognito config."""
        config_data = {
            "paths": {"/test": {"get": {"Properties": {"Handler": "app.handler"}}}},
            "cognito": cognito_config,
        }
        config_file = tmp_path / "config.yaml"
        import yaml

        config_file.write_text(yaml.dump(config_data))
        return config_file

    def test_pool_id_placeholder(self, tmp_path, monkeypatch):
        """${cognito:my-pool:PoolId} が実際の ID に置換されること"""
        monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
        cognito_config = {
            "my-pool": {
                "PoolName": "my-pool",
                "Clients": [
                    {"ClientName": "web", "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"]}
                ],
            }
        }
        config_file = self._make_manager_with_cognito(tmp_path, cognito_config)

        mgr = MockManager(config_file)
        mgr.start()
        try:
            mgr.init_data()

            env = {"USER_POOL_ID": "${cognito:my-pool:PoolId}", "OTHER": "value"}
            resolved = mgr.resolve_placeholders(env)

            assert resolved["OTHER"] == "value"
            assert "${cognito:" not in resolved["USER_POOL_ID"]
            assert resolved["USER_POOL_ID"] == mgr.get_cognito_pool_id("my-pool")
        finally:
            mgr.stop()

    def test_client_id_placeholder(self, tmp_path, monkeypatch):
        """${cognito:my-pool:ClientId:web} が正しく置換されること"""
        monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
        cognito_config = {
            "my-pool": {
                "PoolName": "my-pool",
                "Clients": [
                    {"ClientName": "web", "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"]}
                ],
            }
        }
        config_file = self._make_manager_with_cognito(tmp_path, cognito_config)

        mgr = MockManager(config_file)
        mgr.start()
        try:
            mgr.init_data()

            env = {"CLIENT_ID": "${cognito:my-pool:ClientId:web}"}
            resolved = mgr.resolve_placeholders(env)

            assert "${cognito:" not in resolved["CLIENT_ID"]
            assert resolved["CLIENT_ID"] == mgr.get_cognito_client_id("my-pool", "web")
        finally:
            mgr.stop()

    def test_nonexistent_pool_placeholder_warns(self, tmp_path, monkeypatch):
        """存在しない Pool 名のプレースホルダで値が保持されること"""
        monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
        cognito_config = {
            "my-pool": {
                "PoolName": "my-pool",
                "Clients": [
                    {"ClientName": "web", "ExplicitAuthFlows": ["USER_PASSWORD_AUTH"]}
                ],
            }
        }
        config_file = self._make_manager_with_cognito(tmp_path, cognito_config)

        mgr = MockManager(config_file)
        mgr.start()
        try:
            mgr.init_data()

            env = {"POOL_ID": "${cognito:unknown-pool:PoolId}"}
            resolved = mgr.resolve_placeholders(env)
            # Unresolved placeholder stays as-is with a warning
            assert resolved["POOL_ID"] == "${cognito:unknown-pool:PoolId}"
        finally:
            mgr.stop()

    def test_no_cognito_config(self, tmp_path, monkeypatch):
        """cognito 設定がない場合、プレースホルダは置換されずスキップ"""
        monkeypatch.setattr("sapimo.mock.mock_manager.WORKING_DIR", tmp_path)
        config_data = {
            "paths": {"/test": {"get": {"Properties": {"Handler": "app.handler"}}}},
        }
        import yaml

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        mgr = MockManager(config_file)
        mgr.start()
        try:
            mgr.init_data()

            env = {"POOL_ID": "${cognito:pool:PoolId}", "NORMAL": "abc"}
            resolved = mgr.resolve_placeholders(env)
            # No cognito mock → env returned unchanged
            assert resolved["POOL_ID"] == "${cognito:pool:PoolId}"
            assert resolved["NORMAL"] == "abc"
        finally:
            mgr.stop()
