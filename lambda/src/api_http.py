"""API Gateway 向けの HTTP ユーティリティ。"""

from __future__ import annotations

import base64
import json
from typing import Any

from models import ApiGatewayEvent


def parse_json_body(event: ApiGatewayEvent) -> dict[str, Any]:
    """API Gateway イベントの body を dict にパースする。"""
    body = event.get("body")
    if event.get("isBase64Encoded") and isinstance(body, str):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            payload = {}
    elif isinstance(body, dict):
        payload = dict(body)
    else:
        payload = {}

    return payload if isinstance(payload, dict) else {}


def normalize_path(event: ApiGatewayEvent) -> str:
    """リクエストパスを正規化する（末尾スラッシュ除去）。"""
    path = event.get("path") or event.get("rawPath") or ""
    return path.rstrip("/")


def http_response(status_code: int, body: Any) -> dict[str, Any]:
    """API Gateway 向けの HTTP レスポンスを組み立てる。"""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
