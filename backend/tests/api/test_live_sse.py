"""HTTP and SSE contracts for private replayable DebugRun live projections."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient

import atlas_testops.api.middleware as api_middleware
import atlas_testops.api.v1.live as live_api
from atlas_testops.api.dependencies import (
    get_debug_live_service,
    get_debug_live_stream_limiter,
)
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.live import (
    DebugLiveService,
    DebugLiveStreamLimiter,
    DebugLiveStreamPlan,
)
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import (
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
)
from atlas_testops.domain.runtime.live import (
    DebugLiveCursor,
    DebugLiveEvent,
    DebugLiveRunProjection,
    DebugLiveSnapshot,
    encode_debug_live_cursor,
)
from atlas_testops.main import create_app

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("20000000-0000-4000-8000-000000000002")
ENVIRONMENT_ID = UUID("30000000-0000-4000-8000-000000000003")
CASE_ID = UUID("40000000-0000-4000-8000-000000000004")
RUN_ID = UUID("50000000-0000-4000-8000-000000000005")
NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def _cursor(after_seq: int) -> str:
    return encode_debug_live_cursor(DebugLiveCursor(debug_run_id=RUN_ID, after_seq=after_seq))


def _event(*, seq: int = 2) -> DebugLiveEvent:
    return DebugLiveEvent(
        event_id=uuid7(),
        debug_run_id=RUN_ID,
        seq=seq,
        event_type="debug_run.browser.action.executed",
        lifecycle=DebugRunLifecycle.RUNNING,
        outcome=DebugRunOutcome.NOT_SET,
        snapshot_status=DebugRunSnapshotStatus.CURRENT,
        data={
            "actionId": "action-0001",
            "action": "CLICK",
            "status": "SUCCEEDED",
            "resultingPageRevision": 8,
        },
        occurred_at=NOW + timedelta(seconds=seq),
        cursor=_cursor(seq),
    )


def _snapshot() -> DebugLiveSnapshot:
    return DebugLiveSnapshot(
        run=DebugLiveRunProjection(
            debug_run_id=RUN_ID,
            project_id=PROJECT_ID,
            test_case_id=CASE_ID,
            environment_id=ENVIRONMENT_ID,
            lifecycle=DebugRunLifecycle.RUNNING,
            outcome=DebugRunOutcome.NOT_SET,
            snapshot_status=DebugRunSnapshotStatus.CURRENT,
            revision=4,
            execution_deadline=NOW + timedelta(minutes=5),
            started_at=NOW,
        ),
        cursor=_cursor(1),
        latest_event=None,
        observed_at=NOW,
    )


class RecordingLiveService:
    def __init__(
        self,
        snapshot: DebugLiveSnapshot,
        *,
        events: tuple[DebugLiveEvent | None, ...] = (),
        error: ApplicationError | None = None,
        maximum_connection_seconds: float = 1.0,
    ) -> None:
        self.snapshot = snapshot
        self.events = events
        self.error = error
        self.maximum_connection_seconds = maximum_connection_seconds
        self.snapshot_calls: list[tuple[ActorContext, UUID]] = []
        self.prepare_calls: list[tuple[ActorContext, UUID, str | None]] = []
        self.iter_calls: list[tuple[ActorContext, UUID, DebugLiveStreamPlan]] = []

    async def get_snapshot(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> DebugLiveSnapshot:
        self.snapshot_calls.append((actor, run_id))
        if self.error is not None:
            raise self.error
        return self.snapshot

    async def prepare_stream(
        self,
        actor: ActorContext,
        run_id: UUID,
        *,
        last_event_id: str | None,
    ) -> DebugLiveStreamPlan:
        self.prepare_calls.append((actor, run_id, last_event_id))
        if self.error is not None:
            raise self.error
        return DebugLiveStreamPlan(
            snapshot=self.snapshot if last_event_id is None else None,
            after_seq=1,
        )

    async def iter_events(
        self,
        actor: ActorContext,
        run_id: UUID,
        plan: DebugLiveStreamPlan,
        *,
        is_disconnected: object,
    ) -> AsyncIterator[DebugLiveEvent | None]:
        assert is_disconnected is not None
        self.iter_calls.append((actor, run_id, plan))
        for event in self.events:
            yield event


class RecordingLimiter:
    def __init__(self, error: ApplicationError | None = None) -> None:
        self.error = error
        self.acquire_calls = 0
        self.release_calls = 0

    async def acquire(self) -> None:
        self.acquire_calls += 1
        if self.error is not None:
            raise self.error

    def release(self) -> None:
        self.release_calls += 1


def _client(
    service: RecordingLiveService,
    limiter: RecordingLimiter | None = None,
) -> tuple[TestClient, ActorContext, RecordingLimiter]:
    actor = ActorContext(
        tenant_id=TENANT_ID,
        actor_id=uuid7(),
        request_id="request-debug-live-api",
        development_override=True,
    )
    selected_limiter = limiter or RecordingLimiter()
    settings = Settings(environment="test", cors_origins=[]).model_copy(
        update={
            "debug_live_max_connection_seconds": service.maximum_connection_seconds,
        }
    )
    app = create_app(settings)
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_debug_live_service] = lambda: cast(
        DebugLiveService,
        service,
    )
    app.dependency_overrides[get_debug_live_stream_limiter] = lambda: cast(
        DebugLiveStreamLimiter,
        selected_limiter,
    )
    return TestClient(app), actor, selected_limiter


def _sse_blocks(response_text: str) -> list[str]:
    return [block for block in response_text.split("\n\n") if block]


def _sse_fields(block: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in block.splitlines())


def _stream_scope(*, spec_version: str) -> dict[str, Any]:
    path = f"/v1/debug-runs/{RUN_ID}/events/stream"
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


async def _receive_forever() -> dict[str, Any]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


def test_snapshot_endpoint_returns_private_safe_projection() -> None:
    snapshot = _snapshot()
    service = RecordingLiveService(snapshot)
    client, actor, _limiter = _client(service)

    with client:
        response = client.get(f"/v1/debug-runs/{RUN_ID}/live")

    assert response.status_code == 200
    assert response.headers["cache-control"] == ("private, no-store, no-transform, max-age=0")
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.json() == snapshot.model_dump(mode="json", by_alias=True)
    assert service.snapshot_calls == [(actor, RUN_ID)]


def test_initial_sse_sends_snapshot_then_delta_and_comment_heartbeat() -> None:
    snapshot = _snapshot()
    event = _event()
    service = RecordingLiveService(snapshot, events=(event, None))
    client, actor, limiter = _client(service)

    with client:
        response = client.get(f"/v1/debug-runs/{RUN_ID}/events/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == ("private, no-store, no-transform, max-age=0")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Cookie, Last-Event-ID, Origin"
    blocks = _sse_blocks(response.text)
    assert len(blocks) == 3

    snapshot_fields = _sse_fields(blocks[0])
    assert snapshot_fields["id"] == snapshot.cursor
    assert snapshot_fields["event"] == "debug_run.live.snapshot"
    assert json.loads(snapshot_fields["data"]) == snapshot.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )

    event_fields = _sse_fields(blocks[1])
    assert event_fields["id"] == event.cursor
    assert event_fields["event"] == event.event_type
    assert json.loads(event_fields["data"]) == event.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )

    assert blocks[2] == ": heartbeat"
    assert "id:" not in blocks[2]
    assert service.prepare_calls == [(actor, RUN_ID, None)]
    assert len(service.iter_calls) == 1
    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 1


def test_last_event_id_resumes_without_duplicate_snapshot() -> None:
    snapshot = _snapshot()
    event = _event()
    service = RecordingLiveService(snapshot, events=(event,))
    client, actor, limiter = _client(service)
    resume_cursor = _cursor(1)

    with client:
        response = client.get(
            f"/v1/debug-runs/{RUN_ID}/events/stream",
            headers={"Last-Event-ID": resume_cursor},
        )

    blocks = _sse_blocks(response.text)
    assert response.status_code == 200
    assert len(blocks) == 1
    assert "debug_run.live.snapshot" not in response.text
    assert _sse_fields(blocks[0])["id"] == event.cursor
    assert service.prepare_calls == [(actor, RUN_ID, resume_cursor)]
    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 1


def test_oversized_last_event_id_uses_live_cursor_problem_instead_of_422() -> None:
    invalid_cursor = ApplicationError(
        error_code=ErrorCode.LIVE_CURSOR_INVALID,
        title="Live Cursor 无效",
        detail="Live Cursor 已损坏、超出限制或不属于当前 DebugRun。",
        status_code=400,
    )
    service = RecordingLiveService(_snapshot(), error=invalid_cursor)
    client, actor, limiter = _client(service)
    oversized_cursor = "a" * 513

    with client:
        response = client.get(
            f"/v1/debug-runs/{RUN_ID}/events/stream",
            headers={"Last-Event-ID": oversized_cursor},
        )

    assert response.status_code == 400
    assert response.json()["errorCode"] == "LIVE_CURSOR_INVALID"
    assert service.prepare_calls == [(actor, RUN_ID, oversized_cursor)]
    assert limiter.acquire_calls == 0
    assert limiter.release_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("spec_version", "expected_error"),
    [("2.3", OSError), ("2.4", OSError)],
)
async def test_stream_send_failure_always_releases_capacity(
    spec_version: str,
    expected_error: type[Exception],
) -> None:
    service = RecordingLiveService(_snapshot(), events=(_event(),))
    client, _actor, limiter = _client(service)
    application = cast(Any, client.app)

    async def fail_on_body(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client socket closed")

    with pytest.raises(expected_error):
        await application(
            _stream_scope(spec_version=spec_version),
            _receive_forever,
            fail_on_body,
        )

    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 1


@pytest.mark.anyio
async def test_stream_does_not_swallow_unrelated_send_timeout() -> None:
    service = RecordingLiveService(_snapshot(), events=(_event(),))
    client, _actor, limiter = _client(service)
    application = cast(Any, client.app)

    async def fail_on_body(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            raise TimeoutError("upstream send failed independently")

    with pytest.raises(TimeoutError, match="independently"):
        await application(
            _stream_scope(spec_version="2.4"),
            _receive_forever,
            fail_on_body,
        )

    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 1


@pytest.mark.anyio
async def test_stream_hard_deadline_releases_capacity_when_send_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_api, "_STREAM_CLOSE_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(
        api_middleware,
        "DEBUG_LIVE_STREAM_CLOSE_GRACE_SECONDS",
        0.01,
    )
    service = RecordingLiveService(
        _snapshot(),
        events=(_event(),),
        maximum_connection_seconds=0.01,
    )
    client, _actor, limiter = _client(service)
    application = cast(Any, client.app)
    body_send_started = asyncio.Event()

    async def stall_on_body(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            body_send_started.set()
            await asyncio.Event().wait()

    await asyncio.wait_for(
        application(
            _stream_scope(spec_version="2.4"),
            _receive_forever,
            stall_on_body,
        ),
        timeout=0.5,
    )

    assert body_send_started.is_set()
    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 1


def test_capacity_is_rejected_before_sse_headers_or_iteration() -> None:
    capacity_error = ApplicationError(
        error_code=ErrorCode.LIVE_STREAM_CAPACITY_EXCEEDED,
        title="Live Stream 容量已满",
        detail="当前 API 实例暂时无法接受更多 Live Observer。",
        status_code=429,
        headers={"Retry-After": "2"},
    )
    service = RecordingLiveService(_snapshot(), events=(_event(),))
    limiter = RecordingLimiter(capacity_error)
    client, _actor, _limiter = _client(service, limiter)

    with client:
        response = client.get(f"/v1/debug-runs/{RUN_ID}/events/stream")

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["retry-after"] == "2"
    assert response.json()["errorCode"] == "LIVE_STREAM_CAPACITY_EXCEEDED"
    assert "text/event-stream" not in response.headers["content-type"]
    assert len(service.prepare_calls) == 1
    assert service.iter_calls == []
    assert limiter.acquire_calls == 1
    assert limiter.release_calls == 0


def test_missing_and_hidden_runs_are_indistinguishable_before_stream_start() -> None:
    not_found = ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="DebugRun 不存在",
        detail="DebugRun 不存在或不可见。",
        status_code=404,
    )
    service = RecordingLiveService(_snapshot(), error=not_found)
    limiter = RecordingLimiter()
    client, _actor, _limiter = _client(service, limiter)

    with client:
        snapshot_response = client.get(f"/v1/debug-runs/{RUN_ID}/live")
        stream_response = client.get(f"/v1/debug-runs/{RUN_ID}/events/stream")

    assert snapshot_response.status_code == 404
    assert stream_response.status_code == 404
    assert snapshot_response.json()["errorCode"] == "NOT_FOUND"
    assert stream_response.json()["errorCode"] == "NOT_FOUND"
    assert snapshot_response.json()["detail"] == stream_response.json()["detail"]
    assert stream_response.headers["content-type"].startswith("application/problem+json")
    assert limiter.acquire_calls == 0
    assert limiter.release_calls == 0


def test_live_openapi_declares_platform_session_and_sse_media_type() -> None:
    document = create_app(Settings(environment="test", cors_origins=[])).openapi()
    snapshot_operation = document["paths"]["/v1/debug-runs/{runId}/live"]["get"]
    stream_operation = document["paths"]["/v1/debug-runs/{runId}/events/stream"]["get"]

    assert snapshot_operation["security"] == [{"PlatformSession": []}]
    assert stream_operation["security"] == [{"PlatformSession": []}]
    assert stream_operation["responses"]["200"]["content"]["text/event-stream"]["schema"] == {
        "type": "string"
    }
