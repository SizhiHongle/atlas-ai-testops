"""PostgreSQL repository for TestCase and WorkflowDraft authoring state."""

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.case import (
    CreateTestCase,
    TestCase,
    TestCaseCatalogItem,
    WorkflowDraftSnapshot,
)
from atlas_testops.domain.workflow import (
    DraftAuthor,
    GraphValidationResult,
    NodeLayout,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)

CASE_COLUMNS = (
    "id, tenant_id, project_id, case_key, name, status, intent_version, "
    "intent_version_ref, intent, intent_digest, revision, created_at, updated_at"
)
DRAFT_COLUMNS = (
    "id, tenant_id, project_id, test_case_id, semantic_revision, layout_revision, "
    "intent_version_ref, layout, updated_by, semantic_digest, graph_valid, "
    "validation_issues, execution_levels, matched_required_inputs, "
    "total_required_inputs, created_at, updated_at"
)


class CaseRepository:
    """Persist authoring facts without making authorization decisions."""

    async def create_case(
        self,
        connection: AsyncConnection[DictRow],
        *,
        case_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        intent_version_ref: str,
        intent_digest: str,
        command: CreateTestCase,
    ) -> TestCase | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.test_case (
              id, tenant_id, project_id, case_key, name,
              intent_version, intent_version_ref, intent, intent_digest
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {CASE_COLUMNS}
            """,
            (
                case_id,
                tenant_id,
                project_id,
                command.case_key,
                command.name,
                command.intent_version,
                intent_version_ref,
                Jsonb(command.intent.model_dump(mode="json", by_alias=True)),
                intent_digest,
            ),
        )
        row = await cursor.fetchone()
        return TestCase.model_validate(row) if row is not None else None

    async def get_case(
        self,
        connection: AsyncConnection[DictRow],
        case_id: UUID,
        *,
        for_share: bool = False,
    ) -> TestCase | None:
        lock_clause = "for share" if for_share else ""
        cursor = await connection.execute(
            f"select {CASE_COLUMNS} from atlas.test_case where id = %s {lock_clause}",
            (case_id,),
        )
        row = await cursor.fetchone()
        return TestCase.model_validate(row) if row is not None else None

    async def list_cases(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TestCaseCatalogItem, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (project_id, limit + 1)
        else:
            cursor_filter = "and (item.created_at, item.id) < (%s, %s)"
            parameters = (project_id, cursor.created_at, cursor.id, limit + 1)
        result = await connection.execute(
            f"""
            select item.{CASE_COLUMNS.replace(", ", ", item.")},
                   draft.id as draft_id,
                   draft.semantic_revision,
                   draft.layout_revision,
                   draft.graph_valid,
                   draft.updated_by
            from atlas.test_case as item
            join atlas.workflow_draft as draft on draft.test_case_id = item.id
            where item.project_id = %s {cursor_filter}
            order by item.created_at desc, item.id desc
            limit %s
            """,
            parameters,
        )
        return tuple(TestCaseCatalogItem.model_validate(row) for row in await result.fetchall())

    async def create_draft(
        self,
        connection: AsyncConnection[DictRow],
        *,
        draft_id: UUID,
        case: TestCase,
        graph: WorkflowGraph,
        layout: dict[str, NodeLayout],
        updated_by: DraftAuthor,
        semantic_digest: str,
        validation: GraphValidationResult,
    ) -> WorkflowDraftSnapshot:
        cursor = await connection.execute(
            f"""
            insert into atlas.workflow_draft (
              id, tenant_id, project_id, test_case_id, intent_version_ref,
              layout, updated_by, semantic_digest, graph_valid,
              validation_issues, execution_levels,
              matched_required_inputs, total_required_inputs
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning {DRAFT_COLUMNS}
            """,
            (
                draft_id,
                case.tenant_id,
                case.project_id,
                case.id,
                case.intent_version_ref,
                Jsonb(
                    {
                        key: value.model_dump(mode="json", by_alias=True)
                        for key, value in sorted(layout.items())
                    }
                ),
                updated_by,
                semantic_digest,
                validation.valid,
                Jsonb(
                    [
                        issue.model_dump(mode="json", by_alias=True)
                        for issue in validation.issues
                    ]
                ),
                Jsonb([list(level) for level in validation.execution_levels]),
                validation.matched_required_inputs,
                validation.total_required_inputs,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("workflow draft insert did not return a row")
        await self._insert_graph(
            connection,
            tenant_id=case.tenant_id,
            project_id=case.project_id,
            draft_id=draft_id,
            graph=graph,
        )
        return self._snapshot(row, graph)

    async def get_draft_by_case(
        self,
        connection: AsyncConnection[DictRow],
        case_id: UUID,
        *,
        for_update: bool = False,
    ) -> WorkflowDraftSnapshot | None:
        lock_clause = "for update" if for_update else "for share"
        cursor = await connection.execute(
            f"""
            select {DRAFT_COLUMNS}
            from atlas.workflow_draft
            where test_case_id = %s
            {lock_clause}
            """,
            (case_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        graph = await self._load_graph(connection, row["id"])
        return self._snapshot(row, graph)

    async def get_semantic_author(
        self,
        connection: AsyncConnection[DictRow],
        *,
        draft_id: UUID,
        test_case_id: UUID,
    ) -> UUID | None:
        """Resolve the actor behind the current semantics, excluding layout edits."""

        cursor = await connection.execute(
            """
            select coalesce(
              (
                select actor_id
                from atlas.draft_operation
                where draft_id = %s
                  and operation_scope = 'SEMANTIC'
                order by result_revision desc, created_at desc, id desc
                limit 1
              ),
              (
                select actor_id
                from atlas.audit_event
                where entity_type = 'test_case'
                  and entity_id = %s
                  and event_type = 'test_case.created'
                order by occurred_at desc, id desc
                limit 1
              )
            ) as actor_id
            """,
            (draft_id, test_case_id),
        )
        row = await cursor.fetchone()
        return row["actor_id"] if row is not None else None

    async def replace_graph(
        self,
        connection: AsyncConnection[DictRow],
        *,
        draft: WorkflowDraftSnapshot,
        expected_revision: int,
        graph: WorkflowGraph,
        updated_by: DraftAuthor,
        semantic_digest: str,
        validation: GraphValidationResult,
    ) -> WorkflowDraftSnapshot | None:
        cursor = await connection.execute(
            f"""
            update atlas.workflow_draft
            set semantic_revision = semantic_revision + 1,
                updated_by = %s,
                semantic_digest = %s,
                graph_valid = %s,
                validation_issues = %s,
                execution_levels = %s,
                matched_required_inputs = %s,
                total_required_inputs = %s
            where id = %s and semantic_revision = %s
            returning {DRAFT_COLUMNS}
            """,
            (
                updated_by,
                semantic_digest,
                validation.valid,
                Jsonb(
                    [
                        issue.model_dump(mode="json", by_alias=True)
                        for issue in validation.issues
                    ]
                ),
                Jsonb([list(level) for level in validation.execution_levels]),
                validation.matched_required_inputs,
                validation.total_required_inputs,
                draft.id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await connection.execute(
            "delete from atlas.workflow_edge where draft_id = %s",
            (draft.id,),
        )
        await connection.execute(
            "delete from atlas.workflow_node where draft_id = %s",
            (draft.id,),
        )
        await self._insert_graph(
            connection,
            tenant_id=draft.tenant_id,
            project_id=draft.project_id,
            draft_id=draft.id,
            graph=graph,
        )
        return self._snapshot(row, graph)

    async def update_layout(
        self,
        connection: AsyncConnection[DictRow],
        *,
        draft: WorkflowDraftSnapshot,
        expected_revision: int,
        layout: dict[str, NodeLayout],
        updated_by: DraftAuthor,
    ) -> WorkflowDraftSnapshot | None:
        cursor = await connection.execute(
            f"""
            update atlas.workflow_draft
            set layout_revision = layout_revision + 1,
                layout = %s,
                updated_by = %s
            where id = %s and layout_revision = %s
            returning {DRAFT_COLUMNS}
            """,
            (
                Jsonb(
                    {
                        key: value.model_dump(mode="json", by_alias=True)
                        for key, value in sorted(layout.items())
                    }
                ),
                updated_by,
                draft.id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return self._snapshot(row, draft.graph) if row is not None else None

    async def append_operation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        draft: WorkflowDraftSnapshot,
        operation_scope: str,
        patch_id: UUID | None,
        client_mutation_id: str,
        source: DraftAuthor,
        actor_id: UUID | None,
        base_revision: int,
        result_revision: int,
        request_digest: str,
        before_digest: str,
        after_digest: str,
        operations: JsonValue,
        response: JsonValue,
        rationale_summary: str | None,
    ) -> None:
        await connection.execute(
            """
            insert into atlas.draft_operation (
              id, tenant_id, project_id, draft_id, test_case_id, patch_id,
              client_mutation_id, operation_scope, source, actor_id,
              base_revision, result_revision, request_digest,
              before_digest, after_digest, operations, response, rationale_summary
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                new_entity_id(),
                draft.tenant_id,
                draft.project_id,
                draft.id,
                draft.test_case_id,
                patch_id,
                client_mutation_id,
                operation_scope,
                source,
                actor_id,
                base_revision,
                result_revision,
                request_digest,
                before_digest,
                after_digest,
                Jsonb(operations),
                Jsonb(response),
                rationale_summary,
            ),
        )

    async def _insert_graph(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        draft_id: UUID,
        graph: WorkflowGraph,
    ) -> None:
        for node in graph.nodes:
            await connection.execute(
                """
                insert into atlas.workflow_node (
                  id, tenant_id, project_id, draft_id, node_key, kind,
                  version_ref, phase, input_ports, output_ports, params,
                  terminal, oracle_strength
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_entity_id(),
                    tenant_id,
                    project_id,
                    draft_id,
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
                insert into atlas.workflow_edge (
                  id, tenant_id, project_id, draft_id, edge_key,
                  source_node_key, source_port, target_node_key, target_port,
                  semantic_type, kind, mapping
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_entity_id(),
                    tenant_id,
                    project_id,
                    draft_id,
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

    async def _load_graph(
        self,
        connection: AsyncConnection[DictRow],
        draft_id: UUID,
    ) -> WorkflowGraph:
        node_cursor = await connection.execute(
            """
            select node_key, kind, version_ref, phase, input_ports, output_ports,
                   params, terminal, oracle_strength
            from atlas.workflow_node
            where draft_id = %s
            order by node_key
            """,
            (draft_id,),
        )
        edge_cursor = await connection.execute(
            """
            select edge_key, source_node_key, source_port,
                   target_node_key, target_port, semantic_type, kind, mapping
            from atlas.workflow_edge
            where draft_id = %s
            order by edge_key
            """,
            (draft_id,),
        )
        nodes = tuple(
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
            for row in await node_cursor.fetchall()
        )
        edges = tuple(
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
            for row in await edge_cursor.fetchall()
        )
        return WorkflowGraph(nodes=nodes, edges=edges)

    @staticmethod
    def _snapshot(row: DictRow, graph: WorkflowGraph) -> WorkflowDraftSnapshot:
        validation = GraphValidationResult(
            valid=row["graph_valid"],
            issues=tuple(row["validation_issues"]),
            execution_levels=tuple(tuple(level) for level in row["execution_levels"]),
            matched_required_inputs=row["matched_required_inputs"],
            total_required_inputs=row["total_required_inputs"],
        )
        return WorkflowDraftSnapshot(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            test_case_id=row["test_case_id"],
            semantic_revision=row["semantic_revision"],
            layout_revision=row["layout_revision"],
            graph=graph,
            layout={
                key: NodeLayout.model_validate(value)
                for key, value in row["layout"].items()
            },
            intent_version_ref=row["intent_version_ref"],
            updated_by=row["updated_by"],
            semantic_digest=row["semantic_digest"],
            validation=validation,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
