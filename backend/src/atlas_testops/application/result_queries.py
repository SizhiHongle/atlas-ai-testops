"""Snapshot-explicit, read-only Result Center queries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.access import ActorContext
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.result import (
    FailureClusterItem,
    FailureClusterPage,
    ResultClusterCursor,
    ResultSnapshotSelection,
    TaskResultSnapshot,
    TaskResultView,
    UnitResolutionRevision,
    decode_result_cluster_cursor,
    encode_result_cluster_cursor,
)
from atlas_testops.domain.task import ExecutionUnit, TaskRun
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


class ResultQueryService:
    """Expose immutable Task, Unit, Cluster, and Gate Result projections."""

    def __init__(
        self,
        database: Database,
        *,
        result_repository: ResultFactRepository | None = None,
        task_repository: TaskRunRepository | None = None,
    ) -> None:
        self._database = database
        self._results = result_repository or ResultFactRepository()
        self._tasks = task_repository or TaskRunRepository()

    async def get_task_result(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        snapshot_id: UUID | None,
    ) -> TaskResultView:
        """Read latest or one exact Snapshot without silently crossing revisions."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            if snapshot_id is None:
                snapshot = await self._results.get_latest_snapshot(connection, run.id)
                selection = ResultSnapshotSelection.LATEST
            else:
                snapshot = await self._results.get_snapshot_by_id(connection, snapshot_id)
                selection = ResultSnapshotSelection.EXACT
            snapshot = self._require_snapshot(snapshot, run)
            gate = await self._results.get_latest_task_gate_for_snapshot(
                connection,
                snapshot.id,
            )
            return TaskResultView(
                task_run_id=run.id,
                selection=selection,
                result_snapshot=snapshot,
                task_gate_decision=gate,
                projection_watermark=snapshot.projection_watermark,
            )

    async def get_unit_resolution(
        self,
        actor: ActorContext,
        execution_unit_id: UUID,
        *,
        revision: int | None,
    ) -> UnitResolutionRevision:
        """Read the latest or one exact append-only Unit Resolution revision."""

        if revision is not None and revision < 1:
            raise _invalid_request("Unit Resolution revision 必须大于 0。")
        async with self._database.transaction(actor.database_context()) as connection:
            unit = await self._require_unit(connection, actor, execution_unit_id)
            if revision is None:
                resolution = await self._results.get_latest_resolution(
                    connection,
                    unit.id,
                )
            else:
                resolution = await self._results.get_resolution_revision(
                    connection,
                    execution_unit_id=unit.id,
                    revision=revision,
                )
            if (
                resolution is None
                or resolution.execution_unit_id != unit.id
                or resolution.task_run_id != unit.task_run_id
                or resolution.project_id != unit.project_id
                or resolution.tenant_id != unit.tenant_id
            ):
                raise _not_found("Unit Resolution 不存在或不可见。")
            return resolution

    async def list_snapshot_clusters(
        self,
        actor: ActorContext,
        result_snapshot_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> FailureClusterPage:
        """List a stable as-of Cluster page with latest judgments at that fence."""

        if not 1 <= limit <= 100:
            raise _invalid_request("分页大小必须在 1 到 100 之间。")
        decoded = decode_result_cluster_cursor(
            cursor,
            expected_snapshot_id=result_snapshot_id,
        )
        async with self._database.transaction(actor.database_context()) as connection:
            snapshot = await self._results.get_snapshot_by_id(
                connection,
                result_snapshot_id,
            )
            if snapshot is None:
                raise _not_found("Result Snapshot 不存在或不可见。")
            run = await self._require_run(connection, actor, snapshot.task_run_id)
            snapshot = self._require_snapshot(snapshot, run)
            database_now = await _database_now(connection)
            as_of = decoded.as_of if decoded is not None else database_now
            if as_of < snapshot.created_at or as_of > database_now:
                raise _invalid_request("Cluster Cursor 的 asOf 超出 Snapshot 可查询范围。")
            records = await self._results.list_failure_clusters_page(
                connection,
                result_snapshot_id=snapshot.id,
                as_of=as_of,
                after_fingerprint=(
                    decoded.fingerprint if decoded is not None else None
                ),
                after_failure_cluster_id=(
                    decoded.failure_cluster_id if decoded is not None else None
                ),
                after_cluster_revision_id=(
                    decoded.cluster_revision_id if decoded is not None else None
                ),
                limit=limit + 1,
            )

        selected = records[:limit]
        items = tuple(
            FailureClusterItem(cluster=cluster, classification=classification)
            for cluster, classification in selected
        )
        next_cursor = None
        if len(records) > limit and items:
            last = items[-1].cluster
            next_cursor = encode_result_cluster_cursor(
                ResultClusterCursor(
                    result_snapshot_id=snapshot.id,
                    as_of=as_of,
                    fingerprint=last.fingerprint,
                    failure_cluster_id=last.failure_cluster_id,
                    cluster_revision_id=last.id,
                )
            )
        return FailureClusterPage(
            result_snapshot_id=snapshot.id,
            as_of=as_of,
            projection_watermark=snapshot.projection_watermark,
            items=items,
            next_cursor=next_cursor,
        )

    async def _require_run(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        task_run_id: UUID,
    ) -> TaskRun:
        run = await self._tasks.get_run(connection, task_run_id)
        if run is None or not actor.can_read_project(run.project_id):
            raise _not_found("TaskRun 不存在或不可见。")
        return run

    async def _require_unit(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        execution_unit_id: UUID,
    ) -> ExecutionUnit:
        unit = await self._tasks.get_unit(connection, execution_unit_id)
        if unit is None or not actor.can_read_project(unit.project_id):
            raise _not_found("ExecutionUnit 不存在或不可见。")
        return unit

    @staticmethod
    def _require_snapshot(
        snapshot: TaskResultSnapshot | None,
        run: TaskRun,
    ) -> TaskResultSnapshot:
        if (
            snapshot is None
            or snapshot.task_run_id != run.id
            or snapshot.tenant_id != run.tenant_id
            or snapshot.project_id != run.project_id
            or snapshot.manifest_hash != run.manifest_hash
        ):
            raise _not_found("Result Snapshot 不存在或不属于该 TaskRun。")
        return snapshot


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database transaction timestamp is unavailable")
    return datetime.fromisoformat(str(row["observed_at"]))


def _not_found(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="Result 资源不存在",
        detail=detail,
        status_code=404,
    )


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="Result 查询无效",
        detail=detail,
        status_code=400,
    )
