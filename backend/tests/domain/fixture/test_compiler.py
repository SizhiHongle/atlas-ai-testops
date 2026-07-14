"""Fixture contract and deterministic compiler tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.fixture import (
    AssetVersionStatus,
    AtomEffect,
    AtomPort,
    BlueprintExport,
    BlueprintNode,
    CleanupContract,
    CompileIssueCode,
    ConnectorOperationRef,
    DataAtomContract,
    DataAtomVersion,
    DataBlueprintContract,
    DataClassification,
    ExecutionContextBinding,
    IdempotencyMode,
    IdempotencyPolicy,
    LiteralBinding,
    NodeOutputBinding,
    PortDirection,
    Postcondition,
    PostconditionKind,
    ReconcileContract,
    ResourcePolicy,
    ValidationState,
    canonical_digest,
    compile_blueprint,
)
from atlas_testops.domain.platform import EnvironmentKind

NOW = datetime(2026, 7, 14, tzinfo=UTC)
TENANT_ID = UUID(int=1)
PROJECT_ID = UUID(int=2)
BLUEPRINT_VERSION_ID = UUID(int=30)


def operation(key: str) -> ConnectorOperationRef:
    return ConnectorOperationRef(
        operation_key=key,
        operation_version="1.0.0",
        required_capabilities=(key,),
    )


def create_atom_version(
    atom_version_id: UUID,
    *,
    operation_key: str,
    output_key: str,
    output_type: str,
    parent_type: str | None = None,
    output_classification: DataClassification = DataClassification.INTERNAL,
) -> DataAtomVersion:
    ports = [
        AtomPort(
            key="executionId",
            direction=PortDirection.INPUT,
            semantic_type="atlas.execution-id",
            json_schema={"type": "string", "minLength": 1},
        ),
        AtomPort(
            key="parentRef",
            direction=PortDirection.INPUT,
            semantic_type=parent_type or output_type,
            json_schema={"type": "string", "minLength": 1},
            required=False,
        ),
        AtomPort(
            key=output_key,
            direction=PortDirection.OUTPUT,
            semantic_type=output_type,
            json_schema={"type": "string", "minLength": 1},
            classification=output_classification,
        ),
    ]
    contract = DataAtomContract(
        effect=AtomEffect.CREATE,
        ports=tuple(ports),
        operation=operation(operation_key),
        idempotency_policy=IdempotencyPolicy(
            mode=IdempotencyMode.RECONCILE,
            marker_input="executionId",
        ),
        postconditions=(
            Postcondition(kind=PostconditionKind.OUTPUT_SCHEMA, output_port=output_key),
        ),
        resource_policy=ResourcePolicy(
            resource_type=output_type,
            resource_ref_output=output_key,
        ),
        cleanup_contract=CleanupContract(
            operation=operation(f"{operation_key}.delete"),
            resource_ref_input=output_key,
        ),
        reconcile_contract=ReconcileContract(
            operation=operation(f"{operation_key}.lookup"),
            marker_input="executionId",
            resource_ref_output=output_key,
        ),
    )
    return DataAtomVersion(
        id=atom_version_id,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        atom_id=UUID(int=atom_version_id.int + 100),
        version="1.0.0",
        status=AssetVersionStatus.VALIDATED,
        contract=contract,
        content_digest=canonical_digest(contract),
        static_validation_state=ValidationState.PASSED,
        runtime_validation_state=ValidationState.PENDING,
        cleanup_validation_state=ValidationState.PENDING,
        validated_at=NOW,
        published_at=None,
        published_by=None,
        revision=2,
        created_at=NOW,
        updated_at=NOW,
    )


def valid_blueprint() -> tuple[DataBlueprintContract, dict[UUID, DataAtomVersion]]:
    customer_atom = create_atom_version(
        UUID(int=10),
        operation_key="customer.create",
        output_key="customerRef",
        output_type="resource.customer-ref",
    )
    order_atom = create_atom_version(
        UUID(int=20),
        operation_key="order.create",
        output_key="orderRef",
        output_type="resource.order-ref",
        parent_type="resource.customer-ref",
    )
    contract = DataBlueprintContract(
        run_input_schema={"type": "object", "additionalProperties": False},
        nodes=(
            BlueprintNode(
                id="createCustomer",
                atom_version_id=customer_atom.id,
                actor_slot="primaryUser",
                bindings=(ExecutionContextBinding(target_port="executionId"),),
            ),
            BlueprintNode(
                id="createOrder",
                atom_version_id=order_atom.id,
                actor_slot="primaryUser",
                bindings=(
                    ExecutionContextBinding(target_port="executionId"),
                    NodeOutputBinding(
                        target_port="parentRef",
                        source_node_id="createCustomer",
                        source_port="customerRef",
                    ),
                ),
            ),
        ),
        exports=(
            BlueprintExport(
                name="orderRef",
                source_node_id="createOrder",
                source_port="orderRef",
            ),
        ),
    )
    return contract, {customer_atom.id: customer_atom, order_atom.id: order_atom}


def test_compiles_deterministic_typed_dag_and_reverse_cleanup_order() -> None:
    contract, atoms = valid_blueprint()
    blueprint_digest = canonical_digest(contract)

    first = compile_blueprint(
        contract,
        blueprint_version_id=BLUEPRINT_VERSION_ID,
        blueprint_digest=blueprint_digest,
        atom_versions=atoms,
    )
    second = compile_blueprint(
        contract,
        blueprint_version_id=BLUEPRINT_VERSION_ID,
        blueprint_digest=blueprint_digest,
        atom_versions=dict(reversed(tuple(atoms.items()))),
    )

    assert first.valid is True
    assert first.plan is not None
    assert first.plan.execution_levels == (("createCustomer",), ("createOrder",))
    assert first.plan.cleanup_order == ("createOrder", "createCustomer")
    assert first.plan.plan_digest == second.plan.plan_digest if second.plan else False


def test_rejects_cycles_and_invalid_literals() -> None:
    contract, atoms = valid_blueprint()
    first, second = contract.nodes
    cyclic = contract.model_copy(
        update={
            "nodes": (
                first.model_copy(
                    update={
                        "bindings": (
                            *first.bindings,
                            NodeOutputBinding(
                                target_port="parentRef",
                                source_node_id="createOrder",
                                source_port="orderRef",
                            ),
                        )
                    }
                ),
                second,
            )
        }
    )
    invalid_literal = contract.model_copy(
        update={
            "nodes": (
                first.model_copy(
                    update={"bindings": (LiteralBinding(target_port="executionId", value=42),)}
                ),
                second,
            )
        }
    )

    cycle_result = compile_blueprint(
        cyclic,
        blueprint_version_id=BLUEPRINT_VERSION_ID,
        blueprint_digest=canonical_digest(cyclic),
        atom_versions=atoms,
    )
    literal_result = compile_blueprint(
        invalid_literal,
        blueprint_version_id=BLUEPRINT_VERSION_ID,
        blueprint_digest=canonical_digest(invalid_literal),
        atom_versions=atoms,
    )

    assert CompileIssueCode.GRAPH_CYCLE_DETECTED in {issue.code for issue in cycle_result.issues}
    assert CompileIssueCode.LITERAL_SCHEMA_MISMATCH in {
        issue.code for issue in literal_result.issues
    }


def test_rejects_classification_downgrade_on_export() -> None:
    contract, atoms = valid_blueprint()
    order_id = contract.nodes[1].atom_version_id
    sensitive_order = create_atom_version(
        order_id,
        operation_key="order.create",
        output_key="orderRef",
        output_type="resource.order-ref",
        parent_type="resource.customer-ref",
        output_classification=DataClassification.SENSITIVE,
    )

    result = compile_blueprint(
        contract,
        blueprint_version_id=BLUEPRINT_VERSION_ID,
        blueprint_digest=canonical_digest(contract),
        atom_versions={**atoms, order_id: sensitive_order},
    )

    assert CompileIssueCode.EXPORT_CLASSIFICATION_MISMATCH in {
        issue.code for issue in result.issues
    }


def test_contract_rejects_production_and_credential_ports() -> None:
    contract = next(iter(valid_blueprint()[1].values())).contract

    with pytest.raises(ValidationError):
        contract.model_copy(
            update={"allowed_environment_kinds": (EnvironmentKind.PRODUCTION,)}
        ).model_validate(
            {
                **contract.model_dump(),
                "allowed_environment_kinds": (EnvironmentKind.PRODUCTION,),
            }
        )

    with pytest.raises(ValidationError):
        AtomPort(
            key="credential",
            direction=PortDirection.INPUT,
            semantic_type="auth.password-secret",
            json_schema={"type": "string"},
        )
