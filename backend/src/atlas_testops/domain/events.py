"""可靠事件发布使用的领域事件信封。"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from atlas_testops.core.contracts import FrozenWireModel, new_entity_id, utc_now

DOMAIN_EVENT_SCHEMA_VERSION: Literal["atlas.domain-event/0.1"] = "atlas.domain-event/0.1"


class DomainEvent(FrozenWireModel):
    """与业务状态在同一事务写入 Outbox 的不可变事件。"""

    schema_version: Literal["atlas.domain-event/0.1"] = DOMAIN_EVENT_SCHEMA_VERSION
    event_id: UUID = Field(default_factory=new_entity_id)
    tenant_id: UUID
    aggregate_type: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]+$")
    aggregate_id: UUID
    event_type: str = Field(min_length=1, max_length=160, pattern=r"^[a-z][a-z0-9_.-]+$")
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
