# Atlas AI 测试平台技术设计文档全集

整理日期：2026-07-15

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

- 当前已实现 Workflow、Fixture、Case、ExecutionContract、AssertionResult、EvidenceManifest、Browser Execution Bundle 与 Browser Runtime Report 的版本化 Pydantic 模型；已提交的机器 Schema 由契约导出脚本统一维护。
- Platform RBAC / Session、Identity Catalog、Account Health Verification、Account Lease / Fencing、一次性 Secret Grant、独立 Auth Session Worker 与加密 SessionArtifact 已通过真实 PostgreSQL / Temporal / Chromium / MinIO 验证。
- Environment 精确 Origin、Secret Grant 签发 API 与前端 TypeScript 类型由 OpenAPI 统一生成；前端原型结构、布局和样式保持不变。
- Fixture 与 Case Publication Validation 已落地；CaseVersion 还会读取实际 EvidenceManifest，复核 ExecutionContract、Test IR、Plan 与 Fixture Digest。
- P6-01 已建立不直连主数据库的独立 Browser Worker、短期 Permit + HMAC 内部网关、Staging / Production HTTPS 强制、Temporal Activity、加密 BrowserContext Restore Envelope、严格 Report Hash-chain / 连续且 `actionId` 不可跨链复用的 Action State Machine、完整 Evidence Input Digest 绑定与受限 Playwright Adapter。Operation 不能自报 Artifact，Blocked / 非成功 Receipt 只能得到 `INCONCLUSIVE`；生产 Evidence / Redaction Writer、真实 SaaS Operation / Route Registry 和容器网络沙箱仍按 fail-closed 方式待后续接入。
- Task、AttemptSeal 和 Insight Schema 会在对应领域代码落地时增加，不提前创建空契约。
