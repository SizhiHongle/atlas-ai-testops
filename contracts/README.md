# Atlas Machine-readable Contracts

本目录保存跨进程、跨语言和跨版本的线协议。规范优先级如下：

1. 已提交的版本化 JSON Schema。
2. 生成 Schema 的 Python Pydantic 模型。
3. ADR 与统一领域术语。
4. Word 设计稿中的解释和示例。

如果低优先级材料与高优先级契约冲突，以高优先级契约为准，并修正文档。

## 当前契约

- `workflow-graph.schema.json`：`atlas.workflow-graph/0.1`，由 `atlas_testops.domain.workflow.WorkflowGraph` 导出。
- `workflow-draft.schema.json`：`atlas.workflow-draft/0.1`，由 `atlas_testops.domain.workflow.WorkflowDraft` 导出。
- `domain-event.schema.json`：`atlas.domain-event/0.1`，用于 Transactional Outbox 的稳定事件信封。
- `openapi.json`：当前 FastAPI 公共 HTTP API，由前端生成 TypeScript 类型。

## 生成与校验

```bash
cd backend
uv run python scripts/export_contracts.py
uv run python scripts/export_contracts.py --check
uv run python scripts/export_openapi.py
uv run python scripts/export_openapi.py --check
```

生成文件使用对外 `camelCase` 字段。Python 代码继续使用 `snake_case`，Pydantic 同时接受两种输入形式。

尚未实现的 Task、AttemptSeal 和 Insight 协议不会提前提交空 Schema；它们在对应领域代码落地时按独立 `schemaVersion` 增加。
