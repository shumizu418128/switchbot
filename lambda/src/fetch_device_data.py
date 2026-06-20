"""SwitchBot デバイス一覧を取得し .device_data.json に保存する（仮設スクリプト）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from switchbot_client import SwitchBotError, request_json

REPO_ROOT = Path(__file__).resolve().parent
LAMBDA_SRC = REPO_ROOT / "lambda" / "src"
OUTPUT_PATH = REPO_ROOT / ".device_data.json"

os.environ.setdefault("ENV", "local")
sys.path.insert(0, str(LAMBDA_SRC))

load_dotenv()


def main() -> None:
    """デバイス一覧 API を呼び出し、結果を JSON ファイルに書き出す。"""
    try:
        payload = request_json("GET", "/v1.1/devices")
    except SwitchBotError as e:
        print(f"取得失敗: {e}", file=sys.stderr)
        if e.response_body is not None:
            print(
                json.dumps(e.response_body, ensure_ascii=False, indent=2),
                file=sys.stderr,
            )
        raise SystemExit(1) from e

    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    print(f"保存しました: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
