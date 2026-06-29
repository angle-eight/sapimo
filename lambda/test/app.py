#!/usr/bin/env python3
"""
テスト用 Lambda 関数
"""

import json
import os


def lambda_handler(event, context):
    """
    テスト用のLambda関数ハンドラ
    """

    # 環境変数の確認
    bucket_name = os.environ.get("BUCKET_NAME", "default-bucket")
    table_name = os.environ.get("TABLE_NAME", "default-table")

    # リクエスト情報の抽出
    method = event.get("requestContext", {}).get("http", {}).get("method", "UNKNOWN")
    path = event.get("rawPath", "/unknown")
    query_params = event.get("queryStringParameters", {})

    response_body = {
        "message": "Hello from Lambda!",
        "method": method,
        "path": path,
        "query_parameters": query_params,
        "environment": {"bucket_name": bucket_name, "table_name": table_name},
        "event_info": {
            "version": event.get("version"),
            "route_key": event.get("routeKey"),
            "request_id": event.get("requestContext", {}).get("requestId"),
        },
    }

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(response_body, indent=2),
    }


if __name__ == "__main__":
    # ローカルテスト用
    test_event = {
        "version": "2.0",
        "routeKey": "GET /test",
        "rawPath": "/test",
        "queryStringParameters": {"param1": "value1"},
        "requestContext": {"http": {"method": "GET"}, "requestId": "test-request-123"},
    }

    result = lambda_handler(test_event, {})
    print(json.dumps(result, indent=2))
