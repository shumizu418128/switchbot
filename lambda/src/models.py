from typing import Any, TypedDict


class RequestHeaders(TypedDict, total=False):
    x_api_key: str


class ScheduledEvent(TypedDict, total=False):
    """EventBridge スケジュールイベント。"""

    action: str


class ApiGatewayEvent(TypedDict, total=False):
    """API Gateway プロキシ統合イベント。"""

    body: str | dict[str, Any]
    headers: dict[str, str]
    httpMethod: str
    path: str
    rawPath: str
    requestContext: dict[str, Any]


class LambdaEvent(TypedDict, total=False):
    """Lambda に渡されるイベント（スケジュールまたは API Gateway）。"""

    action: str
    body: str | dict[str, Any]
    headers: dict[str, str]
    httpMethod: str
    path: str
    rawPath: str
    requestContext: dict[str, Any]


class LambdaContext:
    """型ヒント用のスタブ。実体は AWS ランタイムが渡す。"""

    function_name: str
    function_version: str
    invoked_function_arn: str
    memory_limit_in_mb: str
    aws_request_id: str
    log_group_name: str
    log_stream_name: str
