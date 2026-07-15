"""WorkflowPatch protocol tests."""

from uuid import UUID

from atlas_testops.domain.case import (
    AddEdgeOperation,
    AddNodeOperation,
    DraftOperation,
    PatchIssueCode,
    RemoveEdgeOperation,
    RemoveNodeOperation,
    WorkflowPatch,
    preview_workflow_patch,
    semantic_digest,
)
from atlas_testops.domain.workflow import DraftAuthor, GraphIssueCode, WorkflowGraph

INTENT_REF = "intent.customer-filter@1.0.0"


def _patch(*operations: DraftOperation) -> WorkflowPatch:
    return WorkflowPatch.model_validate(
        {
            "patchId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "clientMutationId": "mutation-0001",
            "baseSemanticRevision": 4,
            "source": "human",
            "operations": [
                operation.model_dump(mode="json", by_alias=True)
                for operation in operations
            ],
        }
    )


def test_applies_atomic_node_and_edge_addition(valid_graph: WorkflowGraph) -> None:
    base = WorkflowGraph(nodes=valid_graph.nodes[:-1], edges=valid_graph.edges[:-1])

    preview = preview_workflow_patch(
        base,
        _patch(
            AddNodeOperation(node=valid_graph.nodes[-1]),
            AddEdgeOperation(edge=valid_graph.edges[-1]),
        ),
        intent_version_ref=INTENT_REF,
    )

    assert preview.applicable is True
    assert preview.issues == ()
    assert preview.validation.valid is True
    assert preview.graph == WorkflowGraph(
        nodes=tuple(sorted(valid_graph.nodes, key=lambda item: item.id)),
        edges=tuple(sorted(valid_graph.edges, key=lambda item: item.id)),
    )


def test_separates_applicability_from_semantic_validity(
    valid_graph: WorkflowGraph,
) -> None:
    preview = preview_workflow_patch(
        valid_graph,
        _patch(RemoveEdgeOperation(edge_id="assert-to-cleanup")),
        intent_version_ref=INTENT_REF,
    )

    assert preview.applicable is True
    assert preview.issues == ()
    assert preview.validation.valid is False
    assert GraphIssueCode.REQUIRED_INPUT_MISSING in {
        issue.code for issue in preview.validation.issues
    }


def test_rejects_duplicate_and_dangling_operations(valid_graph: WorkflowGraph) -> None:
    duplicate = preview_workflow_patch(
        valid_graph,
        _patch(AddNodeOperation(node=valid_graph.nodes[0])),
        intent_version_ref=INTENT_REF,
    )
    dangling = preview_workflow_patch(
        valid_graph,
        _patch(RemoveNodeOperation(node_id="cleanup")),
        intent_version_ref=INTENT_REF,
    )

    assert duplicate.applicable is False
    assert duplicate.issues[0].code is PatchIssueCode.NODE_ALREADY_EXISTS
    assert dangling.applicable is False
    assert PatchIssueCode.DANGLING_EDGE in {issue.code for issue in dangling.issues}


def test_semantic_digest_ignores_collection_order(valid_graph: WorkflowGraph) -> None:
    reordered = WorkflowGraph(
        nodes=tuple(reversed(valid_graph.nodes)),
        edges=tuple(reversed(valid_graph.edges)),
    )

    assert semantic_digest(valid_graph, INTENT_REF) == semantic_digest(
        reordered,
        INTENT_REF,
    )


def test_patch_round_trips_camel_case_wire_contract(valid_graph: WorkflowGraph) -> None:
    patch = _patch(AddNodeOperation(node=valid_graph.nodes[0]))
    payload = patch.model_dump(mode="json", by_alias=True)

    assert patch.patch_id == UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert patch.source is DraftAuthor.HUMAN
    assert payload["operations"][0]["op"] == "ADD_NODE"
    assert payload["baseSemanticRevision"] == 4
