"""从 Pydantic 模型导出版本化线协议。"""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.workflow import WorkflowDraft, WorkflowGraph

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_GRAPH_SCHEMA = REPOSITORY_ROOT / "contracts" / "workflow-graph.schema.json"
WORKFLOW_DRAFT_SCHEMA = REPOSITORY_ROOT / "contracts" / "workflow-draft.schema.json"
DOMAIN_EVENT_SCHEMA = REPOSITORY_ROOT / "contracts" / "domain-event.schema.json"


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
