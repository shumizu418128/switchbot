"""レスポンス用の定数と型定義。"""

from __future__ import annotations

from typing import Literal

# Lambda JSON ボディで返す結果コード
ResultCode = Literal["locked", "already_locked"]

# SwitchBot API の成功コード（公式ドキュメント）
SWITCHBOT_API_SUCCESS_STATUS = 100

# デフォルトの SwitchBot OpenAPI ベース URL
DEFAULT_SWITCHBOT_API_BASE = "https://api.switch-bot.com"
