"""领域事件协议测试。"""

from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.events import DomainEvent


def test_domain_event_uses_versioned_camel_case_contract() -> None:
    event = DomainEvent(
        tenant_id=uuid7(),
        aggregate_type="project",
        aggregate_id=uuid7(),
        event_type="project.created",
        payload={"projectName": "Atlas"},
    )

    payload = event.model_dump(mode="json", by_alias=True)
    assert payload["schemaVersion"] == "atlas.domain-event/0.1"
    assert event.event_id.version == 7


def test_domain_event_rejects_unstable_event_names() -> None:
    with pytest.raises(ValidationError):
        DomainEvent(
            tenant_id=uuid7(),
            aggregate_type="Project Type",
            aggregate_id=uuid7(),
            event_type="Project Created",
        )
