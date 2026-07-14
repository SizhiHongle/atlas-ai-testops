"""PostgreSQL repository for versioned fixture assets."""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.fixture import (
    AssetVersionStatus,
    BlueprintCompilationResult,
    CreateDataAtom,
    CreateDataAtomVersion,
    CreateDataBlueprint,
    CreateDataBlueprintVersion,
    DataAtomCatalogItem,
    DataAtomDefinition,
    DataAtomVersion,
    DataBlueprintCatalogItem,
    DataBlueprintDefinition,
    DataBlueprintVersion,
    UpdateDataAtom,
    UpdateDataAtomVersion,
    UpdateDataBlueprint,
    UpdateDataBlueprintVersion,
)

ATOM_DEFINITION_COLUMNS = (
    "id, tenant_id, project_id, atom_key, business_domain, name, description, "
    "status, revision, created_at, updated_at"
)
ATOM_VERSION_COLUMNS = (
    "id, tenant_id, project_id, atom_id, version, status, contract, content_digest, "
    "static_validation_state, runtime_validation_state, cleanup_validation_state, "
    "validated_at, published_at, published_by, revision, created_at, updated_at"
)
BLUEPRINT_DEFINITION_COLUMNS = (
    "id, tenant_id, project_id, blueprint_key, name, description, status, revision, "
    "created_at, updated_at"
)
BLUEPRINT_VERSION_COLUMNS = (
    "id, tenant_id, project_id, blueprint_id, version, status, contract, "
    "content_digest, static_validation_state, runtime_validation_state, "
    "cleanup_validation_state, validated_at, compiled_plan, plan_digest, compile_issues, "
    "compiled_at, published_at, published_by, revision, created_at, updated_at"
)


class FixtureAssetRepository:
    """Persist fixture assets without deciding authorization or publication policy."""

    async def create_atom_definition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        atom_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: CreateDataAtom,
    ) -> DataAtomDefinition | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.data_atom_definition (
              id, tenant_id, project_id, atom_key, business_domain, name, description
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {ATOM_DEFINITION_COLUMNS}
            """,
            (
                atom_id,
                tenant_id,
                project_id,
                command.atom_key,
                command.business_domain,
                command.name,
                command.description,
            ),
        )
        row = await cursor.fetchone()
        return DataAtomDefinition.model_validate(row) if row is not None else None

    async def get_atom_definition(
        self,
        connection: AsyncConnection[DictRow],
        atom_id: UUID,
    ) -> DataAtomDefinition | None:
        cursor = await connection.execute(
            f"select {ATOM_DEFINITION_COLUMNS} from atlas.data_atom_definition where id = %s",
            (atom_id,),
        )
        row = await cursor.fetchone()
        return DataAtomDefinition.model_validate(row) if row is not None else None

    async def list_atom_definitions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[DataAtomCatalogItem, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (project_id, limit + 1)
        else:
            cursor_filter = "and (definition.created_at, definition.id) < (%s, %s)"
            parameters = (project_id, cursor.created_at, cursor.id, limit + 1)
        result = await connection.execute(
            f"""
            select definition.{ATOM_DEFINITION_COLUMNS.replace(", ", ", definition.")},
                   latest.id as latest_version_id,
                   latest.version as latest_version,
                   latest.status as latest_version_status,
                   latest.contract ->> 'effect' as latest_effect,
                   coalesce((
                     select array_agg(port ->> 'key' order by port ->> 'key')
                     from jsonb_array_elements(latest.contract -> 'ports') as port
                     where port ->> 'direction' = 'INPUT'
                   ), '{{}}'::text[]) as input_ports,
                   coalesce((
                     select array_agg(port ->> 'key' order by port ->> 'key')
                     from jsonb_array_elements(latest.contract -> 'ports') as port
                     where port ->> 'direction' = 'OUTPUT'
                   ), '{{}}'::text[]) as output_ports,
                   coalesce(latest.contract ? 'cleanupContract', false) as cleanup_capable
            from atlas.data_atom_definition as definition
            left join lateral (
              select id, version, status, contract
              from atlas.data_atom_version
              where atom_id = definition.id
              order by case status
                when 'PUBLISHED' then 4
                when 'VALIDATED' then 3
                when 'DRAFT' then 2
                else 1
              end desc, created_at desc, id desc
              limit 1
            ) as latest on true
            where definition.project_id = %s {cursor_filter}
            order by definition.created_at desc, definition.id desc
            limit %s
            """,
            parameters,
        )
        return tuple(DataAtomCatalogItem.model_validate(row) for row in await result.fetchall())

    async def update_atom_definition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        atom_id: UUID,
        expected_revision: int,
        command: UpdateDataAtom,
    ) -> DataAtomDefinition | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_atom_definition
            set name = coalesce(%s, name),
                description = coalesce(%s, description),
                status = coalesce(%s, status),
                revision = revision + 1
            where id = %s and revision = %s
            returning {ATOM_DEFINITION_COLUMNS}
            """,
            (
                command.name,
                command.description,
                command.status,
                atom_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return DataAtomDefinition.model_validate(row) if row is not None else None

    async def create_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        definition: DataAtomDefinition,
        command: CreateDataAtomVersion,
        content_digest: str,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.data_atom_version (
              id, tenant_id, project_id, atom_id, version, contract, content_digest
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {ATOM_VERSION_COLUMNS}
            """,
            (
                version_id,
                definition.tenant_id,
                definition.project_id,
                definition.id,
                command.version,
                Jsonb(command.contract.model_dump(mode="json", by_alias=True)),
                content_digest,
            ),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def get_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        version_id: UUID,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            f"select {ATOM_VERSION_COLUMNS} from atlas.data_atom_version where id = %s",
            (version_id,),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def get_atom_versions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        version_ids: tuple[UUID, ...],
    ) -> dict[UUID, DataAtomVersion]:
        if not version_ids:
            return {}
        result = await connection.execute(
            f"""
            select {ATOM_VERSION_COLUMNS}
            from atlas.data_atom_version
            where project_id = %s and id = any(%s)
            """,
            (project_id, list(version_ids)),
        )
        versions = tuple(DataAtomVersion.model_validate(row) for row in await result.fetchall())
        return {item.id: item for item in versions}

    async def list_atom_versions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        atom_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[DataAtomVersion, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (atom_id, limit + 1)
        else:
            cursor_filter = "and (created_at, id) < (%s, %s)"
            parameters = (atom_id, cursor.created_at, cursor.id, limit + 1)
        result = await connection.execute(
            f"""
            select {ATOM_VERSION_COLUMNS}
            from atlas.data_atom_version
            where atom_id = %s {cursor_filter}
            order by created_at desc, id desc
            limit %s
            """,
            parameters,
        )
        return tuple(DataAtomVersion.model_validate(row) for row in await result.fetchall())

    async def update_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        command: UpdateDataAtomVersion,
        content_digest: str,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_atom_version
            set status = 'DRAFT', contract = %s, content_digest = %s,
                static_validation_state = 'PENDING',
                runtime_validation_state = 'PENDING',
                cleanup_validation_state = 'PENDING',
                runtime_validation_evidence_id = null,
                runtime_validated_at = null,
                cleanup_validation_evidence_id = null,
                cleanup_validated_at = null,
                validated_at = null, published_at = null, published_by = null,
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('DRAFT', 'VALIDATED')
            returning {ATOM_VERSION_COLUMNS}
            """,
            (
                Jsonb(command.contract.model_dump(mode="json", by_alias=True)),
                content_digest,
                version_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def validate_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        validated_at: datetime,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_atom_version
            set status = 'VALIDATED', static_validation_state = 'PASSED',
                runtime_validation_state = 'PENDING',
                cleanup_validation_state = 'PENDING',
                runtime_validation_evidence_id = null,
                runtime_validated_at = null,
                cleanup_validation_evidence_id = null,
                cleanup_validated_at = null,
                validated_at = %s, revision = revision + 1
            where id = %s and revision = %s
              and status in ('DRAFT', 'VALIDATED')
            returning {ATOM_VERSION_COLUMNS}
            """,
            (validated_at, version_id, expected_revision),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def publish_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        published_at: datetime,
        published_by: UUID,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_atom_version
            set status = 'PUBLISHED', published_at = %s, published_by = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'VALIDATED'
              and static_validation_state = 'PASSED'
              and runtime_validation_state = 'PASSED'
              and cleanup_validation_state = 'PASSED'
            returning {ATOM_VERSION_COLUMNS}
            """,
            (published_at, published_by, version_id, expected_revision),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def create_blueprint_definition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        blueprint_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: CreateDataBlueprint,
    ) -> DataBlueprintDefinition | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.data_blueprint_definition (
              id, tenant_id, project_id, blueprint_key, name, description
            ) values (%s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {BLUEPRINT_DEFINITION_COLUMNS}
            """,
            (
                blueprint_id,
                tenant_id,
                project_id,
                command.blueprint_key,
                command.name,
                command.description,
            ),
        )
        row = await cursor.fetchone()
        return DataBlueprintDefinition.model_validate(row) if row is not None else None

    async def get_blueprint_definition(
        self,
        connection: AsyncConnection[DictRow],
        blueprint_id: UUID,
    ) -> DataBlueprintDefinition | None:
        cursor = await connection.execute(
            f"select {BLUEPRINT_DEFINITION_COLUMNS} "
            "from atlas.data_blueprint_definition where id = %s",
            (blueprint_id,),
        )
        row = await cursor.fetchone()
        return DataBlueprintDefinition.model_validate(row) if row is not None else None

    async def list_blueprint_definitions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[DataBlueprintCatalogItem, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (project_id, limit + 1)
        else:
            cursor_filter = "and (definition.created_at, definition.id) < (%s, %s)"
            parameters = (project_id, cursor.created_at, cursor.id, limit + 1)
        result = await connection.execute(
            f"""
            select definition.{BLUEPRINT_DEFINITION_COLUMNS.replace(", ", ", definition.")},
                   latest.id as latest_version_id,
                   latest.version as latest_version,
                   latest.status as latest_version_status,
                   coalesce(jsonb_array_length(latest.contract -> 'nodes'), 0) as node_count,
                   coalesce(jsonb_array_length(latest.contract -> 'exports'), 0) as export_count,
                   latest.plan_digest
            from atlas.data_blueprint_definition as definition
            left join lateral (
              select id, version, status, contract, plan_digest
              from atlas.data_blueprint_version
              where blueprint_id = definition.id
              order by case status
                when 'PUBLISHED' then 4
                when 'VALIDATED' then 3
                when 'DRAFT' then 2
                else 1
              end desc, created_at desc, id desc
              limit 1
            ) as latest on true
            where definition.project_id = %s {cursor_filter}
            order by definition.created_at desc, definition.id desc
            limit %s
            """,
            parameters,
        )
        return tuple(
            DataBlueprintCatalogItem.model_validate(row) for row in await result.fetchall()
        )

    async def update_blueprint_definition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        blueprint_id: UUID,
        expected_revision: int,
        command: UpdateDataBlueprint,
    ) -> DataBlueprintDefinition | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_blueprint_definition
            set name = coalesce(%s, name),
                description = coalesce(%s, description),
                status = coalesce(%s, status),
                revision = revision + 1
            where id = %s and revision = %s
            returning {BLUEPRINT_DEFINITION_COLUMNS}
            """,
            (
                command.name,
                command.description,
                command.status,
                blueprint_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return DataBlueprintDefinition.model_validate(row) if row is not None else None

    async def create_blueprint_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        definition: DataBlueprintDefinition,
        command: CreateDataBlueprintVersion,
        content_digest: str,
    ) -> DataBlueprintVersion | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.data_blueprint_version (
              id, tenant_id, project_id, blueprint_id, version, contract, content_digest
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {BLUEPRINT_VERSION_COLUMNS}
            """,
            (
                version_id,
                definition.tenant_id,
                definition.project_id,
                definition.id,
                command.version,
                Jsonb(command.contract.model_dump(mode="json", by_alias=True)),
                content_digest,
            ),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None

    async def get_blueprint_version(
        self,
        connection: AsyncConnection[DictRow],
        version_id: UUID,
    ) -> DataBlueprintVersion | None:
        cursor = await connection.execute(
            f"select {BLUEPRINT_VERSION_COLUMNS} from atlas.data_blueprint_version where id = %s",
            (version_id,),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None

    async def list_blueprint_versions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        blueprint_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[DataBlueprintVersion, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (blueprint_id, limit + 1)
        else:
            cursor_filter = "and (created_at, id) < (%s, %s)"
            parameters = (blueprint_id, cursor.created_at, cursor.id, limit + 1)
        result = await connection.execute(
            f"""
            select {BLUEPRINT_VERSION_COLUMNS}
            from atlas.data_blueprint_version
            where blueprint_id = %s {cursor_filter}
            order by created_at desc, id desc
            limit %s
            """,
            parameters,
        )
        return tuple(DataBlueprintVersion.model_validate(row) for row in await result.fetchall())

    async def update_blueprint_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        command: UpdateDataBlueprintVersion,
        content_digest: str,
    ) -> DataBlueprintVersion | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_blueprint_version
            set status = 'DRAFT', contract = %s, content_digest = %s,
                static_validation_state = 'PENDING',
                runtime_validation_state = 'PENDING',
                cleanup_validation_state = 'PENDING',
                runtime_validation_evidence_id = null,
                runtime_validated_at = null,
                cleanup_validation_evidence_id = null,
                cleanup_validated_at = null,
                validated_at = null,
                compiled_plan = null, plan_digest = null,
                compile_issues = '[]'::jsonb, compiled_at = null,
                published_at = null, published_by = null,
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('DRAFT', 'VALIDATED')
            returning {BLUEPRINT_VERSION_COLUMNS}
            """,
            (
                Jsonb(command.contract.model_dump(mode="json", by_alias=True)),
                content_digest,
                version_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None

    async def save_blueprint_compilation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        compilation: BlueprintCompilationResult,
        compiled_at: datetime,
    ) -> DataBlueprintVersion | None:
        plan = compilation.plan
        plan_payload = (
            Jsonb(plan.model_dump(mode="json", by_alias=True)) if plan is not None else None
        )
        issues_payload = Jsonb(
            [item.model_dump(mode="json", by_alias=True) for item in compilation.issues]
        )
        cursor = await connection.execute(
            f"""
            update atlas.data_blueprint_version
            set status = %s, static_validation_state = %s,
                runtime_validation_state = 'PENDING',
                cleanup_validation_state = 'PENDING',
                runtime_validation_evidence_id = null,
                runtime_validated_at = null,
                cleanup_validation_evidence_id = null,
                cleanup_validated_at = null,
                validated_at = %s,
                compiled_plan = %s, plan_digest = %s, compile_issues = %s,
                compiled_at = %s, published_at = null, published_by = null,
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('DRAFT', 'VALIDATED')
            returning {BLUEPRINT_VERSION_COLUMNS}
            """,
            (
                AssetVersionStatus.VALIDATED if compilation.valid else AssetVersionStatus.DRAFT,
                "PASSED" if compilation.valid else "FAILED",
                compiled_at if compilation.valid else None,
                plan_payload,
                plan.plan_digest if plan is not None else None,
                issues_payload,
                compiled_at,
                version_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None

    async def publish_blueprint_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        version_id: UUID,
        expected_revision: int,
        published_at: datetime,
        published_by: UUID,
    ) -> DataBlueprintVersion | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_blueprint_version
            set status = 'PUBLISHED', published_at = %s, published_by = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'VALIDATED'
              and static_validation_state = 'PASSED'
              and runtime_validation_state = 'PASSED'
              and cleanup_validation_state = 'PASSED'
              and compiled_plan is not null and plan_digest is not null
            returning {BLUEPRINT_VERSION_COLUMNS}
            """,
            (published_at, published_by, version_id, expected_revision),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None
