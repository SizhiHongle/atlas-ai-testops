"""PostgreSQL repository for immutable CaseVersion snapshots."""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.case import (
    CaseVersion,
    DebugRun,
    PlanTemplate,
    PublishCaseVersion,
    TestCase,
    TestIR,
    WorkflowDraftSnapshot,
    case_version_ref,
)
from atlas_testops.domain.workflow import WorkflowEdge, WorkflowGraph, WorkflowNode

CASE_VERSION_COLUMNS = (
    "schema_version, id, tenant_id, project_id, test_case_id, version, "
    "version_ref, status, source_draft_id, semantic_revision, semantic_digest, "
    "intent_version_ref, intent_digest, intent, test_ir, test_ir_digest, "
    "plan_template, plan_digest, compiled_digest, content_digest, debug_run_id, "
    "evidence_manifest_id, evidence_manifest_digest, authored_by, published_by, "
    "review_summary, published_at, retired_at, retired_by, retirement_reason, "
    "revision, created_at, updated_at"
)


class CaseVersionRepository:
    """Persist and reconstruct exact published case snapshots."""

    async def create_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        case: TestCase,
        draft: WorkflowDraftSnapshot,
        run: DebugRun,
        command: PublishCaseVersion,
        test_ir: TestIR,
        plan_template: PlanTemplate,
        compiled_digest: str,
        content_digest: str,
        authored_by: UUID,
        published_by: UUID,
        published_at: datetime,
    ) -> CaseVersion | None:
        """Insert the version root and normalized graph in one caller transaction."""

        if run.evidence_manifest_id is None or run.evidence_manifest_digest is None:
            raise ValueError("published DebugRun evidence must be complete")
        cursor = await connection.execute(
            f"""
            insert into atlas.case_version (
              id, tenant_id, project_id, test_case_id, version, version_ref,
              source_draft_id, semantic_revision, semantic_digest,
              intent_version_ref, intent_digest, intent,
              test_ir, test_ir_digest, plan_template, plan_digest,
              compiled_digest, content_digest, debug_run_id,
              evidence_manifest_id, evidence_manifest_digest,
              authored_by, published_by, review_summary, published_at,
              created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            on conflict do nothing
            returning {CASE_VERSION_COLUMNS}
            """,
            (
                version_id,
                case.tenant_id,
                case.project_id,
                case.id,
                command.version,
                case_version_ref(case.id, command.version),
                draft.id,
                draft.semantic_revision,
                draft.semantic_digest,
                case.intent_version_ref,
                case.intent_digest,
                Jsonb(case.intent.model_dump(mode="json", by_alias=True)),
                Jsonb(test_ir.model_dump(mode="json", by_alias=True)),
                test_ir.content_digest,
                Jsonb(plan_template.model_dump(mode="json", by_alias=True)),
                plan_template.plan_digest,
                compiled_digest,
                content_digest,
                run.id,
                run.evidence_manifest_id,
                run.evidence_manifest_digest,
                authored_by,
                published_by,
                command.review_summary,
                published_at,
                published_at,
                published_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await self._insert_graph(
            connection,
            tenant_id=case.tenant_id,
            project_id=case.project_id,
            test_case_id=case.id,
            case_version_id=version_id,
            graph=draft.graph,
        )
        return self._snapshot(row, draft.graph)

    async def get_version(
        self,
        connection: AsyncConnection[DictRow],
        version_id: UUID,
    ) -> CaseVersion | None:
        cursor = await connection.execute(
            f"select {CASE_VERSION_COLUMNS} from atlas.case_version where id = %s",
            (version_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        graph = (await self._load_graphs(connection, (version_id,)))[version_id]
        return self._snapshot(row, graph)

    async def list_versions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        test_case_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[CaseVersion, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (test_case_id, limit + 1)
        else:
            cursor_filter = "and (published_at, id) < (%s, %s)"
            parameters = (
                test_case_id,
                cursor.created_at,
                cursor.id,
                limit + 1,
            )
        result = await connection.execute(
            f"""
            select {CASE_VERSION_COLUMNS}
            from atlas.case_version
            where test_case_id = %s {cursor_filter}
            order by published_at desc, id desc
            limit %s
            """,
            parameters,
        )
        rows = await result.fetchall()
        graphs = await self._load_graphs(
            connection,
            tuple(row["id"] for row in rows),
        )
        return tuple(self._snapshot(row, graphs[row["id"]]) for row in rows)

    async def _insert_graph(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        test_case_id: UUID,
        case_version_id: UUID,
        graph: WorkflowGraph,
    ) -> None:
        for node in graph.nodes:
            await connection.execute(
                """
                insert into atlas.case_version_node (
                  id, tenant_id, project_id, test_case_id, case_version_id,
                  node_key, kind, version_ref, phase,
                  input_ports, output_ports, params, terminal, oracle_strength
                ) values (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    new_entity_id(),
                    tenant_id,
                    project_id,
                    test_case_id,
                    case_version_id,
                    node.id,
                    node.kind,
                    node.version_ref,
                    node.phase,
                    Jsonb(
                        [
                            port.model_dump(mode="json", by_alias=True)
                            for port in node.input_ports
                        ]
                    ),
                    Jsonb(
                        [
                            port.model_dump(mode="json", by_alias=True)
                            for port in node.output_ports
                        ]
                    ),
                    Jsonb(node.params),
                    node.terminal,
                    node.oracle_strength,
                ),
            )
        for edge in graph.edges:
            await connection.execute(
                """
                insert into atlas.case_version_edge (
                  id, tenant_id, project_id, test_case_id, case_version_id,
                  edge_key, source_node_key, source_port,
                  target_node_key, target_port, semantic_type, kind, mapping
                ) values (
                  %s, %s, %s, %s, %s,
                  %s, %s, %s,
                  %s, %s, %s, %s, %s
                )
                """,
                (
                    new_entity_id(),
                    tenant_id,
                    project_id,
                    test_case_id,
                    case_version_id,
                    edge.id,
                    edge.source_node_id,
                    edge.source_port,
                    edge.target_node_id,
                    edge.target_port,
                    edge.semantic_type,
                    edge.kind,
                    edge.mapping,
                ),
            )

    async def _load_graphs(
        self,
        connection: AsyncConnection[DictRow],
        case_version_ids: tuple[UUID, ...],
    ) -> dict[UUID, WorkflowGraph]:
        if not case_version_ids:
            return {}
        node_cursor = await connection.execute(
            """
            select case_version_id, node_key, kind, version_ref, phase,
                   input_ports, output_ports, params, terminal, oracle_strength
            from atlas.case_version_node
            where case_version_id = any(%s)
            order by case_version_id, node_key
            """,
            (list(case_version_ids),),
        )
        edge_cursor = await connection.execute(
            """
            select case_version_id, edge_key, source_node_key, source_port,
                   target_node_key, target_port, semantic_type, kind, mapping
            from atlas.case_version_edge
            where case_version_id = any(%s)
            order by case_version_id, edge_key
            """,
            (list(case_version_ids),),
        )
        nodes: dict[UUID, list[WorkflowNode]] = {
            version_id: [] for version_id in case_version_ids
        }
        for row in await node_cursor.fetchall():
            nodes[row["case_version_id"]].append(
                WorkflowNode(
                    id=row["node_key"],
                    kind=row["kind"],
                    version_ref=row["version_ref"],
                    phase=row["phase"],
                    input_ports=tuple(row["input_ports"]),
                    output_ports=tuple(row["output_ports"]),
                    params=row["params"],
                    terminal=row["terminal"],
                    oracle_strength=row["oracle_strength"],
                )
            )
        edges: dict[UUID, list[WorkflowEdge]] = {
            version_id: [] for version_id in case_version_ids
        }
        for row in await edge_cursor.fetchall():
            edges[row["case_version_id"]].append(
                WorkflowEdge(
                    id=row["edge_key"],
                    source_node_id=row["source_node_key"],
                    source_port=row["source_port"],
                    target_node_id=row["target_node_key"],
                    target_port=row["target_port"],
                    semantic_type=row["semantic_type"],
                    kind=row["kind"],
                    mapping=row["mapping"],
                )
            )
        return {
            version_id: WorkflowGraph(
                nodes=tuple(nodes[version_id]),
                edges=tuple(edges[version_id]),
            )
            for version_id in case_version_ids
        }

    @staticmethod
    def _snapshot(row: DictRow, graph: WorkflowGraph) -> CaseVersion:
        return CaseVersion.model_validate({**row, "graph": graph})
