from typing import Literal, TypedDict


class RequestHeaders(TypedDict, total=False):
    x_api_key: str


class LockEvent(TypedDict):
    """Lambda に渡される入力イベント（簡易版）。"""

    headers: dict[str, str]
    device_id: str
    action: Literal["lock"]


class LambdaContext:
    """型ヒント用のスタブ。実体は AWS ランタイムが渡す。"""

    function_name: str
    function_version: str
    invoked_function_arn: str
    memory_limit_in_mb: str
    aws_request_id: str
    log_group_name: str
    log_stream_name: str
