"""Fixture runtime contract, validation, registry, and mock provider tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from pydantic import JsonValue, ValidationError

from atlas_testops.application.access import ActorContext
from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
from atlas_testops.application.fixture_runs import (
    FixtureRunService,
    FixtureWorkerService,
    _conflict,
    _dependency_unavailable,
    _forbidden,
    _invalid_request,
    _NodeValidationFailure,
    _not_found,
    _safe_failure_code,
)
from atlas_testops.application.ports.fixture_operations import (
    FixtureOperationContext,
    FixtureOperationError,
    FixtureOperationInvocation,
    FixtureOperationSpec,
)
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.fixture import (
    AssetVersionStatus,
    AtomEffect,
    AtomPort,
    BlueprintExport,
    CleanupContract,
    CompiledFixturePlan,
    CompiledNode,
    ConnectorOperationRef,
    DataAtomContract,
    DataAtomVersion,
    DataClassification,
    ExecutionContextBinding,
    FixtureActorBindingRecord,
    FixtureActorLeaseBinding,
    FixtureFailureCategory,
    FixtureRun,
    FixtureRunKind,
    FixtureRunRecord,
    IdempotencyMode,
    IdempotencyPolicy,
    LiteralBinding,
    NodeOutputBinding,
    PortDirection,
    Postcondition,
    PostconditionKind,
    ReconcileContract,
    ResourceOwnership,
    ResourcePolicy,
    RunInputBinding,
    StartFixtureRun,
    ValidationState,
    build_fixture_manifest,
    canonical_json_digest,
    ensure_future_deadline,
    pointer_value,
    stable_node_idempotency_key,
    validate_operation_inputs,
    validate_operation_outputs,
    validate_run_inputs,
)
from atlas_testops.domain.platform import EnvironmentKind
from atlas_testops.infrastructure.adapters.fixture_registry import (
    FixtureOperationCapabilityError,
    FixtureOperationNotRegisteredError,
    FixtureOperationRegistry,
)
from atlas_testops.infrastructure.adapters.mock_fixture import MockFixtureOperationProvider
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.fixture_runs import FixtureLeaseSnapshot

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _operation(key: str = "customer.create") -> ConnectorOperationRef:
    return ConnectorOperationRef(
        operation_key=key,
        operation_version="1.0.0",
        required_capabilities=(key,),
    )


def _contract() -> DataAtomContract:
    return DataAtomContract(
        effect=AtomEffect.CREATE,
        ports=(
            AtomPort(
                key="executionId",
                direction=PortDirection.INPUT,
                semantic_type="atlas.execution-id",
                json_schema={"type": "string", "minLength": 3},
            ),
            AtomPort(
                key="customerRef",
                direction=PortDirection.OUTPUT,
                semantic_type="resource.customer-ref",
                json_schema={"type": "string", "minLength": 3},
            ),
        ),
        operation=_operation(),
        idempotency_policy=IdempotencyPolicy(
            mode=IdempotencyMode.RECONCILE,
            marker_input="executionId",
        ),
        postconditions=(
            Postcondition(
                kind=PostconditionKind.OUTPUT_SCHEMA,
                output_port="customerRef",
            ),
        ),
        resource_policy=ResourcePolicy(
            resource_type="resource.customer-ref",
            resource_ref_output="customerRef",
        ),
        cleanup_contract=CleanupContract(
            operation=_operation("customer.delete"),
            resource_ref_input="customerRef",
        ),
        reconcile_contract=ReconcileContract(
            operation=_operation("customer.lookup"),
            marker_input="executionId",
            resource_ref_output="customerRef",
        ),
    )


def _plan(blueprint_version_id: UUID, atom_version_id: UUID) -> CompiledFixturePlan:
    return CompiledFixturePlan(
        blueprint_version_id=blueprint_version_id,
        blueprint_digest=DIGEST_A,
        nodes=(
            CompiledNode(
                node_id="createCustomer",
                atom_version_id=atom_version_id,
                atom_digest=DIGEST_B,
                actor_slot="primaryUser",
                bindings=(
                    ExecutionContextBinding(
                        target_port="executionId",
                    ),
                ),
                execution_level=0,
            ),
        ),
        execution_levels=(("createCustomer",),),
        cleanup_order=("createCustomer",),
        exports=(
            BlueprintExport(
                name="customerRef",
                source_node_id="createCustomer",
                source_port="customerRef",
                classification=DataClassification.INTERNAL,
            ),
        ),
        plan_digest=DIGEST_A,
    )


def test_non_created_resource_may_omit_cleanup_contract() -> None:
    payload = _contract().model_dump(mode="python", by_alias=False)
    payload["effect"] = AtomEffect.UPDATE
    payload["cleanup_contract"] = None
    payload["resource_policy"]["ownership"] = ResourceOwnership.ADOPTED
    contract = DataAtomContract.model_validate(payload)
    assert contract.resource_policy is not None
    assert contract.resource_policy.ownership is ResourceOwnership.ADOPTED

    payload["resource_policy"]["ownership"] = ResourceOwnership.CREATED
    with pytest.raises(ValidationError, match="requires cleanupContract"):
        DataAtomContract.model_validate(payload)


def test_runtime_input_output_manifest_pointer_and_digest_guards() -> None:
    contract = _contract()
    validate_operation_inputs(contract, {"executionId": "fix-1"})
    validate_operation_outputs(contract, {"customerRef": "customer-1"})
    invalid_inputs: tuple[dict[str, JsonValue], ...] = (
        {},
        {"executionId": "x"},
        {"executionId": "fix-1", "extra": 1},
    )
    for inputs in invalid_inputs:
        with pytest.raises(ValueError):
            validate_operation_inputs(contract, inputs)
    invalid_outputs: tuple[dict[str, JsonValue], ...] = (
        {},
        {"customerRef": "x"},
        {"customerRef": "ok-1", "extra": 1},
    )
    for outputs in invalid_outputs:
        with pytest.raises(ValueError):
            validate_operation_outputs(contract, outputs)

    validate_run_inputs(
        {
            "type": "object",
            "properties": {"region": {"type": "string"}},
            "required": ["region"],
            "additionalProperties": False,
        },
        {"region": "cn"},
    )
    with pytest.raises(ValueError, match="required property"):
        validate_run_inputs(
            {"type": "object", "required": ["region"]},
            {},
        )

    blueprint_version_id = uuid7()
    atom_version_id = uuid7()
    run_id = uuid7()
    plan = _plan(blueprint_version_id, atom_version_id)
    manifest = build_fixture_manifest(
        fixture_run_id=run_id,
        blueprint_version_id=blueprint_version_id,
        plan=plan,
        node_outputs={"createCustomer": {"customerRef": "customer-1"}},
    )
    assert manifest.exports == {"customerRef": "customer-1"}
    with pytest.raises(ValueError, match="unavailable"):
        build_fixture_manifest(
            fixture_run_id=run_id,
            blueprint_version_id=blueprint_version_id,
            plan=plan,
            node_outputs={},
        )

    document = cast(JsonValue, {"users": [{"name/escaped": "Atlas"}]})
    assert pointer_value(document, "/users/0/name~1escaped") == "Atlas"
    for pointer in ("/users/2", "/users/nope", "/missing", "/users/0/name/x"):
        with pytest.raises(ValueError):
            pointer_value(document, pointer)
    assert canonical_json_digest({"b": 2, "a": 1}) == canonical_json_digest({"a": 1, "b": 2})
    key = stable_node_idempotency_key(
        environment_id=uuid7(),
        blueprint_version_id=blueprint_version_id,
        execution_id="execution-01",
        node_id="createCustomer",
    )
    assert key.startswith("fix_") and len(key) == 68
    now = datetime.now(UTC)
    ensure_future_deadline(now + timedelta(seconds=1), now)
    with pytest.raises(ValueError):
        ensure_future_deadline(now, now)


def test_start_command_rejects_duplicate_slots_and_leases() -> None:
    blueprint_version_id = uuid7()
    environment_id = uuid7()
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    base = {
        "blueprintVersionId": blueprint_version_id,
        "environmentId": environment_id,
        "executionId": "execution-01",
        "executionDeadline": deadline,
    }
    lease_id = uuid7()
    with pytest.raises(ValidationError, match="unique actor slots"):
        StartFixtureRun.model_validate(
            {
                **base,
                "actorBindings": [
                    {"actorSlot": "primaryUser", "accountLeaseId": lease_id, "fencingToken": 1},
                    {"actorSlot": "primaryUser", "accountLeaseId": uuid7(), "fencingToken": 2},
                ],
            }
        )
    with pytest.raises(ValidationError, match="cannot satisfy multiple"):
        StartFixtureRun.model_validate(
            {
                **base,
                "actorBindings": [
                    {"actorSlot": "primaryUser", "accountLeaseId": lease_id, "fencingToken": 1},
                    {"actorSlot": "secondaryUser", "accountLeaseId": lease_id, "fencingToken": 1},
                ],
            }
        )
    command = StartFixtureRun(
        blueprint_version_id=blueprint_version_id,
        environment_id=environment_id,
        execution_id="execution-01",
        execution_deadline=deadline,
        actor_bindings=(
            FixtureActorLeaseBinding(
                actor_slot="secondaryUser",
                account_lease_id=uuid7(),
                fencing_token=2,
            ),
            FixtureActorLeaseBinding(
                actor_slot="primaryUser",
                account_lease_id=uuid7(),
                fencing_token=1,
            ),
        ),
    )
    assert [item.actor_slot for item in command.actor_bindings] == [
        "primaryUser",
        "secondaryUser",
    ]


@pytest.mark.anyio
async def test_registry_and_mock_provider_cover_exact_operations_and_schema_shapes() -> None:
    registry = FixtureOperationRegistry.from_settings(Settings(environment="test"))
    operation = _operation()
    assert registry.supports("generic-password", operation)
    provider = registry.resolve("generic-password", operation)
    context = FixtureOperationContext(
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        fixture_run_id=uuid7(),
        data_node_run_id=uuid7(),
        connector_installation_id=uuid7(),
        account_handle="ah_" + "a" * 32,
        configuration_ref="cfg_abcdefgh",
        idempotency_key="fix_" + "b" * 64,
        request_id="request-01",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    result = await provider.execute(
        context=context,
        invocation=FixtureOperationInvocation(
            operation=operation,
            inputs={},
            expected_outputs={
                "constant": {"const": "fixed"},
                "choice": {"enum": ["first", "second"]},
                "enabled": {"type": "boolean"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "items": {"type": "array"},
                "metadata": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "emptyObject": {"type": "object"},
                "nullable": {"type": ["null", "string"]},
                "nothing": {"type": "null"},
                "fallback": {},
            },
        ),
    )
    assert result.outputs["constant"] == "fixed"
    assert result.outputs["choice"] == "first"
    assert result.outputs["enabled"] is True
    assert isinstance(result.outputs["count"], int)
    assert isinstance(result.outputs["ratio"], float)
    assert result.outputs["items"] == []
    assert result.outputs["metadata"]
    assert result.outputs["emptyObject"] == {}
    assert str(result.outputs["nullable"]).startswith("mock-")
    assert result.outputs["nothing"] is None
    assert str(result.outputs["fallback"]).startswith("mock-")
    assert result.provider_request_id is not None

    with pytest.raises(FixtureOperationNotRegisteredError):
        registry.resolve("missing-adapter", operation)
    with pytest.raises(FixtureOperationCapabilityError):
        registry.resolve(
            "generic-password",
            operation.model_copy(update={"required_capabilities": ("missing.capability",)}),
        )
    production = FixtureOperationRegistry.from_settings(Settings(environment="production"))
    assert not production.supports("generic-password", operation)


def test_registry_rejects_blank_empty_duplicate_and_safe_operation_error() -> None:
    class EmptyProvider:
        def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
            return ()

        async def execute(self, **kwargs: object) -> object:
            return object()

    registry = FixtureOperationRegistry()
    with pytest.raises(ValueError, match="blank"):
        registry.register(" ", MockFixtureOperationProvider())
    with pytest.raises(ValueError, match="at least one"):
        registry.register("empty", EmptyProvider())  # type: ignore[arg-type]
    provider = MockFixtureOperationProvider()
    registry.register("generic-password", provider)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("generic-password", provider)

    error = FixtureOperationError(
        category=FixtureFailureCategory.TRANSIENT,
        code="PROVIDER_BUSY",
        safe_detail="Provider is busy.",
        retryable=True,
        retry_after_seconds=1.5,
    )
    assert str(error) == "Provider is busy."
    assert error.retryable
    assert error.retry_after_seconds == 1.5


def test_control_service_preflight_guards_atom_lease_registry_and_visibility() -> None:
    registry = FixtureOperationRegistry.from_settings(Settings(environment="test"))
    service = FixtureRunService(
        cast(Database, object()),
        cast(FixtureRunDispatcher, object()),
        registry,
        cleanup_grace=timedelta(minutes=1),
    )
    now = datetime.now(UTC)
    project_id = uuid7()
    environment_id = uuid7()
    atom_id = uuid7()
    atom = DataAtomVersion.model_construct(
        id=atom_id,
        status=AssetVersionStatus.VALIDATED,
        static_validation_state=ValidationState.PASSED,
        content_digest=DIGEST_B,
        contract=_contract(),
    )
    compiled_node = CompiledNode(
        node_id="createCustomer",
        atom_version_id=atom_id,
        atom_digest=DIGEST_B,
        actor_slot="primaryUser",
        bindings=(),
        execution_level=0,
    )
    service._validate_atom_for_run(
        atom,
        compiled_node=compiled_node,
        environment_kind=EnvironmentKind.TEST,
        run_kind=FixtureRunKind.VALIDATION,
    )
    invalid_atoms = (
        atom.model_copy(update={"status": "DRAFT"}),
        atom.model_copy(update={"content_digest": DIGEST_A}),
    )
    for invalid_atom in invalid_atoms:
        with pytest.raises(ApplicationError) as captured:
            service._validate_atom_for_run(
                invalid_atom,
                compiled_node=compiled_node,
                environment_kind=EnvironmentKind.TEST,
                run_kind=FixtureRunKind.VALIDATION,
            )
        assert captured.value.error_code is ErrorCode.CONFLICT
    with pytest.raises(ApplicationError) as forbidden_atom:
        service._validate_atom_for_run(
            atom,
            compiled_node=compiled_node,
            environment_kind=EnvironmentKind.PRODUCTION,
            run_kind=FixtureRunKind.VALIDATION,
        )
    assert forbidden_atom.value.error_code is ErrorCode.FORBIDDEN

    published_atom = atom.model_copy(update={"status": AssetVersionStatus.PUBLISHED})
    service._validate_atom_for_run(
        published_atom,
        compiled_node=compiled_node,
        environment_kind=EnvironmentKind.TEST,
        run_kind=FixtureRunKind.EXECUTION,
    )
    with pytest.raises(ApplicationError) as unpublished_execution:
        service._validate_atom_for_run(
            atom,
            compiled_node=compiled_node,
            environment_kind=EnvironmentKind.TEST,
            run_kind=FixtureRunKind.EXECUTION,
        )
    assert unpublished_execution.value.error_code is ErrorCode.CONFLICT

    command = StartFixtureRun(
        blueprint_version_id=uuid7(),
        environment_id=environment_id,
        execution_id="execution-01",
        actor_bindings=(
            FixtureActorLeaseBinding(
                actor_slot="primaryUser",
                account_lease_id=uuid7(),
                fencing_token=7,
            ),
        ),
        execution_deadline=now + timedelta(minutes=2),
    )
    lease = FixtureLeaseSnapshot(
        account_lease_id=command.actor_bindings[0].account_lease_id,
        tenant_id=uuid7(),
        project_id=project_id,
        environment_id=environment_id,
        execution_id=command.execution_id,
        worker_id="worker-fixture-01",
        account_handle="ah_" + "a" * 32,
        fencing_token=7,
        lease_status="ACTIVE",
        lease_expires_at=now + timedelta(minutes=3),
        connector_installation_id=uuid7(),
        connector_adapter_key="generic-password",
        connector_configuration_ref="cfg_abcdefgh",
        connector_status="ACTIVE",
        connector_health_state="HEALTHY",
        connector_revision=1,
    )
    service._validate_lease_for_run(
        lease,
        command=command,
        project_id=project_id,
        fencing_token=7,
    )
    invalid_leases = (
        replace(lease, project_id=uuid7()),
        replace(lease, lease_status="RELEASED"),
        replace(lease, lease_expires_at=now + timedelta(seconds=30)),
        replace(lease, lease_expires_at=now + timedelta(minutes=2, seconds=30)),
        replace(lease, connector_status="DISABLED"),
        replace(lease, connector_health_state="DEGRADED"),
    )
    expected_codes = (
        ErrorCode.CONFLICT,
        ErrorCode.CONFLICT,
        ErrorCode.CONFLICT,
        ErrorCode.CONFLICT,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
    )
    for invalid_lease, expected_code in zip(invalid_leases, expected_codes, strict=True):
        with pytest.raises(ApplicationError) as captured:
            service._validate_lease_for_run(
                invalid_lease,
                command=command,
                project_id=project_id,
                fencing_token=7,
            )
        assert captured.value.error_code is expected_code

    service._validate_registered_operations(lease, atom)
    with pytest.raises(ApplicationError) as missing_operation:
        service._validate_registered_operations(
            replace(lease, connector_adapter_key="missing-adapter"),
            atom,
        )
    assert missing_operation.value.error_code is ErrorCode.CONFLICT

    tenant_id = uuid7()
    run = FixtureRun.model_construct(tenant_id=tenant_id, project_id=project_id)
    service._require_run_read(
        ActorContext(
            tenant_id=tenant_id,
            actor_id=None,
            request_id="request-01",
            development_override=True,
        ),
        run,
    )
    with pytest.raises(ApplicationError) as hidden:
        service._require_run_read(
            ActorContext(
                tenant_id=uuid7(),
                actor_id=None,
                request_id="request-02",
            ),
            run,
        )
    assert hidden.value.error_code is ErrorCode.NOT_FOUND


def test_worker_binding_and_error_helpers_are_fail_closed() -> None:
    worker = FixtureWorkerService(
        cast(Database, object()),
        FixtureOperationRegistry.from_settings(Settings(environment="test")),
        cleanup_grace=timedelta(minutes=1),
    )
    now = datetime.now(UTC)
    run = FixtureRunRecord.model_construct(execution_deadline=now + timedelta(minutes=2))
    binding = FixtureActorBindingRecord.model_construct(
        lease_status="ACTIVE",
        lease_expires_at=now + timedelta(minutes=3),
        connector_status="ACTIVE",
    )
    worker._validate_runtime_binding(binding, run, now=now)
    invalid_bindings = (
        binding.model_copy(update={"lease_status": "RELEASED"}),
        binding.model_copy(update={"lease_expires_at": now + timedelta(seconds=30)}),
        binding.model_copy(update={"connector_status": "DISABLED"}),
    )
    expected = (
        "ACCOUNT_LEASE_INACTIVE",
        "ACCOUNT_LEASE_TOO_SHORT",
        "CONNECTOR_INACTIVE",
    )
    for invalid_binding, expected_code in zip(invalid_bindings, expected, strict=True):
        with pytest.raises(_NodeValidationFailure) as captured:
            worker._validate_runtime_binding(invalid_binding, run, now=now)
        assert captured.value.code == expected_code

    assert _safe_failure_code("provider.busy-now") == "PROVIDER_BUSY_NOW"
    assert _safe_failure_code("1invalid") == "FIXTURE_OPERATION_FAILED"
    assert _safe_failure_code("x" * 81) == "FIXTURE_OPERATION_FAILED"
    errors = (
        (_invalid_request("bad"), ErrorCode.INVALID_REQUEST, 400),
        (_not_found("missing"), ErrorCode.NOT_FOUND, 404),
        (_forbidden("denied"), ErrorCode.FORBIDDEN, 403),
        (_conflict("conflict"), ErrorCode.CONFLICT, 409),
        (
            _dependency_unavailable("offline"),
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            503,
        ),
    )
    for error, code, status in errors:
        assert error.error_code is code
        assert error.status_code == status


@pytest.mark.anyio
async def test_worker_resolves_frozen_binding_types_and_rejects_missing_output() -> None:
    class OutputRepository:
        def __init__(self) -> None:
            self.outputs: dict[str, dict[str, JsonValue]] = {
                "createCustomer": {"customerRef": "customer-1"}
            }

        async def get_node_outputs(
            self,
            connection: object,
            *,
            run_id: UUID,
            node_ids: tuple[str, ...],
        ) -> dict[str, dict[str, JsonValue]]:
            assert connection is not None
            assert run_id == UUID(int=21)
            assert node_ids == ("createCustomer",)
            return self.outputs

    worker = FixtureWorkerService(
        cast(Database, object()),
        FixtureOperationRegistry.from_settings(Settings(environment="test")),
        cleanup_grace=timedelta(minutes=1),
    )
    repository = OutputRepository()
    cast(Any, worker)._runs = repository
    run = FixtureRunRecord.model_construct(
        id=UUID(int=21),
        execution_id="fixture-21",
        run_inputs={"profile": {"name": "Atlas"}},
    )
    node = CompiledNode.model_construct(
        bindings=(
            LiteralBinding(target_port="literal", value=7),
            RunInputBinding(target_port="name", pointer="/profile/name"),
            NodeOutputBinding(
                target_port="customerRef",
                source_node_id="createCustomer",
                source_port="customerRef",
            ),
            ExecutionContextBinding(target_port="executionId"),
        )
    )
    resolved = await worker._resolve_inputs(object(), run, node)
    assert resolved == {
        "literal": 7,
        "name": "Atlas",
        "customerRef": "customer-1",
        "executionId": "fixture-21",
    }

    repository.outputs = {}
    with pytest.raises(_NodeValidationFailure) as missing:
        await worker._resolve_inputs(object(), run, node)
    assert missing.value.code == "UPSTREAM_OUTPUT_MISSING"

    repository.outputs = {"createCustomer": {"customerRef": "customer-1"}}
    missing_run_input = run.model_copy(update={"run_inputs": {}})
    with pytest.raises(_NodeValidationFailure) as unresolved:
        await worker._resolve_inputs(object(), missing_run_input, node)
    assert unresolved.value.code == "RUN_INPUT_BINDING_UNRESOLVED"
