"""Application services for durable fixture control and worker execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from psycopg.errors import UniqueViolation
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.ports.fixture_operations import (
    FixtureOperationContext,
    FixtureOperationError,
    FixtureOperationInvocation,
    FixtureOperationProvider,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    AssetVersionStatus,
    AtomEffect,
    CompiledNode,
    DataAtomVersion,
    DataNodeAttempt,
    DataNodeRunRecord,
    DataNodeRunStatus,
    ExecutionContextBinding,
    FixtureActorBindingRecord,
    FixtureCleanupState,
    FixtureFailureCategory,
    FixtureManifestRecord,
    FixtureNodeActivityResult,
    FixtureOperationResult,
    FixtureReleaseResult,
    FixtureResourcePage,
    FixtureRun,
    FixtureRunDetail,
    FixtureRunKind,
    FixtureRunRecord,
    FixtureRunStatus,
    FixtureValidationEvidence,
    FixtureWorkerPlan,
    LiteralBinding,
    NodeOutputBinding,
    PortDirection,
    PostconditionKind,
    ResourceOwnership,
    ResourceRecordInternal,
    ResourceRecordStatus,
    RunInputBinding,
    StartFixtureRun,
    ValidationEvidenceKind,
    ValidationEvidenceSubject,
    ValidationState,
    build_fixture_manifest,
    canonical_json_digest,
    ensure_future_deadline,
    pointer_value,
    validate_operation_inputs,
    validate_operation_outputs,
    validate_run_inputs,
)
from atlas_testops.domain.platform import EnvironmentKind, EnvironmentStatus, ProjectStatus
from atlas_testops.infrastructure.adapters.fixture_registry import (
    FixtureOperationCapabilityError,
    FixtureOperationNotRegisteredError,
    FixtureOperationRegistry,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.fixture_runs import (
    FixtureLeaseSnapshot,
    FixtureRunRepository,
)
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

FIXTURE_IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class _NodeExecutionClaim:
    phase: Literal["EXECUTE", "VERIFY"]
    run: FixtureRunRecord
    node: DataNodeRunRecord
    compiled_node: CompiledNode
    atom: DataAtomVersion
    binding: FixtureActorBindingRecord
    attempt: DataNodeAttempt
    inputs: dict[str, JsonValue]
    outputs: dict[str, JsonValue] | None
    provider: FixtureOperationProvider


class FixtureRunService:
    """Authorize fixture commands and dispatch durable workflow execution."""

    def __init__(
        self,
        database: Database,
        dispatcher: FixtureRunDispatcher,
        operation_registry: FixtureOperationRegistry,
        *,
        cleanup_grace: timedelta,
        repository: FixtureRunRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._dispatcher = dispatcher
        self._registry = operation_registry
        self._cleanup_grace = cleanup_grace
        self._runs = repository or FixtureRunRepository()
        self._platform = platform_repository or PlatformRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def start(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: StartFixtureRun,
        *,
        idempotency_key: str,
    ) -> CommandResult[FixtureRun]:
        now = utc_now()
        try:
            ensure_future_deadline(command.execution_deadline, now)
        except ValueError as error:
            raise _invalid_request(str(error)) from error
        request_hash = hash_request(
            {
                "projectId": str(project_id),
                **command.model_dump(mode="json", by_alias=True),
            }
        )
        scope = f"projects.{project_id}.fixture-runs.create"
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            if project is None or project.tenant_id != actor.tenant_id:
                raise _not_found("Project 不存在或不可见。")
            if not actor.can_operate_project(project_id):
                raise _forbidden("当前身份无权运行该 Project 的 Fixture。")
            if project.status is not ProjectStatus.ACTIVE:
                raise _conflict("已归档 Project 不能启动 FixtureRun。")

            environment = await self._platform.get_environment(
                connection,
                command.environment_id,
            )
            if environment is None or environment.project_id != project_id:
                raise _not_found("Environment 不存在或不属于该 Project。")
            if environment.status is not EnvironmentStatus.ACTIVE:
                raise _conflict("已停用 Environment 不能启动 FixtureRun。")
            if environment.kind is EnvironmentKind.PRODUCTION:
                raise _forbidden("FixtureRun 禁止在 Production Environment 执行。")

            blueprint = await self._runs.get_blueprint_version_for_share(
                connection,
                command.blueprint_version_id,
            )
            if blueprint is None or blueprint.project_id != project_id:
                raise _not_found("DataBlueprintVersion 不存在或不属于该 Project。")
            required_asset_status = (
                AssetVersionStatus.VALIDATED
                if command.run_kind is FixtureRunKind.VALIDATION
                else AssetVersionStatus.PUBLISHED
            )
            if (
                blueprint.status is not required_asset_status
                or blueprint.static_validation_state is not ValidationState.PASSED
                or blueprint.compiled_plan is None
                or blueprint.plan_digest is None
            ):
                raise _conflict(
                    f"{command.run_kind.value} FixtureRun 要求"
                    f" {required_asset_status.value} DataBlueprintVersion。"
                )
            try:
                validate_run_inputs(blueprint.contract.run_input_schema, command.inputs)
            except ValueError as error:
                raise _invalid_request(str(error)) from error

            plan = blueprint.compiled_plan
            atom_ids = tuple(sorted({node.atom_version_id for node in plan.nodes}))
            atoms = await self._runs.get_atom_versions_for_share(
                connection,
                project_id=project_id,
                version_ids=atom_ids,
            )
            if set(atoms) != set(atom_ids):
                raise _conflict("CompiledFixturePlan 引用的 DataAtomVersion 不完整。")
            bindings_by_slot = {item.actor_slot: item for item in command.actor_bindings}
            required_slots = {node.actor_slot for node in plan.nodes}
            if set(bindings_by_slot) != required_slots:
                raise _invalid_request(
                    "actorBindings 必须精确覆盖 CompiledFixturePlan 的 actorSlot。"
                )
            lease_ids = tuple(item.account_lease_id for item in command.actor_bindings)
            leases = await self._runs.get_lease_snapshots_for_share(connection, lease_ids)
            if set(leases) != set(lease_ids):
                raise _conflict("一个或多个 AccountLease 不存在或缺少 Connector 绑定。")

            for compiled_node in plan.nodes:
                atom = atoms[compiled_node.atom_version_id]
                self._validate_atom_for_run(
                    atom,
                    compiled_node=compiled_node,
                    environment_kind=environment.kind,
                    run_kind=command.run_kind,
                )
                requested_binding = bindings_by_slot[compiled_node.actor_slot]
                lease = leases[requested_binding.account_lease_id]
                self._validate_lease_for_run(
                    lease,
                    command=command,
                    project_id=project_id,
                    fencing_token=requested_binding.fencing_token,
                )
                self._validate_registered_operations(lease, atom)

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
                run = FixtureRun.model_validate(reservation.cached_response.body)
                result = CommandResult(
                    value=run,
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            else:
                run_id = new_entity_id()
                workflow_id = f"atlas-fixture/{actor.tenant_id}/{run_id}"
                cleanup_required = any(
                    atom.contract.resource_policy is not None
                    and atom.contract.resource_policy.ownership is ResourceOwnership.CREATED
                    for atom in atoms.values()
                )
                try:
                    record = await self._runs.create_run(
                        connection,
                        run_id=run_id,
                        tenant_id=actor.tenant_id,
                        project_id=project_id,
                        command=command,
                        blueprint=blueprint,
                        atom_versions=atoms,
                        lease_snapshots=leases,
                        requested_by=actor.actor_id,
                        workflow_id=workflow_id,
                        cleanup_state=(
                            FixtureCleanupState.PENDING
                            if cleanup_required
                            else FixtureCleanupState.NOT_REQUIRED
                        ),
                        requested_at=now,
                        node_ids={node.node_id: new_entity_id() for node in plan.nodes},
                    )
                except UniqueViolation as error:
                    raise _conflict(
                        "AccountLease 已绑定其他 FixtureRun，或运行身份发生并发冲突。"
                    ) from error
                if record is None:
                    raise _conflict(
                        "相同 Environment、BlueprintVersion 与 executionId 的 FixtureRun 已存在。"
                    )
                run = _public_run(record)
                await self._record_control_event(
                    connection,
                    actor=actor,
                    run=run,
                    event_type="fixture_run.requested",
                    occurred_at=now,
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=CachedHttpResponse(
                        status_code=202,
                        body=run.model_dump(mode="json", by_alias=True),
                    ),
                )
                result = CommandResult(value=run, status_code=202, replayed=False)

        try:
            await self._dispatcher.start(result.value)
        except ApplicationError:
            raise
        except Exception as error:
            raise _dependency_unavailable(
                "Fixture Workflow 暂时无法提交，请使用同一请求重试。"
            ) from error
        return result

    async def get_detail(self, actor: ActorContext, run_id: UUID) -> FixtureRunDetail:
        async with self._database.transaction(actor.database_context()) as connection:
            detail = await self._runs.get_detail(connection, run_id)
            if detail is None:
                raise _not_found("FixtureRun 不存在或不可见。")
            self._require_run_read(actor, detail.run)
            return detail

    async def get_manifest(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> FixtureManifestRecord:
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None:
                raise _not_found("FixtureRun 不存在或不可见。")
            self._require_run_read(actor, run)
            manifest = await self._runs.get_manifest(connection, run_id)
            if manifest is None:
                raise _not_found("FixtureRun 尚未生成 FixtureManifest。")
            return manifest

    async def list_resources(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> FixtureResourcePage:
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None:
                raise _not_found("FixtureRun 不存在或不可见。")
            self._require_run_read(actor, run)
            return await self._runs.list_resources(connection, run_id)

    async def release(self, actor: ActorContext, run_id: UUID) -> FixtureRun:
        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None:
                raise _not_found("FixtureRun 不存在或不可见。")
            if not actor.can_operate_project(run.project_id):
                raise _forbidden("当前身份无权释放该 FixtureRun。")
            if run.status not in {
                FixtureRunStatus.READY,
                FixtureRunStatus.CLEANING,
                FixtureRunStatus.RELEASED,
                FixtureRunStatus.CLEANUP_FAILED,
            }:
                raise _conflict("FixtureRun 仅能在 READY 后释放。")
            if run.status is FixtureRunStatus.READY:
                await self._record_control_event(
                    connection,
                    actor=actor,
                    run=run,
                    event_type="fixture_run.release_requested",
                    occurred_at=now,
                )
        if run.status in {FixtureRunStatus.READY, FixtureRunStatus.CLEANING}:
            try:
                await self._dispatcher.release(run)
            except ApplicationError:
                raise
            except Exception as error:
                raise _dependency_unavailable(
                    "Fixture Release 信号暂时无法提交，请重试。"
                ) from error
        return run

    def _validate_atom_for_run(
        self,
        atom: DataAtomVersion,
        *,
        compiled_node: CompiledNode,
        environment_kind: EnvironmentKind,
        run_kind: FixtureRunKind,
    ) -> None:
        required_status = (
            AssetVersionStatus.VALIDATED
            if run_kind is FixtureRunKind.VALIDATION
            else AssetVersionStatus.PUBLISHED
        )
        if (
            atom.status is not required_status
            or atom.static_validation_state is not ValidationState.PASSED
        ):
            raise _conflict(
                f"{run_kind.value} FixtureRun 要求"
                f" {required_status.value} DataAtomVersion {atom.id}。"
            )
        if atom.content_digest != compiled_node.atom_digest:
            raise _conflict(f"DataAtomVersion {atom.id} 已偏离 CompiledFixturePlan。")
        if environment_kind not in atom.contract.allowed_environment_kinds:
            raise _forbidden(f"DataAtomVersion {atom.id} 不允许在当前 EnvironmentKind 执行。")

    def _validate_lease_for_run(
        self,
        lease: FixtureLeaseSnapshot,
        *,
        command: StartFixtureRun,
        project_id: UUID,
        fencing_token: int,
    ) -> None:
        if (
            lease.project_id != project_id
            or lease.environment_id != command.environment_id
            or lease.execution_id != command.execution_id
        ):
            raise _conflict("AccountLease 与 FixtureRun Scope 或 executionId 不匹配。")
        if lease.lease_status != "ACTIVE" or lease.fencing_token != fencing_token:
            raise _conflict("AccountLease 已失效或 Fencing Token 不匹配。")
        cleanup_deadline = command.execution_deadline + self._cleanup_grace
        if lease.lease_expires_at < cleanup_deadline:
            raise _conflict("AccountLease TTL 无法覆盖 FixtureRun executionDeadline 与 Cleanup。")
        if lease.connector_status != "ACTIVE" or lease.connector_health_state != "HEALTHY":
            raise _dependency_unavailable("AccountLease 绑定的 Connector 当前不可用。")

    def _validate_registered_operations(
        self,
        lease: FixtureLeaseSnapshot,
        atom: DataAtomVersion,
    ) -> None:
        references = [atom.contract.operation]
        if atom.contract.cleanup_contract is not None:
            references.append(atom.contract.cleanup_contract.operation)
            if atom.contract.cleanup_contract.verify_operation is not None:
                references.append(atom.contract.cleanup_contract.verify_operation)
        if atom.contract.reconcile_contract is not None:
            references.append(atom.contract.reconcile_contract.operation)
        references.extend(
            item.operation for item in atom.contract.postconditions if item.operation is not None
        )
        try:
            for reference in references:
                self._registry.resolve(lease.connector_adapter_key, reference)
        except (FixtureOperationNotRegisteredError, FixtureOperationCapabilityError) as error:
            raise _conflict(
                f"当前部署未注册 DataAtomVersion {atom.id} 要求的精确 Operation。"
            ) from error

    def _require_run_read(self, actor: ActorContext, run: FixtureRun) -> None:
        if run.tenant_id != actor.tenant_id or not actor.can_read_project(run.project_id):
            raise _not_found("FixtureRun 不存在或不可见。")

    async def _record_control_event(
        self,
        connection: object,
        *,
        actor: ActorContext,
        run: FixtureRun,
        event_type: str,
        occurred_at: object,
    ) -> None:
        # The concrete connection and datetime types are preserved at every call site.
        from datetime import datetime
        from typing import cast

        from psycopg import AsyncConnection
        from psycopg.rows import DictRow

        typed_connection = cast(AsyncConnection[DictRow], connection)
        typed_occurred_at = cast(datetime, occurred_at)
        payload: dict[str, JsonValue] = {
            "projectId": str(run.project_id),
            "environmentId": str(run.environment_id),
            "blueprintVersionId": str(run.blueprint_version_id),
            "executionId": run.execution_id,
            "status": run.status.value,
        }
        await self._audit.append(
            typed_connection,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            environment_id=run.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="fixture_run",
            entity_id=run.id,
            occurred_at=typed_occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            typed_connection,
            DomainEvent(
                tenant_id=run.tenant_id,
                aggregate_type="fixture_run",
                aggregate_id=run.id,
                event_type=event_type,
                occurred_at=typed_occurred_at,
                payload=payload,
            ),
        )


class FixtureWorkerService:
    """Execute provider calls outside short database transactions."""

    def __init__(
        self,
        database: Database,
        operation_registry: FixtureOperationRegistry,
        *,
        cleanup_grace: timedelta,
        repository: FixtureRunRepository | None = None,
    ) -> None:
        self._database = database
        self._registry = operation_registry
        self._cleanup_grace = cleanup_grace
        self._runs = repository or FixtureRunRepository()

    async def load_plan(self, tenant_id: UUID, run_id: UUID) -> FixtureWorkerPlan:
        now = utc_now()
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id)
            if run is None:
                raise RuntimeError("fixture run is not visible to the worker")
            for actor_slot in sorted({node.actor_slot for node in run.compiled_plan.nodes}):
                binding = await self._runs.get_binding_record(
                    connection,
                    run_id=run.id,
                    actor_slot=actor_slot,
                )
                if binding is None:
                    raise RuntimeError("fixture actor binding is missing")
                self._validate_runtime_binding(binding, run, now=now)
            return FixtureWorkerPlan(
                fixture_run_id=run.id,
                execution_levels=run.compiled_plan.execution_levels,
                cleanup_order=run.compiled_plan.cleanup_order,
                execution_deadline=run.execution_deadline,
            )

    async def execute_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> FixtureNodeActivityResult:
        try:
            claim = await self._claim_node(tenant_id, run_id, node_id)
        except _NodeValidationFailure as error:
            await self._fail_unclaimed_node(
                tenant_id,
                run_id,
                node_id,
                category=error.category,
                code=error.code,
                detail=error.detail,
            )
            return FixtureNodeActivityResult(
                node_id=node_id,
                status=DataNodeRunStatus.FAILED,
                failure_category=error.category,
                failure_code=error.code,
            )
        if isinstance(claim, FixtureNodeActivityResult):
            return claim

        outputs: dict[str, JsonValue]
        provider_request_id: str | None = claim.attempt.provider_request_id
        if claim.phase == "EXECUTE":
            result: FixtureOperationResult | None = None
            try:
                result = await claim.provider.execute(
                    context=self._operation_context(claim),
                    invocation=FixtureOperationInvocation(
                        operation=claim.atom.contract.operation,
                        inputs=claim.inputs,
                        expected_outputs=_output_schemas(claim.atom),
                    ),
                )
                validate_operation_outputs(claim.atom.contract, result.outputs)
            except FixtureOperationError as error:
                status = (
                    DataNodeRunStatus.OUTCOME_UNCERTAIN
                    if error.outcome_uncertain
                    else DataNodeRunStatus.FAILED
                )
                return await self._fail_claim(
                    tenant_id,
                    claim,
                    status=status,
                    category=error.category,
                    code=_safe_failure_code(error.code),
                    detail=error.safe_detail,
                )
            except ValueError:
                status = (
                    DataNodeRunStatus.OUTCOME_UNCERTAIN
                    if claim.atom.contract.effect
                    in {AtomEffect.CREATE, AtomEffect.UPDATE, AtomEffect.DELETE}
                    else DataNodeRunStatus.FAILED
                )
                return await self._fail_claim(
                    tenant_id,
                    claim,
                    status=status,
                    category=FixtureFailureCategory.VALIDATION,
                    code="PROVIDER_OUTPUT_INVALID",
                    detail="The provider result did not match the reviewed atom contract.",
                    provider_request_id=(
                        result.provider_request_id if result is not None else None
                    ),
                )
            except Exception:
                return await self._fail_claim(
                    tenant_id,
                    claim,
                    status=DataNodeRunStatus.OUTCOME_UNCERTAIN,
                    category=FixtureFailureCategory.UNCERTAIN,
                    code="PROVIDER_OUTCOME_UNCERTAIN",
                    detail="The provider call ended without a durable outcome confirmation.",
                )
            outputs = result.outputs
            provider_request_id = result.provider_request_id
            try:
                claim = await self._record_provider_outcome(
                    tenant_id,
                    claim,
                    result,
                )
            except Exception:
                return await self._fail_claim(
                    tenant_id,
                    claim,
                    status=DataNodeRunStatus.OUTCOME_UNCERTAIN,
                    category=FixtureFailureCategory.UNCERTAIN,
                    code="PROVIDER_OUTCOME_NOT_DURABLE",
                    detail="The provider outcome could not be durably recorded.",
                    provider_request_id=provider_request_id,
                )
        else:
            if claim.outputs is None:
                raise RuntimeError("verifying fixture node has no durable outputs")
            outputs = claim.outputs

        try:
            await self._verify_postconditions(claim, outputs)
        except FixtureOperationError as error:
            return await self._fail_claim(
                tenant_id,
                claim,
                status=DataNodeRunStatus.FAILED,
                category=error.category,
                code=_safe_failure_code(error.code),
                detail=error.safe_detail,
                provider_request_id=provider_request_id,
            )
        except Exception:
            return await self._fail_claim(
                tenant_id,
                claim,
                status=DataNodeRunStatus.FAILED,
                category=FixtureFailureCategory.VALIDATION,
                code="POSTCONDITION_FAILED",
                detail="A reviewed fixture postcondition did not pass.",
                provider_request_id=provider_request_id,
            )

        finished_at = utc_now()
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            node = await self._runs.get_node_record(
                connection,
                run_id=run_id,
                node_id=node_id,
                for_update=True,
            )
            if node is None:
                raise RuntimeError("fixture node disappeared before completion")
            if node.status is DataNodeRunStatus.SUCCEEDED:
                return FixtureNodeActivityResult(node_id=node_id, status=node.status)
            attempt = await self._runs.get_running_attempt(
                connection,
                node.id,
                for_update=True,
            )
            if attempt is None:
                raise RuntimeError("fixture node has no running attempt")
            updated = await self._runs.complete_node_success(
                connection,
                node=node,
                attempt=attempt,
                provider_request_id=provider_request_id,
                finished_at=finished_at,
            )
            if updated is None:
                raise RuntimeError("fixture node completion lost its revision race")
        return FixtureNodeActivityResult(node_id=node_id, status=DataNodeRunStatus.SUCCEEDED)

    async def finalize_ready(self, tenant_id: UUID, run_id: UUID) -> FixtureRun:
        observed_at = utc_now()
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id, for_update=True)
            if run is None:
                raise RuntimeError("fixture run is not visible to the worker")
            if run.status is FixtureRunStatus.READY:
                return _public_run(run)
            node_ids = tuple(node.node_id for node in run.compiled_plan.nodes)
            outputs = await self._runs.get_node_outputs(
                connection,
                run_id=run.id,
                node_ids=node_ids,
            )
            manifest = build_fixture_manifest(
                fixture_run_id=run.id,
                blueprint_version_id=run.blueprint_version_id,
                plan=run.compiled_plan,
                node_outputs=outputs,
            )
            evidence: list[FixtureValidationEvidence] = []
            if run.run_kind is FixtureRunKind.VALIDATION:
                for atom_version_id, digest in sorted(
                    {
                        node.atom_version_id: node.atom_digest for node in run.compiled_plan.nodes
                    }.items(),
                    key=lambda item: str(item[0]),
                ):
                    evidence.append(
                        _runtime_evidence(
                            run,
                            subject=ValidationEvidenceSubject.ATOM_VERSION,
                            subject_version_id=atom_version_id,
                            digest=digest,
                            observed_at=observed_at,
                        )
                    )
                evidence.append(
                    _runtime_evidence(
                        run,
                        subject=ValidationEvidenceSubject.BLUEPRINT_VERSION,
                        subject_version_id=run.blueprint_version_id,
                        digest=run.plan_digest,
                        observed_at=observed_at,
                    )
                )
            ready = await self._runs.finalize_ready(
                connection,
                run=run,
                manifest=manifest,
                manifest_digest=canonical_json_digest(
                    manifest.model_dump(mode="json", by_alias=True)
                ),
                observed_at=observed_at,
                evidence=tuple(evidence),
            )
            if ready is None:
                raise RuntimeError("fixture run is not ready to finalize")
            return ready

    async def begin_release(self, tenant_id: UUID, run_id: UUID) -> FixtureRun:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id, for_update=True)
            if run is None:
                raise RuntimeError("fixture run is not visible to the worker")
            if run.status in {
                FixtureRunStatus.CLEANING,
                FixtureRunStatus.RELEASED,
                FixtureRunStatus.CLEANUP_FAILED,
            }:
                return _public_run(run)
            started = await self._runs.begin_release(
                connection,
                run=run,
            )
            if started is None:
                raise RuntimeError("fixture run cannot begin release")
            return started

    async def begin_failed_cleanup(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        category: FixtureFailureCategory,
        code: str,
    ) -> FixtureRun:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id, for_update=True)
            if run is None:
                raise RuntimeError("fixture run is not visible to the worker")
            if run.status is FixtureRunStatus.FAILED:
                return _public_run(run)
            started = await self._runs.begin_failed_cleanup(
                connection,
                run=run,
                category=category,
                code=_safe_failure_code(code),
                detail="Fixture preparation stopped because a node did not succeed.",
            )
            if started is None:
                raise RuntimeError("fixture run cannot begin failure cleanup")
            return started

    async def cleanup_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> FixtureReleaseResult:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            resources = await self._runs.list_cleanup_resources(
                connection,
                run_id=run_id,
                node_id=node_id,
            )
        cleaned = 0
        leaked = 0
        for candidate in resources:
            if candidate.status is ResourceRecordStatus.CLEANING:
                await self._mark_cleanup_leaked(tenant_id, candidate)
                leaked += 1
                continue
            async with self._database.transaction(
                DatabaseContext(tenant_id=tenant_id)
            ) as connection:
                resource = await self._runs.claim_resource_cleanup(
                    connection,
                    resource_id=candidate.id,
                    expected_revision=candidate.revision,
                )
                if resource is None:
                    leaked += 1
                    continue
                run = await self._runs.get_run_record(connection, run_id)
                node = await self._runs.get_node_record(
                    connection,
                    run_id=run_id,
                    node_id=node_id,
                )
                if run is None or node is None:
                    raise RuntimeError("fixture cleanup context is missing")
                atom = await self._runs.get_atom_version(connection, node.atom_version_id)
                binding = await self._runs.get_binding_record(
                    connection,
                    run_id=run_id,
                    actor_slot=node.actor_slot,
                )
                if atom is None or binding is None or atom.contract.cleanup_contract is None:
                    raise RuntimeError("fixture cleanup contract is missing")
                cleanup_now = utc_now()
                if (
                    binding.lease_status != "ACTIVE"
                    or binding.lease_expires_at <= cleanup_now
                    or binding.connector_status != "ACTIVE"
                ):
                    await self._runs.fail_resource_cleanup(connection, resource=resource)
                    leaked += 1
                    continue
                cleanup = atom.contract.cleanup_contract
                if (
                    cleanup.operation.operation_key != resource.cleanup_operation_key
                    or cleanup.operation.operation_version != resource.cleanup_operation_version
                ):
                    raise RuntimeError("fixture cleanup operation changed")
                provider = self._registry.resolve(
                    binding.connector_adapter_key,
                    cleanup.operation,
                )
            context = FixtureOperationContext(
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                environment_id=run.environment_id,
                fixture_run_id=run.id,
                data_node_run_id=node.id,
                connector_installation_id=binding.connector_installation_id,
                account_handle=binding.account_handle,
                configuration_ref=binding.connector_configuration_ref,
                idempotency_key=(
                    f"{node.logical_idempotency_key}:cleanup:{resource.cleanup_generation}"
                ),
                request_id=f"fixture-cleanup:{resource.id}:{resource.cleanup_generation}",
                deadline=min(
                    cleanup_now + timedelta(seconds=cleanup.operation.timeout_seconds),
                    binding.lease_expires_at,
                ),
            )
            try:
                await provider.execute(
                    context=context,
                    invocation=FixtureOperationInvocation(
                        operation=cleanup.operation,
                        inputs={cleanup.resource_ref_input: resource.opaque_ref},
                        expected_outputs={},
                    ),
                )
                if cleanup.verify_operation is not None:
                    verify_provider = self._registry.resolve(
                        binding.connector_adapter_key,
                        cleanup.verify_operation,
                    )
                    await verify_provider.execute(
                        context=context,
                        invocation=FixtureOperationInvocation(
                            operation=cleanup.verify_operation,
                            inputs={cleanup.resource_ref_input: resource.opaque_ref},
                            expected_outputs={},
                        ),
                    )
            except Exception:
                await self._mark_cleanup_leaked(tenant_id, resource)
                leaked += 1
                continue
            async with self._database.transaction(
                DatabaseContext(tenant_id=tenant_id)
            ) as connection:
                completed = await self._runs.complete_resource_cleanup(
                    connection,
                    resource=resource,
                    cleaned_at=utc_now(),
                )
                if completed is None:
                    raise RuntimeError("fixture resource cleanup lost its revision race")
                cleaned += 1
        return FixtureReleaseResult(
            fixture_run_id=run_id,
            status=FixtureRunStatus.CLEANING,
            cleanup_state=FixtureCleanupState.RUNNING,
            cleaned_resources=cleaned,
            leaked_resources=leaked,
        )

    async def finalize_release(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        failed_run: bool,
    ) -> FixtureReleaseResult:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id, for_update=True)
            if run is None:
                raise RuntimeError("fixture run is not visible to the worker")
            if run.status in {
                FixtureRunStatus.RELEASED,
                FixtureRunStatus.CLEANUP_FAILED,
                FixtureRunStatus.FAILED,
            }:
                final = _public_run(run)
            elif failed_run:
                result = await self._runs.finalize_failed_run(
                    connection,
                    run=run,
                    finished_at=utc_now(),
                )
                if result is None:
                    raise RuntimeError("failed fixture run could not be finalized")
                final = result
            else:
                result = await self._runs.finalize_release(
                    connection,
                    run=run,
                    finished_at=utc_now(),
                )
                if result is None:
                    raise RuntimeError("fixture release could not be finalized")
                final = result
            resources = await self._runs.list_resources(connection, run_id)
        return FixtureReleaseResult(
            fixture_run_id=run_id,
            status=final.status,
            cleanup_state=final.cleanup_state,
            cleaned_resources=sum(
                item.status is ResourceRecordStatus.CLEANED for item in resources.items
            ),
            leaked_resources=sum(
                item.status is ResourceRecordStatus.LEAKED for item in resources.items
            ),
        )

    async def _claim_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> _NodeExecutionClaim | FixtureNodeActivityResult:
        now = utc_now()
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            run = await self._runs.get_run_record(connection, run_id, for_update=True)
            node = await self._runs.get_node_record(
                connection,
                run_id=run_id,
                node_id=node_id,
                for_update=True,
            )
            if run is None or node is None:
                raise _NodeValidationFailure(
                    FixtureFailureCategory.VALIDATION,
                    "FIXTURE_NODE_NOT_FOUND",
                    "The fixture node does not exist in the frozen run.",
                )
            if node.status in {
                DataNodeRunStatus.SUCCEEDED,
                DataNodeRunStatus.FAILED,
                DataNodeRunStatus.OUTCOME_UNCERTAIN,
            }:
                return FixtureNodeActivityResult(
                    node_id=node.node_id,
                    status=node.status,
                    failure_category=node.failure_category,
                    failure_code=node.failure_code,
                )
            compiled_node = next(
                (item for item in run.compiled_plan.nodes if item.node_id == node_id),
                None,
            )
            if compiled_node is None:
                raise _NodeValidationFailure(
                    FixtureFailureCategory.VALIDATION,
                    "FIXTURE_NODE_NOT_FROZEN",
                    "The fixture node is absent from the frozen plan.",
                )
            atom = await self._runs.get_atom_version(connection, node.atom_version_id)
            binding = await self._runs.get_binding_record(
                connection,
                run_id=run_id,
                actor_slot=node.actor_slot,
            )
            if atom is None or atom.content_digest != compiled_node.atom_digest:
                raise _NodeValidationFailure(
                    FixtureFailureCategory.VALIDATION,
                    "ATOM_VERSION_CHANGED",
                    "The atom version changed after the fixture plan was frozen.",
                )
            if binding is None:
                raise _NodeValidationFailure(
                    FixtureFailureCategory.VALIDATION,
                    "ACTOR_BINDING_MISSING",
                    "The fixture actor binding is unavailable.",
                )
            self._validate_runtime_binding(binding, run, now=now)
            provider = self._registry.resolve(
                binding.connector_adapter_key,
                atom.contract.operation,
            )
            if node.status is DataNodeRunStatus.VERIFYING:
                attempt = await self._runs.get_running_attempt(
                    connection,
                    node.id,
                    for_update=True,
                )
                if attempt is None or node.inputs is None or node.outputs is None:
                    raise RuntimeError("verifying fixture node is missing durable state")
                return _NodeExecutionClaim(
                    phase="VERIFY",
                    run=run,
                    node=node,
                    compiled_node=compiled_node,
                    atom=atom,
                    binding=binding,
                    attempt=attempt,
                    inputs=node.inputs,
                    outputs=node.outputs,
                    provider=provider,
                )
            if node.status is DataNodeRunStatus.RUNNING:
                attempt = await self._runs.get_running_attempt(
                    connection,
                    node.id,
                    for_update=True,
                )
                if attempt is None:
                    raise RuntimeError("running fixture node is missing its attempt")
                uncertain = await self._runs.complete_node_failure(
                    connection,
                    node=node,
                    attempt=attempt,
                    status=DataNodeRunStatus.OUTCOME_UNCERTAIN,
                    category=FixtureFailureCategory.UNCERTAIN,
                    code="ACTIVITY_REPLAY_OUTCOME_UNCERTAIN",
                    detail="The prior provider attempt has no durable completion record.",
                    provider_request_id=None,
                    finished_at=now,
                )
                if uncertain is None:
                    raise RuntimeError("fixture uncertain outcome could not be recorded")
                return FixtureNodeActivityResult(
                    node_id=node_id,
                    status=DataNodeRunStatus.OUTCOME_UNCERTAIN,
                    failure_category=FixtureFailureCategory.UNCERTAIN,
                    failure_code="ACTIVITY_REPLAY_OUTCOME_UNCERTAIN",
                )
            inputs = await self._resolve_inputs(connection, run, compiled_node)
            marker = atom.contract.idempotency_policy.marker_input
            if marker is not None:
                inputs[marker] = node.logical_idempotency_key
            try:
                validate_operation_inputs(atom.contract, inputs)
            except ValueError as error:
                raise _NodeValidationFailure(
                    FixtureFailureCategory.VALIDATION,
                    "ATOM_INPUT_INVALID",
                    "The resolved atom inputs did not match the reviewed contract.",
                ) from error
            started = await self._runs.start_node_attempt(
                connection,
                run=run,
                node=node,
                attempt_id=new_entity_id(),
                inputs=inputs,
                started_at=now,
            )
            if started is None:
                raise RuntimeError("fixture node attempt lost its revision race")
            updated_node, attempt = started
            return _NodeExecutionClaim(
                phase="EXECUTE",
                run=run,
                node=updated_node,
                compiled_node=compiled_node,
                atom=atom,
                binding=binding,
                attempt=attempt,
                inputs=inputs,
                outputs=None,
                provider=provider,
            )

    async def _resolve_inputs(
        self,
        connection: object,
        run: FixtureRunRecord,
        node: CompiledNode,
    ) -> dict[str, JsonValue]:
        from typing import cast

        from psycopg import AsyncConnection
        from psycopg.rows import DictRow

        typed_connection = cast(AsyncConnection[DictRow], connection)
        source_ids = tuple(
            sorted(
                {
                    binding.source_node_id
                    for binding in node.bindings
                    if isinstance(binding, NodeOutputBinding)
                }
            )
        )
        source_outputs = await self._runs.get_node_outputs(
            typed_connection,
            run_id=run.id,
            node_ids=source_ids,
        )
        inputs: dict[str, JsonValue] = {}
        for binding in node.bindings:
            if isinstance(binding, LiteralBinding):
                value = binding.value
            elif isinstance(binding, RunInputBinding):
                try:
                    value = pointer_value(run.run_inputs, binding.pointer)
                except ValueError as error:
                    raise _NodeValidationFailure(
                        FixtureFailureCategory.VALIDATION,
                        "RUN_INPUT_BINDING_UNRESOLVED",
                        "A frozen run input binding could not be resolved.",
                    ) from error
            elif isinstance(binding, NodeOutputBinding):
                source = source_outputs.get(binding.source_node_id)
                if source is None or binding.source_port not in source:
                    raise _NodeValidationFailure(
                        FixtureFailureCategory.VALIDATION,
                        "UPSTREAM_OUTPUT_MISSING",
                        "A required upstream node output is unavailable.",
                    )
                value = source[binding.source_port]
            elif isinstance(binding, ExecutionContextBinding):
                value = run.execution_id
            else:
                raise RuntimeError("unsupported frozen fixture binding")
            inputs[binding.target_port] = value
        return inputs

    async def _record_provider_outcome(
        self,
        tenant_id: UUID,
        claim: _NodeExecutionClaim,
        result: FixtureOperationResult,
    ) -> _NodeExecutionClaim:
        recorded_at = utc_now()
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            node = await self._runs.get_node_record(
                connection,
                run_id=claim.run.id,
                node_id=claim.node.node_id,
                for_update=True,
            )
            if node is None:
                raise RuntimeError("fixture node disappeared after provider execution")
            attempt = await self._runs.get_running_attempt(
                connection,
                node.id,
                for_update=True,
            )
            if attempt is None:
                raise RuntimeError("fixture provider outcome has no running attempt")
            verifying = await self._runs.mark_node_verifying(
                connection,
                node_run_id=node.id,
                expected_revision=node.revision,
                outputs=result.outputs,
                output_digest=canonical_json_digest(result.outputs),
            )
            if verifying is None:
                raise RuntimeError("fixture provider outcome lost its revision race")
            policy = claim.atom.contract.resource_policy
            cleanup = claim.atom.contract.cleanup_contract
            if policy is not None:
                opaque_ref = result.outputs.get(policy.resource_ref_output)
                if not isinstance(opaque_ref, str) or not opaque_ref.strip():
                    raise RuntimeError("resource reference output must be a non-blank string")
                parent_ids: list[UUID] = []
                for parent_input in policy.parent_ref_inputs:
                    parent_ref = claim.inputs.get(parent_input)
                    if not isinstance(parent_ref, str):
                        raise RuntimeError("parent resource input must be a string reference")
                    parent = await self._runs.get_resource_by_opaque_ref(
                        connection,
                        run_id=claim.run.id,
                        opaque_ref=parent_ref,
                    )
                    if parent is None:
                        raise RuntimeError("parent resource is absent from the fixture ledger")
                    parent_ids.append(parent.id)
                resource_id = new_entity_id()
                await self._runs.record_resource(
                    connection,
                    run=claim.run,
                    node=verifying,
                    attempt=attempt,
                    connector_installation_id=claim.binding.connector_installation_id,
                    resource_id=resource_id,
                    resource_handle=f"fr_{resource_id.hex}",
                    resource_type=policy.resource_type,
                    resource_ownership=policy.ownership,
                    opaque_ref=opaque_ref,
                    expires_at=recorded_at + timedelta(seconds=policy.ttl_seconds),
                    cleanup_operation_key=(
                        cleanup.operation.operation_key if cleanup is not None else None
                    ),
                    cleanup_operation_version=(
                        cleanup.operation.operation_version if cleanup is not None else None
                    ),
                    recorded_at=recorded_at,
                    parent_resource_ids=tuple(parent_ids),
                )
        return _NodeExecutionClaim(
            phase="VERIFY",
            run=claim.run,
            node=verifying,
            compiled_node=claim.compiled_node,
            atom=claim.atom,
            binding=claim.binding,
            attempt=attempt,
            inputs=claim.inputs,
            outputs=result.outputs,
            provider=claim.provider,
        )

    async def _verify_postconditions(
        self,
        claim: _NodeExecutionClaim,
        outputs: dict[str, JsonValue],
    ) -> None:
        merged = {**claim.inputs, **outputs}
        for postcondition in claim.atom.contract.postconditions:
            if postcondition.kind is PostconditionKind.OUTPUT_SCHEMA:
                continue
            if postcondition.operation is None:
                raise RuntimeError("resource postcondition has no operation")
            provider = self._registry.resolve(
                claim.binding.connector_adapter_key,
                postcondition.operation,
            )
            await provider.execute(
                context=self._operation_context(claim),
                invocation=FixtureOperationInvocation(
                    operation=postcondition.operation,
                    inputs=merged,
                    expected_outputs={},
                ),
            )

    async def _fail_claim(
        self,
        tenant_id: UUID,
        claim: _NodeExecutionClaim,
        *,
        status: DataNodeRunStatus,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
        provider_request_id: str | None = None,
    ) -> FixtureNodeActivityResult:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            node = await self._runs.get_node_record(
                connection,
                run_id=claim.run.id,
                node_id=claim.node.node_id,
                for_update=True,
            )
            if node is None:
                raise RuntimeError("fixture node disappeared before failure recording")
            if node.status in {
                DataNodeRunStatus.FAILED,
                DataNodeRunStatus.OUTCOME_UNCERTAIN,
            }:
                return FixtureNodeActivityResult(
                    node_id=node.node_id,
                    status=node.status,
                    failure_category=node.failure_category,
                    failure_code=node.failure_code,
                )
            attempt = await self._runs.get_running_attempt(
                connection,
                node.id,
                for_update=True,
            )
            if attempt is None:
                raise RuntimeError("fixture node failure has no running attempt")
            failed = await self._runs.complete_node_failure(
                connection,
                node=node,
                attempt=attempt,
                status=status,
                category=category,
                code=code,
                detail=detail[:500],
                provider_request_id=provider_request_id,
                finished_at=utc_now(),
            )
            if failed is None:
                raise RuntimeError("fixture node failure lost its revision race")
        return FixtureNodeActivityResult(
            node_id=claim.node.node_id,
            status=status,
            failure_category=category,
            failure_code=code,
        )

    async def _fail_unclaimed_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
        *,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
    ) -> None:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            node = await self._runs.get_node_record(
                connection,
                run_id=run_id,
                node_id=node_id,
                for_update=True,
            )
            if node is None or node.status in {
                DataNodeRunStatus.FAILED,
                DataNodeRunStatus.OUTCOME_UNCERTAIN,
            }:
                return
            failed = await self._runs.fail_node_without_attempt(
                connection,
                node=node,
                category=category,
                code=code,
                detail=detail[:500],
                finished_at=utc_now(),
            )
            if failed is None:
                raise RuntimeError("fixture node validation failure lost its revision race")

    async def _mark_cleanup_leaked(
        self,
        tenant_id: UUID,
        resource: ResourceRecordInternal,
    ) -> None:
        async with self._database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            await self._runs.fail_resource_cleanup(connection, resource=resource)

    def _operation_context(self, claim: _NodeExecutionClaim) -> FixtureOperationContext:
        return FixtureOperationContext(
            tenant_id=claim.run.tenant_id,
            project_id=claim.run.project_id,
            environment_id=claim.run.environment_id,
            fixture_run_id=claim.run.id,
            data_node_run_id=claim.node.id,
            connector_installation_id=claim.binding.connector_installation_id,
            account_handle=claim.binding.account_handle,
            configuration_ref=claim.binding.connector_configuration_ref,
            idempotency_key=claim.node.logical_idempotency_key,
            request_id=f"fixture:{claim.run.id}:{claim.node.node_id}:{claim.attempt.attempt_number}",
            deadline=claim.run.execution_deadline,
        )

    def _validate_runtime_binding(
        self,
        binding: FixtureActorBindingRecord,
        run: FixtureRunRecord,
        *,
        now: object,
    ) -> None:
        from datetime import datetime
        from typing import cast

        observed_at = cast(datetime, now)
        if binding.lease_status != "ACTIVE" or binding.lease_expires_at <= observed_at:
            raise _NodeValidationFailure(
                FixtureFailureCategory.AUTH,
                "ACCOUNT_LEASE_INACTIVE",
                "The bound account lease is inactive or expired.",
            )
        if binding.lease_expires_at < run.execution_deadline + self._cleanup_grace:
            raise _NodeValidationFailure(
                FixtureFailureCategory.AUTH,
                "ACCOUNT_LEASE_TOO_SHORT",
                "The bound account lease no longer covers execution and cleanup.",
            )
        if binding.connector_status != "ACTIVE":
            raise _NodeValidationFailure(
                FixtureFailureCategory.INFRASTRUCTURE,
                "CONNECTOR_INACTIVE",
                "The bound connector is not active.",
            )


class _NodeValidationFailure(RuntimeError):
    def __init__(
        self,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.category = category
        self.code = code
        self.detail = detail


def _output_schemas(atom: DataAtomVersion) -> dict[str, dict[str, JsonValue]]:
    return {
        port.key: port.json_schema
        for port in atom.contract.ports
        if port.direction is PortDirection.OUTPUT
    }


def _public_run(run: FixtureRunRecord) -> FixtureRun:
    return FixtureRun.model_validate(
        run.model_dump(include=set(FixtureRun.model_fields)),
    )


def _runtime_evidence(
    run: FixtureRunRecord,
    *,
    subject: ValidationEvidenceSubject,
    subject_version_id: UUID,
    digest: str,
    observed_at: object,
) -> FixtureValidationEvidence:
    from datetime import datetime
    from typing import cast

    return FixtureValidationEvidence(
        id=new_entity_id(),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        environment_id=run.environment_id,
        fixture_run_id=run.id,
        kind=ValidationEvidenceKind.RUNTIME,
        subject=subject,
        subject_version_id=subject_version_id,
        subject_digest=digest,
        passed=True,
        safe_summary="Fixture runtime validation passed for the frozen version digest.",
        observed_at=cast("datetime", observed_at),
    )


def _safe_failure_code(value: str) -> str:
    normalized = value.strip().upper().replace("-", "_").replace(".", "_")
    if not normalized or len(normalized) > 80 or not normalized.replace("_", "").isalnum():
        return "FIXTURE_OPERATION_FAILED"
    if not normalized[0].isalpha():
        return "FIXTURE_OPERATION_FAILED"
    return normalized


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="FixtureRun 请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="FixtureRun 不存在",
        detail=detail,
        status_code=404,
    )


def _forbidden(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="FixtureRun 操作被拒绝",
        detail=detail,
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="FixtureRun 状态冲突",
        detail=detail,
        status_code=409,
    )


def _dependency_unavailable(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Fixture Worker 不可用",
        detail=detail,
        status_code=503,
    )
