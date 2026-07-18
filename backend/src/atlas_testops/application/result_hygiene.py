"""Trusted Cleanup projection from Fixture ledgers into Result revisions."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    FixtureCleanupState,
    FixtureRun,
    FixtureRunKind,
    ResourceOwnership,
    ResourceRecordStatus,
)
from atlas_testops.domain.result import (
    UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
    UNIT_HYGIENE_RESOLUTION_POLICY_VERSION,
    AttemptFixtureBinding,
    AttemptFixtureBindingContent,
    DataHygiene,
    UnitHygieneInputSource,
    UnitHygieneResolutionInput,
    UnitHygieneResolutionRevision,
    UnitHygieneResolutionRevisionContent,
    attempt_fixture_binding_hash,
    parse_task_attempt_fixture_execution_id,
    resolve_unit_data_hygiene,
    result_projection_digest,
    task_attempt_fixture_execution_id,
    unit_hygiene_input_set_hash,
    unit_hygiene_resolution_hash,
)
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionUnit,
    UnitAttempt,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.fixture_runs import FixtureRunRepository
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


class ResultHygieneProjectionError(RuntimeError):
    """Safe permanent error raised when Cleanup provenance is not exact."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class ResultHygieneProjectionService:
    """Bind Task Attempts to Fixtures and append deterministic Unit Hygiene revisions."""

    def __init__(
        self,
        *,
        result_repository: ResultFactRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        fixture_repository: FixtureRunRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._results = result_repository or ResultFactRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._fixtures = fixture_repository or FixtureRunRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def bind_fixture_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        fixture_run: FixtureRun,
        created_at: datetime,
    ) -> AttemptFixtureBinding | None:
        """Bind formal Task execution Fixtures while leaving Debug/Validation runs untouched."""

        attempt_id = parse_task_attempt_fixture_execution_id(fixture_run.execution_id)
        if attempt_id is None:
            return None
        if fixture_run.run_kind is not FixtureRunKind.EXECUTION:
            raise ResultHygieneProjectionError("RESULT_FIXTURE_BINDING_KIND_INVALID")

        attempt = await self._tasks.get_attempt(connection, attempt_id)
        if attempt is None:
            raise ResultHygieneProjectionError("RESULT_FIXTURE_BINDING_ATTEMPT_MISSING")
        unit = await self._tasks.get_unit(connection, attempt.execution_unit_id)
        if unit is None:
            raise ResultHygieneProjectionError("RESULT_FIXTURE_BINDING_UNIT_MISSING")
        self._require_binding_scope(
            unit=unit,
            attempt=attempt,
            fixture_run=fixture_run,
        )

        existing = await self._results.get_attempt_fixture_binding(
            connection,
            attempt.id,
        )
        by_fixture = await self._results.get_fixture_binding_by_run(
            connection,
            fixture_run.id,
        )
        if existing is not None or by_fixture is not None:
            stored = existing or by_fixture
            assert stored is not None
            self._require_exact_binding(
                stored,
                unit=unit,
                attempt=attempt,
                fixture_run=fixture_run,
            )
            return stored

        content = AttemptFixtureBindingContent(
            id=new_entity_id(),
            tenant_id=attempt.tenant_id,
            project_id=attempt.project_id,
            task_run_id=attempt.task_run_id,
            execution_unit_id=attempt.execution_unit_id,
            unit_attempt_id=attempt.id,
            fixture_run_id=fixture_run.id,
            fixture_blueprint_version_id=fixture_run.blueprint_version_id,
            environment_id=fixture_run.environment_id,
            fixture_plan_digest=fixture_run.plan_digest,
            created_at=created_at,
        )
        binding = AttemptFixtureBinding(
            **content.model_dump(mode="python"),
            binding_hash=attempt_fixture_binding_hash(content),
        )
        await self._results.insert_attempt_fixture_binding(connection, binding)
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=binding.tenant_id,
                aggregate_type="unit_attempt",
                aggregate_id=binding.unit_attempt_id,
                event_type="unit_attempt.fixture_bound",
                occurred_at=created_at,
                payload={
                    "attemptFixtureBindingId": str(binding.id),
                    "fixtureRunId": str(binding.fixture_run_id),
                    "bindingHash": binding.binding_hash,
                },
            ),
        )
        await self.project_unit(
            connection,
            unit=unit,
            created_at=created_at,
        )
        return binding

    async def project_fixture_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        fixture_run_id: UUID,
        created_at: datetime,
    ) -> UnitHygieneResolutionRevision | None:
        """Project a Fixture transition when it belongs to a formal Task Attempt."""

        binding = await self._results.get_fixture_binding_by_run(
            connection,
            fixture_run_id,
        )
        if binding is None:
            return None
        await self._tasks.lock_execution_chain(
            connection,
            task_run_id=binding.task_run_id,
            execution_unit_id=binding.execution_unit_id,
        )
        unit = await self._tasks.get_unit(connection, binding.execution_unit_id)
        if unit is None:
            raise ResultHygieneProjectionError("RESULT_HYGIENE_UNIT_MISSING")
        return await self.project_unit(
            connection,
            unit=unit,
            created_at=created_at,
        )

    async def project_unit(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        created_at: datetime,
    ) -> UnitHygieneResolutionRevision | None:
        """Append a revision only after every currently CLOSED Attempt has Cleanup truth."""

        attempts = tuple(
            attempt
            for attempt in await self._tasks.list_attempts(connection, unit.id)
            if attempt.lifecycle is ExecutionLifecycle.CLOSED
        )
        if not attempts:
            return None
        if tuple(item.attempt_number for item in attempts) != tuple(range(1, len(attempts) + 1)):
            raise ResultHygieneProjectionError("RESULT_HYGIENE_ATTEMPT_SEQUENCE_INVALID")

        bindings = {
            binding.unit_attempt_id: binding
            for binding in await self._results.list_fixture_bindings_for_unit(
                connection,
                unit.id,
            )
        }
        inputs: list[UnitHygieneResolutionInput] = []
        for attempt in attempts:
            self._require_attempt_scope(unit, attempt)
            binding = bindings.get(attempt.id)
            if binding is None:
                if attempt.hygiene is not ExecutionHygiene.NOT_REQUIRED:
                    return None
                inputs.append(_not_required_input(attempt))
                continue
            inputs.append(
                await self._fixture_input(
                    connection,
                    unit=unit,
                    attempt=attempt,
                    binding=binding,
                )
            )

        frozen_inputs = tuple(inputs)
        input_set_hash = unit_hygiene_input_set_hash(
            execution_unit_id=unit.id,
            manifest_hash=unit.manifest_hash,
            inputs=frozen_inputs,
        )
        latest = await self._results.get_latest_hygiene_resolution(
            connection,
            unit.id,
        )
        if latest is not None and (
            latest.input_set_hash == input_set_hash
            and latest.resolution_policy_digest == UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST
        ):
            return latest

        watermark = max(item.observed_at for item in frozen_inputs)
        if created_at < watermark:
            raise ResultHygieneProjectionError("RESULT_HYGIENE_WATERMARK_INVALID")
        content = UnitHygieneResolutionRevisionContent(
            id=new_entity_id(),
            unit_hygiene_resolution_id=(
                latest.unit_hygiene_resolution_id if latest is not None else new_entity_id()
            ),
            tenant_id=unit.tenant_id,
            project_id=unit.project_id,
            task_run_id=unit.task_run_id,
            execution_unit_id=unit.id,
            manifest_hash=unit.manifest_hash,
            unit_key=unit.unit_key,
            revision=(latest.revision + 1 if latest is not None else 1),
            inputs=frozen_inputs,
            input_set_hash=input_set_hash,
            data_hygiene=resolve_unit_data_hygiene(frozen_inputs),
            resolution_policy_version=UNIT_HYGIENE_RESOLUTION_POLICY_VERSION,
            resolution_policy_digest=UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
            supersedes_revision_id=(latest.id if latest is not None else None),
            projection_watermark=watermark,
            created_at=created_at,
        )
        resolution = UnitHygieneResolutionRevision(
            **content.model_dump(mode="python"),
            resolution_hash=unit_hygiene_resolution_hash(content),
        )
        await self._results.insert_hygiene_resolution(connection, resolution)
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=resolution.tenant_id,
                aggregate_type="execution_unit",
                aggregate_id=resolution.execution_unit_id,
                event_type="unit.hygiene_resolved",
                occurred_at=created_at,
                payload={
                    "unitHygieneResolutionRevisionId": str(resolution.id),
                    "revision": resolution.revision,
                    "inputSetHash": resolution.input_set_hash,
                    "dataHygiene": resolution.data_hygiene.value,
                    "resolutionHash": resolution.resolution_hash,
                    "projectionWatermark": (resolution.projection_watermark.isoformat()),
                },
            ),
        )
        return resolution

    async def _fixture_input(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        binding: AttemptFixtureBinding,
    ) -> UnitHygieneResolutionInput:
        fixture_run = await self._fixtures.get_run(connection, binding.fixture_run_id)
        if fixture_run is None:
            raise ResultHygieneProjectionError("RESULT_HYGIENE_FIXTURE_MISSING")
        self._require_binding_scope(
            unit=unit,
            attempt=attempt,
            fixture_run=fixture_run,
        )
        self._require_exact_binding(
            binding,
            unit=unit,
            attempt=attempt,
            fixture_run=fixture_run,
        )
        detail = await self._fixtures.get_detail(connection, fixture_run.id)
        resources = await self._fixtures.list_resources(connection, fixture_run.id)
        manifest = await self._fixtures.get_manifest(connection, fixture_run.id)
        if detail is None:
            raise ResultHygieneProjectionError("RESULT_HYGIENE_FIXTURE_DETAIL_MISSING")

        created_resources = tuple(
            item for item in resources.items if item.ownership is ResourceOwnership.CREATED
        )
        resource_counts = Counter(item.status for item in created_resources)
        cleaned_count = resource_counts[ResourceRecordStatus.CLEANED]
        leaked_count = resource_counts[ResourceRecordStatus.LEAKED]
        unresolved_count = len(created_resources) - cleaned_count - leaked_count
        uncertain_nodes = tuple(
            node for node in detail.nodes if node.status.value == "OUTCOME_UNCERTAIN"
        )
        exhausted_reconcile_count = sum(
            node.reconcile_state.value == "EXHAUSTED" for node in uncertain_nodes
        )
        unresolved_reconcile_count = sum(
            node.reconcile_state.value in {"PENDING", "RUNNING", "INCONCLUSIVE"}
            for node in uncertain_nodes
        )
        data_hygiene = _fixture_data_hygiene(
            fixture_run.cleanup_state,
            leaked_count=leaked_count,
            exhausted_reconcile_count=exhausted_reconcile_count,
        )
        resource_state_hash = result_projection_digest(
            {
                "schemaVersion": "atlas.fixture-cleanup-observation/0.1",
                "fixtureRunId": str(fixture_run.id),
                "fixtureRunRevision": fixture_run.revision,
                "cleanupGeneration": fixture_run.cleanup_generation,
                "cleanupState": fixture_run.cleanup_state.value,
                "resources": [
                    item.model_dump(mode="json", by_alias=True) for item in resources.items
                ],
                "cleanupAttempts": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in resources.cleanup_attempts
                ],
                "uncertainNodes": [
                    {
                        "id": str(node.id),
                        "nodeId": node.node_id,
                        "status": node.status.value,
                        "reconcileState": node.reconcile_state.value,
                        "reconcileAttemptCount": node.reconcile_attempt_count,
                        "revision": node.revision,
                        "updatedAt": node.updated_at.isoformat(),
                    }
                    for node in uncertain_nodes
                ],
            }
        )
        return UnitHygieneResolutionInput(
            unit_attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            source=UnitHygieneInputSource.FIXTURE_RUN,
            data_hygiene=data_hygiene,
            fixture_binding_id=binding.id,
            fixture_run_id=fixture_run.id,
            fixture_run_revision=fixture_run.revision,
            fixture_run_status=fixture_run.status.value,
            cleanup_generation=fixture_run.cleanup_generation,
            fixture_plan_digest=fixture_run.plan_digest,
            fixture_manifest_digest=(manifest.manifest_digest if manifest is not None else None),
            resource_state_hash=resource_state_hash,
            resource_count=len(created_resources),
            cleaned_resource_count=cleaned_count,
            leaked_resource_count=leaked_count,
            unresolved_resource_count=unresolved_count,
            exhausted_reconcile_count=exhausted_reconcile_count,
            unresolved_reconcile_count=unresolved_reconcile_count,
            observed_at=fixture_run.updated_at,
        )

    @staticmethod
    def _require_binding_scope(
        *,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        fixture_run: FixtureRun,
    ) -> None:
        if (
            attempt.tenant_id != unit.tenant_id
            or attempt.project_id != unit.project_id
            or attempt.task_run_id != unit.task_run_id
            or attempt.execution_unit_id != unit.id
            or fixture_run.tenant_id != unit.tenant_id
            or fixture_run.project_id != unit.project_id
            or fixture_run.environment_id != unit.environment_id
            or fixture_run.blueprint_version_id != unit.fixture_blueprint_version_id
            or fixture_run.execution_id != task_attempt_fixture_execution_id(attempt.id)
        ):
            raise ResultHygieneProjectionError("RESULT_FIXTURE_BINDING_SCOPE_INVALID")

    @staticmethod
    def _require_attempt_scope(unit: ExecutionUnit, attempt: UnitAttempt) -> None:
        if (
            attempt.tenant_id != unit.tenant_id
            or attempt.project_id != unit.project_id
            or attempt.task_run_id != unit.task_run_id
            or attempt.execution_unit_id != unit.id
            or attempt.manifest_hash != unit.manifest_hash
            or attempt.unit_key != unit.unit_key
        ):
            raise ResultHygieneProjectionError("RESULT_HYGIENE_ATTEMPT_SCOPE_INVALID")

    @staticmethod
    def _require_exact_binding(
        binding: AttemptFixtureBinding,
        *,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        fixture_run: FixtureRun,
    ) -> None:
        if (
            binding.tenant_id != attempt.tenant_id
            or binding.project_id != attempt.project_id
            or binding.task_run_id != attempt.task_run_id
            or binding.execution_unit_id != unit.id
            or binding.unit_attempt_id != attempt.id
            or binding.fixture_run_id != fixture_run.id
            or binding.fixture_blueprint_version_id != fixture_run.blueprint_version_id
            or binding.environment_id != fixture_run.environment_id
            or binding.fixture_plan_digest != fixture_run.plan_digest
        ):
            raise ResultHygieneProjectionError("RESULT_FIXTURE_BINDING_REPLAY_CONFLICT")


def _not_required_input(attempt: UnitAttempt) -> UnitHygieneResolutionInput:
    observed_at = attempt.cleanup_resolved_at or attempt.updated_at
    return UnitHygieneResolutionInput(
        unit_attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        source=UnitHygieneInputSource.EXPLICIT_NOT_REQUIRED,
        data_hygiene=DataHygiene.NOT_APPLICABLE,
        resource_state_hash=result_projection_digest(
            {
                "schemaVersion": "atlas.explicit-no-cleanup/0.1",
                "unitAttemptId": str(attempt.id),
                "attemptNumber": attempt.attempt_number,
                "hygiene": attempt.hygiene.value,
                "updatedAt": attempt.updated_at.isoformat(),
            }
        ),
        resource_count=0,
        cleaned_resource_count=0,
        leaked_resource_count=0,
        unresolved_resource_count=0,
        exhausted_reconcile_count=0,
        unresolved_reconcile_count=0,
        observed_at=observed_at,
    )


def _fixture_data_hygiene(
    cleanup_state: FixtureCleanupState,
    *,
    leaked_count: int,
    exhausted_reconcile_count: int,
) -> DataHygiene:
    if cleanup_state is FixtureCleanupState.NOT_REQUIRED:
        return DataHygiene.NOT_APPLICABLE
    if cleanup_state is FixtureCleanupState.CLEANED:
        return DataHygiene.CLEANED
    if cleanup_state is FixtureCleanupState.LEAKED:
        if not leaked_count and not exhausted_reconcile_count:
            raise ResultHygieneProjectionError("RESULT_HYGIENE_LEAK_FACT_MISSING")
        return DataHygiene.LEAKED
    return DataHygiene.PENDING


__all__ = [
    "ResultHygieneProjectionError",
    "ResultHygieneProjectionService",
]
