"""从 Pydantic 模型导出版本化线协议。"""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from atlas_testops.domain.case import (
    CaseVersion,
    PlanTemplate,
    TestIntent,
    TestIR,
    WorkflowPatch,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    CompiledFixturePlan,
    DataAtomContract,
    DataBlueprintContract,
    FixtureManifest,
)
from atlas_testops.domain.insight import InsightSnapshot
from atlas_testops.domain.result import (
    AttemptClosureNotice,
    AttemptFixtureBinding,
    AttemptSeal,
    FailureClassificationRevision,
    FailureClusterRevision,
    ResultRef,
    TaskGateDecision,
    TaskResultReevaluationCommand,
    TaskResultSnapshot,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
)
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    AssertionResult,
    BrowserExecutionBundle,
    EvidenceManifest,
    ExecutionContract,
    UnitAttemptLiveSnapshot,
)
from atlas_testops.domain.task import (
    BrowserProfileVersion,
    CreateTaskSchedule,
    DataProfileVersion,
    ExecutionProfileVersion,
    ExecutionUnit,
    IdentityProfileVersion,
    StartTaskPlanVersionRun,
    TaskExecutionEvent,
    TaskPlan,
    TaskPlanVersion,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunManifest,
    TaskSchedule,
    TaskUnitExecutionTicket,
    TriggerTaskPlanVersionRun,
    UnitAttempt,
)
from atlas_testops.domain.workflow import WorkflowDraft, WorkflowGraph

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_GRAPH_SCHEMA = REPOSITORY_ROOT / "contracts" / "workflow-graph.schema.json"
WORKFLOW_DRAFT_SCHEMA = REPOSITORY_ROOT / "contracts" / "workflow-draft.schema.json"
DOMAIN_EVENT_SCHEMA = REPOSITORY_ROOT / "contracts" / "domain-event.schema.json"
DATA_ATOM_SCHEMA = REPOSITORY_ROOT / "contracts" / "data-atom.schema.json"
DATA_BLUEPRINT_SCHEMA = REPOSITORY_ROOT / "contracts" / "fixture-blueprint.schema.json"
COMPILED_FIXTURE_PLAN_SCHEMA = REPOSITORY_ROOT / "contracts" / "compiled-fixture-plan.schema.json"
FIXTURE_MANIFEST_SCHEMA = REPOSITORY_ROOT / "contracts" / "fixture-manifest.schema.json"
WORKFLOW_PATCH_SCHEMA = REPOSITORY_ROOT / "contracts" / "workflow-patch.schema.json"
TEST_INTENT_SCHEMA = REPOSITORY_ROOT / "contracts" / "test-intent.schema.json"
TEST_IR_SCHEMA = REPOSITORY_ROOT / "contracts" / "test-ir.schema.json"
PLAN_TEMPLATE_SCHEMA = REPOSITORY_ROOT / "contracts" / "plan-template.schema.json"
CASE_VERSION_SCHEMA = REPOSITORY_ROOT / "contracts" / "case-version.schema.json"
EXECUTION_CONTRACT_SCHEMA = REPOSITORY_ROOT / "contracts" / "execution-contract.schema.json"
ASSERTION_RESULT_SCHEMA = REPOSITORY_ROOT / "contracts" / "assertion-result.schema.json"
EVIDENCE_MANIFEST_SCHEMA = REPOSITORY_ROOT / "contracts" / "evidence-manifest.schema.json"
BROWSER_EXECUTION_BUNDLE_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "browser-execution-bundle.schema.json"
)
BROWSER_RUNTIME_REPORT_SCHEMA = REPOSITORY_ROOT / "contracts" / "browser-runtime-report.schema.json"
TASK_PLAN_VERSION_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-plan-version.schema.json"
TASK_PLAN_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-plan.schema.json"
TASK_PLAN_LAUNCH_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-plan-launch.schema.json"
TASK_RUN_TRIGGER_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-run-trigger.schema.json"
TASK_RUN_MANIFEST_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-run-manifest.schema.json"
TASK_RUN_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-run.schema.json"
EXECUTION_UNIT_SCHEMA = REPOSITORY_ROOT / "contracts" / "execution-unit.schema.json"
UNIT_ATTEMPT_SCHEMA = REPOSITORY_ROOT / "contracts" / "unit-attempt.schema.json"
TASK_EXECUTION_EVENT_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-execution-event.schema.json"
EXECUTION_PROFILE_SCHEMA = REPOSITORY_ROOT / "contracts" / "execution-profile.schema.json"
IDENTITY_PROFILE_SCHEMA = REPOSITORY_ROOT / "contracts" / "identity-profile.schema.json"
BROWSER_PROFILE_SCHEMA = REPOSITORY_ROOT / "contracts" / "browser-profile.schema.json"
DATA_PROFILE_SCHEMA = REPOSITORY_ROOT / "contracts" / "data-profile.schema.json"
TASK_UNIT_EXECUTION_TICKET_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "task-unit-execution-ticket.schema.json"
)
TASK_RUN_COMMAND_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-run-command.schema.json"
TASK_SCHEDULE_CREATE_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-schedule-create.schema.json"
TASK_SCHEDULE_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-schedule.schema.json"
ATTEMPT_SEAL_SCHEMA = REPOSITORY_ROOT / "contracts" / "attempt-seal.schema.json"
RESULT_REF_SCHEMA = REPOSITORY_ROOT / "contracts" / "result-ref.schema.json"
ATTEMPT_CLOSURE_NOTICE_SCHEMA = REPOSITORY_ROOT / "contracts" / "attempt-closure-notice.schema.json"
ATTEMPT_FIXTURE_BINDING_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "attempt-fixture-binding.schema.json"
)
UNIT_RESOLUTION_REVISION_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "unit-resolution-revision.schema.json"
)
UNIT_HYGIENE_RESOLUTION_REVISION_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "unit-hygiene-resolution-revision.schema.json"
)
TASK_RESULT_SNAPSHOT_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-result-snapshot.schema.json"
TASK_RESULT_REEVALUATION_COMMAND_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "task-result-reevaluation-command.schema.json"
)
FAILURE_CLUSTER_REVISION_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "failure-cluster-revision.schema.json"
)
FAILURE_CLASSIFICATION_REVISION_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "failure-classification-revision.schema.json"
)
TASK_GATE_DECISION_SCHEMA = REPOSITORY_ROOT / "contracts" / "task-gate-decision.schema.json"
INSIGHT_SNAPSHOT_SCHEMA = REPOSITORY_ROOT / "contracts" / "insight-snapshot.schema.json"
UNIT_ATTEMPT_LIVE_SNAPSHOT_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "unit-attempt-live-snapshot.schema.json"
)


def render_schema(model: type[BaseModel], schema_id: str) -> str:
    """生成稳定的 JSON Schema 2020-12 文档。"""

    generated = model.model_json_schema(by_alias=True, mode="validation")
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        **generated,
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    """写入或验证所有已实现的机器可读契约。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contracts = {
        WORKFLOW_GRAPH_SCHEMA: render_schema(
            WorkflowGraph,
            "https://atlas.test/contracts/workflow-graph/0.1/schema.json",
        ),
        WORKFLOW_DRAFT_SCHEMA: render_schema(
            WorkflowDraft,
            "https://atlas.test/contracts/workflow-draft/0.1/schema.json",
        ),
        DOMAIN_EVENT_SCHEMA: render_schema(
            DomainEvent,
            "https://atlas.test/contracts/domain-event/0.1/schema.json",
        ),
        DATA_ATOM_SCHEMA: render_schema(
            DataAtomContract,
            "https://atlas.test/contracts/data-atom/0.1/schema.json",
        ),
        DATA_BLUEPRINT_SCHEMA: render_schema(
            DataBlueprintContract,
            "https://atlas.test/contracts/fixture-blueprint/0.1/schema.json",
        ),
        COMPILED_FIXTURE_PLAN_SCHEMA: render_schema(
            CompiledFixturePlan,
            "https://atlas.test/contracts/compiled-fixture-plan/0.1/schema.json",
        ),
        FIXTURE_MANIFEST_SCHEMA: render_schema(
            FixtureManifest,
            "https://atlas.test/contracts/fixture-manifest/0.1/schema.json",
        ),
        WORKFLOW_PATCH_SCHEMA: render_schema(
            WorkflowPatch,
            "https://atlas.test/contracts/workflow-patch/0.1/schema.json",
        ),
        TEST_INTENT_SCHEMA: render_schema(
            TestIntent,
            "https://atlas.test/contracts/test-intent/0.1/schema.json",
        ),
        TEST_IR_SCHEMA: render_schema(
            TestIR,
            "https://atlas.test/contracts/test-ir/0.2/schema.json",
        ),
        PLAN_TEMPLATE_SCHEMA: render_schema(
            PlanTemplate,
            "https://atlas.test/contracts/plan-template/0.1/schema.json",
        ),
        CASE_VERSION_SCHEMA: render_schema(
            CaseVersion,
            "https://atlas.test/contracts/case-version/0.1/schema.json",
        ),
        EXECUTION_CONTRACT_SCHEMA: render_schema(
            ExecutionContract,
            "https://atlas.test/contracts/execution-contract/0.1/schema.json",
        ),
        ASSERTION_RESULT_SCHEMA: render_schema(
            AssertionResult,
            "https://atlas.test/contracts/assertion-result/0.1/schema.json",
        ),
        EVIDENCE_MANIFEST_SCHEMA: render_schema(
            EvidenceManifest,
            "https://atlas.test/contracts/evidence-manifest/0.1/schema.json",
        ),
        BROWSER_EXECUTION_BUNDLE_SCHEMA: render_schema(
            BrowserExecutionBundle,
            "https://atlas.test/contracts/browser-execution-bundle/0.1/schema.json",
        ),
        BROWSER_RUNTIME_REPORT_SCHEMA: render_schema(
            AppendBrowserRuntimeReport,
            "https://atlas.test/contracts/browser-runtime-report/0.1/schema.json",
        ),
        TASK_PLAN_VERSION_SCHEMA: render_schema(
            TaskPlanVersion,
            "https://atlas.test/contracts/task-plan-version/0.1/schema.json",
        ),
        TASK_PLAN_SCHEMA: render_schema(
            TaskPlan,
            "https://atlas.test/contracts/task-plan/0.1/schema.json",
        ),
        TASK_PLAN_LAUNCH_SCHEMA: render_schema(
            StartTaskPlanVersionRun,
            "https://atlas.test/contracts/task-plan-launch/0.1/schema.json",
        ),
        TASK_RUN_TRIGGER_SCHEMA: render_schema(
            TriggerTaskPlanVersionRun,
            "https://atlas.test/contracts/task-run-trigger/0.1/schema.json",
        ),
        TASK_RUN_MANIFEST_SCHEMA: render_schema(
            TaskRunManifest,
            "https://atlas.test/contracts/task-run-manifest/0.1/schema.json",
        ),
        TASK_RUN_SCHEMA: render_schema(
            TaskRun,
            "https://atlas.test/contracts/task-run/0.1/schema.json",
        ),
        EXECUTION_UNIT_SCHEMA: render_schema(
            ExecutionUnit,
            "https://atlas.test/contracts/execution-unit/0.1/schema.json",
        ),
        UNIT_ATTEMPT_SCHEMA: render_schema(
            UnitAttempt,
            "https://atlas.test/contracts/unit-attempt/0.1/schema.json",
        ),
        TASK_EXECUTION_EVENT_SCHEMA: render_schema(
            TaskExecutionEvent,
            "https://atlas.test/contracts/execution-event/0.1/schema.json",
        ),
        EXECUTION_PROFILE_SCHEMA: render_schema(
            ExecutionProfileVersion,
            "https://atlas.test/contracts/execution-profile/0.1/schema.json",
        ),
        IDENTITY_PROFILE_SCHEMA: render_schema(
            IdentityProfileVersion,
            "https://atlas.test/contracts/identity-profile/0.1/schema.json",
        ),
        BROWSER_PROFILE_SCHEMA: render_schema(
            BrowserProfileVersion,
            "https://atlas.test/contracts/browser-profile/0.1/schema.json",
        ),
        DATA_PROFILE_SCHEMA: render_schema(
            DataProfileVersion,
            "https://atlas.test/contracts/data-profile/0.1/schema.json",
        ),
        TASK_UNIT_EXECUTION_TICKET_SCHEMA: render_schema(
            TaskUnitExecutionTicket,
            "https://atlas.test/contracts/task-unit-execution-ticket/0.1/schema.json",
        ),
        TASK_RUN_COMMAND_SCHEMA: render_schema(
            TaskRunCommandIntent,
            "https://atlas.test/contracts/task-run-command/0.1/schema.json",
        ),
        TASK_SCHEDULE_CREATE_SCHEMA: render_schema(
            CreateTaskSchedule,
            "https://atlas.test/contracts/task-schedule-create/0.1/schema.json",
        ),
        TASK_SCHEDULE_SCHEMA: render_schema(
            TaskSchedule,
            "https://atlas.test/contracts/task-schedule/0.1/schema.json",
        ),
        ATTEMPT_SEAL_SCHEMA: render_schema(
            AttemptSeal,
            "https://atlas.test/contracts/attempt-seal/1.0/schema.json",
        ),
        RESULT_REF_SCHEMA: render_schema(
            ResultRef,
            "https://atlas.test/contracts/result-ref/0.1/schema.json",
        ),
        ATTEMPT_CLOSURE_NOTICE_SCHEMA: render_schema(
            AttemptClosureNotice,
            "https://atlas.test/contracts/attempt-closure-notice/0.1/schema.json",
        ),
        ATTEMPT_FIXTURE_BINDING_SCHEMA: render_schema(
            AttemptFixtureBinding,
            "https://atlas.test/contracts/attempt-fixture-binding/0.1/schema.json",
        ),
        UNIT_RESOLUTION_REVISION_SCHEMA: render_schema(
            UnitResolutionRevision,
            "https://atlas.test/contracts/unit-resolution-revision/0.1/schema.json",
        ),
        UNIT_HYGIENE_RESOLUTION_REVISION_SCHEMA: render_schema(
            UnitHygieneResolutionRevision,
            "https://atlas.test/contracts/unit-hygiene-resolution-revision/0.1/schema.json",
        ),
        TASK_RESULT_SNAPSHOT_SCHEMA: render_schema(
            TaskResultSnapshot,
            "https://atlas.test/contracts/task-result-snapshot/0.3/schema.json",
        ),
        TASK_RESULT_REEVALUATION_COMMAND_SCHEMA: render_schema(
            TaskResultReevaluationCommand,
            "https://atlas.test/contracts/task-result-reevaluation-command/0.1/schema.json",
        ),
        FAILURE_CLUSTER_REVISION_SCHEMA: render_schema(
            FailureClusterRevision,
            "https://atlas.test/contracts/failure-cluster-revision/0.1/schema.json",
        ),
        FAILURE_CLASSIFICATION_REVISION_SCHEMA: render_schema(
            FailureClassificationRevision,
            "https://atlas.test/contracts/failure-classification-revision/0.1/schema.json",
        ),
        TASK_GATE_DECISION_SCHEMA: render_schema(
            TaskGateDecision,
            "https://atlas.test/contracts/task-gate-decision/0.1/schema.json",
        ),
        INSIGHT_SNAPSHOT_SCHEMA: render_schema(
            InsightSnapshot,
            "https://atlas.test/contracts/insight-snapshot/0.1/schema.json",
        ),
        UNIT_ATTEMPT_LIVE_SNAPSHOT_SCHEMA: render_schema(
            UnitAttemptLiveSnapshot,
            "https://atlas.test/contracts/unit-attempt-live-snapshot/0.1/schema.json",
        ),
    }

    if args.check:
        stale = [
            path
            for path, rendered in contracts.items()
            if not path.exists() or path.read_text(encoding="utf-8") != rendered
        ]
        if stale:
            formatted = ", ".join(str(path) for path in stale)
            raise SystemExit(f"Generated contracts are stale: {formatted}")
        return

    for path, rendered in contracts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
