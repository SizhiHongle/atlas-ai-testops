# Atlas AI 测试平台实施进度

更新时间：2026-07-19

## 使用规则

本文件记录已经由代码和验证结果证明的进度，不把计划中的能力写成已完成。每个实施切片结束时必须同步更新：

1. 当前阶段与下一步。
2. 已完成的数据库、领域、API、前端和测试证据。
3. 新增或改变的架构决策。
4. 尚未解决的风险和外部依赖。

## 当前状态

- 当前阶段：`P10 前端生产化`
- 当前切片：`P10-01 独立生产前端、真实 API 闭环与发布门禁`
- 总体状态：P5 已建立正式执行宿主、统一 Manual / Schedule / CI / Webhook Trigger、100,000-Unit 可恢复分区物化、有界 Temporal History、signed HTTPS production Port、数据库权威 Temporal Schedule Catalog / Sync / Fire，以及 signed Task Gate Callback 可靠投递；P6 已建立可信 Browser / Evidence / Attempt Result 事实链、DebugRun 只读 Live 和 UnitAttempt-scoped Live Control；P7 已完成三阶段 Result Snapshot、Failure Classification、fail-closed Task Gate 与公开查询 API。P8 V1 已实现 comparable Insight Snapshot。P9 本地参考验收全部通过。P10 已保留原型为设计权威，并在独立生产包中完成 Auth、Space、Identity、Fixture、Case、Task、Live、Result、Insight 的真实 API 接线、权限/错误边界、安全 BFF、Unit/Component/E2E/Visual Regression、生产构建与发布门禁
- 当前分支：`main`
- P10 进入基线提交：`3b8bdc4`

## 阶段看板

| 阶段 | 范围 | 状态 | 完成证据 |
| --- | --- | --- | --- |
| P0 | 工程基础、契约、数据库、进程入口、前端 API 基础 | 已完成 | 46 tests、真实 PostgreSQL/Temporal、三容器构建、前端浏览器 QA |
| P1 | Tenant、Project、Environment、平台身份与权限 | 已完成 | 88 tests、真实 PostgreSQL RLS/RBAC/Session、登录原型浏览器 QA |
| P2 | TestRole、AccountPool、TestAccount、Lease 与 Auth Session | 已完成 | P2-01 至 P2-06 已验收；身份、租约、Secret Grant、加密 Session 与清理链已闭环 |
| P3 | Atom、Blueprint、Fixture Run 与 Cleanup | 已完成 | P3-00 至 P3-03 已验收；资产、耐久运行、取消补偿、Reconcile、Cleanup Retry / Sweeper 与三类发布证据闭环 |
| P4 | TestCase、WorkflowDraft、DebugRun 与 CaseVersion | 后端完成 | P4-00 至 P4-03 已验收；作者态、不可变 DebugRun、精确绑定、Reviewer 发布门禁与 CaseVersion 冻结闭环已落地 |
| P5 | TaskPlan、TaskRun、ExecutionUnit 与 Temporal 编排 | 基础中 | P5-00A 至 P5-00E7 已验收；不可变 Ticket、signed HTTPS Port、durable command、infra retry/rerun、TaskPlan、统一 Trigger、100,000-Unit 分区、数据库权威 Temporal Schedule、signed Gate Callback 与 P6-02B2 Takeover 均有 PostgreSQL / Temporal 证据。部署端真实 SaaS executor 待外部输入 |
| P6 | Browser Worker、Live、Evidence 与 AttemptSeal | 基础中 | P6-00 可信事实层、P6-01 Browser 执行平面、P6-02A 可信截图写入 / 受控读取、P6-02B1 DebugRun Live 安全观察流、P6-02B2 UnitAttempt 控制权、P6-03A AttemptSeal / ResultRef 与 P6-03B ClosureNotice / UnitResolutionRevision 均已验收；真实 SaaS Operation、网络沙箱与 Multi-actor 仍需部署输入或后续实现 |
| P7 | Result Fact、Snapshot、Classification 与 Gate | 已完成 | P6-03A/P6-03B 与 P7-01A 至 P7-03 已实现三阶段 Snapshot、FailureCluster / Classification、`0039` TaskGateDecision、公开 Result API、ETag 与既有 Results 槽位真实数据映射 |
| P8 | Insight Projector、Metric、Snapshot 与 Export | 基础中 | V1 fixed MetricDefinition、qualityFinalizedAt 归窗、ratio-of-sums、DatasetCut、`0040` immutable InsightSnapshot、preview/pin/exact API 与既有 Insights 槽位映射已实现；Projector generation、Signal/Review 与异步 Export 待扩展 |
| P9 | 隔离、并发、故障注入、黄金链路与 SLO 验收 | 基础中 | 本地参考：12 项故障、10,000 Lease 冲突 0、100 Evidence、30 / 30 黄金链、Cleanup 100%、Schedule P95 4,787 ms、Live P95 4 ms 均通过。生产月度 SLO、人工分类、真实影子迭代与灾备演练 `NOT_EVALUATED` |
| P10 | 原型权威下的生产前端工程与真实 API 闭环 | 已完成 | 独立 `atlas-testops-web`、Feature First、同源 BFF、Session/RBAC、九个真实业务域、WorkflowPatch/Layout、14 项 Unit/Component Test、8 项桌面/移动 E2E 与 Visual Baseline、生产构建和安全响应验收全部通过 |

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
- P5-00B1 已补齐原计划中的四类正式 Profile、稳定 request digest、Temporal Workflow identity、同步 materialization seal 与统一 Revision CAS；P5-00B2A 已实现 Intent Consumer 的可靠交付层，P5-00B2B 已实现最多 64 Units 的真实 Root / Attempt Workflow。超过 64 Units 的可恢复分区物化、正式 `TaskUnitExecutionPort`、Schedule / CI / API 入口仍属于后续切片。随后 P6-02B2 的人工控制事实才能精确绑定 `UnitAttempt`。

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

- `task_workflow_start_intent` 在 B1 中只允许不可变 `PENDING` 事实；P5-00B2A 已在后续 migration 中增加 Claim / Lease / Retry / Started / Failed 状态机和恢复领取，P5-00B2B 已继续接入真实 Temporal Root / Attempt Workflow 与短 Activity 边界。
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

- B2A 当时只证明可靠交付，不包含真实 Task 执行；P5-00B2B 已在后续切片补齐 `AtlasTaskRunWorkflow`、Unit 调度 Activity、`AtlasUnitAttemptWorkflow` 与对应 Worker。生产启用仍必须显式配置正式 `TaskUnitExecutionPort`，不能把 Temporal 接受或无 Adapter 的 Queue 冒充可执行能力。
- P5-00C 已在后续切片补齐 TaskRun / Manifest / Unit / Attempt / Event 只读 API；Task Plan authoring、Task Command、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化、AttemptSeal、Result Snapshot、LiveSession 与 ControlLease 仍未实现。
- P5-00B2B 已完成最多 64 Units 的有界编排，P5-00C 已补充只读查询；下一 P5 执行切片应先提供正式 `TaskUnitExecutionPort`，控制命令则必须另建 durable command intent + Workflow signal 交付闭环，再单独处理超过 64 Units 的分区物化、checkpoint / resume 与 Continue-As-New。

## P5-00B2B 范围

### 已实现

- `AtlasTaskRunWorkflow` 直接兼容 P5-00B2A 冻结的 Root Intent Input / Workflow Type，固定消费 `atlas-task-run`；它以短数据库 Activity 加载一个已 Seal、完整且不超过 64 Units 的首 Attempt 计划，并按固定 8-child batch 启动 `AtlasUnitAttemptWorkflow`。Child 只消费固定 `atlas-unit-attempt` Queue，Workflow ID 由 Tenant ID + UnitAttempt ID 确定性生成并使用 `REJECT_DUPLICATE`，不能由调用方改写或重复制造物理 Attempt。
- `AtlasUnitAttemptWorkflow` 按 Begin DB Activity → Execute side-effect Activity → Finish DB Activity 推进。数据库 Activity 每次保持 30 秒短边界；瞬时基础设施错误以 1 秒到 60 秒退避耐久重试，确定性 Task 不变量立即以安全 non-retryable error 失败。真正执行副作用的 Activity 固定 `maximumAttempts=1`，避免未知提交结果被 Temporal 自动重做。每个首 Attempt 的冻结 `executionDeadline` 进入 Child Input，执行前由 PostgreSQL 时钟与 Workflow 时钟双重检查；`scheduleToClose` 覆盖 Queue 排队，`startToClose` 限制实际运行，两者都不越过 deadline。
- Root 对 Child 异常、取消和不可信返回使用类型化安全 fallback，仍把已观察结果交给最终短数据库 Activity 收敛；Activity 在返回前解码、校验并归一化 adapter / 数据库结果，未知异常只写稳定安全码，敏感正文不进入 Temporal History。Attempt finalize 事件冻结 exact status / error code，Run finalize 事件冻结 exact status / counts，CLOSED replay 逐字段核对。固定 Root / Child identity、durable Temporal History、exact-event replay 和 Start Intent collision verification 共同保证 Worker / Consumer 重启后不重复执行已完成 side effect。副作用 Activity 持续 Heartbeat、等待取消完成；原生取消中的运行副作用收敛为 `INCONCLUSIVE / TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN`，Root 停止后续 batch 但保留已完成 Child 的真实结果；已 CLOSED 的非取消 Attempt 不再返回 READY。
- `TaskWorkerService` 将加载计划、Attempt Begin / Finish 与 Run Finish 分隔为独立 tenant-scoped 短事务；事务中只执行 PostgreSQL 复核、状态 CAS 与追加事件，不持锁等待 Temporal 或 execution port。`20260716_0025` 新增 tenant-scoped `SECURITY DEFINER atlas.lock_task_execution_chain(...)`，在数据库内按 Run → Unit → Attempt 固定顺序锁定同一 sealed 执行链；`atlas_app` 只有该受信函数和既有状态函数的 EXECUTE，仍无三张状态表的表级 UPDATE。
- Root Worker 与 Attempt Worker 分别注册到固定双 Queue，并具有独立有界并发。`ATLAS_TASK_WORKER_ENABLED=false` 默认关闭；Intent Consumer 也保持独立开关和默认关闭。仓库没有内置或占位 production `TaskUnitExecutionPort`，启用 Worker 却未注入经评审的 Adapter 时会在建立数据库或 Temporal 连接前 fail-closed。
- 当前没有 `AttemptSeal`，因此 Workflow Payload、execution port 协议和数据库收敛均不允许表达 `PASSED`。副作用报告成功只能得到 `EXECUTED_UNSEALED → FINISHED_UNSEALED`，并以 `INCONCLUSIVE` Quality 持久化；任何失败、歧义、跳过或不可信结果收敛为 `FAILED / INCONCLUSIVE / CANCELED`，绝不把“执行完成”冒充可信通过。

### 已验证

- 定向 Application / Worker / Workflow / Migration / PostgreSQL / Temporal 测试已通过；真实 PostgreSQL 验证 tenant scope、Run → Unit → Attempt 锁序、Revision CAS、状态 / exact-event replay、最小权限与 `20260716_0025` 升降级，真实 Temporal 验证两个 Worker、固定 Type / Queue、1 个与 9 个 Child 的跨 batch 调度、确定性 Child ID、同 Root replay 不重复执行、排队跨 deadline 不调用 execution port、数据库 Activity 连续三次瞬时失败后第 4 次恢复、Adapter 异常与非法返回不泄漏进 History、非 PASS 结果、原生 Child 取消的未知结果与 Root 取消后已完成 Child 结果保留。
- 本切片没有修改任何前端页面、组件、DOM、布局、CSS 或既有交互；Launch、Task Control 与 Live Theatre 继续以前端已设计原型为唯一权威。
- 完整 `make verify` 已通过：780 tests、coverage 90.15%、Ruff、严格 mypy 269 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功。

### 后续边界

- P5-00C 已补充 TaskRun / Manifest / Unit / Attempt / Event 只读 API；Task Plan authoring、Task Command、正式 production `TaskUnitExecutionPort`、Schedule / CI / Webhook Adapter 与超过 64 Units 的可恢复分区物化仍未完成。
- `AttemptSeal`、Result Snapshot / Gate，以及 UnitAttempt-scoped `LiveSession`、`ControlLease`、Epoch / Fence、Human Takeover 与持久化 `ActionGrant` 仍分别属于后续 P6 / P7 / P6-02B2；在这些事实就绪前，Task Runtime 不能产生 `PASSED` 或提供 Live control。

## P5-00C 范围

### 已实现

- 新增 `GET /v1/projects/{projectId}/task-runs`、`GET /v1/task-runs/{runId}`、`GET /v1/task-runs/{runId}/manifest`、`GET /v1/task-runs/{runId}/units`、`GET /v1/task-runs/{runId}/units/{unitId}/attempts` 与 `GET /v1/task-runs/{runId}/events`。TaskRun 使用 Revision ETag；列表使用有界 keyset / ordinal / attemptNumber / seq pagination，所有 Repository 查询只多取一条判断下一页。
- Application Service 先执行 Project 可见性校验，再在 tenant-scoped 短事务中读取；单资源不可见、跨 Tenant 或 Unit 不属于 Run 均返回同形 404。Run Manifest 缺失被视为持久化不变量破坏并 fail-closed，不能伪装成空结果。
- `20260716_0026` 新增 `(tenant_id, project_id, requested_at desc, id desc)` 索引，匹配 Project TaskRun 列表的稳定排序。真实 PostgreSQL 已验证查询、父级归属和跨 Tenant RLS 隔离。
- 本切片不暴露 pause / resume / retry / takeover。当前 Root Workflow 没有完整 pause signal 语义，API 到 Temporal 的可靠 command delivery 也尚未建立；只修改数据库 lifecycle 会产生“接口成功但执行未停”的假状态，因此保持 fail-closed。
- OpenAPI 与前端 API TypeScript 类型已同步；未修改任何前端页面、组件、DOM、布局、CSS 或既有交互，继续以前端原型为唯一权威。

### 已验证

- Application / API / Repository / Migration 定向测试和真实 PostgreSQL 查询测试已通过，覆盖分页边界、Revision ETag、父级归属、不可见资源同形 404 与跨 Tenant RLS；`0026 → 0025 → 0026` 往返及最终索引存在性已验证。
- 完整 `make verify` 已通过：789 tests、coverage 90.25%、Ruff、严格 mypy 275 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功。

### 后续边界

- 正式 `TaskUnitExecutionPort` 需要部署侧真实 SaaS adapter 与凭据边界；仓库不会内置 no-op production executor。
- P5-00D2A 已补齐 durable Cancel；Pause / Resume / Retry / Takeover 仍需要各自的安全点、幂等与最终状态协议，完成前不开放对应 HTTP endpoint。
- Task Plan authoring、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化、AttemptSeal / Result 与 UnitAttempt-scoped Live control 继续分切片落地。

## P5-00D1 范围

### 已实现

- 新增 `atlas.task-unit-execution-ticket/0.1` 机器契约。Ticket 对每个物理 `UnitAttempt` 唯一，冻结 Run request / Manifest、Unit / Case、四类 Profile、Fixture、Environment revision / allowed origins、deadline 与全部内容摘要；不保存账号、Credential、Lease、Session、Token 或 Secret。
- `20260717_0027` 新增 `task_unit_execution_ticket`：完整 Scope FK、`unit_attempt_id` 唯一、不可 UPDATE / DELETE Trigger、`FORCE RLS`、`atlas_app` 仅 SELECT / INSERT、`atlas_dispatcher` 无权限。owner-owned `SECURITY DEFINER` Insert Guard 使用固定 search path，重读并锁定 exact Run / Unit / Attempt / Case / Profile / Fixture / Environment，重新核对当前发布态、状态、Origin 边界和 canonical ticket digest。
- `AtlasUnitAttemptWorkflow` 在 Begin 与任何副作用前增加可耐久重试的 Prepare DB Activity；Port 的输入从裸 `UnitAttemptWorkflowInput` 改为 `TaskUnitExecutionRequest(attempt, ticketId, ticketDigest)`。Prepare 精确重放已存在 Ticket，不因后续 Profile 状态变化改写历史 Ticket；不同 scope / digest 或持久化篡改均 fail-closed。Execute Activity 仍固定 `maximumAttempts=1`。
- 仓库仍不注册 no-op、placeholder 或假 SaaS Adapter；没有部署侧正式 `TaskUnitExecutionPort` 时 Task Worker 保持默认关闭并在外部连接前拒绝启动。本切片只建立执行授权边界，不伪装真实 SaaS 登录或业务操作已经完成。
- 未修改任何前端页面、组件、DOM、布局、CSS 或交互；新增契约不要求 OpenAPI 或前端生成类型变化，前端原型继续是唯一权威。

### 已验证

- Domain、Repository、Application、Workflow、Worker 与 Migration 测试覆盖 Ticket digest / 时间 / Origin、exact replay、篡改拒绝、Port 只接收 prepared request、Prepare 的数据库耐久 retry 与 Worker 装配。
- 真实 PostgreSQL 已验证 `0027 → 0026 → 0027` 往返、atlas_app 创建与精确重放、Trigger 拒绝依赖篡改、跨 Tenant RLS 不可见和无 UPDATE 权限；真实 Temporal 已验证 1 / 9 Child、Ticket-bound Port、History 安全字段、同 Root replay 不重复副作用与完整 PostgreSQL 执行链。
- 完整 `make verify` 已通过：800 tests、coverage 90.25%、Ruff、严格 mypy 279 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功。

### 后续边界

- P5-00D1 只完成正式 Port 的输入授权协议，不提供目标 SaaS、Login Flow、Operation Registry、生产 Secret Provider 或 KMS-backed Vault；这些部署能力明确后才能实现首个 production Adapter。
- AttemptSeal、可信 Oracle / Evidence 终结、UnitAttempt-scoped Live control、Pause / Resume / Retry / Takeover 与超过 64 Units 分区物化仍按独立切片落地；当前成功执行继续只能收敛为 `FINISHED_UNSEALED / INCONCLUSIVE`，不能表达 `PASSED`。

## P5-00D2A 范围

### 已实现

- 新增 `atlas.task-run-command/0.1`、`RequestTaskRunCancel` 与 canonical command digest。首期只允许 `CANCEL`，公开状态固定为 `PENDING / DELIVERED / APPLIED / FAILED`，不暴露 Claim Token、Dispatcher identity、Lease 或内部 Retry 状态。
- `20260717_0028` 新增强制 RLS 的 `task_run_command_intent`、immutable identity、exact Run scope FK、mutation / digest unique、Claim / Retry 索引和严格状态形状。`atlas_app` 只有 SELECT / INSERT 与 apply function；独立 `atlas_dispatcher` 无表级 DML，只有 Claim / Delivered / Retry / Fail fenced function EXECUTE。
- `POST /v1/task-runs/{runId}:cancel` 要求 `If-Match`、`Idempotency-Key == clientMutationId` 与 `RUN_OPERATOR+`。一个短事务内锁定 exact sealed Run revision、推进 `CANCELING`、写 PENDING command 并追加 Task Event / Audit / Outbox；同 mutation exact replay 返回同一命令，API 事务不调用 Temporal。`GET /v1/task-runs/{runId}/commands/{commandId}` 返回安全状态投影。
- Intent Consumer 进程同时运行 Start Intent 与 Command Intent 两个有界 poller；两者复用独立 dispatcher DSN 和 retry policy，但分别 Claim / CAS。Command 投递在事务外验证 deterministic Workflow ID、namespace、Type、Queue 与 Start Memo，再发送 versioned secret-free Signal；`NOT_FOUND` 视为 Workflow 可能尚未 Start 并耐久重试。
- Root 从 plan 读取已持久化的 `cancelRequested`，同时接收 exact command Signal 并按 ID + payload 去重。Cancel 停止新 batch、取消 active Child；已完成结果原样保留，尚未证明完成的副作用保持 `INCONCLUSIVE`。Run `CLOSED / CANCELED` 后，finish transaction 同时把 exact command 标为 `APPLIED`；若 Root 在 Signal 到达前已按 plan 关闭，Dispatcher terminal reconciliation 也只在确认该终态后转为 `APPLIED`。
- OpenAPI、`task-run-command.schema.json` 与前端 TypeScript API 类型已同步；未修改前端页面、组件、DOM、布局、CSS 或既有原型交互。

### 已验证

- Domain、Application、Repository、API、Migration、Consumer、Signaler、Workflow 与 Worker 定向测试覆盖 digest / status shape、Revision / idempotency / RBAC、短事务 Claim、Token + Revision fence、safe error code、Signal identity / Memo、`NOT_FOUND` retry、duplicate Signal、active Child cancel、completed outcome preservation 和 APPLIED 原子边界。
- Alembic 已完成真实 `0028 → 0027 → 0028` 往返。真实 PostgreSQL 已验证 API 接受、Run `CANCELING → CLOSED / CANCELED`、跨 Tenant RLS、atlas_app 无 UPDATE、dispatcher function-only 权限，以及 Workflow 先关闭时由 terminal reconciliation 转为 `APPLIED`。
- 真实 Temporal 已验证 versioned command Signal 可反序列化、exact duplicate 只保留一个 command、active Child 被取消、已完成 Child 结果保持、未知副作用不伪装成已知完成，History 不携带 Secret。
- 完整 `make verify` 已通过：872 tests、coverage 90.48%、Ruff、严格 mypy 289 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功；前端原型未改。

### 后续边界

- P5-00D2B 已在后续切片把 Pause / Resume 定义为 Task 级批次边界派发控制；它不等同于浏览器 Safe Point / Quiesce。Retry 必须追加新 UnitAttempt，Takeover 必须绑定后续 ControlLease / Epoch / Fence，不能复用 Cancel 或 Task Pause 语义。
- Task Plan authoring、正式 production `TaskUnitExecutionPort`、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化、AttemptSeal / Result 与 UnitAttempt-scoped Live control 继续按独立切片落地。

## P5-00D2B 范围

### 已实现

- `atlas.task-run-command/0.2` 在兼容历史 `0.1` Cancel 的前提下新增 durable `PAUSE / RESUME` 与公共终态 `SUPERSEDED`。`POST /v1/task-runs/{runId}:pause|resume` 复用 exact Revision、`Idempotency-Key == clientMutationId`、`RUN_OPERATOR+`、Event / Audit / Outbox 与可靠 Signal 投递边界；Pause 只接受 `RUNNING`，Resume 只接受 `PAUSED`。
- `20260717_0029` 扩展 command intent 的严格状态形状、单 Run 唯一未完成 Pause / Resume、command-specific Insert Guard、Pause / Resume apply function 与 Cancel supersession function。Resume 接受事务保持 Lifecycle 为 `PAUSED` 但推进 Revision；Cancel 从 `PAUSE_REQUESTED / PAUSED` 进入 `CANCELING` 时，同事务把未完成 Pause / Resume 标记 `SUPERSEDED` 并记录 superseding Cancel ID。
- Root 在每个最多 8 个 Child 的批次前调用 `prepare_batch`，以一个短事务为整批创建或精确重放 immutable Execution Ticket。事务提交后的 Ticket 集合是不可追加的预授权边界，因此 Pause 到达后当前批次可以完成，但未授权的下一批不能启动。
- 每个批次结束后 Root 调用 `checkpoint_control`。Pause checkpoint 同事务完成 `PAUSE_REQUESTED → PAUSED`、追加 `task_run.paused` Event 与 command `APPLIED`，随后使用 Temporal `workflow.wait_condition` 耐久等待；Resume Signal 唤醒后，Resume checkpoint 同事务完成 `PAUSED → RUNNING`、追加 `task_run.resumed` Event 与 command `APPLIED`，之后才准备下一批。
- `start_attempt` 在 `PAUSE_REQUESTED` 下只接受已存在且身份完全匹配的 Ticket，从而允许已授权批次正常收敛；它不会让 Pause 后的新 Attempt 绕过批次门禁。最终批次与控制命令竞态由 `finish_run` 在关闭前收口，不留下未完成 Pause / Resume command。
- OpenAPI、`task-run-command.schema.json` 与前端 TypeScript API 类型已同步；未修改前端页面、组件、DOM、布局、CSS 或既有原型交互。这里的 Pause 只对应前端既有“暂停派发 / 继续派发”，不是浏览器 Action Pause 或 Human Takeover。

### 已验证

- Domain、Application、Repository、API、Migration、Signaler、Workflow 与 Worker 定向测试覆盖 v0.1/v0.2 兼容、Pause / Resume digest、Revision / idempotency、单一 open command、batch gate、Ticket 预授权、durable wait / wake、Cancel supersession 与最终批次竞态。
- Alembic 已完成真实 `0028 → 0029 → 0028 → 0029` 往返。真实 PostgreSQL 已验证 `RUNNING → PAUSE_REQUESTED → PAUSED → RUNNING`、Pause / Resume `APPLIED`、预授权 Ticket，以及第二次 Pause 被 Cancel 原子置为 `SUPERSEDED`。
- 真实 Temporal 已验证 10 Units 场景中首批 8 个 Child 完成后停止派发，在 Resume Signal 与 checkpoint 之前第 9 / 10 个不会启动，Resume 后继续完成；Pause / Resume command 不会混入 Cancel finish command。
- 完整 `make verify` 已通过：888 tests、coverage 90.24%、Ruff、严格 mypy 290 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功；前端原型未改。

### 后续边界

- Task 级 Pause 不冻结已运行的 Browser Activity，也不授予人工控制权。UnitAttempt-scoped Browser Safe Point、ControlLease、Epoch / Fence、Human Takeover 与持久化 ActionGrant 仍属于 P6-02B2。
- Retry 必须追加新的 UnitAttempt；Takeover 不能复用 Task Resume。Task Plan authoring、生产 `TaskUnitExecutionPort`、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化与 AttemptSeal / Result 仍待后续切片。

## P5-00D3A 范围

### 已实现

- 新增 `atlas.task-run-manifest/0.2` 与 `atlas.task-retry-policy/0.1`。Manifest 冻结 `infraRetryAttempts`、`maxTotalInfraRetries`、初始 / 最大退避和 jitter，并要求 `policyDigests["infra-retry"]` 与 policy canonical digest 完全一致；历史 `0.1` Manifest 继续可读且自动重试次数为零。
- `20260717_0030` 为 `task_run_manifest` 增加严格 `retry_policy` JSONB 形状、整数 / 上下界、精确键集合和数据库 canonical digest 校验；downgrade 在存在 v0.2 policy 事实时 fail-closed。Migration 同时收紧 execution ticket Insert Guard：首 Attempt 仍要求 `QUEUED` Unit，重试 Attempt 只接受仍为 `RUNNING` 的 Unit、gapless attempt number、前序 `CLOSED / INFRA_ERROR` 与已到达的 `queuedAt / notBefore`。
- `TaskAttemptExecutionPayload` 与持久化 Attempt finalize 事件新增受限 `INFRA_ERROR` 和可选 `retryAfterSeconds`。Attempt Finish 现在只关闭一次物理 Attempt，不提前关闭逻辑 Unit；Assertion / 产品失败、非法返回、取消歧义与 `OUTCOME_UNKNOWN` 不会进入自动重试分支。
- Root 新增最多 8 个结果的 `settle_attempt_batch` 短事务：锁定 exact Run，按 Unit ordinal 原子分配 per-Unit / Run 总预算，复核 frozen policy、显式分类和原始 deadline；满足条件时使用确定性 UUID、gapless `attemptNumber`、同一 Temporal namespace / Workflow ID 与原始 deadline 追加新 Attempt，否则关闭 Unit。Activity replay 只返回同一已存在事实，不重复追加 Attempt 或事件。
- Root Workflow 从固定首 Attempt 列表演进为 wave 循环，维护每个 Unit 的 latest outcome。数据库返回 retry dispatch 后，Workflow 以 `notBefore` 使用 durable timer 等待；Pause / Resume / Cancel Signal 可中断等待并重新进入数据库控制 checkpoint。Pause / Cancel 结算竞态不会追加新的 Ticket 或重试决策；Cancel 在 retry backoff 中会安全关闭尚未派发的新 Attempt。
- Retry Attempt 的 Prepare / Begin 同时经过 Application Admission 与数据库 Ticket Guard，不能仅凭 Workflow payload 绕过父链校验。最终 `finish_run` 按每个 Unit 的完整 gapless Attempt 历史和 latest Attempt 收敛，不覆盖旧 Attempt。当前仍无 AttemptSeal，因此 retry 成功也只能得到 `FINISHED_UNSEALED / INCONCLUSIVE`。
- OpenAPI、TaskRun Manifest JSON Schema 与前端生成 TypeScript API 类型已同步；未修改任何前端页面、组件、DOM、布局、CSS 或既有原型交互，前端原型继续是唯一权威。

### 已验证

- Domain、Repository、Application、Workflow、Worker 与 Migration 定向测试覆盖 policy digest / 上界、legacy 零重试、明确 infra 分类、非 infra 禁止重试、预算耗尽、deterministic Attempt identity、exact replay、Pause / Cancel defer、durable backoff 与 failed Child safe reconciliation。
- 真实 PostgreSQL 已验证 `20260717_0030` upgrade、downgrade / upgrade guard replacement、Python / PostgreSQL canonical policy digest 一致，以及完整 v0.2 Manifest → 首 Attempt `INFRA_ERROR` → 第二 Attempt Ticket / Admission → 成功收敛链。真实 Temporal 既有 Root / Attempt 套件已接入新的 batch settlement Activity。
- 完整 `make verify` 已通过：900 tests、coverage 90.04%、Ruff、严格 mypy 291 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build 全部成功。破坏性的历史 Alembic 往返已改在独立临时数据库执行，重复验证不会触碰共享测试库中的 v0.2 不可变事实；前端只有自动生成 API 类型变化，原型未改。

### 后续边界

- P5-00D3A 不开放 RETRY HTTP command。P5-00D3B 已在后续切片把“仅重跑环境失败 / Rerun Failed”落为新子 TaskRun；它不复活旧 Run，也不覆盖旧 Attempt。
- Adapter 必须明确且可信地返回 `INFRA_ERROR` 才会自动重试。无法证明副作用状态的 `OUTCOME_UNKNOWN` 保持 fail-closed；首个 production `TaskUnitExecutionPort`、真实 SaaS 错误分类表和运维预算仍需部署侧输入。
- Task Plan authoring、Schedule / CI / Webhook Adapter、超过 64 Units 的可恢复分区物化、AttemptSeal / Result，以及 UnitAttempt-scoped Live control / Takeover 继续按独立切片落地。

## P5-00D3B 范围

### 已实现

- 新增 `POST /v1/task-runs/{runId}:rerun-infra-failures`。请求只携带 `clientMutationId`，同时要求 source Run 的 exact `If-Match`、`Idempotency-Key == clientMutationId` 与 `RUN_OPERATOR+`；只有 `SEALED / CLOSED` 且非 legacy 的源 Run 可创建子 Run。
- 子 Run 使用新的 `TaskRun / ExecutionUnit / UnitAttempt / Temporal Workflow` identity，绑定不可变 `rerunOfTaskRunId` 与 `rerunSelectionMode=INFRA_FAILURES`。它复制源 Manifest 的 exact Plan Version、schema、iteration、policy digests、retry policy 与 compiler version，只按稳定 `unitKey` 重编号并选择每个且仅有 `CLOSED / INFRA_ERROR` 的源 Unit。
- `20260717_0031` 在 PostgreSQL 中把 selection mode 与 lineage 绑定并禁止更新。owner-owned、固定 `search_path` 且不可被应用角色直接执行的 Manifest Insert Guard 重读 sealed parent、重算完整预期 Unit JSONB，并拒绝遗漏、夹带、配置漂移或空选择；存在 child rerun 事实时 downgrade fail-closed。
- 子 Run 复用源首 Attempt 的 execution-window 时长并平移到新的数据库排队时间，不延长冻结策略；现有 materialization repository 在同一短事务完成完整聚合、Seal 与唯一 `PENDING` Start Intent。首次创建追加 `task_run.rerun_requested` Event / Audit / Outbox，exact replay 返回同一子 Run 且不重复副作用。
- TaskRun JSON Schema、OpenAPI 和前端自动生成 TypeScript API 类型已同步；没有修改前端页面、组件、DOM、布局、CSS 或既有原型交互。

### 已验证

- Domain、Application、Repository、API 与 Migration 测试覆盖 lineage / mode、Revision / RBAC / idempotency、无可选 Unit、exact source selection、全新物理 identity、事件与 exact replay。
- 真实 PostgreSQL 已验证 `0031 → 0030 → 0031` 无事实往返、源 Run `INFRA_ERROR` 关闭后生成精确 child aggregate / Start Intent，以及存在 child fact 后拒绝降级。Manifest Guard 的 owner 权限、固定 search path 与最小执行权均已验证。
- 完整验证期间修复了 Execution Ticket deadline 的跨语言 canonicalization：Python 现在与 PostgreSQL 一致地使用 UTC、去除小数秒尾零；修复前的随机 digest 冲突已连续 20 次真实 Ticket 创建验证不再复现。
- 最终 `make verify` 全部通过：912 tests、coverage 90.06%、Ruff、严格 mypy 294 files、Schema / OpenAPI 漂移、Python sdist / wheel、前端 `check:api` / `tsc` / Vinext Production Build。

### 后续边界

- 该端点只按数据库最终事实选择 `INFRA_ERROR`，不接受客户端 Unit 列表，也不把 `FAILED / INCONCLUSIVE / CANCELED / OUTCOME_UNKNOWN` 解释成环境失败。它不是向旧 Workflow 发送的 RETRY command。
- Takeover 仍必须等待 UnitAttempt-scoped `LiveSession / ControlLease / Epoch / Fence / ActionGrant`；P5-00E1/E2 已补充 TaskPlan authoring、publication 与首次 Manual Launch，production Adapter、Schedule / CI / Webhook、超过 64 Units 的分区物化与 AttemptSeal / Result 继续独立落地。

## P5-00E1 范围

### 已实现

- 新增 `POST /v1/projects/{projectId}/task-plans`、Project TaskPlan Catalog、TaskPlan Detail、`POST /v1/task-plans/{taskPlanId}/versions`、不可变 Version 历史与 exact Version 读取。写操作要求 `RUN_OPERATOR+`、可审计 Actor 以及 `Idempotency-Key == clientMutationId`；读取继续以 RBAC + RLS 对跨 Tenant / Project 身份返回同形 404。
- TaskPlan 使用 Project 内唯一 `taskKey` 的稳定 Catalog 根；E1 不引入可变 Draft。TaskPlanVersion 请求只携带 exact CaseVersion、Matrix、Profile / Fixture 与 Policy Digest 引用；服务端生成 ID、发布时间、`versionRef` 与 canonical content digest，发布后保持 append-only。
- 创建和发布在同一短事务内完成业务事实、Idempotency completion、Audit 与 Outbox。PostgreSQL 既有 Guard 继续重验 PUBLISHED same-scope Case / Execution / Identity / Browser / Data Profile、PUBLISHED Fixture、ACTIVE TEST/STAGING Environment、结构化 JSON exact key set 和 canonical digest；应用层将门禁失败安全映射为 409，不暴露数据库异常正文。
- Repository 新增 TaskPlan `updatedAt + id` 与 TaskPlanVersion `publishedAt + id` 的稳定 keyset 查询，并复用既有索引。新增 `task-plan.schema.json`，同步 OpenAPI 与前端生成 TypeScript API 类型；没有修改前端页面、组件、DOM、布局、CSS 或既有原型交互。

### 已验证

- Domain、Repository、Application 与 API 定向测试覆盖 pinned Case / Profile exact coverage、稳定分页、写入幂等重放、RBAC、活动 Project、HTTP Header / Location / ETag 和版本精确读取。
- 真实 PostgreSQL API 集成测试已验证：TaskPlan 创建与 replay、使用真实已发布 Case / 四类 Profile / Fixture / Environment 发布 TaskPlanVersion、Catalog / History / exact read，以及不存在 Environment 的发布请求被数据库门禁拒绝并返回 409。
- E1 完整门禁通过：918 tests、coverage 90.13%、Ruff、严格 mypy 299 files、Schema / OpenAPI 漂移、Python sdist / wheel 与前端 `check:api` / lint / Vinext Production Build。

### 后续边界

- E1 不创建或选择四类 Profile；发布调用方必须引用已经存在的 exact published Profile。后续可在不改变前端原型的前提下增加兼容选项查询或部署侧 Profile provisioner。
- E1 本身不物化 TaskRun，也不创建 Start Intent；该边界已由独立的 P5-00E2 Manual Launch 承接。

## P5-00E2 范围

### 已实现

- 新增 `POST /v1/task-plan-versions/{taskPlanVersionId}:run`。请求要求 `RUN_OPERATOR+`、可审计 Actor、`Idempotency-Key == clientMutationId`，并携带可选 `iterationId` 与完整 `TaskRetryPolicy`；策略摘要必须与已发布 TaskPlanVersion 的 `infra-retry` 完全一致。
- Manual compiler 只展开有效组合：Environment 与 Browser 作为全局轴；Identity 必须绑定当前 CaseVersion；Data 必须绑定该 Case 的 Fixture Blueprint。无兼容 Identity / Data、失效 Profile 或编译结果超过 100,000 Units 时返回 409，不用盲目笛卡尔积制造不可执行 Unit；不超过 64 Units 保持同步原子快路径，更大 Run 进入 E4 的可恢复分区物化。
- 每次首次创建生成稳定 manual trigger fingerprint、`atlas.task-run-manifest/0.2` Manifest、确定性排序与连续 ordinal、TaskRun / ExecutionUnit / 首 UnitAttempt、15 分钟冻结执行窗口及确定性 Temporal Workflow identity。既有 Repository 与 PostgreSQL Seal 在同一短事务重验 Plan provenance、PUBLISHED Case / Profile / Fixture、ACTIVE TEST/STAGING Environment，随后创建唯一 `PENDING` Start Intent。
- 业务事实、幂等完成、`task_run.requested` Event、Audit 与 Outbox 同事务提交；重复请求返回同一 Run，不重复 Event、Audit、Outbox 或 Start Intent。新增 `task-plan-launch.schema.json` 并同步 OpenAPI 与前端生成 TypeScript API 类型，未修改前端原型页面。

### 已验证

- Domain / Application / API 定向测试覆盖 compatible-only expansion、稳定排序、100,000 Unit 协议上限与 64 Unit 快路径、retry policy 摘要、RBAC、幂等 Header、exact replay、Manifest v0.2、首 Attempt execution window 与 HTTP Location / ETag。
- 真实 PostgreSQL API 集成测试已从真实 published Case / 四类 Profile / Fixture / Environment 启动 Manual Run，验证 `SEALED`、exact Manifest、一个 Unit / 首 Attempt、`task_run.requested` Event、幂等 replay 与同事务 `PENDING Start Intent`。
- 完整门禁通过：921 tests、coverage 90.15%、Ruff、严格 mypy 301 files、Schema / OpenAPI 漂移；Python sdist / wheel 与前端 `check:api` / lint / Vinext Production Build 在本切片最终验收中执行。

### 后续边界

- Manual Launch 只承接首次 Run 创建，不提供 Temporal Schedule 管理或签名 Webhook Adapter；更大矩阵使用 E4 的可恢复分区物化，不扩大请求事务。
- Start Intent 只证明可靠交付；Worker / Consumer 仍默认关闭。没有部署注入真实 `TaskUnitExecutionPort` 时不会运行 SaaS 副作用；没有 AttemptSeal / Result 时也不能把执行完成描述成可信 `PASSED`。

## P5-00E3 范围

### 已实现

- 新增统一 `POST /v1/task-runs` Trigger 入口与 `atlas.task-run-trigger/0.1` 契约。Schedule、CI、Webhook 分别使用 `scheduleId + scheduledFireTimeUtc`、`provider + pipelineRunId + jobId + rerunIndex`、`sourceKey + deliveryId` 形成永久触发身份。
- 三类 Trigger 复用 E2 的 exact published TaskPlanVersion、compatible-only compiler、Manifest v0.2、Run / Unit / 首 Attempt、materialization Seal、唯一 Start Intent、Event、Audit、Outbox 与幂等事务，不另建弱一致性的旁路启动链。
- CI commit / branch 和 Webhook event type 只投影为受限审计元数据，不参与永久身份，也不能覆盖 TaskPlanVersion 冻结的 Environment、URL、Credential、Tool、Model 或 Policy。重复的外部事件即使更换 HTTP Idempotency-Key，也只能返回同一个逻辑 TaskRun。
- 新增并导出 Trigger JSON Schema，刷新 OpenAPI 与前端生成 TypeScript API 类型；没有修改前端页面、DOM、布局、样式、className 或原型交互。

### 已验证

- Domain / Application / API 定向测试覆盖 UTC 规范化、三类永久 Fingerprint、CI rerun identity、展示元数据排除、严格 payload、RBAC、Header、Location / ETag 和 exact replay。
- 真实 PostgreSQL API 集成测试验证：CI 同一 pipeline job / rerun 使用不同 HTTP Idempotency-Key 且 commit / branch 变化时仍只产生一个 `SEALED` Run，并复用同一 Start Intent 与事实链。

### 后续边界

- E3 是 Trigger ingress，不伪装为 Temporal Schedule catalog、overlap / catch-up 策略管理或外部 callback 验签服务；这些能力继续按独立切片落地。
- 不超过 64 Units 仍走同步快路径；65–100,000 Units 已由 E4 的分区物化和分页 Workflow 承接。Worker 仍要求部署注入正式 production `TaskUnitExecutionPort`，缺少时保持 fail-closed。

## P5-00E4 范围

### 已实现

- `20260718_0042` 将 Manifest / Run 协议上限扩展到 100,000 Units，同时保留不超过 64 Units 的同步原子路径。更大 Run 只在 API 事务中写入 `MATERIALIZING` Root、完整 immutable Manifest 与连续 64-Unit 分区检查点，不提前创建 Unit、Attempt 或 Start Intent。
- 独立 `atlas_dispatcher` Consumer 以数据库时钟、Lease、Claim Token、`dispatchRevision` 与 Consumer identity 领取分区。每个完成事务最多创建 64 个 exact ExecutionUnit / 首 UnitAttempt，并冻结 30 天 execution deadline；Retry / Failed / Lease takeover 都由 owner-owned `SECURITY DEFINER` 函数和 Revision Fence 约束，旧 Consumer 不能覆盖新 Claim。
- 只有所有分区均 `COMPLETED` 且 ordinal 无缝覆盖完整 Manifest 时，数据库才复用原 Seal 权威重算 Plan / Manifest / Unit / request digest、切换 `SEALED` 并追加唯一 `PENDING` Start Intent。部分物化、重复完成或错 Token 都不能进入调度。
- `AtlasTaskRunWorkflow` 每次最多加载 64 个 Unit，保持 8-child batch。当前页无活跃 Child、无未结算批次且全部结果已持久化后才 `Continue-As-New`；最终页用 PostgreSQL `execution_unit.finalized` 事实投影精确核对 Unit / Attempt / Event 全覆盖并关闭 Root，避免在单段 Temporal History 累积 100,000 个结果。
- Cancel 会先排空当前页，给尚未派发或待重试 Unit 写入明确的取消终态，再携带取消状态续跑后续页。因 `Continue-As-New` 不保留 Signal 内存，最终收口会从数据库恢复尚未应用的 exact Cancel command，并只在 Run 已 `CLOSED / CANCELED` 后确认。

### 已验证

- Migration、Repository、Application、Workflow 与 Worker 测试覆盖 100,000 上限、分区连续性、Claim / Retry / Fence、exact replay、分页 ordinal、取消排空、数据库投影完整性和旧 64-Unit 路径兼容。
- 真实 PostgreSQL 已验证一个 65-Unit Run 的两分区认领与提交、最后一个分区 Seal、唯一 Start Intent、64 + 1 分页读取、65 个 Unit / Attempt / Finalization Event 全覆盖、取消投影与 Root 关闭。
- 真实 Temporal 已执行 65 个 Child：首段 64 个全部结算后 `Continue-As-New`，末段只执行第 65 个，最终仅一次投影收口且无重复副作用。完整后端门禁为 1100 tests / coverage 90.02%，Ruff、严格 mypy 366 files、Contracts / OpenAPI、Python sdist / wheel、前端 API / TypeScript / production build 全部通过；前端原型未改。

### 后续边界

- 本切片解决的是大 Run 的耐久物化和 History 上界，不提供真实 SaaS production `TaskUnitExecutionPort`、Temporal Schedule catalog / overlap / catch-up 管理或外部 Webhook 签名验证；这些能力继续独立落地。
- 100,000 是协议与数据库硬上限，不代表可把单 Tenant 并发、Worker 并发或数据库连接池同步放大。P9 已完成 100 并发×100 轮本地 Lease 基准；100,000-Unit 长稳、真实 Worker 集群与生产容量仍需部署环境。

## P5-00E5 范围

### 已实现

- 新增正式 `HttpTaskUnitExecutionPort`，CLI Task Worker 可从完整配置直接装配，不再要求 Python 调用方手工注入 Adapter。禁用状态仍不连接 PostgreSQL / Temporal；启用但 URL / HMAC Key 缺失时在任何外部连接前 fail-closed。
- 线协议只发送 `atlas.task-unit-executor-request/0.1` secret-free envelope，内含已由数据库 Prepare 的 exact Attempt、`ticketId` 与 `ticketDigest`。请求 HMAC 同时绑定固定 Path、Worker、Tenant、Attempt、Ticket、时间戳、Nonce、Body SHA-256 与 `Idempotency-Key == unitAttemptId`。
- executor 响应必须使用 `atlas.task-unit-executor-result/0.1`，并以独立响应签名回绑原 Request Nonce / Digest、Worker / Tenant / Attempt / Ticket、HTTP Status、Response Timestamp 与 Response Body Digest；同时要求 `Cache-Control: no-store`、`application/json` 和 16 KiB 默认上限。
- Staging / Production 强制 HTTPS；Client 禁止 redirect、环境代理和隐式 transport retry。一次 Temporal side-effect Activity 只发起一次 HTTP 调用；Transport timeout、响应丢失、非 200、超限、签名或 payload 异常全部按 `INCONCLUSIVE` 的结果未知收敛，不会触发 `INFRA_ERROR` 自动业务重试。
- Port 只在 executor 已产生数据库可复核的 exact ResultRef 时接受 `RESULT_FINALIZED`；Task Worker 的 Finish Activity 仍会重读 AttemptSeal / ResultRef，远端响应本身不能伪造 `PASSED`。真实 SaaS 登录、业务 Operation、凭据和 Seal 签发仍由部署端 executor 负责。

### 已验证

- HMAC 双向协议测试覆盖 Request/Response exact scope、Body 篡改、Nonce、时钟窗口、幂等键、ResultRef；HTTP Adapter 测试覆盖 signed success、transport ambiguity、非 200、unsigned / invalid / oversized response、deadline、secret-free body、TLS 与配置完整性。
- Task Worker 测试覆盖 CLI 自动注入、缺少 Adapter 时外部连接前拒绝、双 Queue 装配和 pooled Adapter 关闭；`.env.example` 与 Compose profile 已同步可选配置。当前环境没有 Docker CLI，无法重复执行本地 `docker compose config`。
- 真实 PostgreSQL + Temporal 完整后端门禁通过：1114 tests、coverage 90.04%；Ruff、strict mypy 368 files、Contracts / OpenAPI 漂移、Python sdist / wheel、前端 API / TypeScript / production build 全部通过；前端原型源码未改。

### 后续边界

- 本切片完成平台侧 production Port 与安全传输，不伪造一个不存在的目标 SaaS executor。部署方必须提供受审 executor endpoint、同一 Attempt 的业务幂等、Secret / Session / Network 隔离，以及签名 AttemptSeal / ResultRef 写入链。
- Temporal Schedule catalog、Overlap / Catchup / Jitter 已由 E6 完成；签名 Callback 已由 E7 完成，P9 本地容量 / 故障注入也已形成固定 Runner。

## P5-00E6 范围

### 已实现

- `20260718_0043` 新增强制 RLS、定义不可变的 `task_schedule` 与 fenced `task_schedule_sync_intent`。API 创建 Schedule 或请求 Pause/Resume 时，同事务提交 desired state、Sync Intent、Audit、Outbox 与幂等响应；API 事务不调用 Temporal。
- 开放 `POST /v1/task-plan-versions/{id}/schedules`、Schedule get/list、`POST /v1/schedules/{id}:pause` 与 `:resume`。创建命令使用结构化 Calendar、IANA Timezone、V1 `QUEUE_ONE / SKIP`、有限 `RUN_ONCE / SKIP` Catchup、Jitter 与 exact TaskRetryPolicy；响应投影未来五个真实 UTC fire。
- `zoneinfo` 计算明确跳过 DST gap，并为 fold 保留两个真实 UTC 时刻。数据库重算 immutable content digest、Temporal Schedule ID、PlanVersion/Environment/RetryPolicy scope；Environment 变为 `PRODUCTION` 时自动 Pause 关联 Schedule，并拒绝恢复。
- Dispatcher 只通过四个 owner-owned 窄函数 Claim/Apply/Retry/Fail。陈旧 Schedule Revision 自动 `SUPERSEDED`；Temporal create/describe/pause/unpause 在事务外，最终以 Claim Token + Dispatch Revision + Schedule Revision CAS。
- `TemporalTaskScheduleSynchronizer` 精确复核顶层 Memo、Workflow Action/Input/Memo、固定 Queue、结构化 Calendar、Timezone、Overlap/Catchup/Jitter 和 policy；同 ID 不同定义永久失败。`pause_on_failure=true`，不支持 BufferAll/AllowAll。
- `AtlasTaskScheduleTriggerWorkflow` 只接受 Temporal 保留的 `TemporalScheduledById` 与 `TemporalScheduledStartTime`，Activity 重读数据库后复用统一 TaskRun compiler。永久 Fingerprint 是 `scheduleId + nominal fire UTC`；Pause 只跳过 Pause 后启动的 Workflow，不篡改已启动 TaskRun。
- 新增默认关闭的 `atlas-task-schedule-worker`、Docker target、Compose profile 和配置门禁；Schedule 同步并入现有独立 `atlas_dispatcher` Consumer。新增 TaskSchedule 创建/投影 JSON Schema、OpenAPI 与生成 TypeScript 类型，未修改前端原型 DOM、布局、样式、className 或交互。

### 已验证

- 领域与数据库测试覆盖 IANA Zone、DST gap/fold、Digest、RLS、RBAC、幂等、ETag、Pause/Resume、Production 自动 Pause、最小权限、Claim/Retry/Fail/Apply Fence 与陈旧 Revision。
- 真实 Temporal 验证结构化 Schedule、未来 fire、create/replay collision guard、保留名义时间、立即 fire、Pause/Resume 和 Workflow 完成；真实 PostgreSQL + Temporal 纵向链验证 `Schedule → Sync Intent → Temporal action → unified compiler → SEALED TaskRun` 与 exact Trigger Fingerprint。
- Temporal Python 1.30 对 Workflow 沙箱 dataclass `datetime` Activity 结果的反序列化差异已通过 primitive ISO-8601 线协议消除；应用/数据库边界仍使用 aware `datetime`。
- 完整门禁通过：1149 passed / 8 skipped、coverage 90.04%、Ruff、strict mypy 391 files、Contracts / OpenAPI 漂移、Python 3.14 sdist / wheel、前端 API / TypeScript / production build 全部成功；前端原型源码未改。当前环境没有 Docker CLI，未重复执行 `docker compose config`。

### 后续边界

- E6 不开放任意 Cron 字符串、BufferAll/AllowAll、无界 Catchup、客户端执行配置覆盖或 Backfill API。生产启用仍需 Schedule Worker、Dispatcher、Root/Attempt Worker 和真实 executor 全部就绪。
- 容量、故障注入、30 次黄金链和 SLO 属于 P9；本地参考门禁已完成，生产 Telemetry 与试点仍按 Runbook 验收。

## P5-00E7 范围

### 已实现

- 每个新 `TaskGateDecision` 都在同一数据库事务内写入唯一 `task_gate_callback_intent`；Gate 的永久幂等重放不会产生重复事件。Intent 精确冻结 Event、TaskRun、Manifest、Gate Decision、Verdict 与 Timestamp，不保存 Endpoint、Key、签名、数据库 URL 或其他 Secret。
- `atlas.task-gate-callback/0.1` 事件文档固定为 `eventId / taskRunId / manifestHash / gateDecision / timestamp / signature` 六个字段。HMAC-SHA256 精确覆盖前五个字段的 canonical bytes，Timestamp 使用 UTC 整秒；接收方可同时执行签名、重放窗口与永久 `eventId` 幂等校验。
- 独立 `atlas-task-gate-callback-consumer` 只使用固定部署 Endpoint 和独立 HMAC Key。HTTP 发送发生在数据库事务外，禁用 Redirect 和环境 Proxy；Production 强制 HTTPS。`2xx` 标记 Delivered，`408 / 425 / 429 / 5xx` 与传输失败进入有界重试，其余 `4xx` 永久失败。
- `atlas_dispatcher` 只通过四个 owner-owned 窄函数 Claim / Delivered / Retry / Fail，以 Claim Token 和 Lease CAS 防止陈旧 Consumer 覆盖。投递采用 at-least-once，同一 Intent 的每次重试保持同一 `eventId`。
- 新增 `20260718_0044` 强制 RLS、插入复核、不可变语义字段与状态转换守卫；`atlas_app` 只可 Select / Insert，`atlas_dispatcher` 没有 Callback 表直接 DML 权限。新增 Worker 配置、Docker Target、Compose Profile、环境变量示例、机器契约与 ADR-0012。

### 已验证

- 领域、Signer、HTTP Adapter、Consumer、Repository、Worker、配置与 Migration 定向测试覆盖 canonical body、密钥强度、签名篡改、Timestamp 重放、URL/TLS/Redirect 边界、状态分类、重试耗尽、Crash-safe 三段式处理、Lease 丢失、最小权限与 populated downgrade fail-closed。
- 真实 PostgreSQL 完整 `Task → Result → Gate → Callback Intent` 链验证每个新 Gate 精确一个 Intent、Gate replay 不重复；真实发送验证 exact 六字段签名体、`204 → DELIVERED`、`503 → RETRY_WAIT → 204` 使用同一 `eventId`，以及 `400 → FAILED`。
- 完整门禁通过：1190 passed、coverage 90.18%、Ruff、strict mypy 400 files、Contracts / OpenAPI 漂移、Python 3.14 lock / sdist / wheel、前端 API / TypeScript / production build 全部成功。当前环境没有 Docker CLI，Compose 以 Ruby YAML parser 完成静态语法校验；前端原型源码未改。

### 后续边界

- Callback Receiver 不属于本仓库；接收方必须在重放窗口内验签、永久按 `eventId` 去重，并对重复事件返回 `2xx`。本仓库不允许调用方在 API 请求中指定 Callback URL 或 Key。
- P9 已验证本地故障注入、容量、隔离、黄金链与参考 SLO；生产部署仍需真实 Receiver Endpoint / Key、Network Policy、Secret Manager、运行告警与外部门禁。

## P9-01 范围

### 已实现

- 新增 opt-in `backend/scripts/run_p9_acceptance.py` 与 `make p9-acceptance`。Runner 固定要求至少 30 次黄金链和 30 个 Schedule 样本，单个子进程 900 秒超时；任何本地 Gate 失败都非零退出。
- `atlas.p9-acceptance-report/0.1` 只输出 Revision、安全测试摘要、整数计数与毫秒，不保存 DSN、Secret、ObjectRef、Payload 或异常原文。报告把本地 `PASSED`、真实失败和外部 `NOT_EVALUATED` 分开；存在未评估生产 Gate 时总状态只能是 `CONDITIONAL_PASS`。
- 新增默认跳过的 Heavy P9 Suite：100 个 Account Slot 上执行 100 并发×100 轮完整 Acquire / Release；100 个 deterministic large PNG 经过 canonicalize、write-once、read-back 和独立 digest verify；100 个应用内 SSE event-to-client 样本计算 nearest-rank P95。
- 固定 Fault Matrix 复用现有受信测试，覆盖 API timeout/unknown outcome、账号 TTL、Temporal Worker interruption、Evidence Store transport failure、SSE disconnect/stall 和 Cleanup retry / Sweeper。
- Capacity / Isolation Matrix 同时覆盖账号不足、2×本地参考峰值、多 Project Task 查询、Evidence Read Grant、Debug Live 的跨 Project / Tenant 不可见。
- 新增 ADR-0013、P9 Local Reference Baseline 与 Production Readiness Runbook，明确发布顺序、默认关闭开关、Callback unknown outcome、Cleanup / Evidence 止损、兼容镜像回滚和灾备恢复检查。

### 已验证

- Fault Matrix 12 passed；Capacity / Isolation 7 passed。
- Account Lease 完成 10,000 次循环，重复 Active Slot 0、遗留 Active Lease 0、Fence 从 1 单调推进到 100；第二次完整 Runner 记录 5,472 次 `SKIP LOCKED` 短暂背压，全部在同一命令身份的有界重试内收敛。
- 大 Evidence 负载 100 / 100 独立校验，总计 78,807,900 bytes；应用内 Live Event 100 样本 P95 为 4 ms。
- 真实 PostgreSQL 完整 `Task → Result → Classification → Gate → Callback` 与 Cleanup 黄金链连续 30 / 30 通过，本地参考平台失败率 0.00%，Cleanup 断言 100%。
- 真实 PostgreSQL + Temporal Schedule 纵向链 30 / 30 通过；包含测试初始化的保守完整命令 P95 为 4,787 ms，低于 60,000 ms 本地参考门槛。
- P9 Runner 最终生成 `CONDITIONAL_PASS`；所有可在仓库内评估的 Gate 均为 `PASSED`。
- 最终日常门禁 1,192 passed / 3 个 opt-in P9 Heavy tests skipped，coverage 90.18%；Ruff、strict mypy 403 files、Contracts / OpenAPI、Python 3.14 package、前端 API / TypeScript / production build 与 Compose YAML 均通过。

### 外部门禁

- 控制面 99.9% / 月、真实 Network / Proxy / Browser 下的 Schedule / Live SLO、人工 Failure Classification 准确率 ≥90%、真实团队影子迭代和经批准 RTO / RPO 的灾备演练没有部署证据，保持 `NOT_EVALUATED`。
- 真实 SaaS Executor、业务黄金用例、测试账号、Callback Receiver、生产 Object Store/KMS、Network Policy 和监控后端必须由部署 / 试点提供；本地确定性 Adapter 结果不能替代这些输入。

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
4. P5-00D1 已建立 ticket-bound Port 输入，P5-00D2A/D2B 已落地 durable TaskRun Cancel 与 batch-boundary Pause / Resume；下一 P5 控制切片应让 Retry 追加新 Attempt，Takeover 等待 P6-02B2 ControlLease / Epoch / Fence，超过 64 Units 另做可恢复分区物化。
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

## P6-02B2 范围

### 已实现

- 建立 `atlas.live-session/0.1`、`atlas.control-lease/0.1`、`atlas.live-control-command/0.1`、`atlas.live-action-grant/0.1` 与 `atlas.unit-attempt-live-snapshot/0.1`。全部事实精确绑定一个正式 UnitAttempt、immutable Execution Ticket、TaskRun / ExecutionUnit 和 BrowserSession，不复用 DebugRun 多态宿主。
- `20260718_0041` 建立五张 `FORCE RLS` 表、复合 Scope FK、单 Current Lease / Pending Command、单 Action、单次 Grant、单调 Epoch/Fence、状态机、不可更新/删除事实和最小列级权限。人工影响永久保留，数据库阻止 Human-influenced Attempt 封存为 `AUTONOMOUS`。
- 公共 API 开放 Snapshot、异步 `PAUSE / RESUME / TAKEOVER / RETURN` 与 Command 查询。写命令要求强 Control Epoch `If-Match` 和独立 `Idempotency-Key`；同键 exact replay，不同请求冲突。Production Environment Takeover 在应用边界 fail-closed。
- Worker 内部 API 继续使用 exact UnitAttempt Permit + HMAC，支持初始化、Agent Heartbeat、Action Safe Point / Reconcile acknowledgement，以及 ActionGrant 签发、恢复、原子消费和唯一 Receipt。Browser Worker 仍不读取控制面数据库。
- Pause / Takeover 先进入 `QUIESCING`、撤销未消费 Grant 并等待已消费动作回执；Safe Point 后才提升 Epoch/Fence 并进入 `PAUSED` 或 Human Lease。Return 先进入 `RECONCILING`，重建 Page Revision 后才签发新 Agent Lease。
- Heartbeat 只延长当前 Agent Lease，不改变 Owner、Epoch 或 Fence，并受 Attempt Deadline 上限约束。Tenant Reaper 以有界 `FOR UPDATE ... SKIP LOCKED` 批次回收过期 Lease，原子撤销未消费 Grant、拒绝 Pending Command、提升 Fence 并进入 `NO_CONTROLLER`。
- 既有 Task Live 原型使用真实 TaskRun → ExecutionUnit → latest UnitAttempt → LiveSnapshot 映射，在原有 Agent Intent 和接管按钮槽位展示状态并提交 Takeover / Return；未修改 DOM、布局、样式、className 或交互结构。

### 验证状态与后续边界

- 真实 PostgreSQL 覆盖 Initialize replay、Heartbeat、Pause → Resume → Takeover → Return 全状态链、Safe Point、幂等冲突、陈旧 Epoch/Fence、单次 Grant / Receipt replay、TTL Reaper、RLS、最小权限和人工影响门禁。
- 公共 API 覆盖率 100%，内部 API 96%，Domain 96%，Repository 94%；完整门禁为 1075 passed、coverage 90.22%，Ruff、严格 mypy 361 files、Contracts / OpenAPI 漂移、TypeScript 与 production build 全部通过。
- P5-00D2B 的 Task Pause 仍只停止新 Unit 派发，与本切片的 Browser Action Safe Point 分离。SSE 仍是只读观察通道，不承载控制命令。
- 首个真实 SaaS Operation / Published Route、容器级 Egress / DNS / UDP / WebRTC、Envelope Key Ring、公共 Start 自动 Preparation / Bind / Dispatch、Multi-actor、production `TaskUnitExecutionPort`、Schedule / CI / Webhook 和超过 64 Units 的分区物化继续后续落地；缺少受信部署能力时保持 fail-closed。

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
| 2026-07-16 | P5-00B2B Task 耐久编排 | 最多 64 Units 的真实 Root / Attempt Workflow、固定双 Queue、8-child batch、deadline、`maximumAttempts=1` 副作用 Activity、Heartbeat / 等待取消完成、`20260716_0025` tenant-scoped 执行链锁 | 通过；真实 PostgreSQL / Temporal 已验证锁序、最小权限、跨 batch、deadline 排队、瞬时数据库故障恢复、exact replay、History 脱敏、非 PASS 收敛、原生取消未知结果与已完成 Child 结果保留；`0025 → 0024 → 0025` 往返、Task Worker 镜像和默认关闭启动均成功；完整 `make verify` 通过，780 tests / coverage 90.15%、Ruff、严格 mypy 269 files、契约漂移、Python sdist / wheel 与前端生产构建全部成功 |
| 2026-07-16 | P5-00C TaskRun 查询控制面 | Project TaskRun keyset、Run / Manifest、Unit / Attempt、Event 只读 API，RBAC + RLS、父级归属和 `20260716_0026` 查询索引 | 真实 PostgreSQL、`0026 → 0025 → 0026` 往返与完整 `make verify` 通过：789 tests / coverage 90.25%、Ruff、严格 mypy 275 files、契约漂移、Python build 和前端 production build；控制命令保持关闭，前端仅同步生成类型，原型未改 |
| 2026-07-17 | P5-00D1 不可变 Execution Ticket | 每 Attempt 唯一 secret-free Ticket、owner-owned Insert Guard、不可变 Trigger、FORCE RLS、Prepare Activity 与 ticket-bound Port Protocol | 真实 PostgreSQL `0027 → 0026 → 0027`、跨 Tenant / 篡改 / replay、真实 Temporal 1 / 9 Child 与完整数据库执行链均通过；完整 `make verify` 800 tests / coverage 90.25%、Ruff、严格 mypy 279 files、契约漂移、Python build 与前端 production build 全部成功；前端原型未改 |
| 2026-07-17 | P5-00D2A durable TaskRun Cancel | exact Revision + mutation idempotency、`20260717_0028` command intent、dispatcher fenced Signal、Root safe cancel、terminal reconciliation 与 command status | 真实 `0028 → 0027 → 0028`、PostgreSQL API / RLS / 最小权限 / APPLIED 收口和 Temporal duplicate / active Child / late-race 均通过；完整 `make verify` 872 tests / coverage 90.48%、Ruff、严格 mypy 289 files、契约漂移、Python build 与前端 production build 全部成功；前端原型未改 |
| 2026-07-17 | P5-00D2B durable TaskRun Pause / Resume | `atlas.task-run-command/0.2`、`20260717_0029`、8-child batch 预授权门禁、durable checkpoint / wait / wake、Cancel supersession | 真实 `0028 ↔ 0029`、PostgreSQL lifecycle / command / Ticket、Temporal 10 Units 首批暂停和恢复均通过；完整 `make verify` 888 tests / coverage 90.24%、Ruff、严格 mypy 290 files、契约漂移、Python build 与前端 production build 全部成功；前端原型未改 |
| 2026-07-17 | P5-00D3A automatic infrastructure retry | `task-run-manifest/0.2` frozen policy、显式 `INFRA_ERROR`、deterministic append-only Attempt、durable backoff、原 deadline 与双重 Admission / Ticket Guard | 真实 PostgreSQL 已验证 v0.2 Manifest → 首 Attempt `INFRA_ERROR` → 第二 Attempt Ticket / Admission → 成功收敛，以及 `0030` guard replacement 和 fail-closed downgrade；完整 `make verify` 900 tests / coverage 90.04%、Ruff、严格 mypy 291 files、契约漂移、Python build 与前端 production build 全部成功；历史迁移测试独立运行，前端原型未改 |
| 2026-07-17 | P5-00D3B manual infra-failure child Run | database-proven exact `INFRA_ERROR` selection、不可变 lineage / selection mode、全新物理 identity、Seal / Start Intent | 真实 PostgreSQL 与 `0031` 往返 / fail-closed downgrade 已验证；完整 `make verify` 912 tests / coverage 90.06%、Ruff、严格 mypy 294 files、契约漂移、Python build 与前端 production build 全部成功；前端原型未改 |
| 2026-07-18 | P5-00E1 TaskPlan authoring / immutable publication | TaskPlan Catalog、append-only published Version、RBAC、幂等、Audit / Outbox、数据库 exact dependency gate | 真实 PostgreSQL API 已验证 create / replay / publish / query / invalid dependency；完整门禁 918 tests / coverage 90.13%、严格 mypy 299 files、契约与双端构建全部成功；前端原型未改 |
| 2026-07-18 | P5-00E2 Manual Launch | compatible-only matrix compiler、Manifest v0.2、最多 64 Units、首 Attempt、Seal、durable Start Intent | 真实 PostgreSQL API 已验证 sealed aggregate、Event、replay 与 `PENDING` Start Intent；完整门禁 921 tests / coverage 90.15%、严格 mypy 301 files、契约与双端构建全部成功；前端原型未改 |
| 2026-07-18 | P5-00E3 Unified Task Trigger | Schedule / CI / Webhook 强类型入口、永久事件身份、统一编译 / Seal / Start Intent 与 exact replay | 真实 PostgreSQL API 与完整门禁通过：1082 tests / coverage 90.23%、Ruff、严格 mypy 361 files、契约与双端构建全部成功；前端原型未改 |
| 2026-07-18 | P5-00E4 Partitioned Materialization / Execution | `0042` 64-Unit fenced partitions、100,000 上限、Consumer、分页 Root、safe Continue-As-New、DB projected finish / cancel drain | 真实 65-Unit PostgreSQL 两分区全链与真实 Temporal 64 + 1 Child 续跑通过；完整门禁 1100 tests / coverage 90.02%、Ruff、严格 mypy 366 files、Contracts / OpenAPI、Python 包与前端 API / TypeScript / production build 全部成功；前端原型未改 |
| 2026-07-18 | P5-00E5 Signed production execution Port | ticket-bound secret-free HTTPS、双向 HMAC / Nonce / Digest、single-call unknown-outcome、CLI / Compose 装配与资源回收 | 真实 PostgreSQL + Temporal 完整后端门禁 1114 tests / coverage 90.04%；Ruff、strict mypy 368 files、Contracts / OpenAPI、Python package 与前端 API / TypeScript / production build 全部通过；签名、TLS、超时、非 200、无签名 / 篡改 / 超限响应与 deadline 均 fail-closed，前端原型源码未改 |
| 2026-07-18 | P5-00E6 Database-authoritative Temporal Schedule | immutable Schedule Catalog、IANA Calendar / DST、Overlap / Catchup / Jitter、fenced Sync Intent、Pause/Resume、reserved fire identity 与统一 Trigger | 真实 PostgreSQL + Temporal 验证 `Schedule → Sync Intent → Temporal → unified compiler → SEALED TaskRun`；完整门禁 1149 passed / 8 skipped、coverage 90.04%、Ruff、strict mypy 391 files、Contracts / OpenAPI、Python 3.14 package 与前端 API / TypeScript / production build 全部通过；前端原型源码未改 |
| 2026-07-18 | P5-00E7 Signed Task Gate Callback | Gate 同事务 Intent、exact 六字段 HMAC 事件、独立 Consumer、at-least-once、fenced Claim / Retry / Delivered / Fail | 真实 PostgreSQL 验证 `Task → Result → Gate → Callback Intent`、Gate replay 不重复、`204` 成功、`503` 同 Event 重试成功和 `400` 永久失败；完整门禁 1190 passed / coverage 90.18%、Ruff、strict mypy 400 files、Contracts / OpenAPI、Python 3.14 package 与前端 API / TypeScript / production build 全部通过；前端原型源码未改 |
| 2026-07-18 | P9-01 Local Production Hardening | 固定 Fault Matrix、100×100 Lease、2× Evidence、Isolation、30 次黄金链、30 次 Schedule、Live P95、机器报告与 Runbook | 本地 Gate 全部通过：12 fault tests、10,000 Lease 冲突 0、100 / 100 Evidence、30 / 30 黄金链、Cleanup 100%、Schedule P95 4,787 ms、Live P95 4 ms；最终日常门禁 1,192 passed / 3 skipped、coverage 90.18%，静态、契约、包与前端构建全通过；生产月度 SLO、人工分类、影子迭代与灾备演练为 `NOT_EVALUATED`，总体 `CONDITIONAL_PASS`；前端原型源码未改 |
| 2026-07-18 | P6-03A AttemptSeal / ResultRef | Ed25519 contract、Finalize exact replay / conflict、Task trusted PASS recovery、repository、真实 PostgreSQL 与 migration | 通过；70 项定向测试与 1 项真实 PostgreSQL 全链通过，`0032` 有 Fact downgrade 拒绝及清理后 `0032 → 0031 → 0032` 往返成功；完整门禁 937 tests / coverage 90.09%、Ruff、严格 mypy 311 files、Schema / OpenAPI 漂移与 Python sdist / wheel 全部通过；前端原型未改 |
| 2026-07-18 | P6-03B ClosureNotice / UnitResolutionRevision | 无 Seal 终态事实、完整 Attempt 覆盖、append-only Unit Resolution、重试 Stability、Task / Finalize 事务投影、真实 PostgreSQL 与 migration | 通过；37 项 Result 定向测试与 4 项真实 PostgreSQL 全链通过，`0033` 有 Projection Fact downgrade 拒绝，独立空库 `0033 → 0032 → 0033` 往返成功；干净数据库完整门禁 969 tests / coverage 90.13%、Ruff、严格 mypy 316 files、Schema / OpenAPI 漂移与 Python sdist / wheel 全部通过；前端原型未改 |
| 2026-07-18 | P7-01A TaskResultSnapshot Truth | Manifest-ordered latest Resolution Set、Closure-compatible input root、固定 Snapshot Policy / Watermark、Verdict 守恒、各轴分布、四类精确通过率、Task close 原子 Snapshot / Outbox、`0034` append-only guard | 切片完成时本地门禁为 879 passed / 108 skipped；后续 P7-01B0 已在真实 PostgreSQL 完成 `0033 → 0034 → 0035`，AttemptSeal 全链复核 Snapshot Insert Guard，并以 1000 tests / coverage 90.08% 跑通完整 PostgreSQL / Temporal 门禁。`0034` standalone populated downgrade 未单独重复，因为现存 `0035` Cleanup Fact 会按设计先阻止链式降级；前端原型未改 |
| 2026-07-18 | P7-01B0 Cleanup Truth Bridge | Attempt ↔ FixtureRun exact binding、Fixture cleanup / Manifest / Resource / CleanupAttempt / Reconcile observation hash、append-only Unit Hygiene Revision、重试最严重状态保留、`0035` guard | 通过；真实 PostgreSQL 完成空表 `0035 → 0034 → 0035`、合法 binding / Hygiene canonical hash parity、伪造 Blueprint Plan 拒绝、exact replay、最小权限与 populated downgrade fail-closed；完整门禁 1000 tests / coverage 90.08%、真实 Temporal、Ruff、严格 mypy 323 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过；前端原型未改 |
| 2026-07-18 | P7-01B1 FULLY_RESOLVED Snapshot | 向后兼容的 Snapshot 0.2、Manifest-ordered Quality + Hygiene 双输入根、terminal Hygiene readiness、DataHygiene-only overlay、Task close / late Fixture 双触发、`0036` append-only guard | 通过；真实 PostgreSQL 已验证 `0035 → 0036` 兼容既有 0.1 Fact、合法 `QUALITY_FINAL → FULLY_RESOLVED`、exact replay、存在 FULLY Fact 时 downgrade fail-closed，以及独立空库 `0036 → 0035 → 0036` 往返；完整门禁 1011 passed / coverage 90.01%，真实 Temporal、Ruff、严格 mypy 324 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过；前端原型未改 |
| 2026-07-18 | P7-01B2 显式 REEVALUATED Snapshot | Snapshot 0.3、不可变 Reevaluation Command、exact Full source + frozen Policy binding、无自动重评、`0037` append-only guard | 通过；真实 PostgreSQL 已验证现有数据 `0036 → 0037`、显式 Full rev2 → Reevaluated rev3、exact replay、命令事实、最小权限、populated downgrade fail-closed，以及独立空库 `0037 → 0036 → 0037` 往返；完整门禁 1021 passed / coverage 90.11%，真实 Temporal、Ruff、严格 mypy 327 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过；前端原型未改 |
| 2026-07-18 | P7-02A FailureCluster / FailureClassification | exact Snapshot-bound manifest-ordered Cluster、保守规则归因、immutable Evidence Ref、basis-point confidence、人工 append-only review、RBAC / Idempotency / Audit / Outbox、`0038` database guard | 通过；真实 PostgreSQL 已验证 Cluster / Classification canonical hash parity、exact replay、人工确认 revision、最小权限与 advisory-lock 并发边界；全新临时数据库从零升级到 `0038` 成功；完整门禁 1032 passed / coverage 90.08%，Ruff、严格 mypy 332 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过；没有增加公共 Result API，前端原型未改 |
| 2026-07-18 | P7-02B / P7-03 Trusted Result Decision | exact Snapshot + complete Classification set、fail-closed three-valued Gate、Result / Resolution / Cluster / Review / Gate API、ETag、既有 Results 槽位接线、`0039` guard | 通过；真实 PostgreSQL 验证 Gate hash parity、永久 replay、RLS 与最小权限；完整门禁 1048 passed / coverage 90.13%、Ruff、严格 mypy 342 files、Contracts / OpenAPI、Python 包与前端类型/生产构建全部通过；原型 DOM、布局、样式和交互未改 |
| 2026-07-18 | P8 V1 Comparable Insight Snapshot | fixed MetricDefinition、qualityFinalizedAt、ratio-of-sums、current/baseline、DatasetCut、terrain / Gate risk、preview / pin / exact API、`0040` guard、既有 Insights 槽位接线 | 通过；真实 PostgreSQL 完整 Task → Result → Classification → Gate → Insight preview / pin / replay 链、RLS、append-only 与 `0040 ↔ 0039` 空表往返通过；完整门禁 1063 passed / coverage 90.16%、Ruff、严格 mypy 352 files、Contracts / OpenAPI、Python sdist / wheel、前端 API 漂移 / 类型 / production build 全部通过；未改原型结构 |
| 2026-07-18 | P6-02B2 UnitAttempt Live Control | LiveSession / ControlLease、Epoch/Fence、Safe Point、Takeover / Return、Heartbeat / Reaper、持久化单次 ActionGrant、既有 Live 槽位接线、`0041` guard | 通过；真实 PostgreSQL 完整状态链、旧 Fence / Grant、TTL 回收、RLS 与人工影响门禁均通过；完整门禁 1075 passed / coverage 90.22%、Ruff、严格 mypy 361 files、Contracts / OpenAPI、TypeScript 与 production build 全部通过；未改原型 DOM、布局、样式或 className |

## P6-03A 范围

### 已实现

- 建立 `attempt-seal/1.0` 与 `atlas.result-ref/0.1` 机器契约。AttemptSeal 精确绑定 Tenant / Project、TaskRun / ExecutionUnit / UnitAttempt、Run Manifest、Unit Key、不可变 Execution Ticket、Oracle / Artifact / Event Chain Hash、Evidence Policy Digest、Formal Runtime Digest、六条终态轴和签名 Key ID；`PENDING` Verdict、无完整可信证据的 `PASSED` 与伪造 Content Hash 在领域边界直接拒绝。
- 使用 canonical JSON signing body、SHA-256 Content Hash 与可注入 Ed25519 public key ring 验证 Runtime 签名。签名值、Key ID 和 Hash 均有有界 wire format；未知 Key、篡改内容与无效签名 fail-closed。
- `20260718_0032` 建立 append-only `unit_attempt_result_fact`、`result_ref` 与 `result_integrity_incident`。Scope FK 绑定 exact Attempt / Ticket / Fact，ResultRef 的 Seal Hash / CreatedAt 与 Fact Hash / AcceptedAt 由复合 FK 强制一致；数据库 Insert Guard 复核当前 RUNNING Attempt、Ticket、30-key JSON 投影、敏感字段、签名元数据与 canonical hash，三表启用 `FORCE RLS`、不可变 Trigger 和最小 SELECT / INSERT 权限。
- `FinalizeAttemptResultService` 在同一 Tenant 事务和 Run → Unit → Attempt 锁链中完成 Scope / Policy / Runtime / Hygiene / 时间窗复核、Fact + ResultRef 写入、Attempt `RUNNING → FINALIZING → CLOSED`、Task Event 与 Outbox。相同 Attempt + 相同 digest 返回原 ResultRef；不同且已通过签名与 Scope 校验的 digest 不覆盖既有 Fact，只追加 Integrity Incident 并返回稳定冲突。
- Task execution / Workflow Payload 新增 `RESULT_FINALIZED` 与 `resultRefId + sealContentHash`。Task Worker 每次结算先读数据库 Fact；只有 exact Seal / Ref 与 CLOSED Attempt 一致时才允许 `PASSED`，无 Fact 的 `PASSED` 必须拒绝。即使 execution Activity 已完成 Finalize 但成功回包丢失，后续 Finish / Settle / Run Finalize 仍会从数据库恢复 Attempt → Unit → Run 的可信 `PASSED`。
- 未 Seal 的成功执行继续使用 `EXECUTED_UNSEALED → FINISHED_UNSEALED / INCONCLUSIVE`，不会因新增 PASSED 类型而放宽。P6-03A 没有增加公共 Result API、Result Snapshot、Resolution / Classification / Gate，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- Result Domain、签名验证、Finalize exact replay / 内容冲突、Repository 序列化、Workflow 回包丢失恢复和 Migration 静态约束共 70 项定向测试通过；新增文件 Ruff、严格 mypy 与机器 Schema 生成 / 漂移检查通过。
- 真实 PostgreSQL 全链验证正式 Task 聚合 / Ticket、签名 Seal、Fact + Ref、exact replay、不同 digest Incident、Activity 回包丢失恢复、Attempt / Unit / Run `PASSED`、RLS 与不可删除权限，并证明数据库会拒绝错误 Runtime Digest / Hygiene。Migration 在存在 Fact 时正确拒绝 downgrade，清理测试 Result 行后完成 `0032 → 0031 → 0032` 往返；完整后端门禁 937 tests、coverage 90.09%，真实 PostgreSQL / Temporal 全部通过。
- P6-03B 已基于不可变 Attempt Fact 建立 UnitResolutionRevision，P7-01A 已在后续切片建立 TaskResultSnapshot；Classification、Gate 与公共查询 API 仍不提前增加空 API 或空 Schema。P6-02B2 的 LiveSession / ControlLease / Epoch / Fence / Human Takeover / ActionGrant 继续作为独立控制权切片。

## P6-03B 范围

### 已实现

- 建立 `atlas.attempt-closure-notice/0.1`。每个无 Seal 的 CLOSED Attempt 写入一个 immutable ClosureNotice；该事实精确绑定 Tenant / Project、TaskRun / ExecutionUnit / UnitAttempt、Manifest、Unit Key、Attempt Number、关闭时间和 Hygiene，只能表达 `INCONCLUSIVE / NOT_EVALUATED`，证据保持 `UNVERIFIED`，不能制造 `PASSED / FAILED`。
- 建立 `atlas.unit-resolution-revision/0.1`。每个 Revision 绑定 Unit 的全部 CLOSED Attempt Seal / ClosureNotice 输入、canonical input-set hash、固定 Resolution Policy、decisive Attempt、有效终态轴与 Stability；输入不变时 exact replay 返回既有 Revision，输入集合变化时追加 gapless Revision 并保留稳定 Resolution Root 与 predecessor。
- Resolution 以最新物理 Attempt 为 decisive source，同时保留历史解释：单次可信通过 / 失败为 `STABLE`，失败后通过为 `FLAKY_SUSPECT`，Platform / Infrastructure 后通过为 `INFRA_RECOVERED`，相同失败指纹可维持 `STABLE`；不完整或未验证的 Seal 降级为 `INCONCLUSIVE`。
- `20260718_0033` 建立 `attempt_closure_notice` 与 `unit_resolution_revision`。数据库 Trigger 强制 Seal / ClosureNotice 终态互斥，重算 ClosureNotice 21-key canonical projection 和 hash，并重算全部 CLOSED Attempt 输入、input-set hash、decisive axes、Revision chain、policy digest 与 Stability；两表均使用 `FORCE RLS`、append-only Trigger 和 SELECT / INSERT 最小权限。
- `FinalizeAttemptResultService` 在接受 Seal 并关闭 Attempt 的同一事务追加 Unit Resolution；`TaskWorkerService` 在未 Seal Attempt 关闭、重试结算、Run child reconcile 与已封印回包恢复时，同事务创建或 exact replay ClosureNotice / Resolution。任何 CLOSED Attempt 缺少事实或同时存在两种事实都会 fail-closed。
- 本切片只增加后端事实、投影与机器契约，没有增加公共 Result API、TaskResultSnapshot、Classification 或 Gate，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- 37 项 Result 定向测试覆盖 ClosureNotice 终态约束与 hash、sourceStatus / Attempt quality 精确对应、Resolution Revision 链和 Stability、Repository 序列化、应用 exact replay、Migration 约束；4 项真实 PostgreSQL 链验证 Seal → Resolution、无 Seal → ClosureNotice、Infrastructure retry 的两次 Revision、RLS 与不可删除权限。
- 干净数据库完整后端门禁 969 tests / coverage 90.13%，Ruff、严格 mypy 316 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过。存在 Projection Fact 时 `0033` downgrade 正确拒绝；独立空库完成 `0033 → 0032 → 0033` 往返。
- P7-01A 已在后续切片冻结 Task 级 UnitResolutionRevision 输入集合与 Snapshot Policy；后续 Classification / Gate 和公共查询必须绑定确定 Snapshot，不能直接暴露或临时重算底层 Fact。

## P7-01A 范围

### 已实现

- 建立 `atlas.task-result-snapshot/0.1`。Snapshot 精确绑定 Tenant / Project、CLOSED TaskRun、Manifest Hash、Manifest ordinal 顺序下每个 Unit 的 latest `UnitResolutionRevision`、ClosureNotice-compatible `inputResolutionSetHash`、固定 `aggregationPolicyVersion / digest` 与 Resolution 事实水位；semantic `snapshotHash` 排除随机 ID、Revision lineage 和写入时间，因此相同输入、策略与代码产生相同 Hash。
- 冻结 `QUALITY_FINAL` 聚合策略：`manifestCount = passed + failed + inconclusive + notEvaluated`；DataHygiene、EvidenceCompleteness、EvidenceIntegrity、ExecutionInfluence、Stability 与 OutcomeClass 每条轴都必须守恒 Manifest 分母。raw、trusted、autonomous 使用 Manifest 分母，decisive 使用 `PASSED + FAILED` 分母；全部以精确 numerator / denominator 表达，不使用浮点近似。Assisted Pass 不进入 autonomous numerator。
- `ResultProjectionService.snapshot_task` 只在 Run CLOSED、Manifest / Unit / Resolution scope 全部一致且每个 Unit 已关闭时生成 Snapshot。相同 Resolution Set + Policy exact replay 返回既有 revision；输入变化时追加 predecessor-linked revision。写入 Snapshot 后在同一事务追加 `task.snapshot_created` Outbox。
- `TaskWorkerService.finish_run` 在 `task_run.closed` 之后、事务提交之前创建 Snapshot；Snapshot 失败会回滚 Run 关闭。已关闭 Run 的 finish replay 会幂等补齐缺失 Snapshot，不能跳过 Result 真相层。
- `20260718_0034` 建立 append-only `task_result_snapshot`。Insert Guard 锁定 exact TaskRun，复核 Materialization Seal、CLOSED lifecycle、Manifest count、latest Resolution revision，并再次比对每个 Unit 当前全部 CLOSED Attempt 的 Seal / ClosureNotice 集合，防止旧 Resolution 被封存；随后重算 Resolution Set Hash、Watermark、Verdict / axis counts、四种 rate、23-key canonical JSON 与 semantic hash。表启用 `FORCE RLS`、不可变 Trigger、SELECT / INSERT 最小权限和 populated downgrade fail-closed。
- 本切片没有增加公共 Result API、Classification、Gate、`FULLY_RESOLVED` / `REEVALUATED` 生产逻辑，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- 47 项 Result / Snapshot 定向测试覆盖不可变契约、Manifest / axis 数量守恒、四类 rate、semantic hash、Assisted Pass、mixed Verdict、缺失 Resolution fail-closed、exact replay、Repository 序列化和 `0034` 静态约束；连同 Task Worker 回归共 88 项通过。
- 本切片完成时 Ruff、严格 mypy 317 files、Contracts / OpenAPI 漂移和 Python sdist / wheel 通过，无外部基础设施门禁为 879 passed / 108 skipped。后续 P7-01B0 已在真实 PostgreSQL 完成 `0034` upgrade，AttemptSeal 全链实际生成并复核 `QUALITY_FINAL` Snapshot，且完整 PostgreSQL / Temporal 门禁达到 1000 tests / coverage 90.08%；`0034` standalone populated downgrade 未单独重复，因为现存 `0035` Cleanup Fact 会按设计先阻止链式降级。
- P7-01B0 已在后续切片接入 Cleanup / Hygiene 事实；`QUALITY_FINAL → FULLY_RESOLVED` 和显式 `REEVALUATED` 仍由后续 Revision 切片负责。

## P7-01B0 范围

### 已实现

- 建立 `atlas.attempt-fixture-binding/0.1`。FixtureRun 的 `executionKind=EXECUTION` 和 `executionId=unit-attempt:<uuid>` 只允许绑定 exact UnitAttempt；Tenant / Project / TaskRun / ExecutionUnit、Environment、Blueprint、Compiled Plan 与 requestedAt 必须一致。相同内容 exact replay，不同内容冲突，一条 Attempt 和一条 FixtureRun 都只能出现一次。
- 建立 `atlas.unit-hygiene-resolution-revision/0.1`。每个 Revision gapless 覆盖 Unit 的全部 CLOSED Attempt，并冻结 FixtureRun revision / status / generation / cleanup state、Manifest hash、CREATED Resource 数量、CleanupAttempt 数量、未解决 Reconcile 数量、完整 observation hash、policy digest 与 projection watermark。
- Unit Hygiene 聚合按最严重状态保留历史：`LEAKED > CLEANUP_FAILED > PENDING > CLEANED / NOT_APPLICABLE`。因此后续重试成功不能把较早 Attempt 的泄漏改写为 CLEANED；可信 Task Attempt 明确 `NOT_REQUIRED` 时可使用 `EXPLICIT_NOT_REQUIRED`，不能伪造 Fixture 输入。
- FixtureRun 创建事务在正式 execution identity 下先写 AttemptFixtureBinding；Fixture release 进入终态时在同一事务投影 cleanup truth。Task 结算和 Result Truth 重放也会幂等补齐 Unit Hygiene Revision；投影失败会阻止对应事实事务提交。
- `20260718_0035` 建立 append-only `attempt_fixture_binding` 与 `unit_hygiene_resolution_revision`。Insert Guard 锁定 exact Attempt / Unit / Fixture 链，重算 binding 13-key canonical hash、每个 Attempt 的 19-key input hash、Manifest、Resource / CleanupAttempt / Reconcile observation hash、input-set hash、aggregate Hygiene、watermark 与 19-key semantic hash。两表启用 `FORCE RLS`、不可变 Trigger、SELECT / INSERT 最小权限和 populated downgrade fail-closed。
- 导出 `attempt-fixture-binding.schema.json` 与 `unit-hygiene-resolution-revision.schema.json`。本切片没有修改公共 API 或前端页面、组件、DOM、布局、CSS、交互，也没有提前生产 `FULLY_RESOLVED` / `REEVALUATED` Snapshot、Classification 或 Gate。

### 验证状态与下一步

- 110 项 Result / Fixture / Task / Migration 定向与回归测试通过，覆盖 binding scope / replay、cleanup observation、leak precedence、mixed CLEANED / NOT_APPLICABLE、Attempt 数量守恒、重试 Revision 链与数据库静态 guard。
- 真实 PostgreSQL 已完成 `0035` upgrade、空表 `0035 → 0034 → 0035` 往返、Python ↔ PostgreSQL canonical hash parity、伪造 Blueprint Plan 拒绝、FixtureRun → Binding → CLOSED Attempt → Unit Hygiene Revision、exact replay、最小权限以及存在 Binding / Hygiene Fact 时的 populated downgrade fail-closed。
- 完整门禁为 1000 tests / coverage 90.08%，真实 PostgreSQL / Temporal、Ruff、严格 mypy 323 files、Contracts / OpenAPI 漂移检查和 Python sdist / wheel 均通过。共享测试库超过 100 条 Start Intent 后暴露的既有租约接管测试 backlog 假设也已修正为事务内分批领取，生产投递逻辑未改变。
- P7-01B1 已在后续切片完成；下一步建议进入 P7-01B2，评估显式 `REEVALUATED` 命令与政策重算边界，之后进入 FailureClassification 和 fail-closed Gate。

## P7-01B1 范围

### 已实现

- 保留 `atlas.task-result-snapshot/0.1` 的 23-key `QUALITY_FINAL` 文档、Policy 与 semantic Hash，新增向后兼容的 `atlas.task-result-snapshot/0.2` `FULLY_RESOLVED` 形状。0.2 在同一 append-only revision 链上额外绑定 Manifest ordinal 顺序下的 `unitHygieneResolutionRevisionIds` 与 `inputHygieneResolutionSetHash`，不会重写历史 0.1 Snapshot。
- `FULLY_RESOLVED` 只在每个 Manifest Unit 的 latest Unit Hygiene 状态均属于 `CLEANED / LEAKED / NOT_APPLICABLE` 时生成；`PENDING / CLEANUP_FAILED` 保持等待。Finality 表达清理事实已确定，不等于全部清理成功，因此显式 `LEAKED` 仍可进入最终快照并继续阻断后续 Gate。
- Snapshot Verdict、EvidenceCompleteness、EvidenceIntegrity、ExecutionInfluence、Stability、OutcomeClass 与四类 pass rate 继续来自 exact Quality Resolution 集合；只有 DataHygiene 分布由 exact Hygiene Resolution 集合覆盖。Quality root、Hygiene root、两个 Policy digest、combined watermark 与 semantic Hash 均可独立复算。
- TaskWorker 在 Run close 事务中先创建或重放 `QUALITY_FINAL`，再尝试 `FULLY_RESOLVED`；FixtureWorker 在 late cleanup 终态事务中按 Task → Unit 锁顺序投影 Unit Hygiene，并重新检查 Task readiness。最后一个 Unit 清理闭合即可追加 Full Snapshot，重复 Task / Fixture 事件不会产生重复 Revision，Full 之后的 Quality replay 返回原 0.1 Fact。
- `20260718_0036` 扩展 `task_result_snapshot`，以 partial unique input index、phase-aware revision chain、terminal Hygiene coverage、current Attempt / Fixture revision freshness、双输入 root、DataHygiene overlay、25-key canonical JSON、combined watermark 与 semantic Hash 守卫 0.2 写入。存在 Full Fact 时 downgrade fail-closed；只有 0.1 Quality Fact 时可安全回到 `0035` 并恢复原 Insert Guard。
- `task-result-snapshot.schema.json` 已升级为 0.2 contract ID，并通过 conditional JSON Schema 同时表达 0.1 Quality 与 0.2 Full 的不同字段要求。本切片未增加公共 Result API、`REEVALUATED`、Classification 或 Gate，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- 领域、应用、Repository、Task Worker、Migration 静态测试已覆盖 0.1 Hash 兼容、0.2 字段守恒、terminal readiness、DataHygiene-only overlay、Quality replay after Full、Hygiene Manifest order 与 explicit projection columns。
- 真实 PostgreSQL 已完成现有数据 `0035 → 0036` 升级、Fixture truth → Quality rev1 → Full rev2 全链、Python / PostgreSQL 双输入 Hash parity、Full Fact populated downgrade fail-closed，以及独立空库 `0036 → 0035 → 0036` 往返。完整门禁为 1011 passed / coverage 90.01%，真实 PostgreSQL / Temporal、Ruff、严格 mypy 324 files、Contracts / OpenAPI 漂移与 Python sdist / wheel 全部通过。
- 下一步建议进入 P7-01B2：只通过显式命令与冻结新 Policy 追加 `REEVALUATED`，不允许策略发布自动重写历史；随后再进入 FailureClassification 与绑定明确 snapshotId 的 fail-closed Gate。

## P7-01B2 范围

### 已实现

- 建立 `atlas.task-result-reevaluation-command/0.1`。命令永久绑定 Tenant / Project、TaskRun、exact `FULLY_RESOLVED` 源 Snapshot、目标 Aggregation Policy version / digest、Actor、`clientMutationId` 与请求时间；Idempotency Key 必须与 Mutation ID 一致，相同命令稳定 replay，冲突内容 fail-closed。
- 保持 0.1 `QUALITY_FINAL` 和 0.2 `FULLY_RESOLVED` 文档与 Hash 兼容，新增 `atlas.task-result-snapshot/0.3` `REEVALUATED`。新 Revision 绑定 source Snapshot 与 command，沿用 exact Quality / Hygiene revision 集合、两个输入根、水位、数量分布和四类精确通过率，并切换到冻结的 0.3 Aggregation Policy；semantic Hash 不依赖随机 command ID。
- `ResultReevaluationService` 只接受显式内部应用命令：Run 必须为 `SEALED / CLOSED`，源必须是同 Run 的 exact Full Snapshot；同一 source + policy 只产生一个 Reevaluated Revision。策略发布、TaskWorker、FixtureWorker 与普通 Result Projection 都不会调用该服务，也不会批量改写历史。
- `20260718_0037` 建立 append-only `task_result_reevaluation_command` 并扩展 `task_result_snapshot`。数据库 Insert Guard 锁定 Run、源 Snapshot 和命令，复核 27-key canonical JSON、命令 canonical hash、source + policy 唯一性、gapless Revision、双输入根、全部分布、精确 rate、水位和 immutable lineage；两类事实均使用 `FORCE RLS`、最小 SELECT / INSERT 权限和 populated downgrade fail-closed。
- 导出 `task-result-reevaluation-command.schema.json`，并把 `task-result-snapshot.schema.json` 升级为向后兼容的 0.3 contract ID。本切片没有增加公共 HTTP / OpenAPI Result 路由、Classification 或 Gate，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- 领域、应用、Repository 与 Migration 定向测试已覆盖命令 Hash / Idempotency、exact Full source、source + policy deduplication、0.1 / 0.2 兼容、0.3 字段与 semantic Hash、Revision phase、不可变权限和数据库静态守卫。
- 真实 PostgreSQL 已完成现有数据 `0036 → 0037` 升级、显式 Full rev2 → Reevaluated rev3 全链、exact replay、命令事实与最小权限验证；存在 Command / Reevaluated Fact 时 populated downgrade 正确拒绝，独立空库完成 `0037 → 0036 → 0037` 往返。
- 完整门禁为 1021 passed / coverage 90.11%，真实 PostgreSQL / Temporal、Ruff、严格 mypy 327 files、Contracts / OpenAPI 漂移检查和 Python sdist / wheel 均通过。
- 下一步建议进入 FailureClassification：分类事实必须绑定明确的 `snapshotId`；随后实现同样绑定明确 Snapshot 和 Classification Revision 的 fail-closed Gate。公共 Result 查询继续等读取模型稳定后再开放。

## P7-02A 范围

### 已实现

- 建立 `atlas.failure-cluster-revision/0.1`。Cluster 永久绑定一个 exact `TaskResultSnapshot`，只包含需要诊断的 UnitResolution；首版 Policy 对完整 Snapshot 重算同一 `FailureSignal` 的 manifest-ordered 全量集合，禁止拆分、漏项、混入 clean trusted pass 或依赖可变查询结果。
- 建立 `atlas.failure-classification-revision/0.1` 与稳定 `FailureDomain` taxonomy。首个 Revision 只能由冻结规则产生；低证据的 Business / Automation / User 失败保持 `UNKNOWN`，不会把不确定性伪装成产品缺陷，也不会修改原始 Verdict、Snapshot 或 Gate。
- Classification 使用固定分母 `10000` 的 exact confidence、immutable typed Evidence Ref、supporting / contradicting evidence、显式 evidence gap code、author kind 与 judgment state。Contract 为 AI proposal 保留独立且禁止隐藏推理的作者形状；当前持久化写路径只开放 deterministic rule proposal 与人工 review，不接入模型。
- 人工复核只允许 `PROJECT_ADMIN / CASE_REVIEWER / ORG_ADMIN`，要求可信 Actor、`Idempotency-Key == clientMutationId` 与 expected Revision。`HUMAN_CONFIRMED` 不能静默改变归因内容；`HUMAN_REJECTED` 必须回到 `UNKNOWN`、零置信度并提供 contradiction evidence；所有修改均追加新 Revision，并写 Audit / Outbox。
- `20260718_0038` 建立 append-only `failure_cluster_revision` 与 `failure_classification_revision`。数据库重新验证 Snapshot scope、完整 manifest group、规则优先级、Evidence 所属事实、canonical 19 / 26-key JSON、semantic hash、revision chain、RLS 与最小 `SELECT / INSERT` 权限；Snapshot 首次物化和 Classification revision chain 使用相互隔离的 transaction advisory lock。
- 导出 `failure-cluster-revision.schema.json` 与 `failure-classification-revision.schema.json`。本切片没有新增公共 HTTP / OpenAPI Result 路由，没有自动触发分类，也没有修改前端页面、组件、DOM、布局、CSS 或交互。

### 验证状态与下一步

- Domain、Application、Repository 与 Migration 测试已覆盖 conservative signal precedence、clean pass exclusion、低证据 `UNKNOWN`、hash 防篡改、canonical evidence、Cluster replay、人工 review / replay、RBAC、advisory lock 与数据库静态守卫。
- 真实 PostgreSQL 已完成 `0037 → 0038`、REEVALUATED Snapshot → Cluster → Rule Classification → Human Confirmed Revision 全链，并验证表级不可 UPDATE / DELETE；另在全新临时数据库从零升级到 `0038`。完整门禁为 1032 passed / coverage 90.08%，Ruff、严格 mypy 332 files、Contracts / OpenAPI 漂移检查和 Python sdist / wheel 均通过。
- 下一步建议进入 P7-02B fail-closed TaskGateDecision：必须绑定明确的 `resultSnapshotId + failureClassificationRevisionIds + classificationSetHash`，并由数据库证明覆盖该 Snapshot 的完整 Cluster 集合；只消费人工确认或政策明确允许的 judgment state，缺失 / 过期 / 低证据 Classification 默认阻断。之后再稳定 Result Center 读取投影和公共 API，并严格映射既有前端原型数据槽位。

## P7-02B / P7-03 范围

### 已实现

- `atlas.task-gate-decision/0.1` 与 `20260718_0039` 冻结 exact Result Snapshot、完整 current Cluster / latest Classification 集合和 `classificationSetHash`。缺失、低证据、未决 Hygiene、无效 Evidence、人工影响或不稳定执行均默认 `INCONCLUSIVE`；明确失败/泄漏才 `REJECTED`，全部严格通过才 `ACCEPTED`。
- 开放 snapshot-explicit Result / Unit Resolution / Cluster 查询、append-only Classification review 与 Gate evaluation。读接口提供强 ETag、304 和 Snapshot/Watermark headers；写接口保持 RBAC、幂等、Audit / Outbox 与数据库 canonical guard。
- 既有 Task / Results 原型槽位映射真实 TaskRun、Snapshot、Gate、Cluster 与 Classification；无 Session / 数据时保留演示回退。未修改 DOM、布局、样式、className 或既有交互。

## P8 V1 范围

### 已实现

- 建立平台签发的 trusted pass、autonomous trusted pass 与 method health 三项固定 Unit-grain MetricDefinition。归窗使用 immutable TaskRun `finalizedAt` 作为 `qualityFinalizedAt`；聚合只重新求和 numerator / denominator，0 分母返回 `NO_DATA`，不平均百分比。
- `InsightBrief` 输出 7 / 30 / 90 天 UTC current 与相邻 baseline、signed basis-point delta、最多四个 TaskPlan terrain 和 latest non-accepted Gate 风险信号。只有 `FULLY_RESOLVED / REEVALUATED` Result Snapshot 可进入 source cut。
- `InsightDatasetCut` 固定 ordered Result Snapshot 与 Gate Decision IDs / hashes、sourceSetDigest、projection watermark、queryHash、authScopeHash 与 asOf。`atlas.insight-snapshot/0.1` pin 后不可变，semantic hash 不依赖存储 identity。
- `20260718_0040` 建立 append-only `insight_snapshot`。数据库在固定 asOf 与 qualityFinalizedAt 窗口内重新选择每个 TaskRun 的 latest stable Result、latest exact Gate，复核 source set、watermark、文档镜像与 canonical snapshot hash；启用 FORCE RLS、SELECT / INSERT 最小权限和 populated downgrade fail-closed。
- 开放 brief preview、Snapshot pin 与 exact read，提供强 ETag、DatasetCut / query / watermark headers和永久 mutation replay。`insight-snapshot.schema.json`、OpenAPI 与前端类型同步导出。
- 既有 Insights terrain、stable pass、method health 与 risk task 槽位只替换真实数据；DOM、布局、样式、className 和交互结构保持原型不变。

### 验证状态与下一步

- 15 项 P8 定向测试覆盖 ratio-of-sums、NO_DATA / LOW_SAMPLE、permutation invariance、current/baseline、source / Gate cut、hash、防篡改、API/ETag、Repository 与 Migration 静态守卫。
- 真实 PostgreSQL 完整 Task → Result → Classification → Gate → Insight preview / pin / permanent replay 链通过，并验证跨 Tenant RLS、不可 UPDATE / DELETE 和 `0040 → 0039 → 0040` 空表往返。
- 完整门禁 1063 passed / coverage 90.16%，Ruff、严格 mypy 352 files、Contracts / OpenAPI 漂移、Python sdist / wheel、前端 API 漂移 / TypeScript / production build 全部通过。

## P10-01 范围

### 已实现

- 冻结 `frontend/atlas-ai-testops-prototype` 作为视觉与交互权威；新增独立 `frontend/atlas-testops-web`，不在原型巨型页面上继续叠加生产逻辑。
- 建立 Feature First 分层：Auth、Identity、Fixture、Case、Task、Live、Result、Insight 和 Space 均拆分为 Service / Query / Mapper / ViewModel / UI；页面不直接 `fetch`，Server State、Client State 与 URL State 分离。
- 使用 `contracts/openapi.json` 生成严格 TypeScript API 类型，并建立契约漂移门禁。浏览器只访问 `/api/atlas/v1/*` 同源 BFF；BFF 只允许 V1、剥离 forwarding/hop-by-hop header、限制 10 MiB 请求体、贯穿 Request ID，并禁止 Session 数据被中间缓存。
- Password Login、HttpOnly Session、Logout、Workspace Boundary 与角色级操作权限使用真实 Auth API；没有 Session 时跳转登录，Session/网络错误不会被误报成无权限。
- Space、Identity、Fixture、Case、Task、Live、Result 与 Insight 全部读取真实投影；后端没有公开契约的能力明确禁用，不使用 Mock、手填无来源 UUID、假百分比或静默演示回退。
- Case Workbench 完成 WorkflowPatch `validate → apply` 闭环，复用同一 Patch ID、Idempotency-Key 与 semantic Revision；节点拖拽使用独立 Layout Revision，不污染 Debug/发布语义证据。
- Task Center 使用 exact TaskPlanVersion 启动 TaskRun，并按冻结 `infra-retry` digest 校验 canonical 请求；TaskRun 与 Live Control 均使用 If-Match、Idempotency 与 Control Epoch/Fencing。
- Result Center 绑定 exact ResultSnapshot、FailureCluster 与 Classification Revision；Insight 明确区分 `NO_DATA` 与 0，并展示 DatasetCut 来源。
- 建立 Design Tokens、CSS Modules、统一 Loading/Error/Empty/Dialog、Root/Global Error Boundary、Query/Mutation 错误事件和生产 CSP/HSTS/Cross-Origin/Permissions 安全头。
- 测试覆盖 Mapper/Digest/WorkflowPatch Builder Unit Test、Permission Guard Component Test、桌面/移动 Login/Task/Live E2E，以及固定 Task Center 的双端 Visual Regression。

### 验证状态与下一步

- TypeScript strict、ESLint 与 10 个测试文件 / 14 项 Unit+Component Test 已通过。
- Playwright 桌面与移动端 8 项 E2E/Visual Regression 已通过，并在浏览器回归中修复无效 URL `planId` 会触发错误后端请求的问题。
- 最终生产构建、产物 Secret 清理、安全响应头、BFF Problem Details/Request ID 一致性与 API Contract 检查已通过；发布使用源码 commit 绑定 Sites Version 和 Deployment，部署事实以发布平台记录为准。
- Identity 聚合创建、AI WorkflowPatch 生成、Execution/Profile Catalog、Evidence Listing/Streaming/Export、Defect Integration、Feishu OAuth、全局搜索和通知仍缺少公共后端契约；前端保持明确禁用，不能将其描述为已完成。

## 当前风险与外部输入

- 首个真实 SaaS Connector、`PasswordLoginFlow` 和测试账号来源尚未提供；当前交付 Mock Provider、Generic Password Adapter 与可注入的 Playwright Target。
- 独立 Auth Session Worker 与加密 Vault 端口已经落地；生产 Secret Provider、KMS-backed Vault 和生产 Object Store 配置尚未提供，缺失时 Password Session fail-closed。
- 周期性 AccountHealthWorkflow / Identity Reconciler / Tenant Session Janitor 尚未调度；当前已覆盖手工触发、Temporal Workflow 契约、运行时失败触发与单批 Janitor。
- Feishu PlatformPrincipal OAuth 尚未提供 Client Secret、Redirect URI 与权限范围；当前入口不会模拟成功。
- 生产对象存储和 Secret Manager 尚未指定；代码只依赖抽象接口，本地采用 S3-compatible 与不可逆的 Secret 引用。
- 试点项目、黄金用例和真实业务 API 契约尚未提供；P0-P1 不依赖这些输入，P2 之后需要逐步补齐。
- P3-03 已完成取消后补偿、Reconcile、Cleanup Retry / Sweeper、孤儿扫描与 Cleanup Evidence；生产环境仍需按 Tenant 配置 Temporal Schedule 和真实 Provider，缺失时继续 fail-closed。
- P5-00B1 至 P5-00E7 已建立正式 Profile、Seal / CAS、durable Start、100,000-Unit 分区物化与分页 Root / Attempt 编排、查询、immutable Ticket、可靠控制 / retry / rerun、TaskPlan Catalog / immutable publication、统一 Trigger、签名 HTTPS production `TaskUnitExecutionPort`、数据库权威 Temporal Schedule 与 signed Gate Callback。P6 已提供完整 Attempt Fact、Unit Resolution 和 UnitAttempt-scoped Live Control，P7 已完成三阶段 TaskResultSnapshot、FailureCluster / Classification、TaskGateDecision 与公共 Result 查询，P8 V1 已完成 comparable Brief / DatasetCut / immutable Snapshot；部署端真实 SaaS executor 与 Callback Receiver 仍需外部输入。只有数据库中存在 exact Seal Fact 时才允许 Task Workflow 表达 `PASSED`，ClosureNotice 只能使 Resolution 得到 `INCONCLUSIVE / NOT_EVALUATED`。
- P9 本地参考全部通过，但 100×100 Lease 基准出现 5,472 次短暂 `POOL_EXHAUSTED` 背压；均在有界重试内收敛且 Slot 冲突为 0。生产需要按真实连接池、账号容量和突发模型设置 Admission / Retry，不能照搬本地 10 ms 重试。
- P9 总状态为 `CONDITIONAL_PASS`。月度控制面可用性、生产 Schedule / Live Telemetry、人工分类准确率、真实影子迭代和灾备演练没有外部证据，必须按 Runbook 完成后才能声明 Production Ready。
- P6-01 已实现独立无数据库 Browser Worker、Permit + HMAC 内部网关、Temporal Activity、加密 Context Restore、严格报告链与受限 Playwright Adapter；P6-02A Evidence Writer / 受控读取、P6-02B1 DebugRun Live Snapshot / SSE 与 P6-02B2 UnitAttempt LiveSession / ControlLease / Epoch / Fence / Human Takeover / ActionGrant 已完成。真实 SaaS Operation / Route Registry、生产 Bucket Object Lock / Versioning、容器网络沙箱、Envelope Key Ring、公共 Start 自动 Preparation / Bind / Dispatch 和 Multi-actor 尚未实现，缺少对应能力时继续 fail-closed。
- 应用内浏览器已完成 1440×900 原型对照，Playwright 已固化桌面/移动端 Visual Regression；后续视觉改动必须继续以冻结原型和基线截图为准。
