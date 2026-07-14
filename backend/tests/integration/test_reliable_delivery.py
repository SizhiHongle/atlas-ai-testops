"""真实 PostgreSQL 上的 Outbox 与 Idempotency 行为测试。"""

from datetime import UTC, datetime, timedelta
from os import environ
from uuid import UUID, uuid7

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


def create_database() -> Database:
    assert DATABASE_URL is not None
    return Database(
        Settings(
            environment="test",
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=2,
        )
    )


async def create_tenant(database: Database, tenant_id: UUID) -> None:
    async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
        await connection.execute(
            "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
            (tenant_id, f"tenant-{tenant_id.hex[-12:]}", "Reliable Delivery Tenant"),
        )


async def delete_tenant(database: Database, tenant_id: UUID) -> None:
    async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
        await connection.execute(
            "delete from atlas.idempotency_record where tenant_id = %s",
            (tenant_id,),
        )
        await connection.execute(
            "delete from atlas.outbox_event where tenant_id = %s",
            (tenant_id,),
        )
        await connection.execute("delete from atlas.tenant where id = %s", (tenant_id,))


@pytest.mark.anyio
async def test_idempotency_reservation_conflict_and_cached_response() -> None:
    database = create_database()
    repository = IdempotencyRepository()
    tenant_id = uuid7()
    context = DatabaseContext(tenant_id=tenant_id)
    now = datetime.now(UTC)
    request_hash = hash_request({"name": "Atlas"})

    await database.open()
    try:
        await create_tenant(database, tenant_id)
        async with database.transaction(context) as connection:
            reservation = await repository.reserve(
                connection,
                tenant_id=tenant_id,
                scope="projects.create",
                key="request-1",
                request_hash=request_hash,
                now=now,
                ttl=timedelta(hours=1),
            )
            assert reservation.acquired is True

            with pytest.raises(ApplicationError) as processing_error:
                await repository.reserve(
                    connection,
                    tenant_id=tenant_id,
                    scope="projects.create",
                    key="request-1",
                    request_hash=request_hash,
                    now=now,
                    ttl=timedelta(hours=1),
                )
            assert processing_error.value.headers == {"Retry-After": "1"}

            await repository.complete(
                connection,
                tenant_id=tenant_id,
                scope="projects.create",
                key="request-1",
                request_hash=request_hash,
                response=CachedHttpResponse(
                    status_code=201,
                    body={"projectId": str(uuid7())},
                ),
            )

        async with database.transaction(context) as connection:
            repeated = await repository.reserve(
                connection,
                tenant_id=tenant_id,
                scope="projects.create",
                key="request-1",
                request_hash=request_hash,
                now=now + timedelta(seconds=1),
                ttl=timedelta(hours=1),
            )
            assert repeated.acquired is False
            assert repeated.cached_response is not None
            assert repeated.cached_response.status_code == 201

            with pytest.raises(ApplicationError) as conflict_error:
                await repository.reserve(
                    connection,
                    tenant_id=tenant_id,
                    scope="projects.create",
                    key="request-1",
                    request_hash=hash_request({"name": "Different"}),
                    now=now + timedelta(seconds=1),
                    ttl=timedelta(hours=1),
                )
            assert conflict_error.value.error_code is ErrorCode.CONFLICT

            with pytest.raises(ApplicationError) as precondition_error:
                await repository.complete(
                    connection,
                    tenant_id=tenant_id,
                    scope="projects.create",
                    key="missing",
                    request_hash=request_hash,
                    response=CachedHttpResponse(status_code=200, body={}),
                )
            assert precondition_error.value.error_code is ErrorCode.PRECONDITION_FAILED
    finally:
        await delete_tenant(database, tenant_id)
        await database.close()


@pytest.mark.anyio
async def test_outbox_claim_release_and_completion() -> None:
    database = create_database()
    repository = OutboxRepository()
    tenant_id = uuid7()
    context = DatabaseContext(tenant_id=tenant_id)
    now = datetime.now(UTC)
    first = DomainEvent(
        tenant_id=tenant_id,
        aggregate_type="project",
        aggregate_id=uuid7(),
        event_type="project.created",
        occurred_at=now,
    )
    second = DomainEvent(
        tenant_id=tenant_id,
        aggregate_type="environment",
        aggregate_id=uuid7(),
        event_type="environment.created",
        occurred_at=now + timedelta(microseconds=1),
    )

    await database.open()
    try:
        await create_tenant(database, tenant_id)
        async with database.transaction(context) as connection:
            await repository.append(connection, first)
            await repository.append(connection, second)

        async with database.transaction(context) as connection:
            claimed = await repository.claim_batch(
                connection,
                worker_id="projector-1",
                now=now + timedelta(seconds=1),
                stale_before=now - timedelta(minutes=1),
                limit=10,
            )
            assert [message.id for message in claimed] == [first.event_id, second.event_id]
            assert all(message.attempts == 1 for message in claimed)

            assert (
                await repository.mark_processed(
                    connection,
                    event_id=second.event_id,
                    worker_id="wrong-worker",
                    processed_at=now + timedelta(seconds=2),
                )
                is False
            )
            assert await repository.mark_processed(
                connection,
                event_id=second.event_id,
                worker_id="projector-1",
                processed_at=now + timedelta(seconds=2),
            )
            assert await repository.release_claim(
                connection,
                event_id=first.event_id,
                worker_id="projector-1",
                retry_at=now + timedelta(minutes=1),
                error="temporary failure",
            )

        async with database.transaction(context) as connection:
            early = await repository.claim_batch(
                connection,
                worker_id="projector-2",
                now=now + timedelta(seconds=10),
                stale_before=now,
                limit=10,
            )
            assert early == ()

            retried = await repository.claim_batch(
                connection,
                worker_id="projector-2",
                now=now + timedelta(minutes=2),
                stale_before=now + timedelta(minutes=1),
                limit=10,
            )
            assert len(retried) == 1
            assert retried[0].id == first.event_id
            assert retried[0].attempts == 2
            assert await repository.mark_processed(
                connection,
                event_id=first.event_id,
                worker_id="projector-2",
                processed_at=now + timedelta(minutes=2),
            )
    finally:
        await delete_tenant(database, tenant_id)
        await database.close()
