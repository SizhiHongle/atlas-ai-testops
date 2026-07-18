"""Immutable Task Unit execution ticket contract tests."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.task import (
    TaskUnitExecutionTicket,
    task_unit_execution_ticket_digest,
)
from atlas_testops.domain.task.models import _postgres_timestamptz_json_text

NOW = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _values() -> dict[str, object]:
    values: dict[str, object] = {
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
        "environment_revision": 3,
        "allowed_origins": (
            "https://b.example.test",
            "https://a.example.test",
        ),
        "execution_deadline": NOW + timedelta(hours=1),
    }
    origins = cast(tuple[str, ...], values["allowed_origins"])
    normalized = {**values, "allowed_origins": tuple(sorted(origins))}
    values["ticket_digest"] = task_unit_execution_ticket_digest(
        **cast(Any, normalized)
    )
    return values


def test_ticket_normalizes_origins_and_verifies_every_frozen_fact() -> None:
    ticket = TaskUnitExecutionTicket.model_validate(
        {"id": UUID(int=13), "created_at": NOW, **_values()}
    )

    assert ticket.allowed_origins == (
        "https://a.example.test",
        "https://b.example.test",
    )
    assert ticket.ticket_digest == task_unit_execution_ticket_digest(
        **ticket.model_dump(
            mode="python",
            by_alias=False,
            exclude={"id", "schema_version", "ticket_digest", "created_at"},
        )
    )
    dumped = ticket.model_dump(mode="json", by_alias=True)
    assert "password" not in str(dumped).casefold()
    assert "credential" not in str(dumped).casefold()


def test_ticket_deadline_digest_matches_postgresql_timestamp_json() -> None:
    deadline = datetime(2026, 7, 17, 10, 52, 51, 914730, tzinfo=UTC)
    equivalent = deadline.astimezone(timezone(timedelta(hours=8)))

    assert _postgres_timestamptz_json_text(deadline) == (
        "2026-07-17T10:52:51.91473+00:00"
    )
    assert _postgres_timestamptz_json_text(equivalent) == (
        "2026-07-17T10:52:51.91473+00:00"
    )
    assert _postgres_timestamptz_json_text(deadline.replace(microsecond=0)) == (
        "2026-07-17T10:52:51+00:00"
    )


def test_ticket_rejects_digest_tampering_and_empty_network_boundary() -> None:
    values = _values()
    with pytest.raises(ValidationError, match="ticketDigest"):
        TaskUnitExecutionTicket.model_validate(
            {
                "id": UUID(int=13),
                "created_at": NOW,
                **values,
                "case_content_digest": "sha256:" + "b" * 64,
            }
        )

    with pytest.raises(ValidationError, match="allowed_origins"):
        TaskUnitExecutionTicket.model_validate(
            {
                "id": UUID(int=13),
                "created_at": NOW,
                **values,
                "allowed_origins": (),
            }
        )

    with pytest.raises(ValidationError, match="path"):
        TaskUnitExecutionTicket.model_validate(
            {
                "id": UUID(int=13),
                "created_at": NOW,
                **values,
                "allowed_origins": ("https://example.test/private",),
            }
        )


def test_ticket_rejects_creation_at_or_after_the_attempt_deadline() -> None:
    values = _values()
    with pytest.raises(ValidationError, match="executionDeadline"):
        TaskUnitExecutionTicket.model_validate(
            {
                "id": UUID(int=13),
                "created_at": values["execution_deadline"],
                **values,
            }
        )
