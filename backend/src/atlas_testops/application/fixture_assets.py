"""Application service for versioned fixture asset control-plane operations."""

from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    AssetDefinitionStatus,
    AssetVersionStatus,
    CompileBlueprintResponse,
    CreateDataAtom,
    CreateDataAtomVersion,
    CreateDataBlueprint,
    CreateDataBlueprintVersion,
    DataAtomDefinition,
    DataAtomPage,
    DataAtomVersion,
    DataAtomVersionPage,
    DataBlueprintDefinition,
    DataBlueprintPage,
    DataBlueprintVersion,
    DataBlueprintVersionPage,
    UpdateDataAtom,
    UpdateDataAtomVersion,
    UpdateDataBlueprint,
    UpdateDataBlueprintVersion,
    ValidationState,
    canonical_digest,
    compile_blueprint,
)
from atlas_testops.domain.platform import Project, ProjectStatus
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.fixture_assets import FixtureAssetRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

FIXTURE_IDEMPOTENCY_TTL = timedelta(hours=24)


class CursorRecord(Protocol):
    """Minimum projection required to create a stable time cursor."""

    @property
    def id(self) -> UUID: ...

    @property
    def created_at(self) -> datetime: ...


class FixtureAssetService:
    """Coordinate fixture asset authorization, lifecycle gates, and durable facts."""

    def __init__(
        self,
        database: Database,
        fixture_repository: FixtureAssetRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._fixtures = fixture_repository or FixtureAssetRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def create_atom_definition(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateDataAtom,
        *,
        idempotency_key: str,
    ) -> CommandResult[DataAtomDefinition]:
        now = utc_now()
        scope = f"projects.{project_id}.data-atoms.create"
        request_hash = hash_request(
            {
                "projectId": str(project_id),
                **command.model_dump(mode="json", by_alias=True),
            }
        )
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            self._require_project(actor, project, maintain=True, active=True)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=FIXTURE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=DataAtomDefinition.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            atom = await self._fixtures.create_atom_definition(
                connection,
                atom_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=project_id,
                command=command,
            )
            if atom is None:
                raise self._conflict(
                    "DataAtom Key 已存在",
                    "同一 Project 内的 atomKey 必须唯一。",
                )
            await self._record_atom_definition_event(
                connection,
                actor=actor,
                atom=atom,
                event_type="data_atom.created",
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=atom,
            )
            return CommandResult(value=atom, status_code=201, replayed=False)

    async def list_atom_definitions(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> DataAtomPage:
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            self._require_project(actor, project)
            records = await self._fixtures.list_atom_definitions(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        next_cursor = self._next_cursor(records, limit)
        return DataAtomPage(items=items, next_cursor=next_cursor)

    async def get_atom_definition(
        self,
        actor: ActorContext,
        atom_id: UUID,
    ) -> DataAtomDefinition:
        async with self._database.transaction(actor.database_context()) as connection:
            atom = await self._fixtures.get_atom_definition(connection, atom_id)
            self._require_atom(actor, atom)
            assert atom is not None
            return atom

    async def update_atom_definition(
        self,
        actor: ActorContext,
        atom_id: UUID,
        command: UpdateDataAtom,
        *,
        expected_revision: int,
    ) -> DataAtomDefinition:
        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_atom_definition(connection, atom_id)
            self._require_atom(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            if current.status is AssetDefinitionStatus.ARCHIVED:
                raise self._asset_immutable("已归档的 DataAtom 定义不可修改。")
            updated = await self._fixtures.update_atom_definition(
                connection,
                atom_id=atom_id,
                expected_revision=expected_revision,
                command=command,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_atom_definition_event(
                connection,
                actor=actor,
                atom=updated,
                event_type="data_atom.updated",
                occurred_at=now,
            )
            return updated

    async def create_atom_version(
        self,
        actor: ActorContext,
        atom_id: UUID,
        command: CreateDataAtomVersion,
        *,
        idempotency_key: str,
    ) -> CommandResult[DataAtomVersion]:
        now = utc_now()
        scope = f"data-atoms.{atom_id}.versions.create"
        request_hash = hash_request(
            {
                "atomId": str(atom_id),
                **command.model_dump(mode="json", by_alias=True),
            }
        )
        async with self._database.transaction(actor.database_context()) as connection:
            atom = await self._fixtures.get_atom_definition(connection, atom_id)
            self._require_atom(actor, atom, maintain=True)
            assert atom is not None
            if atom.status is AssetDefinitionStatus.ARCHIVED:
                raise self._asset_immutable("已归档的 DataAtom 不能创建新版本。")
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=FIXTURE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=DataAtomVersion.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            version = await self._fixtures.create_atom_version(
                connection,
                version_id=new_entity_id(),
                definition=atom,
                command=command,
                content_digest=canonical_digest(command.contract),
            )
            if version is None:
                raise self._conflict(
                    "DataAtom 版本已存在",
                    "同一 DataAtom 内的 version 必须唯一。",
                )
            await self._record_atom_version_event(
                connection,
                actor=actor,
                version=version,
                event_type="data_atom_version.created",
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=version,
            )
            return CommandResult(value=version, status_code=201, replayed=False)

    async def list_atom_versions(
        self,
        actor: ActorContext,
        atom_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> DataAtomVersionPage:
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            atom = await self._fixtures.get_atom_definition(connection, atom_id)
            self._require_atom(actor, atom)
            records = await self._fixtures.list_atom_versions(
                connection,
                atom_id=atom_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return DataAtomVersionPage(
            items=items,
            next_cursor=self._next_cursor(records, limit),
        )

    async def get_atom_version(
        self,
        actor: ActorContext,
        version_id: UUID,
    ) -> DataAtomVersion:
        async with self._database.transaction(actor.database_context()) as connection:
            version = await self._fixtures.get_atom_version(connection, version_id)
            self._require_atom_version(actor, version)
            assert version is not None
            return version

    async def update_atom_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        command: UpdateDataAtomVersion,
        *,
        expected_revision: int,
    ) -> DataAtomVersion:
        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_atom_version(connection, version_id)
            self._require_atom_version(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            self._require_mutable_version(current.status, "DataAtom")
            updated = await self._fixtures.update_atom_version(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                command=command,
                content_digest=canonical_digest(command.contract),
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_atom_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_atom_version.updated",
                occurred_at=now,
            )
            return updated

    async def validate_atom_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        *,
        expected_revision: int,
    ) -> DataAtomVersion:
        validated_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_atom_version(connection, version_id)
            self._require_atom_version(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            self._require_mutable_version(current.status, "DataAtom")
            if canonical_digest(current.contract) != current.content_digest:
                raise ApplicationError(
                    error_code=ErrorCode.VALIDATION_FAILED,
                    title="DataAtom 摘要校验失败",
                    detail="契约内容与 contentDigest 不一致。",
                    status_code=409,
                )
            updated = await self._fixtures.validate_atom_version(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                validated_at=validated_at,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_atom_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_atom_version.validated",
                occurred_at=validated_at,
            )
            return updated

    async def publish_atom_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        *,
        expected_revision: int,
    ) -> DataAtomVersion:
        published_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_atom_version(connection, version_id)
            self._require_atom_version(actor, current, publish=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            published_by = self._require_publisher(actor)
            self._require_publication_evidence(current)
            updated = await self._fixtures.publish_atom_version(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                published_at=published_at,
                published_by=published_by,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_atom_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_atom_version.published",
                occurred_at=published_at,
            )
            return updated

    async def create_blueprint_definition(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateDataBlueprint,
        *,
        idempotency_key: str,
    ) -> CommandResult[DataBlueprintDefinition]:
        now = utc_now()
        scope = f"projects.{project_id}.data-blueprints.create"
        request_hash = hash_request(
            {
                "projectId": str(project_id),
                **command.model_dump(mode="json", by_alias=True),
            }
        )
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            self._require_project(actor, project, maintain=True, active=True)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=FIXTURE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=DataBlueprintDefinition.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            blueprint = await self._fixtures.create_blueprint_definition(
                connection,
                blueprint_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=project_id,
                command=command,
            )
            if blueprint is None:
                raise self._conflict(
                    "DataBlueprint Key 已存在",
                    "同一 Project 内的 blueprintKey 必须唯一。",
                )
            await self._record_blueprint_definition_event(
                connection,
                actor=actor,
                blueprint=blueprint,
                event_type="data_blueprint.created",
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=blueprint,
            )
            return CommandResult(value=blueprint, status_code=201, replayed=False)

    async def list_blueprint_definitions(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> DataBlueprintPage:
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            self._require_project(actor, project)
            records = await self._fixtures.list_blueprint_definitions(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return DataBlueprintPage(
            items=items,
            next_cursor=self._next_cursor(records, limit),
        )

    async def get_blueprint_definition(
        self,
        actor: ActorContext,
        blueprint_id: UUID,
    ) -> DataBlueprintDefinition:
        async with self._database.transaction(actor.database_context()) as connection:
            blueprint = await self._fixtures.get_blueprint_definition(
                connection,
                blueprint_id,
            )
            self._require_blueprint(actor, blueprint)
            assert blueprint is not None
            return blueprint

    async def update_blueprint_definition(
        self,
        actor: ActorContext,
        blueprint_id: UUID,
        command: UpdateDataBlueprint,
        *,
        expected_revision: int,
    ) -> DataBlueprintDefinition:
        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_blueprint_definition(
                connection,
                blueprint_id,
            )
            self._require_blueprint(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            if current.status is AssetDefinitionStatus.ARCHIVED:
                raise self._asset_immutable("已归档的 DataBlueprint 定义不可修改。")
            updated = await self._fixtures.update_blueprint_definition(
                connection,
                blueprint_id=blueprint_id,
                expected_revision=expected_revision,
                command=command,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_blueprint_definition_event(
                connection,
                actor=actor,
                blueprint=updated,
                event_type="data_blueprint.updated",
                occurred_at=now,
            )
            return updated

    async def create_blueprint_version(
        self,
        actor: ActorContext,
        blueprint_id: UUID,
        command: CreateDataBlueprintVersion,
        *,
        idempotency_key: str,
    ) -> CommandResult[DataBlueprintVersion]:
        now = utc_now()
        scope = f"data-blueprints.{blueprint_id}.versions.create"
        request_hash = hash_request(
            {
                "blueprintId": str(blueprint_id),
                **command.model_dump(mode="json", by_alias=True),
            }
        )
        async with self._database.transaction(actor.database_context()) as connection:
            blueprint = await self._fixtures.get_blueprint_definition(
                connection,
                blueprint_id,
            )
            self._require_blueprint(actor, blueprint, maintain=True)
            assert blueprint is not None
            if blueprint.status is AssetDefinitionStatus.ARCHIVED:
                raise self._asset_immutable("已归档的 DataBlueprint 不能创建新版本。")
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=FIXTURE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=DataBlueprintVersion.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            version = await self._fixtures.create_blueprint_version(
                connection,
                version_id=new_entity_id(),
                definition=blueprint,
                command=command,
                content_digest=canonical_digest(command.contract),
            )
            if version is None:
                raise self._conflict(
                    "DataBlueprint 版本已存在",
                    "同一 DataBlueprint 内的 version 必须唯一。",
                )
            await self._record_blueprint_version_event(
                connection,
                actor=actor,
                version=version,
                event_type="data_blueprint_version.created",
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=version,
            )
            return CommandResult(value=version, status_code=201, replayed=False)

    async def list_blueprint_versions(
        self,
        actor: ActorContext,
        blueprint_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> DataBlueprintVersionPage:
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            blueprint = await self._fixtures.get_blueprint_definition(
                connection,
                blueprint_id,
            )
            self._require_blueprint(actor, blueprint)
            records = await self._fixtures.list_blueprint_versions(
                connection,
                blueprint_id=blueprint_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return DataBlueprintVersionPage(
            items=items,
            next_cursor=self._next_cursor(records, limit),
        )

    async def get_blueprint_version(
        self,
        actor: ActorContext,
        version_id: UUID,
    ) -> DataBlueprintVersion:
        async with self._database.transaction(actor.database_context()) as connection:
            version = await self._fixtures.get_blueprint_version(connection, version_id)
            self._require_blueprint_version(actor, version)
            assert version is not None
            return version

    async def update_blueprint_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        command: UpdateDataBlueprintVersion,
        *,
        expected_revision: int,
    ) -> DataBlueprintVersion:
        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_blueprint_version(connection, version_id)
            self._require_blueprint_version(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            self._require_mutable_version(current.status, "DataBlueprint")
            updated = await self._fixtures.update_blueprint_version(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                command=command,
                content_digest=canonical_digest(command.contract),
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_blueprint_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_blueprint_version.updated",
                occurred_at=now,
            )
            return updated

    async def compile_blueprint_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        *,
        expected_revision: int,
    ) -> CompileBlueprintResponse:
        compiled_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_blueprint_version(connection, version_id)
            self._require_blueprint_version(actor, current, maintain=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            self._require_mutable_version(current.status, "DataBlueprint")
            if canonical_digest(current.contract) != current.content_digest:
                raise ApplicationError(
                    error_code=ErrorCode.VALIDATION_FAILED,
                    title="DataBlueprint 摘要校验失败",
                    detail="契约内容与 contentDigest 不一致。",
                    status_code=409,
                )
            atom_ids = tuple(
                sorted(
                    {node.atom_version_id for node in current.contract.nodes},
                    key=str,
                )
            )
            atoms = await self._fixtures.get_atom_versions(
                connection,
                project_id=current.project_id,
                version_ids=atom_ids,
            )
            compilation = compile_blueprint(
                current.contract,
                blueprint_version_id=current.id,
                blueprint_digest=current.content_digest,
                atom_versions=atoms,
            )
            updated = await self._fixtures.save_blueprint_compilation(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                compilation=compilation,
                compiled_at=compiled_at,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_blueprint_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_blueprint_version.compiled",
                occurred_at=compiled_at,
            )
            return CompileBlueprintResponse(version=updated, compilation=compilation)

    async def publish_blueprint_version(
        self,
        actor: ActorContext,
        version_id: UUID,
        *,
        expected_revision: int,
    ) -> DataBlueprintVersion:
        published_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._fixtures.get_blueprint_version(connection, version_id)
            self._require_blueprint_version(actor, current, publish=True)
            assert current is not None
            self._check_revision(current.revision, expected_revision)
            published_by = self._require_publisher(actor)
            self._require_publication_evidence(current)
            if current.compiled_plan is None or current.plan_digest is None:
                raise self._publication_evidence_required()
            updated = await self._fixtures.publish_blueprint_version(
                connection,
                version_id=version_id,
                expected_revision=expected_revision,
                published_at=published_at,
                published_by=published_by,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            await self._record_blueprint_version_event(
                connection,
                actor=actor,
                version=updated,
                event_type="data_blueprint_version.published",
                occurred_at=published_at,
            )
            return updated

    async def _complete_idempotency(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        scope: str,
        key: str,
        request_hash: str,
        value: WireModel,
    ) -> None:
        await self._idempotency.complete(
            connection,
            tenant_id=actor.tenant_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response=CachedHttpResponse(status_code=201, body=self._json_object(value)),
        )

    async def _record_atom_definition_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        atom: DataAtomDefinition,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        await self._record_event(
            connection,
            actor=actor,
            project_id=atom.project_id,
            aggregate_type="data_atom",
            aggregate_id=atom.id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload={
                "projectId": str(atom.project_id),
                "atomKey": atom.atom_key,
                "status": atom.status.value,
                "revision": atom.revision,
            },
        )

    async def _record_atom_version_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        version: DataAtomVersion,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        await self._record_event(
            connection,
            actor=actor,
            project_id=version.project_id,
            aggregate_type="data_atom_version",
            aggregate_id=version.id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=self._version_event_payload(
                project_id=version.project_id,
                definition_id=version.atom_id,
                definition_key="atomId",
                version=version.version,
                status=version.status,
                content_digest=version.content_digest,
                static_state=version.static_validation_state,
                runtime_state=version.runtime_validation_state,
                cleanup_state=version.cleanup_validation_state,
                revision=version.revision,
            ),
        )

    async def _record_blueprint_definition_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        blueprint: DataBlueprintDefinition,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        await self._record_event(
            connection,
            actor=actor,
            project_id=blueprint.project_id,
            aggregate_type="data_blueprint",
            aggregate_id=blueprint.id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload={
                "projectId": str(blueprint.project_id),
                "blueprintKey": blueprint.blueprint_key,
                "status": blueprint.status.value,
                "revision": blueprint.revision,
            },
        )

    async def _record_blueprint_version_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        version: DataBlueprintVersion,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload = self._version_event_payload(
            project_id=version.project_id,
            definition_id=version.blueprint_id,
            definition_key="blueprintId",
            version=version.version,
            status=version.status,
            content_digest=version.content_digest,
            static_state=version.static_validation_state,
            runtime_state=version.runtime_validation_state,
            cleanup_state=version.cleanup_validation_state,
            revision=version.revision,
        )
        if version.plan_digest is not None:
            payload["planDigest"] = version.plan_digest
        payload["compileIssueCount"] = len(version.compile_issues)
        await self._record_event(
            connection,
            actor=actor,
            project_id=version.project_id,
            aggregate_type="data_blueprint_version",
            aggregate_id=version.id,
            event_type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        project_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, JsonValue],
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type=aggregate_type,
            entity_id=aggregate_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    def _require_project(
        self,
        actor: ActorContext,
        project: Project | None,
        *,
        maintain: bool = False,
        publish: bool = False,
        active: bool = False,
    ) -> None:
        if project is None or not actor.can_read_project(project.id):
            raise self._not_found("Project 不存在。")
        if maintain and not actor.can_maintain_components(project.id):
            raise self._forbidden("当前角色不能维护该 Project 的 fixture assets。")
        if publish and not actor.can_publish_components(project.id):
            raise self._forbidden("当前角色不能发布该 Project 的 fixture assets。")
        if active and project.status is ProjectStatus.ARCHIVED:
            raise self._conflict("Project 已归档", "归档 Project 不能新增 fixture assets。")

    def _require_atom(
        self,
        actor: ActorContext,
        atom: DataAtomDefinition | None,
        *,
        maintain: bool = False,
    ) -> None:
        if atom is None or not actor.can_read_project(atom.project_id):
            raise self._not_found("DataAtom 不存在。")
        if maintain and not actor.can_maintain_components(atom.project_id):
            raise self._forbidden("当前角色不能维护该 DataAtom。")

    def _require_atom_version(
        self,
        actor: ActorContext,
        version: DataAtomVersion | None,
        *,
        maintain: bool = False,
        publish: bool = False,
    ) -> None:
        if version is None or not actor.can_read_project(version.project_id):
            raise self._not_found("DataAtomVersion 不存在。")
        if maintain and not actor.can_maintain_components(version.project_id):
            raise self._forbidden("当前角色不能维护该 DataAtomVersion。")
        if publish and not actor.can_publish_components(version.project_id):
            raise self._forbidden("当前角色不能发布该 DataAtomVersion。")

    def _require_blueprint(
        self,
        actor: ActorContext,
        blueprint: DataBlueprintDefinition | None,
        *,
        maintain: bool = False,
    ) -> None:
        if blueprint is None or not actor.can_read_project(blueprint.project_id):
            raise self._not_found("DataBlueprint 不存在。")
        if maintain and not actor.can_maintain_components(blueprint.project_id):
            raise self._forbidden("当前角色不能维护该 DataBlueprint。")

    def _require_blueprint_version(
        self,
        actor: ActorContext,
        version: DataBlueprintVersion | None,
        *,
        maintain: bool = False,
        publish: bool = False,
    ) -> None:
        if version is None or not actor.can_read_project(version.project_id):
            raise self._not_found("DataBlueprintVersion 不存在。")
        if maintain and not actor.can_maintain_components(version.project_id):
            raise self._forbidden("当前角色不能维护该 DataBlueprintVersion。")
        if publish and not actor.can_publish_components(version.project_id):
            raise self._forbidden("当前角色不能发布该 DataBlueprintVersion。")

    @staticmethod
    def _require_mutable_version(status: AssetVersionStatus, asset_name: str) -> None:
        if status in {AssetVersionStatus.PUBLISHED, AssetVersionStatus.DEPRECATED}:
            raise FixtureAssetService._asset_immutable(
                f"已发布或废弃的 {asset_name} 版本不可修改。"
            )

    @staticmethod
    def _require_publisher(actor: ActorContext) -> UUID:
        if actor.actor_id is None:
            raise FixtureAssetService._forbidden("发布动作需要可审计的 Actor。")
        return actor.actor_id

    @staticmethod
    def _require_publication_evidence(
        version: DataAtomVersion | DataBlueprintVersion,
    ) -> None:
        if version.status is not AssetVersionStatus.VALIDATED or any(
            state is not ValidationState.PASSED
            for state in (
                version.static_validation_state,
                version.runtime_validation_state,
                version.cleanup_validation_state,
            )
        ):
            raise FixtureAssetService._publication_evidence_required()

    @staticmethod
    def _version_event_payload(
        *,
        project_id: UUID,
        definition_id: UUID,
        definition_key: str,
        version: str,
        status: AssetVersionStatus,
        content_digest: str,
        static_state: ValidationState,
        runtime_state: ValidationState,
        cleanup_state: ValidationState,
        revision: int,
    ) -> dict[str, JsonValue]:
        return {
            "projectId": str(project_id),
            definition_key: str(definition_id),
            "version": version,
            "status": status.value,
            "contentDigest": content_digest,
            "staticValidationState": static_state.value,
            "runtimeValidationState": runtime_state.value,
            "cleanupValidationState": cleanup_state.value,
            "revision": revision,
        }

    @staticmethod
    def _next_cursor[T: CursorRecord](records: tuple[T, ...], limit: int) -> str | None:
        if len(records) <= limit or not records:
            return None
        last = records[limit - 1]
        return next_time_cursor(last.created_at, last.id)

    @staticmethod
    def _json_object(model: WireModel) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))

    @staticmethod
    def _check_revision(current_revision: int, expected_revision: int) -> None:
        if current_revision != expected_revision:
            raise FixtureAssetService._revision_conflict(current_revision)

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _forbidden(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="权限不足",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _conflict(title: str, detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title=title,
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _asset_immutable(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.ASSET_IMMUTABLE,
            title="资产版本不可变",
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _publication_evidence_required() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PUBLICATION_EVIDENCE_REQUIRED,
            title="发布证据不完整",
            detail="发布前必须具备 static、runtime 与 cleanup 三类 PASSED 证据。",
            status_code=409,
        )

    @staticmethod
    def _revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PRECONDITION_FAILED,
            title="资源 Revision 已变化",
            detail="请读取最新资源后重新提交变更。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
