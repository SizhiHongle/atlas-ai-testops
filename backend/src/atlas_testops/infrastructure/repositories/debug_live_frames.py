"""PostgreSQL projection for the latest masked DebugRun browser frame."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.runtime import DebugLiveFrame, DebugLiveFrameUpdate


@dataclass(frozen=True, slots=True)
class DebugLiveFrameContent:
    """Latest frame metadata plus private bytes inside one authorized transaction."""

    metadata: DebugLiveFrame
    payload: bytes


class DebugLiveFrameRepository:
    """Upsert one bounded ephemeral frame without changing evidence facts."""

    async def upsert(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        debug_run_id: UUID,
        command: DebugLiveFrameUpdate,
        recorded_at: datetime,
    ) -> DebugLiveFrame:
        cursor = await connection.execute(
            """
            insert into atlas.debug_live_frame (
              debug_run_id, tenant_id, project_id, environment_id,
              execution_contract_id, frame_revision, page_revision,
              mime_type, content_digest, size_bytes, payload,
              captured_at, recorded_at
            ) values (
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            on conflict (debug_run_id) do update
            set frame_revision = excluded.frame_revision,
                page_revision = excluded.page_revision,
                mime_type = excluded.mime_type,
                content_digest = excluded.content_digest,
                size_bytes = excluded.size_bytes,
                payload = excluded.payload,
                captured_at = excluded.captured_at,
                recorded_at = excluded.recorded_at
            where atlas.debug_live_frame.execution_contract_id
                    = excluded.execution_contract_id
              and atlas.debug_live_frame.frame_revision < excluded.frame_revision
              and atlas.debug_live_frame.captured_at <= excluded.captured_at
            returning
              debug_run_id, project_id, environment_id, execution_contract_id,
              frame_revision, page_revision, mime_type, content_digest,
              size_bytes, captured_at
            """,
            (
                debug_run_id,
                tenant_id,
                project_id,
                environment_id,
                command.execution_contract_id,
                command.frame_revision,
                command.page_revision,
                command.mime_type,
                command.content_digest,
                len(command.payload),
                bytes(command.payload),
                command.captured_at,
                recorded_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await connection.execute(
                """
                select
                  debug_run_id, project_id, environment_id, execution_contract_id,
                  frame_revision, page_revision, mime_type, content_digest,
                  size_bytes, captured_at
                from atlas.debug_live_frame
                where debug_run_id = %s
                """,
                (debug_run_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("debug live frame upsert did not return a row")
        return DebugLiveFrame.model_validate(row)

    async def get(
        self,
        connection: AsyncConnection[DictRow],
        debug_run_id: UUID,
    ) -> DebugLiveFrameContent | None:
        cursor = await connection.execute(
            """
            select
              debug_run_id, project_id, environment_id, execution_contract_id,
              frame_revision, page_revision, mime_type, content_digest,
              size_bytes, captured_at, payload
            from atlas.debug_live_frame
            where debug_run_id = %s
            """,
            (debug_run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = bytes(row.pop("payload"))
        return DebugLiveFrameContent(
            metadata=DebugLiveFrame.model_validate(row),
            payload=payload,
        )
