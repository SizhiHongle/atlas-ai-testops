"""Read-only TaskRun control-plane projections."""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.access import ActorContext
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.task import (
    ExecutionUnit,
    ExecutionUnitPage,
    TaskExecutionEventPage,
    TaskRun,
    TaskRunManifest,
    TaskRunPage,
    UnitAttemptPage,
)
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


class TaskRunQueryService:
    """Expose bounded Task execution reads without implying runtime control."""

    def __init__(
        self,
        database: Database,
        *,
        task_run_repository: TaskRunRepository | None = None,
        platform_repository: PlatformRepository | None = None,
    ) -> None:
        self._database = database
        self._runs = task_run_repository or TaskRunRepository()
        self._platform = platform_repository or PlatformRepository()

    async def list_for_project(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TaskRunPage:
        """List visible TaskRuns with stable requested-time keyset pagination."""

        self._validate_limit(limit)
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_project(connection, actor, project_id)
            records = await self._runs.list_runs(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit + 1,
            )
        items = records[:limit]
        next_cursor = (
            next_time_cursor(items[-1].requested_at, items[-1].id)
            if len(records) > limit and items
            else None
        )
        return TaskRunPage(items=items, next_cursor=next_cursor)

    async def get(self, actor: ActorContext, task_run_id: UUID) -> TaskRun:
        """Read one visible TaskRun projection."""

        async with self._database.transaction(actor.database_context()) as connection:
            return await self._require_run(connection, actor, task_run_id)

    async def get_manifest(
        self,
        actor: ActorContext,
        task_run_id: UUID,
    ) -> TaskRunManifest:
        """Read the immutable Run Manifest after enforcing parent visibility."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            manifest = await self._runs.get_manifest(connection, run.id)
            if manifest is None:
                raise RuntimeError("stored TaskRun is missing its immutable manifest")
            return manifest

    async def list_units(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        after_ordinal: int,
        limit: int,
    ) -> ExecutionUnitPage:
        """List ExecutionUnits in immutable manifest order."""

        self._validate_forward_page(after_ordinal, limit)
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            records = await self._runs.list_units_page(
                connection,
                task_run_id=run.id,
                after_ordinal=after_ordinal,
                limit=limit + 1,
            )
        items = records[:limit]
        next_after_ordinal = (
            items[-1].ordinal if len(records) > limit and items else None
        )
        return ExecutionUnitPage(
            items=items,
            next_after_ordinal=next_after_ordinal,
        )

    async def list_attempts(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        execution_unit_id: UUID,
        *,
        after_attempt_number: int,
        limit: int,
    ) -> UnitAttemptPage:
        """List attempts only when the Unit belongs to the visible TaskRun."""

        self._validate_forward_page(after_attempt_number, limit)
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            unit = await self._require_unit(connection, execution_unit_id, run.id)
            records = await self._runs.list_attempts_page(
                connection,
                execution_unit_id=unit.id,
                after_attempt_number=after_attempt_number,
                limit=limit + 1,
            )
        items = records[:limit]
        next_after_attempt_number = (
            items[-1].attempt_number if len(records) > limit and items else None
        )
        return UnitAttemptPage(
            items=items,
            next_after_attempt_number=next_after_attempt_number,
        )

    async def list_events(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        after_seq: int,
        limit: int,
    ) -> TaskExecutionEventPage:
        """Replay monotonic Task execution events after one acknowledged sequence."""

        self._validate_forward_page(after_seq, limit)
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            records = await self._runs.list_events(
                connection,
                task_run_id=run.id,
                after_seq=after_seq,
                limit=limit + 1,
            )
        items = records[:limit]
        next_after_seq = items[-1].seq if len(records) > limit and items else None
        return TaskExecutionEventPage(items=items, next_after_seq=next_after_seq)

    async def _require_project(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        project_id: UUID,
    ) -> None:
        project = await self._platform.get_project(connection, project_id)
        if project is None or not actor.can_read_project(project_id):
            raise self._not_found("Project 不存在或不可见。")

    async def _require_run(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        task_run_id: UUID,
    ) -> TaskRun:
        run = await self._runs.get_run(connection, task_run_id)
        if run is None or not actor.can_read_project(run.project_id):
            raise self._not_found("TaskRun 不存在或不可见。")
        return run

    async def _require_unit(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
        task_run_id: UUID,
    ) -> ExecutionUnit:
        unit = await self._runs.get_unit(connection, execution_unit_id)
        if unit is None or unit.task_run_id != task_run_id:
            raise self._not_found("ExecutionUnit 不存在或不属于该 TaskRun。")
        return unit

    @classmethod
    def _validate_forward_page(cls, after_value: int, limit: int) -> None:
        if after_value < 0:
            raise cls._invalid_request("分页起点不能小于 0。")
        cls._validate_limit(limit)

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if not 1 <= limit <= 100:
            raise TaskRunQueryService._invalid_request("分页大小必须在 1 到 100 之间。")

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _invalid_request(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="分页请求无效",
            detail=detail,
            status_code=400,
        )
