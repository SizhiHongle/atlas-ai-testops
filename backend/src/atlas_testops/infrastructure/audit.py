"""追加式 Audit Event 数据访问。"""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.core.contracts import new_entity_id


class AuditRepository:
    """将权限相关动作写入不可变审计表。"""

    async def append(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID | None,
        environment_id: UUID | None,
        actor_id: UUID | None,
        event_type: str,
        entity_type: str,
        entity_id: UUID | None,
        occurred_at: datetime,
        payload: dict[str, JsonValue],
        request_id: str,
    ) -> UUID:
        """在业务事务内追加一条不可变 Audit Event。"""

        event_id = new_entity_id()
        await connection.execute(
            """
            insert into atlas.audit_event (
              id, tenant_id, project_id, environment_id, actor_id,
              event_type, entity_type, entity_id, occurred_at, payload, request_id
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                tenant_id,
                project_id,
                environment_id,
                actor_id,
                event_type,
                entity_type,
                entity_id,
                occurred_at,
                Jsonb(payload),
                request_id,
            ),
        )
        return event_id
