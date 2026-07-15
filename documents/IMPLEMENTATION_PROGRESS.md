# Atlas AI 测试平台实施进度

更新时间：2026-07-15

## 使用规则

本文件记录已经由代码和验证结果证明的进度，不把计划中的能力写成已完成。每个实施切片结束时必须同步更新：

1. 当前阶段与下一步。
2. 已完成的数据库、领域、API、前端和测试证据。
3. 新增或改变的架构决策。
4. 尚未解决的风险和外部依赖。

## 当前状态

- 当前阶段：`P6 Browser / Oracle / Evidence Runtime（基础中）`
- 当前切片：`P6-01 无数据库 Browser Worker 与受限 Playwright 执行（已完成）`
- 总体状态：P4 发布闭环、P6-00 受信运行事实与 P6-01 Browser 执行平面已落地；下一步补齐生产 Evidence / Redaction 与 Live 能力，前端继续只按既有原型槽位接线
- 当前分支：`main`
- 当前进入基线提交：`befc631`

## 阶段看板

| 阶段 | 范围 | 状态 | 完成证据 |
| --- | --- | --- | --- |
| P0 | 工程基础、契约、数据库、进程入口、前端 API 基础 | 已完成 | 46 tests、真实 PostgreSQL/Temporal、三容器构建、前端浏览器 QA |
| P1 | Tenant、Project、Environment、平台身份与权限 | 已完成 | 88 tests、真实 PostgreSQL RLS/RBAC/Session、登录原型浏览器 QA |
| P2 | TestRole、AccountPool、TestAccount、Lease 与 Auth Session | 已完成 | P2-01 至 P2-06 已验收；身份、租约、Secret Grant、加密 Session 与清理链已闭环 |
| P3 | Atom、Blueprint、Fixture Run 与 Cleanup | 已完成 | P3-00 至 P3-03 已验收；资产、耐久运行、取消补偿、Reconcile、Cleanup Retry / Sweeper 与三类发布证据闭环 |
| P4 | TestCase、WorkflowDraft、DebugRun 与 CaseVersion | 后端完成 | P4-00 至 P4-03 已验收；作者态、不可变 DebugRun、精确绑定、Reviewer 发布门禁与 CaseVersion 冻结闭环已落地 |
| P5 | TaskPlan、TaskRun、ExecutionUnit 与 Temporal 编排 | 未开始 | — |
| P6 | Browser Worker、Live、Evidence 与 AttemptSeal | 基础中 | P6-00 已完成可信事实层；P6-01 独立无数据库 Browser Worker、受信网关、Context Restore、报告链与受限 Playwright Adapter 已验收；Live / 生产 Evidence Writer / AttemptSeal 待后续 |
| P7 | Result Fact、Snapshot、Classification 与 Gate | 未开始 | — |
| P8 | Insight Projector、Metric、Snapshot 与 Export | 未开始 | — |
| P9 | 隔离、并发、故障注入、黄金链路与 SLO 验收 | 未开始 | — |

## P0-01 范围

### 已完成

- Python 3.14 / FastAPI 基础包。
- `atlas.workflow-graph/0.1` 与 `atlas.workflow-draft/0.1` Pydantic 协议。
- Workflow Graph 的 Node、Port、Edge、DAG、必填输入、终止节点和 HARD Oracle 结构校验。
- JSON Schema 导出与漂移检查。
- 八份技术文档的 Python 架构、术语和版本基线整理。
- 本文件、实施要点和实施矩阵的持续记忆机制。

### 已完成（续）

- 统一领域基础模型、Problem Details、Request ID 和依赖 readiness。
- Psycopg 3 异步连接池、Alembic upgrade/downgrade、RLS、不可变 Audit、Outbox 和幂等基表。
- Outbox 的 claim、release、retry、owner check 和 mark processed 数据访问。
- Idempotency-Key 的 request hash、处理中冲突、缓存重放和完成协议。
- PostgreSQL 18、Temporal Dev Server 和 MinIO 本地环境。
- 独立 Temporal Worker 入口与真实 `atlas.platform-probe/0.1` Workflow。
- OpenAPI → TypeScript、SWR Provider、类型化 API Client 和 `/system-status` 真实接口页面。
- API、Migration、Temporal Worker 三个 Docker target。

## P1-01 范围

### 已完成

- Tenant、Project、Environment 领域模型与仓储。
- 创建、列表、详情和并发更新 API。
- Development Actor Context 与数据库 RLS 上下文桥接。
- Cursor Pagination、Idempotency-Key、If-Match / ETag 与 OpenAPI 契约。
- 两个 Tenant 的真实 PostgreSQL RLS、API 隔离和越权隐藏测试。
- 现有首页 Project Chip 接入真实 Session Project；未新增原型外 Workspace 页面。

## P1-02 范围

### 已完成

- `PlatformUser`、`PasswordCredential`、`PlatformMembership` 与 `PlatformSession` Migration。
- 组织级与项目级 RBAC：`ORG_ADMIN`、`PROJECT_ADMIN`、`COMPONENT_MAINTAINER`、`CASE_AUTHOR`、`CASE_REVIEWER`、`RUN_OPERATOR`、`OBSERVER`。
- Argon2id 密码保护、失败计数与临时锁定；密码计算移入受并发限制的线程池。
- Opaque Session Token 只通过 HttpOnly / SameSite Cookie 传递，数据库仅保存 SHA-256 Token Hash。
- Session Idle / Absolute Expiry、实时 Membership 复核、服务端撤销与 Origin 防护。
- `/v1/auth/bootstrap`、`/v1/auth/login`、`/v1/session`、`/v1/auth/logout`。
- 现有 `/login` 原型接入真实账号密码登录；Feishu 未配置时明确提示，不模拟登录成功。
- 保留原型 DOM、布局和 CSS；只在现有交互位置接入数据与状态。

## P2-01 范围

### 已完成

- `TestRole`、`AccountPool`、`TestAccount`、`AccountSlot` 与 `CredentialBinding` Migration、RLS、约束和索引。
- TestAccount 的 lifecycle、health、operational、sync、cooldown、credential 与 slot 正交状态；可用性只由实时投影计算，不保存可变 `available` 标志。
- TestRole、AccountPool、TestAccount 的创建、列表、详情、revision 更新、隔离、恢复与容量 API。
- TestAccount 对外只返回脱敏登录提示和认证方式；SecretRef 只在数据库凭证绑定中保存，不进入响应、Audit 或 Outbox Payload。
- 双 Tenant 的真实 PostgreSQL RLS、Idempotency、If-Match、状态转换与容量集成测试。
- Identity Wallet 保留固定四卡、原有 DOM、布局和 CSS，只在既有文本槽位接入真实角色、账号、权限、环境与容量；数据不足时保留原型占位内容。
- `20260713_0004` downgrade → upgrade → Identity API 往返验证。

## P2-02 范围

### 已完成

- `AccountLease` Migration、RLS、同一 Slot 只能有一个 Active Lease 的部分唯一索引，以及租约终态不可变 Trigger。
- `/internal/v1/account-leases` 的幂等 Acquire、单租约查询、Heartbeat、Release 与 Tenant 级 TTL Reaper。
- LRU 候选选择、`FOR UPDATE SKIP LOCKED`、服务端 TTL、Execution Deadline 上限、结构化 Release Reason 和 Opaque Account Handle。
- Account `leaseEpoch` 与 Lease `fencingToken` 双重校验；旧 Worker 在释放、回收、管理撤销或新租约产生后不能继续续租或影响新租约。
- 正常释放进入 Cooldown；TTL 过期进入 `DEGRADED / VERIFYING`；清理失败进入隔离；所有异常账号必须重新验证后才能调度。
- Account 隔离、挂起、退休，以及 AccountPool / TestRole 禁用会在同一事务撤销 Active Lease、推进 Fence，并写入 Audit 与 Outbox。
- Acquire 对 Role、Pool、Environment 调度作用域使用共享锁，对 Account / Slot 使用排他跳锁；拿锁后以新语句快照复核 Active Lease，关闭管理状态与高并发提交窗口。
- Worker 安全投影不包含 Account ID、Slot ID、登录提示或 SecretRef；事件 Payload 只保留执行标识、Account ID、Fence、状态和结构化原因。
- 既有 Identity Wallet 布局和 CSS 未修改；容量与“租用中”数量继续从实时 AccountPool Capacity 投影读取。

## P2-03 范围

### 已完成

- `20260713_0006` 为 Environment 增加规范化精确 Origin 策略，并建立强制 RLS 的 `secret_grant` 一次性授权账本。
- 原始 `grantRef` 只存在于签发响应，PostgreSQL 仅保存 SHA-256 Hash；Grant Ref、SecretRef、用户名和密码均不进入 Audit、Outbox 或持久化 Payload。
- `/internal/v1/account-leases/{leaseId}:issue-secret-grant` 校验 Environment、Lease、最新 Fence、Purpose、Worker Identity 与 Allowed Origin，并使用 `Cache-Control: no-store` 返回 30–300 秒短期授权。
- Redemption 仅提供给受信 Auth Worker 的进程内应用服务，不开放“取密码”HTTP API；原子状态迁移保证最多兑换一次，重放返回稳定错误。
- `SecretProvider`、私有 `PasswordSecretScope` 与 `AdapterContext.with_password_secret(...)` 闭包隔离秘密；Adapter 无法取得 SecretRef、SecretVersion 或 `getSecret()` 返回值。
- `GenericPasswordAdapter` 实现版本化 `account.read` / `auth.password` Capability、协商、健康探针与安全错误；`MockIdentityProvider` 提供确定性合约测试目标。
- Lease 释放/过期/管理撤销、Environment 禁用、Credential 失效和 Grant TTL Reaper 都会终结未消费 Grant；Grant 终态由数据库 Trigger 保证不可逆。
- 20 路并发 Redemption 只产生一次成功；跨 Tenant、错误 Origin、错误 Worker、过期、重放、Credential 撤销和 Lease 撤销均在真实 PostgreSQL 验证。
- OpenAPI 和前端 TypeScript API 类型已生成；未改动前端页面 DOM、布局、CSS 或原型交互。

## P2-04 范围

### 已完成

- `20260713_0007` 建立强制 RLS 的 `connector_installation` 与 `connector_capability`，并让 TestAccount、SecretGrant 显式绑定权威 Connector。
- Connector 配置只保存不透明 `configurationRef`；公共响应固定投影为 `configurationState=CONFIGURED`，配置引用不进入响应、Audit、Outbox 或前端类型。
- `/v1/connector-installations` 提供幂等创建、详情、Cursor 列表、Revision PATCH 与 `:validate`；验证使用“短事务读取 → 事务外 Probe / Negotiate → Revision CAS 落库”。
- `AdapterRegistry` 只允许部署时显式登记的工厂，不接受动态 Module、任意 URL 或请求驱动的 import；Mock Provider 仅在 local / test / development 进程环境注册。
- 实际 Capability Snapshot 使用结构化名称、版本和执行模式；账号导入必须绑定 ACTIVE Connector，且 Credential AuthMethod 必须被实际能力覆盖。
- Production Environment 只允许 `OBSERVE_ONLY` Connector；Connector Origin 必须是 Environment 精确 Origin 的子集，Environment 缩减仍被依赖的 Origin 会返回结构化冲突。
- Lease Acquire 在领取 Account / Slot 前按稳定顺序共享锁定 ACTIVE Connector；SecretGrant 签发和消费同时复核 Connector 状态、账号绑定、`auth.password` 与 Origin。
- Connector 禁用、重配置、验证降级或账号重新绑定会撤销 Active Lease、推进 Fence，并终结未消费 Grant；结构化原因分别为 `CONNECTOR_DISABLED`、`CONNECTOR_REBOUND` 与 `CONNECTOR_UNAVAILABLE`。
- 单连接池并发测试证明 Adapter Probe 不占用数据库事务；Probe 期间发生 Revision 更新后，最终 CAS 返回 412，不会覆盖并发配置。
- OpenAPI 与前端 TypeScript API 类型已同步；未修改前端页面、组件、DOM、布局、CSS 或既有原型交互。

## P2-05 范围

### 已完成

- `20260713_0008` 为 AccountPool 增加失败阈值与重试冷却策略，为 TestAccount 增加连续失败数、最近检查时间、最近成功时间和 Connector 作用域身份指纹。
- 建立强制 RLS 的 `account_health_check` 与追加式 `account_state_transition`；检查终态和状态迁移由 Constraint / Trigger 保证不可逆，单账号只允许一个 `RUNNING` 检查。
- `POST /v1/test-accounts/{accountId}:verify` 使用“Environment / Connector / Account 短事务快照 → 事务外 Secret 闭包与 Adapter 登录 → Revision CAS 短事务落地”的两阶段协议，外部 I/O 不占数据库连接或行锁。
- 登录成功必须同时通过身份指纹与 `roleKey` 校验；Provider Subject 只转换为 Connector 作用域 SHA-256 指纹，SecretRef、登录名、密码、原始 Subject 与 Provider 原始响应均不进入公共响应、Audit 或 Outbox。
- 账号登录类失败累计到 AccountPool 阈值后进入 `QUARANTINED`；身份不一致、角色漂移、账号锁定和人工处置要求立即隔离；Provider / Network / Secret 基础设施失败保持可重试且不增加账号失败计数。
- Credential Broker 每次运行时登录都复核已验证身份和角色；漂移或认证失败会撤销 Active Lease、推进 Fence、写入健康检查和状态迁移事实，并阻止旧 Session 链继续使用。
- Connector 失效或安全配置变化会撤销 Lease 并让全部关联账号重新验证；账号改绑 Connector 会清除旧作用域身份指纹，同时保持人工 `QUARANTINED` 不被管理更新绕过。
- 数据库约束禁止没有身份指纹和成功检查时间的账号进入 `HEALTHY`；从 `0007` 升级时，旧 `HEALTHY` 投影安全回退到 `UNKNOWN / VERIFYING`。
- 提供健康检查与状态迁移 Cursor 历史 API、幂等重放、If-Match / ETag、双 Tenant RLS、失败阈值、身份 / 角色漂移、缺失 Secret、并发 Revision 和秘密扫描测试。
- OpenAPI 与前端 TypeScript 生成类型已同步；当前原型没有账号健康管理入口，因此未新增页面，也未修改任何现有前端 DOM、布局、CSS 或交互。

## P2-06 范围

### 已完成

- `20260714_0009` 新增强制 RLS 的 `browser_session_artifact` 与 `auth_action_ticket`，以 Constraint、Partial Unique Index 和 Trigger 保证每个 Lease 只有一个活动 Session、终态不可逆，以及 Lease / Account / Credential / Connector 变化后的同步撤销。
- Session Artifact 只在 PostgreSQL 保存作用域、Revision 快照、不透明对象引用、SHA-256 摘要、Key Version 和生命周期；Playwright Storage State 使用 AES-256-GCM 加密后写入 S3-compatible Object Store，AAD 绑定 Tenant、Project、Environment、Lease Fence、Account、Connector、Credential 与 Allowed Origins。
- API 进程只加载 `AuthSessionDispatcher`，通过独立 `atlas-auth-session` Temporal Task Queue 调用 Auth Session Worker；API 不读取 Vault Key、不解密 Storage State、不启动 Playwright，也不直接调用 Secret Provider。
- `POST /internal/v1/account-leases/{leaseId}:ensure-session` 只返回 `browserContextRef` 或受限 `actionTicketId`，响应固定 `Cache-Control: no-store`；ObjectRef、Digest、Key Version、Cookie、Token、SecretRef 与 Storage State 均不进入 HTTP、Audit 或 Outbox。
- Password 自动登录采用“短事务锁定 Lease / Snapshot 并一次性消费 Secret Grant → 事务外 Secret 闭包、Playwright / Provider 登录和加密上传 → 短事务 Revision CAS 发布”的协议；外部 I/O 不持有数据库连接或行锁。
- Playwright Runtime 共享一个 Browser Process，但每次认证使用新的非持久化 `BrowserContext`；禁止视频、Trace 与下载落盘，Storage State 只在受控内存闭包中出现，并以 Worker 级 Semaphore 限制并发。
- OIDC、SAML、TOTP、Manual Bootstrap 或 Provider Challenge 不伪造自动成功，而是创建有 TTL、Origin / Lease / Fence 绑定且单 Lease 唯一的 `AuthActionTicket`。
- Session Janitor 在短事务中以 `SKIP LOCKED` 领取终态 Artifact，在事务外幂等删除密文，再以短事务写入 `DESTROYED` 与安全事件；删除失败保留 `DESTROYING` Claim 供超时重试。
- 真实 PostgreSQL 测试证明 20 路并发只执行一次登录并生成一个密文 Artifact，旧 Fence 被拒绝，跨 Tenant RLS 不可见，Revision Race 无法发布，Lease 撤销后 Artifact 失效并被 Janitor 销毁。
- 真实 Temporal、Chromium 与 MinIO 分别通过 Workflow、隔离 Context 和 AES-GCM 上传 / 解密 / 删除验证；OpenAPI 与前端生成类型已同步，未修改前端原型页面、DOM、布局、样式或交互。

## P3-00/P3-01 范围

### 已完成

- 建立 `atlas.atom/0.1`、`atlas.fixture-blueprint/0.1`、`atlas.compiled-fixture-plan/0.1` 与 `atlas.fixture-manifest/0.1` 领域契约并导出 JSON Schema；只允许引用部署时登记的结构化 Connector Operation，不允许 URL、Module、Callable、Header、Shell、SQL 或任意代码进入协议。
- 静态 Blueprint Compiler 只解析 exact DataAtom Version，验证 Port、JSON Schema Literal、必填输入、SourceRef、Semantic Type、Classification、DAG 与 Export，并生成确定性的执行层级、逆序 Cleanup 顺序与 Plan Digest。
- `20260714_0010` 建立强制 RLS 的 `data_atom_definition`、`data_atom_version`、`data_blueprint_definition` 与 `data_blueprint_version`；Definition 使用 Revision CAS，Published / Deprecated Version 由数据库 Trigger 保证不可修改，应用角色无 DELETE 权限。
- DataAtom CREATE 语义必须同时声明 Resource、Cleanup 与 Reconcile；密码、Secret、Cookie、Token、Storage State 语义类型和 Production Environment 均不能进入当前 Fixture 资产协议。
- DataAtom / DataBlueprint 提供 Definition、Version、Catalog、Validate、Compile 与 Publish API；命令支持 Idempotency-Key，更新使用 If-Match / ETag，目录使用 Cursor Pagination，并按 Tenant / Project RLS 隔离。
- 发布门禁要求 Static Validation、Runtime Validation 与 Cleanup Validation 三类独立 PASSED 证据；DataBlueprint 还必须保存由当前 Revision 编译出的确定性 Compiled Plan。P3-02 写入绑定当前 Version / Digest 的 Runtime Evidence，P3-03 只在正常 `RELEASED` 的 Validation Run 完成真实清理后写入 Cleanup Evidence；失败、取消、泄漏或证据过期时发布继续 fail-closed。
- 既有 Atoms / Assets 原型只在原有两个 DataAtom 卡片和一个 Blueprint 资产槽位读取真实 Catalog；没有数据时保留原型占位内容，未新增页面、未重排 DOM、未修改布局、CSS 或既有交互。
- 真实 PostgreSQL 集成测试覆盖全生命周期、RLS、幂等、Revision CAS、失败编译、确定性编译、发布证据、发布后不可变、无 DELETE 权限和 Audit 安全投影；领域 Compiler、RBAC 与分支行为均有单元测试。

## P3-02 范围

### 已完成

- `20260714_0011` 建立强制 RLS 的 `fixture_run`、`fixture_actor_binding`、`data_node_run`、`data_node_attempt`、`resource_record`、`resource_dependency`、`fixture_manifest` 与 `fixture_validation_evidence`；生命周期、不可变事实、Scope FK、FK 左前缀索引、单 Lease 只能绑定一个 FixtureRun 和应用角色权限由数据库约束。
- API 提供 FixtureRun 启动、详情、Manifest、Resource Ledger 与 Release；`VALIDATION` 只接受 `VALIDATED` exact assets，`EXECUTION` 只接受 `PUBLISHED` exact assets。启动请求冻结 Compiled Plan / Digest、Run Input、Environment、Actor Slot、Account Lease 和 Fencing Token，并要求 Lease TTL 覆盖 `executionDeadline + fixture_cleanup_grace`；控制面使用 Idempotency-Key、RBAC、RLS、Audit 与 Outbox。
- API 只负责控制面和 Temporal dispatch；独立 `atlas-fixture` Task Queue 的 Fixture Worker 执行 Provider I/O。Operation 必须在部署时 exact registry 登记，不接受动态 URL、Module、Script、Header 或请求驱动的 Callable。
- 每次外部 I/O 前先持久化 `DataNodeAttempt=RUNNING`；确定性 Provider 失败记录 `FAILED`，Transport 异常、非法返回或 Worker 重放遇到无终态 Attempt 时保守记录 `OUTCOME_UNCERTAIN`，不盲目重试可能已经生效的 CREATE。
- Temporal Workflow 按编译层级并行执行 DAG，全部成功后冻结显式 Export 的 FixtureManifest 并写入 Atom / Blueprint Runtime Evidence；PostgreSQL 是权威事实，Temporal History 不替代业务记录。
- Resource Ledger 在 Postcondition 前写入不透明资源引用及依赖；只有 `CREATED` 所有权进入自动清理，`ADOPTED / LEASED / SHARED` 不会被误删。正常 Release 与节点失败均按逆拓扑清理已创建资源并释放绑定 Lease。
- 真实 PostgreSQL 和 Temporal 测试覆盖 READY → CLEANING → RELEASED、部分执行失败清理、Provider 显式失败、非法返回、Transport 不确定、幂等重放、Manifest / Evidence、Lease 释放和发布门禁；只更新生成的 OpenAPI / TypeScript 类型，未修改前端原型页面、DOM、布局、样式或交互。

## P3-03 范围

### 已完成

- `20260714_0012` 为 FixtureRun 增加不可逆 `terminalIntent`、取消请求与 Cleanup Generation，为 DataNodeRun 增加 Reconcile 状态机，并新增强制 RLS 的 `data_node_reconcile_attempt` 与 `resource_cleanup_attempt`；状态迁移、Attempt 终态、Scope、索引、权限和发布证据绑定均由数据库约束。
- `OUTCOME_UNCERTAIN` 的 CREATE 不再盲重试：Fixture Worker 先持久化 Reconcile Attempt，再在事务外调用 exact registry。`FOUND` 恢复原始输出和资源账本，`ABSENT` 只允许一次有界、安全的 CREATE 重试，`INCONCLUSIVE` 按配置退避，耗尽后进入 `EXHAUSTED` 并按泄漏处理。
- API 提供 `POST /v1/fixture-runs/{runId}:cancel` 与 `POST /v1/fixture-runs/{runId}:retry-cleanup`；内部 API 提供有界 `POST /internal/v1/fixture-cleanup:sweep`。控制面只 dispatch，Provider I/O 仍由独立 Fixture Worker 在短事务 Claim 之外执行。
- Temporal FixtureRun Workflow 同时处理业务取消信号和原生 Workflow Cancellation，并在 `finally` 中进入补偿；独立 Cleanup Workflow 负责显式重试，Tenant Sweep Workflow 负责到期重试、过期孤儿扫描以及 Reconcile / Cleanup stale claim 恢复。
- Cleanup Attempt 使用 Generation 和 Worker Claim 保证幂等与所有权；Transient Failure 保留待重试，Permanent Failure 或重试耗尽进入 `LEAKED`。每次 Provider Cleanup 前重新校验 Lease / Connector / Fence，终态且已释放 Fence 的隔离资源不会被不安全地再次删除。
- Cleanup Evidence 只由完成真实清理且终态意图为 `RELEASED` 的 Validation Run 产生；取消、失败、未决 Reconcile、泄漏或清理失败均不能伪造发布条件。资产 Contract、Validation 或 Compile Revision 变化时，旧 Cleanup Evidence 自动失效。
- 真实 PostgreSQL 与 Temporal 测试覆盖 Reconcile `FOUND / ABSENT`、有界 CREATE 重试、业务信号取消、原生 Temporal Cancellation、Transient Cleanup Retry、Sweeper、孤儿与 stale claim 恢复、Cleanup Evidence 和发布门禁；只更新生成的 OpenAPI / TypeScript 类型，未修改前端原型页面、DOM、布局、样式或交互。

## P4-00/P4-01 范围

### 已完成

- 建立 `atlas.test-intent/0.1`、`atlas.test-ir/0.2`、`atlas.plan-template/0.1` 与 WorkflowPatch 领域契约，并导出版本化 JSON Schema；Test IR 只接受精确 Requirement、Actor、Fixture、Surface 与 Node Version 引用，不接受动态 URL、Selector、Header、Cookie、Token、Script、Shell、SQL 或任意代码能力。
- 确定性 Case Compiler 对 WorkflowGraph 做稳定排序，验证需求锚点、角色、Fixture、Surface、HARD Oracle 与禁用能力，生成内容摘要一致的 Test IR 和 PlanTemplate；编译不执行外部 I/O，也不解析 floating version。
- WorkflowPatch 同时服务 AI 与人工作者，区分 Patch 可应用性和当前图的业务有效性；结构性悬空引用不能持久化，但缺少必填输入等暂时无效作者态可以保存并明确标记，便于后续继续修图。
- `20260715_0013` 建立强制 RLS 的 `test_case`、`workflow_draft`、`workflow_node`、`workflow_edge` 与追加式 `draft_operation`；Scope FK、外键索引、部分索引、最小权限和 Trigger 保证租户隔离、身份不可变、无越权删除以及 semantic / layout Revision 每次只推进一条轴。
- API 提供 TestCase 创建、Catalog、详情、WorkflowDraft 读取、Patch 预检/原子应用和 Layout 更新；写命令使用 Idempotency-Key，语义与布局分别使用 If-Match / ETag CAS，并在同一短事务内写入 DraftOperation、Audit 与 Outbox。
- RBAC 将 `CASE_AUTHOR` 与 `CASE_REVIEWER` 分离；跨 Tenant 资源统一返回 404。Audit / Outbox 只保存 Revision、Digest、有效性和来源，不保存 Requirement 正文、Workflow 参数或 Intent 内容。
- 前端只新增 OpenAPI 生成类型和 `lib/api/cases.ts` 的 SWR / mutation 适配层，未修改现有 Cases / Case Canvas 原型页面、DOM、布局、CSS 或交互；后续接线继续以既有前端原型为唯一视觉与交互权威。
- 真实 PostgreSQL 测试覆盖双 Tenant RLS、幂等重放、语义与布局双 Revision、412 冲突、暂时无效图、结构冲突和追加式操作历史；迁移完成 downgrade / upgrade 往返，全量门禁通过 303 tests，覆盖率 90.32%。

## P4-02 范围

### 已完成

- 建立 `DebugRunLifecycle`、`DebugRunOutcome` 与 `DebugRunSnapshotStatus` 三个正交投影；运行快照固定 Draft ID、semanticRevision、semanticDigest、Test IR、PlanTemplate、compiledDigest、Environment 与 executionDeadline，模型和数据库同时校验嵌套 Digest 一致性。
- `20260715_0014` 建立强制 RLS 的 `debug_run` 与追加式 `debug_run_event`；Scope FK、不可变快照 Trigger、生命周期约束、当前成功结果 Partial Index、最小权限和事件 Trigger 保证状态与主运行一致、`seq` 无间隙且不能更新或删除历史事件。
- `PASSED` 只能在 `TERMINATED` 且绑定完整 `evidenceManifestId + digest` 时写入；当前没有 P6 Browser Worker / Evidence Service，因此公共控制面没有完成或写入结果的接口，不会用 Mock、API 请求或 Agent 自报伪造通过。
- API 提供 Draft DebugRun 启动、TestCase 历史、详情、`afterSeq` 事件增量读取和幂等取消；创建时共享锁定 Case / Project / Environment、排他锁定 Draft、复核 If-Match 与 semanticDigest，再做确定性编译并冻结快照。Production Environment、Archived Case / Project、Disabled Environment 和编译失败均被拒绝。
- Browser Runtime 通过独立 `DebugRunDispatcher` 端口注入。未配置时在任何写事实前返回 `DEBUG_RUNTIME_UNAVAILABLE`；已冻结后 dispatch 或 cancel signal 暂时失败时保留权威请求，调用方用相同 Idempotency-Key 重放，不重复创建 Run 或事件，也不改变 Outcome。
- semantic Patch 与 DebugRun 采用统一 Draft → Run 锁顺序；语义变更在同一事务把旧快照标记 `OUTDATED` 并追加事件，layout-only Revision 不影响 DebugRun。跨 Tenant 探测在检查 Runtime 可用性前统一返回 404。
- 前端仅更新生成 OpenAPI 类型和 `lib/api/cases.ts` 的 SWR / mutation adapter，使用稳定 key 去重 Catalog、详情和事件读取；未修改 Cases、Case Canvas、Debug 或 Publish 原型页面、组件、DOM、布局、CSS 或既有交互。
- 真实 PostgreSQL 测试覆盖 runtime 缺失、dispatch / cancel signal 失败重放、Production 拒绝、跨 Tenant 隐藏、Digest 冻结、layout 不失效、semantic 自动过期、事件顺序/状态防绕过、无证据 PASS 拒绝和最小权限；迁移完成 `0014 → 0013 → 0014` 往返，全量门禁通过 306 tests，覆盖率 90.39%。

## P4-03 范围

### 已完成

- 建立 `atlas.case-version/0.1` 机器契约；CaseVersion 冻结 Test Intent、规范化 Workflow Graph、Test IR、PlanTemplate、semantic / intent / compiled / content Digest、DebugRun 与 Evidence Manifest 引用，并在 Pydantic 模型中交叉校验所有嵌套快照。
- `20260715_0015` 建立强制 RLS 的 `case_version`、`case_version_node` 与 `case_version_edge`；Scope FK、复合/部分索引、最小权限和 Trigger 共同保证发布内容与 provenance 不可更新、节点和 Edge 不可修改或删除、应用角色不能删除版本，且不提供 `latest` 或浮动引用。
- 发布事务按 Case / Project 共享锁、Draft 排他锁、精确 Role / Fixture 共享锁、DebugRun 共享锁的固定顺序执行；在同一短事务内复核 If-Match、Draft / Intent Digest、确定性编译、Actor Revision / Capability、已发布 Fixture 的 Static / Runtime / Cleanup 证据、DebugRun 的 CURRENT + TERMINATED + PASSED 及三类 Digest 一致性。
- 当前语义 Author 由最新 SEMANTIC DraftOperation（初始版本回退到不可变创建 Audit）解析；发布要求可审计 Reviewer Actor、`CASE_REVIEWER` 权限且 Reviewer 与 Author 不是同一 Actor，数据库再次用约束阻止自审版本进入事实表。
- API 提供 `POST /v1/test-cases/{caseId}:publish`、`GET /v1/test-cases/{caseId}/versions` 与 `GET /v1/case-versions/{versionId}`；发布使用 Idempotency-Key + clientMutationId、If-Match CAS、精确 DebugRun ID，并原子写入 Audit、Outbox 与可重放 HTTP 结果。
- 前端仅新增生成 TypeScript 类型以及 `lib/api/cases.ts` 的 CaseVersion SWR / publish adapter；未修改 Cases、Case Canvas、Debug、Publish 原型页面、DOM、布局、CSS 或既有交互。
- 真实 PostgreSQL 测试覆盖无成功试运行、自审、过期 DebugRun、重复版本 / 证据、幂等重放、跨 Tenant 404、发布后 Draft 继续演进而历史版本不变、节点 / Edge 不可变与无 DELETE 权限；迁移完成 `0015 → 0014 → 0015` 往返，全量门禁通过 310 tests，覆盖率 90.49%。

## P6-00 范围

### 已完成

- 建立 `atlas.execution-contract/0.1`、`atlas.assertion-result/0.1` 与 `atlas.evidence-manifest/0.1` 机器契约并导出 JSON Schema。ExecutionContract 冻结 exact Test IR / Plan、FixtureRun / FixtureManifest、Actor Role Revision、Lease / Fence、BrowserContextRef、Browser Revision、Locale / Timezone、Model / Prompt / Reasoning Policy、Tool / MCP Schema 与 Policy Digest。
- 确定性 Oracle 只接受与冻结 Assertion Program 匹配的结果输入，不接受调用方提供 Case outcome；HARD Failure、缺失 / 不确定证据和完整 Verified Pass 使用固定规则推导 `FAILED / INCONCLUSIVE / PASSED`，Observation、Artifact 和 Finalization 全部受 ExecutionContract 时间窗约束。
- `20260715_0016` 建立强制 RLS 的 `execution_contract`、`execution_contract_actor_binding`、`assertion_result`、`evidence_artifact` 与 `evidence_manifest`。Scope FK、不可变 Trigger、复合 / 部分索引和 `SELECT/INSERT` 最小权限保证事实不可覆盖；数据库再次复核 `debug-run:{id}` execution scope、Role / Lease / Fence / Session、Fixture exports、Assertion / Artifact 引用，并重新推导 completeness、integrity 与 outcome。
- 新增内部 `DebugRuntimeService`，按 `CREATED → BINDING → READY → RUNNING → FINALIZING → TERMINATED` 推进 DebugRun，并在每一步原子写入 DebugRunEvent、Audit 与 Outbox。绑定与终结均支持同一精确命令幂等重放；不同 Contract 或 Evidence 命令被拒绝。
- CaseVersion 发布不再只相信 DebugRun 上的 Manifest ID / Digest，而是加载实际 EvidenceManifest，复核 ExecutionContract、Test IR、Plan、Fixture、Outcome、Completeness 与 Integrity 全部一致。旧 P6 前无证据 `PASSED` 和迁移时仍活动的旧 Run 会安全回退为 `INCONCLUSIVE`。
- P6-00 当时没有公共 Runtime 完成 API，也未提供 Browser Worker、Live Action/Event、Artifact 字节验证或 View Token；P6-01 已补齐受信内部 Worker 协议，公共完成 API 仍保持关闭。
- `AttemptSeal` 明确延后到 P5 创建正式 `UnitAttempt` 后，不创建属于 DebugRun 的无宿主空协议。
- 前端仅更新 OpenAPI 生成类型；未修改任何现有页面、组件、DOM、布局、CSS 或交互，继续以前端原型为唯一视觉与交互权威。

## P6-01 范围

### 已实现

- 建立 `atlas.browser-execution-bundle/0.1`、`atlas.browser-runtime-report/0.1` 与加密 `atlas.browser-context-restore-envelope/0.1` 协议。执行包只暴露冻结 Contract / Test IR / Plan、Fixture Export 和每 Actor 的密文 Restore Envelope，不把 Storage State、ObjectRef 或 Vault Key 放入 Temporal History。
- `20260715_0017` 新增强制 RLS、不可更新/删除的 `browser_runtime_report`，以及 EvidenceManifest `finalization_command_digest`。数据库 Trigger 约束首条 `execution.started`、唯一 Started、完成后禁写、单调时间与终结时精确 Chain Head / Count；应用层再约束类型化 Payload、无间隙 Sequence、同一 `actionId` 不可跨 Action 链复用和连续 Proposal → Policy → ALLOW Receipt，不允许其他普通 Report 插入一个未闭合 Action；Policy 后仅可用 `execution.blocked` 代替无法形成的可信 Receipt。
- 新增只面向机器身份的 Browser Runtime 内部 API：读取执行包、Ready、Start、追加 Report 和 Finalize Evidence。Temporal Dispatcher 签发 exact Tenant / Run / Worker / Deadline 的短期 Permit；每个请求还以独立 HMAC Key 签名 Method、Path、Scope、Timestamp、Nonce、Body 与 Permit Digest，响应使用 `no-store`，请求体有界。Local / Test / Development 可使用 HTTP 调试，Staging / Production 的 Browser Worker 配置非 HTTPS Runtime API 时在启动前拒绝。
- 新增独立 `atlas-browser-worker` 进程与 Docker Target。Worker Settings 不包含控制面 `database_url`，Browser Workflow 只在已经绑定 exact ExecutionContract 后启动；一个有 Heartbeat 的长时 Activity 承载副作用并禁止自动 Activity Retry，Temporal 结果仅返回安全终态摘要。
- BrowserContext Restore Descriptor 使用 AES-256-GCM Envelope 和 Contract-bound AAD 投递；Worker 在内存中核对 Tenant / Project / Environment、Lease / Fence、Worker、Actor、Context Ref、Key Version 与 Expiry，再经 SessionArtifact Vault 恢复隔离的非持久化 Playwright Context。
- Playwright Adapter 校验实际 Playwright / Chromium Revision、Tool / Policy / MCP Digest 和 executable Action 集；只解析部署时 exact Operation / Published Route Registry。HTTP 与 WebSocket 都限制在 Session Scope 的精确 Origin，绝对 URL、动态 Module、Script、Callable 与任意 Locator 不能从 Agent / 资产 / HTTP 注入。
- DOM Action 绑定当前 Observation 的 retained `ElementHandle`、Page Revision、Semantic Fingerprint 与单次 Nonce，执行前重新核对可见性、Element Key 和 Accessible Name。Action ID / Grant 只消费一次；Report Chain 出现 `execution.blocked` 或任一 Receipt 为 `FAILED / OUTCOME_UNKNOWN` 时，全部 Assertion 与最终 Outcome 强制为 `INCONCLUSIVE`，不能由 Operation 或后续证据覆盖。
- Browser Operation 只能经 `BrowserToolSession` 把原始证据字节交给可信 `BrowserArtifactWriter`；Operation 直接构造或返回 `EvidenceArtifactInput` 必须拒绝，即使其元数据与 `VERIFIED` 字段形状完整也不能进入终结输入。
- Report Payload 限制大小、嵌套深度、敏感文本和绝对 URL；链首、Sequence、时间、Previous Digest、Content Digest、Action 连续性与链尾均严格校验。每条 Assertion / Artifact Report 携带完整 Input 的 Canonical Digest，Finalization 对 Command 中每个完整 `AssertionResultInput` / `EvidenceArtifactInput` 重算 Digest 并匹配 exact Report 集合；同一完整命令的 replay 再由持久化 Finalization Command Digest 约束。
- P6-01 未修改任何前端页面、组件、DOM、布局、CSS 或既有交互。前端原型继续是唯一视觉与交互权威，后续能力只能接入已有 Debug / Live / Evidence 槽位。

### 当前 fail-closed 边界

- 生产 `BrowserArtifactWriter` / Evidence Redaction、对象存储写入、独立 Hash 与 Integrity Verification 尚未实现，属于 P6-02；未配置时 `CAPTURE_VIEW` 拒绝执行。
- 默认 Browser Operation / Route Registry 为空；首个真实 SaaS exact Operation 与 Published Route 必须由部署代码注册，不能用通用动态脚本替代。
- Playwright HTTP / WebSocket Routing 不是完整网络沙箱。生产容器仍需部署 Egress、DNS、UDP / WebRTC 限制。
- BrowserContext Envelope 目前只支持一个活动 Key Version；生产 Key Ring、Rotation 与旧 Key 有界解密窗口尚未落地。
- 公共 DebugRun Start 尚未自动完成 Runtime Preparation、ExecutionContract Bind 与 Browser Dispatch；P6-01 只消费已经正确绑定的 Contract。
- 当前只支持单 Actor；Multi-actor Context、并行调度、Lease 与控制权仲裁延后。

### 验证状态

- Alembic `20260715_0017 → 20260715_0016 → 20260715_0017` 往返通过；报告表、EvidenceManifest Finalization Digest、Trigger、RLS 与最小权限可重复部署。
- 真实 PostgreSQL、Temporal 与 Chromium 验证覆盖内部机器认证、Report / Finalization 幂等与状态机、DOM 重排后目标不漂移、WebSocket Origin、Browser Revision、失败 Receipt、Context Envelope、Worker 装配和原生 Temporal Cancellation。
- `make verify` 通过：389 tests、覆盖率 90.10%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python sdist / wheel、TypeScript 与前端生产构建全部成功；仅更新生成类型，前端原型页面未改。

### 下一步

1. P6-02 落地生产 Evidence / Redaction Writer、Evidence Object 验证、Live Event / Action、短期 View / Read Token 和断线重放；只映射到前端现有 Debug / Live / Evidence 槽位，不重画或调整原型结构。
2. 串联公共 DebugRun Start → Runtime Preparation → ExecutionContract Bind → Browser Dispatch，并保持同一命令的幂等恢复语义。
3. 接入首个真实 SaaS Browser Operation / Published Route，并在部署层实施容器 Egress / DNS / UDP / WebRTC 策略与 Envelope Key Ring Rotation。
4. P5 建立 TaskPlan / TaskRun / ExecutionUnit / UnitAttempt 后，再在 P6 后续切片创建 AttemptSeal，并在 P7 形成 Result Snapshot / Gate；Multi-actor 同时等待正式调度与控制权协议。
5. 接入首个真实 SaaS Fixture Provider 与 `PasswordLoginFlow`、生产 Secret Provider 和 KMS-backed `SessionArtifactVault`；缺少受信部署配置时继续 fail-closed。
6. 为各 Tenant 配置生产 Temporal Schedule，周期调度 Fixture Cleanup Sweep、`AccountHealthWorkflow`、Connector Reconcile、Credential Expiry Monitor 和 Session Janitor Workflow。

## 验证记录

| 日期 | 范围 | 命令或证据 | 结果 |
| --- | --- | --- | --- |
| 2026-07-13 | 后端基线 | `ruff`、`mypy`、`pytest`、Schema check、`uv build` | 通过；13 tests，覆盖率 95.95% |
| 2026-07-13 | 前端基线 | `npm run lint`、`npm run build` | 通过 |
| 2026-07-13 | Word 文档 QA | 8 documents / 325 pages render inspection | 通过；未发现截断或溢出 |
| 2026-07-13 | P0 数据库 | Alembic downgrade/upgrade；真实 RLS、Audit、Outbox、Idempotency | 通过 |
| 2026-07-13 | P0 Temporal | 真实 Server + Worker + Probe Workflow | 通过 |
| 2026-07-13 | P0 全量门禁 | `make verify` | 通过；46 tests，覆盖率 94.39% |
| 2026-07-13 | P0 容器 | API、Migration、Temporal Worker 镜像及运行探针 | 通过 |
| 2026-07-13 | P0 前端 QA | `/system-status`，桌面及 390×844，刷新交互 | 通过；无 Console Error、无横向溢出 |
| 2026-07-13 | P1-01 Platform | Tenant / Project / Environment API、双 Tenant RLS、幂等与 Revision | 通过；真实 PostgreSQL |
| 2026-07-13 | P1-02 Identity Gateway | Argon2id、Cookie Session、RBAC、锁定、撤销、审计与 Outbox | 通过；88 tests，覆盖率 95.38% |
| 2026-07-13 | P1 Migration | `20260713_0003` downgrade → upgrade → Auth API | 通过 |
| 2026-07-13 | P1 前端 QA | `/login` 失败/成功/Feishu 未配置、首页 Session Project | 通过；无 Console Error / Warning，未修改原型 CSS |
| 2026-07-13 | P1 全量门禁 | `make verify` | 通过；后端、契约、包构建与前端生产构建全部通过 |
| 2026-07-13 | P2-01 Identity Catalog | TestRole / AccountPool / TestAccount / AccountSlot / CredentialBinding、RLS、SecretRef 与容量 | 通过；真实 PostgreSQL |
| 2026-07-13 | P2 Migration | `20260713_0004` downgrade → upgrade → Identity API | 通过 |
| 2026-07-13 | P2-01 前端 QA | 既有 Identity Wallet 四卡接入真实 API | 通过；6 个目录请求全为 200、无 Console Error / Warning / Issue、未修改原型 CSS |
| 2026-07-13 | P2-01 全量门禁 | `make verify` | 通过；97 tests、覆盖率 91.43%、契约、Python 包与前端生产构建全部通过 |
| 2026-07-13 | P2-02 Lease / Fencing | 100 路并发 Acquire、20 轮 Pool Disable 对撞、5 类管理撤销、TTL / Reaper | 通过；真实 PostgreSQL，无重复 Slot、无遗留 Active Lease、旧 Fence 被拒绝 |
| 2026-07-13 | P2 Migration | `20260713_0005` downgrade → upgrade → Lease API | 通过；新增 Release Reason 约束已往返验证 |
| 2026-07-13 | P2-02 全量门禁 | `make verify` | 通过；113 tests、覆盖率 92.40%、契约、Python 包、TypeScript 与前端生产构建全部通过 |
| 2026-07-13 | P2-03 Secret Grant / Adapter | Hash-only Grant、20 路原子 Redemption、Origin / Worker / Fence、TTL、Credential / Lease 撤销、秘密扫描 | 通过；真实 PostgreSQL，最多一次消费，持久化与事件中无原始 Grant/Secret |
| 2026-07-13 | P2 Migration | `20260713_0006` downgrade → upgrade；应用层与 PostgreSQL Origin 规范化约束 | 通过；当前 Head 为 `20260713_0006`，非规范端口、Host 与默认端口被拒绝 |
| 2026-07-13 | P2-03 契约 | OpenAPI → TypeScript、SecretGrant no-store API、稳定 Problem Details 错误码 | 通过；只更新生成类型，未修改前端原型结构与样式 |
| 2026-07-13 | P2-03 全量门禁 | `make verify` | 通过；146 tests、覆盖率 91.61%、严格 mypy、契约漂移、Python 包与前端生产构建全部通过 |
| 2026-07-13 | P2-04 Connector / Capability | Connector CRUD、事务外 Probe、Revision CAS、RLS、Origin / Mode、账号绑定、Lease / Grant 级联失效 | 通过；真实 PostgreSQL，157 tests、覆盖率 91.41% |
| 2026-07-13 | P2 Migration | `20260713_0007` upgrade → downgrade `0006` → upgrade `0007` | 通过；当前 Head 为 `20260713_0007`，约束、Trigger、RLS 与旧 Grant 兼容恢复成功 |
| 2026-07-13 | P2-04 契约 | OpenAPI → TypeScript、Connector 管理与验证 API | 通过；仅更新生成类型，未改动前端原型结构、样式或交互 |
| 2026-07-13 | P2-04 全量门禁 | `make verify` | 通过；157 tests、覆盖率 91.41%、严格 mypy、契约漂移、Python 包与前端生产构建全部通过 |
| 2026-07-13 | P2-05 Account Health | 登录 / 身份 / 角色探针、阈值隔离、运行时失败闭环、RLS、Revision CAS、秘密扫描 | 通过；真实 PostgreSQL，身份与角色漂移立即隔离，基础设施失败不误累计 |
| 2026-07-13 | P2 Migration | `20260713_0008` upgrade → downgrade `0007` → upgrade `0008` | 通过；当前 Head 为 `20260713_0008`，旧无证据 `HEALTHY` 账号安全回退，约束 / Trigger / RLS 往返成功 |
| 2026-07-13 | P2-05 契约 | OpenAPI → TypeScript、账号验证与两类历史 API | 通过；仅更新生成类型，未修改前端原型页面、结构、布局、样式或交互 |
| 2026-07-13 | P2-05 全量门禁 | `make verify` | 通过；201 tests、覆盖率 91.74%、严格 mypy、契约漂移、Python 包与前端生产构建全部通过 |
| 2026-07-14 | P2-06 Auth Session | 20 路 Single Flight、AES-GCM Storage State、Fence / Origin / Revision CAS、RLS、撤销与 Janitor | 通过；真实 PostgreSQL，无 Session 明文或存储元数据泄漏 |
| 2026-07-14 | P2 Migration | `20260714_0009` upgrade → downgrade `0008` → upgrade `0009` | 通过；当前 Head 为 `20260714_0009`，Constraint、Trigger、Partial Unique Index 与 RLS 往返成功 |
| 2026-07-14 | P2-06 Runtime | 真实 Temporal Workflow、Chromium 隔离 Context、MinIO AES-GCM seal / decrypt / delete | 通过；API 与 Browser / Vault Key 进程边界保持隔离 |
| 2026-07-14 | P2-06 契约 | OpenAPI → TypeScript、ensure-session discriminated response、no-store | 通过；仅更新生成类型，前端原型页面、结构、布局、样式与交互未改动 |
| 2026-07-14 | P2-06 后端门禁 | Python 3.14.6、239 tests、coverage | 通过；239 tests，覆盖率 90.06%、严格 mypy |
| 2026-07-14 | P2-06 全量门禁 | `make verify` | 通过；后端 lint / type / test / contract / package 与前端 type / contract / production build 全部成功 |
| 2026-07-14 | P3-00/P3-01 领域与 API | DataAtom / DataBlueprint Contract、静态 Compiler、Catalog、CRUD、Validate / Compile / Publish | 通过；exact version、DAG、Port、Schema、Classification、RBAC、Idempotency 与 Revision CAS 均有测试 |
| 2026-07-14 | P3 Migration | `20260714_0010` upgrade → downgrade `0009` → upgrade `0010` | 通过；Constraint、Trigger、Index、RLS、发布后不可变与无 DELETE 权限往返成功 |
| 2026-07-14 | P3-00/P3-01 PostgreSQL | 双 Tenant RLS、完整资产生命周期、确定性编译、三类发布证据与安全 Audit | 通过；真实 PostgreSQL，Published 版本不可修改，协议正文不进入 Audit Payload |
| 2026-07-14 | P3-00/P3-01 前端 | 既有 Atoms / Assets 原型接入真实 DataAtom / DataBlueprint Catalog | 类型检查与生产构建通过；只替换既有数据槽位，未修改布局、样式或原型结构；应用内 Browser 插件初始化异常，渲染复核待恢复后补做 |
| 2026-07-14 | P3-00/P3-01 全量门禁 | `make verify` | 通过；247 tests、覆盖率 90.52%、严格 mypy、契约漂移、Python 包与前端生产构建全部成功 |
| 2026-07-14 | P3-02 Runtime / API | FixtureRun、Node Attempt、Resource Ledger、Manifest、Runtime Evidence、exact registry 与独立 Worker | 通过；真实 PostgreSQL / Temporal，正常释放、部分失败清理与不确定结果均有验证 |
| 2026-07-14 | P3 Migration | `20260714_0011` downgrade `0010` → upgrade `0011` | 通过；Constraint、Trigger、Scope FK、Index、RLS、Privilege 与可选非 CREATED cleanup metadata 往返成功 |
| 2026-07-14 | P3-02 契约 | OpenAPI → TypeScript、FixtureRun / Manifest / Resource API | 通过；仅更新生成契约和类型，未修改前端原型页面、结构、布局、样式或交互 |
| 2026-07-14 | P3-02 全量门禁 | `make verify`、`docker compose config --quiet` | 通过；264 tests、覆盖率 90.04%、严格 mypy、契约漂移、Python 包与前端生产构建全部成功 |
| 2026-07-14 | P3-03 Runtime / API | 取消补偿、Reconcile、Cleanup Retry / Sweeper、孤儿与 stale claim 恢复、Cleanup Evidence | 通过；真实 PostgreSQL / Temporal，覆盖业务信号与原生 Cancellation、`FOUND / ABSENT / INCONCLUSIVE / EXHAUSTED`、Transient / Permanent Cleanup Failure 与发布门禁 |
| 2026-07-14 | P3 Migration | `20260714_0012` downgrade `0011` → upgrade `0012` | 通过；状态机 Constraint、Attempt Guard、Scope FK、Index、RLS、Privilege 与旧数据 fail-closed 修复往返成功 |
| 2026-07-14 | P3-03 契约 | OpenAPI → TypeScript、cancel / retry-cleanup / cleanup sweep 与 Attempt 安全投影 | 通过；仅更新生成契约和类型，未修改前端原型页面、结构、布局、样式或交互 |
| 2026-07-14 | P3-03 全量门禁 | `make verify` | 通过；286 tests、覆盖率 90.08%、ruff、严格 mypy、契约漂移、Python 包与前端生产构建全部成功 |
| 2026-07-15 | P4-00/P4-01 协议与 API | Test Intent / Test IR 0.2 / PlanTemplate、WorkflowPatch、TestCase Catalog、WorkflowDraft 双 Revision | 通过；确定性摘要、RBAC、幂等、CAS、Audit / Outbox 与前端类型适配均已验证 |
| 2026-07-15 | P4 Migration | `20260715_0013` upgrade → downgrade `0012` → upgrade `0013` | 通过；Scope FK、Index、Trigger、强制 RLS、最小权限与 DraftOperation 追加历史往返成功 |
| 2026-07-15 | P4-00/P4-01 全量门禁 | `make verify` | 通过；303 tests、覆盖率 90.32%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python 包、TypeScript 与前端生产构建全部成功 |
| 2026-07-15 | P4-02 DebugRun 控制面 | 不可变 Draft snapshot、dispatch / cancel 重放、OUTDATED、事件 replay、无证据 PASS 拒绝 | 通过；真实 PostgreSQL、跨 Tenant 404、Production deny、最小权限与事件数据库防绕过均已验证 |
| 2026-07-15 | P4-02 Migration | `20260715_0014` upgrade → downgrade `0013` → upgrade `0014` | 通过；Scope FK、状态 / Digest / Evidence Constraint、Trigger、Partial Index、强制 RLS 与 Privilege 往返成功 |
| 2026-07-15 | P4-02 全量门禁 | `make verify` | 通过；306 tests、覆盖率 90.39%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python 包、TypeScript 与前端生产构建全部成功 |
| 2026-07-15 | P4-03 CaseVersion 发布门禁 | 当前 Draft 复编、精确 Role / Fixture、成功 DebugRun、Author / Reviewer 分离、冻结快照 | 通过；真实 PostgreSQL 覆盖幂等、过期证据、跨 Tenant、不可变 Trigger 与最小权限 |
| 2026-07-15 | P4-03 Migration | `20260715_0015` upgrade → downgrade `0014` → upgrade `0015` | 通过；Scope FK、复合/部分索引、强制 RLS、不可变 Trigger 与 Privilege 往返成功 |
| 2026-07-15 | P4-03 全量门禁 | `make verify` | 通过；310 tests、覆盖率 90.49%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python 包、TypeScript 与前端生产构建全部成功 |
| 2026-07-15 | P6-00 Trusted Runtime | ExecutionContract exact binding、Oracle fail-closed、EvidenceManifest、幂等状态推进、CaseVersion 实证复核 | 通过；真实 PostgreSQL 覆盖 Fixture / Lease execution scope、Role / Fence / Session、证据时间窗、结果防伪、跨 Tenant RLS 与不可变事实 |
| 2026-07-15 | P6-00 Migration | `20260715_0016` downgrade `0015` → upgrade `0016` | 通过；旧活动 / 无证据成功 Run 安全回退，Scope FK、Trigger、Index、RLS、Privilege 和 P6 Evidence 降级清理往返成功 |
| 2026-07-15 | P6-00 全量门禁 | `make verify` | 通过；319 tests、覆盖率 91.05%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python sdist / wheel、TypeScript 与前端生产构建全部成功 |
| 2026-07-15 | P6-01 Browser 执行平面 | Permit + HMAC 内部网关、Temporal Activity、Context Envelope、Report Chain、受限 Playwright | 通过；真实 PostgreSQL / Temporal / Chromium 覆盖精确绑定、幂等、DOM 目标稳定、Origin、失败闭合与 Worker 无数据库装配 |
| 2026-07-15 | P6-01 Migration | `20260715_0017` downgrade `0016` → upgrade `0017` | 通过；Append-only Report、Evidence Finalization Digest、Trigger、RLS、Privilege 与 CaseVersion Evidence 流程往返成功 |
| 2026-07-15 | P6-01 全量门禁 | `make verify` | 通过；389 tests、覆盖率 90.10%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python sdist / wheel、TypeScript 与前端生产构建全部成功 |

## 当前风险与外部输入

- 首个真实 SaaS Connector、`PasswordLoginFlow` 和测试账号来源尚未提供；当前交付 Mock Provider、Generic Password Adapter 与可注入的 Playwright Target。
- 独立 Auth Session Worker 与加密 Vault 端口已经落地；生产 Secret Provider、KMS-backed Vault 和生产 Object Store 配置尚未提供，缺失时 Password Session fail-closed。
- 周期性 AccountHealthWorkflow / Identity Reconciler / Tenant Session Janitor 尚未调度；当前已覆盖手工触发、Temporal Workflow 契约、运行时失败触发与单批 Janitor。
- Feishu PlatformPrincipal OAuth 尚未提供 Client Secret、Redirect URI 与权限范围；当前入口不会模拟成功。
- 生产对象存储和 Secret Manager 尚未指定；代码只依赖抽象接口，本地采用 S3-compatible 与不可逆的 Secret 引用。
- 试点项目、黄金用例和真实业务 API 契约尚未提供；P0-P1 不依赖这些输入，P2 之后需要逐步补齐。
- P3-03 已完成取消后补偿、Reconcile、Cleanup Retry / Sweeper、孤儿扫描与 Cleanup Evidence；生产环境仍需按 Tenant 配置 Temporal Schedule 和真实 Provider，缺失时继续 fail-closed。
- P6-01 已实现独立无数据库 Browser Worker、Permit + HMAC 内部网关、Temporal Activity、加密 Context Restore、严格报告链与受限 Playwright Adapter；生产 Evidence / Redaction Writer、对象字节验证、Live / View Token、真实 SaaS Operation / Route Registry、容器网络沙箱、Envelope Key Ring、公共 Start 自动 Preparation / Bind / Dispatch 和 Multi-actor 尚未实现，缺少对应能力时继续 fail-closed。
- 应用内 Browser 插件当前初始化报 `Cannot redefine property: process`；前端类型与生产构建已验证，服务保持可访问，自动化渲染回归需在插件恢复后补做。
