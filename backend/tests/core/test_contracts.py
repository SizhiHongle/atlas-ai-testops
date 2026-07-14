"""共享契约基础测试。"""

from datetime import UTC

import pytest
from pydantic import ValidationError

from atlas_testops.core.contracts import FrozenWireModel, WireModel, new_entity_id, utc_now


class ExampleWireModel(WireModel):
    """用于验证别名和未知字段策略。"""

    display_name: str


class ExampleFact(FrozenWireModel):
    """用于验证不可变事实。"""

    sequence: int


def test_wire_model_round_trips_camel_case() -> None:
    model = ExampleWireModel.model_validate({"displayName": "Atlas"})

    assert model.display_name == "Atlas"
    assert model.model_dump(by_alias=True) == {"displayName": "Atlas"}


def test_wire_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExampleWireModel.model_validate({"displayName": "Atlas", "unknown": True})


def test_frozen_wire_model_cannot_be_changed() -> None:
    fact = ExampleFact(sequence=1)

    with pytest.raises(ValidationError):
        fact.sequence = 2


def test_ids_and_time_use_ordered_uuid_and_utc() -> None:
    entity_id = new_entity_id()
    current = utc_now()

    assert entity_id.version == 7
    assert current.tzinfo is UTC
