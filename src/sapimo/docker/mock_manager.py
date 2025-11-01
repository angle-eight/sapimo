"""
Docker環境専用AWSモック管理
"""

from pathlib import Path
from typing import Dict, Any, List, Optional

from sapimo.mock.mock_manager import MockManager as BaseMockManager
from sapimo.constants import WORKING_DIR
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


class DockerMockManager(BaseMockManager):
    """Docker環境専用AWSモック管理"""

    def __init__(self, config_file: Path):
        # Docker環境のデータパス設定（テスト時は一時ディレクトリを使用）
        import os

        if os.access("/data", os.W_OK):
            self.data_path = Path("/data")
        else:
            # 権限がない場合は一時ディレクトリを使用
            import tempfile

            self.data_path = Path(tempfile.mkdtemp(prefix="sapimo_data_"))

        self.config_file = config_file
        self._init_data_called = False

        # Docker環境セットアップ
        self._setup_docker_environment()

        # 永続化データから復元
        self.restore_from_persistent_storage()

        # 基底クラスの初期化
        super().__init__(config_file)

        # 永続化ボリューム設定
        self.setup_persistent_volumes()

    def _setup_docker_environment(self):
        """Docker環境セットアップ"""
        # データディレクトリの作成
        self.data_path.mkdir(parents=True, exist_ok=True)

        # 各AWSサービス用ディレクトリを作成
        for service in ["s3", "dynamodb", "sqs", "sns", "ses"]:
            (self.data_path / service).mkdir(parents=True, exist_ok=True)

        logger.info(f"Docker environment setup complete. Data path: {self.data_path}")

    def setup_persistent_volumes(self):
        """永続化ボリュームの設定"""
        # S3データの永続化設定
        if hasattr(self, "_s3_mock") and self._s3_mock:
            s3_data_path = self.data_path / "s3"
            self._setup_s3_persistence(s3_data_path)

        # DynamoDBデータの永続化設定
        if hasattr(self, "_dynamo_mock") and self._dynamo_mock:
            dynamo_data_path = self.data_path / "dynamodb"
            self._setup_dynamo_persistence(dynamo_data_path)

    def _setup_s3_persistence(self, s3_data_path: Path):
        """S3データの永続化設定"""
        s3_data_path.mkdir(parents=True, exist_ok=True)

        # 既存データがある場合は復元
        for bucket_dir in s3_data_path.iterdir():
            if bucket_dir.is_dir():
                logger.info(f"Restoring S3 bucket: {bucket_dir.name}")

    def _setup_dynamo_persistence(self, dynamo_data_path: Path):
        """DynamoDBデータの永続化設定"""
        dynamo_data_path.mkdir(parents=True, exist_ok=True)

        # 既存データがある場合は復元
        for table_dir in dynamo_data_path.iterdir():
            if table_dir.is_dir():
                logger.info(f"Restoring DynamoDB table: {table_dir.name}")

    def start(self):
        """モック開始（init_data()も自動実行）"""
        super().start()
        self.init_data()
        self._init_data_called = True
        logger.info("Docker AWS mocks started and initialized")

    def sync(self):
        """データを永続化ストレージに同期"""
        # init_data()が呼ばれていない場合は警告
        if not self._init_data_called:
            logger.warning(
                "sync() called before init_data() - calling init_data() first"
            )
            self.init_data()
            self._init_data_called = True

        try:
            # 基底クラスの同期を実行（ローカルファイルシステムへの書き込み）
            sync_result = super().sync()

            # 永続化ストレージに同期
            self._sync_to_persistent_storage()

            logger.info("Successfully synced mock data to persistent storage")
            return sync_result

        except Exception as e:
            logger.error(f"Failed to sync mock data: {e}")
            return {}

    def _sync_to_persistent_storage(self):
        """内部メソッド：永続化ストレージへの同期"""
        # S3データの同期
        if hasattr(self, "_s3_mock") and self._s3_mock:
            self._sync_s3_data()

        # DynamoDBデータの同期
        if hasattr(self, "_dynamo_mock") and self._dynamo_mock:
            self._sync_dynamo_data()

    def _sync_s3_data(self):
        """S3データを永続化ストレージに同期"""
        if not hasattr(self, "_s3_mock") or not self._s3_mock:
            return

        s3_data_path = self.data_path / "s3"
        s3_local_path = WORKING_DIR / "s3"

        # ローカルからDocker永続化ボリュームへコピー
        if s3_local_path.exists():
            import shutil

            try:
                # 古いデータを削除
                if s3_data_path.exists():
                    shutil.rmtree(s3_data_path)

                # 新しいデータをコピー
                shutil.copytree(s3_local_path, s3_data_path)
                logger.debug("S3 data synced to persistent storage")
            except Exception as e:
                logger.error(f"Failed to sync S3 data: {e}")

    def _sync_dynamo_data(self):
        """DynamoDBデータを永続化ストレージに同期"""
        if not hasattr(self, "_dynamo_mock") or not self._dynamo_mock:
            return

        dynamo_data_path = self.data_path / "dynamodb"
        dynamo_local_path = WORKING_DIR / "dynamodb"

        # ローカルからDocker永続化ボリュームへコピー
        if dynamo_local_path.exists():
            import shutil

            try:
                # 古いデータを削除
                if dynamo_data_path.exists():
                    shutil.rmtree(dynamo_data_path)

                # 新しいデータをコピー
                shutil.copytree(dynamo_local_path, dynamo_data_path)
                logger.debug("DynamoDB data synced to persistent storage")
            except Exception as e:
                logger.error(f"Failed to sync DynamoDB data: {e}")

    def restore_from_persistent_storage(self):
        """永続化ストレージからデータを復元"""
        try:
            # S3データの復元
            s3_data_path = self.data_path / "s3"
            if s3_data_path.exists():
                self._restore_s3_data(s3_data_path)

            # DynamoDBデータの復元
            dynamo_data_path = self.data_path / "dynamodb"
            if dynamo_data_path.exists():
                self._restore_dynamo_data(dynamo_data_path)

            logger.info("Successfully restored mock data from persistent storage")

        except Exception as e:
            logger.error(f"Failed to restore mock data: {e}")

    def _restore_s3_data(self, s3_data_path: Path):
        """S3データを復元"""
        s3_local_path = WORKING_DIR / "s3"

        if s3_data_path.exists() and s3_data_path.is_dir():
            import shutil

            try:
                # ローカルパスを作成
                s3_local_path.parent.mkdir(parents=True, exist_ok=True)

                # 古いローカルデータを削除
                if s3_local_path.exists():
                    shutil.rmtree(s3_local_path)

                # 永続化データをローカルにコピー
                shutil.copytree(s3_data_path, s3_local_path)
                logger.debug("S3 data restored from persistent storage")
            except Exception as e:
                logger.error(f"Failed to restore S3 data: {e}")

    def _restore_dynamo_data(self, dynamo_data_path: Path):
        """DynamoDBデータを復元"""
        dynamo_local_path = WORKING_DIR / "dynamodb"

        if dynamo_data_path.exists() and dynamo_data_path.is_dir():
            import shutil

            try:
                # ローカルパスを作成
                dynamo_local_path.parent.mkdir(parents=True, exist_ok=True)

                # 古いローカルデータを削除
                if dynamo_local_path.exists():
                    shutil.rmtree(dynamo_local_path)

                # 永続化データをローカルにコピー
                shutil.copytree(dynamo_data_path, dynamo_local_path)
                logger.debug("DynamoDB data restored from persistent storage")
            except Exception as e:
                logger.error(f"Failed to restore DynamoDB data: {e}")

    def get_change(self, service_name: str = None):
        """変更を取得（永続化も実行）"""
        # 基底クラスの処理を実行
        changes = super().get_change(service_name)

        # 変更検出後に永続化も実行
        if changes:
            try:
                self._sync_to_persistent_storage()
            except Exception as e:
                logger.error(f"Failed to sync changes to persistent storage: {e}")

        return changes

    def get_docker_mock_status(self) -> Dict[str, Any]:
        """モック状態を取得"""
        status = {"data_path": str(self.data_path), "persistent_volumes": {}}

        # 各サービスの永続化データサイズを取得
        for service in ["s3", "dynamodb", "sqs", "sns", "ses"]:
            service_path = self.data_path / service
            if service_path.exists():
                total_size = sum(
                    f.stat().st_size for f in service_path.rglob("*") if f.is_file()
                )
                file_count = len(list(service_path.rglob("*")))

                status["persistent_volumes"][service] = {
                    "size": total_size,
                    "files": file_count,
                    "path": str(service_path),
                }

        return status

    def cleanup_persistent_data(self, services: Optional[List[str]] = None):
        """永続化データの清掃"""
        try:
            if services is None:
                services = ["s3", "dynamodb", "sqs", "sns", "ses"]

            for service in services:
                service_path = self.data_path / service
                if service_path.exists():
                    import shutil

                    shutil.rmtree(service_path)
                    service_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Cleaned up persistent data for {service}")

            return True

        except Exception as e:
            logger.error(f"Failed to cleanup persistent data: {e}")
            return False
