"""Slack リクエスト署名の検証。"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
MAX_TIMESTAMP_AGE_SECONDS = 60 * 5


def verify_slack_signature(timestamp: str, signature: str, body: str) -> bool:
    """Slack Interactivity リクエストの署名を検証する。

    Args:
        timestamp: ``X-Slack-Request-Timestamp`` ヘッダー値。
        signature: ``X-Slack-Signature`` ヘッダー値。
        body: リクエストの raw body 文字列。

    Returns:
        署名が有効なら ``True``。
    """
    if not SLACK_SIGNING_SECRET or not timestamp or not signature:
        return False

    try:
        request_ts = int(timestamp)
    except ValueError:
        return False

    if abs(time.time() - request_ts) > MAX_TIMESTAMP_AGE_SECONDS:
        return False

    sig_basestring = f"v0:{timestamp}:{body}"
    computed = (
        "v0="
        + hmac.new(
            SLACK_SIGNING_SECRET.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(computed, signature)
