"""handler.lambda_handler の単体テスト。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from src.handler import lambda_handler
from src.switchbot_client import SwitchBotDeviceStateError, SwitchBotError


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """テスト用の必須環境変数。"""
    monkeypatch.setenv("API_KEY", "expected-secret-key")
    monkeypatch.setenv("SWITCHBOT_TOKEN", "token")
    monkeypatch.setenv("SWITCHBOT_SECRET", "secret")
    monkeypatch.setenv("SWITCHBOT_DEVICE_ID", "DEVICE_ID_01")


def _post_event(path: str = "/lock", api_key: str = "expected-secret-key") -> dict:
    """Function URL 形式の POST イベント。"""
    return {
        "version": "2.0",
        "rawPath": path,
        "requestContext": {"http": {"method": "POST", "path": path}},
        "headers": {"x-api-key": api_key},
        "isBase64Encoded": False,
        "body": "",
    }


def test_missing_api_key_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """API_KEY 未設定は 500。"""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("SWITCHBOT_TOKEN", "t")
    monkeypatch.setenv("SWITCHBOT_SECRET", "s")
    monkeypatch.setenv("SWITCHBOT_DEVICE_ID", "d")
    r = lambda_handler(_post_event(), None)
    assert r["statusCode"] == 500
    body = json.loads(r["body"])
    assert body["error"] == "configuration_error"


def test_unauthorized_missing_header(base_env: None) -> None:
    """API キーが無いと 401。"""
    ev = _post_event()
    del ev["headers"]
    r = lambda_handler(ev, None)
    assert r["statusCode"] == 401


def test_unauthorized_wrong_key(base_env: None) -> None:
    """API キーが不一致なら 401。"""
    r = lambda_handler(_post_event(api_key="wrong"), None)
    assert r["statusCode"] == 401


def test_method_not_allowed(base_env: None) -> None:
    """GET は 405。"""
    ev = _post_event()
    ev["requestContext"]["http"]["method"] = "GET"
    r = lambda_handler(ev, None)
    assert r["statusCode"] == 405


def test_not_found_path(base_env: None) -> None:
    """未対応パスは 404。"""
    ev = _post_event(path="/other")
    r = lambda_handler(ev, None)
    assert r["statusCode"] == 404


def test_already_locked(base_env: None) -> None:
    """施錠済みなら result=already_locked。"""
    with patch("src.handler.ensure_locked", return_value="already_locked"):
        r = lambda_handler(_post_event(), None)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["ok"] is True
    assert body["result"] == "already_locked"


def test_locked(base_env: None) -> None:
    """施錠実行時は result=locked。"""
    with patch("src.handler.ensure_locked", return_value="locked"):
        r = lambda_handler(_post_event(), None)
    assert r["statusCode"] == 200
    body = json.loads(r["body"])
    assert body["result"] == "locked"


def test_switchbot_error_502(base_env: None) -> None:
    """SwitchBotError は 502。"""
    err = SwitchBotError("API失敗", http_status=500, api_status_code=190)
    with patch("src.handler.ensure_locked", side_effect=err):
        r = lambda_handler(_post_event(), None)
    assert r["statusCode"] == 502
    body = json.loads(r["body"])
    assert body["error"] == "switchbot_error"


def test_jammed_502(base_env: None) -> None:
    """jammed は device_state で 502。"""
    with patch(
        "src.handler.ensure_locked",
        side_effect=SwitchBotDeviceStateError("jammed"),
    ):
        r = lambda_handler(_post_event(), None)
    assert r["statusCode"] == 502
    body = json.loads(r["body"])
    assert body["error"] == "device_state"


def test_root_path_allowed(base_env: None) -> None:
    """Function URL のルート ``/`` も POST 許可。"""
    with patch("src.handler.ensure_locked", return_value="already_locked"):
        r = lambda_handler(_post_event(path="/"), None)
    assert r["statusCode"] == 200
