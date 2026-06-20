"""EventBridge スケジュールイベントのハンドラー。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from models import ScheduledEvent
from switchbot_service import co2_check, lock_check

TaskFn = Callable[[], Any]


def co2_and_lock_check():
    co2_check()
    lock_check()


SCHEDULE_TASKS: dict[str, TaskFn] = {
    "co2_and_lock_check": co2_and_lock_check,
}


def handle_scheduled(event: ScheduledEvent) -> Any:
    """スケジュールイベントを action 名でディスパッチする。

    Args:
        event: EventBridge が渡すイベント（例: ``{"action": "co2_check"}``）。

    Returns:
        各タスクハンドラーの戻り値。

    Raises:
        ValueError: 未登録の action の場合。
    """
    action = event.get("action")
    if not action:
        raise ValueError("schedule event requires 'action'")

    task_fn = SCHEDULE_TASKS.get(action)
    if task_fn is None:
        raise ValueError(f"unknown schedule action: {action}")

    return task_fn()
