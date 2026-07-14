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
- `data-atom.schema.json`：`atlas.atom/0.1`，定义部署注册操作、强类型端口、幂等、对账与清理契约。
- `fixture-blueprint.schema.json`：`atlas.fixture-blueprint/0.1`，定义只引用精确 Atom 版本的静态数据 DAG。
- `compiled-fixture-plan.schema.json`：`atlas.compiled-fixture-plan/0.1`，保存确定性的执行层级、摘要与反向清理顺序。
- `fixture-manifest.schema.json`：`atlas.fixture-manifest/0.1`，限制 FixtureRun 只向测试执行暴露显式 exports。
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

`fixture-manifest.schema.json` 已冻结跨进程边界，但生成它的 Fixture Worker 属于 P3-02；当前 P3-01 不伪造 runtime 或 cleanup 通过证据。尚未实现的 Task、AttemptSeal 和 Insight 协议不会提前提交空 Schema；它们在对应领域代码落地时按独立 `schemaVersion` 增加。
