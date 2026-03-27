#!/usr/bin/env python3
"""
Docker環境専用AWSモック統合テスト
Phase 4の機能を検証するためのテストスクリプト
"""

import sys
from pathlib import Path

# プロジェクトパスを追加
project_root = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(project_root))

from sapimo.docker.mock_manager import DockerMockManager
from sapimo.constants import CONFIG_FILE
from sapimo.utils import LogManager

logger = LogManager.setup_logger(__file__)


def test_docker_mock_manager():
    """DockerMockManagerの基本機能テスト"""
    print("=" * 50)
    print("Testing DockerMockManager")
    print("=" * 50)

    # CONFIG_FILEが存在しない場合はダミーを作成
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            f.write("""
s3:
  test-bucket:
    BucketName: test-bucket

dynamodb:
  TestTable:
    TableName: TestTable
    AttributeDefinitions:
    - AttributeName: id
      AttributeType: S
    KeySchema:
    - AttributeName: id
      KeyType: HASH
""")
        print(f"Created dummy config at {CONFIG_FILE}")

    try:
        # DockerMockManagerの初期化
        mock_manager = DockerMockManager(CONFIG_FILE)
        print("✅ DockerMockManager initialized successfully")
        print(f"   - Data path: {mock_manager.data_path}")

        # モックの開始
        mock_manager.start()
        print("✅ AWS mocks started successfully")

        # ステータス確認
        status = mock_manager.get_docker_mock_status()
        print("✅ Mock status:")
        for key, value in status.items():
            print(f"   - {key}: {value}")

        # 同期テスト
        sync_result = mock_manager.sync()
        print(f"✅ Sync completed: {sync_result}")

        # S3変更テスト（もしmockが動いている場合）
        s3_changes = mock_manager.get_change("s3")
        print(f"✅ S3 changes: {s3_changes}")

        # モックの停止
        mock_manager.stop()
        print("✅ AWS mocks stopped successfully")

        return True

    except Exception as e:
        logger.exception("Test failed")
        print(f"❌ Test failed: {e}")
        return False


def test_s3_operations():
    """S3操作のテスト"""
    print("\n" + "=" * 50)
    print("Testing S3 Operations")
    print("=" * 50)

    try:
        import boto3
        from moto import mock_aws

        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")

            # バケット作成
            s3.create_bucket(Bucket="test-bucket")
            print("✅ S3 bucket created")

            # オブジェクトアップロード
            s3.put_object(Bucket="test-bucket", Key="test.txt", Body=b"test data")
            print("✅ S3 object uploaded")

            # オブジェクトリスト
            objects = s3.list_objects_v2(Bucket="test-bucket")
            print(f"✅ S3 objects listed: {len(objects.get('Contents', []))} objects")

        return True

    except Exception as e:
        logger.exception("S3 test failed")
        print(f"❌ S3 test failed: {e}")
        return False


def test_dynamodb_operations():
    """DynamoDB操作のテスト"""
    print("\n" + "=" * 50)
    print("Testing DynamoDB Operations")
    print("=" * 50)

    try:
        import boto3
        from moto import mock_aws

        with mock_aws():
            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

            # テーブル作成
            table = dynamodb.create_table(
                TableName="TestTable",
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
            print("✅ DynamoDB table created")

            # アイテム追加
            table.put_item(Item={"id": "test1", "data": "test data"})
            print("✅ DynamoDB item added")

            # アイテム取得
            response = table.get_item(Key={"id": "test1"})
            print(f"✅ DynamoDB item retrieved: {response.get('Item')}")

        return True

    except Exception as e:
        logger.exception("DynamoDB test failed")
        print(f"❌ DynamoDB test failed: {e}")
        return False


if __name__ == "__main__":
    print("Docker AWS Mock Integration Test")
    print("Phase 4 functionality verification")
    print()

    # テスト実行
    tests = [
        test_docker_mock_manager,
        test_s3_operations,
        test_dynamodb_operations,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            logger.exception(f"Test {test.__name__} crashed")
            print(f"❌ Test {test.__name__} crashed: {e}")
            results.append(False)

    # 結果サマリー
    print("\n" + "=" * 50)
    print("Test Results Summary")
    print("=" * 50)

    passed = sum(results)
    total = len(results)

    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed")
        sys.exit(1)
