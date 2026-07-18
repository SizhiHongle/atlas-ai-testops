"""PostgreSQL repository for immutable task execution roots and events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskPlan,
    TaskPlanVersion,
    TaskRun,
    TaskRunManifest,
    UnitAttempt,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)

# Bound this synchronous 2N-insert path without shrinking the manifest protocol limit.
MAX_INITIAL_EXECUTION_UNITS = 64

TASK_PLAN_COLUMNS = (
    "id, tenant_id, project_id, task_key, name, status, created_by, revision, "
    "created_at, updated_at"
)
TASK_PLAN_VERSION_COLUMNS = (
    "id, tenant_id, project_id, task_plan_id, schema_version, version, version_ref, "
    "pinned_case_version_ids, matrix, profile_refs, policy_digests, content_digest, "
    "published_by, published_at, revision, created_at, updated_at"
)
TASK_RUN_COLUMNS = (
    "id, tenant_id, project_id, task_plan_version_id, manifest_hash, trigger_source, "
    "trigger_fingerprint, request_digest, materialization_state, materialized_unit_count, "
    "materialized_first_attempt_count, materialization_sealed_at, rerun_of_task_run_id, "
    "rerun_selection_mode, lifecycle, quality, hygiene, requested_by, temporal_namespace, "
    "temporal_workflow_id, requested_at, queued_at, started_at, finalized_at, "
    "cleanup_resolved_at, closed_at, revision, created_at, updated_at"
)
TASK_RUN_MANIFEST_COLUMNS = (
    "task_run_id, tenant_id, project_id, task_plan_version_id, schema_version, "
    "trigger_source, trigger_fingerprint, iteration_id, units, policy_digests, "
    "retry_policy, compiler_version, manifest_hash"
)
EXECUTION_UNIT_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, manifest_hash, unit_key, ordinal, "
    "case_version_id, execution_profile_version_id, fixture_blueprint_version_id, "
    "identity_profile_version_id, environment_id, browser_profile_version_id, "
    "data_profile_version_id, parameter_digest, dependency_digest, "
    "lifecycle, quality, hygiene, started_at, "
    "finalized_at, cleanup_resolved_at, closed_at, revision, created_at, updated_at"
)
UNIT_ATTEMPT_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, manifest_hash, "
    "unit_key, case_version_id, attempt_number, lifecycle, quality, hygiene, "
    "temporal_namespace, temporal_workflow_id, queued_at, execution_deadline, "
    "started_at, finalized_at, "
    "cleanup_resolved_at, closed_at, revision, created_at, updated_at"
)
TASK_EXECUTION_EVENT_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, seq, "
    "event_type, lifecycle, quality, hygiene, payload, occurred_at"
)


class ImmutableCreateKind(StrEnum):
    """Distinguish a new immutable fact from a returned conflict candidate."""

    CREATED = "CREATED"
    EXISTING = "EXISTING"


class ImmutableFactConflictError(RuntimeError):
    """Signal that an immutable natural key already stores different content."""


@dataclass(frozen=True, slots=True)
class ImmutableCreateResult[FactT]:
    """Expose immutable insert conflicts without overwriting either version."""

    kind: ImmutableCreateKind
    fact: FactT


@dataclass(frozen=True, slots=True)
class TaskRunCreateResult:
    """Return the stored manifest so callers can verify trigger-fingerprint replay."""

    kind: ImmutableCreateKind
    task_run: TaskRun
    manifest: TaskRunManifest


class TaskRunRepository:
    """Persist task facts without handling authorization or external orchestration."""

    async def create_task_plan(
        self,
        connection: AsyncConnection[DictRow],
        plan: TaskPlan,
    ) -> ImmutableCreateResult[TaskPlan]:
        """Insert a stable task root or return the row that blocked the insert."""

        cursor = await connection.execute(
            f"""
            insert into atlas.task_plan (
              id, tenant_id, project_id, task_key, name, status, created_by,
              revision, created_at, updated_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {TASK_PLAN_COLUMNS}
            """,
            (
                plan.id,
                plan.tenant_id,
                plan.project_id,
                plan.task_key,
                plan.name,
                plan.status,
                plan.created_by,
                plan.revision,
                plan.created_at,
                plan.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                TaskPlan.model_validate(row),
            )
        existing = await self._get_task_plan_conflict(connection, plan)
        if existing is None:
            raise RuntimeError("task plan conflict did not resolve to a stored row")
        if existing != plan:
            raise ImmutableFactConflictError("task plan identity already stores different content")
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def create_task_plan_version(
        self,
        connection: AsyncConnection[DictRow],
        version: TaskPlanVersion,
    ) -> ImmutableCreateResult[TaskPlanVersion]:
        """Insert a published immutable version or return its conflict candidate."""

        cursor = await connection.execute(
            f"""
            insert into atlas.task_plan_version (
              id, tenant_id, project_id, task_plan_id, schema_version,
              version, version_ref, pinned_case_version_ids, matrix,
              profile_refs, policy_digests, content_digest, published_by,
              published_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            on conflict do nothing
            returning {TASK_PLAN_VERSION_COLUMNS}
            """,
            (
                version.id,
                version.tenant_id,
                version.project_id,
                version.task_plan_id,
                version.schema_version,
                version.version,
                version.version_ref,
                list(version.pinned_case_version_ids),
                Jsonb(version.matrix.model_dump(mode="json", by_alias=True)),
                Jsonb(version.profile_refs.model_dump(mode="json", by_alias=True)),
                Jsonb(version.policy_digests),
                version.content_digest,
                version.published_by,
                version.published_at,
                version.revision,
                version.created_at,
                version.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                TaskPlanVersion.model_validate(row),
            )
        existing = await self._get_task_plan_version_conflict(connection, version)
        if existing is None:
            raise RuntimeError("task plan version conflict did not resolve to a stored row")
        if existing != version:
            raise ImmutableFactConflictError(
                "task plan version identity already stores different immutable content"
            )
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def create_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run: TaskRun,
        manifest: TaskRunManifest,
        units: tuple[ExecutionUnit, ...],
        first_attempts: tuple[UnitAttempt, ...],
    ) -> TaskRunCreateResult:
        """Insert one complete initial aggregate in a bounded caller transaction."""

        if (
            len(manifest.units) > MAX_INITIAL_EXECUTION_UNITS
            or len(units) > MAX_INITIAL_EXECUTION_UNITS
        ):
            raise ValueError(
                "P5-00B1 synchronous initial materialization is limited to "
                f"{MAX_INITIAL_EXECUTION_UNITS} ExecutionUnits"
            )
        ordered_units, ordered_attempts = self._validate_initial_aggregate(
            task_run=task_run,
            manifest=manifest,
            units=units,
            first_attempts=first_attempts,
        )
        expected_request_digest = manifest.recompute_request_digest()
        if task_run.request_digest != expected_request_digest:
            raise ValueError("TaskRun requestDigest must match its logical Run Manifest input")
        if task_run.materialization_state is not TaskMaterializationState.MATERIALIZING:
            raise ValueError("a new TaskRun must begin in MATERIALIZING state")
        if task_run.temporal_namespace is None or task_run.temporal_workflow_id is None:
            raise ValueError("a new TaskRun requires a deterministic Temporal identity")
        if task_run.temporal_workflow_id != task_run_workflow_id(
            tenant_id=task_run.tenant_id,
            task_run_id=task_run.id,
        ):
            raise ValueError("TaskRun temporalWorkflowId is not deterministic")
        for attempt in ordered_attempts:
            if (
                attempt.temporal_namespace != task_run.temporal_namespace
                or attempt.temporal_workflow_id
                != unit_attempt_workflow_id(
                    tenant_id=attempt.tenant_id,
                    unit_attempt_id=attempt.id,
                )
            ):
                raise ValueError(
                    "every first UnitAttempt requires a deterministic Temporal identity "
                    "in the TaskRun namespace"
                )
        task_plan_version = await self.get_task_plan_version(
            connection,
            task_run.task_plan_version_id,
        )
        if task_plan_version is None:
            raise ValueError(
                "TaskRun TaskPlanVersion is missing or outside the current tenant/project scope"
            )
        self._validate_manifest_provenance(
            task_run=task_run,
            manifest=manifest,
            task_plan_version=task_plan_version,
        )
        run_cursor = await connection.execute(
            f"""
            insert into atlas.task_run (
              id, tenant_id, project_id, task_plan_version_id, manifest_hash,
              trigger_source, trigger_fingerprint, request_digest,
              materialization_state, materialized_unit_count,
              materialized_first_attempt_count, materialization_sealed_at,
              rerun_of_task_run_id, rerun_selection_mode, lifecycle, quality,
              hygiene, requested_by, temporal_namespace, temporal_workflow_id,
              requested_at, queued_at, started_at, finalized_at,
              cleanup_resolved_at, closed_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s
            )
            on conflict (tenant_id, trigger_source, trigger_fingerprint) do nothing
            returning {TASK_RUN_COLUMNS}
            """,
            (
                task_run.id,
                task_run.tenant_id,
                task_run.project_id,
                task_run.task_plan_version_id,
                task_run.manifest_hash,
                task_run.trigger_source,
                task_run.trigger_fingerprint,
                task_run.request_digest,
                task_run.materialization_state,
                task_run.materialized_unit_count,
                task_run.materialized_first_attempt_count,
                task_run.materialization_sealed_at,
                task_run.rerun_of_task_run_id,
                task_run.rerun_selection_mode,
                task_run.lifecycle,
                task_run.quality,
                task_run.hygiene,
                task_run.requested_by,
                task_run.temporal_namespace,
                task_run.temporal_workflow_id,
                task_run.requested_at,
                task_run.queued_at,
                task_run.started_at,
                task_run.finalized_at,
                task_run.cleanup_resolved_at,
                task_run.closed_at,
                task_run.revision,
                task_run.created_at,
                task_run.updated_at,
            ),
        )
        run_row = await run_cursor.fetchone()
        if run_row is None:
            existing_run = await self._get_task_run_conflict(connection, task_run)
            if existing_run is None:
                raise RuntimeError("task run conflict did not resolve to a stored row")
            existing_manifest = await self.get_manifest(connection, existing_run.id)
            if existing_manifest is None:
                raise RuntimeError("stored task run is missing its immutable manifest")
            if (
                existing_run.request_digest != expected_request_digest
                or existing_manifest.recompute_request_digest() != expected_request_digest
                or existing_run.rerun_of_task_run_id != task_run.rerun_of_task_run_id
                or existing_run.rerun_selection_mode != task_run.rerun_selection_mode
            ):
                raise ImmutableFactConflictError(
                    "task run identity already stores different immutable run input"
                )
            return TaskRunCreateResult(
                ImmutableCreateKind.EXISTING,
                existing_run,
                existing_manifest,
            )

        stored_run = TaskRun.model_validate(run_row)
        stored_manifest = await self._insert_manifest(connection, manifest)
        for unit in ordered_units:
            await self._insert_unit(connection, unit)
        for attempt in ordered_attempts:
            await self._insert_attempt(connection, attempt)
        sealed_cursor = await connection.execute(
            f"""
            select {TASK_RUN_COLUMNS}
            from atlas.seal_task_run_materialization(%s, %s)
            """,
            (task_run.id, task_run.revision),
        )
        sealed_row = await sealed_cursor.fetchone()
        if sealed_row is None:
            raise RuntimeError("task run materialization seal did not return a row")
        stored_run = TaskRun.model_validate(sealed_row)
        return TaskRunCreateResult(
            ImmutableCreateKind.CREATED,
            stored_run,
            stored_manifest,
        )

    async def get_task_plan(
        self,
        connection: AsyncConnection[DictRow],
        task_plan_id: UUID,
    ) -> TaskPlan | None:
        cursor = await connection.execute(
            f"select {TASK_PLAN_COLUMNS} from atlas.task_plan where id = %s",
            (task_plan_id,),
        )
        row = await cursor.fetchone()
        return TaskPlan.model_validate(row) if row is not None else None

    async def get_task_plan_version(
        self,
        connection: AsyncConnection[DictRow],
        task_plan_version_id: UUID,
    ) -> TaskPlanVersion | None:
        cursor = await connection.execute(
            f"""
            select {TASK_PLAN_VERSION_COLUMNS}
            from atlas.task_plan_version
            where id = %s
            """,
            (task_plan_version_id,),
        )
        row = await cursor.fetchone()
        return TaskPlanVersion.model_validate(row) if row is not None else None

    async def get_run(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskRun | None:
        cursor = await connection.execute(
            f"select {TASK_RUN_COLUMNS} from atlas.task_run where id = %s",
            (task_run_id,),
        )
        row = await cursor.fetchone()
        return TaskRun.model_validate(row) if row is not None else None

    async def list_runs(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskRun, ...]:
        """List one Project's TaskRuns with stable requested-time keyset pagination."""

        params: tuple[object, ...]
        if cursor is None:
            query = f"""
                select {TASK_RUN_COLUMNS}
                from atlas.task_run
                where project_id = %s
                order by requested_at desc, id desc
                limit %s
            """
            params = (project_id, limit)
        else:
            query = f"""
                select {TASK_RUN_COLUMNS}
                from atlas.task_run
                where project_id = %s
                  and (requested_at, id) < (%s, %s)
                order by requested_at desc, id desc
                limit %s
            """
            params = (project_id, cursor.created_at, cursor.id, limit)
        result = await connection.execute(query, params)
        return tuple(TaskRun.model_validate(row) for row in await result.fetchall())

    async def get_run_for_update(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskRun | None:
        """Lock one TaskRun before any Unit or Attempt in a worker transaction."""

        await self.lock_execution_chain(
            connection,
            task_run_id=task_run_id,
        )
        return await self.get_run(connection, task_run_id)

    async def lock_execution_chain(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        execution_unit_id: UUID | None = None,
        unit_attempt_id: UUID | None = None,
    ) -> None:
        """Acquire the trusted tenant-scoped Run-to-Attempt lock chain."""

        cursor = await connection.execute(
            """
            select atlas.lock_task_execution_chain(%s, %s, %s)
            """,
            (task_run_id, execution_unit_id, unit_attempt_id),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("trusted Task execution lock function returned no row")

    async def get_manifest(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskRunManifest | None:
        cursor = await connection.execute(
            f"""
            select {TASK_RUN_MANIFEST_COLUMNS}
            from atlas.task_run_manifest
            where task_run_id = %s
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        return TaskRunManifest.model_validate(row) if row is not None else None

    async def get_unit(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> ExecutionUnit | None:
        cursor = await connection.execute(
            f"select {EXECUTION_UNIT_COLUMNS} from atlas.execution_unit where id = %s",
            (execution_unit_id,),
        )
        row = await cursor.fetchone()
        return ExecutionUnit.model_validate(row) if row is not None else None

    async def get_unit_for_update(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> ExecutionUnit | None:
        """Lock one ExecutionUnit after its parent TaskRun is locked."""

        candidate = await self.get_unit(connection, execution_unit_id)
        if candidate is None:
            return None
        await self.lock_execution_chain(
            connection,
            task_run_id=candidate.task_run_id,
            execution_unit_id=execution_unit_id,
        )
        return await self.get_unit(connection, execution_unit_id)

    async def list_units(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> tuple[ExecutionUnit, ...]:
        cursor = await connection.execute(
            f"""
            select {EXECUTION_UNIT_COLUMNS}
            from atlas.execution_unit
            where task_run_id = %s
            order by ordinal, id
            """,
            (task_run_id,),
        )
        return tuple(ExecutionUnit.model_validate(row) for row in await cursor.fetchall())

    async def list_units_page(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        after_ordinal: int,
        limit: int,
    ) -> tuple[ExecutionUnit, ...]:
        cursor = await connection.execute(
            f"""
            select {EXECUTION_UNIT_COLUMNS}
            from atlas.execution_unit
            where task_run_id = %s and ordinal > %s
            order by ordinal, id
            limit %s
            """,
            (task_run_id, after_ordinal, limit),
        )
        return tuple(ExecutionUnit.model_validate(row) for row in await cursor.fetchall())

    async def get_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> UnitAttempt | None:
        cursor = await connection.execute(
            f"select {UNIT_ATTEMPT_COLUMNS} from atlas.unit_attempt where id = %s",
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return UnitAttempt.model_validate(row) if row is not None else None

    async def get_attempt_for_update(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> UnitAttempt | None:
        """Lock one UnitAttempt after its TaskRun and ExecutionUnit are locked."""

        candidate = await self.get_attempt(connection, unit_attempt_id)
        if candidate is None:
            return None
        await self.lock_execution_chain(
            connection,
            task_run_id=candidate.task_run_id,
            execution_unit_id=candidate.execution_unit_id,
            unit_attempt_id=unit_attempt_id,
        )
        return await self.get_attempt(connection, unit_attempt_id)

    async def list_first_attempts(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> tuple[UnitAttempt, ...]:
        """Load every first Attempt in its immutable Unit ordinal order."""

        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from (
              select attempt.*, unit.ordinal as dispatch_ordinal
              from atlas.unit_attempt attempt
              join atlas.execution_unit unit
                on unit.id = attempt.execution_unit_id
               and unit.task_run_id = attempt.task_run_id
               and unit.tenant_id = attempt.tenant_id
               and unit.project_id = attempt.project_id
              where attempt.task_run_id = %s
                and attempt.attempt_number = 1
            ) first_attempt
            order by dispatch_ordinal, id
            """,
            (task_run_id,),
        )
        return tuple(UnitAttempt.model_validate(row) for row in await cursor.fetchall())

    async def create_attempt(
        self,
        connection: AsyncConnection[DictRow],
        attempt: UnitAttempt,
    ) -> ImmutableCreateResult[UnitAttempt]:
        """Append a new attempt while making exact request replay idempotent."""

        if (
            attempt.temporal_namespace is None
            or attempt.temporal_workflow_id
            != unit_attempt_workflow_id(
                tenant_id=attempt.tenant_id,
                unit_attempt_id=attempt.id,
            )
        ):
            raise ValueError("UnitAttempt requires a deterministic Temporal identity")
        existing = await self._get_attempt_conflict(connection, attempt)
        if existing is not None:
            return self._resolve_attempt_replay(attempt, existing)
        if attempt.attempt_number <= 1:
            raise ValueError("create_attempt only appends retry attempts after the first Attempt")
        await self._require_retry_prerequisites(connection, attempt)
        cursor = await self._insert_attempt(connection, attempt, ignore_conflict=True)
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                UnitAttempt.model_validate(row),
            )
        existing = await self._get_attempt_conflict(connection, attempt)
        if existing is None:
            raise RuntimeError("unit attempt conflict did not resolve to a stored row")
        return self._resolve_attempt_replay(attempt, existing)

    async def list_attempts(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> tuple[UnitAttempt, ...]:
        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.unit_attempt
            where execution_unit_id = %s
            order by attempt_number, id
            """,
            (execution_unit_id,),
        )
        return tuple(UnitAttempt.model_validate(row) for row in await cursor.fetchall())

    async def get_attempt_by_number(
        self,
        connection: AsyncConnection[DictRow],
        *,
        execution_unit_id: UUID,
        attempt_number: int,
    ) -> UnitAttempt | None:
        """Read one exact physical Attempt number within a Unit."""

        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.unit_attempt
            where execution_unit_id = %s and attempt_number = %s
            limit 1
            """,
            (execution_unit_id, attempt_number),
        )
        row = await cursor.fetchone()
        return UnitAttempt.model_validate(row) if row is not None else None

    async def list_attempts_for_run(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> tuple[UnitAttempt, ...]:
        """List every immutable Attempt in deterministic Unit/Attempt order."""

        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.unit_attempt
            where task_run_id = %s
            order by execution_unit_id, attempt_number, id
            """,
            (task_run_id,),
        )
        return tuple(UnitAttempt.model_validate(row) for row in await cursor.fetchall())

    async def count_retry_attempts(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> int:
        """Count physical Attempts beyond each Unit's immutable first Attempt."""

        cursor = await connection.execute(
            """
            select count(*)::integer
            from atlas.unit_attempt
            where task_run_id = %s and attempt_number > 1
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("retry Attempt count query returned no row")
        return int(row["count"])

    async def list_attempts_page(
        self,
        connection: AsyncConnection[DictRow],
        *,
        execution_unit_id: UUID,
        after_attempt_number: int,
        limit: int,
    ) -> tuple[UnitAttempt, ...]:
        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.unit_attempt
            where execution_unit_id = %s and attempt_number > %s
            order by attempt_number, id
            limit %s
            """,
            (execution_unit_id, after_attempt_number, limit),
        )
        return tuple(UnitAttempt.model_validate(row) for row in await cursor.fetchall())

    async def append_event(
        self,
        connection: AsyncConnection[DictRow],
        event: TaskExecutionEvent,
    ) -> ImmutableCreateResult[TaskExecutionEvent]:
        """Append a supplied monotonic event or return the conflicting immutable fact."""

        cursor = await connection.execute(
            f"""
            insert into atlas.task_run_event (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, seq, event_type, lifecycle, quality, hygiene,
              payload, occurred_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s
            )
            on conflict do nothing
            returning {TASK_EXECUTION_EVENT_COLUMNS}
            """,
            (
                event.id,
                event.tenant_id,
                event.project_id,
                event.task_run_id,
                event.execution_unit_id,
                event.unit_attempt_id,
                event.seq,
                event.event_type,
                event.lifecycle,
                event.quality,
                event.hygiene,
                Jsonb(event.payload),
                event.occurred_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                TaskExecutionEvent.model_validate(row),
            )
        existing = await self._get_event_conflict(connection, event)
        if existing is None:
            raise RuntimeError("task execution event conflict did not resolve to a stored row")
        if existing != event:
            raise ImmutableFactConflictError(
                "task execution event identity already stores different immutable content"
            )
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def list_events(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        after_seq: int,
        limit: int,
    ) -> tuple[TaskExecutionEvent, ...]:
        cursor = await connection.execute(
            f"""
            select {TASK_EXECUTION_EVENT_COLUMNS}
            from atlas.task_run_event
            where task_run_id = %s and seq > %s
            order by seq
            limit %s
            """,
            (task_run_id, after_seq, limit),
        )
        return tuple(TaskExecutionEvent.model_validate(row) for row in await cursor.fetchall())

    async def _get_task_plan_conflict(
        self,
        connection: AsyncConnection[DictRow],
        plan: TaskPlan,
    ) -> TaskPlan | None:
        cursor = await connection.execute(
            f"""
            select {TASK_PLAN_COLUMNS}
            from atlas.task_plan
            where id = %s
               or (
                 tenant_id = %s and project_id = %s and task_key = %s
               )
            order by (id = %s) desc
            limit 1
            """,
            (plan.id, plan.tenant_id, plan.project_id, plan.task_key, plan.id),
        )
        row = await cursor.fetchone()
        return TaskPlan.model_validate(row) if row is not None else None

    async def _get_task_plan_version_conflict(
        self,
        connection: AsyncConnection[DictRow],
        version: TaskPlanVersion,
    ) -> TaskPlanVersion | None:
        cursor = await connection.execute(
            f"""
            select {TASK_PLAN_VERSION_COLUMNS}
            from atlas.task_plan_version
            where id = %s
               or (
                 tenant_id = %s and project_id = %s
                 and task_plan_id = %s and version = %s
               )
            order by (id = %s) desc
            limit 1
            """,
            (
                version.id,
                version.tenant_id,
                version.project_id,
                version.task_plan_id,
                version.version,
                version.id,
            ),
        )
        row = await cursor.fetchone()
        return TaskPlanVersion.model_validate(row) if row is not None else None

    async def _get_task_run_conflict(
        self,
        connection: AsyncConnection[DictRow],
        task_run: TaskRun,
    ) -> TaskRun | None:
        cursor = await connection.execute(
            f"""
            select {TASK_RUN_COLUMNS}
            from atlas.task_run
            where tenant_id = %s
              and trigger_source = %s
              and trigger_fingerprint = %s
            limit 1
            """,
            (
                task_run.tenant_id,
                task_run.trigger_source,
                task_run.trigger_fingerprint,
            ),
        )
        row = await cursor.fetchone()
        return TaskRun.model_validate(row) if row is not None else None

    async def _insert_manifest(
        self,
        connection: AsyncConnection[DictRow],
        manifest: TaskRunManifest,
    ) -> TaskRunManifest:
        cursor = await connection.execute(
            f"""
            insert into atlas.task_run_manifest (
              task_run_id, tenant_id, project_id, task_plan_version_id,
              schema_version, trigger_source, trigger_fingerprint, iteration_id,
              units, policy_digests, retry_policy, compiler_version, manifest_hash,
              unit_count
            ) values (
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s
            )
            returning {TASK_RUN_MANIFEST_COLUMNS}
            """,
            (
                manifest.task_run_id,
                manifest.tenant_id,
                manifest.project_id,
                manifest.task_plan_version_id,
                manifest.schema_version,
                manifest.trigger_source,
                manifest.trigger_fingerprint,
                manifest.iteration_id,
                Jsonb([unit.model_dump(mode="json", by_alias=True) for unit in manifest.units]),
                Jsonb(manifest.policy_digests),
                (
                    Jsonb(manifest.retry_policy.model_dump(mode="json", by_alias=True))
                    if manifest.retry_policy is not None
                    else None
                ),
                manifest.compiler_version,
                manifest.manifest_hash,
                len(manifest.units),
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("task run manifest insert did not return a row")
        return TaskRunManifest.model_validate(row)

    async def _insert_unit(
        self,
        connection: AsyncConnection[DictRow],
        unit: ExecutionUnit,
    ) -> None:
        await connection.execute(
            """
            insert into atlas.execution_unit (
              id, tenant_id, project_id, task_run_id, manifest_hash, unit_key,
              ordinal, case_version_id, execution_profile_version_id,
              fixture_blueprint_version_id, identity_profile_version_id,
              environment_id, browser_profile_version_id,
              data_profile_version_id, parameter_digest, dependency_digest,
              lifecycle, quality, hygiene, started_at, finalized_at,
              cleanup_resolved_at, closed_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                unit.id,
                unit.tenant_id,
                unit.project_id,
                unit.task_run_id,
                unit.manifest_hash,
                unit.unit_key,
                unit.ordinal,
                unit.case_version_id,
                unit.execution_profile_version_id,
                unit.fixture_blueprint_version_id,
                unit.identity_profile_version_id,
                unit.environment_id,
                unit.browser_profile_version_id,
                unit.data_profile_version_id,
                unit.parameter_digest,
                unit.dependency_digest,
                unit.lifecycle,
                unit.quality,
                unit.hygiene,
                unit.started_at,
                unit.finalized_at,
                unit.cleanup_resolved_at,
                unit.closed_at,
                unit.revision,
                unit.created_at,
                unit.updated_at,
            ),
        )

    async def _insert_attempt(
        self,
        connection: AsyncConnection[DictRow],
        attempt: UnitAttempt,
        *,
        ignore_conflict: bool = False,
    ) -> AsyncCursor[DictRow]:
        conflict_clause = (
            "on conflict (execution_unit_id, attempt_number) do nothing"
            if ignore_conflict
            else ""
        )
        returning_clause = f"returning {UNIT_ATTEMPT_COLUMNS}" if ignore_conflict else ""
        return await connection.execute(
            f"""
            insert into atlas.unit_attempt (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              manifest_hash, unit_key, case_version_id, attempt_number,
              lifecycle, quality, hygiene, temporal_namespace,
              temporal_workflow_id, queued_at,
              execution_deadline, started_at, finalized_at, cleanup_resolved_at,
              closed_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s
            )
            {conflict_clause}
            {returning_clause}
            """,
            (
                attempt.id,
                attempt.tenant_id,
                attempt.project_id,
                attempt.task_run_id,
                attempt.execution_unit_id,
                attempt.manifest_hash,
                attempt.unit_key,
                attempt.case_version_id,
                attempt.attempt_number,
                attempt.lifecycle,
                attempt.quality,
                attempt.hygiene,
                attempt.temporal_namespace,
                attempt.temporal_workflow_id,
                attempt.queued_at,
                attempt.execution_deadline,
                attempt.started_at,
                attempt.finalized_at,
                attempt.cleanup_resolved_at,
                attempt.closed_at,
                attempt.revision,
                attempt.created_at,
                attempt.updated_at,
            ),
        )

    async def _get_attempt_conflict(
        self,
        connection: AsyncConnection[DictRow],
        attempt: UnitAttempt,
    ) -> UnitAttempt | None:
        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.unit_attempt
            where execution_unit_id = %s and attempt_number = %s
            limit 1
            """,
            (attempt.execution_unit_id, attempt.attempt_number),
        )
        row = await cursor.fetchone()
        return UnitAttempt.model_validate(row) if row is not None else None

    @staticmethod
    async def _require_retry_prerequisites(
        connection: AsyncConnection[DictRow],
        attempt: UnitAttempt,
    ) -> None:
        """Preflight retry facts; the insert trigger owns authoritative row locks."""

        run_cursor = await connection.execute(
            """
            select
              run.materialization_state,
              run.lifecycle as task_run_lifecycle,
              run.temporal_namespace
            from atlas.task_run run
            where run.id = %s
              and run.tenant_id = %s
              and run.project_id = %s
            """,
            (
                attempt.task_run_id,
                attempt.tenant_id,
                attempt.project_id,
            ),
        )
        run_row = await run_cursor.fetchone()
        if run_row is None:
            raise ValueError("UnitAttempt retry parent scope is missing")
        if run_row["materialization_state"] != TaskMaterializationState.SEALED:
            raise ValueError("UnitAttempt retry requires a SEALED TaskRun")
        if run_row["task_run_lifecycle"] not in {
            ExecutionLifecycle.QUEUED,
            ExecutionLifecycle.RUNNING,
        }:
            raise ValueError("UnitAttempt retry requires a dispatchable TaskRun")
        if run_row["temporal_namespace"] != attempt.temporal_namespace:
            raise ValueError("UnitAttempt retry must use the TaskRun Temporal namespace")

        unit_cursor = await connection.execute(
            """
            select
              unit.lifecycle as execution_unit_lifecycle,
              previous.lifecycle as previous_attempt_lifecycle,
              previous.quality as previous_attempt_quality
            from atlas.execution_unit unit
            left join atlas.unit_attempt previous
              on previous.execution_unit_id = unit.id
             and previous.attempt_number = %s
            where unit.id = %s
              and unit.task_run_id = %s
              and unit.tenant_id = %s
              and unit.project_id = %s
            """,
            (
                attempt.attempt_number - 1,
                attempt.execution_unit_id,
                attempt.task_run_id,
                attempt.tenant_id,
                attempt.project_id,
            ),
        )
        unit_row = await unit_cursor.fetchone()
        if unit_row is None:
            raise ValueError("UnitAttempt retry ExecutionUnit scope is missing")
        if unit_row["execution_unit_lifecycle"] not in {
            ExecutionLifecycle.QUEUED,
            ExecutionLifecycle.RUNNING,
        }:
            raise ValueError("UnitAttempt retry requires a dispatchable ExecutionUnit")
        if (
            unit_row["previous_attempt_lifecycle"] != ExecutionLifecycle.CLOSED
            or unit_row["previous_attempt_quality"]
            not in {
                ExecutionQuality.FAILED,
                ExecutionQuality.BLOCKED,
                ExecutionQuality.INCONCLUSIVE,
                ExecutionQuality.INFRA_ERROR,
                ExecutionQuality.CANCELED,
            }
        ):
            raise ValueError("UnitAttempt retry requires one closed retryable previous Attempt")

    @classmethod
    def _resolve_attempt_replay(
        cls,
        requested: UnitAttempt,
        existing: UnitAttempt,
    ) -> ImmutableCreateResult[UnitAttempt]:
        if cls._attempt_creation_identity(existing) != cls._attempt_creation_identity(
            requested
        ):
            raise ImmutableFactConflictError(
                "unit attempt identity already stores different immutable content"
            )
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def _get_event_conflict(
        self,
        connection: AsyncConnection[DictRow],
        event: TaskExecutionEvent,
    ) -> TaskExecutionEvent | None:
        cursor = await connection.execute(
            f"""
            select {TASK_EXECUTION_EVENT_COLUMNS}
            from atlas.task_run_event
            where id = %s or (task_run_id = %s and seq = %s)
            order by (id = %s) desc
            limit 1
            """,
            (event.id, event.task_run_id, event.seq, event.id),
        )
        row = await cursor.fetchone()
        return TaskExecutionEvent.model_validate(row) if row is not None else None

    @staticmethod
    def _validate_initial_aggregate(
        *,
        task_run: TaskRun,
        manifest: TaskRunManifest,
        units: tuple[ExecutionUnit, ...],
        first_attempts: tuple[UnitAttempt, ...],
    ) -> tuple[tuple[ExecutionUnit, ...], tuple[UnitAttempt, ...]]:
        if (
            manifest.task_run_id != task_run.id
            or manifest.task_plan_version_id != task_run.task_plan_version_id
            or manifest.tenant_id != task_run.tenant_id
            or manifest.project_id != task_run.project_id
            or manifest.trigger_source is not task_run.trigger_source
            or manifest.trigger_fingerprint != task_run.trigger_fingerprint
            or manifest.manifest_hash != task_run.manifest_hash
        ):
            raise ValueError("TaskRunManifest must match the TaskRun root")

        ordered_units = tuple(sorted(units, key=lambda unit: (unit.ordinal, unit.id)))
        if tuple(unit.ordinal for unit in ordered_units) != tuple(range(1, len(ordered_units) + 1)):
            raise ValueError("ExecutionUnit ordinals must be contiguous from one")
        if len(ordered_units) != len(manifest.units):
            raise ValueError("ExecutionUnits must exactly match the manifest unit count")
        for unit, manifest_unit in zip(ordered_units, manifest.units, strict=True):
            if (
                unit.task_run_id != task_run.id
                or unit.tenant_id != task_run.tenant_id
                or unit.project_id != task_run.project_id
                or unit.manifest_hash != task_run.manifest_hash
            ):
                raise ValueError("ExecutionUnit must match the TaskRun scope")
            unit_identity = (
                unit.ordinal,
                unit.unit_key,
                unit.case_version_id,
                unit.execution_profile_version_id,
                unit.fixture_blueprint_version_id,
                unit.identity_profile_version_id,
                unit.environment_id,
                unit.browser_profile_version_id,
                unit.data_profile_version_id,
                unit.parameter_digest,
                unit.dependency_digest,
            )
            manifest_identity = (
                manifest_unit.ordinal,
                manifest_unit.unit_key,
                manifest_unit.case_version_id,
                manifest_unit.execution_profile_version_id,
                manifest_unit.fixture_blueprint_version_id,
                manifest_unit.identity_profile_version_id,
                manifest_unit.environment_id,
                manifest_unit.browser_profile_version_id,
                manifest_unit.data_profile_version_id,
                manifest_unit.parameter_digest,
                manifest_unit.dependency_digest,
            )
            if unit_identity != manifest_identity:
                raise ValueError("ExecutionUnit must match every exact Manifest reference")

        unit_ordinals = {unit.id: unit.ordinal for unit in ordered_units}
        if len(unit_ordinals) != len(ordered_units):
            raise ValueError("ExecutionUnit ids must be unique")
        ordered_attempts = tuple(
            sorted(
                first_attempts,
                key=lambda attempt: (
                    unit_ordinals.get(attempt.execution_unit_id, 2**63),
                    attempt.attempt_number,
                    attempt.id,
                ),
            )
        )
        if len(ordered_attempts) != len(ordered_units):
            raise ValueError("one initial UnitAttempt is required per ExecutionUnit")
        for unit, attempt in zip(ordered_units, ordered_attempts, strict=True):
            if (
                attempt.execution_unit_id != unit.id
                or attempt.attempt_number != 1
                or attempt.task_run_id != task_run.id
                or attempt.tenant_id != task_run.tenant_id
                or attempt.project_id != task_run.project_id
                or attempt.manifest_hash != task_run.manifest_hash
                or attempt.unit_key != unit.unit_key
                or attempt.case_version_id != unit.case_version_id
            ):
                raise ValueError("first UnitAttempt must exactly match its ExecutionUnit")
        return ordered_units, ordered_attempts

    @staticmethod
    def _validate_manifest_provenance(
        *,
        task_run: TaskRun,
        manifest: TaskRunManifest,
        task_plan_version: TaskPlanVersion,
    ) -> None:
        """Reject compiled references outside the stored published plan boundary."""

        if (
            task_plan_version.id != task_run.task_plan_version_id
            or task_plan_version.id != manifest.task_plan_version_id
            or task_plan_version.tenant_id != task_run.tenant_id
            or task_plan_version.tenant_id != manifest.tenant_id
            or task_plan_version.project_id != task_run.project_id
            or task_plan_version.project_id != manifest.project_id
        ):
            raise ValueError(
                "TaskRunManifest provenance must match the stored "
                "TaskPlanVersion id and tenant/project scope"
            )
        uncovered_policy_keys = tuple(
            key
            for key, digest in task_plan_version.policy_digests.items()
            if manifest.policy_digests.get(key) != digest
        )
        if uncovered_policy_keys:
            raise ValueError(
                "TaskRunManifest policyDigests must cover every stored "
                "TaskPlanVersion policy digest with the same value; "
                f"missing or mismatched keys: {', '.join(uncovered_policy_keys)}"
            )

        pinned_case_ids = set(task_plan_version.pinned_case_version_ids)
        environment_ids = set(task_plan_version.matrix.environment_ids)
        browser_profile_ids = set(task_plan_version.matrix.browser_profile_version_ids)
        identity_profile_ids = set(task_plan_version.matrix.identity_profile_version_ids)
        data_profile_ids = set(task_plan_version.matrix.data_profile_version_ids)
        case_profiles = {
            profile.case_version_id: profile
            for profile in task_plan_version.profile_refs.case_profiles
        }
        for unit in manifest.units:
            if unit.case_version_id not in pinned_case_ids:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} caseVersionId is not pinned "
                    "by the stored TaskPlanVersion"
                )
            if unit.environment_id not in environment_ids:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} environmentId is outside "
                    "the stored TaskPlanVersion matrix"
                )
            if unit.browser_profile_version_id not in browser_profile_ids:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} browserProfileVersionId is outside "
                    "the stored TaskPlanVersion matrix"
                )
            if unit.identity_profile_version_id not in identity_profile_ids:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} identityProfileVersionId is outside "
                    "the stored TaskPlanVersion matrix"
                )
            if unit.data_profile_version_id not in data_profile_ids:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} dataProfileVersionId is outside "
                    "the stored TaskPlanVersion matrix"
                )

            profile = case_profiles.get(unit.case_version_id)
            if profile is None:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} has no stored Case profile binding"
                )
            if unit.execution_profile_version_id != profile.execution_profile_version_id:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} executionProfileVersionId "
                    "does not match the stored TaskPlanVersion profileRefs"
                )
            if unit.fixture_blueprint_version_id != profile.fixture_blueprint_version_id:
                raise ValueError(
                    f"Manifest Unit ordinal {unit.ordinal} fixtureBlueprintVersionId "
                    "does not match the stored TaskPlanVersion profileRefs"
                )

    @staticmethod
    def _attempt_creation_identity(attempt: UnitAttempt) -> tuple[object, ...]:
        """Compare immutable creation facts while ignoring later progress updates."""

        return (
            attempt.id,
            attempt.tenant_id,
            attempt.project_id,
            attempt.task_run_id,
            attempt.execution_unit_id,
            attempt.manifest_hash,
            attempt.unit_key,
            attempt.case_version_id,
            attempt.attempt_number,
            attempt.temporal_namespace,
            attempt.temporal_workflow_id,
            attempt.queued_at,
            attempt.execution_deadline,
            attempt.created_at,
        )
