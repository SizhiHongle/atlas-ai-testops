"""PostgreSQL 异步连接池与事务上下文。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from atlas_testops.core.config import Settings


@dataclass(frozen=True, slots=True)
class DatabaseContext:
    """由认证层构造的数据库隔离上下文。"""

    tenant_id: UUID
    actor_id: UUID | None = None
    request_id: str | None = None


class Database:
    """管理连接池，并保证 Tenant 上下文只在当前事务生效。"""

    def __init__(self, settings: Settings) -> None:
        database_url = settings.database_url_value
        if database_url is None:
            raise ValueError("database_url is required to create Database")

        self._statement_timeout_ms = settings.database_statement_timeout_ms
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            conninfo=database_url,
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            open=False,
            timeout=settings.database_connect_timeout_seconds,
            kwargs={"autocommit": False, "row_factory": dict_row},
            configure=self._configure_connection,
            name="atlas-api",
        )

    async def _configure_connection(self, connection: AsyncConnection[DictRow]) -> None:
        """为复用连接设置不会泄漏业务上下文的会话级参数。"""

        await connection.execute("select set_config('timezone', 'UTC', false)")
        await connection.execute(
            "select set_config('statement_timeout', %s, false)",
            (f"{self._statement_timeout_ms}ms",),
        )
        await connection.execute("select set_config('search_path', 'atlas,public', false)")
        await connection.commit()

    async def open(self) -> None:
        """打开连接池并等待最小连接数可用。"""

        await self._pool.open(wait=True)

    async def close(self) -> None:
        """关闭连接池及其后台任务。"""

        await self._pool.close()

    async def check(self) -> None:
        """执行最小查询，失败时让 readiness 返回不可用。"""

        async with self._pool.connection() as connection:
            await connection.execute("select 1")

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        """开启短事务，并通过 set_config 注入可信隔离上下文。"""

        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                "select set_config('atlas.tenant_id', %s, true)",
                (str(context.tenant_id),),
            )
            if context.actor_id is not None:
                await connection.execute(
                    "select set_config('atlas.actor_id', %s, true)",
                    (str(context.actor_id),),
                )
            if context.request_id is not None:
                await connection.execute(
                    "select set_config('atlas.request_id', %s, true)",
                    (context.request_id,),
                )
            yield connection

    @asynccontextmanager
    async def session_transaction(
        self,
        *,
        token_hash: str,
        request_id: str,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        """只按不可逆 Token Hash 打开单条 Session 的 RLS 视图。"""

        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                "select set_config('atlas.session_hash', %s, true)",
                (token_hash,),
            )
            await connection.execute(
                "select set_config('atlas.request_id', %s, true)",
                (request_id,),
            )
            yield connection
