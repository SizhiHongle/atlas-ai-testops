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
- `workflow-patch.schema.json`：`atlas.workflow-patch/0.1`，定义 AI 与人工共用的原子语义编辑协议。
- `test-intent.schema.json`：`atlas.test-intent/0.1`，定义需求锚点、角色、Fixture、Surface 与证据策略。
- `test-ir.schema.json`：`atlas.test-ir/0.2`，定义环境无关、只引用精确版本的确定性测试中间表示。
- `plan-template.schema.json`：`atlas.plan-template/0.1`，定义由 Test IR 纯编译得到的执行层级与摘要。
- `case-version.schema.json`：`atlas.case-version/0.1`，冻结已评审 Draft 的 Intent、Graph、Test IR、PlanTemplate 与可信 DebugRun 证据引用。
- `execution-contract.schema.json`：`atlas.execution-contract/0.1`，冻结 Runtime 的账号 Lease/Fence、Fixture Manifest、浏览器、模型、Prompt、Tool/MCP 与 Policy 版本。
- `assertion-result.schema.json`：`atlas.assertion-result/0.1`，记录由冻结 Assertion Program 产生的确定性 Oracle 结果。
- `evidence-manifest.schema.json`：`atlas.evidence-manifest/0.1`，封存 Oracle、Artifact、事件链与完整性根，且 `PASSED` 必须完整并已验证。
- `browser-execution-bundle.schema.json`：`atlas.browser-execution-bundle/0.1`，把已验证 ExecutionContract、Plan、Fixture Export 与加密 BrowserContext Restore Envelope 交付给 exact Worker。
- `browser-runtime-report.schema.json`：`atlas.browser-runtime-report/0.1`，定义类型化、单调、Digest-linked 的 Browser Observation / Action / Policy / Receipt / Assertion / Artifact 报告事实；Action Report 必须连续，同一 `actionId` 不能跨 Action 链复用。
- `execution-profile.schema.json`：`atlas.execution-profile/0.1`，冻结 CaseVersion、Test IR、Plan、Model、Prompt 与 Tool 预绑定；它不是一次具体运行的 `ExecutionContract`。
- `identity-profile.schema.json`：`atlas.identity-profile/0.1`，冻结 Case actor 到 TestRole 的无秘密映射，不包含账号、Credential、Lease 或 Session。
- `browser-profile.schema.json`：`atlas.browser-profile/0.1`，冻结 Browser revision、Viewport、Locale、Timezone 与 Runtime image/capability attestation digest。
- `data-profile.schema.json`：`atlas.data-profile/0.1`，冻结 exact Fixture Blueprint、Compiled Plan 与无秘密 Run Inputs；dispatch admission 会按 exact Fixture `run_input_schema` 复验。
- `task-plan.schema.json`：稳定 TaskPlan Catalog 根，记录 Project 内唯一 `taskKey`、名称、状态、创建者和 Revision。
- `task-plan-version.schema.json`：`atlas.task-plan/0.1`，冻结 pinned CaseVersion、矩阵、四类正式 Profile Version 与 Policy Digest；数据库验证同作用域、发布态和 Case / Fixture exact compatibility，并对结构化 JSON 缺键 / null fail-closed。
- `task-plan-launch.schema.json`：Manual Launch 请求；绑定 `clientMutationId`、可选 Iteration 与已发布 `infra-retry` 摘要对应的完整 `TaskRetryPolicy`。
- `task-run-manifest.schema.json`：兼容历史 `atlas.task-run-manifest/0.1`，当前 `0.2` 冻结完整 Unit 集、触发指纹、策略、`TaskRetryPolicy` 与可重算 Manifest Hash；Repository 与 PostgreSQL 双层校验 exact PlanVersion provenance，自动重试仅接受 policy-bound `INFRA_ERROR`。
- `task-run.schema.json`：正式批次的三轴状态、稳定 request digest、`MATERIALIZING → SEALED` 完整性门禁、namespace-scoped Temporal identity，以及 P5-00D3B 的不可变 `rerunOfTaskRunId + INFRA_FAILURES` child lineage。
- `execution-unit.schema.json`：Manifest 中一个 exact CaseVersion × Matrix Cell 的逻辑执行槽位，绑定 `executionProfileVersionId`，不复用 DebugRun-scoped ExecutionContract。
- `unit-attempt.schema.json`：ExecutionUnit 的追加式物理尝试及确定性 Temporal identity；业务重试创建新 Attempt，Activity retry 不创建新 Attempt。
- `task-execution-event.schema.json`：`atlas.execution-event/0.1` 的追加式、单调 Task 执行事件投影。
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

`AttemptSeal` 只能绑定正式 `UnitAttempt`；P5-00A 已建立正式宿主，但 Seal、LiveSession、ControlLease 和持久化 ActionGrant 仍按后续切片独立落地，不创建空协议。Result Snapshot 和 Insight 协议会在对应领域代码落地时按独立 `schemaVersion` 增加。

Browser Runtime 的内部 HTTP 端点还要求短期 Tenant / Run / Worker-scoped Execution Permit 和 HMAC Request Signature，且 Staging / Production Runtime API Origin 必须使用 HTTPS；JSON Schema 只约束消息形状，不替代传输层授权、Report Hash-chain State Machine 或数据库不可变约束。Evidence Finalization 会对完整 `AssertionResultInput` / `EvidenceArtifactInput` 重算 Canonical Digest 并与 Report Chain 的 exact 集合匹配；Artifact 还必须来自可信 `BrowserArtifactWriter`，不能由 Operation 自报。
