"""Transactional Outbox 数据访问。"""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, JsonValue

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.events import DomainEvent


class OutboxMessage(FrozenWireModel):
    """被 Dispatcher 领取后的事件记录。"""

    id: UUID
    tenant_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, JsonValue]
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    attempts: int


class OutboxRepository:
    """所有方法都要求调用方提供短事务和可信 Tenant 上下文。"""

    async def append(
        self,
        connection: AsyncConnection[DictRow],
        event: DomainEvent,
        *,
        available_at: datetime | None = None,
    ) -> None:
        """在业务事务内追加事件。"""

        await connection.execute(
            """
            insert into atlas.outbox_event (
              id, tenant_id, aggregate_type, aggregate_id, event_type,
              payload, occurred_at, available_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.event_id,
                event.tenant_id,
                event.aggregate_type,
                event.aggregate_id,
                event.event_type,
                Jsonb(event.payload),
                event.occurred_at,
                available_at or event.occurred_at,
            ),
        )

    async def claim_batch(
        self,
        connection: AsyncConnection[DictRow],
        *,
        worker_id: str,
        now: datetime,
        stale_before: datetime,
        limit: int,
    ) -> tuple[OutboxMessage, ...]:
        """以 SKIP LOCKED 原子领取一批可发布事件。"""

        cursor = await connection.execute(
            """
            with candidates as (
              select id
              from atlas.outbox_event
              where processed_at is null
                and available_at <= %s
                and (claimed_at is null or claimed_at < %s)
              order by available_at, occurred_at, id
              limit %s
              for update skip locked
            )
            update atlas.outbox_event as event
            set claimed_at = %s,
                claimed_by = %s,
                attempts = event.attempts + 1,
                last_error = null
            from candidates
            where event.id = candidates.id
            returning event.id, event.tenant_id, event.aggregate_type,
                      event.aggregate_id, event.event_type, event.payload,
                      event.occurred_at, event.available_at, event.attempts
            """,
            (now, stale_before, limit, now, worker_id),
        )
        rows = await cursor.fetchall()
        messages = tuple(OutboxMessage.model_validate(row) for row in rows)
        return tuple(
            sorted(messages, key=lambda item: (item.available_at, item.occurred_at, item.id))
        )

    async def mark_processed(
        self,
        connection: AsyncConnection[DictRow],
        *,
        event_id: UUID,
        worker_id: str,
        processed_at: datetime,
    ) -> bool:
        """只有当前 Claim Owner 可以确认事件已处理。"""

        cursor = await connection.execute(
            """
            update atlas.outbox_event
            set processed_at = %s, claimed_at = null, claimed_by = null
            where id = %s and claimed_by = %s and processed_at is null
            returning id
            """,
            (processed_at, event_id, worker_id),
        )
        return await cursor.fetchone() is not None

    async def release_claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        event_id: UUID,
        worker_id: str,
        retry_at: datetime,
        error: str,
    ) -> bool:
        """发布失败时释放 Claim，并把重试时间推迟到事务提交后。"""

        cursor = await connection.execute(
            """
            update atlas.outbox_event
            set available_at = %s,
                claimed_at = null,
                claimed_by = null,
                last_error = %s
            where id = %s and claimed_by = %s and processed_at is null
            returning id
            """,
            (retry_at, error[:4000], event_id, worker_id),
        )
        return await cursor.fetchone() is not None
