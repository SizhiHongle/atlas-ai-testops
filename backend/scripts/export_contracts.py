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
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    AssertionResult,
    BrowserExecutionBundle,
    EvidenceManifest,
    ExecutionContract,
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
BROWSER_RUNTIME_REPORT_SCHEMA = (
    REPOSITORY_ROOT / "contracts" / "browser-runtime-report.schema.json"
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
