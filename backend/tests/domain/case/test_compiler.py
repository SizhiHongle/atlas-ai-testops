"""Deterministic TestCase compiler tests."""

from collections.abc import Callable
from uuid import UUID

from atlas_testops.domain.case import (
    CaseCompilationResult,
    CaseCompileIssueCode,
    compile_case,
)
from atlas_testops.domain.case import (
    TestIntent as CaseIntent,
)
from atlas_testops.domain.workflow import WorkflowGraph

CASE_ID = UUID("33333333-3333-4333-8333-333333333333")
INTENT_REF = "intent.customer-filter@1.0.0"


def _compile(intent: CaseIntent, graph: WorkflowGraph) -> CaseCompilationResult:
    return compile_case(
        test_case_id=CASE_ID,
        semantic_revision=7,
        intent_version_ref=INTENT_REF,
        intent=intent,
        graph=graph,
    )


def test_compiles_exact_test_ir_and_plan_template(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    first = _compile(intent_factory(), valid_graph)
    second = _compile(
        intent_factory(),
        WorkflowGraph(
            nodes=tuple(reversed(valid_graph.nodes)),
            edges=tuple(reversed(valid_graph.edges)),
        ),
    )

    assert first.valid is True
    assert first.issues == ()
    assert first.test_ir is not None
    assert first.plan_template is not None
    assert second.test_ir is not None
    assert second.plan_template is not None
    assert first.test_ir.schema_version == "atlas.test-ir/0.2"
    assert first.plan_template.schema_version == "atlas.plan-template/0.1"
    assert first.test_ir.content_digest == second.test_ir.content_digest
    assert first.plan_template.plan_digest == second.plan_template.plan_digest
    assert first.compiled_digest == second.compiled_digest
    assert first.test_ir.assertions[0].node_id == "relationship-assert"
    assert "node:agent" in first.test_ir.required_features


def test_rejects_missing_exact_bindings(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    result = _compile(
        intent_factory(
            requirement_refs=(),
            actors=(),
            fixture=None,
            surfaces=(),
        ),
        valid_graph,
    )

    assert result.valid is False
    assert result.test_ir is None
    assert result.plan_template is None
    assert {issue.code for issue in result.issues} == {
        CaseCompileIssueCode.REQUIREMENT_SOURCE_MISSING,
        CaseCompileIssueCode.ACTOR_BINDING_MISSING,
        CaseCompileIssueCode.FIXTURE_BINDING_MISSING,
        CaseCompileIssueCode.SURFACE_BINDING_MISSING,
    }


def test_rejects_dynamic_capabilities(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    unsafe_agent = valid_graph.nodes[1].model_copy(
        update={"params": {"targetUrl": "relative-admin-path"}}
    )
    unsafe_graph = WorkflowGraph(
        nodes=(valid_graph.nodes[0], unsafe_agent, *valid_graph.nodes[2:]),
        edges=valid_graph.edges,
    )

    result = _compile(intent_factory(), unsafe_graph)

    assert result.valid is False
    assert {issue.code for issue in result.issues} == {
        CaseCompileIssueCode.FORBIDDEN_CAPABILITY
    }
    assert result.issues[0].node_id == "filter-agent"


def test_requires_graph_fixture_to_match_exact_intent_binding(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    mismatched_fixture = valid_graph.nodes[0].model_copy(
        update={"version_ref": "fixture.customer@2.0.0"}
    )
    graph = WorkflowGraph(
        nodes=(mismatched_fixture, *valid_graph.nodes[1:]),
        edges=valid_graph.edges,
    )

    result = _compile(intent_factory(), graph)

    assert result.valid is False
    assert {issue.code for issue in result.issues} == {
        CaseCompileIssueCode.FIXTURE_GRAPH_MISMATCH
    }


def test_requires_hard_oracle_from_outcome_policy(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    non_agent = valid_graph.nodes[1].model_copy(update={"kind": "action"})
    non_oracle = valid_graph.nodes[2].model_copy(update={"oracle_strength": None})
    graph = WorkflowGraph(
        nodes=(valid_graph.nodes[0], non_agent, non_oracle, valid_graph.nodes[3]),
        edges=valid_graph.edges,
    )

    result = _compile(intent_factory(), graph)

    assert result.valid is False
    assert {issue.code for issue in result.issues} == {
        CaseCompileIssueCode.HARD_ORACLE_MISSING
    }
