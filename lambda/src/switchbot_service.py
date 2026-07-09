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
HOME_WIFI_SSID = os.environ.get("HOME_WIFI_SSID", "").strip()
HUMIDITY_HISTORY_PARAM = os.environ.get("HUMIDITY_HISTORY_PARAM", "").strip()
HUMIDITY_CHECK_INTERVAL_SECONDS = 3600
HUMIDITY_WINDOW_SECONDS = 3600
LOCK_ALERT_DELAY_SECONDS = 300
LIGHT_SETTLE_SECONDS = 8
LIGHT_ACTION_MAX_ATTEMPTS = 5

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


def _get_hub2_light_level() -> int:
    """Hub 2 の ambient lightLevel（1〜20）を取得する。

    Returns:
        Hub 2 の ``lightLevel``。

    Raises:
        ValueError: ``lightLevel`` が応答に含まれない場合。
    """
    path = f"/v1.1/devices/{DeviceId.HUB2}/status"
    response = request_json("GET", path)
    light_level = response.get("body", {}).get("lightLevel")
    if light_level is None:
        raise ValueError(f"Hub 2 lightLevel が取得できません: {response}")
    return int(light_level)


def _send_light_command(command_type: str, command: str) -> None:
    """ライト（IR）へコマンドを1回送る。"""
    path = f"/v1.1/devices/{DeviceId.LIGHT}/commands"
    request_json(
        "POST",
        path,
        {
            "commandType": command_type,
            "command": command,
            "parameter": "default",
        },
    )


def _measure_light_change(
    before_level: int,
    *,
    command_type: str,
    command: str,
    phase: str,
    attempt: int,
    expect_increase: bool,
) -> tuple[int, bool]:
    """コマンド1回の直前・直後の lightLevel を比較する。

    Args:
        before_level: コマンド送信前の明るさ。
        command_type: SwitchBot の ``commandType``。
        command: 送信するコマンド名。
        phase: ログ用フェーズ名（``on`` / ``off``）。
        attempt: 試行回数。
        expect_increase: ``True`` なら明るくなることを期待。

    Returns:
        ``(after_level, changed_as_expected)``。``after_level`` は新規計測値。
    """
    _send_light_command(command_type, command)
    time.sleep(LIGHT_SETTLE_SECONDS)
    after_level = _get_hub2_light_level()
    diff = after_level - before_level
    changed = diff > 0 if expect_increase else diff < 0
    print(
        f"_measure_light_change: phase={phase} attempt={attempt} "
        f"before={before_level} after={after_level} diff={diff} "
        f"changed={changed}",
        flush=True,
    )
    return after_level, changed


def _retry_until_light_change(
    start_level: int,
    *,
    command_type: str,
    command: str,
    phase: str,
    expect_increase: bool,
) -> tuple[int, bool]:
    """同一コマンドを再送し、直前・直後の差分が出るまで試行する。

    計測値は ``start_level`` → ``after_1`` → ``after_2`` … と連鎖する。
    各試行で ``before_level`` を引数として渡し、次試行には ``int(after_level)`` を渡す。

    Returns:
        ``(last_level, changed_as_expected)``
    """
    before_level = int(start_level)
    last_level = before_level

    for attempt in range(1, LIGHT_ACTION_MAX_ATTEMPTS + 1):
        after_level, changed = _measure_light_change(
            before_level,
            command_type=command_type,
            command=command,
            phase=phase,
            attempt=attempt,
            expect_increase=expect_increase,
        )
        last_level = after_level
        if changed:
            return last_level, True

        print(
            f"_retry_until_light_change: phase={phase} "
            f"attempt={attempt} 差分なしのため再送します",
            flush=True,
        )
        before_level = int(after_level)

    return last_level, False


def _ensure_light_off() -> bool:
    """コマンド1回ごとに直前・直後の lightLevel を比較し、オフを保証する。

    計測値を連鎖させる（1と2、2と3、…）。
    - 「全灯」: 直後 > 直前 なら点灯成功
    - ``turnOn``（トグル）: 直後 < 直前 なら消灯成功

    絶対値は使わず差分のみ。失敗時は同じコマンドを再送する。

    Returns:
        消灯を確認できたら ``True``、できなければ ``False``。
    """
    initial_level = _get_hub2_light_level()
    print(f"_ensure_light_off: initial={initial_level}", flush=True)

    level_after_on, _ = _retry_until_light_change(
        initial_level,
        command_type="customize",
        command="全灯",
        phase="on",
        expect_increase=True,
    )

    _, off_confirmed = _retry_until_light_change(
        level_after_on,
        command_type="command",
        command="turnOn",
        phase="off",
        expect_increase=False,
    )
    return off_confirmed


def on_arrived_home() -> None:
    """在宅状態が false から true に変化したときに呼ばれる。"""
    print("on_arrived_home: test message", flush=True)


def on_left_home() -> None:
    """在宅状態が true から false に変化したときに呼ばれる。"""
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

    try:
        if not _ensure_light_off():
            print("_ensure_light_off: failed to confirm light off", flush=True)
            _send_slack_alert(
                "<@U099ANR7PL7> :rotating_light: *警告: 外出時のライト消灯を確認できませんでした*"
            )
    except SwitchBotError as exc:
        print(f"on_left_home: ライト消灯に失敗: {exc}", flush=True)
        _send_slack_alert(
            f"<@U099ANR7PL7> :rotating_light: *警告: 外出時のライト操作で API エラー*\n`{exc}`"
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
# MARK: - Humidity history
#####################################
def _get_humidity_history() -> list[dict[str, Any]]:
    """SSM Parameter Store から湿度履歴を取得する。

    Returns:
        ``{"value": float, "timestamp": int}`` のリスト。取得失敗時は空リスト。
    """
    if not HUMIDITY_HISTORY_PARAM:
        return []

    try:
        result = ssm_client.get_parameter(
            Name=HUMIDITY_HISTORY_PARAM, WithDecryption=True
        )
        raw_value = result.get("Parameter", {}).get("Value", "[]")
        history = json.loads(raw_value)
        if isinstance(history, list):
            return history
    except ssm_client.exceptions.ParameterNotFound:
        return []
    except (ClientError, json.JSONDecodeError):
        return []

    return []


def _put_humidity_history(history: list[dict[str, Any]]) -> None:
    """SSM Parameter Store に湿度履歴を保存する。

    Args:
        history: ``{"value": float, "timestamp": int}`` のリスト。
    """
    if not HUMIDITY_HISTORY_PARAM:
        return

    value = json.dumps(history)
    ssm_client.put_parameter(
        Name=HUMIDITY_HISTORY_PARAM, Value=value, Type="String", Overwrite=True
    )


def _update_humidity_history(current_value: float) -> float:
    """湿度履歴に新しい値を追加し、1時間以内の平均値を返す。

    古いエントリ（HUMIDITY_WINDOW_SECONDS 超過）を自動で削除してから保存する。

    Args:
        current_value: 現在の湿度（%）。

    Returns:
        過去1時間の湿度平均値（%）。
    """
    history = _get_humidity_history()

    now = int(time.time())
    cutoff = now - HUMIDITY_WINDOW_SECONDS

    history = [entry for entry in history if entry.get("timestamp", 0) >= cutoff]
    history.append({"value": current_value, "timestamp": now})

    _put_humidity_history(history)

    values = [entry["value"] for entry in history]
    return sum(values) / len(values)


def _should_run_humidity_check() -> bool:
    """前回の湿度チェックから HUMIDITY_CHECK_INTERVAL_SECONDS 以上経過しているか。

    Returns:
        湿度チェックを実行すべきなら ``True``。履歴が空のときは初回として ``True``。
    """
    history = _get_humidity_history()
    if not history:
        return True

    last_ts = max(entry.get("timestamp", 0) for entry in history)
    return time.time() - last_ts >= HUMIDITY_CHECK_INTERVAL_SECONDS


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
            "humidity_alert_active": False,
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
            "humidity_alert_active": False,
        }
    except (ClientError, json.JSONDecodeError):
        return {
            "alert_active": False,
            "last_alert_type": None,
            "updated_at": None,
            "humidity_alert_active": False,
        }

    humidity_alert_active = state.get("humidity_alert_active")
    if humidity_alert_active is None:
        humidity_alert_active = state.get("last_humidity_alert_at") is not None

    return {
        "alert_active": bool(state.get("alert_active", False)),
        "last_alert_type": state.get("last_alert_type"),
        "updated_at": state.get("updated_at"),
        "humidity_alert_active": bool(humidity_alert_active),
    }


def _put_alert_state(
    alert_active: bool,
    alert_type: str | None,
    *,
    humidity_alert_active: bool | None = None,
) -> None:
    """SSM Parameter Store に通知状態を保存する。"""
    if not ALERT_STATE_PARAM:
        return

    current = _get_alert_state()
    value = json.dumps(
        {
            "alert_active": alert_active,
            "last_alert_type": alert_type,
            "updated_at": int(time.time()),
            "humidity_alert_active": (
                humidity_alert_active
                if humidity_alert_active is not None
                else current.get("humidity_alert_active", False)
            ),
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
    humidity_min_threshold = 40
    humidity_max_threshold = 60

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

    if not _should_run_humidity_check():
        return

    state = _get_alert_state()
    avg_humidity = _update_humidity_history(humidity)
    humidity_alert_active = bool(state.get("humidity_alert_active", False))
    out_of_range = (
        avg_humidity <= humidity_min_threshold or avg_humidity >= humidity_max_threshold
    )
    in_normal_range = humidity_min_threshold < avg_humidity < humidity_max_threshold

    if out_of_range and not humidity_alert_active:
        avg_humidity_rounded = round(avg_humidity, 1)
        status = (
            f"\n`{co2} ppm`\n`{temperature} ℃`\n"
            f"`{avg_humidity_rounded} %（1時間平均）`\n`battery: {battery} %`"
        )
        if avg_humidity <= humidity_min_threshold:
            alert_text = f"<@U099ANR7PL7> :rotating_light: *警告: 湿度が{humidity_min_threshold}%未満です*{status}"
        else:
            alert_text = f"<@U099ANR7PL7> :rotating_light: *警告: 湿度が{humidity_max_threshold}%を超えました*{status}"
        _send_slack_alert(alert_text)
        _put_alert_state(
            bool(state.get("alert_active")),
            state.get("last_alert_type"),
            humidity_alert_active=True,
        )
    elif in_normal_range and humidity_alert_active:
        _put_alert_state(
            bool(state.get("alert_active")),
            state.get("last_alert_type"),
            humidity_alert_active=False,
        )


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
