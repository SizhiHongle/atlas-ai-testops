"""Deterministic static compiler for fixture blueprints."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from atlas_testops.domain.fixture.models import (
    AssetVersionStatus,
    AtomEffect,
    BlueprintCompilationResult,
    BlueprintExport,
    BlueprintNode,
    CompiledFixturePlan,
    CompiledNode,
    CompileIssue,
    CompileIssueCode,
    DataAtomVersion,
    DataBlueprintContract,
    DataClassification,
    LiteralBinding,
    NodeOutputBinding,
    PortDirection,
    canonical_digest,
)

_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.SENSITIVE: 3,
}


def compile_blueprint(
    contract: DataBlueprintContract,
    *,
    blueprint_version_id: UUID,
    blueprint_digest: str,
    atom_versions: Mapping[UUID, DataAtomVersion],
) -> BlueprintCompilationResult:
    """Compile a static DAG without executing connector or user-provided code."""

    issues: list[CompileIssue] = []
    nodes_by_id = {node.id: node for node in contract.nodes}
    dependencies: dict[str, set[str]] = {node.id: set() for node in contract.nodes}

    for node in sorted(contract.nodes, key=lambda item: item.id):
        atom_version = atom_versions.get(node.atom_version_id)
        if atom_version is None:
            issues.append(
                _issue(
                    CompileIssueCode.ATOM_VERSION_NOT_FOUND,
                    "The referenced atom version does not exist in this project.",
                    node_id=node.id,
                )
            )
            continue
        if atom_version.status not in {
            AssetVersionStatus.VALIDATED,
            AssetVersionStatus.PUBLISHED,
        }:
            issues.append(
                _issue(
                    CompileIssueCode.ATOM_VERSION_NOT_VALIDATED,
                    "The referenced atom version must be VALIDATED or PUBLISHED.",
                    node_id=node.id,
                )
            )

        input_ports = {
            port.key: port
            for port in atom_version.contract.ports
            if port.direction is PortDirection.INPUT
        }
        bound_targets: set[str] = set()
        for binding in node.bindings:
            target = input_ports.get(binding.target_port)
            if target is None:
                issues.append(
                    _issue(
                        CompileIssueCode.TARGET_PORT_NOT_FOUND,
                        "The binding target is not an input port on the atom version.",
                        node_id=node.id,
                        port_key=binding.target_port,
                    )
                )
                continue
            bound_targets.add(binding.target_port)
            if isinstance(binding, LiteralBinding):
                errors = tuple(Draft202012Validator(target.json_schema).iter_errors(binding.value))
                if errors:
                    issues.append(
                        _issue(
                            CompileIssueCode.LITERAL_SCHEMA_MISMATCH,
                            "The literal value does not satisfy the target port JSON Schema.",
                            node_id=node.id,
                            port_key=binding.target_port,
                        )
                    )
            elif isinstance(binding, NodeOutputBinding):
                _validate_node_output_binding(
                    binding=binding,
                    target_node_id=node.id,
                    target_semantic_type=target.semantic_type,
                    target_classification=target.classification,
                    nodes_by_id=nodes_by_id,
                    atom_versions=atom_versions,
                    dependencies=dependencies,
                    issues=issues,
                )

        for port in sorted(input_ports.values(), key=lambda item: item.key):
            if port.required and port.key not in bound_targets:
                issues.append(
                    _issue(
                        CompileIssueCode.REQUIRED_INPUT_MISSING,
                        "A required atom input has no blueprint binding.",
                        node_id=node.id,
                        port_key=port.key,
                    )
                )

        if (
            atom_version.contract.effect is AtomEffect.CREATE
            and atom_version.contract.cleanup_contract is None
        ):
            issues.append(
                _issue(
                    CompileIssueCode.CLEANUP_CONTRACT_MISSING,
                    "A CREATE atom must declare a reviewed cleanup contract.",
                    node_id=node.id,
                )
            )

    _validate_exports(
        contract.exports,
        nodes_by_id=nodes_by_id,
        atom_versions=atom_versions,
        issues=issues,
    )
    execution_levels = _execution_levels(dependencies)
    if execution_levels is None:
        issues.append(
            _issue(
                CompileIssueCode.GRAPH_CYCLE_DETECTED,
                "The blueprint node graph contains a dependency cycle.",
            )
        )

    if issues or execution_levels is None:
        return BlueprintCompilationResult(valid=False, issues=tuple(issues))

    level_by_node = {
        node_id: level for level, node_ids in enumerate(execution_levels) for node_id in node_ids
    }
    compiled_nodes = tuple(
        CompiledNode(
            node_id=node.id,
            atom_version_id=node.atom_version_id,
            atom_digest=atom_versions[node.atom_version_id].content_digest,
            actor_slot=node.actor_slot,
            bindings=node.bindings,
            execution_level=level_by_node[node.id],
        )
        for node in sorted(contract.nodes, key=lambda item: item.id)
    )
    cleanup_order = tuple(
        node_id
        for node_id in reversed(tuple(item for level in execution_levels for item in level))
        if atom_versions[nodes_by_id[node_id].atom_version_id].contract.effect is AtomEffect.CREATE
    )
    plan_body: dict[str, JsonValue] = {
        "schemaVersion": "atlas.compiled-fixture-plan/0.1",
        "blueprintVersionId": str(blueprint_version_id),
        "blueprintDigest": blueprint_digest,
        "nodes": [item.model_dump(mode="json", by_alias=True) for item in compiled_nodes],
        "executionLevels": [list(item) for item in execution_levels],
        "cleanupOrder": list(cleanup_order),
        "exports": [item.model_dump(mode="json", by_alias=True) for item in contract.exports],
    }
    plan = CompiledFixturePlan(
        blueprint_version_id=blueprint_version_id,
        blueprint_digest=blueprint_digest,
        nodes=compiled_nodes,
        execution_levels=execution_levels,
        cleanup_order=cleanup_order,
        exports=contract.exports,
        plan_digest=canonical_digest(plan_body),
    )
    return BlueprintCompilationResult(valid=True, issues=(), plan=plan)


def _validate_node_output_binding(
    *,
    binding: NodeOutputBinding,
    target_node_id: str,
    target_semantic_type: str,
    target_classification: DataClassification,
    nodes_by_id: Mapping[str, BlueprintNode],
    atom_versions: Mapping[UUID, DataAtomVersion],
    dependencies: dict[str, set[str]],
    issues: list[CompileIssue],
) -> None:
    source_node = nodes_by_id.get(binding.source_node_id)
    if source_node is None:
        issues.append(
            _issue(
                CompileIssueCode.SOURCE_NODE_NOT_FOUND,
                "The binding source node does not exist in the blueprint.",
                node_id=target_node_id,
                port_key=binding.target_port,
            )
        )
        return

    source_atom_id = source_node.atom_version_id
    source_atom = atom_versions.get(source_atom_id)
    if source_atom is None:
        return
    source_outputs = {
        port.key: port
        for port in source_atom.contract.ports
        if port.direction is PortDirection.OUTPUT
    }
    source_port = source_outputs.get(binding.source_port)
    if source_port is None:
        issues.append(
            _issue(
                CompileIssueCode.SOURCE_PORT_NOT_FOUND,
                "The binding source is not an output port on its atom version.",
                node_id=target_node_id,
                port_key=binding.target_port,
            )
        )
        return

    dependencies[target_node_id].add(binding.source_node_id)
    if source_port.semantic_type != target_semantic_type:
        issues.append(
            _issue(
                CompileIssueCode.PORT_TYPE_MISMATCH,
                "Source and target ports have different semantic types.",
                node_id=target_node_id,
                port_key=binding.target_port,
            )
        )
    if (
        _CLASSIFICATION_RANK[source_port.classification]
        > _CLASSIFICATION_RANK[target_classification]
    ):
        issues.append(
            _issue(
                CompileIssueCode.FORBIDDEN_SECRET_FLOW,
                "A binding cannot lower the source data classification.",
                node_id=target_node_id,
                port_key=binding.target_port,
            )
        )


def _validate_exports(
    exports: tuple[BlueprintExport, ...],
    *,
    nodes_by_id: Mapping[str, BlueprintNode],
    atom_versions: Mapping[UUID, DataAtomVersion],
    issues: list[CompileIssue],
) -> None:
    for export in sorted(exports, key=lambda item: item.name):
        source_node = nodes_by_id.get(export.source_node_id)
        if source_node is None:
            issues.append(
                _issue(
                    CompileIssueCode.EXPORT_SOURCE_NOT_FOUND,
                    "The export source node does not exist in the blueprint.",
                    export_name=export.name,
                )
            )
            continue
        atom_version = atom_versions.get(source_node.atom_version_id)
        if atom_version is None:
            continue
        output_ports = {
            port.key: port
            for port in atom_version.contract.ports
            if port.direction is PortDirection.OUTPUT
        }
        source_port = output_ports.get(export.source_port)
        if source_port is None:
            issues.append(
                _issue(
                    CompileIssueCode.EXPORT_SOURCE_NOT_FOUND,
                    "The export source is not an output port on its atom version.",
                    export_name=export.name,
                )
            )
            continue
        if (
            _CLASSIFICATION_RANK[export.classification]
            < _CLASSIFICATION_RANK[source_port.classification]
        ):
            issues.append(
                _issue(
                    CompileIssueCode.EXPORT_CLASSIFICATION_MISMATCH,
                    "An export cannot lower the source data classification.",
                    export_name=export.name,
                )
            )


def _execution_levels(
    dependencies: Mapping[str, set[str]],
) -> tuple[tuple[str, ...], ...] | None:
    remaining = {node_id: set(items) for node_id, items in dependencies.items()}
    levels: list[tuple[str, ...]] = []
    while remaining:
        ready = tuple(sorted(node_id for node_id, items in remaining.items() if not items))
        if not ready:
            return None
        levels.append(ready)
        for node_id in ready:
            del remaining[node_id]
        for items in remaining.values():
            items.difference_update(ready)
    return tuple(levels)


def _issue(
    code: CompileIssueCode,
    message: str,
    *,
    node_id: str | None = None,
    port_key: str | None = None,
    export_name: str | None = None,
) -> CompileIssue:
    return CompileIssue(
        code=code,
        message=message,
        node_id=node_id,
        port_key=port_key,
        export_name=export_name,
    )
