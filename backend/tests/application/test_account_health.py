"""Account health policy and safe failure mapping tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from atlas_testops.application.account_health import (
    AccountHealthService,
    decide_health_failure,
    health_failure_outcome,
    map_adapter_health_failure,
)
from atlas_testops.application.ports.providers import AdapterOperationError
from atlas_testops.domain.identity import (
    AccountHealth,
    AccountHealthFailureCode,
    AccountOperationalStatus,
    AccountStateTransitionReason,
    AdapterError,
    AdapterErrorCode,
)


@pytest.mark.parametrize("retry_cooldown_seconds", [0, 300])
def test_account_failure_enters_degraded_until_threshold(
    retry_cooldown_seconds: int,
) -> None:
    now = datetime.now(UTC)

    state = decide_health_failure(
        current_failures=0,
        threshold=3,
        retry_cooldown_seconds=retry_cooldown_seconds,
        code=AccountHealthFailureCode.AUTHENTICATION_FAILED,
        now=now,
    )

    assert state.health_status is AccountHealth.DEGRADED
    assert state.consecutive_health_failures == 1
    assert state.reason is AccountStateTransitionReason.VERIFICATION_FAILED
    if retry_cooldown_seconds:
        assert state.operational_status is AccountOperationalStatus.COOLDOWN
        assert state.cooldown_until == now + timedelta(seconds=retry_cooldown_seconds)
    else:
        assert state.operational_status is AccountOperationalStatus.VERIFYING
        assert state.cooldown_until is None


def test_account_failure_threshold_and_immediate_failures_quarantine() -> None:
    now = datetime.now(UTC)

    threshold = decide_health_failure(
        current_failures=2,
        threshold=3,
        retry_cooldown_seconds=300,
        code=AccountHealthFailureCode.CREDENTIAL_EXPIRED,
        now=now,
    )
    drift = decide_health_failure(
        current_failures=0,
        threshold=3,
        retry_cooldown_seconds=300,
        code=AccountHealthFailureCode.ROLE_DRIFT,
        now=now,
    )

    assert threshold.health_status is AccountHealth.QUARANTINED
    assert threshold.reason is AccountStateTransitionReason.FAILURE_THRESHOLD_REACHED
    assert threshold.consecutive_health_failures == 3
    assert drift.health_status is AccountHealth.QUARANTINED
    assert drift.reason is AccountStateTransitionReason.ROLE_DRIFT
    assert drift.consecutive_health_failures == 1


def test_infrastructure_failure_does_not_increment_account_failure_count() -> None:
    state = decide_health_failure(
        current_failures=2,
        threshold=3,
        retry_cooldown_seconds=60,
        code=AccountHealthFailureCode.PROVIDER_UNAVAILABLE,
        now=datetime.now(UTC),
    )

    assert state.health_status is AccountHealth.DEGRADED
    assert state.consecutive_health_failures == 2
    assert state.reason is AccountStateTransitionReason.VERIFICATION_FAILED


@pytest.mark.parametrize("code", tuple(AccountHealthFailureCode))
def test_health_failure_outcome_is_safe_and_complete(
    code: AccountHealthFailureCode,
) -> None:
    outcome = health_failure_outcome(code)

    assert outcome.succeeded is False
    assert outcome.identity_fingerprint is None
    assert outcome.failure_code is code
    assert outcome.safe_summary
    assert outcome.retryable is (
        code
        in {
            AccountHealthFailureCode.RATE_LIMITED,
            AccountHealthFailureCode.PROVIDER_UNAVAILABLE,
            AccountHealthFailureCode.NETWORK_TIMEOUT,
            AccountHealthFailureCode.SECRET_UNAVAILABLE,
            AccountHealthFailureCode.INTERNAL_ERROR,
            AccountHealthFailureCode.STALE_SNAPSHOT,
        }
    )


@pytest.mark.parametrize(
    ("adapter_code", "health_code"),
    [
        (AdapterErrorCode.CONFIGURATION_INVALID, AccountHealthFailureCode.PROVIDER_UNAVAILABLE),
        (AdapterErrorCode.CAPABILITY_UNSUPPORTED, AccountHealthFailureCode.CAPABILITY_UNSUPPORTED),
        (AdapterErrorCode.AUTHENTICATION_FAILED, AccountHealthFailureCode.AUTHENTICATION_FAILED),
        (AdapterErrorCode.CREDENTIAL_EXPIRED, AccountHealthFailureCode.CREDENTIAL_EXPIRED),
        (AdapterErrorCode.MANUAL_ACTION_REQUIRED, AccountHealthFailureCode.MANUAL_ACTION_REQUIRED),
        (AdapterErrorCode.ACCOUNT_LOCKED, AccountHealthFailureCode.ACCOUNT_LOCKED),
        (AdapterErrorCode.RATE_LIMITED, AccountHealthFailureCode.RATE_LIMITED),
        (AdapterErrorCode.PROVIDER_UNAVAILABLE, AccountHealthFailureCode.PROVIDER_UNAVAILABLE),
        (AdapterErrorCode.NETWORK_TIMEOUT, AccountHealthFailureCode.NETWORK_TIMEOUT),
        (AdapterErrorCode.INTERNAL_ERROR, AccountHealthFailureCode.INTERNAL_ERROR),
    ],
)
def test_adapter_failures_map_to_stable_health_codes(
    adapter_code: AdapterErrorCode,
    health_code: AccountHealthFailureCode,
) -> None:
    error = AdapterOperationError(
        AdapterError(
            code=adapter_code,
            category="provider",
            operation="authenticate",
            safe_message="safe failure",
            retryable=False,
            request_id="request-1",
        )
    )

    assert map_adapter_health_failure(error) is health_code


def test_service_rejects_invalid_timing_and_fingerprint_scope() -> None:
    with pytest.raises(ValueError, match="verification_timeout"):
        AccountHealthService(
            object(),  # type: ignore[arg-type]
            adapter_registry=object(),  # type: ignore[arg-type]
            secret_provider=None,
            verification_timeout=timedelta(0),
        )
    with pytest.raises(ValueError, match="attempt_ttl"):
        AccountHealthService(
            object(),  # type: ignore[arg-type]
            adapter_registry=object(),  # type: ignore[arg-type]
            secret_provider=None,
            verification_timeout=timedelta(seconds=10),
            attempt_ttl=timedelta(seconds=10),
        )
    with pytest.raises(ValueError, match="connector_id"):
        AccountHealthService.identity_fingerprint(None, "provider-subject")

    connector_id = uuid7()
    first = AccountHealthService.identity_fingerprint(connector_id, " subject ")
    second = AccountHealthService.identity_fingerprint(connector_id, "subject")
    assert first == second
    assert first.startswith("sha256:")
    assert "subject" not in first
