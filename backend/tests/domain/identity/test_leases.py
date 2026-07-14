"""测试账号租约领域协议与安全投影。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.identity import (
    AccountLease,
    AccountLeaseStatus,
    CredentialAuthMethod,
    LeaseReleaseReason,
    LeaseRequirements,
    lease_fence_matches,
    lease_is_expired,
)


def make_lease(
    *,
    status: AccountLeaseStatus = AccountLeaseStatus.ACTIVE,
    now: datetime | None = None,
) -> AccountLease:
    observed_at = now or datetime.now(UTC)
    terminal = status is not AccountLeaseStatus.ACTIVE
    return AccountLease(
        id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        pool_id=uuid7(),
        account_id=uuid7(),
        slot_id=uuid7(),
        execution_id="execution-001",
        worker_id="worker-001",
        account_handle=f"ah_{uuid7().hex}",
        fencing_token=7,
        ttl_seconds=1800,
        status=status,
        acquired_at=observed_at,
        heartbeat_at=observed_at,
        expires_at=observed_at + timedelta(minutes=30),
        max_expires_at=observed_at + timedelta(hours=2),
        released_at=observed_at + timedelta(minutes=1) if terminal else None,
        release_reason=LeaseReleaseReason.COMPLETED if terminal else None,
        revision=2 if terminal else 1,
        updated_at=observed_at,
    )


def test_lease_requirements_are_normalized_and_safe() -> None:
    requirements = LeaseRequirements(
        tags=("Region: cn", "persona:new_customer"),
        auth_methods=(CredentialAuthMethod.PASSWORD, CredentialAuthMethod.PASSWORD),
        capabilities=("Visit:Create", "customer.read"),
    )

    assert requirements.tags == ("persona:new_customer", "region:cn")
    assert requirements.label_filter() == {
        "persona": "new_customer",
        "region": "cn",
    }
    assert requirements.auth_methods == (CredentialAuthMethod.PASSWORD,)
    assert requirements.capabilities == ("customer.read", "visit:create")


@pytest.mark.parametrize("tag", ["missing-separator", "password:value", "region:"])
def test_lease_requirements_reject_invalid_or_sensitive_tags(tag: str) -> None:
    with pytest.raises(ValidationError):
        LeaseRequirements(tags=(tag,))


def test_account_lease_handle_excludes_internal_account_identity() -> None:
    lease = make_lease()

    payload = lease.to_handle().model_dump(mode="json", by_alias=True)

    assert payload["leaseId"] == str(lease.id)
    assert payload["accountHandle"] == lease.account_handle
    assert payload["heartbeatAfterSeconds"] == 600
    assert "accountId" not in payload
    assert "slotId" not in payload
    assert "poolId" not in payload
    assert "workerId" not in payload


def test_lease_expiry_and_fence_require_both_tokens() -> None:
    now = datetime.now(UTC)
    lease = make_lease(now=now)

    assert lease_is_expired(lease, now + timedelta(minutes=30)) is True
    assert lease_fence_matches(lease, 7, 7) is True
    assert lease_fence_matches(lease, 6, 7) is False
    assert lease_fence_matches(lease, 7, 8) is False


def test_terminal_lease_requires_release_metadata() -> None:
    lease = make_lease()
    payload = lease.model_dump()
    payload["status"] = AccountLeaseStatus.RELEASED

    with pytest.raises(ValidationError, match="terminal metadata"):
        AccountLease.model_validate(payload)
