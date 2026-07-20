"""TestIntent value source and binding invariant tests."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.case import (
    FixtureContract,
    ValueSource,
    ValueSourceKind,
)
from atlas_testops.domain.case import (
    TestIntent as CaseIntent,
)

DIGEST = f"sha256:{'a' * 64}"


def test_literal_value_source_preserves_explicit_json_null() -> None:
    source = ValueSource(
        kind=ValueSourceKind.LITERAL,
        reference=None,
        value=None,
    )

    assert "value" in source.model_fields_set
    assert source.value is None


def test_value_source_rejects_missing_or_extra_payload() -> None:
    with pytest.raises(ValidationError):
        ValueSource(kind=ValueSourceKind.LITERAL)

    with pytest.raises(ValidationError):
        ValueSource(
            kind=ValueSourceKind.FIXTURE,
            reference="customerId",
            value=None,
        )


def test_fixture_exports_are_normalized_and_unique() -> None:
    fixture = FixtureContract(
        blueprint_version_id=UUID("22222222-2222-4222-8222-222222222222"),
        blueprint_version_ref="fixture.customer@1.0.0",
        content_digest=DIGEST,
        required_exports={" customerId ": " CustomerId "},
    )

    assert fixture.required_exports == {"customerId": "CustomerId"}

    with pytest.raises(ValidationError):
        FixtureContract(
            blueprint_version_id=UUID("22222222-2222-4222-8222-222222222222"),
            blueprint_version_ref="fixture.customer@1.0.0",
            content_digest=DIGEST,
            required_exports={"customerId": "CustomerId", " customerId ": "Other"},
        )


def test_intent_variable_names_are_normalized_and_unique() -> None:
    source = ValueSource(kind=ValueSourceKind.RUN, reference="executionId")
    intent = CaseIntent(
        summary="Use one stable execution identifier.",
        variables={" executionId ": source},
    )

    assert intent.variables == {"executionId": source}

    with pytest.raises(ValidationError):
        CaseIntent(
            summary="Reject aliases that normalize to the same variable.",
            variables={"executionId": source, " executionId ": source},
        )
