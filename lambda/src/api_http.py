"""API Gateway 向けの HTTP ユーティリティ。"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

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


def get_raw_body(event: ApiGatewayEvent) -> str:
    """API Gateway イベントの body をデコード済み文字列で返す。"""
    body = event.get("body")
    if event.get("isBase64Encoded") and isinstance(body, str):
        return base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        return body
    return ""


def parse_slack_interaction_payload(raw_body: str) -> dict[str, Any]:
    """Slack Interactivity の form body から ``payload`` JSON を取り出す。

    Args:
        raw_body: ``application/x-www-form-urlencoded`` の body 文字列。

    Returns:
        パース済み payload dict。失敗時は空 dict。
    """
    parsed = parse_qs(raw_body, keep_blank_values=True)
    payload_values = parsed.get("payload", [])
    if not payload_values:
        return {}

    try:
        data = json.loads(payload_values[0])
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def get_request_header(event: ApiGatewayEvent, name: str) -> str:
    """API Gateway イベントからヘッダー値を取得する（大文字小文字を無視）。"""
    headers = event.get("headers") or {}
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered and isinstance(value, str):
            return value
    return ""


def http_response(status_code: int, body: Any) -> dict[str, Any]:
    """API Gateway 向けの HTTP レスポンスを組み立てる。"""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def slack_interaction_response(
    text: str,
    *,
    response_type: str = "ephemeral",
) -> dict[str, Any]:
    """Slack Interactivity 用の即時レスポンスを組み立てる。

    Args:
        text: ユーザーに表示するメッセージ。
        response_type: ``ephemeral`` または ``in_channel``。

    Returns:
        API Gateway 形式のレスポンス dict。
    """
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {"response_type": response_type, "text": text},
            ensure_ascii=False,
        ),
    }
