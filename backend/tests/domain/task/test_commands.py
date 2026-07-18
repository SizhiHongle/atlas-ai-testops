"""Durable TaskRun control-command contract tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.task import (
    TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
    RequestTaskRunCancel,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    task_run_command_digest,
    task_run_workflow_id,
)

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _command(**updates: Any) -> TaskRunCommandIntent:
    tenant_id = UUID(int=1)
    project_id = UUID(int=2)
    run_id = UUID(int=3)
    workflow_id = task_run_workflow_id(tenant_id=tenant_id, task_run_id=run_id)
    values: dict[str, Any] = {
        "id": UUID(int=4),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_run_id": run_id,
        "command_type": TaskRunCommandType.CANCEL,
        "client_mutation_id": "cancel-command-001",
        "expected_run_revision": 7,
        "accepted_run_revision": 8,
        "request_digest": DIGEST_A,
        "manifest_hash": DIGEST_B,
        "temporal_namespace": "atlas-task",
        "temporal_workflow_id": workflow_id,
        "status": TaskRunCommandStatus.PENDING,
        "dispatch_attempts": 0,
        "created_by": UUID(int=5),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    values.setdefault(
        "command_digest",
        task_run_command_digest(
            tenant_id=values["tenant_id"],
            project_id=values["project_id"],
            task_run_id=values["task_run_id"],
            command_type=values["command_type"],
            client_mutation_id=values["client_mutation_id"],
            expected_run_revision=values["expected_run_revision"],
            request_digest=values["request_digest"],
            manifest_hash=values["manifest_hash"],
            temporal_namespace=values["temporal_namespace"],
            temporal_workflow_id=values["temporal_workflow_id"],
            schema_version=values.get(
                "schema_version",
                "atlas.task-run-command/0.2",
            ),
        ),
    )
    return TaskRunCommandIntent(**values)


def test_cancel_request_and_command_are_exact_secret_free_contracts() -> None:
    request = RequestTaskRunCancel(client_mutation_id="cancel-command-001")
    command = _command()

    assert request.client_mutation_id == command.client_mutation_id
    assert command.accepted_run_revision == command.expected_run_revision + 1
    assert command.temporal_workflow_id.endswith(
        f"/{command.tenant_id.hex}/{command.task_run_id.hex}"
    )
    assert command.model_dump(mode="json", by_alias=True)["commandType"] == "CANCEL"
    assert "password" not in str(command).casefold()


@pytest.mark.parametrize(
    "updates",
    [
        {"accepted_run_revision": 9},
        {"temporal_workflow_id": "atlas-task/run/invalid"},
        {"command_digest": DIGEST_A},
        {"updated_at": NOW - timedelta(seconds=1)},
        {"status": TaskRunCommandStatus.DELIVERED},
        {
            "status": TaskRunCommandStatus.PENDING,
            "failed_at": NOW,
        },
        {
            "status": TaskRunCommandStatus.APPLIED,
            "applied_at": NOW,
            "failed_at": NOW,
        },
        {
            "status": TaskRunCommandStatus.FAILED,
            "failed_at": None,
        },
    ],
)
def test_command_rejects_tampering_and_contradictory_status(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _command(**updates)


def test_command_accepts_delivered_applied_failed_and_pending_retry_projections() -> None:
    delivered_at = NOW + timedelta(seconds=1)
    delivered = _command(
        status=TaskRunCommandStatus.DELIVERED,
        dispatch_attempts=1,
        delivered_at=delivered_at,
        updated_at=delivered_at,
    )
    applied_at = delivered_at + timedelta(seconds=1)
    applied = delivered.model_copy(
        update={
            "status": TaskRunCommandStatus.APPLIED,
            "applied_at": applied_at,
            "updated_at": applied_at,
        }
    )
    failed = _command(
        status=TaskRunCommandStatus.FAILED,
        dispatch_attempts=3,
        last_error_code="TEMPORAL_RETRY_EXHAUSTED",
        failed_at=delivered_at,
        updated_at=delivered_at,
    )
    retrying = _command(
        dispatch_attempts=2,
        last_error_code="TEMPORAL_RPC_UNAVAILABLE",
        updated_at=delivered_at,
    )

    assert TaskRunCommandIntent.model_validate(applied).status is TaskRunCommandStatus.APPLIED
    assert failed.failed_at == retrying.updated_at == delivered_at
    assert retrying.status is TaskRunCommandStatus.PENDING


def test_cancel_request_rejects_unsafe_or_short_mutation_identity() -> None:
    for request_type in (
        RequestTaskRunCancel,
        RequestTaskRunPause,
        RequestTaskRunResume,
    ):
        for mutation_id in ("short", "contains a space", "x" * 201):
            with pytest.raises(ValidationError):
                request_type(client_mutation_id=mutation_id)


def test_v02_pause_resume_legacy_cancel_and_supersession_are_exact() -> None:
    pause = _command(
        command_type=TaskRunCommandType.PAUSE,
        client_mutation_id="pause-command-001",
    )
    resume = _command(
        command_type=TaskRunCommandType.RESUME,
        client_mutation_id="resume-command-001",
    )
    legacy_cancel = _command(
        schema_version=TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
    )
    superseded_at = NOW + timedelta(seconds=1)
    superseded = _command(
        command_type=TaskRunCommandType.PAUSE,
        client_mutation_id="pause-command-002",
        status=TaskRunCommandStatus.SUPERSEDED,
        superseded_at=superseded_at,
        superseded_by_command_id=UUID(int=99),
        updated_at=superseded_at,
    )

    assert RequestTaskRunPause(
        client_mutation_id=pause.client_mutation_id
    ).client_mutation_id == pause.client_mutation_id
    assert RequestTaskRunResume(
        client_mutation_id=resume.client_mutation_id
    ).client_mutation_id == resume.client_mutation_id
    assert legacy_cancel.command_type is TaskRunCommandType.CANCEL
    assert superseded.status is TaskRunCommandStatus.SUPERSEDED
    assert superseded.superseded_by_command_id == UUID(int=99)

    with pytest.raises(ValidationError):
        _command(
            schema_version=TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
            command_type=TaskRunCommandType.PAUSE,
            client_mutation_id="pause-command-legacy",
        )
