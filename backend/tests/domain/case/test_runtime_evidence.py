"""ExecutionContract, deterministic Oracle, and EvidenceManifest invariants."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.case import (
    DebugRun,
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
    compile_case,
)
from atlas_testops.domain.case import (
    TestIntent as CaseIntent,
)
from atlas_testops.domain.runtime import (
    AssertionResultInput,
    AssertionStatus,
    BindDebugExecution,
    BindExecutionActor,
    BrowserExecutionProfile,
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionActorBinding,
    ExecutionContract,
    FinalizeDebugEvidence,
    FixtureExecutionBinding,
    ModelExecutionProfile,
    OracleOutcome,
    ToolExecutionProfile,
    Viewport,
    build_assertion_result,
    build_evidence_manifest,
    build_execution_contract,
    evidence_manifest_digest,
    expected_assertion_digest,
    json_body,
)
from atlas_testops.domain.workflow import OracleStrength, WorkflowGraph

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("20000000-0000-4000-8000-000000000002")
ENVIRONMENT_ID = UUID("30000000-0000-4000-8000-000000000003")
CASE_ID = UUID("40000000-0000-4000-8000-000000000004")
RUN_ID = UUID("50000000-0000-4000-8000-000000000005")
CONTRACT_ID = UUID("60000000-0000-4000-8000-000000000006")
FIXTURE_RUN_ID = UUID("70000000-0000-4000-8000-000000000007")
LEASE_ID = UUID("80000000-0000-4000-8000-000000000008")
ARTIFACT_ID = UUID("90000000-0000-4000-8000-000000000009")
MANIFEST_ID = UUID("a0000000-0000-4000-8000-00000000000a")


def _run(
    graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> DebugRun:
    compilation = compile_case(
        test_case_id=CASE_ID,
        semantic_revision=3,
        intent_version_ref="intent.customer-filter@1.0.0",
        intent=intent_factory(),
        graph=graph,
    )
    assert compilation.test_ir is not None
    assert compilation.plan_template is not None
    assert compilation.compiled_digest is not None
    now = datetime.now(UTC)
    return DebugRun(
        id=RUN_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        test_case_id=CASE_ID,
        draft_id=UUID("b0000000-0000-4000-8000-00000000000b"),
        semantic_revision=3,
        semantic_digest=DIGEST_A,
        compiled_digest=compilation.compiled_digest,
        test_ir=compilation.test_ir,
        test_ir_digest=compilation.test_ir.content_digest,
        plan_template=compilation.plan_template,
        plan_digest=compilation.plan_template.plan_digest,
        lifecycle=DebugRunLifecycle.CREATED,
        outcome=DebugRunOutcome.NOT_SET,
        snapshot_status=DebugRunSnapshotStatus.CURRENT,
        temporal_workflow_id=f"atlas-debug/{TENANT_ID}/{RUN_ID}",
        requested_by=None,
        execution_deadline=now + timedelta(minutes=20),
        requested_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )


def _bind_command() -> BindDebugExecution:
    return BindDebugExecution(
        worker_identity="browser-worker-01",
        fixture_run_id=FIXTURE_RUN_ID,
        actors=(
            BindExecutionActor(
                actor_slot="operator",
                account_lease_id=LEASE_ID,
                fencing_token=7,
                browser_context_ref="bctx_" + "x" * 40,
            ),
        ),
        browser=BrowserExecutionProfile(
            revision="chromium-140.0.7339.16",
            viewport=Viewport(width=1440, height=900),
            locale="zh-CN",
            timezone="Asia/Shanghai",
        ),
        model=ModelExecutionProfile(
            model_profile_ref="model.browser-agent@1.0.0",
            prompt_bundle_ref="prompt.browser-agent@1.0.0",
            reasoning_policy_ref="reasoning.bounded@1.0.0",
        ),
        tools=ToolExecutionProfile(
            tool_catalog_ref="tools.browser-safe@1.0.0",
            mcp_server_manifest_digest=DIGEST_A,
            tool_schema_digest=DIGEST_B,
            policy_bundle_ref="policy.browser-test@1.0.0",
            policy_digest=DIGEST_C,
        ),
    )


def _contract(run: DebugRun, created_at: datetime) -> ExecutionContract:
    actor = run.test_ir.actors[0]
    return build_execution_contract(
        contract_id=CONTRACT_ID,
        run=run,
        command=_bind_command(),
        actors=(
            ExecutionActorBinding(
                actor_slot=actor.actor_slot,
                role_id=actor.role_id,
                role_key=actor.role_key,
                role_revision=actor.role_revision,
                account_lease_id=LEASE_ID,
                account_handle="ah_" + "z" * 32,
                fencing_token=7,
                browser_context_ref="bctx_" + "x" * 40,
            ),
        ),
        fixture=FixtureExecutionBinding(
            fixture_run_id=FIXTURE_RUN_ID,
            blueprint_version_id=run.test_ir.fixture.blueprint_version_id,
            blueprint_version_ref=run.test_ir.fixture.blueprint_version_ref,
            blueprint_content_digest=run.test_ir.fixture.content_digest,
            fixture_plan_digest=DIGEST_B,
            fixture_manifest_digest=DIGEST_C,
        ),
        created_at=created_at,
    )


def _artifact(now: datetime) -> EvidenceArtifactInput:
    return EvidenceArtifactInput(
        id=ARTIFACT_ID,
        kind=EvidenceArtifactKind.SCREENSHOT,
        object_ref="evidence://tenant/project/run/assertion.png",
        content_digest=DIGEST_A,
        size_bytes=1024,
        mime_type="image/png",
        redaction_policy_digest=DIGEST_B,
        integrity=EvidenceIntegrity.VERIFIED,
        required=True,
        captured_at=now,
    )


def _assertion(
    run: DebugRun,
    now: datetime,
    *,
    status: AssertionStatus = AssertionStatus.PASSED,
    evidence: bool = True,
) -> AssertionResultInput:
    specification = run.test_ir.assertions[0]
    return AssertionResultInput(
        assertion_id=specification.assertion_id,
        status=status,
        expected_digest=expected_assertion_digest(
            run.test_ir,
            specification.assertion_id,
        ),
        actual_safe_summary="The visible customer set matched the expected role scope.",
        evaluator_version_ref=specification.evaluator_version_ref,
        evidence_refs=(ARTIFACT_ID,) if evidence else (),
        observed_at=now,
        duration_ms=240,
    )


def test_execution_contract_and_complete_hard_oracle_produce_verified_pass(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    now = run.requested_at + timedelta(seconds=1)
    contract = _contract(run, now)
    manifest, private_artifacts = build_evidence_manifest(
        manifest_id=MANIFEST_ID,
        run=run,
        contract=contract,
        command=FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            assertion_results=(_assertion(run, now),),
            artifacts=(_artifact(now),),
            event_chain_head_digest=DIGEST_C,
            event_count=8,
            finalized_at=now + timedelta(seconds=1),
        ),
    )

    assert manifest.outcome is OracleOutcome.PASSED
    assert manifest.completeness is EvidenceCompleteness.COMPLETE
    assert manifest.integrity is EvidenceIntegrity.VERIFIED
    assert manifest.passed_assertions == 1
    assert manifest.missing_assertion_ids == ()
    assert private_artifacts[0].object_ref.startswith("evidence://")
    assert "objectRef" not in manifest.model_dump(mode="json", by_alias=True)["artifacts"][0]


def test_oracle_rejects_changed_expected_program_and_never_accepts_worker_outcome(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    now = run.requested_at + timedelta(seconds=1)
    contract = _contract(run, now)
    assertion = _assertion(run, now).model_copy(update={"expected_digest": DIGEST_A})

    with pytest.raises(ValueError, match="expectedDigest"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=FinalizeDebugEvidence(
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                assertion_results=(assertion,),
                artifacts=(_artifact(now),),
                event_chain_head_digest=DIGEST_C,
                event_count=3,
                finalized_at=now + timedelta(seconds=1),
            ),
        )

    with pytest.raises(ValidationError):
        FinalizeDebugEvidence.model_validate(
            {
                "executionContractId": str(contract.id),
                "executionContractDigest": contract.content_digest,
                "eventChainHeadDigest": DIGEST_C,
                "eventCount": 1,
                "finalizedAt": now.isoformat(),
                "outcome": "PASSED",
            }
        )


def test_missing_hard_evidence_is_inconclusive_but_hard_failure_remains_failed(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    now = run.requested_at + timedelta(seconds=1)
    contract = _contract(run, now)

    incomplete, _ = build_evidence_manifest(
        manifest_id=MANIFEST_ID,
        run=run,
        contract=contract,
        command=FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            assertion_results=(_assertion(run, now, evidence=False),),
            event_chain_head_digest=DIGEST_C,
            event_count=2,
            finalized_at=now + timedelta(seconds=1),
        ),
    )
    assert incomplete.outcome is OracleOutcome.INCONCLUSIVE
    assert incomplete.completeness is EvidenceCompleteness.MISSING

    failed, _ = build_evidence_manifest(
        manifest_id=UUID("c0000000-0000-4000-8000-00000000000c"),
        run=run,
        contract=contract,
        command=FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            assertion_results=(
                _assertion(run, now, status=AssertionStatus.FAILED),
            ),
            artifacts=(_artifact(now),),
            event_chain_head_digest=DIGEST_C,
            event_count=4,
            finalized_at=now + timedelta(seconds=1),
        ),
    )
    assert failed.outcome is OracleOutcome.FAILED


def test_execution_contract_digest_detects_runtime_profile_tampering(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    contract = _contract(run, run.requested_at + timedelta(seconds=1))
    payload = contract.model_dump(mode="json", by_alias=True)
    payload["browser"]["revision"] = "chromium-tampered"

    with pytest.raises(ValidationError, match="contentDigest"):
        type(contract).model_validate(payload)


def test_evidence_timestamps_must_stay_inside_the_frozen_execution_window(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    contract_created_at = run.requested_at + timedelta(seconds=1)
    contract = _contract(run, contract_created_at)
    observed_at = contract_created_at + timedelta(seconds=2)

    with pytest.raises(ValueError, match="assertion observation"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=FinalizeDebugEvidence(
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                assertion_results=(_assertion(run, observed_at),),
                artifacts=(_artifact(observed_at),),
                event_chain_head_digest=DIGEST_C,
                event_count=2,
                finalized_at=observed_at - timedelta(seconds=1),
            ),
        )


def test_runtime_wire_contracts_reject_duplicate_and_tampered_facts(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    now = run.requested_at + timedelta(seconds=1)
    bind_command = _bind_command()
    contract = _contract(run, now)
    artifact = _artifact(now)
    assertion = _assertion(run, now)
    manifest, _ = build_evidence_manifest(
        manifest_id=MANIFEST_ID,
        run=run,
        contract=contract,
        command=FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            assertion_results=(assertion,),
            artifacts=(artifact,),
            event_chain_head_digest=DIGEST_C,
            event_count=2,
            finalized_at=now + timedelta(seconds=1),
        ),
    )

    with pytest.raises(ValidationError, match="actor slots"):
        BindDebugExecution.model_validate(
            {
                **bind_command.model_dump(mode="json", by_alias=True),
                "actors": [
                    bind_command.actors[0].model_dump(mode="json", by_alias=True),
                    bind_command.actors[0].model_dump(mode="json", by_alias=True),
                ],
            }
        )
    contract_payload = contract.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="unique slots"):
        type(contract).model_validate(
            {**contract_payload, "actors": [*contract_payload["actors"]] * 2}
        )
    with pytest.raises(ValidationError, match="predate"):
        type(contract).model_validate(
            {
                **contract_payload,
                "createdAt": contract_payload["executionDeadline"],
            }
        )
    with pytest.raises(ValidationError, match="evidenceRefs"):
        AssertionResultInput.model_validate(
            {
                **assertion.model_dump(mode="json", by_alias=True),
                "evidenceRefs": [str(ARTIFACT_ID), str(ARTIFACT_ID)],
            }
        )
    result_payload = manifest.assertion_results[0].model_dump(
        mode="json",
        by_alias=True,
    )
    with pytest.raises(ValidationError, match="resultDigest"):
        type(manifest.assertion_results[0]).model_validate(
            {**result_payload, "resultDigest": DIGEST_A}
        )
    finalize_payload = {
        "executionContractId": str(contract.id),
        "executionContractDigest": contract.content_digest,
        "assertionResults": [assertion.model_dump(mode="json", by_alias=True)] * 2,
        "artifacts": [artifact.model_dump(mode="json", by_alias=True)],
        "eventChainHeadDigest": DIGEST_C,
        "eventCount": 2,
        "finalizedAt": (now + timedelta(seconds=1)).isoformat(),
    }
    with pytest.raises(ValidationError, match="unique assertion"):
        FinalizeDebugEvidence.model_validate(finalize_payload)
    with pytest.raises(ValidationError, match="unique IDs"):
        FinalizeDebugEvidence.model_validate(
            {
                **finalize_payload,
                "assertionResults": [assertion.model_dump(mode="json", by_alias=True)],
                "artifacts": [artifact.model_dump(mode="json", by_alias=True)] * 2,
            }
        )

    manifest_payload = manifest.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="sorted and unique"):
        type(manifest).model_validate(
            {
                **manifest_payload,
                "assertionResults": [*manifest_payload["assertionResults"]] * 2,
            }
        )
    with pytest.raises(ValidationError, match="artifacts must be sorted"):
        type(manifest).model_validate(
            {
                **manifest_payload,
                "artifacts": [*manifest_payload["artifacts"]] * 2,
            }
        )
    with pytest.raises(ValidationError, match="counts"):
        type(manifest).model_validate({**manifest_payload, "passedAssertions": 0})
    with pytest.raises(ValidationError, match="oracleResultsDigest"):
        type(manifest).model_validate({**manifest_payload, "oracleResultsDigest": DIGEST_A})
    with pytest.raises(ValidationError, match="artifactManifestDigest"):
        type(manifest).model_validate(
            {**manifest_payload, "artifactManifestDigest": DIGEST_A}
        )
    with pytest.raises(ValidationError, match="contentDigest"):
        type(manifest).model_validate({**manifest_payload, "contentDigest": DIGEST_A})
    invalid_pass = manifest.model_copy(
        update={"completeness": EvidenceCompleteness.MISSING}
    )
    invalid_pass_payload = invalid_pass.model_dump(mode="json", by_alias=True)
    invalid_pass_payload["contentDigest"] = evidence_manifest_digest(invalid_pass)
    with pytest.raises(ValidationError, match="complete and verified"):
        type(manifest).model_validate(invalid_pass_payload)
    assert json_body(manifest)["contentDigest"] == manifest.content_digest


def test_oracle_rejects_unknown_or_stale_inputs_and_derives_partial_soft_evidence(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    now = run.requested_at + timedelta(seconds=1)
    contract = _contract(run, now)
    assertion = _assertion(run, now)

    with pytest.raises(ValueError, match="not part"):
        expected_assertion_digest(run.test_ir, "assertion:unknown")
    graph_without_assertion = run.test_ir.workflow.model_copy(
        update={
            "nodes": tuple(
                node
                for node in run.test_ir.workflow.nodes
                if node.id != run.test_ir.assertions[0].node_id
            )
        }
    )
    ir_without_assertion_node = run.test_ir.model_copy(
        update={"workflow": graph_without_assertion}
    )
    with pytest.raises(ValueError, match="node is missing"):
        expected_assertion_digest(
            ir_without_assertion_node,
            run.test_ir.assertions[0].assertion_id,
        )
    with pytest.raises(ValueError, match="not declared"):
        build_assertion_result(
            test_ir=run.test_ir,
            value=assertion.model_copy(update={"assertion_id": "assertion:unknown"}),
        )
    with pytest.raises(ValueError, match="evaluator version"):
        build_assertion_result(
            test_ir=run.test_ir,
            value=assertion.model_copy(
                update={"evaluator_version_ref": "assert.other@1.0.0"}
            ),
        )
    base_finalize = FinalizeDebugEvidence(
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        event_chain_head_digest=DIGEST_C,
        event_count=2,
        finalized_at=now + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="different execution contract"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=base_finalize.model_copy(update={"execution_contract_id": UUID(int=1)}),
        )
    with pytest.raises(ValueError, match="digest is stale"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=base_finalize.model_copy(update={"execution_contract_digest": DIGEST_A}),
        )
    with pytest.raises(ValueError, match="cannot predate"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=base_finalize.model_copy(
                update={"finalized_at": contract.created_at - timedelta(seconds=1)}
            ),
        )
    with pytest.raises(ValueError, match="capture"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=base_finalize.model_copy(
                update={
                    "artifacts": (
                        _artifact(base_finalize.finalized_at + timedelta(seconds=1)),
                    )
                }
            ),
        )
    with pytest.raises(ValueError, match="unknown evidence artifact"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=base_finalize.model_copy(update={"assertion_results": (assertion,)}),
        )

    soft_specification = run.test_ir.assertions[0].model_copy(
        update={"strength": OracleStrength.SOFT}
    )
    soft_ir = run.test_ir.model_copy(update={"assertions": (soft_specification,)})
    soft_run = run.model_copy(update={"test_ir": soft_ir})
    partial, _ = build_evidence_manifest(
        manifest_id=MANIFEST_ID,
        run=soft_run,
        contract=contract,
        command=base_finalize,
    )
    assert partial.completeness is EvidenceCompleteness.PARTIAL
    assert partial.outcome is OracleOutcome.INCONCLUSIVE

    with pytest.raises(ValueError, match="execution deadline"):
        build_evidence_manifest(
            manifest_id=MANIFEST_ID,
            run=run,
            contract=contract,
            command=FinalizeDebugEvidence(
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                event_chain_head_digest=DIGEST_C,
                event_count=2,
                finalized_at=contract.execution_deadline + timedelta(seconds=1),
            ),
        )
