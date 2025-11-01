"""
ボリューム・ファイル管理
Docker環境でのファイル操作を管理
"""

import os
import shutil
from typing import Dict, List, Any
from pathlib import Path


class VolumeManager:
    """Docker環境でのボリューム・ファイル管理"""

    def __init__(self):
        self.workspace_path = Path("/workspace")
        self.api_mock_path = Path("/api_mock")
        self.data_path = Path("/data")

    def setup_volumes(self) -> bool:
        """ボリュームの初期セットアップ"""
        try:
            # 必要なディレクトリの作成
            for path in [self.api_mock_path, self.data_path]:
                path.mkdir(parents=True, exist_ok=True)

            # api_mock/app.py が存在しない場合は作成
            app_py = self.api_mock_path / "app.py"
            if not app_py.exists():
                self._create_default_app_py(app_py)

            return True
        except Exception:
            return False

    def _create_default_app_py(self, app_py_path: Path):
        """デフォルトのapp.pyを作成"""
        default_content = """from sapimo.mock import api


# API endpoints will be generated here by sapimo
# You can customize the behavior by editing this file
"""
        app_py_path.write_text(default_content)

    def sync_api_mock_files(self) -> Dict[str, Any]:
        """api_mockファイルの同期状況を取得"""
        api_mock_files = []

        if self.api_mock_path.exists():
            for file_path in self.api_mock_path.rglob("*"):
                if file_path.is_file():
                    api_mock_files.append(
                        {
                            "path": str(file_path.relative_to(self.api_mock_path)),
                            "size": file_path.stat().st_size,
                            "modified": file_path.stat().st_mtime,
                        }
                    )

        return {
            "api_mock_path": str(self.api_mock_path),
            "files": api_mock_files,
            "total_files": len(api_mock_files),
        }

    def get_data_usage(self) -> Dict[str, Any]:
        """データボリュームの使用状況を取得"""
        usage = {"total_size": 0, "directories": {}}

        if self.data_path.exists():
            for item in self.data_path.iterdir():
                if item.is_dir():
                    dir_size = sum(
                        f.stat().st_size for f in item.rglob("*") if f.is_file()
                    )
                    usage["directories"][item.name] = {
                        "size": dir_size,
                        "files": len(list(item.rglob("*"))),
                    }
                    usage["total_size"] += dir_size

        return usage

    def cleanup_data(self, services: List[str] = None) -> bool:
        """データの清掃"""
        try:
            if not services:
                # 全データを削除
                if self.data_path.exists():
                    shutil.rmtree(self.data_path)
                    self.data_path.mkdir(parents=True, exist_ok=True)
            else:
                # 指定されたサービスのみ削除
                for service in services:
                    service_path = self.data_path / service
                    if service_path.exists():
                        shutil.rmtree(service_path)

            return True
        except Exception:
            return False

    def get_volume_status(self) -> Dict[str, Any]:
        """ボリューム全体の状態を取得"""
        return {
            "workspace": {
                "path": str(self.workspace_path),
                "exists": self.workspace_path.exists(),
                "readable": os.access(self.workspace_path, os.R_OK)
                if self.workspace_path.exists()
                else False,
            },
            "api_mock": {
                "path": str(self.api_mock_path),
                "exists": self.api_mock_path.exists(),
                "writable": os.access(self.api_mock_path, os.W_OK)
                if self.api_mock_path.exists()
                else False,
            },
            "data": {
                "path": str(self.data_path),
                "exists": self.data_path.exists(),
                "writable": os.access(self.data_path, os.W_OK)
                if self.data_path.exists()
                else False,
            },
        }
