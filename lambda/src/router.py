"""Lambda イベントの種別判定とディスパッチ。"""

from __future__ import annotations

from typing import Any

from api_http import http_response
from models import LambdaContext, LambdaEvent
from routes.api import handle_api
from routes.schedule import handle_scheduled


def dispatch(event: LambdaEvent, context: LambdaContext) -> Any:
    """
    AWS Lambdaからのリクエストは、まずここに到達する。
    イベント種別に応じて適切なハンドラーへ振り分ける。

    Args:
        event: Lambda に渡された生イベント。
        context: Lambda ランタイムコンテキスト。

    Returns:
        スケジュールタスクの戻り値、または API Gateway レスポンス dict。
    """
    del context  # 現時点では未使用

    if _is_api_gateway_event(event):
        return handle_api(event)

    if _is_scheduled_event(event):
        return handle_scheduled(event)

    return http_response(400, {"error": "unsupported event"})


def _is_api_gateway_event(event: LambdaEvent) -> bool:
    """API Gateway プロキシ統合イベントかどうか。"""
    return bool(
        event.get("requestContext")
        or event.get("httpMethod")
        or event.get("path")
        or event.get("rawPath")
    )


def _is_scheduled_event(event: LambdaEvent) -> bool:
    """EventBridge スケジュールイベントかどうか。"""
    return bool(event.get("action"))
