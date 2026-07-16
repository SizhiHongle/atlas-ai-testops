"""PostgreSQL repositories for Task profiles and trusted execution state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.task import (
    BrowserProfileVersion,
    DataProfileVersion,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionProfileVersion,
    ExecutionQuality,
    ExecutionUnit,
    IdentityActorBinding,
    IdentityProfileVersion,
    TaskProfileStatus,
    TaskRun,
    UnitAttempt,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    EXECUTION_UNIT_COLUMNS,
    TASK_RUN_COLUMNS,
    UNIT_ATTEMPT_COLUMNS,
    ImmutableCreateKind,
    ImmutableCreateResult,
    ImmutableFactConflictError,
)

EXECUTION_PROFILE_COLUMNS = (
    "id, tenant_id, project_id, schema_version, profile_key, version, version_ref, "
    "status, case_version_id, case_content_digest, test_ir_digest, plan_digest, "
    "compiled_digest, model, tools, supported_features, content_digest, published_by, "
    "published_at, deprecated_at, revoked_at, revision, created_at, updated_at"
)
IDENTITY_PROFILE_COLUMNS = (
    "id, tenant_id, project_id, schema_version, profile_key, version, version_ref, "
    "status, case_version_id, case_content_digest, content_digest, published_by, "
    "published_at, deprecated_at, revoked_at, revision, created_at, updated_at"
)
IDENTITY_ACTOR_COLUMNS = (
    "actor_slot, role_id, role_key, role_revision, capabilities"
)
BROWSER_PROFILE_COLUMNS = (
    "id, tenant_id, project_id, schema_version, profile_key, version, version_ref, "
    "status, engine, browser_revision, viewport, locale, timezone, runtime_image_digest, "
    "capability_digest, content_digest, published_by, published_at, deprecated_at, "
    "revoked_at, revision, created_at, updated_at"
)
DATA_PROFILE_COLUMNS = (
    "id, tenant_id, project_id, schema_version, profile_key, version, version_ref, "
    "status, blueprint_version_id, blueprint_version_ref, blueprint_content_digest, "
    "plan_digest, run_inputs, input_digest, content_digest, published_by, published_at, "
    "deprecated_at, revoked_at, revision, created_at, updated_at"
)
WORKFLOW_START_INTENT_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, owner_kind, owner_id, namespace, "
    "workflow_id, request_digest, workflow_type, task_queue, status, created_at"
)


@dataclass(frozen=True, slots=True)
class TaskWorkflowStartIntent:
    """Read-only append-only workflow start fact; B1 never claims or consumes it."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    owner_kind: str
    owner_id: UUID
    namespace: str
    workflow_id: str
    request_digest: str
    workflow_type: str
    task_queue: str
    status: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: DictRow) -> TaskWorkflowStartIntent:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


class TaskProfileRepository:
    """Persist immutable published profiles and return exact natural-key replays."""

    async def create_execution_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile: ExecutionProfileVersion,
    ) -> ImmutableCreateResult[ExecutionProfileVersion]:
        _require_published(profile.status)
        cursor = await connection.execute(
            f"""
            insert into atlas.execution_profile_version (
              id, tenant_id, project_id, schema_version, profile_key, version, version_ref,
              status, case_version_id, case_content_digest, test_ir_digest, plan_digest,
              compiled_digest, model, tools, supported_features, content_digest, published_by,
              published_at, deprecated_at, revoked_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {EXECUTION_PROFILE_COLUMNS}
            """,
            (
                profile.id,
                profile.tenant_id,
                profile.project_id,
                profile.schema_version,
                profile.profile_key,
                profile.version,
                profile.version_ref,
                profile.status,
                profile.case_version_id,
                profile.case_content_digest,
                profile.test_ir_digest,
                profile.plan_digest,
                profile.compiled_digest,
                Jsonb(profile.model.model_dump(mode="json", by_alias=True)),
                Jsonb(profile.tools.model_dump(mode="json", by_alias=True)),
                list(profile.supported_features),
                profile.content_digest,
                profile.published_by,
                profile.published_at,
                profile.deprecated_at,
                profile.revoked_at,
                profile.revision,
                profile.created_at,
                profile.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                ExecutionProfileVersion.model_validate(row),
            )
        existing = await self._get_execution_profile_conflict(connection, profile)
        return _exact_replay(profile, existing, "ExecutionProfileVersion")

    async def create_identity_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile: IdentityProfileVersion,
    ) -> ImmutableCreateResult[IdentityProfileVersion]:
        _require_published(profile.status)
        cursor = await connection.execute(
            f"""
            insert into atlas.identity_profile_version (
              id, tenant_id, project_id, schema_version, profile_key, version, version_ref,
              status, case_version_id, case_content_digest, content_digest, published_by,
              published_at, deprecated_at, revoked_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {IDENTITY_PROFILE_COLUMNS}
            """,
            (
                profile.id,
                profile.tenant_id,
                profile.project_id,
                profile.schema_version,
                profile.profile_key,
                profile.version,
                profile.version_ref,
                profile.status,
                profile.case_version_id,
                profile.case_content_digest,
                profile.content_digest,
                profile.published_by,
                profile.published_at,
                profile.deprecated_at,
                profile.revoked_at,
                profile.revision,
                profile.created_at,
                profile.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            existing = await self._get_identity_profile_conflict(connection, profile)
            return _exact_replay(profile, existing, "IdentityProfileVersion")
        for ordinal, actor in enumerate(profile.actors, start=1):
            await connection.execute(
                """
                insert into atlas.identity_profile_actor_binding (
                  identity_profile_version_id, tenant_id, project_id, actor_slot, ordinal,
                  role_id, role_key, role_revision, capabilities
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    profile.id,
                    profile.tenant_id,
                    profile.project_id,
                    actor.actor_slot,
                    ordinal,
                    actor.role_id,
                    actor.role_key,
                    actor.role_revision,
                    list(actor.capabilities),
                ),
            )
        return ImmutableCreateResult(
            ImmutableCreateKind.CREATED,
            IdentityProfileVersion.model_validate({**row, "actors": profile.actors}),
        )

    async def create_browser_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile: BrowserProfileVersion,
    ) -> ImmutableCreateResult[BrowserProfileVersion]:
        _require_published(profile.status)
        cursor = await connection.execute(
            f"""
            insert into atlas.browser_profile_version (
              id, tenant_id, project_id, schema_version, profile_key, version, version_ref,
              status, engine, browser_revision, viewport, locale, timezone,
              runtime_image_digest, capability_digest, content_digest, published_by,
              published_at, deprecated_at, revoked_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {BROWSER_PROFILE_COLUMNS}
            """,
            (
                profile.id,
                profile.tenant_id,
                profile.project_id,
                profile.schema_version,
                profile.profile_key,
                profile.version,
                profile.version_ref,
                profile.status,
                profile.engine,
                profile.browser_revision,
                Jsonb(profile.viewport.model_dump(mode="json", by_alias=True)),
                profile.locale,
                profile.timezone,
                profile.runtime_image_digest,
                profile.capability_digest,
                profile.content_digest,
                profile.published_by,
                profile.published_at,
                profile.deprecated_at,
                profile.revoked_at,
                profile.revision,
                profile.created_at,
                profile.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                BrowserProfileVersion.model_validate(row),
            )
        existing = await self._get_browser_profile_conflict(connection, profile)
        return _exact_replay(profile, existing, "BrowserProfileVersion")

    async def create_data_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile: DataProfileVersion,
    ) -> ImmutableCreateResult[DataProfileVersion]:
        _require_published(profile.status)
        cursor = await connection.execute(
            f"""
            insert into atlas.data_profile_version (
              id, tenant_id, project_id, schema_version, profile_key, version, version_ref,
              status, blueprint_version_id, blueprint_version_ref, blueprint_content_digest,
              plan_digest, run_inputs, input_digest, content_digest, published_by, published_at,
              deprecated_at, revoked_at, revision, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {DATA_PROFILE_COLUMNS}
            """,
            (
                profile.id,
                profile.tenant_id,
                profile.project_id,
                profile.schema_version,
                profile.profile_key,
                profile.version,
                profile.version_ref,
                profile.status,
                profile.blueprint_version_id,
                profile.blueprint_version_ref,
                profile.blueprint_content_digest,
                profile.plan_digest,
                Jsonb(profile.run_inputs),
                profile.input_digest,
                profile.content_digest,
                profile.published_by,
                profile.published_at,
                profile.deprecated_at,
                profile.revoked_at,
                profile.revision,
                profile.created_at,
                profile.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                DataProfileVersion.model_validate(row),
            )
        existing = await self._get_data_profile_conflict(connection, profile)
        return _exact_replay(profile, existing, "DataProfileVersion")

    async def get_execution_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile_version_id: UUID,
    ) -> ExecutionProfileVersion | None:
        cursor = await connection.execute(
            f"select {EXECUTION_PROFILE_COLUMNS} "
            "from atlas.execution_profile_version where id = %s",
            (profile_version_id,),
        )
        row = await cursor.fetchone()
        return ExecutionProfileVersion.model_validate(row) if row is not None else None

    async def get_identity_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile_version_id: UUID,
    ) -> IdentityProfileVersion | None:
        cursor = await connection.execute(
            f"select {IDENTITY_PROFILE_COLUMNS} "
            "from atlas.identity_profile_version where id = %s",
            (profile_version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        actors = await self._get_identity_actors(connection, profile_version_id)
        return IdentityProfileVersion.model_validate({**row, "actors": actors})

    async def get_browser_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile_version_id: UUID,
    ) -> BrowserProfileVersion | None:
        cursor = await connection.execute(
            f"select {BROWSER_PROFILE_COLUMNS} "
            "from atlas.browser_profile_version where id = %s",
            (profile_version_id,),
        )
        row = await cursor.fetchone()
        return BrowserProfileVersion.model_validate(row) if row is not None else None

    async def get_data_profile_version(
        self,
        connection: AsyncConnection[DictRow],
        profile_version_id: UUID,
    ) -> DataProfileVersion | None:
        cursor = await connection.execute(
            f"select {DATA_PROFILE_COLUMNS} "
            "from atlas.data_profile_version where id = %s",
            (profile_version_id,),
        )
        row = await cursor.fetchone()
        return DataProfileVersion.model_validate(row) if row is not None else None

    async def _get_execution_profile_conflict(
        self,
        connection: AsyncConnection[DictRow],
        profile: ExecutionProfileVersion,
    ) -> ExecutionProfileVersion | None:
        row = await _get_profile_conflict_row(
            connection,
            table="execution_profile_version",
            columns=EXECUTION_PROFILE_COLUMNS,
            profile=profile,
        )
        return ExecutionProfileVersion.model_validate(row) if row is not None else None

    async def _get_identity_profile_conflict(
        self,
        connection: AsyncConnection[DictRow],
        profile: IdentityProfileVersion,
    ) -> IdentityProfileVersion | None:
        row = await _get_profile_conflict_row(
            connection,
            table="identity_profile_version",
            columns=IDENTITY_PROFILE_COLUMNS,
            profile=profile,
        )
        if row is None:
            return None
        actors = await self._get_identity_actors(connection, row["id"])
        return IdentityProfileVersion.model_validate({**row, "actors": actors})

    async def _get_browser_profile_conflict(
        self,
        connection: AsyncConnection[DictRow],
        profile: BrowserProfileVersion,
    ) -> BrowserProfileVersion | None:
        row = await _get_profile_conflict_row(
            connection,
            table="browser_profile_version",
            columns=BROWSER_PROFILE_COLUMNS,
            profile=profile,
        )
        return BrowserProfileVersion.model_validate(row) if row is not None else None

    async def _get_data_profile_conflict(
        self,
        connection: AsyncConnection[DictRow],
        profile: DataProfileVersion,
    ) -> DataProfileVersion | None:
        row = await _get_profile_conflict_row(
            connection,
            table="data_profile_version",
            columns=DATA_PROFILE_COLUMNS,
            profile=profile,
        )
        return DataProfileVersion.model_validate(row) if row is not None else None

    @staticmethod
    async def _get_identity_actors(
        connection: AsyncConnection[DictRow],
        profile_version_id: UUID,
    ) -> tuple[IdentityActorBinding, ...]:
        cursor = await connection.execute(
            f"""
            select {IDENTITY_ACTOR_COLUMNS}
            from atlas.identity_profile_actor_binding
            where identity_profile_version_id = %s
            order by ordinal, actor_slot
            """,
            (profile_version_id,),
        )
        return tuple(IdentityActorBinding.model_validate(row) for row in await cursor.fetchall())


class TaskExecutionStateRepository:
    """Call database-owned CAS functions and read append-only start intents."""

    async def next_task_execution_event_seq(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
    ) -> int:
        """Read the next gapless sequence while the trusted CAS keeps Run locked."""

        cursor = await connection.execute(
            """
            select coalesce(max(seq), 0) + 1 as next_seq
            from atlas.task_run_event
            where task_run_id = %s
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("task event sequence query returned no row")
        return int(row["next_seq"])

    async def seal_task_run_materialization(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        expected_revision: int,
    ) -> TaskRun | None:
        cursor = await connection.execute(
            f"""
            select {TASK_RUN_COLUMNS}
            from atlas.seal_task_run_materialization(%s, %s)
            """,
            (task_run_id, expected_revision),
        )
        row = await cursor.fetchone()
        return TaskRun.model_validate(row) if row is not None else None

    async def transition_task_run_state(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        expected_revision: int,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        hygiene: ExecutionHygiene,
        started_at: datetime | None,
        finalized_at: datetime | None,
        cleanup_resolved_at: datetime | None,
        closed_at: datetime | None,
    ) -> TaskRun | None:
        cursor = await connection.execute(
            f"""
            select {TASK_RUN_COLUMNS}
            from atlas.transition_task_run_state(
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                task_run_id,
                expected_revision,
                lifecycle,
                quality,
                hygiene,
                started_at,
                finalized_at,
                cleanup_resolved_at,
                closed_at,
            ),
        )
        row = await cursor.fetchone()
        return TaskRun.model_validate(row) if row is not None else None

    async def transition_execution_unit_state(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        expected_revision: int,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        hygiene: ExecutionHygiene,
        started_at: datetime | None,
        finalized_at: datetime | None,
        cleanup_resolved_at: datetime | None,
        closed_at: datetime | None,
    ) -> ExecutionUnit | None:
        cursor = await connection.execute(
            f"""
            select {EXECUTION_UNIT_COLUMNS}
            from atlas.transition_execution_unit_state(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                task_run_id,
                execution_unit_id,
                expected_revision,
                lifecycle,
                quality,
                hygiene,
                started_at,
                finalized_at,
                cleanup_resolved_at,
                closed_at,
            ),
        )
        row = await cursor.fetchone()
        return ExecutionUnit.model_validate(row) if row is not None else None

    async def transition_unit_attempt_state(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        unit_attempt_id: UUID,
        expected_revision: int,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        hygiene: ExecutionHygiene,
        started_at: datetime | None,
        finalized_at: datetime | None,
        cleanup_resolved_at: datetime | None,
        closed_at: datetime | None,
    ) -> UnitAttempt | None:
        cursor = await connection.execute(
            f"""
            select {UNIT_ATTEMPT_COLUMNS}
            from atlas.transition_unit_attempt_state(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                task_run_id,
                execution_unit_id,
                unit_attempt_id,
                expected_revision,
                lifecycle,
                quality,
                hygiene,
                started_at,
                finalized_at,
                cleanup_resolved_at,
                closed_at,
            ),
        )
        row = await cursor.fetchone()
        return UnitAttempt.model_validate(row) if row is not None else None

    async def get_workflow_start_intent(
        self,
        connection: AsyncConnection[DictRow],
        *,
        owner_kind: str,
        owner_id: UUID,
    ) -> TaskWorkflowStartIntent | None:
        cursor = await connection.execute(
            f"""
            select {WORKFLOW_START_INTENT_COLUMNS}
            from atlas.task_workflow_start_intent
            where owner_kind = %s and owner_id = %s
            """,
            (owner_kind, owner_id),
        )
        row = await cursor.fetchone()
        return TaskWorkflowStartIntent.from_row(row) if row is not None else None

    async def list_pending_workflow_start_intents(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        limit: int,
    ) -> tuple[TaskWorkflowStartIntent, ...]:
        cursor = await connection.execute(
            f"""
            select {WORKFLOW_START_INTENT_COLUMNS}
            from atlas.task_workflow_start_intent
            where project_id = %s and status = 'PENDING'
            order by created_at, id
            limit %s
            """,
            (project_id, limit),
        )
        return tuple(TaskWorkflowStartIntent.from_row(row) for row in await cursor.fetchall())


async def _get_profile_conflict_row(
    connection: AsyncConnection[DictRow],
    *,
    table: str,
    columns: str,
    profile: ExecutionProfileVersion
    | IdentityProfileVersion
    | BrowserProfileVersion
    | DataProfileVersion,
) -> DictRow | None:
    # Table and column values are module constants, never request-controlled input.
    cursor = await connection.execute(
        f"""
        select {columns}
        from atlas.{table}
        where id = %s
           or (tenant_id = %s and project_id = %s and profile_key = %s and version = %s)
        order by case when id = %s then 0 else 1 end
        limit 1
        """,
        (
            profile.id,
            profile.tenant_id,
            profile.project_id,
            profile.profile_key,
            profile.version,
            profile.id,
        ),
    )
    return await cursor.fetchone()


def _require_published(status: TaskProfileStatus) -> None:
    if status is not TaskProfileStatus.PUBLISHED:
        raise ValueError("new Task profile versions must start in PUBLISHED status")


def _exact_replay[ProfileT: (
    ExecutionProfileVersion,
    IdentityProfileVersion,
    BrowserProfileVersion,
    DataProfileVersion,
)](
    requested: ProfileT,
    existing: ProfileT | None,
    kind: str,
) -> ImmutableCreateResult[ProfileT]:
    if existing is None:
        raise RuntimeError(f"{kind} conflict did not resolve to a stored row")
    requested_identity = (
        requested.tenant_id,
        requested.project_id,
        requested.profile_key,
        requested.version,
        requested.content_digest,
    )
    existing_identity = (
        existing.tenant_id,
        existing.project_id,
        existing.profile_key,
        existing.version,
        existing.content_digest,
    )
    if requested_identity != existing_identity:
        raise ImmutableFactConflictError(
            f"{kind} identity already stores different immutable content"
        )
    return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)
