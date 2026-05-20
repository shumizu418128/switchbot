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
from typing import Any

import boto3
from botocore.exceptions import ClientError

if os.getenv("ENV") == "local":
    from dotenv import load_dotenv

    load_dotenv()

# SwitchBot API の成功コード（公式ドキュメント）
SWITCHBOT_API_SUCCESS_STATUS = 100

TOKEN = os.environ.get("TOKEN", "").strip()
SECRET = os.environ.get("CLIENT_SECRET", "").strip()
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
ALERT_STATE_PARAM = os.environ.get("ALERT_STATE_PARAM", "").strip()
WIFI_STATE_PARAM = os.environ.get("WIFI_STATE_PARAM", "").strip()
HOME_WIFI_SSID = os.environ.get("HOME_WIFI_SSID", "").strip()

API_BASE_URL = os.environ.get(
    "SWITCHBOT_API_BASE_URL", "https://api.switch-bot.com"
).strip()
ssm_client = boto3.client("ssm")


def _get_alert_state() -> dict[str, Any]:
    """SSM Parameter Store から通知状態を取得する。"""
    if not ALERT_STATE_PARAM:
        return {"alert_active": False, "last_alert_type": None, "updated_at": None}

    try:
        result = ssm_client.get_parameter(Name=ALERT_STATE_PARAM, WithDecryption=True)
        raw_value = result.get("Parameter", {}).get("Value", "{}")
        state = json.loads(raw_value)
    except ssm_client.exceptions.ParameterNotFound:
        return {"alert_active": False, "last_alert_type": None, "updated_at": None}
    except (ClientError, json.JSONDecodeError):
        return {"alert_active": False, "last_alert_type": None, "updated_at": None}

    return {
        "alert_active": bool(state.get("alert_active", False)),
        "last_alert_type": state.get("last_alert_type"),
        "updated_at": state.get("updated_at"),
    }


def _put_alert_state(alert_active: bool, alert_type: str | None) -> None:
    """SSM Parameter Store に通知状態を保存する。"""
    if not ALERT_STATE_PARAM:
        return

    value = json.dumps(
        {
            "alert_active": alert_active,
            "last_alert_type": alert_type,
            "updated_at": int(time.time()),
        }
    )
    ssm_client.put_parameter(
        Name=ALERT_STATE_PARAM, Value=value, Type="SecureString", Overwrite=True
    )


def _get_home_presence_state() -> dict[str, Any]:
    """SSM Parameter Store から在宅状態を取得する。"""
    if not WIFI_STATE_PARAM:
        return {"at_home": False, "updated_at": None}

    try:
        result = ssm_client.get_parameter(Name=WIFI_STATE_PARAM, WithDecryption=True)
        raw_value = result.get("Parameter", {}).get("Value", "{}")
        state = json.loads(raw_value)
    except ssm_client.exceptions.ParameterNotFound:
        return {"at_home": False, "updated_at": None}
    except (ClientError, json.JSONDecodeError):
        return {"at_home": False, "updated_at": None}

    return {
        "at_home": bool(state.get("at_home", False)),
        "updated_at": state.get("updated_at"),
    }


def _put_home_presence_state(at_home: bool) -> None:
    """SSM Parameter Store に在宅状態を保存する。"""
    if not WIFI_STATE_PARAM:
        return

    value = json.dumps({"at_home": at_home, "updated_at": int(time.time())})
    ssm_client.put_parameter(
        Name=WIFI_STATE_PARAM, Value=value, Type="SecureString", Overwrite=True
    )


def on_arrived_home() -> None:
    """在宅状態が false から true に変化したときに呼ばれる。"""
    pass


def on_left_home() -> None:
    """在宅状態が true から false に変化したときに呼ばれる。"""
    pass


def update_home_presence_from_ssid(ssid: str) -> bool:
    """受信 SSID と家 SSID を比較し、在宅状態の変化時に処理して保存する。

    Args:
        ssid: クライアントから報告された WiFi SSID。

    Returns:
        現在の在宅判定（家 SSID と一致すれば ``True``）。
    """
    at_home = bool(HOME_WIFI_SSID) and ssid == HOME_WIFI_SSID
    state = _get_home_presence_state()
    was_at_home = bool(state.get("at_home", False))

    if at_home and not was_at_home:
        on_arrived_home()
        _put_home_presence_state(True)
    elif not at_home and was_at_home:
        on_left_home()
        _put_home_presence_state(False)

    return at_home


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


def co2_check():
    """CO2濃度をチェックする。"""

    # CO2センサーのデバイス ID
    co2_device_id = "B0E9FEA40541"
    path = f"/v1.1/devices/{co2_device_id}/status"

    response = _request_json("GET", path)
    body = response.get("body", {})
    co2 = body.get("CO2")
    temperature = body.get("temperature")
    humidity = body.get("humidity")
    battery = body.get("battery")

    co2_threshold = 1000
    humidity_min_threshold = 40
    humidity_max_threshold = 60

    alert_type: str | None = None
    if co2 >= co2_threshold:
        alert_type = "co2"
    elif humidity <= humidity_min_threshold or humidity >= humidity_max_threshold:
        alert_type = "humidity"

    state = _get_alert_state()
    was_alert_active = bool(state.get("alert_active", False))

    if alert_type is not None and not was_alert_active:
        # Slackに送るメッセージ
        status = (
            f"\n`{co2} ppm`\n`{temperature} ℃`\n`{humidity} %`\n`battery: {battery} %`"
        )

        if alert_type == "co2":
            slack_message = {
                "text": f"<@U099ANR7PL7> :rotating_light: *警告: CO2濃度が{co2_threshold}ppmを超えました*{status}"
            }
        else:
            slack_message = {
                "text": f"<@U099ANR7PL7> :rotating_light: *警告: 湿度が{humidity_min_threshold}%～{humidity_max_threshold}%を超えました*{status}"
            }

        data = json.dumps(slack_message).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req):
            pass  # 成功時は何もしない

        _put_alert_state(True, alert_type)

    if alert_type is None and was_alert_active:
        _put_alert_state(False, None)
