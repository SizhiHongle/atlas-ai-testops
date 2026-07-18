"""Tenant and dispatcher storage for durable TaskRun control commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.task import TaskRunCommandIntent
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
    ImmutableFactConflictError,
)

PUBLIC_COMMAND_COLUMNS = """
  id, tenant_id, project_id, task_run_id, schema_version, command_type,
  client_mutation_id, command_digest, expected_run_revision, accepted_run_revision,
  request_digest, manifest_hash, namespace as temporal_namespace,
  workflow_id as temporal_workflow_id,
  case when status in ('CLAIMED', 'RETRY_WAIT') then 'PENDING' else status end as status,
  dispatch_attempts, last_error_code, signal_delivered_at as delivered_at,
  applied_at, dispatch_failed_at as failed_at, superseded_at,
  superseded_by_command_id, created_by, created_at, updated_at
"""


@dataclass(frozen=True, slots=True)
class ClaimedTaskRunCommandIntent:
    """Exact dispatcher lease projection without API credentials or secret material."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    schema_version: str
    command_type: str
    client_mutation_id: str
    command_digest: str
    expected_run_revision: int
    accepted_run_revision: int
    request_digest: str
    manifest_hash: str
    namespace: str
    workflow_id: str
    status: str
    claim_token: UUID
    dispatch_revision: int
    dispatch_attempts: int
    claim_expires_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: DictRow) -> ClaimedTaskRunCommandIntent:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


class TaskRunCommandRepository:
    """Persist tenant commands and call dispatcher-only delivery functions."""

    async def create(
        self,
        connection: AsyncConnection[DictRow],
        command: TaskRunCommandIntent,
    ) -> ImmutableCreateResult[TaskRunCommandIntent]:
        cursor = await connection.execute(
            f"""
            insert into atlas.task_run_command_intent (
              id, tenant_id, project_id, task_run_id, schema_version, command_type,
              client_mutation_id, command_digest, expected_run_revision,
              accepted_run_revision, request_digest, manifest_hash, namespace,
              workflow_id, status, available_at, dispatch_attempts,
              dispatch_revision, created_by, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, 'PENDING', %s, 0,
              0, %s, %s, %s
            )
            on conflict do nothing
            returning {PUBLIC_COMMAND_COLUMNS}
            """,
            (
                command.id,
                command.tenant_id,
                command.project_id,
                command.task_run_id,
                command.schema_version,
                command.command_type,
                command.client_mutation_id,
                command.command_digest,
                command.expected_run_revision,
                command.accepted_run_revision,
                command.request_digest,
                command.manifest_hash,
                command.temporal_namespace,
                command.temporal_workflow_id,
                command.created_at,
                command.created_by,
                command.created_at,
                command.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                TaskRunCommandIntent.model_validate(row),
            )
        existing = await self.get_by_mutation(
            connection,
            task_run_id=command.task_run_id,
            client_mutation_id=command.client_mutation_id,
        )
        if existing is None:
            raise RuntimeError("Task command conflict did not resolve to a stored row")
        if self._creation_identity(existing) != self._creation_identity(command):
            raise ImmutableFactConflictError(
                "Task command idempotency identity stores different immutable content"
            )
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def get(
        self,
        connection: AsyncConnection[DictRow],
        command_id: UUID,
    ) -> TaskRunCommandIntent | None:
        cursor = await connection.execute(
            f"""
            select {PUBLIC_COMMAND_COLUMNS}
            from atlas.task_run_command_intent
            where id = %s
            """,
            (command_id,),
        )
        row = await cursor.fetchone()
        return TaskRunCommandIntent.model_validate(row) if row is not None else None

    async def get_by_mutation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        client_mutation_id: str,
    ) -> TaskRunCommandIntent | None:
        cursor = await connection.execute(
            f"""
            select {PUBLIC_COMMAND_COLUMNS}
            from atlas.task_run_command_intent
            where task_run_id = %s and client_mutation_id = %s
            """,
            (task_run_id, client_mutation_id),
        )
        row = await cursor.fetchone()
        return TaskRunCommandIntent.model_validate(row) if row is not None else None

    async def get_open_for_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
    ) -> TaskRunCommandIntent | None:
        """Return the single unresolved Pause/Resume command for a visible Run."""

        cursor = await connection.execute(
            f"""
            select {PUBLIC_COMMAND_COLUMNS}
            from atlas.task_run_command_intent
            where task_run_id = %s
              and command_type in ('PAUSE', 'RESUME')
              and status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED')
            order by accepted_run_revision desc, created_at desc, id desc
            limit 1
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        return TaskRunCommandIntent.model_validate(row) if row is not None else None

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        claimed_by: str,
        namespace: str,
        limit: int,
        lease_duration: timedelta,
    ) -> tuple[ClaimedTaskRunCommandIntent, ...]:
        cursor = await connection.execute(
            """
            select
              id, tenant_id, project_id, task_run_id, schema_version, command_type,
              client_mutation_id, command_digest, expected_run_revision,
              accepted_run_revision, request_digest, manifest_hash, namespace,
              workflow_id, status, claim_token, dispatch_revision,
              dispatch_attempts, claim_expires_at, created_at
            from atlas.claim_task_run_command_intents(%s, %s, %s, %s)
            """,
            (claimed_by, namespace, limit, _whole_seconds(lease_duration)),
        )
        return tuple(
            ClaimedTaskRunCommandIntent.from_row(row) for row in await cursor.fetchall()
        )

    async def mark_delivered(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.mark_task_run_command_intent_delivered(%s, %s, %s) as applied
            """,
            (intent_id, claim_token, dispatch_revision),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def retry(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.retry_task_run_command_intent(%s, %s, %s, %s, %s) as applied
            """,
            (
                intent_id,
                claim_token,
                dispatch_revision,
                error_code,
                _retry_milliseconds(retry_delay),
            ),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def fail(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.fail_task_run_command_intent(%s, %s, %s, %s) as applied
            """,
            (intent_id, claim_token, dispatch_revision, error_code),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def apply_cancel(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        command_digest: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.apply_task_run_cancel_command(%s, %s) as applied
            """,
            (intent_id, command_digest),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def apply_pause(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        command_digest: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.apply_task_run_pause_command(%s, %s) as applied
            """,
            (intent_id, command_digest),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def apply_resume(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        command_digest: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.apply_task_run_resume_command(%s, %s) as applied
            """,
            (intent_id, command_digest),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def supersede_for_cancel(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        cancel_command_id: UUID,
    ) -> int:
        cursor = await connection.execute(
            """
            select atlas.supersede_task_run_commands(%s, %s) as affected
            """,
            (task_run_id, cancel_command_id),
        )
        row = await cursor.fetchone()
        return int(row["affected"]) if row is not None else 0

    @staticmethod
    def _creation_identity(command: TaskRunCommandIntent) -> tuple[object, ...]:
        return (
            command.tenant_id,
            command.project_id,
            command.task_run_id,
            command.schema_version,
            command.command_type,
            command.client_mutation_id,
            command.command_digest,
            command.expected_run_revision,
            command.accepted_run_revision,
            command.request_digest,
            command.manifest_hash,
            command.temporal_namespace,
            command.temporal_workflow_id,
        )


def _whole_seconds(value: timedelta) -> int:
    seconds = value.total_seconds()
    if seconds < 1 or not seconds.is_integer():
        raise ValueError("lease duration must be a positive whole number of seconds")
    return int(seconds)


def _retry_milliseconds(value: timedelta) -> int:
    milliseconds = ceil(value.total_seconds() * 1_000)
    if not 100 <= milliseconds <= 3_600_000:
        raise ValueError("retry delay must be between 100 and 3600000 milliseconds")
    return milliseconds
