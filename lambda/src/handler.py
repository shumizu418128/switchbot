import json
import os

from switchbot_client import lock


def validate_request(api_key: str):
    """リクエストを検証する。"""
    if api_key != os.environ.get("API_KEY"):
        return False
    return True


def lambda_handler(event, context):
    """Lambda 関数のハンドラー。"""
    # API キーを検証
    if not validate_request(event.get("headers", {}).get("x-api-key")):
        return {"statusCode": 401, "body": json.dumps({"error": "Unauthorized"})}

    # デバイス ID を取得
    device_id = event.get("device_id")
    if not device_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Device ID is required"}),
        }

    # 施錠コマンドを送信
    if event.get("action") == "lock":
        return lock(device_id)

    return {"statusCode": 400, "body": json.dumps({"error": "Invalid action"})}
