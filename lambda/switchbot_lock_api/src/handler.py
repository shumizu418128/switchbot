"""Lambda Function URL 用ハンドラー。"""

from __future__ import annotations

import json
import os
from typing import Any

from .switchbot_client import (
    SwitchBotClient,
    SwitchBotDeviceStateError,
    SwitchBotError,
    ensure_locked,
)

REQUEST_API_KEY = os.environ.get("API_KEY", "").strip()


def _json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """API Gateway / Function URL 形式のレスポンスを返す。

    Args:
        status_code: HTTP ステータス。
        body: JSON シリアライズ可能な dict。

    Returns:
        Lambda の return 用 dict。
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _get_header(headers: dict[str, str] | None, name: str) -> str | None:
    """ヘッダーを大文字小文字を無視して取得する。

    Args:
        headers: イベントの headers。
        name: ヘッダー名（例: x-api-key）。

    Returns:
        値。無ければ None。
    """
    if not headers:
        return None
    lower = name.lower()
    for key, value in headers.items():
        if key.lower() == lower:
            return value
    return None


def _get_http_method(event: dict[str, Any]) -> str:
    """HTTP メソッドを取得する（HTTP API v2 / 旧 REST 両対応）。"""
    if "httpMethod" in event:
        return str(event.get("httpMethod") or "")
    http = event.get("requestContext", {}).get("http", {})
    return str(http.get("method") or "")


def _get_raw_path(event: dict[str, Any]) -> str:
    """リクエストパスを取得する。"""
    if "rawPath" in event:
        return str(event.get("rawPath") or "")
    path = event.get("path")
    if path is not None:
        return str(path)
    http = event.get("requestContext", {}).get("http", {})
    return str(http.get("path") or "")


def _is_lock_path(path: str) -> bool:
    """施錠エンドポイントとして許可するパスか。

    Function URL のルート ``/`` も許可する。
    """
    p = path.rstrip("/") or "/"
    if p == "/lock":
        return True
    if p == "" or p == "/":
        return True
    return False


def _verify_api_key(event: dict[str, Any], expected: str) -> bool:
    """x-api-key を検証する。"""
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return False
    key = _get_header(headers, "x-api-key")
    if not key or not expected:
        return False
    return key == expected


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """施錠 API（POST /lock 相当）。

    Args:
        event: Lambda / Function URL イベント。
        context: Lambda コンテキスト（未使用）。

    Returns:
        HTTP レスポンス dict。
    """
    try:
        if not REQUEST_API_KEY:
            return _json_response(
                500,
                {
                    "error": "configuration_error",
                    "message": "APIキーが設定されていません",
                },
            )

        if not _verify_api_key(event, REQUEST_API_KEY):
            return _json_response(
                401, {"error": "unauthorized", "message": "無効な API キーです"}
            )

        method = _get_http_method(event).upper()
        if method != "POST":
            return _json_response(
                405,
                {"error": "method_not_allowed", "message": "POST のみ許可されています"},
            )

        path = _get_raw_path(event)
        if not _is_lock_path(path):
            return _json_response(
                404, {"error": "not_found", "message": "パスが見つかりません"}
            )

        try:
            client = SwitchBotClient.from_env()
        except ValueError as e:
            return _json_response(
                500,
                {"error": "configuration_error", "message": str(e)},
            )

        try:
            result = ensure_locked(client)
        except SwitchBotDeviceStateError as e:
            return _json_response(
                502,
                {
                    "error": "device_state",
                    "message": str(e),
                },
            )
        except SwitchBotError as e:
            return _json_response(
                502,
                {
                    "error": "switchbot_error",
                    "message": str(e),
                    "detail": {
                        "http_status": e.http_status,
                        "api_status_code": e.api_status_code,
                    },
                },
            )

        return _json_response(
            200,
            {"ok": True, "result": result},
        )
    except Exception:
        # 詳細は CloudWatch Logs を参照
        return _json_response(
            500,
            {
                "error": "internal_error",
                "message": "内部エラーが発生しました",
            },
        )
