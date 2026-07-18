"""UnitAttempt live-control contract and ETag invariants."""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.core.concurrency import (
    format_control_epoch_etag,
    parse_control_epoch_etag,
)
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.runtime import (
    ControlLease,
    ControlLeaseState,
    LiveActionExecutionStatus,
    LiveActionGrant,
    LiveActionGrantState,
    LiveControllerType,
    RequestLiveControl,
)


def test_control_epoch_etag_is_strong_and_exact() -> None:
    assert format_control_epoch_etag(7) == '"control-epoch-7"'
    assert parse_control_epoch_etag('"control-epoch-7"') == 7
    for invalid in ('W/"control-epoch-7"', "*", '"revision-7"', '"control-epoch-0"'):
        with pytest.raises(ApplicationError):
            parse_control_epoch_etag(invalid)


def test_control_lease_requires_terminal_release_time() -> None:
    now = datetime.now(UTC)
    values = {
        "id": uuid7(),
        "tenantId": uuid7(),
        "projectId": uuid7(),
        "taskRunId": uuid7(),
        "executionUnitId": uuid7(),
        "unitAttemptId": uuid7(),
        "liveSessionId": uuid7(),
        "ownerType": "AGENT",
        "ownerId": "worker:browser-1",
        "controlEpoch": 1,
        "fencingToken": 1,
        "state": "ACTIVE",
        "expiresAt": now + timedelta(minutes=2),
        "reason": "initial agent control",
        "createdAt": now,
        "updatedAt": now,
    }
    lease = ControlLease.model_validate(values)
    assert lease.owner_type is LiveControllerType.AGENT
    with pytest.raises(ValidationError):
        ControlLease.model_validate(
            {
                **values,
                "state": ControlLeaseState.RELEASED,
            }
        )


def test_action_grant_completed_state_requires_exact_receipt() -> None:
    now = datetime.now(UTC)
    values = {
        "grantId": uuid7(),
        "tenantId": uuid7(),
        "projectId": uuid7(),
        "taskRunId": uuid7(),
        "executionUnitId": uuid7(),
        "unitAttemptId": uuid7(),
        "liveSessionId": uuid7(),
        "controlLeaseId": uuid7(),
        "actionId": uuid7(),
        "proposalDigest": f"sha256:{'1' * 64}",
        "browserSessionId": "browser-session-1",
        "pageId": "page-1",
        "pageRevision": 4,
        "controlEpoch": 2,
        "fencingToken": 2,
        "ownerType": "HUMAN",
        "ownerId": "user:42",
        "allowedAdapter": "click",
        "expiresAt": now + timedelta(seconds=15),
        "policyDigest": f"sha256:{'2' * 64}",
        "state": "COMPLETED",
        "createdAt": now,
        "consumedAt": now + timedelta(seconds=1),
        "completedAt": now + timedelta(seconds=2),
        "receiptId": uuid7(),
        "executionStatus": LiveActionExecutionStatus.SUCCEEDED,
        "resultingPageRevision": 5,
    }
    grant = LiveActionGrant.model_validate(values)
    assert grant.state is LiveActionGrantState.COMPLETED
    with pytest.raises(ValidationError):
        LiveActionGrant.model_validate({**values, "receiptId": None})


def test_live_control_reason_rejects_all_control_characters() -> None:
    for reason in ("line\nbreak", "tab\tbreak", "delete\x7fbreak"):
        with pytest.raises(ValidationError):
            RequestLiveControl(reason=reason)
