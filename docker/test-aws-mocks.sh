#!/bin/bash
# Docker環境専用AWSモック統合テスト

echo "🧪 Testing Docker AWS Mock Integration (Container Environment)"

# Docker Composeが起動しているかチェック
if ! docker compose ps | grep -q "sapimo.*Up"; then
    echo "❌ Docker Compose services are not running. Please run 'docker compose up -d' first."
    exit 1
fi

# コンテナ内でテスト実行
docker compose exec sapimo-aws-mock python3 -c "
import sys
sys.path.insert(0, '/workspace/src')

try:
    from sapimo.docker.mock_manager import DockerMockManager
    from sapimo.constants import CONFIG_FILE
    import tempfile
    import yaml

    # テスト用設定ファイル作成
    test_config = {
        'paths': {
            '/test': {
                'get': {
                    'Properties': {
                        'CodeUri': 'lambda/test/',
                        'Handler': 'app.lambda_handler',
                        'Runtime': 'python3.12',
                        'Timeout': 3
                    }
                }
            }
        },
        's3': {'test-bucket': {}},
        'dynamodb': {
            'test-table': {
                'TableName': 'test-table',
                'KeySchema': [{'AttributeName': 'id', 'KeyType': 'HASH'}],
                'AttributeDefinitions': [{'AttributeName': 'id', 'AttributeType': 'S'}],
                'ProvisionedThroughput': {'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
            }
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(test_config, f)
        test_config_path = f.name

    print('✅ Test config created')

    # DockerMockManagerのテスト
    from pathlib import Path
    mock_manager = DockerMockManager(Path(test_config_path))

    print('✅ DockerMockManager initialized')

    # ステータステスト
    status = mock_manager.get_docker_mock_status()
    print(f'✅ Status retrieved: {len(status)} items')

    # 永続化パステスト
    print(f'✅ Data path: {status[\"data_path\"]}')

    # モック開始・停止テスト
    mock_manager.start()
    print('✅ Mock started successfully')

    # 同期テスト
    sync_result = mock_manager.sync()
    print('✅ Sync completed')

    mock_manager.stop()
    print('✅ Mock stopped successfully')

    import os
    os.unlink(test_config_path)

    print('🎉 All Docker AWS mock tests passed!')

except ImportError as e:
    print(f'❌ Import error: {e}')
    import sys
    sys.exit(1)
except Exception as e:
    print(f'❌ Test failed: {e}')
    import traceback
    traceback.print_exc()
    import sys
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo "✅ Docker AWS Mock integration test completed successfully"
else
    echo "❌ Docker AWS Mock integration test failed"
    exit 1
fi