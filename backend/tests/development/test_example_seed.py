"""Contract tests for the local example seed payloads."""

from uuid import UUID

from atlas_testops.development.example_seed import (
    BLUEPRINT_VERSION_REF,
    build_atom_examples,
    build_blueprint_commands,
    build_search_case_command,
)
from atlas_testops.domain.case import CreateTestCase, compile_case
from atlas_testops.domain.workflow import validate_workflow_graph

DIGEST = f"sha256:{'a' * 64}"
ROLE_ID = UUID("11111111-1111-4111-8111-111111111111")
BLUEPRINT_VERSION_ID = UUID("22222222-2222-4222-8222-222222222222")
KEYWORD_ATOM_VERSION_ID = UUID("33333333-3333-4333-8333-333333333333")
EXPECTATION_ATOM_VERSION_ID = UUID("44444444-4444-4444-8444-444444444444")
CASE_ID = UUID("55555555-5555-4555-8555-555555555555")


def test_example_fixture_contracts_are_well_formed() -> None:
    atoms = build_atom_examples()

    assert [item.definition.atom_key for item in atoms] == [
        "demo.web.search-keyword",
        "demo.web.search-expectation",
    ]
    assert all(item.version.contract.effect.value == "READ" for item in atoms)

    definition, version = build_blueprint_commands(
        keyword_atom_version_id=KEYWORD_ATOM_VERSION_ID,
        expectation_atom_version_id=EXPECTATION_ATOM_VERSION_ID,
    )

    assert definition.blueprint_key == "demo.web.search-context"
    assert len(version.contract.nodes) == 2
    assert {item.name for item in version.contract.exports} == {
        "searchKeyword",
        "expectedText",
    }


def test_baidu_search_case_graph_validates_and_compiles() -> None:
    command = build_search_case_command(
        case_key="WEB-BAIDU-SEARCH-AAA",
        name="百度搜索 AAA",
        keyword="AAA",
        role={
            "id": str(ROLE_ID),
            "roleKey": "public.web.visitor",
            "revision": 1,
            "capabilities": [
                "web.page.open",
                "web.search.read",
                "web.search.submit",
            ],
        },
        blueprint_version={
            "id": str(BLUEPRINT_VERSION_ID),
            "contentDigest": DIGEST,
        },
    )

    validation = validate_workflow_graph(command.graph)
    compilation = compile_case(
        test_case_id=CASE_ID,
        semantic_revision=1,
        intent_version_ref=f"test-intent/{CASE_ID}@0.1.0",
        intent=command.intent,
        graph=command.graph,
    )
    wire_command = CreateTestCase.model_validate(
        command.model_dump(mode="json", by_alias=True, exclude_none=True)
    )

    assert validation.valid is True
    assert validation.matched_required_inputs == validation.total_required_inputs == 5
    assert command.intent.fixture is not None
    assert command.intent.fixture.blueprint_version_ref == BLUEPRINT_VERSION_REF
    assert compilation.valid is True
    assert compilation.test_ir is not None
    assert compilation.plan_template is not None
    assert wire_command.intent.variables["searchKeyword"].value == "AAA"
