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
from dataclasses import dataclass
from typing import Any

from .models import DEFAULT_SWITCHBOT_API_BASE, SWITCHBOT_API_SUCCESS_STATUS


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


class SwitchBotDeviceStateError(SwitchBotError):
    """デバイス状態が施錠操作に不適切な場合（例: jammed）。"""


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


@dataclass(frozen=True)
class SwitchBotClient:
    """SwitchBot OpenAPI に対する最小限のクライアント。"""

    token: str
    secret: str
    device_id: str
    base_url: str = DEFAULT_SWITCHBOT_API_BASE

    @classmethod
    def from_env(cls) -> SwitchBotClient:
        """環境変数から設定を読み取る。

        Returns:
            設定済みのクライアント。

        Raises:
            ValueError: 必須環境変数が欠けている場合。
        """
        token = os.environ.get("SWITCHBOT_TOKEN", "").strip()
        secret = os.environ.get("SWITCHBOT_SECRET", "").strip()
        device_id = os.environ.get("SWITCHBOT_DEVICE_ID", "").strip()
        base = os.environ.get(
            "SWITCHBOT_API_BASE_URL", DEFAULT_SWITCHBOT_API_BASE
        ).strip()
        if not token or not secret or not device_id:
            raise ValueError(
                "SWITCHBOT_TOKEN, SWITCHBOT_SECRET, SWITCHBOT_DEVICE_ID はすべて必須です。"
            )
        return cls(
            token=token,
            secret=secret,
            device_id=device_id,
            base_url=base or DEFAULT_SWITCHBOT_API_BASE,
        )

    def _auth_headers(self) -> dict[str, str]:
        """認証ヘッダーを生成する。"""
        t_ms = int(time.time() * 1000)
        nonce = str(uuid.uuid4())
        sign = _build_sign(self.token, t_ms, nonce, self.secret)
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "charset": "utf8",
            "t": str(t_ms),
            "sign": sign,
            "nonce": nonce,
        }

    def _request_json(
        self,
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
        url = self.base_url.rstrip("/") + path
        data: bytes | None = None
        headers = self._auth_headers()
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

    def get_device_status_body(self) -> dict[str, Any]:
        """デバイス状態の body を取得する。

        Returns:
            API 応答の ``body`` オブジェクト。
        """
        path = f"/v1.1/devices/{self.device_id}/status"
        payload = self._request_json("GET", path)
        body = payload.get("body")
        if not isinstance(body, dict):
            raise SwitchBotError("応答に body がありません", response_body=payload)
        return body

    def send_lock_command(self) -> dict[str, Any]:
        """施錠コマンドを送信する。

        Returns:
            API 応答全体。
        """
        path = f"/v1.1/devices/{self.device_id}/commands"
        body = {
            "commandType": "command",
            "command": "lock",
            "parameter": "default",
        }
        return self._request_json("POST", path, body)


def parse_lock_state(status_body: dict[str, Any]) -> str | None:
    """状態オブジェクトから lockState を取り出す。

    Args:
        status_body: ``get_device_status_body`` の戻り値。

    Returns:
        lockState 文字列。無ければ None。
    """
    raw = status_body.get("lockState")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip()
    return str(raw)


def is_locked_state(lock_state: str | None) -> bool:
    """施錠済みとみなせるか。

    Args:
        lock_state: API の lockState。

    Returns:
        施錠済みなら True。
    """
    if not lock_state:
        return False
    normalized = lock_state.lower().strip()
    return normalized in ("lock", "locked")


def is_jammed_state(lock_state: str | None) -> bool:
    """ジャム状態か。

    Args:
        lock_state: API の lockState。

    Returns:
        jammed なら True。
    """
    if not lock_state:
        return False
    return lock_state.lower().strip() == "jammed"


def ensure_locked(client: SwitchBotClient) -> str:
    """施錠状態を確認し、未施錠なら施錠する。

    Args:
        client: SwitchBot クライアント。

    Returns:
        ``locked`` または ``already_locked``。

    Raises:
        SwitchBotDeviceStateError: jammed など操作不能な状態。
        SwitchBotError: API エラー。
    """
    status_body = client.get_device_status_body()
    lock_state = parse_lock_state(status_body)
    if is_locked_state(lock_state):
        return "already_locked"
    if is_jammed_state(lock_state):
        raise SwitchBotDeviceStateError(
            "ロックが jammed 状態のため施錠できません",
            api_status_code=None,
            response_body=status_body,
        )
    client.send_lock_command()
    return "locked"
