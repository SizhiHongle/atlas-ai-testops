"""数据库连接池单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid7

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.infrastructure.database import Database, DatabaseContext


def create_database() -> tuple[Database, MagicMock]:
    pool = MagicMock()
    with patch(
        "atlas_testops.infrastructure.database.AsyncConnectionPool",
        return_value=pool,
    ):
        database = Database(
            Settings(
                environment="test",
                database_url=SecretStr("postgresql://atlas_app:atlas_app@localhost/atlas"),
                database_pool_min_size=1,
                database_pool_max_size=2,
            )
        )
    return database, pool


def async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def test_database_requires_url() -> None:
    with pytest.raises(ValueError, match="database_url"):
        Database(Settings(environment="test"))


@pytest.mark.anyio
async def test_opens_and_closes_pool() -> None:
    database, pool = create_database()
    pool.open = AsyncMock()
    pool.close = AsyncMock()

    await database.open()
    await database.close()

    pool.open.assert_awaited_once_with(wait=True)
    pool.close.assert_awaited_once_with()


@pytest.mark.anyio
async def test_configures_connection_and_checks_health() -> None:
    database, pool = create_database()
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.commit = AsyncMock()
    pool.connection.return_value = async_context(connection)

    await database._configure_connection(connection)
    await database.check()

    assert connection.execute.await_count == 4
    connection.commit.assert_awaited_once_with()


@pytest.mark.anyio
async def test_transaction_sets_local_isolation_context() -> None:
    database, pool = create_database()
    connection = MagicMock()
    connection.execute = AsyncMock()
    transaction = async_context(None)
    connection.transaction.return_value = transaction
    pool.connection.return_value = async_context(connection)
    context = DatabaseContext(
        tenant_id=uuid7(),
        actor_id=uuid7(),
        request_id="request-42",
    )

    async with database.transaction(context) as yielded:
        assert yielded is connection

    assert connection.execute.await_count == 3
    values = [call.args[1][0] for call in connection.execute.await_args_list]
    assert values == [str(context.tenant_id), str(context.actor_id), "request-42"]


@pytest.mark.anyio
async def test_transaction_allows_optional_actor_and_request() -> None:
    database, pool = create_database()
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.transaction.return_value = async_context(None)
    pool.connection.return_value = async_context(connection)
    context = DatabaseContext(tenant_id=uuid7())

    async with database.transaction(context):
        pass

    connection.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_session_transaction_sets_only_token_hash_and_request() -> None:
    database, pool = create_database()
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.transaction.return_value = async_context(None)
    pool.connection.return_value = async_context(connection)

    async with database.session_transaction(
        token_hash="a" * 64,
        request_id="session-request-42",
    ) as yielded:
        assert yielded is connection

    assert connection.execute.await_count == 2
    values = [call.args[1][0] for call in connection.execute.await_args_list]
    assert values == ["a" * 64, "session-request-42"]
