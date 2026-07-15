"""Pure WorkflowPatch application helpers."""

from atlas_testops.domain.case.models import (
    AddEdgeOperation,
    AddNodeOperation,
    PatchIssue,
    PatchIssueCode,
    RemoveEdgeOperation,
    RemoveNodeOperation,
    ReplaceEdgeOperation,
    ReplaceNodeOperation,
    WorkflowPatch,
    WorkflowPatchPreview,
    canonical_digest,
)
from atlas_testops.domain.workflow import GraphIssueCode, WorkflowGraph, validate_workflow_graph

_NON_PERSISTABLE_GRAPH_ISSUES = {
    GraphIssueCode.DUPLICATE_NODE_ID,
    GraphIssueCode.DUPLICATE_EDGE_ID,
    GraphIssueCode.DANGLING_EDGE,
}


def canonical_workflow_graph(graph: WorkflowGraph) -> WorkflowGraph:
    """Return a stable graph ordering for persistence, hashing, and compilation."""

    return WorkflowGraph(
        nodes=tuple(sorted(graph.nodes, key=lambda item: item.id)),
        edges=tuple(sorted(graph.edges, key=lambda item: item.id)),
    )


def semantic_digest(graph: WorkflowGraph, intent_version_ref: str) -> str:
    """Digest semantic content while excluding layout and revision counters."""

    normalized_graph = canonical_workflow_graph(graph)
    return canonical_digest(
        {
            "intentVersionRef": intent_version_ref,
            "graph": normalized_graph.model_dump(mode="json", by_alias=True),
        }
    )


def preview_workflow_patch(
    graph: WorkflowGraph,
    patch: WorkflowPatch,
    *,
    intent_version_ref: str,
) -> WorkflowPatchPreview:
    """Apply one patch in memory and return deterministic validation facts."""

    nodes = {node.id: node for node in graph.nodes}
    edges = {edge.id: edge for edge in graph.edges}
    issues: list[PatchIssue] = []

    for index, operation in enumerate(patch.operations):
        if isinstance(operation, AddNodeOperation):
            if operation.node.id in nodes:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.NODE_ALREADY_EXISTS,
                        message="The node already exists in this draft.",
                        operation_index=index,
                        node_id=operation.node.id,
                    )
                )
            else:
                nodes[operation.node.id] = operation.node
        elif isinstance(operation, ReplaceNodeOperation):
            if operation.node_id not in nodes:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.NODE_NOT_FOUND,
                        message="The node to replace does not exist.",
                        operation_index=index,
                        node_id=operation.node_id,
                    )
                )
            else:
                nodes[operation.node_id] = operation.node
        elif isinstance(operation, RemoveNodeOperation):
            if operation.node_id not in nodes:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.NODE_NOT_FOUND,
                        message="The node to remove does not exist.",
                        operation_index=index,
                        node_id=operation.node_id,
                    )
                )
            else:
                del nodes[operation.node_id]
        elif isinstance(operation, AddEdgeOperation):
            if operation.edge.id in edges:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.EDGE_ALREADY_EXISTS,
                        message="The edge already exists in this draft.",
                        operation_index=index,
                        edge_id=operation.edge.id,
                    )
                )
            else:
                edges[operation.edge.id] = operation.edge
        elif isinstance(operation, ReplaceEdgeOperation):
            if operation.edge_id not in edges:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.EDGE_NOT_FOUND,
                        message="The edge to replace does not exist.",
                        operation_index=index,
                        edge_id=operation.edge_id,
                    )
                )
            else:
                edges[operation.edge_id] = operation.edge
        elif isinstance(operation, RemoveEdgeOperation):
            if operation.edge_id not in edges:
                issues.append(
                    PatchIssue(
                        code=PatchIssueCode.EDGE_NOT_FOUND,
                        message="The edge to remove does not exist.",
                        operation_index=index,
                        edge_id=operation.edge_id,
                    )
                )
            else:
                del edges[operation.edge_id]

    next_graph = canonical_workflow_graph(
        WorkflowGraph(
        nodes=tuple(sorted(nodes.values(), key=lambda item: item.id)),
        edges=tuple(sorted(edges.values(), key=lambda item: item.id)),
        )
    )
    validation = validate_workflow_graph(next_graph)
    for graph_issue in validation.issues:
        if graph_issue.code not in _NON_PERSISTABLE_GRAPH_ISSUES:
            continue
        issues.append(
            PatchIssue(
                code=PatchIssueCode.DANGLING_EDGE,
                message=graph_issue.message,
                edge_id=graph_issue.edge_id,
                node_id=graph_issue.node_id,
            )
        )

    return WorkflowPatchPreview(
        applicable=not issues,
        issues=tuple(issues),
        graph=next_graph,
        semantic_digest=semantic_digest(next_graph, intent_version_ref),
        validation=validation,
    )
