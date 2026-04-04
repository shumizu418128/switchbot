"""SwitchBot OpenAPI v1.1 クライアント（署名付き HTTP）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Literal

# Lambda JSON ボディで返す結果コード
ResultCode = Literal["locked", "already_locked"]

# SwitchBot API の成功コード（公式ドキュメント）
SWITCHBOT_API_SUCCESS_STATUS = 100

TOKEN = os.environ.get("SWITCHBOT_TOKEN", "").strip()
SECRET = os.environ.get("SWITCHBOT_SECRET", "").strip()

API_BASE_URL = os.environ.get(
    "SWITCHBOT_API_BASE_URL", "https://api.switch-bot.com"
).strip()


class SwitchBotError(Exception):
    """SwitchBot API 呼び出しに関するエラー（ネットワーク・HTTP・API 応答）。"""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        api_status_code: int | None = None,
        response_body: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.api_status_code = api_status_code
        self.response_body = response_body


def _build_sign(token: str, t_ms: int, nonce: str, secret: str) -> str:
    """HMAC-SHA256 署名を Base64 文字列で返す。

    Args:
        token: Open Token。
        t_ms: ミリ秒の UNIX 時刻。
        nonce: リクエストごとの一意な文字列。
        secret: Secret Key。

    Returns:
        Base64 エンコードされた署名。
    """
    string_to_sign = f"{token}{t_ms}{nonce}".encode("utf-8")
    key = secret.encode("utf-8")
    digest = hmac.new(key, msg=string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _auth_headers() -> dict[str, str]:
    """認証ヘッダーを生成する。"""
    t_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    sign = _build_sign(TOKEN, t_ms, nonce, SECRET)
    return {
        "Authorization": TOKEN,
        "Content-Type": "application/json",
        "charset": "utf8",
        "t": str(t_ms),
        "sign": sign,
        "nonce": nonce,
    }


def _request_json(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON API を呼び出し、JSON オブジェクトを返す。

    Args:
        method: HTTP メソッド。
        path: 先頭スラッシュ付きパス（例: /v1.1/devices/xxx/status）。
        body: POST 時の JSON ボディ。

    Returns:
        パース済み JSON オブジェクト。

    Raises:
        SwitchBotError: HTTP エラーまたは不正な応答の場合。
    """
    url = API_BASE_URL.rstrip("/") + path
    data: bytes | None = None
    headers = _auth_headers()
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            http_status = resp.status
    except urllib.error.HTTPError as e:
        try:
            err_raw = e.read().decode("utf-8")
            err_json = json.loads(err_raw) if err_raw else None
        except json.JSONDecodeError:
            err_json = err_raw
        raise SwitchBotError(
            f"SwitchBot HTTP エラー: {e.code}",
            http_status=e.code,
            response_body=err_json,
        ) from e
    except urllib.error.URLError as e:
        raise SwitchBotError(f"SwitchBot 接続エラー: {e.reason}") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SwitchBotError(
            f"JSON 解析に失敗しました: {raw[:200]}", http_status=http_status
        ) from e

    api_code = payload.get("statusCode")
    if api_code != SWITCHBOT_API_SUCCESS_STATUS:
        raise SwitchBotError(
            payload.get("message", "SwitchBot API エラー"),
            http_status=http_status,
            api_status_code=api_code if isinstance(api_code, int) else None,
            response_body=payload,
        )
    return payload


def lock(device_id: str) -> dict[str, Any]:
    """施錠コマンドを送信する。

    Args:
        device_id: 対象スマートロックのデバイス ID。

    Returns:
        API 応答全体。
    """
    path = f"/v1.1/devices/{device_id}/commands"
    body = {
        "commandType": "command",
        "command": "lock",
        "parameter": "default",
    }
    return _request_json("POST", path, body)
