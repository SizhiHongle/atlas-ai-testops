"""Versioned contracts for fixture atoms, blueprints, and compiled plans."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.platform import EnvironmentKind

ATOM_SCHEMA_VERSION: Literal["atlas.atom/0.1"] = "atlas.atom/0.1"
BLUEPRINT_SCHEMA_VERSION: Literal["atlas.fixture-blueprint/0.1"] = "atlas.fixture-blueprint/0.1"
COMPILED_PLAN_SCHEMA_VERSION: Literal["atlas.compiled-fixture-plan/0.1"] = (
    "atlas.compiled-fixture-plan/0.1"
)
FIXTURE_MANIFEST_SCHEMA_VERSION: Literal["atlas.fixture-manifest/0.1"] = (
    "atlas.fixture-manifest/0.1"
)

ASSET_KEY_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,7}$"
PORT_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$"
SEMVER_PATTERN = (
    r"^(?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)
JSON_POINTER_PATTERN = r"^(?:/(?:[^~/]|~[01])*)+$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"

AssetKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=160,
        pattern=ASSET_KEY_PATTERN,
    ),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=5, max_length=80, pattern=SEMVER_PATTERN),
]
PortKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=PORT_KEY_PATTERN,
    ),
]


class FixtureCommand(FrozenWireModel):
    """Normalize short command text before applying length constraints."""

    model_config = ConfigDict(str_strip_whitespace=True)


class AssetDefinitionStatus(StrEnum):
    """Lifecycle of a stable asset identity."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AssetVersionStatus(StrEnum):
    """Lifecycle of an immutable-after-publication asset version."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class ValidationState(StrEnum):
    """Independent static, runtime, and cleanup publication evidence."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


class AtomEffect(StrEnum):
    """Externally observable effect of an atom operation."""

    READ = "READ"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    WAIT = "WAIT"


class PortDirection(StrEnum):
    """Direction of a strongly typed atom port."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class DataClassification(StrEnum):
    """Data exposure class used by compilation and manifest gates."""

    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    SENSITIVE = "SENSITIVE"


class ResourceOwnership(StrEnum):
    """Ownership determines whether automatic cleanup may delete a resource."""

    CREATED = "CREATED"
    ADOPTED = "ADOPTED"
    LEASED = "LEASED"
    SHARED = "SHARED"


class IdempotencyMode(StrEnum):
    """Supported provider idempotency and reconciliation strategies."""

    PROVIDER_NATIVE = "PROVIDER_NATIVE"
    BUSINESS_MARKER = "BUSINESS_MARKER"
    RECONCILE = "RECONCILE"


class PostconditionKind(StrEnum):
    """Structured postconditions that never execute arbitrary expressions."""

    OUTPUT_SCHEMA = "OUTPUT_SCHEMA"
    RESOURCE_VISIBLE = "RESOURCE_VISIBLE"
    RESOURCE_RELATION = "RESOURCE_RELATION"


class CleanupPolicy(StrEnum):
    """Fixture cleanup policy frozen into a blueprint version."""

    ALWAYS = "ALWAYS"
    RETAIN_ON_FAILURE = "RETAIN_ON_FAILURE"


def validate_json_schema(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Validate a JSON Schema 2020-12 document without evaluating user code."""

    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise ValueError("invalid JSON Schema 2020-12 document") from error
    return value


def canonical_digest(value: FrozenWireModel | dict[str, JsonValue]) -> str:
    """Return a stable SHA-256 digest for a wire contract or JSON object."""

    payload: object
    if isinstance(value, FrozenWireModel):
        payload = value.model_dump(mode="json", by_alias=True)
    else:
        payload = value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class AtomPort(FrozenWireModel):
    """A JSON-Schema-backed port with a domain semantic type."""

    key: PortKey
    direction: PortDirection
    semantic_type: str = Field(min_length=3, max_length=160, pattern=ASSET_KEY_PATTERN)
    json_schema: dict[str, JsonValue]
    required: bool = True
    classification: DataClassification = DataClassification.INTERNAL

    @field_validator("json_schema")
    @classmethod
    def check_json_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return validate_json_schema(value)

    @model_validator(mode="after")
    def protect_secret_boundaries(self) -> Self:
        """Keep raw credentials and browser state outside fixture data ports."""

        lowered = self.semantic_type.casefold()
        forbidden = ("password", "secret", "cookie", "token", "storage-state")
        if any(item in lowered for item in forbidden):
            raise ValueError("fixture ports cannot carry credentials or browser state")
        if self.direction is PortDirection.OUTPUT and not self.required:
            raise ValueError("output ports must be required when the atom succeeds")
        return self


class ConnectorOperationRef(FrozenWireModel):
    """A deployment-registered operation; never a URL, module, or callable."""

    operation_key: AssetKey
    operation_version: SemanticVersion
    required_capabilities: tuple[AssetKey, ...] = Field(min_length=1, max_length=32)
    timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("required_capabilities")
    @classmethod
    def normalize_capabilities(cls, values: tuple[AssetKey, ...]) -> tuple[AssetKey, ...]:
        return tuple(sorted(set(values)))


class RetryPolicy(FrozenWireModel):
    """Bounded retry budget for transient connector outcomes."""

    max_attempts: int = Field(default=3, ge=1, le=6)
    initial_backoff_ms: int = Field(default=250, ge=50, le=30_000)
    maximum_backoff_ms: int = Field(default=5_000, ge=50, le=120_000)
    retryable_categories: tuple[Literal["RATE_LIMIT", "TRANSIENT", "AUTH_REFRESH"], ...] = (
        "RATE_LIMIT",
        "TRANSIENT",
    )

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.maximum_backoff_ms < self.initial_backoff_ms:
            raise ValueError("maximumBackoffMs must be at least initialBackoffMs")
        if len(set(self.retryable_categories)) != len(self.retryable_categories):
            raise ValueError("retryableCategories must be unique")
        return self


class IdempotencyPolicy(FrozenWireModel):
    """Stable provider-side deduplication and reconciliation contract."""

    mode: IdempotencyMode
    marker_input: PortKey | None = None

    @model_validator(mode="after")
    def require_marker_when_needed(self) -> Self:
        if (
            self.mode in {IdempotencyMode.BUSINESS_MARKER, IdempotencyMode.RECONCILE}
            and self.marker_input is None
        ):
            raise ValueError("markerInput is required for marker or reconcile idempotency")
        return self


class Postcondition(FrozenWireModel):
    """A bounded read-after-write or output verification declaration."""

    kind: PostconditionKind
    operation: ConnectorOperationRef | None = None
    output_port: PortKey | None = None

    @model_validator(mode="after")
    def require_structured_target(self) -> Self:
        if self.kind is PostconditionKind.OUTPUT_SCHEMA and self.output_port is None:
            raise ValueError("OUTPUT_SCHEMA postcondition requires outputPort")
        if self.kind is not PostconditionKind.OUTPUT_SCHEMA and self.operation is None:
            raise ValueError("resource postcondition requires a connector operation")
        return self


class ResourcePolicy(FrozenWireModel):
    """How an external resource is registered and later cleaned up."""

    resource_type: str = Field(min_length=3, max_length=160, pattern=ASSET_KEY_PATTERN)
    ownership: ResourceOwnership = ResourceOwnership.CREATED
    resource_ref_output: PortKey
    parent_ref_inputs: tuple[PortKey, ...] = Field(default=(), max_length=16)
    ttl_seconds: int = Field(default=3_600, ge=60, le=604_800)

    @field_validator("parent_ref_inputs")
    @classmethod
    def normalize_parent_inputs(cls, values: tuple[PortKey, ...]) -> tuple[PortKey, ...]:
        if len(set(values)) != len(values):
            raise ValueError("parentRefInputs must be unique")
        return values


class CleanupContract(FrozenWireModel):
    """A reviewed operation that cleans one ledger-owned resource."""

    operation: ConnectorOperationRef
    resource_ref_input: PortKey
    verify_operation: ConnectorOperationRef | None = None


class ReconcileContract(FrozenWireModel):
    """A reviewed lookup used after an uncertain provider outcome."""

    operation: ConnectorOperationRef
    marker_input: PortKey
    resource_ref_output: PortKey


class DataAtomContract(FrozenWireModel):
    """Immutable atom behavior and safety contract exported as atlas.atom/0.1."""

    schema_version: Literal["atlas.atom/0.1"] = ATOM_SCHEMA_VERSION
    effect: AtomEffect
    ports: tuple[AtomPort, ...] = Field(min_length=1, max_length=64)
    operation: ConnectorOperationRef
    retry_policy: RetryPolicy = RetryPolicy()
    idempotency_policy: IdempotencyPolicy
    postconditions: tuple[Postcondition, ...] = Field(min_length=1, max_length=16)
    resource_policy: ResourcePolicy | None = None
    cleanup_contract: CleanupContract | None = None
    reconcile_contract: ReconcileContract | None = None
    allowed_environment_kinds: tuple[EnvironmentKind, ...] = Field(
        default=(EnvironmentKind.TEST, EnvironmentKind.STAGING),
        validate_default=True,
    )

    @field_validator("allowed_environment_kinds")
    @classmethod
    def normalize_environment_kinds(
        cls,
        values: tuple[EnvironmentKind, ...],
    ) -> tuple[EnvironmentKind, ...]:
        if not values or EnvironmentKind.PRODUCTION in values:
            raise ValueError("fixture atoms cannot target production environments")
        return tuple(sorted(set(values), key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_atom_invariants(self) -> Self:
        keyed_ports = [(port.direction, port.key) for port in self.ports]
        if len(set(keyed_ports)) != len(keyed_ports):
            raise ValueError("atom port keys must be unique within each direction")
        input_ports = {
            port.key: port for port in self.ports if port.direction is PortDirection.INPUT
        }
        output_ports = {
            port.key: port for port in self.ports if port.direction is PortDirection.OUTPUT
        }
        if not output_ports:
            raise ValueError("an atom must publish at least one output port")
        marker = self.idempotency_policy.marker_input
        if marker is not None and marker not in input_ports:
            raise ValueError("idempotency markerInput must reference an input port")
        for postcondition in self.postconditions:
            if (
                postcondition.output_port is not None
                and postcondition.output_port not in output_ports
            ):
                raise ValueError("postcondition outputPort must reference an output port")
        if self.effect is AtomEffect.CREATE:
            if self.resource_policy is None:
                raise ValueError("CREATE atom requires resourcePolicy")
            if self.resource_policy.ownership is not ResourceOwnership.CREATED:
                raise ValueError("CREATE atom resource ownership must be CREATED")
            if self.cleanup_contract is None:
                raise ValueError("CREATE atom requires cleanupContract")
            if self.reconcile_contract is None:
                raise ValueError("CREATE atom requires reconcileContract")
        if self.resource_policy is not None:
            if self.resource_policy.resource_ref_output not in output_ports:
                raise ValueError("resourceRefOutput must reference an output port")
            if any(item not in input_ports for item in self.resource_policy.parent_ref_inputs):
                raise ValueError("parentRefInputs must reference input ports")
        if (
            self.cleanup_contract is not None
            and self.cleanup_contract.resource_ref_input not in output_ports
        ):
            raise ValueError("cleanup resourceRefInput must reference an output port")
        if self.reconcile_contract is not None:
            if self.reconcile_contract.marker_input not in input_ports:
                raise ValueError("reconcile markerInput must reference an input port")
            if self.reconcile_contract.resource_ref_output not in output_ports:
                raise ValueError("reconcile resourceRefOutput must reference an output port")
        return self


class CreateDataAtom(FixtureCommand):
    """Create a stable atom identity inside a project."""

    atom_key: AssetKey
    business_domain: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9-]{1,79}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1_000)


class UpdateDataAtom(FixtureCommand):
    """Update mutable atom definition metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=1_000)
    status: AssetDefinitionStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.name is None and self.description is None and self.status is None:
            raise ValueError("at least one atom definition field is required")
        return self


class CreateDataAtomVersion(FixtureCommand):
    """Create a mutable draft of one exact atom version."""

    version: SemanticVersion
    contract: DataAtomContract


class UpdateDataAtomVersion(FixtureCommand):
    """Replace the contract of a non-published atom version."""

    contract: DataAtomContract


class DataAtomDefinition(FrozenWireModel):
    """Stable atom identity independent of any version."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    atom_key: str
    business_domain: str
    name: str
    description: str
    status: AssetDefinitionStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DataAtomVersion(FrozenWireModel):
    """One contract version with three independent publication evidence states."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    atom_id: UUID
    version: str
    status: AssetVersionStatus
    contract: DataAtomContract
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    static_validation_state: ValidationState
    runtime_validation_state: ValidationState
    cleanup_validation_state: ValidationState
    validated_at: AwareDatetime | None
    published_at: AwareDatetime | None
    published_by: UUID | None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DataAtomCatalogItem(DataAtomDefinition):
    """Atom definition plus the most advanced visible version projection."""

    latest_version_id: UUID | None = None
    latest_version: str | None = None
    latest_version_status: AssetVersionStatus | None = None
    latest_effect: AtomEffect | None = None
    input_ports: tuple[str, ...] = ()
    output_ports: tuple[str, ...] = ()
    cleanup_capable: bool = False


class DataAtomPage(FrozenWireModel):
    """Cursor page for the atom catalog."""

    items: tuple[DataAtomCatalogItem, ...]
    next_cursor: str | None = None


class DataAtomVersionPage(FrozenWireModel):
    """Cursor page for versions of one atom definition."""

    items: tuple[DataAtomVersion, ...]
    next_cursor: str | None = None


class BindingBase(FrozenWireModel):
    """Common target port for a structured blueprint input binding."""

    target_port: PortKey


class LiteralBinding(BindingBase):
    kind: Literal["LITERAL"] = "LITERAL"
    value: JsonValue


class RunInputBinding(BindingBase):
    kind: Literal["RUN_INPUT"] = "RUN_INPUT"
    pointer: str = Field(min_length=1, max_length=512, pattern=JSON_POINTER_PATTERN)


class NodeOutputBinding(BindingBase):
    kind: Literal["NODE_OUTPUT"] = "NODE_OUTPUT"
    source_node_id: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    source_port: PortKey


class ExecutionContextBinding(BindingBase):
    kind: Literal["EXECUTION_CONTEXT"] = "EXECUTION_CONTEXT"
    field: Literal["executionId"] = "executionId"


BlueprintInputBinding = Annotated[
    LiteralBinding | RunInputBinding | NodeOutputBinding | ExecutionContextBinding,
    Field(discriminator="kind"),
]


class BlueprintNode(FrozenWireModel):
    """One exact atom version and its structured inputs inside a static DAG."""

    id: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    atom_version_id: UUID
    actor_slot: str = Field(min_length=2, max_length=80, pattern=PORT_KEY_PATTERN)
    bindings: tuple[BlueprintInputBinding, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def require_unique_target_bindings(self) -> Self:
        targets = [binding.target_port for binding in self.bindings]
        if len(set(targets)) != len(targets):
            raise ValueError("a blueprint node input cannot have multiple bindings")
        return self


class BlueprintExport(FrozenWireModel):
    """An explicitly named field visible to downstream test execution."""

    name: PortKey
    source_node_id: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    source_port: PortKey
    classification: DataClassification = DataClassification.INTERNAL

    @model_validator(mode="after")
    def reject_sensitive_export(self) -> Self:
        if self.classification is DataClassification.SENSITIVE:
            raise ValueError("SENSITIVE data cannot be exported in a fixture manifest")
        return self


class DataBlueprintContract(FrozenWireModel):
    """Static fixture DAG exported as atlas.fixture-blueprint/0.1."""

    schema_version: Literal["atlas.fixture-blueprint/0.1"] = BLUEPRINT_SCHEMA_VERSION
    run_input_schema: dict[str, JsonValue]
    nodes: tuple[BlueprintNode, ...] = Field(min_length=1, max_length=100)
    exports: tuple[BlueprintExport, ...] = Field(min_length=1, max_length=100)
    cleanup_policy: CleanupPolicy = CleanupPolicy.ALWAYS

    @field_validator("run_input_schema")
    @classmethod
    def check_run_input_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return validate_json_schema(value)

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        node_ids = [node.id for node in self.nodes]
        export_names = [item.name for item in self.exports]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("blueprint node IDs must be unique")
        if len(set(export_names)) != len(export_names):
            raise ValueError("blueprint export names must be unique")
        return self


class CreateDataBlueprint(FixtureCommand):
    """Create a stable data blueprint identity."""

    blueprint_key: AssetKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1_000)


class UpdateDataBlueprint(FixtureCommand):
    """Update mutable blueprint definition metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, min_length=1, max_length=1_000)
    status: AssetDefinitionStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.name is None and self.description is None and self.status is None:
            raise ValueError("at least one blueprint definition field is required")
        return self


class CreateDataBlueprintVersion(FixtureCommand):
    """Create a mutable draft of one exact blueprint version."""

    version: SemanticVersion
    contract: DataBlueprintContract


class UpdateDataBlueprintVersion(FixtureCommand):
    """Replace the contract of a non-published blueprint version."""

    contract: DataBlueprintContract


class DataBlueprintDefinition(FrozenWireModel):
    """Stable blueprint identity independent of any version."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    blueprint_key: str
    name: str
    description: str
    status: AssetDefinitionStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DataBlueprintVersion(FrozenWireModel):
    """One blueprint contract and its latest deterministic compilation."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    blueprint_id: UUID
    version: str
    status: AssetVersionStatus
    contract: DataBlueprintContract
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    static_validation_state: ValidationState
    runtime_validation_state: ValidationState
    cleanup_validation_state: ValidationState
    validated_at: AwareDatetime | None
    compiled_plan: CompiledFixturePlan | None = None
    plan_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    compile_issues: tuple[CompileIssue, ...] = ()
    compiled_at: AwareDatetime | None
    published_at: AwareDatetime | None
    published_by: UUID | None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DataBlueprintCatalogItem(DataBlueprintDefinition):
    """Blueprint definition plus the most advanced visible version projection."""

    latest_version_id: UUID | None = None
    latest_version: str | None = None
    latest_version_status: AssetVersionStatus | None = None
    node_count: int = Field(default=0, ge=0)
    export_count: int = Field(default=0, ge=0)
    plan_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)


class DataBlueprintPage(FrozenWireModel):
    items: tuple[DataBlueprintCatalogItem, ...]
    next_cursor: str | None = None


class DataBlueprintVersionPage(FrozenWireModel):
    items: tuple[DataBlueprintVersion, ...]
    next_cursor: str | None = None


class CompileIssueCode(StrEnum):
    """Stable compiler failures safe for API and UI consumption."""

    ATOM_VERSION_NOT_FOUND = "ATOM_VERSION_NOT_FOUND"
    ATOM_VERSION_NOT_VALIDATED = "ATOM_VERSION_NOT_VALIDATED"
    TARGET_PORT_NOT_FOUND = "TARGET_PORT_NOT_FOUND"
    SOURCE_NODE_NOT_FOUND = "SOURCE_NODE_NOT_FOUND"
    SOURCE_PORT_NOT_FOUND = "SOURCE_PORT_NOT_FOUND"
    PORT_TYPE_MISMATCH = "PORT_TYPE_MISMATCH"
    REQUIRED_INPUT_MISSING = "REQUIRED_INPUT_MISSING"
    LITERAL_SCHEMA_MISMATCH = "LITERAL_SCHEMA_MISMATCH"
    GRAPH_CYCLE_DETECTED = "GRAPH_CYCLE_DETECTED"
    EXPORT_SOURCE_NOT_FOUND = "EXPORT_SOURCE_NOT_FOUND"
    EXPORT_CLASSIFICATION_MISMATCH = "EXPORT_CLASSIFICATION_MISMATCH"
    CLEANUP_CONTRACT_MISSING = "CLEANUP_CONTRACT_MISSING"
    FORBIDDEN_SECRET_FLOW = "FORBIDDEN_SECRET_FLOW"


class CompileIssue(FrozenWireModel):
    code: CompileIssueCode
    message: str = Field(min_length=1, max_length=500)
    node_id: str | None = None
    port_key: str | None = None
    export_name: str | None = None


class CompiledNode(FrozenWireModel):
    node_id: str
    atom_version_id: UUID
    atom_digest: str = Field(pattern=DIGEST_PATTERN)
    actor_slot: str
    bindings: tuple[BlueprintInputBinding, ...]
    execution_level: int = Field(ge=0)


class CompiledFixturePlan(FrozenWireModel):
    """Immutable, digest-addressed plan consumed by the future Fixture Worker."""

    schema_version: Literal["atlas.compiled-fixture-plan/0.1"] = COMPILED_PLAN_SCHEMA_VERSION
    blueprint_version_id: UUID
    blueprint_digest: str = Field(pattern=DIGEST_PATTERN)
    nodes: tuple[CompiledNode, ...]
    execution_levels: tuple[tuple[str, ...], ...]
    cleanup_order: tuple[str, ...]
    exports: tuple[BlueprintExport, ...]
    plan_digest: str = Field(pattern=DIGEST_PATTERN)


class BlueprintCompilationResult(FrozenWireModel):
    valid: bool
    issues: tuple[CompileIssue, ...]
    plan: CompiledFixturePlan | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.valid != (not self.issues and self.plan is not None):
            raise ValueError("valid compilation requires a plan and no issues")
        return self


class CompileBlueprintResponse(FrozenWireModel):
    version: DataBlueprintVersion
    compilation: BlueprintCompilationResult


class FixtureManifest(FrozenWireModel):
    """Only explicitly exported fixture values cross into test execution."""

    schema_version: Literal["atlas.fixture-manifest/0.1"] = FIXTURE_MANIFEST_SCHEMA_VERSION
    fixture_run_id: UUID
    blueprint_version_id: UUID
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    exports: dict[str, JsonValue]
