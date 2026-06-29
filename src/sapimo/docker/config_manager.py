"""
Docker設定ファイルの読み込み・管理
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class DockerConfigManager:
    """Dockerの設定ファイル管理"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("sapimo-docker.yml")
        self._config = None
        self._load_config()

    def _load_config(self):
        """設定ファイルを読み込み（存在しない場合はデフォルト値使用）"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    self._config = yaml.safe_load(f) or {}
            except yaml.YAMLError:
                self._config = {}
        else:
            self._config = {}

    def get_python_config(self) -> Dict[str, Any]:
        """Python関連の設定を取得"""
        python_config = self._config.get("python", {})

        # デフォルト値
        defaults = {
            "default_version": "3.12",
            "auto_install_missing": True,
            "versions": ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"],
        }

        # ユーザー設定とマージ
        for key, default_value in defaults.items():
            if key not in python_config:
                python_config[key] = default_value

        return python_config

    def get_aws_mocks_config(self) -> Dict[str, Any]:
        """AWS Mock関連の設定を取得"""
        aws_config = self._config.get("aws_mocks", {})

        # デフォルト値
        defaults = {
            "persist_data": True,
            "auto_create_buckets": True,
            "auto_create_tables": True,
        }

        for key, default_value in defaults.items():
            if key not in aws_config:
                aws_config[key] = default_value

        return aws_config

    def get_development_config(self) -> Dict[str, Any]:
        """開発関連の設定を取得"""
        dev_config = self._config.get("development", {})

        # デフォルト値
        defaults = {"auto_reload": True, "log_level": "INFO", "show_lambda_logs": True}

        for key, default_value in defaults.items():
            if key not in dev_config:
                dev_config[key] = default_value

        return dev_config

    def get_network_config(self) -> Dict[str, Any]:
        """ネットワーク関連の設定を取得"""
        # トップレベルの host/port 設定を確認
        network_config = {}

        if "host" in self._config:
            network_config["host"] = self._config["host"]
        else:
            network_config["host"] = "127.0.0.1"

        if "port" in self._config:
            network_config["port"] = self._config["port"]
        else:
            network_config["port"] = 3000

        return network_config

    def get_all_config(self) -> Dict[str, Any]:
        """すべての設定を取得"""
        return {
            "python": self.get_python_config(),
            "aws_mocks": self.get_aws_mocks_config(),
            "development": self.get_development_config(),
            "network": self.get_network_config(),
            "raw_config": self._config,
        }

    def create_default_config(self, force: bool = False) -> bool:
        """デフォルト設定ファイルを作成"""
        if self.config_path.exists() and not force:
            return False

        default_config = {
            "python": self.get_python_config(),
            "aws_mocks": self.get_aws_mocks_config(),
            "development": self.get_development_config(),
        }

        try:
            with open(self.config_path, "w") as f:
                yaml.dump(default_config, f, default_flow_style=False, indent=2)
            return True
        except Exception:
            return False
