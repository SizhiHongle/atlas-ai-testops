"""HTTP 命令幂等记录。"""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.core.errors import ApplicationError, ErrorCode


class CachedHttpResponse(FrozenWireModel):
    """完成命令后可安全重放的响应。"""

    status_code: int
    body: dict[str, JsonValue]


class IdempotencyReservation(FrozenWireModel):
    """命令处理器取得的新 Reservation 或已有缓存。"""

    acquired: bool
    cached_response: CachedHttpResponse | None = None


def hash_request(payload: JsonValue) -> str:
    """对已经解析的 JSON 生成稳定 SHA-256。"""

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


class IdempotencyRepository:
    """在业务事务中预约并完成 Idempotency-Key。"""

    async def reserve(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        now: datetime,
        ttl: timedelta,
    ) -> IdempotencyReservation:
        """首次请求取得处理权，重复请求读取缓存或得到明确冲突。"""

        await connection.execute(
            """
            delete from atlas.idempotency_record
            where tenant_id = %s and scope = %s and idempotency_key = %s
              and expires_at <= %s
            """,
            (tenant_id, scope, key, now),
        )
        cursor = await connection.execute(
            """
            insert into atlas.idempotency_record (
              tenant_id, scope, idempotency_key, request_hash,
              state, created_at, expires_at
            ) values (%s, %s, %s, %s, 'PROCESSING', %s, %s)
            on conflict (tenant_id, scope, idempotency_key) do nothing
            returning idempotency_key
            """,
            (tenant_id, scope, key, request_hash, now, now + ttl),
        )
        if await cursor.fetchone() is not None:
            return IdempotencyReservation(acquired=True)

        existing_cursor = await connection.execute(
            """
            select request_hash, state, status_code, response_body
            from atlas.idempotency_record
            where tenant_id = %s and scope = %s and idempotency_key = %s
            for update
            """,
            (tenant_id, scope, key),
        )
        existing = await existing_cursor.fetchone()
        if existing is None:
            raise RuntimeError("idempotency record disappeared while reserving")
        if existing["request_hash"] != request_hash:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="Idempotency-Key 冲突",
                detail="同一个 Idempotency-Key 已用于不同请求。",
                status_code=409,
            )
        if existing["state"] == "COMPLETED":
            return IdempotencyReservation(
                acquired=False,
                cached_response=CachedHttpResponse(
                    status_code=existing["status_code"],
                    body=existing["response_body"],
                ),
            )
        raise ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="请求仍在处理中",
            detail="同一个 Idempotency-Key 对应的命令尚未完成。",
            status_code=409,
            headers={"Retry-After": "1"},
        )

    async def complete(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        response: CachedHttpResponse,
    ) -> None:
        """与业务写入一起提交最终响应。"""

        cursor = await connection.execute(
            """
            update atlas.idempotency_record
            set state = 'COMPLETED', status_code = %s, response_body = %s
            where tenant_id = %s and scope = %s and idempotency_key = %s
              and request_hash = %s and state = 'PROCESSING'
            returning idempotency_key
            """,
            (
                response.status_code,
                Jsonb(response.body),
                tenant_id,
                scope,
                key,
                request_hash,
            ),
        )
        if await cursor.fetchone() is None:
            raise ApplicationError(
                error_code=ErrorCode.PRECONDITION_FAILED,
                title="幂等记录无法完成",
                detail="命令没有活动的 Idempotency Reservation。",
                status_code=412,
            )

    async def cancel(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
    ) -> bool:
        """业务没有产生结果时移除当前 PROCESSING Reservation。"""

        cursor = await connection.execute(
            """
            delete from atlas.idempotency_record
            where tenant_id = %s and scope = %s and idempotency_key = %s
              and request_hash = %s and state = 'PROCESSING'
            returning idempotency_key
            """,
            (tenant_id, scope, key, request_hash),
        )
        return await cursor.fetchone() is not None
