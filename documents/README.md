# Atlas AI 测试平台技术设计文档全集

整理日期：2026-07-16

本目录包含 8 份核心技术设计文档，以及统一架构决策、领域术语和机器可读契约入口。

## 统一基线

- [ADR-0001: Python 后端运行时与契约边界](adr/ADR-0001-python-backend-runtime.md)
- [ADR-0002: 模块化单体与事实权威边界](adr/ADR-0002-modular-monolith-and-authority-boundaries.md)
- [ADR-0003: PostgreSQL Migration、RLS 与 Outbox](adr/ADR-0003-postgresql-migrations-rls-and-outbox.md)
- [ADR-0004: API 契约与前端状态边界](adr/ADR-0004-api-contract-and-frontend-state.md)
- [ADR-0005: Auth Session Worker 与加密浏览器状态](adr/ADR-0005-auth-session-worker-and-encrypted-artifacts.md)
- [ADR-0006: Fixture 资产契约、确定性编译与发布门禁](adr/ADR-0006-fixture-asset-contracts-and-publication-gates.md)
- [ADR-0007: 受信 Runtime、确定性 Oracle 与证据根](adr/ADR-0007-trusted-runtime-and-evidence-root.md)
- [ADR-0008: 无数据库 Browser Worker 与受限浏览器执行协议](adr/ADR-0008-database-free-browser-worker.md)
- [ADR-0009: 正式任务执行宿主与不可变 Run Manifest](adr/ADR-0009-formal-task-execution-hosts.md)
- [Atlas 统一领域术语](DOMAIN_GLOSSARY.md)
- [机器可读契约](../contracts/README.md)

## 实施记忆

- [实施进度](IMPLEMENTATION_PROGRESS.md)
- [实施要点](IMPLEMENTATION_NOTES.md)
- [实施矩阵](IMPLEMENTATION_MATRIX.md)

后端统一采用 Python 3.14、FastAPI、Pydantic、Temporal Python SDK、Playwright Python 和 Psycopg 3；前端继续使用 Next.js / TypeScript。Word 文档中的 Python class 是实现示例，跨语言线协议以版本化 JSON Schema 为准。

## 文档状态

| 文档 | 版本 | 状态 | 主要范围 |
| --- | --- | --- | --- |
| `AI_Test_Platform_Implementation_Plan_v1.1.docx` | v1.1 | 正式基线 | 总体落地方案、领域模型、运行模型、架构与实施路线 |
| `Atlas_身份与测试账号体系技术设计_v1.1.docx` | v1.1 | 正式基线 | 账号池、租约、MFA、SaaS Adapter、权限与凭证安全 |
| `Atlas_数据预加载与原子组件编排功能设计对齐稿_v0.2.docx` | v0.2 | 业务对齐中，架构已冻结 | 原子组件、强类型端口、DAG、资源台账与清理 |
| `Atlas_AI用例与浏览器Agent工作流功能设计及实现对齐稿_v0.3.docx` | v0.3 | 已对齐基线 | TestCase、WorkflowDraft、CaseVersion、Browser Agent 与 ATMP |
| `Atlas_任务中心与批量执行控制面功能设计及实现对齐稿_v0.2.docx` | v0.2 | 业务对齐中，架构已冻结 | 任务、矩阵、Manifest、调度控制面与持续回归 |
| `Atlas_现场与浏览器实时执行功能设计及实现对齐稿_v0.2.docx` | v0.2 | 对齐草案，架构已冻结 | UnitAttempt 现场、实时画面、人工协助与证据采集 |
| `Atlas_结果中心功能设计及实现对齐稿_v0.2.docx` | v0.2 | 业务对齐中，架构已冻结 | AttemptSeal、结果投影、归因、重放与门禁 |
| `Atlas_洞察中心功能设计及实现对齐稿_v0.2.docx` | v0.2 | 业务对齐中，架构已冻结 | Cohort、可比性、稳定性、质量信号与洞察治理 |

## 状态解释

- 正式基线：可以直接约束对应模块的实现。
- 已对齐基线：核心产品和技术决策已冻结，新增变化需要版本升级。
- 业务对齐中 / 对齐草案：Python 架构已经冻结，文档开头列出的业务决策仍需负责人逐项确认。

## 推荐阅读顺序

ADR-0001 → 统一领域术语 → 总体落地方案 → 身份与账号 → 数据与原子编排 → AI 用例 → 任务中心 → 测试现场 → 结果中心 → 洞察中心。

## 契约与实现状态

- 当前已实现 Workflow、Fixture、Case、四类正式 Task Profile Version、TaskPlanVersion、TaskRunManifest、TaskRun / ExecutionUnit / UnitAttempt 投影、TaskExecutionEvent、ExecutionContract、AssertionResult、EvidenceManifest、Browser Execution Bundle、Browser Runtime Report，以及 Debug Live Cursor / Run Projection / Event / Snapshot 的 Pydantic 模型；已提交的机器 Schema 由契约导出脚本统一维护。
- Platform RBAC / Session、Identity Catalog、Account Health Verification、Account Lease / Fencing、一次性 Secret Grant、独立 Auth Session Worker 与加密 SessionArtifact 已通过真实 PostgreSQL / Temporal / Chromium / MinIO 验证。
- Environment 精确 Origin、Secret Grant 签发 API 与前端 TypeScript 类型由 OpenAPI 统一生成；前端原型结构、布局和样式保持不变。
- Fixture 与 Case Publication Validation 已落地；CaseVersion 还会读取实际 EvidenceManifest，复核 ExecutionContract、Test IR、Plan 与 Fixture Digest。
- P6-01 已建立不直连主数据库的独立 Browser Worker、Permit + HMAC 内部网关、加密 BrowserContext Restore Envelope、严格 Report Hash-chain 与受限 Playwright Adapter；P6-02A 已实现 DOM Mask、canonical PNG、write-once / read-back verified Evidence Writer，以及不暴露 ObjectRef 的 Manifest、hash-only scoped Read Grant 与完整字节二次校验。
- P6-02B1 已实现 DebugRun-scoped 安全 Live Snapshot 与 SSE：Opaque Base64URL Cursor、`Last-Event-ID` 有序 replay、轻量 Snapshot / head 单 SQL、短事务轮询、无 `id` Heartbeat comment、事件类型 allowlist、有界 Observer 容量与连接生命周期；Snapshot SQL 不物化完整 Test IR / PlanTemplate，取消原因、Digest、ObjectRef 和未知 Payload 不进入 Live 响应。
- `DebugRun=TERMINATED` 不封存事件日志；SSE 会跨过 `debug_run.terminated` replay 后续可能出现的 `debug_run.snapshot_outdated`，到达当前 head 后继续等待，直到客户端断开或 Service 事件生成预算耗尽。Route 内 StreamingResponse 以预算加固定 1.0 秒 Close Grace 管理 Source 关闭和 Observer Slot 释放；最后安装的 pure-ASGI Middleware 使用相同的 maximum 与 Close Grace 包住 `BaseHTTPMiddleware` 后的真实 client-facing `send`，卡死的网络写到期会被取消。
- Live Event 表按 `20260716_0019` / `20260716_0020` / `20260716_0021` 三阶段加固：0019 只提交 32 KiB `CHECK ... NOT VALID` 可修复边界；0020 只先 Validate，再创建不可变 Trigger；0021 通过 Alembic autocommit 执行 `DROP INDEX CONCURRENTLY IF EXISTS` 清理冗余 replay index，downgrade 以 `CREATE INDEX CONCURRENTLY IF NOT EXISTS` 恢复。历史超限 Payload 导致 0020 失败时，版本保持 0019；修复数据后可安全重试，成功后再进入 0021。
- P5-00A 已建立 `TaskPlanVersion → TaskRun → ExecutionUnit → UnitAttempt` 正式宿主；P5-00B1 进一步建立四类正式 Profile、稳定 request digest、namespace-scoped Temporal identity registry、同步物化 Seal、Pending Start Intent 与 Run → Unit → Attempt Revision CAS。P5-00B2A 已实现 `20260716_0024` durable Intent 状态机、独立 `atlas_dispatcher`、短事务 Claim / Ack、稳定 Temporal `request_id` 和既有 Workflow collision verification；Consumer 与 Compose profile 默认关闭，`STARTED` 只表示 Temporal 接受。真实 `AtlasTaskRunWorkflow` / Activity、公共 Task 控制面和超过 64 Units 的可恢复分区物化仍待后续。
- 正式 `LiveSession`、`ControlLease`、浏览器控制 Epoch / Fence、Human Takeover、持久化 ActionGrant 与 AttemptSeal 尚未实现，属于 P6-02B2 后续；当前 SSE 只是只读 Observer，不提供 Action 或人工控制。真实 SaaS Operation / Route Registry 和容器网络沙箱同样继续 fail-closed。
- Task 与四类 Profile 机器 Schema 已随 P5-00B1 导出；AttemptSeal、Result Snapshot 和 Insight Schema 会在对应领域代码落地时增加，不提前创建空契约。
