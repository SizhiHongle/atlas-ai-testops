"""Pure Workflow Graph validation."""

from collections import defaultdict

from atlas_testops.domain.workflow.models import (
    GraphIssue,
    GraphIssueCode,
    GraphValidationResult,
    OracleStrength,
    PortSpec,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPhase,
)


def validate_workflow_graph(graph: WorkflowGraph) -> GraphValidationResult:
    """Validate graph structure, typed mappings, topology, and oracle coverage."""
    issues: list[GraphIssue] = []
    node_by_id: dict[str, WorkflowNode] = {}
    edge_ids: set[str] = set()
    incoming_edges: dict[str, list[WorkflowEdge]] = defaultdict(list)
    outgoing_edges: dict[str, list[WorkflowEdge]] = defaultdict(list)
    writers: dict[tuple[str, str], list[WorkflowEdge]] = defaultdict(list)

    if not graph.nodes:
        issues.append(
            GraphIssue(
                code=GraphIssueCode.EMPTY_GRAPH,
                message="Workflow Graph must contain at least one node.",
            )
        )

    for node in graph.nodes:
        if node.id in node_by_id:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.DUPLICATE_NODE_ID,
                    message=f"Node ID '{node.id}' is duplicated.",
                    node_id=node.id,
                )
            )
            continue
        node_by_id[node.id] = node
        _validate_port_keys(node, issues)

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
    indegree: dict[str, int] = dict.fromkeys(node_by_id, 0)

    for edge in graph.edges:
        if edge.id in edge_ids:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.DUPLICATE_EDGE_ID,
                    message=f"Edge ID '{edge.id}' is duplicated.",
                    edge_id=edge.id,
                )
            )
        edge_ids.add(edge.id)

        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.DANGLING_EDGE,
                    message=(f"Edge '{edge.id}' references a missing source or target node."),
                    edge_id=edge.id,
                )
            )
            continue

        incoming_edges[target.id].append(edge)
        outgoing_edges[source.id].append(edge)
        writers[(target.id, edge.target_port)].append(edge)
        if target.id not in adjacency[source.id]:
            adjacency[source.id].add(target.id)
            indegree[target.id] += 1

        source_port = _find_port(source.output_ports, edge.source_port)
        target_port = _find_port(target.input_ports, edge.target_port)
        if source_port is None:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.SOURCE_PORT_MISSING,
                    message=(
                        f"Node '{source.id}' does not publish output port '{edge.source_port}'."
                    ),
                    node_id=source.id,
                    edge_id=edge.id,
                )
            )
        if target_port is None:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.TARGET_PORT_MISSING,
                    message=(
                        f"Node '{target.id}' does not declare input port '{edge.target_port}'."
                    ),
                    node_id=target.id,
                    edge_id=edge.id,
                )
            )
        if source_port is None or target_port is None:
            continue
        if source_port.kind != target_port.kind or edge.kind != source_port.kind:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.PORT_KIND_MISMATCH,
                    message=f"Edge '{edge.id}' mixes data and control ports.",
                    edge_id=edge.id,
                )
            )
        if source_port.semantic_type != target_port.semantic_type:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.PORT_TYPE_MISMATCH,
                    message=(
                        f"Edge '{edge.id}' maps '{source_port.semantic_type}' to "
                        f"'{target_port.semantic_type}'."
                    ),
                    edge_id=edge.id,
                )
            )
        if (
            edge.semantic_type
            not in {
                source_port.semantic_type,
                target_port.semantic_type,
            }
            or source_port.semantic_type != target_port.semantic_type
        ):
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.EDGE_TYPE_DECLARATION_MISMATCH,
                    message=(
                        f"Edge '{edge.id}' declares '{edge.semantic_type}' but connects "
                        f"'{source_port.semantic_type}' to '{target_port.semantic_type}'."
                    ),
                    edge_id=edge.id,
                )
            )

    total_required_inputs = 0
    matched_required_inputs = 0
    for node in node_by_id.values():
        for port in node.input_ports:
            port_writers = writers[(node.id, port.key)]
            if port.required:
                total_required_inputs += 1
                if len(port_writers) == 1:
                    matched_required_inputs += 1
                elif not port_writers:
                    issues.append(
                        GraphIssue(
                            code=GraphIssueCode.REQUIRED_INPUT_MISSING,
                            message=f"Node '{node.id}' is missing required input '{port.key}'.",
                            node_id=node.id,
                        )
                    )
            if len(port_writers) > 1:
                issues.append(
                    GraphIssue(
                        code=GraphIssueCode.INPUT_MULTIPLE_WRITERS,
                        message=f"Node '{node.id}' input '{port.key}' has multiple writers.",
                        node_id=node.id,
                    )
                )

        has_incoming = bool(incoming_edges[node.id])
        has_outgoing = bool(outgoing_edges[node.id])
        if len(node_by_id) > 1 and not has_incoming and not has_outgoing:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.ORPHAN_NODE,
                    message=f"Node '{node.id}' is isolated from the graph.",
                    node_id=node.id,
                )
            )
        if node.phase is WorkflowPhase.CLEANUP and not node.terminal:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.INVALID_TERMINAL,
                    message=f"Cleanup node '{node.id}' must be terminal.",
                    node_id=node.id,
                )
            )
        if node.terminal and has_outgoing:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.INVALID_TERMINAL,
                    message=f"Terminal node '{node.id}' must not have successors.",
                    node_id=node.id,
                )
            )
        if not node.terminal and not has_outgoing:
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.MISSING_SUCCESSOR,
                    message=f"Non-terminal node '{node.id}' has no successor.",
                    node_id=node.id,
                )
            )

    execution_levels = _topological_levels(adjacency, indegree)
    if sum(map(len, execution_levels)) != len(node_by_id):
        issues.append(
            GraphIssue(
                code=GraphIssueCode.GRAPH_CYCLE,
                message="Workflow Graph contains a cycle.",
            )
        )

    hard_oracles = {
        node.id for node in node_by_id.values() if node.oracle_strength is OracleStrength.HARD
    }
    for node in node_by_id.values():
        if node.kind.casefold() != "agent":
            continue
        reachable = _reachable_nodes(node.id, adjacency)
        if not reachable.intersection(hard_oracles):
            issues.append(
                GraphIssue(
                    code=GraphIssueCode.ASSERTION_COVERAGE_MISSING,
                    message=f"Agent node '{node.id}' does not reach a HARD Oracle.",
                    node_id=node.id,
                )
            )

    unique_issues = _deduplicate_issues(issues)
    return GraphValidationResult(
        valid=not unique_issues,
        issues=tuple(unique_issues),
        execution_levels=execution_levels,
        matched_required_inputs=matched_required_inputs,
        total_required_inputs=total_required_inputs,
    )


def _validate_port_keys(node: WorkflowNode, issues: list[GraphIssue]) -> None:
    for ports in (node.input_ports, node.output_ports):
        seen: set[str] = set()
        for port in ports:
            if port.key in seen:
                issues.append(
                    GraphIssue(
                        code=GraphIssueCode.DUPLICATE_PORT,
                        message=f"Node '{node.id}' declares port '{port.key}' more than once.",
                        node_id=node.id,
                    )
                )
            seen.add(port.key)


def _find_port(ports: tuple[PortSpec, ...], key: str) -> PortSpec | None:
    return next((port for port in ports if port.key == key), None)


def _topological_levels(
    adjacency: dict[str, set[str]], indegree: dict[str, int]
) -> tuple[tuple[str, ...], ...]:
    remaining_indegree = indegree.copy()
    ready = sorted(node_id for node_id, degree in remaining_indegree.items() if degree == 0)
    levels: list[tuple[str, ...]] = []

    while ready:
        current_level = tuple(ready)
        levels.append(current_level)
        next_ready: list[str] = []
        for node_id in current_level:
            for target_id in sorted(adjacency[node_id]):
                remaining_indegree[target_id] -= 1
                if remaining_indegree[target_id] == 0:
                    next_ready.append(target_id)
        ready = sorted(next_ready)

    return tuple(levels)


def _reachable_nodes(source_id: str, adjacency: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    pending = list(adjacency[source_id])
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(adjacency[node_id] - visited)
    return visited


def _deduplicate_issues(issues: list[GraphIssue]) -> list[GraphIssue]:
    unique: list[GraphIssue] = []
    seen: set[tuple[GraphIssueCode, str, str | None, str | None]] = set()
    for issue in issues:
        key = (issue.code, issue.message, issue.node_id, issue.edge_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique
