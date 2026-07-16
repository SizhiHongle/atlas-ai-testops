# Atlas AI 测试平台实施进度

更新时间：2026-07-16

## 使用规则

本文件记录已经由代码和验证结果证明的进度，不把计划中的能力写成已完成。每个实施切片结束时必须同步更新：

1. 当前阶段与下一步。
2. 已完成的数据库、领域、API、前端和测试证据。
3. 新增或改变的架构决策。
4. 尚未解决的风险和外部依赖。

## 当前状态

- 当前阶段：`P5 Task Runtime（基础中）`
- 当前切片：`P5-00B2A 可靠 Workflow Start Intent 交付层（已验收）`
- 总体状态：P5 已建立正式执行宿主、四类 Profile Version、stable request digest、deterministic Temporal identity、materialization seal、Revision CAS，以及由独立 `atlas_dispatcher` 消费的 durable Start Intent 状态机；真实 `AtlasTaskRunWorkflow` / Activity、公共 Command API、大批次分区物化、AttemptSeal 和 P6-02B2 控制权仍待后续，Consumer 与 Compose 默认关闭，前端继续只按既有原型槽位接线
- 当前分支：`main`
- 当前进入基线提交：`5e6372f`

## 阶段看板

| 阶段 | 范围 | 状态 | 完成证据 |
| --- | --- | --- | --- |
| P0 | 工程基础、契约、数据库、进程入口、前端 API 基础 | 已完成 | 46 tests、真实 PostgreSQL/Temporal、三容器构建、前端浏览器 QA |
| P1 | Tenant、Project、Environment、平台身份与权限 | 已完成 | 88 tests、真实 PostgreSQL RLS/RBAC/Session、登录原型浏览器 QA |
| P2 | TestRole、AccountPool、TestAccount、Lease 与 Auth Session | 已完成 | P2-01 至 P2-06 已验收；身份、租约、Secret Grant、加密 Session 与清理链已闭环 |
| P3 | Atom、Blueprint、Fixture Run 与 Cleanup | 已完成 | P3-00 至 P3-03 已验收；资产、耐久运行、取消补偿、Reconcile、Cleanup Retry / Sweeper 与三类发布证据闭环 |
| P4 | TestCase、WorkflowDraft、DebugRun 与 CaseVersion | 后端完成 | P4-00 至 P4-03 已验收；作者态、不可变 DebugRun、精确绑定、Reviewer 发布门禁与 CaseVersion 冻结闭环已落地 |
| P5 | TaskPlan、TaskRun、ExecutionUnit 与 Temporal 编排 | 基础中 | P5-00A 宿主、P5-00B1 Profile / request digest / Workflow identity / Seal / CAS，以及 P5-00B2A durable Intent Consumer 均已验收；真实 Task Workflow / Activity、大批次物化与公共控制面待后续 |
| P6 | Browser Worker、Live、Evidence 与 AttemptSeal | 基础中 | P6-00 可信事实层、P6-01 Browser 执行平面与 P6-02A 可信截图写入 / 受控读取已验收；P6-02B1 DebugRun Live 安全观察流已实现，P6-02B2 控制权与 AttemptSeal 可基于正式 UnitAttempt 继续落地 |
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

## P5-00A 范围

### 已实现

- 建立 `TaskPlan`、`atlas.task-plan/0.1` `TaskPlanVersion`、`atlas.task-run-manifest/0.1` `TaskRunManifest`、`TaskRun`、`ExecutionUnit`、`UnitAttempt` 与 `TaskExecutionEvent` 领域契约；正式对象链固定为 `TaskPlanVersion → TaskRun → ExecutionUnit → UnitAttempt`，不复用作者态 `DebugRun`。
- Manifest 冻结 CaseVersion、Execution Contract、Fixture Blueprint、Environment、Identity / Browser / Data Profile 的精确 ID 值及 Parameter / Dependency / Policy Digest，并通过 canonical hash 绑定完整 Unit 集。Repository 与 PostgreSQL 双层要求 Policy 覆盖 Plan 的全部同值键、Case 与四个矩阵轴均来自 exact PlanVersion，且 ExecutionContract / Fixture 与 Case Profile 一致；允许编译器增加 resolved policy digest，不虚构完整笛卡尔积。其中 CaseVersion、Environment、Fixture Blueprint Version 已由真实同作用域 FK 验证，另外四类版本 ID 当前是不可变 typed pinned reference slots，尚无正式宿主与发布门禁。业务重试追加新的 `UnitAttempt`，Activity retry 仍属于同一 Attempt。
- Lifecycle、Quality、Hygiene 使用三条独立状态轴。结果允许先进入 CLOSED，Hygiene 随后继续 PENDING / RUNNING / CLEANUP_FAILED retry，最终 CLEANED 或 LEAKED 时才写 `cleanupResolvedAt`，且该时间可晚于 `closedAt`；CLOSED 后 Lifecycle、Quality、身份和既有里程碑仍不可回写。Task 级 Pause 表示停止新派发，不等同于后续浏览器 Safe Point Pause。
- `20260716_0022` 建立 `task_plan`、`task_plan_version`、`task_run`、`task_run_manifest`、`execution_unit`、`unit_attempt` 与 `task_run_event`。PlanVersion 写入时复核 PUBLISHED CaseVersion、ACTIVE TEST/STAGING Environment 与 PUBLISHED Fixture Blueprint 的同作用域宿主；复合 FK、Plan-to-Manifest provenance、Manifest-to-Unit 精确绑定、确定性父行锁、Attempt / Event 无间隙序号、不可变 Trigger、`FORCE RLS` 和无 DELETE 最小权限由 PostgreSQL 执行。Matrix、Profile、Policy 与 Manifest Unit JSONB validator 使用 exact key set，并对缺键、SQL / JSON `NULL` fail-closed。Event 在 Run→Unit→Attempt 顺序锁定最窄 Scope，并以实际里程碑作为 occurredAt 下界。
- `TaskRunRepository` 在调用方数据库事务内按固定顺序创建完整初始聚合，先读取并验证 exact PlanVersion provenance；当前按设计稿 P1 小批次边界最多同步物化 64 Units，超限在首条 SQL 前 fail-closed。Repository 支持 Plan / Version / Run / Manifest / Unit / Attempt 查询、追加 Attempt 和单调 Event replay，同一 immutable fact 只允许 exact replay，冲突不会覆盖已保存历史。事务中不调用 Temporal、HTTP、Playwright 或对象存储。
- 导出 TaskPlanVersion、TaskRunManifest、TaskRun、ExecutionUnit、UnitAttempt 与 TaskExecutionEvent JSON Schema；本切片不增加公共 HTTP API，因此 OpenAPI 与前端生成类型不发生 P5 变化。
- 真实 PostgreSQL 测试覆盖 published CaseVersion 到 Attempt 的完整反向链、PlanVersion 真实依赖门禁、Plan-to-Manifest policy / matrix provenance、原始 SQL 缺键与 JSON `null` 绕过拒绝、trigger replay、Attempt #2、Attempt / Event gap 与冲突、CLOSED 后 Cleanup 推进及事件、窄作用域事件状态/锁/时间下界、跨 Tenant RLS、跨 Project Scope、Version / Manifest / Unit / Attempt / Event 不可变、七表 `FORCE RLS` 与应用角色无 DELETE；迁移完成 `0022 → 0021 → 0022` 往返。
- 全仓门禁通过 586 tests，覆盖率 90.33%；Ruff、mypy、Contract / OpenAPI 漂移、Python sdist / wheel、前端 API 类型、TypeScript 与 production build 全部通过。
- 未修改任何前端页面、组件、DOM、布局、CSS 或既有交互；Launch、Task Control 与 Live Theatre 仍以前端已设计原型为唯一权威。

### 后续边界

- P5-00A 只建立事实宿主和持久化边界，不伪造 Temporal 调度、Command API、Schedule / CI Adapter、AttemptSeal、LiveSession、ControlLease 或 Result Snapshot。
- P5-00B1 已补齐原计划中的四类正式 Profile、稳定 request digest、Temporal Workflow identity、同步 materialization seal 与统一 Revision CAS；P5-00B2A 已实现 Intent Consumer 的可靠交付层。超过 64 Units 的可恢复分区物化、真实 Task Workflow / Activity、Schedule / CI / API 入口仍属于后续切片。随后 P6-02B2 的人工控制事实才能精确绑定 `UnitAttempt`。

## P5-00B1 范围

### 已实现

- 新增 `atlas.execution-profile/0.1`、`atlas.identity-profile/0.1`、`atlas.browser-profile/0.1` 与 `atlas.data-profile/0.1` 四类机器契约和不可变领域宿主；统一将正式 Task 引用命名为 `executionProfileVersionId`，不复用 DebugRun-scoped `ExecutionContract`。
- Profile content digest 覆盖 exact Tenant / Project / version identity 与冻结 contract。Execution Profile 绑定 published CaseVersion 的 content / Test IR / Plan / compiled digest；Identity Profile 绑定 Case actor 与当前 TestRole revision / capabilities；Browser Profile 绑定 Chromium revision / Viewport / Locale / Timezone 与 runtime attestation；Data Profile 绑定 published Fixture Blueprint / compiled plan 与无秘密 Run Inputs。
- `20260716_0023` 建立四类 Profile 表、Identity actor binding、Workflow identity registry 与 Workflow start intent；所有新表启用并强制 Tenant RLS，应用角色仅获 Profile SELECT / INSERT、Registry / Intent SELECT，无 DELETE 权限。Profile JSON 递归拒绝 Password、Token、Credential、Account、Session、Lease 等敏感字段形状；Identity actor 集一旦达到 Profile content digest 即封口，不能继续追加破坏不可变内容。
- TaskPlanVersion / Manifest 的正式字段由 `executionContractVersionId` 迁移为 `executionProfileVersionId`。PostgreSQL 使用与 Python 对齐的 recursive canonical JSON + SHA-256 重算 Profile、PlanVersion、Unit key / dependency、Manifest hash 与 stable request digest，不接受调用方只提供任意格式正确的 digest。
- TaskRun 以 `(tenantId, triggerSource, triggerFingerprint)` insert-or-get；request digest 只覆盖 logical trigger input，不包含服务端 Run ID 与时间。同一自然键只有 digest 与 `rerunOfTaskRunId` lineage 都相同才视为 replay，冲突不会覆盖既有事实。
- Run / Attempt Workflow ID 由 Tenant ID 与对象 ID 确定性生成，并通过 `(namespace, workflowId)` Registry 在 Run / Attempt / Tenant 之间统一占位。历史 P5-00A 行标记为 `legacy_unsealed=true`，保留原业务 revision，但不能 Seal、不能推进状态，也不会伪装成已调度。
- 同步聚合仍限 64 Units。`seal_task_run_materialization` 在 Run 行锁内重算 digest，核对全部 Unit 和 exactly-one first Attempt，重验 PUBLISHED Profile、Case / Fixture、ACTIVE TEST/STAGING Environment 与 TestRole snapshot，成功后写 `SEALED` 计数并原子追加唯一 `PENDING` Start Intent；本切片不消费 Intent 或调用 Temporal。
- 三个 SECURITY DEFINER 状态函数统一使用 Run → Unit → Attempt 锁序和 expected Revision CAS；应用角色对三张状态表的直接列 UPDATE 已撤销。Task Admission 同事务要求父 Run SEALED 且可派发、Unit 仍为 QUEUED，再复验 Profile / Environment / Role drift 与 Run Inputs schema；后续 Attempt 还要求父 Run namespace、可派发生命周期与已 CLOSED 的可重试前序 Attempt，exact replay 与 Revision race 均 fail-closed。
- 新增四类 Profile、Task admission、trusted state repository / service、migration contract 与真实 PostgreSQL 全链用例；未修改任何前端页面、组件、DOM、布局、CSS 或既有交互。

### 已验收

- 完整 `make verify` 通过 651 项测试，覆盖率 90.35%；Ruff、严格 mypy（248 个 source files）、机器 Schema / OpenAPI 漂移检查、Python sdist / wheel、前端 API 类型、TypeScript 与 production build 全部通过。
- 最终 migration 在隔离 PostgreSQL 中完成从空库升级、空库与 populated database 的 `0023 → 0022 → 0023` 往返，并通过 Task execution hosts 全链、跨版本 Debug Live 历史修复、RLS 与最小权限测试。验证期间修复了 backfill trigger、canonical JSON、内部行锁权限、受信函数公开列投影和 v2 → v1 downgrade JSON 兼容转换。
- 3 个真实 Chromium / localhost 用例全部通过，覆盖 DOM 重排后目标稳定、Browser revision 校验与敏感区域截图脱敏。未修改任何前端原型页面、组件、DOM、布局、CSS 或既有交互。

### 后续边界

- `task_workflow_start_intent` 在 B1 中只允许不可变 `PENDING` 事实；P5-00B2A 已在后续 migration 中增加 Claim / Lease / Retry / Started / Failed 状态机和恢复领取，但真实 Temporal Task Workflow / Activity 仍未实现。
- 超过 64 Units 的 Manifest 仍由数据库 fail-closed。后续必须实现可恢复分区物化、分片 checkpoint、Seal 恢复和容量测试，不能扩大当前同步事务。
- 公共 Task Plan / Run / Command / Event API、Schedule / CI / Webhook Adapter、AttemptSeal、Result Snapshot、LiveSession 与 ControlLease 均未在 B1 中伪造完成。

## P5-00B2A 范围

### 已实现

- `20260716_0024` 将 `task_workflow_start_intent` 扩展为 `PENDING → CLAIMED → RETRY_WAIT / STARTED / FAILED` 的数据库权威状态机，增加可信 `manifestHash`、`availableAt`、Claim Token、Claim Lease、Dispatcher Identity、`dispatchAttempts`、安全 Error Code、终态时间和单调 `dispatchRevision`。到期 Claim 可由其他 Consumer 接管，Ready / Expired Claim 均有有界索引；数据库 Trigger 只允许合法状态转换。
- 新增专用登录角色 `atlas_dispatcher`。该角色不是 Superuser、没有 `BYPASSRLS`、没有 Intent 表级 DML，只能执行四个 owner-owned `SECURITY DEFINER` 函数；`atlas_app` 无权 Claim 或确认结果。Claim 还必须显式绑定 Temporal namespace，并只领取 exact `TASK_RUN + AtlasTaskRunWorkflow + atlas-task-run`，防止跨 namespace 或未来 UnitAttempt Intent 被当前 Consumer 误消费。
- 新增独立 `TaskIntentDispatcherDatabase` 与三段式 `TaskWorkflowIntentConsumer`：第一段短事务批量 Claim 并提交，第二段在事务外调用 Temporal，第三段以 exact Intent ID + Claim Token + Revision 的短事务确认 `STARTED`、安排 Retry 或永久 `FAILED`。确认时间与 Retry 可用时间由 PostgreSQL 时钟生成；任何 Lease 过期或 CAS 失配都不会覆盖新 Consumer 的结果。
- Temporal 提交只接受确定性 Run Workflow ID、固定 Workflow Type / Task Queue，以及包含 `schemaVersion + tenantId + projectId + taskRunId + requestDigest + manifestHash` 的无秘密 Input。每次提交使用稳定 `request_id=str(intent.id)`、`REJECT_DUPLICATE + USE_EXISTING`；无论新建还是碰到既有 Workflow，均 `describe` 并精确核对 namespace、Workflow Type、Task Queue 与 Memo identity / digest，碰撞不一致时 fail-closed。
- Temporal RPC 不确定结果只做有界进程内重试；`INVALID_ARGUMENT / PERMISSION_DENIED / UNAUTHENTICATED / NOT_FOUND / FAILED_PRECONDITION / OUT_OF_RANGE / UNIMPLEMENTED / DATA_LOSS` 等永久错误直接进入安全 `FAILED`，依赖异常正文不会持久化。进程崩溃发生在 Start 前、Start 后 Ack 前或 Ack 后时，分别由 Lease 接管、稳定 Request ID + collision verification 或终态排除恢复。
- 新增独立 `atlas-task-intent-consumer` 入口、Docker target 与 Compose profile。`ATLAS_TASK_INTENT_CONSUMPTION_ENABLED` 默认 `false`，Compose 服务还要求显式启用 `task-intent-consumer` profile；只有启用时才允许解包专用 Dispatcher DSN。未注册占位或 no-op Workflow。
- `STARTED` 只表示 Temporal 接受并可验证这次 Workflow Start，不表示 Task 已运行、已成功或已产出结果。PostgreSQL 仍是业务状态和最终事实权威，Temporal History 不替代 TaskRun / Event / Evidence。
- 定向验证覆盖 90 项 Core / Worker / Config / Migration / 真实 PostgreSQL / 真实 Temporal 测试；最终 `make verify` 通过 Ruff、严格 mypy 259 个 source files、712 tests、90.46% 覆盖率、Schema / OpenAPI 漂移、Python sdist / wheel、前端 TypeScript 与 production build。`20260716_0024 → 20260716_0023 → 20260716_0024` 往返和独立 `task-intent-consumer` Docker 镜像构建均已通过。
- 未修改任何前端页面、组件、DOM、布局、CSS 或既有交互；Launch、Task Control 与 Live Theatre 继续以前端已设计原型为唯一权威。

### 后续边界

- `AtlasTaskRunWorkflow`、Unit 调度 Activity、`AtlasUnitAttemptWorkflow` 和对应 Worker 尚未实现；在真实 Worker 就绪前，生产环境必须保持 Intent Consumer 关闭。B2A 不以接受到尚无人处理的 Task Queue 冒充 Task 执行能力。
- 公共 Task Plan / Run / Command / Event API、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化、AttemptSeal、Result Snapshot、LiveSession 与 ControlLease 均未实现。
- 下一 P5 执行切片应先落地最多 64 Units 的真实 `AtlasTaskRunWorkflow` 与可恢复 Unit 调度，再单独处理超过 64 Units 的分区物化、checkpoint / resume 与 Continue-As-New；不得通过放宽当前同步 Seal 或数据库事务伪装大批次能力。

## P6-00 范围

### 已完成

- 建立 `atlas.execution-contract/0.1`、`atlas.assertion-result/0.1` 与 `atlas.evidence-manifest/0.1` 机器契约并导出 JSON Schema。ExecutionContract 冻结 exact Test IR / Plan、FixtureRun / FixtureManifest、Actor Role Revision、Lease / Fence、BrowserContextRef、Browser Revision、Locale / Timezone、Model / Prompt / Reasoning Policy、Tool / MCP Schema 与 Policy Digest。
- 确定性 Oracle 只接受与冻结 Assertion Program 匹配的结果输入，不接受调用方提供 Case outcome；HARD Failure、缺失 / 不确定证据和完整 Verified Pass 使用固定规则推导 `FAILED / INCONCLUSIVE / PASSED`，Observation、Artifact 和 Finalization 全部受 ExecutionContract 时间窗约束。
- `20260715_0016` 建立强制 RLS 的 `execution_contract`、`execution_contract_actor_binding`、`assertion_result`、`evidence_artifact` 与 `evidence_manifest`。Scope FK、不可变 Trigger、复合 / 部分索引和 `SELECT/INSERT` 最小权限保证事实不可覆盖；数据库再次复核 `debug-run:{id}` execution scope、Role / Lease / Fence / Session、Fixture exports、Assertion / Artifact 引用，并重新推导 completeness、integrity 与 outcome。
- 新增内部 `DebugRuntimeService`，按 `CREATED → BINDING → READY → RUNNING → FINALIZING → TERMINATED` 推进 DebugRun，并在每一步原子写入 DebugRunEvent、Audit 与 Outbox。绑定与终结均支持同一精确命令幂等重放；不同 Contract 或 Evidence 命令被拒绝。
- CaseVersion 发布不再只相信 DebugRun 上的 Manifest ID / Digest，而是加载实际 EvidenceManifest，复核 ExecutionContract、Test IR、Plan、Fixture、Outcome、Completeness 与 Integrity 全部一致。旧 P6 前无证据 `PASSED` 和迁移时仍活动的旧 Run 会安全回退为 `INCONCLUSIVE`。
- P6-00 当时没有公共 Runtime 完成 API，也未提供 Browser Worker、Live Action/Event、Artifact 字节验证或 View Token；P6-01 已补齐受信内部 Worker 协议，公共完成 API 仍保持关闭。
- `AttemptSeal` 明确不属于 DebugRun；P5-00A 已建立正式 `UnitAttempt` 宿主，Seal 仍在后续 P6 切片按独立不可变协议落地。
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

- P6-01 验收时生产 `BrowserArtifactWriter` / Evidence Redaction、对象存储写入、独立 Hash 与 Integrity Verification 尚未实现；P6-02A 已补齐该主链，未完整配置可信 Evidence Store 时 `CAPTURE_VIEW` 仍拒绝执行。
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

1. 在 P6-02B2 基于 P5-00A 已建立的正式 UnitAttempt 落地 LiveSession、ControlLease、浏览器控制 Epoch / Fence、Human Takeover 与持久化 ActionGrant；P6-02B1 已提供的 DebugRun Live 只映射到前端现有 Debug / Live / Evidence 槽位，不重画或调整原型结构。
2. 串联公共 DebugRun Start → Runtime Preparation → ExecutionContract Bind → Browser Dispatch，并保持同一命令的幂等恢复语义。
3. 接入首个真实 SaaS Browser Operation / Published Route，并在部署层实施容器 Egress / DNS / UDP / WebRTC 策略与 Envelope Key Ring Rotation。
4. P5-00B2A 已建立 durable Start Intent 交付层；下一 P5 切片落地最多 64 Units 的真实 `AtlasTaskRunWorkflow` / Activity 和 Unit 调度，随后再处理超过 64 Units 的分区物化。后续在 P6 创建 AttemptSeal，并在 P7 形成 Result Snapshot / Gate；Multi-actor 仍等待正式调度与控制权协议。
5. 接入首个真实 SaaS Fixture Provider 与 `PasswordLoginFlow`、生产 Secret Provider 和 KMS-backed `SessionArtifactVault`；缺少受信部署配置时继续 fail-closed。
6. 为各 Tenant 配置生产 Temporal Schedule，周期调度 Fixture Cleanup Sweep、`AccountHealthWorkflow`、Connector Reconcile、Credential Expiry Monitor 和 Session Janitor Workflow。

## P6-02A 范围

### 已实现

- Browser Worker 的可信截图路径会先在 Playwright DOM 中遮罩 `input`、`textarea`、`select`、可编辑内容与显式 `data-atlas-sensitive` 元素，再捕获 PNG；Writer 使用固定 RGB、白底 alpha flatten、去元数据和固定压缩级别生成 canonical PNG，并限制原始字节与像素规模。
- `PngEvidenceArtifactWriter` 使用作用域化唯一对象键写入 S3-compatible Object Store；只有在独立回读完整对象并重新核对 SHA-256 与大小后，才返回 `integrity=VERIFIED` 的 `EvidenceArtifactInput`。上传、回读或校验失败时不生成可信 Receipt，并尽力删除本次对象；生产 Bucket 仍必须启用 Object Lock / Versioning。
- API 新增 `GET /v1/debug-runs/{runId}/evidence` 安全投影、`POST /v1/debug-runs/{runId}/evidence/{artifactId}/read-tokens` 与 `GET /v1/evidence/artifacts/{artifactId}/content`。Manifest 不暴露 ObjectRef；对象读取发生在短数据库事务之外，完整字节在响应前再次校验固定大小与 SHA-256。
- `20260715_0018` 新增强制 RLS 的 `evidence_read_grant`。Opaque `evr_` Token 只在签发响应出现，PostgreSQL 只保存 SHA-256 Hash；Grant 精确绑定 Tenant、Project、Environment、DebugRun、ExecutionContract、Artifact、Actor、Platform Session 与 `INLINE / DOWNLOAD` Purpose，并以 10–120 秒 TTL、1–32 次最大读取和单步 Revision / Read Count 状态机限制使用。
- 读取同时要求普通 Platform Session 与 `Authorization: Atlas-Evidence <token>`，不接受 Query Token。新签发会撤销同 Artifact / Actor / Session / Purpose 的旧活动 Grant；过期、撤销、Purpose 不匹配、Session / Actor 不匹配、读取次数耗尽或跨 Scope 使用均 fail-closed。
- API 与 Browser Worker 的 Evidence Store 配置必须完整提供非空 Endpoint、Access Key 和 Secret Key；Staging / Production 强制 TLS 并拒绝自动创建 Bucket。连接 / 读取超时、有限重试、并发上限与 MinIO Transport Error 均受控；`capture_view` 未连接可信 Store 时拒绝启动或执行，读取未配置、对象缺失、完整性失败与 Store 不可用分别返回受控 503 / 409，不返回未验证字节。
- 本切片没有修改任何前端页面、组件、DOM、布局、CSS 或既有交互；后续接线仍以前端既有 Debug / Live / Evidence 原型为准。

### 验证状态

- Alembic `20260715_0018 → 20260715_0017 → 20260715_0018` 往返通过；真实 PostgreSQL 覆盖 Actor + Tenant RLS、Session 有效 / 撤销、并发单次兑换、并发替换仅保留一个活动 Grant、列级 UPDATE、无 DELETE 与 ObjectRef Scope Constraint。
- 真实 Chromium 覆盖主页面与 iframe 的 DOM Mask、普通区域不误遮罩、canonical PNG、对象回读与 Receipt SHA-256；应用与 API 测试覆盖双认证、拒绝 Query Token、通用 401、无 Token Audit / Outbox、事务外对象 I/O、409 / 503 与 no-store 安全响应。
- `make verify` 通过：467 tests、覆盖率 90.05%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python sdist / wheel、TypeScript 与前端生产构建全部成功；前端只有自动生成的 API 类型变化，页面与原型未改。

## P6-02B1 范围

### 已实现

- 建立 `atlas.debug-live-cursor/0.1`、`atlas.debug-live-run-projection/0.1`、`atlas.debug-live-event/0.1` 与 `atlas.debug-live-snapshot/0.1` 冻结契约。当前宿主明确是 `DebugRun`，不是尚未建立的 `UnitAttempt` 或正式 `LiveSession`。
- API 新增 `GET /v1/debug-runs/{runId}/live` 安全快照与 `GET /v1/debug-runs/{runId}/events/stream` SSE。两者复用 Platform Session / Project 可见性边界；OpenAPI 明确声明 `PlatformSession`，响应使用 private `no-store`、`no-transform`、`nosniff`，SSE 同时禁用代理缓冲。
- 首次订阅以单条无行锁 SQL 在同一 MVCC Snapshot 中读取轻量 `DebugLiveRunProjection`、最新事件和精确 `headSeq`，先发送 `debug_run.live.snapshot`。查询只构造 Live 所需字段，不加载或反序列化完整 DebugRun 的 Test IR / PlanTemplate 等大快照；Opaque Cursor 是绑定 exact DebugRun 与 `afterSeq` 的 canonical Base64URL JSON。重连只接受 `Last-Event-ID`，按 `seq > afterSeq ORDER BY seq` 重放，跨 Run、损坏、超长或超前 Cursor 均在开始流式响应前返回 400。
- 每次事件拉取使用独立短事务，关闭事务后才向网络 yield；无事件等待、慢消费者背压和 Heartbeat 均不占 PostgreSQL 连接或行锁。Heartbeat 只发送 SSE comment，不带 `id`、不推进 Cursor。`DebugRun=TERMINATED` 不封存事件日志，之后仍可能因 Draft 语义变化追加 `debug_run.snapshot_outdated`；SSE 必须跨过 `debug_run.terminated` replay 到当前 head，并继续 Poll，直到客户端断开或最长连接时限到达。
- Live Event 不原样转发 `debug_run_event.payload`，而是按事件类型使用精确 allowlist 构造安全投影。取消 `reason`、Report / Chain Digest、ObjectRef、Authorization、Password、输入 Value 和未知字段均不进入 SSE；已落地的 Browser Report 只补充 Live UI 所需的低风险 Action、Observation、Policy、Receipt、Assertion 与 Artifact 摘要。
- `DebugLiveStreamLimiter` 对单 API 进程的 Observer 数量设置硬上限，容量耗尽立即返回带 `Retry-After` 的 429，不排队占用连接；默认 Poll、Heartbeat、最长连接时长、Batch Size 与最大 Observer 均由有界配置控制。Service 的 `maximum_connection_seconds` 是事件生成预算；Route 内 `_DebugLiveStreamingResponse` 使用该预算加固定 1.0 秒 Close Grace 约束生成与关闭路径，并在 `finally` 中关闭 Source、归还 Observer Slot。最后安装的 pure-ASGI `DebugLiveStreamSendDeadlineMiddleware` 使用相同的 maximum 与 1.0 秒 Close Grace，包住 `BaseHTTPMiddleware` 重包装后的真实 client-facing `send`；网络写到期仍阻塞时由这一层取消。两层职责不同，Grace 期间均不生成新的业务事件。
- Migration 分三阶段加固既有 `debug_run_event`：`20260716_0019` 只增加 32 KiB JSON Payload `CHECK ... NOT VALID` 并提交可修复边界，不扫描历史数据；`20260716_0020` 只先 `VALIDATE CONSTRAINT`，成功后再创建阻止 `UPDATE / DELETE` 的不可变 Trigger；`20260716_0021` 独立进入 Alembic `autocommit_block()`，以 `DROP INDEX CONCURRENTLY IF EXISTS` 删除已被 `(debug_run_id, seq)` Unique Constraint 覆盖的冗余 replay index，downgrade 则以 `CREATE INDEX CONCURRENTLY IF NOT EXISTS` 恢复。若历史超限 Payload 使 0020 失败，事务回滚且数据库版本保持 0019，可修复历史数据后重试 0020；只有验证与 Trigger 成功后才进入 0021 的非阻塞索引清理。B1 继续以现有权威事件日志为事实源，不新增重复事件表、通知事实源或长期数据库会话。
- 本切片没有修改前端页面、组件、DOM、布局、CSS 或既有交互；后续接线严格以前端已设计的 Debug / Live / Evidence 原型为准。

### 验证状态

- Domain、Application 与 API 测试覆盖 Cursor round-trip / 损坏 / 跨 Run / 超前、轻量 Snapshot 高水位、`Last-Event-ID` 有序 replay、跨 `debug_run.terminated` 继续读取后续 `snapshot_outdated`、Heartbeat 不推进 Cursor、事务外等待、Route 关闭路径与 pure-ASGI 实际 `send` 的 Hard Deadline / Close Grace、事件 allowlist、取消原因脱敏、容量 429、SSE `id / event / data` 帧与 404 权限边界。
- 真实 PostgreSQL 集成测试覆盖轻量单 SQL `headSeq`、跨终止事件的无缺口 replay、终止后 `snapshot_outdated`、跨 Project / Tenant 404、32 KiB Payload 拒绝与事件 UPDATE / DELETE Trigger；Validation 失败后版本保持 0019、修复历史 Payload、重试成功，以及完整 `0018 ↔ 0021` Alembic 往返和 0021 concurrent drop / recreate 均已通过。
- 最终 `make verify` 已完整通过：Ruff all、严格 mypy 233 files、pytest 521 passed / coverage 90.18%、真实 PostgreSQL retry + `0018 ↔ 0021` roundtrip、Contracts Checks、Python sdist / wheel、Frontend `check:api`、`tsc` 与 Vinext Production Build 全部成功；前端原型页面未修改。

### P6-02B2 / P5 后续仍待落地

- P5-00A 已建立 `TaskRun / ExecutionUnit / UnitAttempt` 正式宿主，P5-00B2A 已补充 durable Start Intent Consumer，但真实 Task Workflow / Activity 仍未接入。B2A 不创建 `LiveSession`、`AttemptSeal` 或其他现场事实；P6-02B2 将直接绑定正式 UnitAttempt，不能退回 DebugRun 多态宿主。
- Browser `ControlLease`、控制权 Epoch / Fence、Pause / Resume / Takeover Command、Human Takeover、Safe Point / Quiesce、持久化且绑定 Epoch / Fence 的 `ActionGrant` 均未实现。当前 SSE 是只读 Observer 通道，不接受 Frame、Command、Action 或人工输入，也不把 P6-01 Worker 内部单次 Action 校验误称为持久化人工控制协议。
- 首个真实 SaaS Operation / Published Route、容器级 Egress / DNS / UDP / WebRTC、Envelope Key Ring、公共 Start 自动 Preparation / Bind / Dispatch、Multi-actor 和正式 AttemptSeal 仍按既有计划后续落地，缺少对应部署能力时继续 fail-closed。

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
| 2026-07-15 | P6-02A Evidence 安全链 | DOM / iframe Mask、canonical PNG、hash-only scoped Read Grant、完整字节二次校验 | 通过；真实 Chromium 与 PostgreSQL 覆盖 Session / Actor RLS、并发签发 / 兑换、篡改、超时、双认证与 no-store |
| 2026-07-15 | P6-02A Migration | `20260715_0018` downgrade `0017` → upgrade `0018` | 通过；ObjectRef Scope、强制 Actor + Tenant RLS、不可变 Trigger、列级 UPDATE、无 DELETE 与完整 downgrade 往返成功 |
| 2026-07-15 | P6-02A 全量门禁 | `make verify` | 通过；467 tests、覆盖率 90.05%、ruff、严格 mypy、Schema / OpenAPI 漂移、Python sdist / wheel、TypeScript 与前端生产构建全部成功 |
| 2026-07-16 | P6-02B1 Live 安全观察流 | 轻量 DebugRun Snapshot、Opaque Cursor、`Last-Event-ID` replay、短事务 SSE、Heartbeat comment、Route lifecycle deadline + pure-ASGI client-facing `send` deadline、Payload allowlist、Observer 容量 | 通过；覆盖跨 `terminated` 的后续事件、敏感字段不透出、404 权限同形、429、停滞真实 `send` 的取消与 OpenAPI Platform Session 契约 |
| 2026-07-16 | P6-02B1 PostgreSQL / Migration | `20260716_0019` `CHECK ... NOT VALID`；`20260716_0020` Validate + Immutable Trigger；`20260716_0021` autocommit concurrent replay index cleanup | 通过；Validation 失败 → 保持 0019 → 修复历史 Payload → 重试成功、真实 `0018 ↔ 0021` 往返及 concurrent drop / recreate 均已验证 |
| 2026-07-16 | P6-02B1 全量门禁 | `make verify` | 通过；Ruff all、严格 mypy 233 files、pytest 521 passed / coverage 90.18%、真实 PostgreSQL retry + `0018 ↔ 0021` roundtrip、Contracts Checks、Python sdist / wheel、Frontend `check:api`、`tsc` 与 Vinext Production Build 全部成功 |
| 2026-07-16 | P5-00A 正式执行宿主 | TaskPlanVersion / Run Manifest / TaskRun / ExecutionUnit / UnitAttempt / TaskExecutionEvent、Repository exact replay | 通过；领域与仓储定向测试、机器 Schema 漂移检查及真实 PostgreSQL 完整反向链验收 |
| 2026-07-16 | P5-00A PostgreSQL / Migration | `20260716_0022`，`0021 → 0022 → 0021 → 0022` | 通过；复合 Scope、Manifest 绑定、Attempt / Event gapless、不可变 Trigger、`FORCE RLS`、最小权限和完整 downgrade 均已验证 |
| 2026-07-16 | P5-00B1 调度前置 | 四类 Profile、stable request digest、Workflow identity registry、materialization Seal、Revision CAS | 通过；完整 `make verify` 651 tests / coverage 90.35%，真实 PostgreSQL、Chromium 与 `0023 ↔ 0022` populated roundtrip 均已验收 |
| 2026-07-16 | P5-00B2A Intent 可靠交付 | `20260716_0024` 状态机、独立 `atlas_dispatcher`、三段式 Consumer、稳定 Temporal Start / collision verification | 通过；90 项定向及真实 PostgreSQL / Temporal 测试、`0024 → 0023 → 0024` 往返、Consumer 镜像构建与完整 `make verify` 均成功；全量 712 tests / coverage 90.46% |

## 当前风险与外部输入

- 首个真实 SaaS Connector、`PasswordLoginFlow` 和测试账号来源尚未提供；当前交付 Mock Provider、Generic Password Adapter 与可注入的 Playwright Target。
- 独立 Auth Session Worker 与加密 Vault 端口已经落地；生产 Secret Provider、KMS-backed Vault 和生产 Object Store 配置尚未提供，缺失时 Password Session fail-closed。
- 周期性 AccountHealthWorkflow / Identity Reconciler / Tenant Session Janitor 尚未调度；当前已覆盖手工触发、Temporal Workflow 契约、运行时失败触发与单批 Janitor。
- Feishu PlatformPrincipal OAuth 尚未提供 Client Secret、Redirect URI 与权限范围；当前入口不会模拟成功。
- 生产对象存储和 Secret Manager 尚未指定；代码只依赖抽象接口，本地采用 S3-compatible 与不可逆的 Secret 引用。
- 试点项目、黄金用例和真实业务 API 契约尚未提供；P0-P1 不依赖这些输入，P2 之后需要逐步补齐。
- P3-03 已完成取消后补偿、Reconcile、Cleanup Retry / Sweeper、孤儿扫描与 Cleanup Evidence；生产环境仍需按 Tenant 配置 Temporal Schedule 和真实 Provider，缺失时继续 fail-closed。
- P5-00B1 已建立正式 Profile、Seal、CAS 与 Pending Start Intent，P5-00B2A 已实现独立、默认关闭的 durable Intent Consumer 与 Claim / Lease / Retry / Started / Failed 恢复状态机；Task Temporal Root Workflow / Activity、Command API、Schedule / CI Adapter 和超过 64 Units 的可恢复分区物化尚未实现。`STARTED` 只表示 Temporal 接受，不能描述成 Task 已执行或成功。
- P6-01 已实现独立无数据库 Browser Worker、Permit + HMAC 内部网关、Temporal Activity、加密 Context Restore、严格报告链与受限 Playwright Adapter；P6-02A Evidence Writer / 受控读取与 P6-02B1 DebugRun Live Snapshot / SSE 已完成。P6-02B2 的 UnitAttempt-scoped LiveSession、ControlLease、控制 Epoch / Fence、Human Takeover 与持久化 ActionGrant，以及真实 SaaS Operation / Route Registry、生产 Bucket Object Lock / Versioning、容器网络沙箱、Envelope Key Ring、公共 Start 自动 Preparation / Bind / Dispatch 和 Multi-actor 尚未实现，缺少对应能力时继续 fail-closed。
- 应用内 Browser 插件当前初始化报 `Cannot redefine property: process`；前端类型与生产构建已验证，服务保持可访问，自动化渲染回归需在插件恢复后补做。
