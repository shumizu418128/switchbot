"""switchbot_client のロジックテスト（ネットワークなし）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from src.switchbot_client import (
    SwitchBotDeviceStateError,
    ensure_locked,
    is_locked_state,
    parse_lock_state,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("lock", True),
        ("LOCK", True),
        ("locked", True),
        ("unlock", False),
        ("", False),
        (None, False),
    ],
)
def test_is_locked_state(raw: str | None, expected: bool) -> None:
    """施錠状態の判定。"""
    assert is_locked_state(raw) is expected


def test_jammed_blocks_lock() -> None:
    """jammed のとき ensure_locked は例外。"""
    client = MagicMock()
    client.get_device_status_body.return_value = {"lockState": "jammed"}
    with pytest.raises(SwitchBotDeviceStateError):
        ensure_locked(client)
    client.send_lock_command.assert_not_called()


def test_already_locked_skips_command() -> None:
    """施錠済みならコマンドを送らない。"""
    client = MagicMock()
    client.get_device_status_body.return_value = {"lockState": "lock"}
    assert ensure_locked(client) == "already_locked"
    client.send_lock_command.assert_not_called()


def test_unlock_sends_lock() -> None:
    """未施錠なら lock を送信。"""
    client = MagicMock()
    client.get_device_status_body.return_value = {"lockState": "unlock"}
    assert ensure_locked(client) == "locked"
    client.send_lock_command.assert_called_once()


def test_parse_lock_state() -> None:
    """lockState の取得。"""
    assert parse_lock_state({"lockState": " lock "}) == "lock"
