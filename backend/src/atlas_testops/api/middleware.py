"""HTTP 中间件。"""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from atlas_testops.core.request_context import (
    REQUEST_ID_HEADER,
    normalize_request_id,
    reset_request_id,
    set_request_id,
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
