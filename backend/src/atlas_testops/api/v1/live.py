"""Private replayable DebugRun live projection API."""

from asyncio import timeout
from collections.abc import AsyncGenerator, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Request, Response
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from atlas_testops.api.dependencies import (
    DebugLiveServiceDependency,
    DebugLiveStreamLimiterDependency,
)
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.domain.runtime import (
    DebugLiveEvent,
    DebugLiveSnapshot,
)
from atlas_testops.domain.runtime.live import DEBUG_LIVE_CURSOR_MAX_LENGTH

RunIdPath = Annotated[UUID, Path(alias="runId")]
LastEventIdHeader = Annotated[
    str | None,
    Header(
        alias="Last-Event-ID",
        json_schema_extra={"maxLength": DEBUG_LIVE_CURSOR_MAX_LENGTH},
    ),
]

_PRIVATE_NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store, no-transform, max-age=0",
    "Pragma": "no-cache",
    "Vary": "Cookie, Last-Event-ID, Origin",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
_STREAM_HEADERS = {
    **_PRIVATE_NO_STORE_HEADERS,
    "X-Accel-Buffering": "no",
}
_STREAM_CLOSE_GRACE_SECONDS = 1.0


class _DebugLiveStreamingResponse(StreamingResponse):
    """Enforce the stream lifetime across generation and network writes."""

    def __init__(
        self,
        source: AsyncGenerator[str],
        *,
        maximum_seconds: float,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(
            source,
            media_type="text/event-stream",
            headers=_STREAM_HEADERS,
        )
        self._source = source
        self._maximum_seconds = maximum_seconds
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        deadline = timeout(self._maximum_seconds + _STREAM_CLOSE_GRACE_SECONDS)
        try:
            async with deadline:
                await super().__call__(scope, receive, send)
        except TimeoutError:
            if not deadline.expired():
                raise
        finally:
            try:
                await self._source.aclose()
            finally:
                self._on_close()

router = APIRouter(
    responses={
        400: {"description": "Live Cursor 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "DebugRun 不存在或不可见", "model": ProblemDetails},
        429: {"description": "Live Observer 容量已满", "model": ProblemDetails},
    }
)


@router.get(
    "/debug-runs/{runId}/live",
    response_model=DebugLiveSnapshot,
    summary="读取 DebugRun Live 安全快照",
)
async def get_debug_live_snapshot(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: DebugLiveServiceDependency,
) -> DebugLiveSnapshot:
    snapshot = await service.get_snapshot(actor, run_id)
    response.headers.update(_PRIVATE_NO_STORE_HEADERS)
    return snapshot


@router.get(
    "/debug-runs/{runId}/events/stream",
    response_class=Response,
    responses={
        200: {
            "description": "DebugRun snapshot 后接可恢复的单调事件流",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            },
        }
    },
    summary="订阅可恢复的 DebugRun Live 事件",
)
async def stream_debug_live_events(
    run_id: RunIdPath,
    request: Request,
    actor: ActorDependency,
    service: DebugLiveServiceDependency,
    limiter: DebugLiveStreamLimiterDependency,
    last_event_id: LastEventIdHeader = None,
) -> StreamingResponse:
    plan = await service.prepare_stream(
        actor,
        run_id,
        last_event_id=last_event_id,
    )
    await limiter.acquire()
    released = False

    def release_once() -> None:
        nonlocal released
        if released:
            return
        limiter.release()
        released = True

    async def event_source() -> AsyncGenerator[str]:
        try:
            if plan.snapshot is not None:
                yield _sse_message(
                    event_id=plan.snapshot.cursor,
                    event_type="debug_run.live.snapshot",
                    data=plan.snapshot.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    ),
                )
            async for event in service.iter_events(
                actor,
                run_id,
                plan,
                is_disconnected=request.is_disconnected,
            ):
                if event is None:
                    yield ": heartbeat\n\n"
                else:
                    yield _event_message(event)
        finally:
            release_once()

    try:
        return _DebugLiveStreamingResponse(
            event_source(),
            maximum_seconds=service.maximum_connection_seconds,
            on_close=release_once,
        )
    except Exception:
        release_once()
        raise


def _event_message(event: DebugLiveEvent) -> str:
    return _sse_message(
        event_id=event.cursor,
        event_type=event.event_type,
        data=event.model_dump_json(by_alias=True, exclude_none=True),
    )


def _sse_message(*, event_id: str, event_type: str, data: str) -> str:
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"
