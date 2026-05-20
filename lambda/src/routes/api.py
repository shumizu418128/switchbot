"""API Gateway HTTP イベントのハンドラー。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api_http import http_response, normalize_path, parse_json_body
from botocore.exceptions import ClientError
from models import ApiGatewayEvent
from switchbot_service import update_home_presence_from_ssid

RouteFn = Callable[[dict[str, Any]], dict[str, Any]]


def _handle_wifi(body: dict[str, Any]) -> dict[str, Any]:
    """POST /wifi: SSID から在宅判定を更新する。"""
    ssid = body.get("ssid")

    if not isinstance(ssid, str) or not ssid.strip():
        return http_response(400, {"error": "ssid is required"})

    ssid = ssid.strip()
    try:
        at_home = update_home_presence_from_ssid(ssid)
    except ClientError as exc:
        return http_response(
            500, {"error": "failed to update home presence", "detail": str(exc)}
        )

    return http_response(200, {"ok": True, "at_home": at_home})


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
    route_fn = _match_route(method, path)

    if route_fn is None:
        return http_response(404, {"error": "not found"})

    body = parse_json_body(event)
    return route_fn(body)
