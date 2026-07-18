"""Dispatcher-only storage boundary for recoverable TaskRun materialization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow


@dataclass(frozen=True, slots=True)
class ClaimedTaskMaterializationPartition:
    """One leased, secret-free materialization checkpoint."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    manifest_hash: str
    partition_index: int
    first_ordinal: int
    last_ordinal: int
    status: str
    claim_token: UUID
    revision: int
    materialization_attempts: int
    claim_expires_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: DictRow) -> ClaimedTaskMaterializationPartition:
        """Copy the narrow function projection without retaining a database row."""

        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


class TaskMaterializationPartitionRepository:
    """Call only atlas_dispatcher's narrow SECURITY DEFINER functions."""

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        claimed_by: str,
        limit: int,
        lease_duration: timedelta,
    ) -> tuple[ClaimedTaskMaterializationPartition, ...]:
        cursor = await connection.execute(
            """
            select *
            from atlas.claim_task_run_materialization_partitions(%s, %s, %s)
            """,
            (
                claimed_by,
                limit,
                _whole_seconds(lease_duration),
            ),
        )
        return tuple(
            ClaimedTaskMaterializationPartition.from_row(row)
            for row in await cursor.fetchall()
        )

    async def complete(
        self,
        connection: AsyncConnection[DictRow],
        *,
        partition: ClaimedTaskMaterializationPartition,
        claimed_by: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.complete_task_run_materialization_partition(
              %s, %s, %s, %s
            ) as run_sealed
            """,
            (
                partition.id,
                partition.claim_token,
                partition.revision,
                claimed_by,
            ),
        )
        row = await cursor.fetchone()
        return row is not None and row["run_sealed"] is not None

    async def retry(
        self,
        connection: AsyncConnection[DictRow],
        *,
        partition: ClaimedTaskMaterializationPartition,
        claimed_by: str,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.retry_task_run_materialization_partition(
              %s, %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                partition.id,
                partition.claim_token,
                partition.revision,
                claimed_by,
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
        partition: ClaimedTaskMaterializationPartition,
        claimed_by: str,
        error_code: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.fail_task_run_materialization_partition(
              %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                partition.id,
                partition.claim_token,
                partition.revision,
                claimed_by,
                error_code,
            ),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])


def _whole_seconds(value: timedelta) -> int:
    seconds = value.total_seconds()
    if seconds < 1 or not seconds.is_integer():
        raise ValueError("materialization lease must use positive whole seconds")
    return int(seconds)


def _retry_milliseconds(value: timedelta) -> int:
    milliseconds = ceil(value.total_seconds() * 1_000)
    if not 100 <= milliseconds <= 3_600_000:
        raise ValueError("materialization retry delay is outside the database contract")
    return milliseconds


__all__ = [
    "ClaimedTaskMaterializationPartition",
    "TaskMaterializationPartitionRepository",
]
