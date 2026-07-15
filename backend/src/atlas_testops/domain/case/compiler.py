"""Deterministic compiler from WorkflowDraft semantics to Test IR and PlanTemplate."""

from __future__ import annotations

from re import sub
from uuid import UUID

from pydantic import JsonValue

from atlas_testops.domain.case.models import (
    AssertionSpec,
    CaseCompilationResult,
    CaseCompileIssue,
    CaseCompileIssueCode,
    PlanNodeRef,
    PlanTemplate,
    TestIntent,
    TestIR,
    canonical_digest,
)
from atlas_testops.domain.case.patches import canonical_workflow_graph
from atlas_testops.domain.workflow import (
    OracleStrength,
    WorkflowGraph,
    validate_workflow_graph,
)

_FORBIDDEN_PARAM_KEYS = frozenset(
    {
        "authorization",
        "callable",
        "cookie",
        "cookies",
        "css",
        "endpoint",
        "eval",
        "header",
        "headers",
        "href",
        "http",
        "javascript",
        "js",
        "module",
        "origin",
        "password",
        "script",
        "selector",
        "shell",
        "sql",
        "storage_state",
        "token",
        "tokens",
        "uri",
        "url",
        "xpath",
    }
)
_BROWSER_NODE_KINDS = frozenset({"agent", "browser"})


def compile_case(
    *,
    test_case_id: UUID,
    semantic_revision: int,
    intent_version_ref: str,
    intent: TestIntent,
    graph: WorkflowGraph,
) -> CaseCompilationResult:
    """Compile a validated graph without external I/O or floating asset lookup."""

    normalized_graph = canonical_workflow_graph(graph)
    validation = validate_workflow_graph(normalized_graph)
    issues: list[CaseCompileIssue] = []
    if not validation.valid:
        issues.extend(
            CaseCompileIssue(
                code=CaseCompileIssueCode.GRAPH_INVALID,
                message=item.message,
                node_id=item.node_id,
            )
            for item in validation.issues
        )
    if not intent.requirement_refs:
        issues.append(
            CaseCompileIssue(
                code=CaseCompileIssueCode.REQUIREMENT_SOURCE_MISSING,
                message="At least one requirement source anchor is required.",
            )
        )
    if not intent.actors:
        issues.append(
            CaseCompileIssue(
                code=CaseCompileIssueCode.ACTOR_BINDING_MISSING,
                message="One exact business-role binding is required.",
            )
        )
    if intent.fixture is None:
        issues.append(
            CaseCompileIssue(
                code=CaseCompileIssueCode.FIXTURE_BINDING_MISSING,
                message="One exact published fixture blueprint is required.",
            )
        )
    else:
        fixture_nodes = tuple(
            node
            for node in normalized_graph.nodes
            if node.kind.casefold() == "fixture"
        )
        published_exports = {
            port.key: port.semantic_type
            for node in fixture_nodes
            for port in node.output_ports
        }
        if (
            not fixture_nodes
            or any(
                node.version_ref != intent.fixture.blueprint_version_ref
                for node in fixture_nodes
            )
            or any(
                published_exports.get(key) != semantic_type
                for key, semantic_type in intent.fixture.required_exports.items()
            )
        ):
            issues.append(
                CaseCompileIssue(
                    code=CaseCompileIssueCode.FIXTURE_GRAPH_MISMATCH,
                    message=(
                        "Workflow fixture nodes must match the exact blueprint version "
                        "and required exports."
                    ),
                )
            )
    if (
        any(node.kind.casefold() in _BROWSER_NODE_KINDS for node in normalized_graph.nodes)
        and not intent.surfaces
    ):
        issues.append(
            CaseCompileIssue(
                code=CaseCompileIssueCode.SURFACE_BINDING_MISSING,
                message="Browser and Agent nodes require an exact Surface contract.",
            )
        )
    if intent.outcome_policy.require_hard_oracle and not any(
        node.oracle_strength is OracleStrength.HARD for node in normalized_graph.nodes
    ):
        issues.append(
            CaseCompileIssue(
                code=CaseCompileIssueCode.HARD_ORACLE_MISSING,
                message="The outcome policy requires at least one HARD Oracle.",
            )
        )

    for node in normalized_graph.nodes:
        if _contains_forbidden_capability(node.params):
            issues.append(
                CaseCompileIssue(
                    code=CaseCompileIssueCode.FORBIDDEN_CAPABILITY,
                    message="Node parameters contain a forbidden dynamic capability.",
                    node_id=node.id,
                )
            )

    if issues or intent.fixture is None:
        return CaseCompilationResult(valid=False, issues=tuple(issues))

    assertions = tuple(
        AssertionSpec(
            assertion_id=f"assertion:{node.id}",
            node_id=node.id,
            evaluator_version_ref=node.version_ref,
            strength=node.oracle_strength,
        )
        for node in normalized_graph.nodes
        if node.oracle_strength is not None
    )
    required_features = tuple(
        sorted(
            {
                *intent.required_features,
                *(f"node:{node.kind.casefold()}" for node in normalized_graph.nodes),
            }
        )
    )
    test_ir_body: dict[str, JsonValue] = {
        "schemaVersion": "atlas.test-ir/0.2",
        "testCaseId": str(test_case_id),
        "semanticRevision": semantic_revision,
        "intentVersionRef": intent_version_ref,
        "requirementRefs": [
            item.model_dump(mode="json", by_alias=True) for item in intent.requirement_refs
        ],
        "actors": [item.model_dump(mode="json", by_alias=True) for item in intent.actors],
        "fixture": intent.fixture.model_dump(mode="json", by_alias=True),
        "workflow": normalized_graph.model_dump(mode="json", by_alias=True),
        "surfaces": [item.model_dump(mode="json", by_alias=True) for item in intent.surfaces],
        "variables": {
            key: value.model_dump(mode="json", by_alias=True)
            for key, value in sorted(intent.variables.items())
        },
        "assertions": [item.model_dump(mode="json", by_alias=True) for item in assertions],
        "evidencePolicy": intent.evidence_policy.model_dump(mode="json", by_alias=True),
        "recoveryPolicy": intent.recovery_policy.model_dump(mode="json", by_alias=True),
        "outcomePolicy": intent.outcome_policy.model_dump(mode="json", by_alias=True),
        "requiredFeatures": list(required_features),
        "executionLevels": [list(level) for level in validation.execution_levels],
    }
    test_ir = TestIR(
        test_case_id=test_case_id,
        semantic_revision=semantic_revision,
        intent_version_ref=intent_version_ref,
        requirement_refs=intent.requirement_refs,
        actors=intent.actors,
        fixture=intent.fixture,
        workflow=normalized_graph,
        surfaces=intent.surfaces,
        variables=intent.variables,
        assertions=assertions,
        evidence_policy=intent.evidence_policy,
        recovery_policy=intent.recovery_policy,
        outcome_policy=intent.outcome_policy,
        required_features=required_features,
        execution_levels=validation.execution_levels,
        content_digest=canonical_digest(test_ir_body),
    )
    level_by_node = {
        node_id: level
        for level, node_ids in enumerate(validation.execution_levels)
        for node_id in node_ids
    }
    plan_nodes = tuple(
        PlanNodeRef(
            node_id=node.id,
            kind=node.kind,
            version_ref=node.version_ref,
            execution_level=level_by_node[node.id],
        )
        for node in normalized_graph.nodes
    )
    graph_digest = canonical_digest(
        normalized_graph.model_dump(mode="json", by_alias=True)
    )
    plan_body: dict[str, JsonValue] = {
        "schemaVersion": "atlas.plan-template/0.1",
        "testCaseId": str(test_case_id),
        "semanticRevision": semantic_revision,
        "testIrDigest": test_ir.content_digest,
        "graphDigest": graph_digest,
        "nodes": [item.model_dump(mode="json", by_alias=True) for item in plan_nodes],
        "executionLevels": [list(level) for level in validation.execution_levels],
        "requiredFeatures": list(required_features),
    }
    plan = PlanTemplate(
        test_case_id=test_case_id,
        semantic_revision=semantic_revision,
        test_ir_digest=test_ir.content_digest,
        graph_digest=graph_digest,
        nodes=plan_nodes,
        execution_levels=validation.execution_levels,
        required_features=required_features,
        plan_digest=canonical_digest(plan_body),
    )
    return CaseCompilationResult(
        valid=True,
        issues=(),
        test_ir=test_ir,
        plan_template=plan,
        compiled_digest=canonical_digest(
            {
                "testIrDigest": test_ir.content_digest,
                "planDigest": plan.plan_digest,
            }
        ),
    )


def _contains_forbidden_capability(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = sub(r"(?<!^)(?=[A-Z])", "_", key).casefold().replace("-", "_")
            fragments = set(normalized.split("_"))
            if fragments.intersection(_FORBIDDEN_PARAM_KEYS):
                return True
            if _contains_forbidden_capability(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_capability(item) for item in value)
    if isinstance(value, str):
        lowered = value.strip().casefold()
        return (
            "://" in lowered
            or lowered.startswith(("javascript:", "data:", "file:"))
            or "document.cookie" in lowered
        )
    return False
