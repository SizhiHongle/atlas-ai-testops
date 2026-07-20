"""Case authoring, compilation, and immutable plan contracts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from re import fullmatch
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.workflow import (
    DraftAuthor,
    ExactVersionRef,
    GraphValidationResult,
    NodeLayout,
    OracleStrength,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)

TEST_INTENT_SCHEMA_VERSION: Literal["atlas.test-intent/0.1"] = "atlas.test-intent/0.1"
TEST_IR_SCHEMA_VERSION: Literal["atlas.test-ir/0.2"] = "atlas.test-ir/0.2"
PLAN_TEMPLATE_SCHEMA_VERSION: Literal["atlas.plan-template/0.1"] = (
    "atlas.plan-template/0.1"
)

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
CASE_KEY_PATTERN = r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){1,7}$"
REFERENCE_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9._:-]{1,159}$"
SEMVER_PATTERN = (
    r"^(?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)[.](?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:[+][0-9A-Za-z.-]+)?$"
)

CaseKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=80,
        pattern=CASE_KEY_PATTERN,
    ),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=80,
        pattern=SEMVER_PATTERN,
    ),
]


def canonical_digest(value: FrozenWireModel | dict[str, JsonValue]) -> str:
    """Return a deterministic SHA-256 digest for one wire contract."""

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


class TestCaseStatus(StrEnum):
    """Lifecycle of a stable TestCase identity."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ValueSourceKind(StrEnum):
    """Only four deterministic value sources are accepted by Test IR."""

    FIXTURE = "FIXTURE"
    ACTOR = "ACTOR"
    RUN = "RUN"
    LITERAL = "LITERAL"


class SourceRequirementRef(FrozenWireModel):
    """Digest-only link to one untrusted requirement source anchor."""

    document_id: str = Field(min_length=1, max_length=160)
    document_version: str = Field(min_length=1, max_length=80)
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    anchor: str = Field(min_length=1, max_length=320)
    excerpt_digest: str = Field(pattern=DIGEST_PATTERN)


class ActorContract(FrozenWireModel):
    """Non-secret business-role binding used by one case."""

    actor_slot: str = Field(
        min_length=2,
        max_length=80,
        pattern=REFERENCE_KEY_PATTERN,
    )
    role_id: UUID
    role_key: str = Field(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z][a-z0-9._-]{1,79}$",
    )
    role_revision: int = Field(ge=1)
    capabilities: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip().casefold() for item in values}))
        if any(
            not item or len(item) > 128 or not item[0].isalpha()
            for item in normalized
        ):
            raise ValueError("actor capabilities are invalid")
        return normalized


class FixtureContract(FrozenWireModel):
    """Exact published fixture blueprint consumed by the case."""

    blueprint_version_id: UUID
    blueprint_version_ref: ExactVersionRef
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    required_exports: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("required_exports")
    @classmethod
    def normalize_exports(cls, values: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in values.items():
            normalized_key = key.strip()
            normalized_type = value.strip()
            if (
                fullmatch(REFERENCE_KEY_PATTERN, normalized_key) is None
                or not normalized_type
                or len(normalized_type) > 160
            ):
                raise ValueError("fixture export bindings are invalid")
            if normalized_key in normalized:
                raise ValueError("fixture export bindings must be unique")
            normalized[normalized_key] = normalized_type
        return dict(sorted(normalized.items()))


class SurfaceRef(FrozenWireModel):
    """Exact published semantic page contract reference."""

    surface_key: str = Field(
        min_length=3,
        max_length=160,
        pattern=REFERENCE_KEY_PATTERN,
    )
    version_ref: ExactVersionRef
    content_digest: str = Field(pattern=DIGEST_PATTERN)


class ValueSource(FrozenWireModel):
    """Structured value source without expressions or dynamic evaluation."""

    kind: ValueSourceKind
    reference: str | None = Field(default=None, min_length=1, max_length=320)
    value: JsonValue | None = None

    @model_validator(mode="after")
    def require_exactly_one_payload(self) -> Self:
        value_supplied = "value" in self.model_fields_set
        if self.kind is ValueSourceKind.LITERAL:
            if self.reference is not None or not value_supplied:
                raise ValueError("LITERAL value source requires value only")
        elif self.reference is None or value_supplied:
            raise ValueError("non-literal value source requires reference only")
        return self


class EvidencePolicy(FrozenWireModel):
    """Environment-independent evidence requirements."""

    trace: bool = True
    screenshots: Literal["critical-actions", "assertions", "all"] = "critical-actions"
    retain_success_days: int = Field(default=7, ge=1, le=90)
    retain_failure_days: int = Field(default=30, ge=1, le=365)


class RecoveryPolicy(FrozenWireModel):
    """Bounded retry and recovery policy compiled into a plan."""

    max_unit_attempts: int = Field(default=1, ge=1, le=3)
    retry_browser_crash: bool = False
    retry_unknown_side_effect: bool = False


class OutcomePolicy(FrozenWireModel):
    """Independent Oracle authority requirements."""

    require_hard_oracle: bool = True
    evidence_incomplete_blocks_pass: bool = True
    agent_may_decide_pass: Literal[False] = False


class TestIntent(FrozenWireModel):
    """Versioned author intent referenced by one WorkflowDraft."""

    schema_version: Literal["atlas.test-intent/0.1"] = TEST_INTENT_SCHEMA_VERSION
    summary: str = Field(min_length=1, max_length=2_000)
    requirement_refs: tuple[SourceRequirementRef, ...] = Field(
        default=(),
        max_length=64,
    )
    actors: tuple[ActorContract, ...] = Field(default=(), max_length=8)
    fixture: FixtureContract | None = None
    surfaces: tuple[SurfaceRef, ...] = Field(default=(), max_length=32)
    variables: dict[str, ValueSource] = Field(default_factory=dict, max_length=128)
    evidence_policy: EvidencePolicy = EvidencePolicy()
    recovery_policy: RecoveryPolicy = RecoveryPolicy()
    outcome_policy: OutcomePolicy = OutcomePolicy()
    required_features: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("required_features")
    @classmethod
    def normalize_features(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({item.strip().casefold() for item in values}))
        if any(
            not item or len(item) > 160 or not item[0].isalpha()
            for item in normalized
        ):
            raise ValueError("required features are invalid")
        return normalized

    @field_validator("variables")
    @classmethod
    def normalize_variables(cls, values: dict[str, ValueSource]) -> dict[str, ValueSource]:
        normalized: dict[str, ValueSource] = {}
        for key, value in values.items():
            normalized_key = key.strip()
            if fullmatch(REFERENCE_KEY_PATTERN, normalized_key) is None:
                raise ValueError("variable names are invalid")
            if normalized_key in normalized:
                raise ValueError("variable names must be unique")
            normalized[normalized_key] = value
        return dict(sorted(normalized.items()))

    @model_validator(mode="after")
    def enforce_single_active_actor(self) -> Self:
        if len(self.actors) > 1:
            raise ValueError("the initial case protocol supports one active actor")
        if len({item.actor_slot for item in self.actors}) != len(self.actors):
            raise ValueError("actor slots must be unique")
        return self


class CreateTestCase(FrozenWireModel):
    """Create a TestCase and its unique current WorkflowDraft atomically."""

    case_key: CaseKey
    name: str = Field(min_length=1, max_length=160)
    intent_version: SemanticVersion = "0.1.0"
    intent: TestIntent
    graph: WorkflowGraph = WorkflowGraph(nodes=(), edges=())
    layout: dict[str, NodeLayout] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_layout_nodes(self) -> Self:
        node_ids = {node.id for node in self.graph.nodes}
        unknown = set(self.layout).difference(node_ids)
        if unknown:
            raise ValueError("layout references unknown graph nodes")
        return self


class TestCase(FrozenWireModel):
    """Stable TestCase aggregate identity and current intent."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    case_key: str
    name: str
    status: TestCaseStatus
    intent_version: str
    intent_version_ref: str
    intent: TestIntent
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TestCaseCatalogItem(TestCase):
    """TestCase list projection with its current draft state."""

    draft_id: UUID
    semantic_revision: int = Field(ge=0)
    layout_revision: int = Field(ge=0)
    graph_valid: bool
    updated_by: DraftAuthor


class TestCasePage(FrozenWireModel):
    """Cursor page of TestCase catalog entries."""

    items: tuple[TestCaseCatalogItem, ...]
    next_cursor: str | None = None


class WorkflowDraftSnapshot(FrozenWireModel):
    """Authoritative current WorkflowDraft projection."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    test_case_id: UUID
    semantic_revision: int = Field(ge=0)
    layout_revision: int = Field(ge=0)
    graph: WorkflowGraph
    layout: dict[str, NodeLayout]
    intent_version_ref: str
    updated_by: DraftAuthor
    semantic_digest: str = Field(pattern=DIGEST_PATTERN)
    validation: GraphValidationResult
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AddNodeOperation(FrozenWireModel):
    op: Literal["ADD_NODE"] = "ADD_NODE"
    node: WorkflowNode


class ReplaceNodeOperation(FrozenWireModel):
    op: Literal["REPLACE_NODE"] = "REPLACE_NODE"
    node_id: str
    node: WorkflowNode

    @model_validator(mode="after")
    def preserve_node_identity(self) -> Self:
        if self.node.id != self.node_id:
            raise ValueError("replacement node must preserve nodeId")
        return self


class RemoveNodeOperation(FrozenWireModel):
    op: Literal["REMOVE_NODE"] = "REMOVE_NODE"
    node_id: str


class AddEdgeOperation(FrozenWireModel):
    op: Literal["ADD_EDGE"] = "ADD_EDGE"
    edge: WorkflowEdge


class ReplaceEdgeOperation(FrozenWireModel):
    op: Literal["REPLACE_EDGE"] = "REPLACE_EDGE"
    edge_id: str
    edge: WorkflowEdge

    @model_validator(mode="after")
    def preserve_edge_identity(self) -> Self:
        if self.edge.id != self.edge_id:
            raise ValueError("replacement edge must preserve edgeId")
        return self


class RemoveEdgeOperation(FrozenWireModel):
    op: Literal["REMOVE_EDGE"] = "REMOVE_EDGE"
    edge_id: str


DraftOperation = Annotated[
    AddNodeOperation
    | ReplaceNodeOperation
    | RemoveNodeOperation
    | AddEdgeOperation
    | ReplaceEdgeOperation
    | RemoveEdgeOperation,
    Field(discriminator="op"),
]


class WorkflowPatch(FrozenWireModel):
    """Atomic semantic edit shared by AI and human authoring."""

    patch_id: UUID
    client_mutation_id: str = Field(min_length=8, max_length=200)
    base_semantic_revision: int = Field(ge=0)
    source: DraftAuthor
    operations: tuple[DraftOperation, ...] = Field(min_length=1, max_length=128)
    rationale_summary: str | None = Field(default=None, min_length=1, max_length=1_000)


class LayoutPatch(FrozenWireModel):
    """Layout-only mutation that cannot invalidate semantic debug evidence."""

    client_mutation_id: str = Field(min_length=8, max_length=200)
    base_layout_revision: int = Field(ge=0)
    source: DraftAuthor
    positions: dict[str, NodeLayout] = Field(min_length=1, max_length=512)


class PatchIssueCode(StrEnum):
    NODE_ALREADY_EXISTS = "NODE_ALREADY_EXISTS"
    NODE_NOT_FOUND = "NODE_NOT_FOUND"
    EDGE_ALREADY_EXISTS = "EDGE_ALREADY_EXISTS"
    EDGE_NOT_FOUND = "EDGE_NOT_FOUND"
    DANGLING_EDGE = "DANGLING_EDGE"
    LAYOUT_NODE_NOT_FOUND = "LAYOUT_NODE_NOT_FOUND"


class PatchIssue(FrozenWireModel):
    code: PatchIssueCode
    message: str
    operation_index: int | None = None
    node_id: str | None = None
    edge_id: str | None = None


class WorkflowPatchPreview(FrozenWireModel):
    """Pure patch preview; graph validity and patch applicability are separate."""

    applicable: bool
    issues: tuple[PatchIssue, ...]
    graph: WorkflowGraph
    semantic_digest: str
    validation: GraphValidationResult


class CaseCompileIssueCode(StrEnum):
    GRAPH_INVALID = "GRAPH_INVALID"
    REQUIREMENT_SOURCE_MISSING = "REQUIREMENT_SOURCE_MISSING"
    ACTOR_BINDING_MISSING = "ACTOR_BINDING_MISSING"
    FIXTURE_BINDING_MISSING = "FIXTURE_BINDING_MISSING"
    FIXTURE_GRAPH_MISMATCH = "FIXTURE_GRAPH_MISMATCH"
    SURFACE_BINDING_MISSING = "SURFACE_BINDING_MISSING"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    HARD_ORACLE_MISSING = "HARD_ORACLE_MISSING"


class CaseCompileIssue(FrozenWireModel):
    code: CaseCompileIssueCode
    message: str
    node_id: str | None = None


class AssertionSpec(FrozenWireModel):
    """Immutable Oracle reference derived from an assertion graph node."""

    assertion_id: str
    node_id: str
    evaluator_version_ref: ExactVersionRef
    strength: OracleStrength


class TestIR(FrozenWireModel):
    """Strongly typed, environment-independent Test IR v0.2."""

    schema_version: Literal["atlas.test-ir/0.2"] = TEST_IR_SCHEMA_VERSION
    test_case_id: UUID
    semantic_revision: int = Field(ge=0)
    intent_version_ref: ExactVersionRef
    requirement_refs: tuple[SourceRequirementRef, ...]
    actors: tuple[ActorContract, ...]
    fixture: FixtureContract
    workflow: WorkflowGraph
    surfaces: tuple[SurfaceRef, ...]
    variables: dict[str, ValueSource]
    assertions: tuple[AssertionSpec, ...]
    evidence_policy: EvidencePolicy
    recovery_policy: RecoveryPolicy
    outcome_policy: OutcomePolicy
    required_features: tuple[str, ...]
    execution_levels: tuple[tuple[str, ...], ...]
    content_digest: str = Field(pattern=DIGEST_PATTERN)


class PlanNodeRef(FrozenWireModel):
    node_id: str
    kind: str
    version_ref: ExactVersionRef
    execution_level: int = Field(ge=0)


class PlanTemplate(FrozenWireModel):
    """Environment-independent plan compiled from one exact Test IR."""

    schema_version: Literal["atlas.plan-template/0.1"] = PLAN_TEMPLATE_SCHEMA_VERSION
    test_case_id: UUID
    semantic_revision: int = Field(ge=0)
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    graph_digest: str = Field(pattern=DIGEST_PATTERN)
    nodes: tuple[PlanNodeRef, ...]
    execution_levels: tuple[tuple[str, ...], ...]
    required_features: tuple[str, ...]
    plan_digest: str = Field(pattern=DIGEST_PATTERN)


class CaseCompilationResult(FrozenWireModel):
    valid: bool
    issues: tuple[CaseCompileIssue, ...]
    test_ir: TestIR | None = None
    plan_template: PlanTemplate | None = None
    compiled_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
