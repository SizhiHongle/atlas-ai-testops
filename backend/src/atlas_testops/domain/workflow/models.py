"""Strict Workflow Graph protocol models."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"
PORT_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$"
EXACT_VERSION_REF_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"
    r"@[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
WORKFLOW_GRAPH_SCHEMA_VERSION: Literal["atlas.workflow-graph/0.1"] = "atlas.workflow-graph/0.1"
WORKFLOW_DRAFT_SCHEMA_VERSION: Literal["atlas.workflow-draft/0.1"] = "atlas.workflow-draft/0.1"

ExactVersionRef = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=EXACT_VERSION_REF_PATTERN,
    ),
]


class DomainModel(BaseModel):
    """Base model for immutable Python objects and camelCase wire payloads."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class WorkflowPhase(StrEnum):
    """Supported deterministic workflow phases."""

    SETUP = "setup"
    IDENTITY = "identity"
    EXECUTE = "execute"
    ASSERT = "assert"
    CLEANUP = "cleanup"


class PortKind(StrEnum):
    """Port trust and scheduling category."""

    DATA = "data"
    CONTROL = "control"


class OracleStrength(StrEnum):
    """Oracle authority level."""

    HARD = "hard"
    SOFT = "soft"
    DIAGNOSTIC = "diagnostic"


class EdgeMapping(StrEnum):
    """Supported edge mapping operations for the v0.1 contract."""

    DIRECT = "direct"


class DraftAuthor(StrEnum):
    """Actor that last changed a Workflow Draft."""

    AI = "ai"
    HUMAN = "human"


class PortSpec(DomainModel):
    """A strongly typed node port."""

    key: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    semantic_type: str = Field(min_length=1, max_length=128)
    kind: PortKind = PortKind.DATA
    required: bool = True
    sensitive: bool = False

    @field_validator("key", "semantic_type", mode="before")
    @classmethod
    def strip_port_values(cls, value: object) -> object:
        """Strip textual protocol values before field validation."""
        return value.strip() if isinstance(value, str) else value


class WorkflowNode(DomainModel):
    """A published node reference inside an authoring graph."""

    id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    kind: str = Field(min_length=1, max_length=64)
    version_ref: ExactVersionRef
    phase: WorkflowPhase
    input_ports: tuple[PortSpec, ...] = ()
    output_ports: tuple[PortSpec, ...] = ()
    params: dict[str, JsonValue] = Field(default_factory=dict)
    terminal: bool = False
    oracle_strength: OracleStrength | None = None

    @field_validator("kind", "version_ref", mode="before")
    @classmethod
    def strip_node_values(cls, value: object) -> object:
        """Strip textual protocol values before field validation."""
        return value.strip() if isinstance(value, str) else value


class WorkflowEdge(DomainModel):
    """A direct typed mapping between two node ports."""

    id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    source_node_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    source_port: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    target_node_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    target_port: str = Field(min_length=1, max_length=128, pattern=PORT_KEY_PATTERN)
    semantic_type: str = Field(min_length=1, max_length=128)
    kind: PortKind = PortKind.DATA
    mapping: EdgeMapping = EdgeMapping.DIRECT

    @field_validator("semantic_type", mode="before")
    @classmethod
    def strip_semantic_type(cls, value: object) -> object:
        """Strip the declared edge semantic type before validation."""
        return value.strip() if isinstance(value, str) else value


class WorkflowGraph(DomainModel):
    """The semantic graph owned by a WorkflowDraft revision."""

    schema_version: Literal["atlas.workflow-graph/0.1"] = WORKFLOW_GRAPH_SCHEMA_VERSION
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]


class NodeLayout(DomainModel):
    """Authoring-only node position excluded from semantic graph revisions."""

    x: float
    y: float


class WorkflowDraft(DomainModel):
    """Mutable authoring state with separate semantic and layout revisions."""

    schema_version: Literal["atlas.workflow-draft/0.1"] = WORKFLOW_DRAFT_SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    test_case_id: str = Field(min_length=1, max_length=128, pattern=ID_PATTERN)
    semantic_revision: int = Field(ge=0)
    layout_revision: int = Field(ge=0)
    graph: WorkflowGraph
    layout: dict[str, NodeLayout] = Field(default_factory=dict)
    intent_version_ref: ExactVersionRef
    updated_by: DraftAuthor


class GraphIssueCode(StrEnum):
    """Stable machine-readable graph validation failures."""

    EMPTY_GRAPH = "EMPTY_GRAPH"
    DUPLICATE_NODE_ID = "DUPLICATE_NODE_ID"
    DUPLICATE_EDGE_ID = "DUPLICATE_EDGE_ID"
    DUPLICATE_PORT = "DUPLICATE_PORT"
    DANGLING_EDGE = "DANGLING_EDGE"
    SOURCE_PORT_MISSING = "SOURCE_PORT_MISSING"
    TARGET_PORT_MISSING = "TARGET_PORT_MISSING"
    PORT_KIND_MISMATCH = "PORT_KIND_MISMATCH"
    PORT_TYPE_MISMATCH = "PORT_TYPE_MISMATCH"
    EDGE_TYPE_DECLARATION_MISMATCH = "EDGE_TYPE_DECLARATION_MISMATCH"
    REQUIRED_INPUT_MISSING = "REQUIRED_INPUT_MISSING"
    INPUT_MULTIPLE_WRITERS = "INPUT_MULTIPLE_WRITERS"
    GRAPH_CYCLE = "GRAPH_CYCLE"
    ORPHAN_NODE = "ORPHAN_NODE"
    MISSING_SUCCESSOR = "MISSING_SUCCESSOR"
    INVALID_TERMINAL = "INVALID_TERMINAL"
    ASSERTION_COVERAGE_MISSING = "ASSERTION_COVERAGE_MISSING"


class GraphIssue(DomainModel):
    """One graph validation failure."""

    code: GraphIssueCode
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class GraphValidationResult(DomainModel):
    """Validation result and deterministic execution levels."""

    valid: bool
    issues: tuple[GraphIssue, ...]
    execution_levels: tuple[tuple[str, ...], ...]
    matched_required_inputs: int
    total_required_inputs: int
