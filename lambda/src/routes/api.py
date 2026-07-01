"""API Gateway HTTP イベントのハンドラー。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api_http import (
    get_raw_body,
    get_request_header,
    http_response,
    normalize_path,
    parse_json_body,
    parse_slack_interaction_payload,
    slack_interaction_response,
)
from botocore.exceptions import ClientError
from models import ApiGatewayEvent
from slack_verify import verify_slack_signature
from switchbot_client import SwitchBotError
from switchbot_service import (
    WIFI_EVENT_CONNECTED,
    WIFI_EVENT_DISCONNECTED,
    lock_smart_lock,
    update_home_presence_from_ssid,
)

RouteFn = Callable[[dict[str, Any]], dict[str, Any]]


def _handle_wifi(body: dict[str, Any]) -> dict[str, Any]:
    """POST /wifi: クライアント Webhook イベントから在宅判定を更新する。"""
    event = body.get("event")

    if event not in (WIFI_EVENT_CONNECTED, WIFI_EVENT_DISCONNECTED):
        return http_response(
            400,
            {
                "error": "event is required",
                "expected": [WIFI_EVENT_CONNECTED, WIFI_EVENT_DISCONNECTED],
            },
        )

    ssid: str | None = None
    if event == WIFI_EVENT_CONNECTED:
        raw_ssid = body.get("ssid")
        if not isinstance(raw_ssid, str) or not raw_ssid.strip():
            return http_response(400, {"error": "ssid is required for wifi_connected"})
        ssid = raw_ssid.strip()

    try:
        at_home = update_home_presence_from_ssid(event, ssid)
    except ClientError as exc:
        return http_response(
            500, {"error": "failed to update home presence", "detail": str(exc)}
        )

    return http_response(200, {"ok": True, "at_home": at_home})


def _handle_slack_interactions(event: ApiGatewayEvent) -> dict[str, Any]:
    """POST /slack/interactions: Slack ボタン押下でスマートロックを施錠する。"""
    raw_body = get_raw_body(event)
    timestamp = get_request_header(event, "X-Slack-Request-Timestamp")
    signature = get_request_header(event, "X-Slack-Signature")

    if not verify_slack_signature(timestamp, signature, raw_body):
        return http_response(401, {"error": "invalid slack signature"})

    payload = parse_slack_interaction_payload(raw_body)
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return slack_interaction_response("操作を認識できませんでした。")

    action = actions[0]
    if not isinstance(action, dict) or action.get("action_id") != "lock_door":
        return slack_interaction_response("未対応の操作です。")

    try:
        lock_smart_lock()
    except SwitchBotError as exc:
        return slack_interaction_response(f"施錠に失敗しました: {exc}")

    return slack_interaction_response("鍵を閉めました。")


API_ROUTES: dict[tuple[str, str], RouteFn] = {
    ("POST", "/wifi"): _handle_wifi,
}


def _match_route(method: str, path: str) -> RouteFn | None:
    """HTTP メソッドとパスに対応するルート関数を返す（ステージ付きパスにも対応）。"""
    for (route_method, route_path), route_fn in API_ROUTES.items():
        if method != route_method:
            continue
        if path == route_path or path.endswith(route_path):
            return route_fn
    return None


def handle_api(event: ApiGatewayEvent) -> dict[str, Any]:
    """HTTP パスで API ルートをディスパッチする。

    ``router.dispatch`` から呼び出される API Gateway 専用エントリ。

    Args:
        event: API Gateway プロキシ統合イベント。

    Returns:
        API Gateway 形式のレスポンス dict。
    """
    method = (event.get("httpMethod") or "").upper()
    path = normalize_path(event)

    if method == "POST" and path.endswith("/slack/interactions"):
        return _handle_slack_interactions(event)

    route_fn = _match_route(method, path)

    if route_fn is None:
        return http_response(404, {"error": "not found"})

    body = parse_json_body(event)
    return route_fn(body)
