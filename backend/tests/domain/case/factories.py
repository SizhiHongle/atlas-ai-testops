"""Reusable deterministic TestCase protocol factories."""

from collections.abc import Callable
from uuid import UUID

from atlas_testops.domain.case import (
    ActorContract,
    FixtureContract,
    SourceRequirementRef,
    SurfaceRef,
    TestIntent,
)
from atlas_testops.domain.workflow import (
    OracleStrength,
    PortSpec,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPhase,
)

DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"


def _port(key: str, semantic_type: str) -> PortSpec:
    return PortSpec(key=key, semantic_type=semantic_type)


def build_valid_graph() -> WorkflowGraph:
    """Build the shared valid workflow graph."""

    nodes = (
        WorkflowNode(
            id="prepare-data",
            kind="fixture",
            version_ref="fixture.customer@1.0.0",
            phase=WorkflowPhase.SETUP,
            output_ports=(_port("customerId", "CustomerId"),),
        ),
        WorkflowNode(
            id="filter-agent",
            kind="agent",
            version_ref="agent.semantic-filter@1.0.0",
            phase=WorkflowPhase.EXECUTE,
            input_ports=(_port("customerId", "CustomerId"),),
            output_ports=(_port("rows", "CustomerRows"),),
        ),
        WorkflowNode(
            id="relationship-assert",
            kind="assertion",
            version_ref="assert.customer-visible@1.0.0",
            phase=WorkflowPhase.ASSERT,
            input_ports=(_port("rows", "CustomerRows"),),
            output_ports=(_port("result", "AssertionResult"),),
            oracle_strength=OracleStrength.HARD,
        ),
        WorkflowNode(
            id="cleanup",
            kind="cleanup",
            version_ref="cleanup.customer@1.0.0",
            phase=WorkflowPhase.CLEANUP,
            input_ports=(_port("result", "AssertionResult"),),
            terminal=True,
        ),
    )
    edges = (
        WorkflowEdge(
            id="data-to-agent",
            source_node_id="prepare-data",
            source_port="customerId",
            target_node_id="filter-agent",
            target_port="customerId",
            semantic_type="CustomerId",
        ),
        WorkflowEdge(
            id="agent-to-assert",
            source_node_id="filter-agent",
            source_port="rows",
            target_node_id="relationship-assert",
            target_port="rows",
            semantic_type="CustomerRows",
        ),
        WorkflowEdge(
            id="assert-to-cleanup",
            source_node_id="relationship-assert",
            source_port="result",
            target_node_id="cleanup",
            target_port="result",
            semantic_type="AssertionResult",
        ),
    )
    return WorkflowGraph(nodes=nodes, edges=edges)


def build_intent_factory() -> Callable[..., TestIntent]:
    """Build the shared TestIntent factory."""

    def build(**updates: object) -> TestIntent:
        intent = TestIntent(
            summary="A customer operator filters visible relationship rows.",
            requirement_refs=(
                SourceRequirementRef(
                    document_id="requirements/customer-search",
                    document_version="2026-07-15",
                    content_digest=DIGEST_A,
                    anchor="customer-search/filter-visible-rows",
                    excerpt_digest=DIGEST_B,
                ),
            ),
            actors=(
                ActorContract(
                    actor_slot="operator",
                    role_id=UUID("11111111-1111-4111-8111-111111111111"),
                    role_key="customer.operator",
                    role_revision=3,
                    capabilities=("customer.read",),
                ),
            ),
            fixture=FixtureContract(
                blueprint_version_id=UUID("22222222-2222-4222-8222-222222222222"),
                blueprint_version_ref="fixture.customer@1.0.0",
                content_digest=DIGEST_A,
                required_exports={"customerId": "CustomerId"},
            ),
            surfaces=(
                SurfaceRef(
                    surface_key="customer.relationship-list",
                    version_ref="surface.customer-relationship@1.0.0",
                    content_digest=DIGEST_B,
                ),
            ),
        )
        return intent.model_copy(update=updates)

    return build
