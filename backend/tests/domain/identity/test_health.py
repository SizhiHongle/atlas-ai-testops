"""Account health fact validation tests."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.identity import (
    AccountHealth,
    AccountHealthCheck,
    AccountHealthCheckStatus,
    AccountHealthCheckTrigger,
    AccountHealthFailureCode,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountStateTransition,
    AccountStateTransitionReason,
    AccountSyncStatus,
    PasswordAuthenticationResult,
    VerifyTestAccount,
)


def health_check_payload(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "id": uuid7(),
        "tenant_id": uuid7(),
        "project_id": uuid7(),
        "environment_id": uuid7(),
        "account_id": uuid7(),
        "connector_installation_id": uuid7(),
        "credential_binding_id": uuid7(),
        "trigger": AccountHealthCheckTrigger.MANUAL,
        "status": AccountHealthCheckStatus.RUNNING,
        "origin": "https://example.test",
        "role_key": "sales",
        "account_revision": 2,
        "connector_revision": 1,
        "credential_revision": 1,
        "result_health_status": None,
        "failure_code": None,
        "retryable": None,
        "safe_summary": "Health verification is running.",
        "actor_id": uuid7(),
        "request_id": "request-1",
        "started_at": now,
        "finished_at": None,
        "expires_at": now + timedelta(minutes=2),
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    return payload


def transition_payload(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "id": uuid7(),
        "tenant_id": uuid7(),
        "project_id": uuid7(),
        "environment_id": uuid7(),
        "account_id": uuid7(),
        "health_check_id": uuid7(),
        "reason": AccountStateTransitionReason.VERIFICATION_STARTED,
        "from_lifecycle_status": AccountLifecycle.ACTIVE,
        "to_lifecycle_status": AccountLifecycle.ACTIVE,
        "from_health_status": AccountHealth.UNKNOWN,
        "to_health_status": AccountHealth.UNKNOWN,
        "from_operational_status": AccountOperationalStatus.READY,
        "to_operational_status": AccountOperationalStatus.VERIFYING,
        "from_sync_status": AccountSyncStatus.NOT_APPLICABLE,
        "to_sync_status": AccountSyncStatus.NOT_APPLICABLE,
        "from_cooldown_until": None,
        "to_cooldown_until": None,
        "safe_summary": "Health verification started.",
        "actor_id": uuid7(),
        "request_id": "request-1",
        "occurred_at": now,
    }
    payload.update(overrides)
    return payload


def test_verify_command_normalizes_exact_origin() -> None:
    command = VerifyTestAccount(origin="HTTPS://EXAMPLE.TEST:443/")

    assert command.origin == "https://example.test"


def test_password_authentication_result_normalizes_identity_and_roles() -> None:
    result = PasswordAuthenticationResult(
        provider_subject=" provider-subject ",
        role_keys=("Sales", " sales ", "observer"),
    )

    assert result.provider_subject == "provider-subject"
    assert result.role_keys == ("observer", "sales")
    with pytest.raises(ValidationError, match="must not be blank"):
        PasswordAuthenticationResult(provider_subject="   ")


def test_running_health_check_rejects_terminal_metadata() -> None:
    with pytest.raises(ValidationError, match="running health check"):
        AccountHealthCheck.model_validate(
            health_check_payload(finished_at=datetime.now(UTC), retryable=False)
        )


def test_terminal_health_check_requires_completion_metadata() -> None:
    with pytest.raises(ValidationError, match="completion metadata"):
        AccountHealthCheck.model_validate(
            health_check_payload(status=AccountHealthCheckStatus.FAILED)
        )


def test_successful_health_check_requires_exact_success_metadata() -> None:
    now = datetime.now(UTC)
    valid = AccountHealthCheck.model_validate(
        health_check_payload(
            status=AccountHealthCheckStatus.SUCCEEDED,
            result_health_status=AccountHealth.HEALTHY,
            retryable=False,
            finished_at=now,
        )
    )
    assert valid.status is AccountHealthCheckStatus.SUCCEEDED

    with pytest.raises(ValidationError, match="successful health check"):
        AccountHealthCheck.model_validate(
            health_check_payload(
                status=AccountHealthCheckStatus.SUCCEEDED,
                result_health_status=AccountHealth.DEGRADED,
                retryable=False,
                finished_at=now,
            )
        )


def test_failed_health_check_requires_failure_and_degraded_result() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="requires a failure code"):
        AccountHealthCheck.model_validate(
            health_check_payload(
                status=AccountHealthCheckStatus.FAILED,
                result_health_status=AccountHealth.DEGRADED,
                retryable=False,
                finished_at=now,
            )
        )
    with pytest.raises(ValidationError, match="requires a degraded result"):
        AccountHealthCheck.model_validate(
            health_check_payload(
                status=AccountHealthCheckStatus.FAILED,
                result_health_status=AccountHealth.HEALTHY,
                failure_code=AccountHealthFailureCode.AUTHENTICATION_FAILED,
                retryable=False,
                finished_at=now,
            )
        )


def test_stale_health_check_requires_stale_snapshot_metadata() -> None:
    now = datetime.now(UTC)
    valid = AccountHealthCheck.model_validate(
        health_check_payload(
            status=AccountHealthCheckStatus.STALE,
            failure_code=AccountHealthFailureCode.STALE_SNAPSHOT,
            retryable=True,
            finished_at=now,
        )
    )
    assert valid.status is AccountHealthCheckStatus.STALE

    with pytest.raises(ValidationError, match="stale health check"):
        AccountHealthCheck.model_validate(
            health_check_payload(
                status=AccountHealthCheckStatus.STALE,
                result_health_status=AccountHealth.DEGRADED,
                failure_code=AccountHealthFailureCode.STALE_SNAPSHOT,
                retryable=True,
                finished_at=now,
            )
        )


def test_state_transition_requires_an_actual_orthogonal_state_change() -> None:
    transition = AccountStateTransition.model_validate(transition_payload())
    assert transition.reason is AccountStateTransitionReason.VERIFICATION_STARTED

    with pytest.raises(ValidationError, match="must change"):
        AccountStateTransition.model_validate(
            transition_payload(
                to_operational_status=AccountOperationalStatus.READY,
            )
        )
