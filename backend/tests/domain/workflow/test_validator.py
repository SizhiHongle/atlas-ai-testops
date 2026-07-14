"""Workflow Graph validator tests."""

import pytest
from pydantic import ValidationError

from atlas_testops.domain.workflow import (
    DraftAuthor,
    GraphIssueCode,
    NodeLayout,
    OracleStrength,
    PortSpec,
    WorkflowDraft,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPhase,
    validate_workflow_graph,
)


def port(key: str, semantic_type: str, *, required: bool = True) -> PortSpec:
    return PortSpec(key=key, semantic_type=semantic_type, required=required)


def valid_graph() -> WorkflowGraph:
    nodes = (
        WorkflowNode(
            id="prepare-data",
            kind="fixture",
            version_ref="fixture.customer@1.0.0",
            phase=WorkflowPhase.SETUP,
            output_ports=(port("customerId", "CustomerId"),),
        ),
        WorkflowNode(
            id="filter-agent",
            kind="agent",
            version_ref="agent.semantic-filter@1.0.0",
            phase=WorkflowPhase.EXECUTE,
            input_ports=(port("customerId", "CustomerId"),),
            output_ports=(port("rows", "CustomerRows"),),
        ),
        WorkflowNode(
            id="relationship-assert",
            kind="assertion",
            version_ref="assert.customer-visible@1.0.0",
            phase=WorkflowPhase.ASSERT,
            input_ports=(port("rows", "CustomerRows"),),
            output_ports=(port("result", "AssertionResult"),),
            oracle_strength=OracleStrength.HARD,
        ),
        WorkflowNode(
            id="cleanup",
            kind="cleanup",
            version_ref="cleanup.customer@1.0.0",
            phase=WorkflowPhase.CLEANUP,
            input_ports=(port("result", "AssertionResult"),),
            terminal=True,
        ),
    )
    edges = (
        WorkflowEdge(
            id="data-to-agent",
            source_node_id="prepare-data",
            source_port="customerId",
            target_node_id="filter-agent",
            target_port="customerId",
            semantic_type="CustomerId",
        ),
        WorkflowEdge(
            id="agent-to-assert",
            source_node_id="filter-agent",
            source_port="rows",
            target_node_id="relationship-assert",
            target_port="rows",
            semantic_type="CustomerRows",
        ),
        WorkflowEdge(
            id="assert-to-cleanup",
            source_node_id="relationship-assert",
            source_port="result",
            target_node_id="cleanup",
            target_port="result",
            semantic_type="AssertionResult",
        ),
    )
    return WorkflowGraph(nodes=nodes, edges=edges)


def issue_codes(graph: WorkflowGraph) -> set[GraphIssueCode]:
    return {issue.code for issue in validate_workflow_graph(graph).issues}


def test_rejects_empty_graph() -> None:
    result = validate_workflow_graph(WorkflowGraph(nodes=(), edges=()))

    assert result.valid is False
    assert {issue.code for issue in result.issues} == {GraphIssueCode.EMPTY_GRAPH}


def test_accepts_closed_typed_dag() -> None:
    result = validate_workflow_graph(valid_graph())

    assert result.valid is True
    assert result.issues == ()
    assert result.matched_required_inputs == 3
    assert result.total_required_inputs == 3
    assert result.execution_levels == (
        ("prepare-data",),
        ("filter-agent",),
        ("relationship-assert",),
        ("cleanup",),
    )


def test_rejects_cycle() -> None:
    graph = valid_graph()
    cycle_edge = WorkflowEdge(
        id="cleanup-to-data",
        source_node_id="cleanup",
        source_port="cleanupResult",
        target_node_id="prepare-data",
        target_port="seed",
        semantic_type="CleanupResult",
    )
    cleanup = graph.nodes[-1].model_copy(
        update={"output_ports": (port("cleanupResult", "CleanupResult"),), "terminal": False}
    )
    prepare = graph.nodes[0].model_copy(update={"input_ports": (port("seed", "CleanupResult"),)})
    cyclic_graph = WorkflowGraph(
        nodes=(prepare, *graph.nodes[1:-1], cleanup),
        edges=(*graph.edges, cycle_edge),
    )

    assert GraphIssueCode.GRAPH_CYCLE in issue_codes(cyclic_graph)


def test_rejects_type_mismatch_and_multiple_writers() -> None:
    graph = valid_graph()
    duplicate_writer = WorkflowEdge(
        id="second-data-writer",
        source_node_id="prepare-data",
        source_port="customerId",
        target_node_id="filter-agent",
        target_port="customerId",
        semantic_type="CustomerId",
    )
    mismatched_agent = graph.nodes[1].model_copy(
        update={"input_ports": (port("customerId", "TrackingId"),)}
    )
    invalid_graph = WorkflowGraph(
        nodes=(graph.nodes[0], mismatched_agent, *graph.nodes[2:]),
        edges=(*graph.edges, duplicate_writer),
    )

    codes = issue_codes(invalid_graph)
    assert GraphIssueCode.PORT_TYPE_MISMATCH in codes
    assert GraphIssueCode.INPUT_MULTIPLE_WRITERS in codes


def test_requires_hard_oracle_after_agent() -> None:
    graph = valid_graph()
    soft_assertion = graph.nodes[2].model_copy(update={"oracle_strength": OracleStrength.SOFT})
    invalid_graph = WorkflowGraph(
        nodes=(*graph.nodes[:2], soft_assertion, graph.nodes[3]),
        edges=graph.edges,
    )

    assert GraphIssueCode.ASSERTION_COVERAGE_MISSING in issue_codes(invalid_graph)


def test_detects_dangling_edges_and_missing_inputs() -> None:
    graph = valid_graph()
    dangling_edge = graph.edges[0].model_copy(update={"source_node_id": "missing-node"})
    invalid_graph = WorkflowGraph(nodes=graph.nodes, edges=(dangling_edge, *graph.edges[1:]))

    codes = issue_codes(invalid_graph)
    assert GraphIssueCode.DANGLING_EDGE in codes
    assert GraphIssueCode.REQUIRED_INPUT_MISSING in codes


def test_rejects_non_exact_version_reference() -> None:
    node = valid_graph().nodes[0]
    payload = {**node.model_dump(), "version_ref": "fixture.customer@latest"}

    with pytest.raises(ValidationError):
        WorkflowNode.model_validate(payload)


def test_rejects_incorrect_edge_semantic_type_declaration() -> None:
    graph = valid_graph()
    invalid_edge = graph.edges[0].model_copy(update={"semantic_type": "TrackingId"})
    invalid_graph = WorkflowGraph(nodes=graph.nodes, edges=(invalid_edge, *graph.edges[1:]))

    assert GraphIssueCode.EDGE_TYPE_DECLARATION_MISMATCH in issue_codes(invalid_graph)


def test_round_trips_camel_case_wire_contract() -> None:
    graph = valid_graph()
    payload = graph.model_dump(mode="json", by_alias=True)

    assert payload["schemaVersion"] == "atlas.workflow-graph/0.1"
    assert payload["nodes"][0]["versionRef"] == "fixture.customer@1.0.0"
    assert payload["edges"][0]["sourceNodeId"] == "prepare-data"
    assert WorkflowGraph.model_validate(payload) == graph


def test_models_workflow_draft_revisions_separately() -> None:
    graph = valid_graph()
    draft = WorkflowDraft(
        id="draft-1",
        test_case_id="case-1",
        semantic_revision=3,
        layout_revision=8,
        graph=graph,
        layout={"prepare-data": NodeLayout(x=120, y=80)},
        intent_version_ref="intent.customer-filter@1.0.0",
        updated_by=DraftAuthor.HUMAN,
    )

    payload = draft.model_dump(mode="json", by_alias=True)

    assert payload["semanticRevision"] == 3
    assert payload["layoutRevision"] == 8
    assert payload["layout"]["prepare-data"] == {"x": 120.0, "y": 80.0}
