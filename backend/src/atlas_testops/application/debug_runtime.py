"""Trusted state writer for DebugRun binding, execution, and evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.ports.browser_runtime import BrowserContextEnvelopeCodec
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import (
    DebugRun,
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
    canonical_digest,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    AssertionStatus,
    BindDebugExecution,
    BrowserContextRestoreDescriptor,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceManifest,
    ExecutionActorBinding,
    ExecutionContract,
    FinalizeDebugEvidence,
    FixtureExecutionBinding,
    OracleOutcome,
    build_evidence_manifest,
    build_execution_contract,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.browser_runtime import (
    BrowserRuntimeReportRepository,
)
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
from atlas_testops.infrastructure.repositories.runtime import RuntimeRepository

_BROWSER_LIVE_PAYLOAD_KEYS: dict[BrowserRuntimeReportKind, tuple[str, ...]] = {
    BrowserRuntimeReportKind.EXECUTION_STARTED: (),
    BrowserRuntimeReportKind.NODE_STARTED: ("nodeId", "nodeKind", "versionRef"),
    BrowserRuntimeReportKind.OBSERVATION_CAPTURED: (
        "observationRef",
        "pageRef",
        "pageRevision",
        "routeKey",
        "targetCount",
    ),
    BrowserRuntimeReportKind.ACTION_PROPOSED: (
        "action",
        "risk",
        "nodeId",
        "targetRef",
        "routeKey",
    ),
    BrowserRuntimeReportKind.POLICY_DECIDED: ("decision", "matchedRules"),
    BrowserRuntimeReportKind.ACTION_EXECUTED: (
        "receiptId",
        "action",
        "status",
        "resultingPageRevision",
    ),
    BrowserRuntimeReportKind.ARTIFACT_CAPTURED: (
        "artifactId",
        "kind",
        "sizeBytes",
        "integrity",
    ),
    BrowserRuntimeReportKind.ASSERTION_EVALUATED: ("assertionId", "status"),
    BrowserRuntimeReportKind.NODE_COMPLETED: (
        "nodeId",
        "assertionResultCount",
        "artifactCount",
    ),
    BrowserRuntimeReportKind.EXECUTION_BLOCKED: ("failureType",),
    BrowserRuntimeReportKind.EXECUTION_COMPLETED: (
        "assertionResultCount",
        "artifactCount",
    ),
}


def _build_browser_live_event_payload(
    report: AppendBrowserRuntimeReport,
) -> dict[str, JsonValue]:
    safe_summary = report.payload.get("safeSummary")
    payload: dict[str, JsonValue] = {
        "reportId": str(report.report_id),
        "reportSequence": report.sequence,
        "reportKind": report.kind.value,
        "chainDigest": report.chain_digest,
    }
    if report.actor_slot is not None:
        payload["actorSlot"] = report.actor_slot
    if report.action_id is not None:
        payload["actionId"] = str(report.action_id)
    if isinstance(safe_summary, str):
        payload["safeSummary"] = safe_summary[:500]
    for key in _BROWSER_LIVE_PAYLOAD_KEYS[report.kind]:
        value = report.payload[key]
        if key == "matchedRules" and isinstance(value, list):
            payload[key] = value[:64]
            payload["matchedRuleCount"] = len(value)
        else:
            payload[key] = value
    return payload


class DebugRuntimeService:
    """Advance DebugRun state only from database-verified runtime facts."""

    def __init__(
        self,
        database: Database,
        *,
        runtime_repository: RuntimeRepository | None = None,
        browser_report_repository: BrowserRuntimeReportRepository | None = None,
        browser_context_envelope_codec: BrowserContextEnvelopeCodec | None = None,
        debug_run_repository: DebugRunRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._runtime = runtime_repository or RuntimeRepository()
        self._browser_reports = browser_report_repository or BrowserRuntimeReportRepository()
        self._browser_context_envelope_codec = browser_context_envelope_codec
        self._runs = debug_run_repository or DebugRunRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def bind(
        self,
        tenant_id: UUID,
        run_id: UUID,
        command: BindDebugExecution,
    ) -> ExecutionContract:
        """Freeze exact lease, Fixture, browser, model, tool, and policy versions."""

        now = utc_now()
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"runtime-bind:{command.worker_identity}:{run_id}",
        )
        async with self._database.transaction(context) as connection:
            run = await self._require_run(connection, run_id, for_update=True)
            existing = await self._runtime.get_contract_for_run(connection, run.id)
            if existing is not None:
                self._require_contract_command(existing, command)
                return existing
            if run.lifecycle is not DebugRunLifecycle.CREATED:
                raise self._conflict("只有 CREATED DebugRun 可以冻结 ExecutionContract。")
            if run.cancel_requested_at is not None:
                raise self._conflict("已请求取消的 DebugRun 不能继续绑定 Runtime。")
            if run.snapshot_status is DebugRunSnapshotStatus.OUTDATED:
                raise self._conflict("已过期 DebugRun 不再绑定新的 Runtime。")
            if now >= run.execution_deadline:
                raise self._conflict("DebugRun 已越过 executionDeadline。")

            fixture_record = await self._runtime.get_fixture_binding(
                connection,
                command.fixture_run_id,
            )
            if fixture_record is None:
                raise self._binding_invalid("Fixture Manifest 不存在或不可见。")
            expected_execution_id = f"debug-run:{run.id}"
            fixture_contract = run.test_ir.fixture
            required_exports = set(fixture_contract.required_exports)
            if (
                fixture_record.tenant_id != run.tenant_id
                or fixture_record.project_id != run.project_id
                or fixture_record.environment_id != run.environment_id
                or fixture_record.blueprint_version_id != fixture_contract.blueprint_version_id
                or fixture_record.blueprint_version_ref != fixture_contract.blueprint_version_ref
                or fixture_record.blueprint_content_digest != fixture_contract.content_digest
                or fixture_record.run_kind != "EXECUTION"
                or fixture_record.status != "READY"
                or fixture_record.execution_id != expected_execution_id
                or fixture_record.execution_deadline < run.execution_deadline
                or not required_exports.issubset(fixture_record.exports)
            ):
                raise self._binding_invalid(
                    "FixtureRun 必须是同一 Execution 的 READY、exact Blueprint/Manifest。"
                )

            commands_by_slot = {item.actor_slot: item for item in command.actors}
            specifications_by_slot = {item.actor_slot: item for item in run.test_ir.actors}
            if len(commands_by_slot) != 1:
                raise self._binding_invalid(
                    "P6-01 Browser Worker 仅接受一个 active actor slot。"
                )
            if set(commands_by_slot) != set(specifications_by_slot):
                raise self._binding_invalid("Actor slot 必须与冻结 Test IR 完全一致。")
            actors: list[ExecutionActorBinding] = []
            for actor_slot in sorted(specifications_by_slot):
                actor_command = commands_by_slot[actor_slot]
                specification = specifications_by_slot[actor_slot]
                record = await self._runtime.get_actor_binding(
                    connection,
                    fixture_run_id=fixture_record.fixture_run_id,
                    actor_slot=actor_slot,
                    account_lease_id=actor_command.account_lease_id,
                    browser_context_ref=actor_command.browser_context_ref,
                )
                if record is None or (
                    record.role_id != specification.role_id
                    or record.role_key != specification.role_key
                    or record.role_revision != specification.role_revision
                    or record.role_status != "ACTIVE"
                    or record.fencing_token != actor_command.fencing_token
                    or record.lease_status != "ACTIVE"
                    or record.lease_worker_id != command.worker_identity
                    or record.lease_execution_id != expected_execution_id
                    or record.lease_expires_at < run.execution_deadline
                    or record.lease_max_expires_at < run.execution_deadline
                    or record.browser_context_ref != actor_command.browser_context_ref
                    or record.session_status != "READY"
                    or record.session_worker_identity != command.worker_identity
                    or record.session_expires_at < run.execution_deadline
                ):
                    raise self._binding_invalid(
                        f"Actor slot '{actor_slot}' 的 Role、Lease、Fence 或 Session 已失效。"
                    )
                actors.append(
                    ExecutionActorBinding(
                        actor_slot=record.actor_slot,
                        role_id=record.role_id,
                        role_key=record.role_key,
                        role_revision=record.role_revision,
                        account_lease_id=record.account_lease_id,
                        account_handle=record.account_handle,
                        fencing_token=record.fencing_token,
                        browser_context_ref=record.browser_context_ref,
                    )
                )

            fixture = FixtureExecutionBinding(
                fixture_run_id=fixture_record.fixture_run_id,
                blueprint_version_id=fixture_record.blueprint_version_id,
                blueprint_version_ref=fixture_record.blueprint_version_ref,
                blueprint_content_digest=fixture_record.blueprint_content_digest,
                fixture_plan_digest=fixture_record.fixture_plan_digest,
                fixture_manifest_digest=fixture_record.fixture_manifest_digest,
            )
            contract = build_execution_contract(
                contract_id=new_entity_id(),
                run=run,
                command=command,
                actors=tuple(actors),
                fixture=fixture,
                created_at=now,
            )
            await self._runtime.create_contract(connection, contract)
            bound = await self._runtime.bind_run(
                connection,
                run=run,
                contract=contract,
            )
            if bound is None:
                raise self._conflict("DebugRun 在绑定期间发生并发变化。")
            payload: dict[str, JsonValue] = {
                "executionContractId": str(contract.id),
                "executionContractDigest": contract.content_digest,
                "fixtureRunId": str(contract.fixture.fixture_run_id),
                "fixtureManifestDigest": contract.fixture.fixture_manifest_digest,
                "browserRevision": contract.browser.revision,
                "modelProfileRef": contract.model.model_profile_ref,
                "toolCatalogRef": contract.tools.tool_catalog_ref,
                "policyDigest": contract.tools.policy_digest,
            }
            await self._append_runtime_event(
                connection,
                run=bound,
                event_type="debug_run.execution_bound",
                payload=payload,
                occurred_at=now,
                request_id=context.request_id or "runtime-bind",
            )
            return contract

    async def mark_ready(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        execution_contract_id: UUID,
        execution_contract_digest: str,
        worker_identity: str | None = None,
    ) -> DebugRun:
        """Move a fully bound DebugRun to READY using exact contract identity."""

        return await self._transition(
            tenant_id,
            run_id,
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
            expected=DebugRunLifecycle.BINDING,
            target=DebugRunLifecycle.READY,
            event_type="debug_run.ready",
            worker_identity=worker_identity,
        )

    async def start_execution(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        execution_contract_id: UUID,
        execution_contract_digest: str,
        worker_identity: str | None = None,
    ) -> DebugRun:
        """Move READY to RUNNING before any browser side effect is accepted."""

        return await self._transition(
            tenant_id,
            run_id,
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
            expected=DebugRunLifecycle.READY,
            target=DebugRunLifecycle.RUNNING,
            event_type="debug_run.started",
            worker_identity=worker_identity,
        )

    async def get_browser_execution_bundle(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        worker_identity: str,
    ) -> BrowserExecutionBundle:
        """Project one protected, database-verified bundle into the Browser Worker."""

        codec = self._browser_context_envelope_codec
        if codec is None:
            raise ApplicationError(
                error_code=ErrorCode.DEBUG_RUNTIME_UNAVAILABLE,
                title="Browser Context Envelope 未配置",
                detail="控制面不能安全投递 SessionArtifact restore metadata。",
                status_code=503,
            )
        now = utc_now()
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"runtime-bundle:{worker_identity}:{run_id}",
        )
        async with self._database.transaction(context) as connection:
            run = await self._require_run(connection, run_id, for_update=True)
            contract = await self._runtime.get_contract_for_run(connection, run.id)
            if contract is None:
                raise self._conflict("DebugRun 尚未绑定 ExecutionContract。")
            self._require_worker(contract, worker_identity)
            if run.lifecycle not in {
                DebugRunLifecycle.BINDING,
                DebugRunLifecycle.READY,
                DebugRunLifecycle.RUNNING,
            }:
                raise self._conflict("当前 DebugRun 生命周期不能读取 Browser 执行包。")
            if run.cancel_requested_at is not None:
                raise self._conflict("已请求取消的 DebugRun 不再签发执行包。")
            if now >= contract.execution_deadline:
                raise self._conflict("ExecutionContract 已越过 executionDeadline。")
            fixture = await self._runtime.get_fixture_binding(
                connection,
                contract.fixture.fixture_run_id,
            )
            if fixture is None or (
                fixture.fixture_manifest_digest != contract.fixture.fixture_manifest_digest
                or fixture.status != "READY"
                or fixture.execution_deadline < contract.execution_deadline
            ):
                raise self._binding_invalid("Fixture Manifest 在执行前已失效。")
            records = await self._runtime.get_browser_context_restore_records(
                connection,
                contract.id,
            )
            records_by_slot = {item.actor_slot: item for item in records}
            actor_slots = {item.actor_slot for item in contract.actors}
            if set(records_by_slot) != actor_slots:
                raise self._binding_invalid("BrowserContext restore metadata 不完整。")
            envelopes = []
            for actor in contract.actors:
                record = records_by_slot[actor.actor_slot]
                if (
                    record.session_status != "READY"
                    or record.session_worker_identity != worker_identity
                    or record.session_expires_at < contract.execution_deadline
                    or record.browser_context_ref != actor.browser_context_ref
                    or record.lease_id != actor.account_lease_id
                    or record.lease_fence != actor.fencing_token
                ):
                    raise self._binding_invalid(
                        f"Actor slot '{actor.actor_slot}' 的 BrowserContext 已失效。"
                    )
                descriptor = BrowserContextRestoreDescriptor(
                    actor_slot=record.actor_slot,
                    browser_context_ref=record.browser_context_ref,
                    artifact_id=record.artifact_id,
                    tenant_id=record.tenant_id,
                    project_id=record.project_id,
                    environment_id=record.environment_id,
                    lease_id=record.lease_id,
                    lease_fence=record.lease_fence,
                    account_id=record.account_id,
                    connector_installation_id=record.connector_installation_id,
                    credential_binding_id=record.credential_binding_id,
                    allowed_origins=record.allowed_origins,
                    object_ref=record.object_ref,
                    object_digest=record.object_digest,
                    key_version=record.key_version,
                    format_version=record.format_version,
                    expires_at=contract.execution_deadline,
                )
                envelopes.append(codec.seal(descriptor, contract=contract))
            return BrowserExecutionBundle(
                execution_contract=contract,
                test_ir=run.test_ir,
                plan_template=run.plan_template,
                fixture_exports=fixture.exports,
                restore_envelopes=tuple(envelopes),
                issued_at=now,
            )

    async def append_browser_report(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport:
        """Verify and append one Browser Worker fact without trusting its sequence."""

        now = utc_now()
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"runtime-report:{worker_identity}:{report.report_id}",
        )
        async with self._database.transaction(context) as connection:
            run = await self._require_run(connection, run_id, for_update=True)
            contract = await self._runtime.get_contract_for_run(connection, run.id)
            if contract is None:
                raise self._binding_invalid("DebugRun 缺少 ExecutionContract。")
            self._require_worker(contract, worker_identity)
            self._require_exact_contract(
                contract,
                report.execution_contract_id,
                report.execution_contract_digest,
            )
            if run.lifecycle is not DebugRunLifecycle.RUNNING:
                raise self._conflict("只有 RUNNING DebugRun 可以追加 Browser report。")
            if run.cancel_requested_at is not None:
                raise self._conflict("已请求取消的 DebugRun 不接受新的 Browser report。")
            if not contract.created_at <= report.occurred_at <= now:
                raise self._binding_invalid("Browser report 时间不在可信执行窗口内。")
            if now > contract.execution_deadline:
                raise self._binding_invalid("Browser report 超过 executionDeadline。")
            if report.actor_slot is not None and report.actor_slot not in {
                item.actor_slot for item in contract.actors
            }:
                raise self._binding_invalid("Browser report 引用了未知 Actor slot。")

            existing_by_id = await self._browser_reports.get_by_id(
                connection,
                report.report_id,
            )
            existing_by_sequence = await self._browser_reports.get_by_sequence(
                connection,
                execution_contract_id=contract.id,
                sequence=report.sequence,
            )
            existing = existing_by_id or existing_by_sequence
            if existing is not None:
                if (
                    existing.debug_run_id == run.id
                    and existing.value == report
                    and existing_by_id == existing_by_sequence
                ):
                    return existing
                raise self._conflict("Browser report ID 或 sequence 已被不同事实占用。")

            if (
                report.kind is BrowserRuntimeReportKind.ACTION_PROPOSED
                and report.action_id is not None
                and await self._browser_reports.action_exists(
                    connection,
                    execution_contract_id=contract.id,
                    action_id=report.action_id,
                )
            ):
                raise self._conflict("Browser actionId 在同一报告链中只能提出一次。")

            latest = await self._browser_reports.get_latest(connection, contract.id)
            expected_sequence = 1 if latest is None else latest.value.sequence + 1
            expected_previous = (
                "sha256:" + "0" * 64 if latest is None else latest.value.chain_digest
            )
            if (
                report.sequence != expected_sequence
                or report.previous_chain_digest != expected_previous
            ):
                raise self._conflict("Browser report 必须单调、无间隙地追加。")
            if report.sequence == 1:
                if report.kind is not BrowserRuntimeReportKind.EXECUTION_STARTED:
                    raise self._binding_invalid("Browser report 链必须从 execution.started 开始。")
            else:
                assert latest is not None
                if report.occurred_at < latest.value.occurred_at:
                    raise self._binding_invalid("Browser report occurredAt 必须单调递增。")
                if latest.value.kind is BrowserRuntimeReportKind.EXECUTION_COMPLETED:
                    raise self._conflict("execution.completed 之后不能追加 Browser report。")
                if report.kind is BrowserRuntimeReportKind.EXECUTION_STARTED:
                    raise self._conflict("Browser report 链只能包含一个 execution.started。")
                action_proposal = None
                if report.kind is BrowserRuntimeReportKind.ACTION_EXECUTED:
                    proposal_record = await self._browser_reports.get_by_sequence(
                        connection,
                        execution_contract_id=contract.id,
                        sequence=report.sequence - 2,
                    )
                    action_proposal = (
                        proposal_record.value if proposal_record is not None else None
                    )
                self._require_action_report_transition(
                    latest.value,
                    report,
                    action_proposal=action_proposal,
                )
            persisted = await self._browser_reports.append(
                connection,
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                environment_id=run.environment_id,
                debug_run_id=run.id,
                report=report,
                recorded_at=now,
            )
            await self._append_runtime_event(
                connection,
                run=run,
                event_type=f"debug_run.browser.{report.kind.value}",
                payload=_build_browser_live_event_payload(report),
                occurred_at=report.occurred_at,
                request_id=context.request_id or "runtime-report",
            )
            return persisted

    async def finalize_evidence(
        self,
        tenant_id: UUID,
        run_id: UUID,
        command: FinalizeDebugEvidence,
        *,
        worker_identity: str | None = None,
    ) -> tuple[DebugRun, EvidenceManifest]:
        """Finalize from Oracle facts; the command has no caller-supplied PASS field."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"runtime-finalize:{run_id}",
        )
        async with self._database.transaction(context) as connection:
            run = await self._require_run(connection, run_id, for_update=True)
            existing_manifest = (
                await self._runtime.get_evidence_manifest(
                    connection,
                    run.evidence_manifest_id,
                )
                if run.evidence_manifest_id is not None
                else None
            )
            if run.lifecycle is DebugRunLifecycle.TERMINATED:
                if existing_manifest is None:
                    raise self._conflict("终态 DebugRun 缺少可验证 Evidence Manifest。")
                contract = await self._runtime.get_contract_for_run(connection, run.id)
                if contract is None:
                    raise self._binding_invalid("终态 DebugRun 缺少 ExecutionContract。")
                if worker_identity is not None:
                    self._require_worker(contract, worker_identity)
                finalization_digest = await self._runtime.get_evidence_finalization_digest(
                    connection,
                    existing_manifest.id,
                )
                self._require_finalization_contract(
                    existing_manifest,
                    command,
                    finalization_digest=finalization_digest,
                )
                return run, existing_manifest
            if run.lifecycle is not DebugRunLifecycle.RUNNING:
                raise self._conflict("只有 RUNNING DebugRun 可以进入证据 Finalize。")
            contract = await self._runtime.get_contract_for_run(connection, run.id)
            if contract is None:
                raise self._binding_invalid("DebugRun 缺少 ExecutionContract。")
            self._require_exact_contract(
                contract,
                command.execution_contract_id,
                command.execution_contract_digest,
            )
            if worker_identity is not None:
                self._require_worker(contract, worker_identity)
            reports = await self._browser_reports.list_for_contract(
                connection,
                contract.id,
            )
            latest_report = reports[-1] if reports else None
            if latest_report is None or (
                latest_report.value.kind
                is not BrowserRuntimeReportKind.EXECUTION_COMPLETED
                or len(reports) != command.event_count
                or latest_report.value.sequence != command.event_count
                or latest_report.value.chain_digest != command.event_chain_head_digest
            ):
                raise self._binding_invalid(
                    "Evidence finalization 与已完成 Browser report 链不一致。"
                )
            self._require_reported_evidence(reports, command)
            if command.finalized_at < latest_report.value.occurred_at:
                raise self._binding_invalid(
                    "Evidence finalization 不能早于最后一个 Browser report。"
                )
            if command.finalized_at > run.execution_deadline:
                raise self._binding_invalid("Evidence finalization 超过 executionDeadline。")
            try:
                manifest, private_artifacts = build_evidence_manifest(
                    manifest_id=new_entity_id(),
                    run=run,
                    contract=contract,
                    command=command,
                )
            except ValueError as error:
                raise self._binding_invalid(str(error)) from error
            await self._runtime.persist_evidence(
                connection,
                contract=contract,
                manifest=manifest,
                private_artifacts=private_artifacts,
                finalization_command_digest=canonical_digest(command),
            )
            finalizing = await self._runtime.transition_run(
                connection,
                run=run,
                expected_lifecycle=DebugRunLifecycle.RUNNING,
                next_lifecycle=DebugRunLifecycle.FINALIZING,
            )
            if finalizing is None:
                raise self._conflict("DebugRun 在 Finalize 期间发生并发变化。")
            await self._append_runtime_event(
                connection,
                run=finalizing,
                event_type="debug_run.finalizing",
                payload={
                    "evidenceManifestId": str(manifest.id),
                    "evidenceManifestDigest": manifest.content_digest,
                    "completeness": manifest.completeness.value,
                    "integrity": manifest.integrity.value,
                },
                occurred_at=manifest.finalized_at,
                request_id=context.request_id or "runtime-finalize",
            )
            outcome = {
                OracleOutcome.PASSED: DebugRunOutcome.PASSED,
                OracleOutcome.FAILED: DebugRunOutcome.FAILED,
                OracleOutcome.INCONCLUSIVE: DebugRunOutcome.INCONCLUSIVE,
            }[manifest.outcome]
            terminated = await self._runtime.finish_run(
                connection,
                run=finalizing,
                outcome=outcome,
                manifest=manifest,
            )
            if terminated is None:
                raise self._conflict("DebugRun 无法原子提交终态结果。")
            await self._append_runtime_event(
                connection,
                run=terminated,
                event_type="debug_run.terminated",
                payload={
                    "outcome": terminated.outcome.value,
                    "evidenceManifestId": str(manifest.id),
                    "evidenceManifestDigest": manifest.content_digest,
                    "oracleResultsDigest": manifest.oracle_results_digest,
                    "artifactManifestDigest": manifest.artifact_manifest_digest,
                },
                occurred_at=manifest.finalized_at,
                request_id=context.request_id or "runtime-finalize",
            )
            return terminated, manifest

    def _require_reported_evidence(
        self,
        reports: tuple[BrowserRuntimeReport, ...],
        command: FinalizeDebugEvidence,
    ) -> None:
        completed = reports[-1].value.payload
        if (
            completed.get("assertionResultCount") != len(command.assertion_results)
            or completed.get("artifactCount") != len(command.artifacts)
        ):
            raise self._binding_invalid(
                "execution.completed 计数与 finalization command 不一致。"
            )
        reported_assertions = [
            (
                report.value.payload.get("assertionId"),
                report.value.payload.get("assertionInputDigest"),
            )
            for report in reports
            if report.value.kind is BrowserRuntimeReportKind.ASSERTION_EVALUATED
        ]
        command_assertions = [
            (item.assertion_id, canonical_digest(item))
            for item in command.assertion_results
        ]
        if sorted(reported_assertions) != sorted(command_assertions):
            raise self._binding_invalid(
                "finalization assertionResults 必须与 Browser report 完全一致。"
            )
        reported_artifacts = [
            (
                report.value.payload.get("artifactId"),
                report.value.payload.get("artifactInputDigest"),
            )
            for report in reports
            if report.value.kind is BrowserRuntimeReportKind.ARTIFACT_CAPTURED
        ]
        command_artifacts = [
            (str(item.id), canonical_digest(item))
            for item in command.artifacts
        ]
        if sorted(reported_artifacts, key=str) != sorted(command_artifacts, key=str):
            raise self._binding_invalid(
                "finalization artifacts 必须与 Browser report 完全一致。"
            )
        unsafe_execution = any(
            report.value.kind is BrowserRuntimeReportKind.EXECUTION_BLOCKED
            or (
                report.value.kind is BrowserRuntimeReportKind.ACTION_EXECUTED
                and report.value.payload.get("status") != "SUCCEEDED"
            )
            for report in reports
        )
        if unsafe_execution and any(
            item.status is not AssertionStatus.INCONCLUSIVE
            for item in command.assertion_results
        ):
            raise self._binding_invalid(
                "Blocked 或结果不确定的 Browser 执行只能终结为 INCONCLUSIVE。"
            )

    def _require_action_report_transition(
        self,
        previous: AppendBrowserRuntimeReport,
        current: AppendBrowserRuntimeReport,
        *,
        action_proposal: AppendBrowserRuntimeReport | None,
    ) -> None:
        if previous.kind is BrowserRuntimeReportKind.ACTION_PROPOSED and (
            current.kind is not BrowserRuntimeReportKind.POLICY_DECIDED
            or previous.action_id != current.action_id
            or previous.actor_slot != current.actor_slot
        ):
            raise self._binding_invalid(
                "policy.decided 必须紧随同一 Actor/action 的 action.proposed。"
            )
        if (
            current.kind is BrowserRuntimeReportKind.POLICY_DECIDED
            and previous.kind is not BrowserRuntimeReportKind.ACTION_PROPOSED
        ):
            raise self._binding_invalid(
                "policy.decided 必须紧随同一 Actor/action 的 action.proposed。"
            )
        if previous.kind is BrowserRuntimeReportKind.POLICY_DECIDED:
            decision = previous.payload.get("decision")
            if current.kind is BrowserRuntimeReportKind.EXECUTION_BLOCKED:
                return
            if (
                decision != "ALLOW"
                or current.kind is not BrowserRuntimeReportKind.ACTION_EXECUTED
                or previous.action_id != current.action_id
                or previous.actor_slot != current.actor_slot
                or action_proposal is None
                or action_proposal.kind
                is not BrowserRuntimeReportKind.ACTION_PROPOSED
                or action_proposal.action_id != current.action_id
                or action_proposal.actor_slot != current.actor_slot
                or action_proposal.payload.get("action")
                != current.payload.get("action")
            ):
                raise self._binding_invalid(
                    "ALLOW policy 后只能追加同一 Proposal 的 action receipt。"
                )
        elif current.kind is BrowserRuntimeReportKind.ACTION_EXECUTED:
            raise self._binding_invalid(
                "action.executed 必须紧随一个 ALLOW policy decision。"
            )

    async def _transition(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        execution_contract_id: UUID,
        execution_contract_digest: str,
        expected: DebugRunLifecycle,
        target: DebugRunLifecycle,
        event_type: str,
        worker_identity: str | None,
    ) -> DebugRun:
        now = utc_now()
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"runtime-transition:{target.value}:{run_id}",
        )
        async with self._database.transaction(context) as connection:
            run = await self._require_run(connection, run_id, for_update=True)
            contract = await self._runtime.get_contract_for_run(connection, run.id)
            if contract is None:
                raise self._binding_invalid("DebugRun 缺少 ExecutionContract。")
            self._require_exact_contract(
                contract,
                execution_contract_id,
                execution_contract_digest,
            )
            if worker_identity is not None:
                self._require_worker(contract, worker_identity)
            if run.lifecycle is target:
                return run
            if run.lifecycle is not expected:
                raise self._conflict(f"DebugRun 必须从 {expected.value} 进入 {target.value}。")
            if run.cancel_requested_at is not None:
                raise self._conflict("已请求取消的 DebugRun 不接受新的执行动作。")
            if now >= run.execution_deadline:
                raise self._conflict("DebugRun 已越过 executionDeadline。")
            transitioned = await self._runtime.transition_run(
                connection,
                run=run,
                expected_lifecycle=expected,
                next_lifecycle=target,
            )
            if transitioned is None:
                raise self._conflict("DebugRun 状态在推进期间发生并发变化。")
            await self._append_runtime_event(
                connection,
                run=transitioned,
                event_type=event_type,
                payload={
                    "executionContractId": str(contract.id),
                    "executionContractDigest": contract.content_digest,
                },
                occurred_at=now,
                request_id=context.request_id or "runtime-transition",
            )
            return transitioned

    async def _require_run(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
        *,
        for_update: bool,
    ) -> DebugRun:
        run = await self._runs.get_run(connection, run_id, for_update=for_update)
        if run is None:
            raise ApplicationError(
                error_code=ErrorCode.NOT_FOUND,
                title="DebugRun 不存在",
                detail="受信 Runtime 无法读取该 Tenant 下的 DebugRun。",
                status_code=404,
            )
        return run

    async def _append_runtime_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: DebugRun,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        await self._runs.append_event(
            connection,
            run=run,
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at,
        )
        await self._audit.append(
            connection,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            environment_id=run.environment_id,
            actor_id=None,
            event_type=event_type,
            entity_type="debug_run",
            entity_id=run.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=run.tenant_id,
                aggregate_type="debug_run",
                aggregate_id=run.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_contract_command(
        contract: ExecutionContract,
        command: BindDebugExecution,
    ) -> None:
        requested_actors = tuple(
            (
                item.actor_slot,
                item.account_lease_id,
                item.fencing_token,
                item.browser_context_ref,
            )
            for item in command.actors
        )
        frozen_actors = tuple(
            (
                item.actor_slot,
                item.account_lease_id,
                item.fencing_token,
                item.browser_context_ref,
            )
            for item in contract.actors
        )
        if (
            command.worker_identity != contract.worker_identity
            or command.fixture_run_id != contract.fixture.fixture_run_id
            or command.browser != contract.browser
            or command.model != contract.model
            or command.tools != contract.tools
            or requested_actors != frozen_actors
        ):
            raise DebugRuntimeService._conflict(
                "DebugRun 已绑定不同的 ExecutionContract；禁止覆盖。"
            )

    @staticmethod
    def _require_exact_contract(
        contract: ExecutionContract,
        contract_id: UUID,
        contract_digest: str,
    ) -> None:
        if contract.id != contract_id or contract.content_digest != contract_digest:
            raise DebugRuntimeService._binding_invalid("ExecutionContract ID 或 Digest 不匹配。")

    @staticmethod
    def _require_finalization_contract(
        manifest: EvidenceManifest,
        command: FinalizeDebugEvidence,
        *,
        finalization_digest: str | None,
    ) -> None:
        if (
            manifest.execution_contract_id != command.execution_contract_id
            or manifest.execution_contract_digest != command.execution_contract_digest
            or finalization_digest is None
            or finalization_digest != canonical_digest(command)
        ):
            raise DebugRuntimeService._conflict(
                "终态 DebugRun 已由不同 finalization command 封存。"
            )

    @staticmethod
    def _require_worker(contract: ExecutionContract, worker_identity: str) -> None:
        if contract.worker_identity != worker_identity:
            raise DebugRuntimeService._binding_invalid(
                "Worker identity 与冻结 ExecutionContract 不匹配。"
            )

    @staticmethod
    def _binding_invalid(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.VALIDATION_FAILED,
            title="可信 Runtime 绑定无效",
            detail=detail,
            status_code=422,
        )

    @staticmethod
    def _conflict(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="DebugRun Runtime 状态冲突",
            detail=detail,
            status_code=409,
        )
