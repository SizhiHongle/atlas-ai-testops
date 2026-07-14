"""测试身份目录领域模型与可用性计算测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.identity import (
    AccountAvailabilityReason,
    AccountHealth,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountPoolStatus,
    AccountSource,
    AccountSyncStatus,
    CreateTestAccount,
    CreateTestRole,
    CredentialAuthMethod,
    CredentialBindingInput,
    account_availability_reason,
    require_masked_login_hint,
    validate_labels,
)


def test_role_capabilities_are_normalized_and_deduplicated() -> None:
    command = CreateTestRole(
        role_key="sales_manager",
        name=" 销售主管 ",
        capabilities=("Customer.Read", "customer.read", "team:manage"),
    )

    assert command.name == "销售主管"
    assert command.capabilities == ("customer.read", "team:manage")


@pytest.mark.parametrize(
    "labels",
    [
        {"password": "must-not-exist"},
        {"session_token_hint": "must-not-exist"},
        {"UPPER SPACE": "value"},
        {"region": ""},
    ],
)
def test_labels_reject_sensitive_or_invalid_values(labels: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        validate_labels(labels)


def test_login_hint_must_already_be_masked() -> None:
    assert require_masked_login_hint("sa***@example.test") == "sa***@example.test"
    with pytest.raises(ValueError, match="must be masked"):
        require_masked_login_hint("sales@example.test")


def test_external_account_requires_subject_and_managed_account_forbids_it() -> None:
    credential = CredentialBindingInput(
        auth_method=CredentialAuthMethod.PASSWORD,
        secret_ref="sec_identity_test_001",
        secret_version="v1",
    )

    with pytest.raises(ValidationError):
        CreateTestAccount(
            connector_installation_id=uuid7(),
            account_key="sales-01",
            source=AccountSource.EXTERNAL_SYNCED,
            login_hint_masked="sa***@example.test",
            credentials=(credential,),
        )
    with pytest.raises(ValidationError):
        CreateTestAccount(
            connector_installation_id=uuid7(),
            account_key="sales-01",
            source=AccountSource.ATLAS_MANAGED,
            external_subject_id="external-1",
            login_hint_masked="sa***@example.test",
            credentials=(credential,),
        )


def test_account_availability_is_computed_from_orthogonal_state() -> None:
    now = datetime.now(UTC)

    def availability(
        *,
        cooldown_until: datetime | None = None,
        health_status: AccountHealth = AccountHealth.HEALTHY,
        active_lease: bool = False,
    ) -> AccountAvailabilityReason:
        return account_availability_reason(
            pool_status=AccountPoolStatus.ACTIVE,
            lifecycle_status=AccountLifecycle.ACTIVE,
            health_status=health_status,
            operational_status=AccountOperationalStatus.READY,
            sync_status=AccountSyncStatus.NOT_APPLICABLE,
            cooldown_until=cooldown_until,
            credential_valid=True,
            slot_available=True,
            active_lease=active_lease,
            now=now,
        )

    assert availability() is AccountAvailabilityReason.AVAILABLE
    assert availability(
        cooldown_until=now + timedelta(minutes=1)
    ) is AccountAvailabilityReason.COOLDOWN_ACTIVE
    assert availability(
        health_status=AccountHealth.QUARANTINED
    ) is AccountAvailabilityReason.HEALTH_NOT_HEALTHY
    assert availability(active_lease=True) is AccountAvailabilityReason.LEASED
