"""Safe DebugRun live snapshots and replayable event streams."""

from asyncio import Lock, sleep
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from uuid import UUID

from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import DebugRunEvent
from atlas_testops.domain.runtime.live import (
    DebugLiveCursor,
    DebugLiveEvent,
    DebugLiveSnapshot,
    decode_debug_live_cursor,
    encode_debug_live_cursor,
)
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.debug_live_frames import (
    DebugLiveFrameContent,
    DebugLiveFrameRepository,
)
from atlas_testops.infrastructure.repositories.debug_runs import (
    DebugRunLiveSeed,
    DebugRunRepository,
)

_LIVE_EVENT_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "debug_run.requested": (
        "semanticRevision",
        "environmentId",
    ),
    "debug_run.snapshot_outdated": ("currentSemanticRevision",),
    "debug_run.cancel_requested": (),
    "debug_run.execution_bound": (
        "executionContractId",
        "browserRevision",
        "modelProfileRef",
        "toolCatalogRef",
    ),
    "debug_run.ready": ("executionContractId",),
    "debug_run.started": ("executionContractId",),
    "debug_run.finalizing": (
        "evidenceManifestId",
        "completeness",
        "integrity",
    ),
    "debug_run.terminated": (
        "outcome",
        "evidenceManifestId",
    ),
    "debug_run.browser.execution.started": (
        "reportId",
        "reportSequence",
        "reportKind",
        "safeSummary",
    ),
    "debug_run.browser.node.started": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "nodeId",
        "nodeKind",
        "versionRef",
    ),
    "debug_run.browser.observation.captured": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "observationRef",
        "pageRef",
        "pageRevision",
        "routeKey",
        "targetCount",
    ),
    "debug_run.browser.planner.completed": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "planningMode",
        "provider",
        "model",
        "externalCall",
        "status",
        "latencyMs",
        "inputUnits",
        "outputUnits",
        "modelProfileRef",
        "promptBundleRef",
        "reasoningPolicyRef",
        "selectedTargetRole",
    ),
    "debug_run.browser.action.proposed": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "actionId",
        "safeSummary",
        "action",
        "risk",
        "nodeId",
        "targetRef",
        "routeKey",
    ),
    "debug_run.browser.policy.decided": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "actionId",
        "safeSummary",
        "decision",
        "matchedRules",
        "matchedRuleCount",
    ),
    "debug_run.browser.action.executed": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "actionId",
        "safeSummary",
        "receiptId",
        "action",
        "status",
        "resultingPageRevision",
    ),
    "debug_run.browser.artifact.captured": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "artifactId",
        "kind",
        "sizeBytes",
        "integrity",
    ),
    "debug_run.browser.assertion.evaluated": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "assertionId",
        "status",
    ),
    "debug_run.browser.node.completed": (
        "reportId",
        "reportSequence",
        "reportKind",
        "actorSlot",
        "safeSummary",
        "nodeId",
        "assertionResultCount",
        "artifactCount",
    ),
    "debug_run.browser.execution.blocked": (
        "reportId",
        "reportSequence",
        "reportKind",
        "safeSummary",
        "failureType",
    ),
    "debug_run.browser.execution.completed": (
        "reportId",
        "reportSequence",
        "reportKind",
        "safeSummary",
        "assertionResultCount",
        "artifactCount",
    ),
}


@dataclass(frozen=True, slots=True)
class DebugLiveStreamPlan:
    """Validated stream start that is safe to use after HTTP headers begin."""

    snapshot: DebugLiveSnapshot | None
    after_seq: int


class DebugLiveStreamLimiter:
    """Bound process-local observers without holding waiting requests open."""

    def __init__(self, maximum_connections: int) -> None:
        if maximum_connections < 1:
            raise ValueError("maximum_connections must be positive")
        self._maximum_connections = maximum_connections
        self._active_connections = 0
        self._lock = Lock()

    @property
    def active_connections(self) -> int:
        return self._active_connections

    async def acquire(self) -> None:
        async with self._lock:
            if self._active_connections >= self._maximum_connections:
                raise ApplicationError(
                    error_code=ErrorCode.LIVE_STREAM_CAPACITY_EXCEEDED,
                    title="Live Stream 容量已满",
                    detail="当前 API 实例暂时无法接受更多 Live Observer。",
                    status_code=429,
                    headers={"Retry-After": "2"},
                )
            self._active_connections += 1

    def release(self) -> None:
        if self._active_connections < 1:
            raise RuntimeError("debug live stream capacity released twice")
        self._active_connections -= 1


class DebugLiveService:
    """Project authorized, lock-free projection over durable DebugRun events."""

    def __init__(
        self,
        database: Database,
        *,
        poll_interval_seconds: float,
        heartbeat_interval_seconds: float,
        maximum_connection_seconds: float,
        batch_size: int,
        repository: DebugRunRepository | None = None,
        frame_repository: DebugLiveFrameRepository | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll interval must be positive")
        if heartbeat_interval_seconds <= poll_interval_seconds:
            raise ValueError("heartbeat interval must exceed poll interval")
        if maximum_connection_seconds <= heartbeat_interval_seconds:
            raise ValueError("maximum connection lifetime must exceed heartbeat interval")
        if not 1 <= batch_size <= 500:
            raise ValueError("batch size must be between 1 and 500")
        self._database = database
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._maximum_connection_seconds = maximum_connection_seconds
        self._batch_size = batch_size
        self._runs = repository or DebugRunRepository()
        self._frames = frame_repository or DebugLiveFrameRepository()

    @property
    def maximum_connection_seconds(self) -> float:
        return self._maximum_connection_seconds

    async def get_snapshot(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> DebugLiveSnapshot:
        """Return a safe run projection and its exact event high-water cursor."""

        seed = await self._get_visible_seed(actor, run_id)
        return self._snapshot(seed)

    async def get_live_frame(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> DebugLiveFrameContent:
        """Read and independently verify the latest private operator frame."""

        async with self._database.transaction(actor.database_context()) as connection:
            seed = await self._runs.get_live_seed(connection, run_id)
            if seed is None or not actor.can_read_project(seed.run.project_id):
                raise self._not_found()
            frame = await self._frames.get(connection, run_id)
            if frame is None:
                raise ApplicationError(
                    error_code=ErrorCode.NOT_FOUND,
                    title="实时浏览器画面尚未生成",
                    detail="Browser Worker 尚未发布可展示的实时画面。",
                    status_code=404,
                )
        digest = f"sha256:{sha256(frame.payload).hexdigest()}"
        if (
            len(frame.payload) != frame.metadata.size_bytes
            or digest != frame.metadata.content_digest
        ):
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="实时浏览器画面校验失败",
                detail="实时画面字节与受信元数据不一致，Atlas 已拒绝展示。",
                status_code=503,
            )
        return frame

    async def prepare_stream(
        self,
        actor: ActorContext,
        run_id: UUID,
        *,
        last_event_id: str | None,
    ) -> DebugLiveStreamPlan:
        """Validate authorization and cursor before starting a streaming response."""

        cursor = (
            decode_debug_live_cursor(last_event_id, expected_run_id=run_id)
            if last_event_id is not None
            else None
        )
        seed = await self._get_visible_seed(actor, run_id)
        if cursor is not None and cursor.after_seq > seed.head_seq:
            raise ApplicationError(
                error_code=ErrorCode.LIVE_CURSOR_INVALID,
                title="Live Cursor 无效",
                detail="Live Cursor 超过当前 DebugRun 的事件高水位。",
                status_code=400,
            )
        after_seq = seed.head_seq if cursor is None else cursor.after_seq
        return DebugLiveStreamPlan(
            snapshot=self._snapshot(seed) if cursor is None else None,
            after_seq=after_seq,
        )

    async def iter_events(
        self,
        actor: ActorContext,
        run_id: UUID,
        plan: DebugLiveStreamPlan,
        *,
        is_disconnected: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[DebugLiveEvent | None]:
        """Yield projected events or heartbeat markers outside database transactions."""

        after_seq = plan.after_seq
        started_at = monotonic()
        last_emit_at = started_at
        while monotonic() - started_at < self._maximum_connection_seconds:
            if await is_disconnected():
                return
            records = await self._read_events(actor, run_id, after_seq=after_seq)
            if records:
                for record in records:
                    if await is_disconnected():
                        return
                    event = self._event(record)
                    after_seq = record.seq
                    last_emit_at = monotonic()
                    yield event
                continue

            now = monotonic()
            if now - last_emit_at >= self._heartbeat_interval_seconds:
                last_emit_at = now
                yield None
            remaining = self._maximum_connection_seconds - (monotonic() - started_at)
            if remaining <= 0:
                return
            await sleep(min(self._poll_interval_seconds, remaining))

    async def _get_visible_seed(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> DebugRunLiveSeed:
        async with self._database.transaction(actor.database_context()) as connection:
            seed = await self._runs.get_live_seed(connection, run_id)
            if seed is None or not actor.can_read_project(seed.run.project_id):
                raise self._not_found()
            return seed

    @staticmethod
    def _not_found() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="DebugRun 不存在",
            detail="DebugRun 不存在或不可见。",
            status_code=404,
        )

    async def _read_events(
        self,
        actor: ActorContext,
        run_id: UUID,
        *,
        after_seq: int,
    ) -> tuple[DebugRunEvent, ...]:
        async with self._database.transaction(actor.database_context()) as connection:
            records = await self._runs.list_events(
                connection,
                run_id=run_id,
                after_seq=after_seq,
                limit=self._batch_size,
            )
        return records[: self._batch_size]

    def _snapshot(self, seed: DebugRunLiveSeed) -> DebugLiveSnapshot:
        run = seed.run
        return DebugLiveSnapshot(
            run=run,
            cursor=encode_debug_live_cursor(
                DebugLiveCursor(
                    debug_run_id=run.debug_run_id,
                    after_seq=seed.head_seq,
                )
            ),
            latest_event=(
                self._event(seed.latest_event) if seed.latest_event is not None else None
            ),
            observed_at=utc_now(),
        )

    @staticmethod
    def _event(event: DebugRunEvent) -> DebugLiveEvent:
        return DebugLiveEvent(
            event_id=event.id,
            debug_run_id=event.debug_run_id,
            seq=event.seq,
            event_type=event.event_type,
            lifecycle=event.lifecycle,
            outcome=event.outcome,
            snapshot_status=event.snapshot_status,
            data=DebugLiveService._safe_payload(event),
            occurred_at=event.occurred_at,
            cursor=encode_debug_live_cursor(
                DebugLiveCursor(
                    debug_run_id=event.debug_run_id,
                    after_seq=event.seq,
                )
            ),
        )

    @staticmethod
    def _safe_payload(event: DebugRunEvent) -> dict[str, JsonValue]:
        allowed_keys = _LIVE_EVENT_PAYLOAD_KEYS.get(event.event_type, ())
        return {key: event.payload[key] for key in allowed_keys if key in event.payload}
