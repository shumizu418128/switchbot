"""SwitchBot API を使った業務処理（センサー監視・在宅判定）。"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any

import boto3
from botocore.exceptions import ClientError
from models import DeviceId
from switchbot_client import SwitchBotError, request_json

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
ALERT_STATE_PARAM = os.environ.get("ALERT_STATE_PARAM", "").strip()
LOCK_ALERT_STATE_PARAM = os.environ.get("LOCK_ALERT_STATE_PARAM", "").strip()
WIFI_STATE_PARAM = os.environ.get("WIFI_STATE_PARAM", "").strip()
LOCK_ALERT_DELAY_SECONDS = 300
LIGHT_OFF_TIMER_COMMAND = "30分切り"
LIGHT_OFF_TIMER_SEND_COUNT = 3
LIGHT_OFF_TIMER_SEND_GAP_SECONDS = 8

ssm_client = boto3.client("ssm")


#####################################
# MARK: - Bluetooth
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
    print("on_arrived_home", flush=True)
    try:
        _send_slack_alert("帰宅を検知しました")
    except Exception as exc:
        print(f"on_arrived_home: Slack送信失敗: {exc}", flush=True)


def _send_light_off_timer() -> None:
    """外出時にライトの30分切タイマーを送信する（反映漏れ対策で複数回送る）。"""
    path = f"/v1.1/devices/{DeviceId.LIGHT}/commands"
    body = {
        "commandType": "customize",
        "command": LIGHT_OFF_TIMER_COMMAND,
        "parameter": "default",
    }
    failures: list[str] = []

    for attempt in range(1, LIGHT_OFF_TIMER_SEND_COUNT + 1):
        try:
            request_json("POST", path, body)
            print(
                f"on_left_home: ライト30分切送信 {attempt}/{LIGHT_OFF_TIMER_SEND_COUNT}",
                flush=True,
            )
        except SwitchBotError as exc:
            msg = f"{attempt}回目: {exc}"
            failures.append(msg)
            print(f"on_left_home: ライト30分切送信失敗 {msg}", flush=True)

        if attempt < LIGHT_OFF_TIMER_SEND_COUNT:
            time.sleep(LIGHT_OFF_TIMER_SEND_GAP_SECONDS)

    if len(failures) == LIGHT_OFF_TIMER_SEND_COUNT:
        detail = "\n".join(f"`{item}`" for item in failures)
        _send_slack_alert(
            f"<@U099ANR7PL7> :rotating_light: *警告: 外出時のライト30分切送信に失敗しました*\n{detail}"
        )


def on_left_home() -> None:
    """在宅状態が true から false に変化したときに呼ばれる。"""
    try:
        _send_slack_alert("外出を検知しました")
    except Exception as exc:
        print(f"on_left_home: Slack送信失敗: {exc}", flush=True)

    try:
        path = f"/v1.1/devices/{DeviceId.AIR_CONDITIONER}/commands"
        request_json(
            "POST",
            path,
            {
                "commandType": "command",
                "command": "turnOff",
                "parameter": "default",
            },
        )
    except SwitchBotError as exc:
        print(f"on_left_home: エアコン停止に失敗: {exc}", flush=True)
        _send_slack_alert(
            f"<@U099ANR7PL7> :rotating_light: *警告: 外出時のエアコン停止に失敗しました*\n`{exc}`"
        )

    _send_light_off_timer()

    print("on_left_home done", flush=True)


WIFI_EVENT_CONNECTED = "connected"
WIFI_EVENT_DISCONNECTED = "disconnected"


def update_home_presence_from_event(event: str) -> bool:
    """Pico WH Webhook イベントから在宅判定し、変化時のみ処理して保存する。

    CO2 監視と同様、SSM の以前の状態を読んでから現在の在宅かどうかを決める。

    Args:
        event: ``connected`` または ``disconnected``。

    Returns:
        現在の在宅判定。
    """
    state = _get_home_presence_state()
    was_at_home = bool(state.get("at_home", False))
    at_home = event == WIFI_EVENT_CONNECTED

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
        return {
            "alert_active": False,
            "last_alert_type": None,
            "updated_at": None,
        }

    try:
        result = ssm_client.get_parameter(Name=ALERT_STATE_PARAM, WithDecryption=True)
        raw_value = result.get("Parameter", {}).get("Value", "{}")
        state = json.loads(raw_value)
    except ssm_client.exceptions.ParameterNotFound:
        return {
            "alert_active": False,
            "last_alert_type": None,
            "updated_at": None,
        }
    except (ClientError, json.JSONDecodeError):
        return {
            "alert_active": False,
            "last_alert_type": None,
            "updated_at": None,
        }

    return {
        "alert_active": bool(state.get("alert_active", False)),
        "last_alert_type": state.get("last_alert_type"),
        "updated_at": state.get("updated_at"),
    }


def _put_alert_state(
    alert_active: bool,
    alert_type: str | None,
) -> None:
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


def _send_slack_alert(text: str) -> None:
    """Slack Incoming Webhook に通知を送る。

    Args:
        text: 送信するメッセージ本文。
    """
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req):
        pass


def co2_check() -> None:
    """CO2濃度をチェックする。"""
    path = f"/v1.1/devices/{DeviceId.CO2}/status"

    response = request_json("GET", path)
    body = response.get("body", {})
    co2 = body.get("CO2")
    temperature = body.get("temperature")
    humidity = body.get("humidity")
    battery = body.get("battery")

    co2_threshold = 1000

    state = _get_alert_state()
    was_alert_active = bool(state.get("alert_active", False))

    if co2 >= co2_threshold and not was_alert_active:
        status = (
            f"\n`{co2} ppm`\n`{temperature} ℃`\n`{humidity} %`\n`battery: {battery} %`"
        )
        _send_slack_alert(
            f"<@U099ANR7PL7> :rotating_light: *警告: CO2濃度が{co2_threshold}ppmを超えました*{status}"
        )
        _put_alert_state(True, "co2")

    if (
        co2 < co2_threshold
        and was_alert_active
        and state.get("last_alert_type") == "co2"
    ):
        _put_alert_state(False, None)


#####################################
# MARK: - Lock
#####################################
def _get_lock_alert_state() -> dict[str, Any]:
    """SSM Parameter Store から鍵通知状態を取得する。"""
    default = {"alert_active": False, "abnormal_since": None, "updated_at": None}
    if not LOCK_ALERT_STATE_PARAM:
        return default

    try:
        result = ssm_client.get_parameter(
            Name=LOCK_ALERT_STATE_PARAM, WithDecryption=True
        )
        raw_value = result.get("Parameter", {}).get("Value", "{}")
        state = json.loads(raw_value)
    except ssm_client.exceptions.ParameterNotFound:
        return default
    except (ClientError, json.JSONDecodeError):
        return default

    return {
        "alert_active": bool(state.get("alert_active", False)),
        "abnormal_since": state.get("abnormal_since"),
        "updated_at": state.get("updated_at"),
    }


def _put_lock_alert_state(
    *,
    alert_active: bool,
    abnormal_since: int | None,
) -> None:
    """SSM Parameter Store に鍵通知状態を保存する。"""
    if not LOCK_ALERT_STATE_PARAM:
        return

    value = json.dumps(
        {
            "alert_active": alert_active,
            "abnormal_since": abnormal_since,
            "updated_at": int(time.time()),
        }
    )
    ssm_client.put_parameter(
        Name=LOCK_ALERT_STATE_PARAM, Value=value, Type="SecureString", Overwrite=True
    )


def _send_lock_slack_alert(
    lock_state: Any,
    door_state: Any,
    battery: Any,
) -> None:
    """鍵異常通知を Block Kit 付きで Slack Incoming Webhook に送る。

    Args:
        lock_state: SwitchBot の ``lockState``。
        door_state: SwitchBot の ``doorState``。
        battery: バッテリー残量（%）。
    """
    status_parts = [f"`lockState: {lock_state}`"]
    if door_state is not None:
        status_parts.append(f"`doorState: {door_state}`")
    if battery is not None:
        status_parts.append(f"`battery: {battery} %`")
    status = "\n".join(status_parts)

    text = (
        f"<@U099ANR7PL7> :rotating_light: :door: :warning: "
        f"*警告: ドアが5分以上開いています* :lock:\n{status}"
    )
    slack_message = {
        "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "鍵を閉める"},
                        "action_id": "lock_door",
                        "style": "primary",
                    }
                ],
            },
        ],
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


def lock_smart_lock() -> None:
    """スマートロックを施錠する。

    Raises:
        SwitchBotError: API 呼び出しに失敗した場合。
    """
    path = f"/v1.1/devices/{DeviceId.SMART_LOCK}/commands"
    request_json(
        "POST",
        path,
        {
            "commandType": "command",
            "command": "lock",
            "parameter": "default",
        },
    )


def notify_lock_closed() -> None:
    """Slack の施錠ボタン成功後に、メンションなしで施錠完了を通知する。"""
    try:
        _send_slack_alert("鍵を閉めました")
    except Exception as exc:
        print(f"notify_lock_closed: Slack送信失敗: {exc}", flush=True)


def lock_check() -> None:
    """スマートロックの解錠・ドア開状態をチェックし、5分以上継続時に Slack へ通知する。"""
    path = f"/v1.1/devices/{DeviceId.SMART_LOCK}/status"

    response = request_json("GET", path)
    body = response.get("body", {})
    lock_state = body.get("lockState")
    door_state = body.get("doorState")
    battery = body.get("battery")

    is_unlocked = lock_state == "unlocked"
    is_door_closed = door_state == "closed"
    should_alert = is_unlocked or not is_door_closed

    now = int(time.time())
    state = _get_lock_alert_state()
    was_alert_active = bool(state.get("alert_active", False))
    abnormal_since = state.get("abnormal_since")

    if not should_alert:
        if was_alert_active or abnormal_since is not None:
            _put_lock_alert_state(alert_active=False, abnormal_since=None)
        return

    if abnormal_since is None:
        _put_lock_alert_state(alert_active=False, abnormal_since=now)
        return

    elapsed = now - int(abnormal_since)
    if elapsed >= LOCK_ALERT_DELAY_SECONDS and not was_alert_active:
        _send_lock_slack_alert(lock_state, door_state, battery)
        _put_lock_alert_state(alert_active=True, abnormal_since=int(abnormal_since))


if __name__ == "__main__":
    # テスト
    on_left_home()
