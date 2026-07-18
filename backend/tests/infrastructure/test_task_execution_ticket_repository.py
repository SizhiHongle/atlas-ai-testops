"""Repository tests for immutable UnitAttempt execution ticket replay."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.task import (
    TaskUnitExecutionTicket,
    task_unit_execution_ticket_digest,
)
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableFactConflictError,
)

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


class _Cursor:
    def __init__(self, row: DictRow | None) -> None:
        self._row = row

    async def fetchone(self) -> DictRow | None:
        return self._row


class _Connection:
    def __init__(self, *rows: DictRow | None) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> _Cursor:
        self.calls.append((query, params))
        return _Cursor(self._rows.pop(0))


def _ticket() -> TaskUnitExecutionTicket:
    values = {
        "tenant_id": UUID(int=1),
        "project_id": UUID(int=2),
        "task_run_id": UUID(int=3),
        "execution_unit_id": UUID(int=4),
        "unit_attempt_id": UUID(int=5),
        "request_digest": DIGEST,
        "manifest_hash": DIGEST,
        "ordinal": 1,
        "unit_key": DIGEST,
        "case_version_id": UUID(int=6),
        "case_content_digest": DIGEST,
        "test_ir_digest": DIGEST,
        "plan_digest": DIGEST,
        "compiled_digest": DIGEST,
        "attempt_number": 1,
        "execution_profile_version_id": UUID(int=7),
        "execution_profile_digest": DIGEST,
        "identity_profile_version_id": UUID(int=8),
        "identity_profile_digest": DIGEST,
        "browser_profile_version_id": UUID(int=9),
        "browser_profile_digest": DIGEST,
        "data_profile_version_id": UUID(int=10),
        "data_profile_digest": DIGEST,
        "fixture_blueprint_version_id": UUID(int=11),
        "fixture_blueprint_digest": DIGEST,
        "environment_id": UUID(int=12),
        "environment_revision": 1,
        "allowed_origins": ("https://example.test",),
        "execution_deadline": NOW + timedelta(hours=1),
    }
    return TaskUnitExecutionTicket.model_validate(
        {
            "id": UUID(int=13),
            "created_at": NOW,
            "ticket_digest": task_unit_execution_ticket_digest(**cast(Any, values)),
            **values,
        }
    )


def _row(ticket: TaskUnitExecutionTicket) -> DictRow:
    return ticket.model_dump(mode="python")


@pytest.mark.anyio
async def test_create_returns_the_inserted_ticket_and_all_frozen_columns() -> None:
    ticket = _ticket()
    connection = _Connection(_row(ticket))
    repository = TaskExecutionTicketRepository()

    result = await repository.create(
        cast(AsyncConnection[DictRow], connection),
        ticket,
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert result.fact == ticket
    assert "on conflict do nothing" in connection.calls[0][0].casefold()
    assert connection.calls[0][1] is not None
    assert len(connection.calls[0][1]) == 33


@pytest.mark.anyio
async def test_create_replays_only_the_same_attempt_and_ticket_digest() -> None:
    ticket = _ticket()
    connection = _Connection(None, _row(ticket))
    repository = TaskExecutionTicketRepository()

    result = await repository.create(
        cast(AsyncConnection[DictRow], connection),
        ticket.model_copy(update={"id": UUID(int=99), "created_at": NOW}),
    )

    assert result.kind is ImmutableCreateKind.EXISTING
    assert result.fact == ticket
    assert "unit_attempt_id = %s" in connection.calls[1][0]

    changed_values = ticket.model_dump(
        mode="python",
        by_alias=False,
        exclude={"id", "schema_version", "ticket_digest", "created_at"},
    )
    changed_values["environment_revision"] = 2
    conflicting = ticket.model_copy(
        update={
            "environment_revision": 2,
            "ticket_digest": task_unit_execution_ticket_digest(**changed_values),
        }
    )
    conflict_connection = _Connection(None, _row(conflicting))
    with pytest.raises(ImmutableFactConflictError):
        await repository.create(
            cast(AsyncConnection[DictRow], conflict_connection),
            ticket,
        )


@pytest.mark.anyio
async def test_getters_preserve_model_digest_validation() -> None:
    ticket = _ticket()
    connection = _Connection(_row(ticket), _row(ticket))
    repository = TaskExecutionTicketRepository()

    assert await repository.get_by_attempt(
        cast(AsyncConnection[DictRow], connection),
        ticket.unit_attempt_id,
    ) == ticket
    assert await repository.get(
        cast(AsyncConnection[DictRow], connection),
        ticket.id,
    ) == ticket
