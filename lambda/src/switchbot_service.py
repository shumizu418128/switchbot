"""SwitchBot API を使った業務処理（センサー監視・在宅判定）。"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

import boto3
from botocore.exceptions import ClientError
from switchbot_client import request_json

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
ALERT_STATE_PARAM = os.environ.get("ALERT_STATE_PARAM", "").strip()
WIFI_STATE_PARAM = os.environ.get("WIFI_STATE_PARAM", "").strip()
HOME_WIFI_SSID = os.environ.get("HOME_WIFI_SSID", "").strip()

ssm_client = boto3.client("ssm")


#####################################
# MARK: - Wifi
#####################################
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
    print("on_arrived_home: test message", flush=True)


def on_left_home() -> None:
    """在宅状態が true から false に変化したときに呼ばれる。"""
    light_device_id = "01-202604181058-79179070"
    path = f"/v1.1/devices/{light_device_id}/commands"
    request_json(
        "POST",
        path,
        {
            "commandType": "customize",
            "command": "保安灯",
            "parameter": "default",
        },
    )
    # 電源はトグルなので、turnOnとあるが電源が既に入っているときはオフになる
    request_json(
        "POST",
        path,
        {
            "commandType": "command",
            "command": "turnOn",
            "parameter": "default",
        },
    )
    print("on_left_home", flush=True)


WIFI_EVENT_CONNECTED = "wifi_connected"
WIFI_EVENT_DISCONNECTED = "wifi_disconnected"


def update_home_presence_from_ssid(event: str, ssid: str | None = None) -> bool:
    """Webhook イベントと SSID から在宅判定し、変化時のみ処理して保存する。

    CO2 監視と同様、SSM の以前の状態を読んでから現在の在宅かどうかを決める。

    Args:
        event: ``wifi_connected`` または ``wifi_disconnected``。
        ssid: 接続時の WiFi SSID（切断時は不要）。

    Returns:
        現在の在宅判定。
    """
    state = _get_home_presence_state()
    was_at_home = bool(state.get("at_home", False))

    if event == WIFI_EVENT_CONNECTED:
        at_home = bool(HOME_WIFI_SSID) and ssid == HOME_WIFI_SSID
    else:
        at_home = False

    if at_home and not was_at_home:
        on_arrived_home()
        _put_home_presence_state(True)

    if not at_home and was_at_home:
        on_left_home()
        _put_home_presence_state(False)

    return at_home


#####################################
# MARK: - CO2
#####################################
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


def co2_check() -> None:
    """CO2濃度をチェックする。"""
    co2_device_id = "B0E9FEA40541"
    path = f"/v1.1/devices/{co2_device_id}/status"

    response = request_json("GET", path)
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
            pass

        _put_alert_state(True, alert_type)

    if alert_type is None and was_alert_active:
        _put_alert_state(False, None)


if __name__ == "__main__":
    # テスト
    on_arrived_home()
