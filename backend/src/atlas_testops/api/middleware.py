"""HTTP 中间件。"""

from asyncio import get_running_loop, timeout_at
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas_testops.api.problem_details import PROBLEM_CONTENT_TYPE, ProblemDetails
from atlas_testops.core.errors import ErrorCode
from atlas_testops.core.request_context import (
    REQUEST_ID_HEADER,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)

BROWSER_RUNTIME_MAX_BODY_BYTES = 1024 * 1024
DEBUG_LIVE_STREAM_CLOSE_GRACE_SECONDS = 1.0


class _DebugLiveStreamSendDeadlineExceeded(Exception):
    """Stop an SSE response whose client-facing send exceeded its deadline."""


class DebugLiveStreamSendDeadlineMiddleware:
    """Bound real client writes after BaseHTTPMiddleware response wrapping."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        stream_path_prefix: str,
        maximum_connection_seconds: float,
        close_grace_seconds: float | None = None,
    ) -> None:
        self._app = app
        self._stream_path_prefix = stream_path_prefix
        self._maximum_seconds = maximum_connection_seconds
        self._close_grace_seconds = (
            DEBUG_LIVE_STREAM_CLOSE_GRACE_SECONDS
            if close_grace_seconds is None
            else close_grace_seconds
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if not self._is_debug_live_stream(scope):
            await self._app(scope, receive, send)
            return

        deadline: float | None = None

        async def send_with_deadline(message: Message) -> None:
            nonlocal deadline
            if message["type"] == "http.response.start" and deadline is None:
                deadline = (
                    get_running_loop().time()
                    + self._maximum_seconds
                    + self._close_grace_seconds
                )
            if deadline is None:
                await send(message)
                return
            timer = timeout_at(deadline)
            try:
                async with timer:
                    await send(message)
            except TimeoutError:
                if not timer.expired():
                    raise
                raise _DebugLiveStreamSendDeadlineExceeded from None

        try:
            await self._app(scope, receive, send_with_deadline)
        except _DebugLiveStreamSendDeadlineExceeded:
            return

    def _is_debug_live_stream(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope.get("method") != "GET":
            return False
        path = str(scope.get("path", ""))
        return path.startswith(self._stream_path_prefix) and path.endswith(
            "/events/stream"
        )


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """建立 Request ID 上下文，并把最终值回传给调用方。"""

    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    token = set_request_id(request_id)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def browser_runtime_body_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Reject unbounded/chunked Runtime commands before FastAPI buffers their body."""

    if not request.url.path.startswith("/internal/v1/debug-runs/"):
        return await call_next(request)
    content_length = request.headers.get("Content-Length")
    if not (request.method == "GET" and content_length in {None, "0"}):
        try:
            body_size = int(content_length) if content_length is not None else -1
        except ValueError:
            body_size = -1
        if not 0 <= body_size <= BROWSER_RUNTIME_MAX_BODY_BYTES:
            request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
            problem = ProblemDetails(
                type="https://atlas.test/problems/invalid-request",
                title="Browser Runtime 请求体无效",
                status=413,
                detail="内部 Runtime 请求必须携带 Content-Length 且不能超过 1 MiB。",
                instance=request.url.path,
                error_code=ErrorCode.INVALID_REQUEST,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=413,
                content=problem.model_dump(mode="json", by_alias=True),
                media_type=PROBLEM_CONTENT_TYPE,
                headers={
                    REQUEST_ID_HEADER: request_id,
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                },
            )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response
