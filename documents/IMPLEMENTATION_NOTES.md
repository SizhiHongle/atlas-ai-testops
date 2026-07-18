# Atlas AI 测试平台实施要点

更新时间：2026-07-16

## 文档优先级

发生冲突时按以下优先级处理，并在同一切片修正低优先级材料：

1. 已提交且通过漂移检查的版本化 JSON Schema / OpenAPI。
2. 生成契约的 Python 领域模型。
3. Accepted ADR 与 `DOMAIN_GLOSSARY.md`。
4. Word 技术设计文档。
5. 原型页面中的演示业务数据。

界面结构、布局、视觉样式和既有交互以当前前端原型为唯一权威。没有新的设计依据时，不新增页面、不重排 DOM、不改写 `globals.css`；后端能力只绑定到原型已经存在的入口、状态和数据槽位。

## 平台身份与测试账号边界

- `PlatformPrincipal` 由 `PlatformUser`、`PlatformMembership` 与 `PlatformSession` 实现，表示登录 Atlas 的人员主体。
- `TestAccount` 表示被测系统中的业务账号，只能进入 P2 的 AccountPool / Lease / CredentialBinding 链路。
- PlatformPrincipal 密码不得复用为 TestAccount Credential，两个边界不得建立隐式外键或共享 Session。
- Platform Session 使用 Opaque Token；客户端只持有 HttpOnly Cookie，数据库只保存 SHA-256 Token Hash。
- Session 每次解析都实时复核 User、Tenant、Project 与 Membership；授权撤销后必须先持久化 Session 撤销，再返回未认证。
- Password 使用 Argon2id；内存密集计算在线程池执行，并设置进程级并发上限。

## 测试账号目录边界

- `TestRole` 是被测业务角色；`AccountPool` 在一个 Environment 内绑定一个 TestRole，二者都不授予 Atlas PlatformRole。
- `TestAccount` 的 lifecycle、health、operational、sync、cooldown、credential 和 slot 状态必须正交保存；`available` 只能实时计算。
- `CredentialBinding` 只保存不可兑换的 SecretRef、版本、用途和有效期；公共 API、Audit、Outbox、日志和 Identity Wallet 均不得出现 SecretRef 或秘密值。
- MVP AccountPool 只支持 exclusive slot；一个 TestAccount 对应一个 AccountSlot，为 P2-02 的部分唯一索引和 Fencing 提供稳定资源边界。
- Identity Wallet 继续使用原型固定四卡；API 数据只覆盖既有角色、账号、权限、环境、容量和健康文本槽位，不改变布局和 CSS。

## 账号租约与 Fencing 边界

- `AccountLease` 是 PostgreSQL 权威事实；同一 AccountSlot 只允许一个 Active Lease，终态 Lease 不允许再次修改。
- Worker 只接收 Opaque Account Handle、Lease ID、Fencing Token、Heartbeat 周期和 Expiry，不接收 Account ID、Slot ID、登录提示或 SecretRef。
- 每次 Acquire 和管理撤销都会推进 Account `leaseEpoch`；Heartbeat 与 Release 必须同时匹配 Lease Token 和账号最新 Epoch。
- Heartbeat 不能越过 Execution Deadline；TTL 终结必须先提交 `EXPIRED`、结构化原因和账号复核状态，再向调用方返回错误。
- Account 隔离、挂起、退休以及 Pool / Role 禁用必须与 Lease 撤销、Fence 推进、Audit 和 Outbox 位于同一事务。
- Acquire 先共享锁定可调度 Role / Pool / Environment，再以 `FOR UPDATE SKIP LOCKED` 领取 Account / Slot，并在持锁后使用新语句快照复核 Active Lease。
- P2 内部 Lease API 暂时复用 Platform Actor 与 RBAC；P5 引入 Execution Token 后，Worker 身份必须切换到短期、最小权限的执行主体。

## Secret Grant 与 Provider Adapter 边界

- Environment 只保存规范化的精确 HTTP Origin；Scheme、Host 和 IPv6 统一规范化，默认端口被移除，Path、Query、Fragment、User Info、歧义 IP 与非法端口被拒绝。Production 默认只允许 HTTPS，且当前禁止签发 Grant。
- `SecretGrant` 是短 TTL、最多一次兑换的受限授权，不是 Credential。原始 `grantRef` 只在签发响应存在，数据库只保存 SHA-256 Hash；每个 Lease / Credential / Purpose / Worker 同时最多一个 `ISSUED` Grant。
- Broker 必须同时校验 Tenant、Project、Environment、Lease、最新 `FencingToken`、Worker Identity、Credential Purpose、Credential 状态和 Allowed Origin；任何一项变化都使旧 Grant 失效。
- Redemption 在 PostgreSQL 中先原子提交 `REDEEMED`，再在事务外调用 Adapter；外部 I/O 不持有数据库锁。Adapter 失败不会恢复 Grant，保持 at-most-once 语义并要求调用方重新签发。
- `SecretProvider` 只提供闭包消费；Broker 把 SecretRef / SecretVersion 封装进私有 `PasswordSecretScope`，`AdapterContext` 只暴露 `with_password_secret(...)`。Adapter 不接收 SecretRef、SecretVersion、Secret Provider 或返回秘密的 `getSecret()`。
- Lease 终结、Environment 禁用、Credential 失效与 TTL Reaper 必须撤销所有未消费 Grant；`REDEEMED / REVOKED / EXPIRED` 均为数据库不可变终态。
- 签发、拒绝、兑换和 Adapter 结果必须写入 Audit 与 Outbox，但 Payload 只能包含 Lease、Fence、Purpose、状态、稳定错误码和低基数 Adapter 元数据。
- HTTP 只开放签发端点，且响应使用 `Cache-Control: no-store`；Redemption 属于受信 Auth Worker 的进程内边界，不开放读取密码、Cookie、Token 或任意 Provider 请求的 API。
- 前端只消费生成的 OpenAPI TypeScript 类型；Secret Grant 不新增可视页面，也不改变现有原型 DOM、布局或 CSS。

## ConnectorInstallation 与 Capability Snapshot 边界

- `ConnectorInstallation` 是 TestAccount 到具体 Provider Connection 的权威绑定，不是可由 Worker 或 Agent 临时提供的 Endpoint。`adapterKey` 与作用域创建后不可变。
- `configurationRef` 是控制面内部的不透明定位符；公共投影只返回 `configurationState`，Audit、Outbox、Problem Details、OpenAPI Response 和前端状态都不得回显配置引用。
- Adapter 只能由进程启动时构造的 `AdapterRegistry` 显式解析。禁止根据数据库或 HTTP 字段执行动态 import、任意 Module、脚本、URL、Header 或厂商 SDK 方法。
- Connector 验证固定拆成三个阶段：短事务读取 Revision 与配置快照、事务外执行 Probe / Negotiate、短事务以原 Revision CAS 写入 Health 与 Capability Snapshot。外部 I/O 不得持有连接或行锁。
- Connector 的实际能力必须以结构化 `{name, version, mode}` 保存；Manifest 只是代码理论能力，账号、Grant 和 Worker 只能依赖最近一次成功协商后的实际快照。
- Production Environment 当前只允许 `OBSERVE_ONLY`；Connection Origin 必须是 Environment Origin 子集。缩减 Environment Origin 前必须先消除 Connector 依赖。
- 新 TestAccount 必须绑定同一 Tenant / Project / Environment 的 ACTIVE Connector；认证方式必须被实际能力覆盖。迁移前 Legacy Account 可保留空绑定，但不可继续调度。
- 安全锁顺序为 Environment → Connector → Account / Lease → Credential → Grant。Lease Acquire 先稳定顺序共享锁定候选 Connector，避免禁用与租约创建交错后留下活动租约。
- Connector 离开 ACTIVE、配置变化或账号改绑必须撤销活动 Lease 并推进 Fence；数据库 Trigger 同时撤销未消费 Grant。终态 Grant 不允许恢复。
- Connector 管理暂不新增前端页面；只同步 OpenAPI 生成类型。后续只有原型出现相应入口或获得新设计稿时，才可绑定 UI，不得自行改造现有原型。

## TestAccount Health Verification 边界

- 未验证账号保持 `UNKNOWN / VERIFYING`，不得仅凭管理字段进入 `HEALTHY`。数据库要求 `HEALTHY` 同时具备 Connector 作用域 `IdentityFingerprint`、最近检查时间、最近成功时间和清零的连续失败计数。
- 手工验证固定拆成三个阶段：按 Environment → Connector → Account 顺序短事务锁定快照、事务外通过 Secret 闭包执行 Adapter 登录、短事务以 Account / Connector / Credential Revision CAS 落地结果。探针期间不持有数据库连接或锁。
- Provider Subject 只用于当前调用中的身份比较，并保存为 `sha256(connectorId + subject)` 形式的不可逆指纹；原始 Subject、用户名、密码、SecretRef、Provider 请求和响应不得进入健康事实、事件、日志或公共投影。
- 成功结果要求登录、身份指纹和 `roleKey` 同时匹配。身份不一致、角色漂移、账号锁定和人工处置要求立即 `QUARANTINED`；可归因于账号的连续失败达到 Pool 阈值后隔离。
- Rate Limit、Provider 不可用、Network Timeout、Secret 不可用与内部 Adapter 故障属于基础设施失败，不增加账号失败计数；账号仍保持不可调度，并通过稳定错误码与 `retryable` 指示恢复策略。
- Credential Broker 运行时登录不能信任历史健康投影，必须再次比较身份指纹和角色。失败时原子撤销 Lease、推进 Fence、追加 `AccountHealthCheck` / `AccountStateTransition`、Audit 与 Outbox。
- Connector 离开 ACTIVE 或安全配置变化时，全部关联账号回退到待验证状态；改绑到另一个 Connector 时清除旧作用域指纹。任何自动失效不得解除人工 `QUARANTINED`，只有显式 Restore 才能解除。
- `AccountHealthCheck` 和 `AccountStateTransition` 只保存低基数分类、安全摘要和前后状态；终态检查与状态事实不可修改。当前原型没有管理入口，后端只生成 API 类型，不新增或改写前端页面。

## Auth Session Worker 与 SessionArtifact 边界

- API 进程只依赖 `AuthSessionDispatcher`，不得加载 Playwright Runtime、解密 Key、Storage State 或 Secret Provider；自动登录只在独立 `atlas-auth-session` Temporal Task Queue 的 Auth Session Worker 执行。
- `SessionArtifact` 是被测系统浏览器登录状态的加密生命周期元数据，不是 Atlas `PlatformSession`，也不是 P6 的实时 `BrowserSession`。公共响应只返回 `BrowserContextRef`，不得返回 ObjectRef、Digest、Key Version、Cookie、Token 或 Storage State。
- 一个 Active Lease 同时最多一个 `CREATING / READY` Artifact。每次 Ensure 必须复核 Tenant、Project、Environment、Allowed Origin、Lease Fence、Worker Identity、Connector Capability、Account Health 和 Credential Revision。
- 自动登录固定拆为三个阶段：短事务预留 Artifact 并原子消费一次性 Secret Grant；事务外通过 Secret 闭包执行 Provider / Playwright 登录并加密上传；短事务按 Lease、Account、Connector 与 Credential Revision CAS 发布 `READY`。
- Playwright 共享 Browser Process 以控制启动成本，但每次认证必须新建非持久化 `BrowserContext`；认证路径禁止 Video、Trace、Screenshot 与 Download 自动落盘，Context 在导出 Storage State 后立即关闭。
- Storage State 使用 AES-256-GCM；AAD 绑定 Artifact、Tenant、Project、Environment、Lease / Fence、Account、Connector、Credential、Origin 和 Format Version。PostgreSQL 只保存密文 ObjectRef、SHA-256 Digest、Size 与 Key Version，不保存 Encryption Key 或明文。
- 本地 S3-compatible Vault 可以使用进程注入的静态 AES Key；Staging / Production 禁止静态 Key 与自动建 Bucket，必须注入 KMS-backed `SessionArtifactVault`。API Deployment 不得获得这些 Worker-only 环境变量。
- OIDC、SAML、TOTP、Manual Bootstrap 和 Provider Challenge 当前返回有界 `AuthActionTicket`，不得把人工步骤伪装为自动登录成功；Ticket 绑定 Lease / Fence / Worker / Origin，并随依赖失效自动取消。
- Lease、Account、Credential 或 Connector 失效必须在同一数据库事务触发 Artifact 撤销；Session Janitor 先短事务 Claim，事务外幂等删除密文，再短事务写入 `DESTROYED`。对象删除失败不能误报成功，并可在 Claim TTL 后重试。
- Auth Session Audit / Outbox 只包含 Lease、Fence、状态、Expiry 和低基数失败分类；ObjectRef、BrowserContextRef 之外的存储元数据、Provider Subject、SecretRef 与任何秘密不得进入事件。
- 当前前端原型没有 Auth Session 管理入口；本切片只生成 OpenAPI TypeScript 类型，不新增页面、不重排 DOM、不修改布局、CSS 或既有交互。

## DataAtom、DataBlueprint 与发布门禁边界

- `DataAtomDefinition` / `DataBlueprintDefinition` 是可修改目录实体，名称、摘要和归档状态通过 Revision CAS 更新；`DataAtomVersion` / `DataBlueprintVersion` 是版本化协议，进入 `PUBLISHED` 或 `DEPRECATED` 后由数据库保证不可修改且不可删除。
- `DataAtom` 只描述数据依赖和部署登记的 `ConnectorOperationRef`。请求、数据库和 Worker 协议均不得携带动态 Module、Callable、URL、Header、Shell、SQL、JavaScript 或任意代码。
- CREATE Atom 必须同时声明 Resource Descriptor、Cleanup Operation 与 Reconcile Operation；只创建数据而没有可追踪资源和补偿协议的 Atom 不能保存为有效版本。
- 当前协议拒绝 password、secret、cookie、token、storage-state 等秘密语义类型，也拒绝 Production Environment。Fixture 需要秘密时必须经由 P2 的 Lease / SecretGrant / SessionArtifact 边界，不得把秘密编码进 Literal、Port 或 Manifest。
- `DataBlueprint` 只能引用同一 Project 的 exact `DataAtomVersion`。静态 Compiler 验证 Node / Port / Edge、JSON Schema Literal、必填输入、SourceRef、Semantic Type、Classification 单向流、DAG 与 Export，不在编译时执行 Connector 或访问外部系统。
- 编译结果按稳定 Node ID 生成确定性并行层级，Cleanup 使用逆拓扑顺序；`CompiledFixturePlan` Digest 覆盖 Blueprint、Atom Version 和完整执行计划，任何引用或输入变化都会产生新 Digest。
- 发布固定要求 Static Validation、Runtime Validation 与 Cleanup Validation 三类独立 PASSED 证据并绑定当前 Version Revision；Blueprint 还必须有当前 Revision 的 Compiled Plan。Runtime Evidence 来自成功运行，Cleanup Evidence 只来自正常 `RELEASED` 且真实清理完成的 Validation Run；失败、取消、泄漏或 Revision 变化时系统保持 fail-closed，不允许用运行成功冒充可清理。
- Audit / Outbox 只记录资产 ID、Version、Revision、状态、Digest 与低基数结果，不复制 Atom / Blueprint Contract 正文，避免把 Literal 或未来敏感元数据扩散到事件面。
- 前端只把真实 Catalog 投影到既有两个 DataAtom 卡片和一个 Blueprint 资产槽位；目录为空或尚无可展示 Version 时保留原型占位，不新增卡片、不重排 DOM、不修改 CSS 或既有交互。

## FixtureRun 与 Resource Ledger 边界

- API 启动 FixtureRun 时必须冻结 exact Blueprint Version、Compiled Plan / Digest、Run Input、Environment、Actor Slot、Account Lease 与 Fencing Token；`VALIDATION` 只能运行 `VALIDATED` 资产并产出 Runtime Evidence，`EXECUTION` 只能运行已经通过完整发布门禁的 `PUBLISHED` 资产。同一 Lease 只能绑定一个 FixtureRun，API 与 Worker 都必须确认其 TTL 覆盖 `executionDeadline + fixture_cleanup_grace`。PostgreSQL 是运行、Attempt、Manifest、Evidence 和资源状态的权威，Temporal History 不是业务数据库。
- Provider Operation 只能由独立 Fixture Worker 通过部署时 exact registry 执行；资产、HTTP 请求和持久化数据不得注入动态 URL、Module、Callable、Script、Header、Shell 或 SQL。
- 外部 I/O 前必须先持久化 `DataNodeAttempt=RUNNING`。无法证明 Provider 未生效的 Transport / Decode / Replay 异常必须进入 `OUTCOME_UNCERTAIN`，不得盲重试 CREATE；显式 Reconcile 的 `FOUND` 恢复输出与资源，`ABSENT` 只开放一次有界 CREATE 重试，`INCONCLUSIVE` 按配置退避，耗尽则隔离为泄漏。
- Resource Ledger 必须先于 Postcondition 保存；只有 `CREATED` Ownership 可进入自动 Cleanup，`ADOPTED / LEASED / SHARED` 即使具有资源引用也不得被平台自动删除。
- 正常 Release、节点失败和取消都按 Compiled Plan 的逆拓扑顺序清理已创建资源并释放绑定 Lease；Temporal Workflow 在业务信号和原生 Cancellation 下都通过 `finally` 进入补偿。Cleanup 前再次复核实时 Lease / Connector / Fence，旧 Fence、过期或已撤销 Lease 不得继续调用 Provider，而是把资源标记为 `LEAKED`。Transient Failure 通过 Generation Attempt、显式 Retry 或 Tenant Sweeper 继续处理，Sweeper 同时恢复 stale claim 并扫描到期孤儿；Permanent Failure 或重试耗尽保持隔离且不产生 Cleanup Evidence。
- FixtureManifest 只包含 Blueprint 显式 Export 的非敏感值，不复制全部 Node Output、秘密、Connector 配置引用或 Provider 原始响应。当前前端只更新生成 API 类型，不修改原型页面、DOM、布局、CSS 或交互。

## ExecutionContract、Oracle 与 EvidenceManifest 边界

- DebugRun 只能从 `CREATED` 绑定一次不可变 ExecutionContract；合约必须与冻结 Test IR / Plan、`READY EXECUTION` FixtureRun / FixtureManifest、Role Revision、AccountLease / Fence、READY SessionArtifact 和 execution deadline 精确一致。
- FixtureRun 与 AccountLease 的 `executionId` 都必须是 `debug-run:{debugRunId}`。Session、Lease 与 Fixture deadline 必须覆盖整个 DebugRun，Actor slot 必须完整且与 Test IR 一一对应。
- Browser Agent 只提交 Assertion observation 与 Artifact metadata，不提交 Case outcome。AssertionResult 必须匹配冻结 Assertion ID、Node、Strength、Evaluator Version 和 expected program digest。
- Oracle 推导规则固定为：任一 HARD `FAILED` 得到 `FAILED`；缺 HARD 结果、HARD `INCONCLUSIVE`、证据不完整或 integrity 非 `VERIFIED` 得到 `INCONCLUSIVE`；只有完整证据下所有 HARD 均 `PASSED` 才得到 `PASSED`。
- Assertion observation、Artifact capture 与 Evidence finalization 必须位于 ExecutionContract 时间窗内。EvidenceManifest 不携带对象存储地址，只保存安全 Artifact 摘要、事件链头和可验证 Digest。
- P6 Runtime 事实表启用强制 RLS、Scope FK、不可变 Trigger 和 `SELECT/INSERT` 最小权限；PostgreSQL 再次推导 completeness、integrity 与 outcome。CaseVersion 发布必须加载实际 Manifest 复核，不能只相信 DebugRun 引用。
- P6-01 的独立 Browser Worker 已通过受信内部协议读取执行包、推进状态、追加报告和终结证据；没有公共 Runtime 完成接口，Worker 不能直接访问主数据库。
- P6-02A 的 Evidence Manifest 公共读取只返回不可变安全投影，不暴露 ObjectRef。Artifact 字节读取要求普通 Platform Session 与独立 `Atlas-Evidence` Authorization Header，Grant 只保存 Token Hash，并精确绑定 Tenant / Project / Run / Contract / Artifact / Actor / Session / Purpose / TTL / Max Reads；Token 不得进入 Query、Audit、Outbox 或持久化明文。
- Read Grant 的兑换只在短事务内原子推进 Read Count 并写 Audit；事务关闭后才访问 Object Store。API 必须先完整缓冲有界对象，再将字节数与不可变 Receipt 的 SHA-256 全量比对，任何缺失、截断、替换或超限都不得产生部分响应。
- AttemptSeal 归属于正式 UnitAttempt；P6-03A 已以独立不可变协议落地，并精确绑定 P5-00A 的正式宿主，不能复用 DebugRun EvidenceManifest。

## DebugRun Live B1 边界

- P6-02B1 是 `DebugRun` 作用域的只读安全观察流，不是正式 `UnitAttempt` 现场，也不创建 `LiveSession`。公共边界固定为 `GET /v1/debug-runs/{runId}/live` 与 `GET /v1/debug-runs/{runId}/events/stream`，复用 Platform Session 和 Project 可见性；不可见与不存在必须返回同形 404。
- 首次订阅必须用一条无行锁 SQL 在同一 MVCC Snapshot 中读取轻量 `DebugLiveRunProjection`、最新事件和事件高水位，先发 `debug_run.live.snapshot`，再从该高水位等待增量。SQL 只构造 Live 所需的 Run ID、Project / Case / Environment Scope、Lifecycle / Outcome / Snapshot Status、Revision 与时间字段，不查询或反序列化完整 Test IR、PlanTemplate、Failure Detail 等大快照。不能先查完整 Run、再用另一事务猜测 Cursor，也不能把 Snapshot 自身当作新业务事实写回事件表。
- Cursor 使用 `atlas.debug-live-cursor/0.1` 的 canonical、无填充 Base64URL JSON，精确绑定 `debugRunId + afterSeq`。客户端只通过 SSE `Last-Event-ID` 恢复；损坏、超长、跨 Run 或超前 Cursor 必须在 HTTP headers 开始前返回 `LIVE_CURSOR_INVALID`。业务事件的 SSE `id` 使用其 Opaque Cursor；Heartbeat 是无 `id` 的 comment，绝不推进恢复位置。
- `debug_run_event` 是唯一事实源。每批 `seq > afterSeq ORDER BY seq` 查询使用独立有界短事务，事务关闭后才把 Event yield 给网络；Poll Sleep、Heartbeat、客户端背压和连接等待均不得持有 PostgreSQL 连接或行锁。`DebugRun=TERMINATED` 只表示执行生命周期结束，不封存 `debug_run_event`；Draft 后续语义变化仍会追加 `debug_run.snapshot_outdated`，其事件 Lifecycle 仍可为 `TERMINATED`。Stream 必须 replay 到当前 head，再持续 Poll，直到客户端断开或事件生成预算耗尽。
- Live Event 必须从 event-type allowlist 重新构造，不能原样转发 JSON Payload。取消原因、Report / Chain Digest、ObjectRef、Authorization、Password、输入 Value 和未知字段不得进入 Snapshot 或 SSE；只允许 UI 所需的低风险 Run 状态、Action / Observation / Policy / Receipt / Assertion / Artifact 摘要。
- 每个 API 进程使用有界 `DebugLiveStreamLimiter`；容量耗尽立即返回 429 + `Retry-After`，不能排队占用数据库或 Worker 资源。Poll Interval、Heartbeat、Batch Size、最大连接时长和 Observer 上限必须使用有界部署配置。
- `maximum_connection_seconds` 定义 Service 的事件生成预算。Route 内 `_DebugLiveStreamingResponse` 使用该 maximum 加固定 1.0 秒 Close Grace 约束生成与关闭路径，并在 `finally` 中关闭 Source、释放 Observer Slot；最后安装的 pure-ASGI `DebugLiveStreamSendDeadlineMiddleware` 使用相同的 maximum 与 1.0 秒 Close Grace，包住 `BaseHTTPMiddleware` 重包装后的真实 client-facing `send`。两层分别保证业务 Source 生命周期与最终网络写入上界，Close Grace 只用于正常结束 Body、关闭 Source 和释放 Slot，不允许生成新业务事件；停滞写入到期后由 pure-ASGI 层取消。
- Migration 必须保留可修复且低阻塞的三阶段边界：`20260716_0019` 只为 `debug_run_event.payload::text` 增加 32768 bytes 的 `CHECK ... NOT VALID` 并提交；`20260716_0020` 只先 `VALIDATE CONSTRAINT`，再创建 `atlas.prevent_fact_mutation()` UPDATE / DELETE Trigger；`20260716_0021` 独立使用 Alembic `autocommit_block()` 执行 `DROP INDEX CONCURRENTLY IF EXISTS atlas.debug_run_event_replay_idx`，downgrade 使用 `CREATE INDEX CONCURRENTLY IF NOT EXISTS` 恢复这个已被 `(debug_run_id, seq)` Unique Constraint 覆盖的普通索引。若历史超限 Payload 使 0020 Validation 失败，事务回滚且版本保持 0019；修复数据后再重试 0020，成功后才进入 0021 的并发索引清理。不新增重复事件表、LISTEN / NOTIFY 权威源或每连接数据库 Session。
- P5-00A 已建立正式 `UnitAttempt`；P6-02B2 已实现 `LiveSession / ControlLease`、浏览器控制 Epoch / Fence、Action Safe Point Pause / Resume / Takeover Command、Human Takeover、Quiesce，以及持久化且绑定控制 Epoch / Fence 的 `ActionGrant`。P5-00D2B 的 Task 级 Pause 只停止新 Unit 派发，不覆盖这些现场能力。B1 SSE 仍不接收 Frame、Command、Action 或人工输入，控制命令走独立 UnitAttempt Live Control API。

## Browser Worker、内部网关与 Playwright 边界

- `atlas-browser-worker` 是独立的 Temporal Worker。其 `BrowserWorkerSettings` 不包含 `database_url`，不构造控制面 `Database`；API 只在 DebugRun 已绑定 exact ExecutionContract 后向专用 Task Queue Dispatch。
- 一个 Browser Workflow 使用一个有 Heartbeat 的长时 Activity 执行浏览器副作用，Activity 不自动重试。导航、点击、输入或连接中断无法证明结果时记录 `OUTCOME_UNKNOWN`，本次执行只能 fail-closed，不能盲重放或推导 `PASSED`。
- 内部 Runtime Gateway 同时要求 Tenant / Run / Worker / Deadline 绑定的短期 Execution Permit 和独立 HMAC Request Signature。Signature 覆盖 Method、Path、Scope、Timestamp、Nonce、Body Digest 与 Permit Digest；响应使用 `no-store`，请求体有界，精确签名请求仅做有界幂等重试。Local / Test / Development 可以使用 HTTP 调试；Staging / Production 的 `BrowserWorkerSettings` 必须把 Runtime API 配置为 HTTPS Origin，否则 Worker 在启动前 fail-closed。
- `BrowserContextRestoreEnvelope` 使用 AES-256-GCM，AAD 绑定 ExecutionContract ID / Digest、Worker、Actor Slot、BrowserContextRef 与 Expiry；密文内 Descriptor 继续绑定 Tenant / Project / Environment、Lease / Fence 与 SessionArtifact Vault 元数据。Worker 只在内存解密，Scope、Key Version、Deadline 或 Integrity 不一致立即拒绝。
- `BrowserRuntimeReport` 是单调、类型化、不可变 Hash-chain：首条只能是 `execution.started`，每个 `actionId` 在整条 Contract Chain 只能提出一次；Proposal → 同 Actor Policy → ALLOW 后同 Proposal Receipt 必须连续，唯一例外是 Policy 后用 `execution.blocked` 明确终止无法形成可信 Receipt 的 Action。Action Report 中间不能插入其他普通 Report，Denied / Blocked Action 只能进入 Blocked 路径，完整链末条只能是 `execution.completed`。
- Playwright 只允许 frozen Tool Catalog 中真正实现的 Action，并复核 Tool / Policy / MCP Digest、Action Risk、Semantic Role、Route / Origin 与单次 Grant。Operation 和 Route 必须由部署时 exact-version Registry 注册，资产、Agent 与 HTTP 请求不能注入绝对 URL、Locator、Module、Script 或 Callable。
- DOM Action 必须引用当前 Observation 的 retained `ElementHandle`、Page Revision 与单次 Nonce；执行前重新核对 Visible、Element Key、Accessible Name 与 Semantic Fingerprint，页面变化使旧 Target 失效。普通 Request 与 WebSocket 都限制在 Session / Published Route 的精确 Origin。
- `CAPTURE_VIEW` 只能把原始字节交给受信 `BrowserArtifactWriter`。P6-02A 在 Playwright 截图前对输入控件、可编辑内容和显式敏感节点执行 DOM Mask，再将结果规范化为去元数据、RGB、白底 alpha flatten、固定压缩的 canonical PNG；对象写入后必须独立回读并复核完整 SHA-256 与大小，成功后才能形成 `VERIFIED` Receipt。`BrowserPlanOperation` 仍不得直接构造或返回 `EvidenceArtifactInput` 绕过 Writer。
- Evidence Finalization 必须精确匹配 Chain Head / Count，并对 Report 中的每个 `assertionInputDigest` / `artifactInputDigest` 与 Finalize Command 中完整 `AssertionResultInput` / `EvidenceArtifactInput` 的 Canonical Digest 重新比对；只匹配 ID、Count、Content Digest 或部分字段不足以终结。Finalization Command Digest 只允许同一完整命令 exact replay。
- Report Chain 出现 `execution.blocked`，或任一 Action Receipt 为 `FAILED / OUTCOME_UNKNOWN` 时，Finalize Command 中全部 Assertion Result 必须是 `INCONCLUSIVE`，最终 Outcome 也只能是 `INCONCLUSIVE`；后续 Assertion、Artifact 或 Operation 不能覆盖该安全结论。
- Evidence Store 的 Endpoint / Access Key / Secret Key 必须成套配置；Staging / Production 禁止自动创建 Bucket，部署时应为 Browser Worker 与 API 使用分离的最小权限写 / 读 Credential。未配置 Store 时 `capture_view` 与对象读取分别 fail-closed，不允许退化为内存 Hash、未校验下载或公开 Bucket URL。
- 当前只支持单 Actor。P6-02B1 已实现 DebugRun-scoped Live Snapshot / SSE，P6-02B2 已实现 UnitAttempt-scoped LiveSession、ControlLease、控制 Epoch / Fence、Human Takeover 与持久化 ActionGrant；首个真实 SaaS Operation / Route Registry、容器级 Egress / DNS / UDP / WebRTC 约束、Envelope Key Ring Rotation、公共 Start 到 Preparation / Bind / Dispatch 的自动串联和 Multi-actor 仍未完成，缺少任一所需部署能力时继续 fail-closed。
- 本切片没有修改任何前端页面、组件、DOM、布局、CSS 或既有交互；前端原型继续是唯一视觉与交互权威。

## 正式 Task 执行宿主边界

- P5-00A 固定 `TaskPlanVersion → TaskRun → ExecutionUnit → UnitAttempt` 四层宿主；P5-00B1 补齐 `ExecutionProfileVersion`、`IdentityProfileVersion`、`BrowserProfileVersion` 与 `DataProfileVersion` 正式不可变宿主。`DebugRun` 只服务发布前作者态试运行，DebugRun-scoped `ExecutionContract` 不能冒充正式 Execution Profile。
- `TaskPlanVersion` 只保存 pinned CaseVersion、结构化 Matrix / Profile 引用与 Policy Digest；发布后不可修改。P5-00B1 要求四类 Profile 真实存在、同 Tenant / Project、处于 PUBLISHED 且与 exact Case / Fixture 兼容；`IdentityProfileVersion` 额外冻结 Case actor 的 TestRole revision / capabilities，任何账号、Credential、Lease、Session 或 Token 字段均拒绝进入 Profile。
- `TaskRunManifest` 冻结完整 Unit 集、触发指纹、策略和 compiler version，并以 canonical `manifestHash` 绑定。Repository 与 PostgreSQL 使用同一递归 canonical JSON 规则重算 Plan、Profile、Manifest、Unit 和 stable request digest；同一 `tenant + triggerSource + triggerFingerprint` 只有 logical request digest、不可变 `rerunOfTaskRunId` lineage 与 rerun selection mode 都相同才视为 replay，服务端生成的 Run ID 与时间不参与幂等身份。
- `ExecutionUnit` 是 Manifest 中一个逻辑矩阵单元；`UnitAttempt` 是一次物理执行。业务重试追加 gapless `attemptNumber`，Activity retry 不增加 Attempt，旧 Attempt 不更新身份、不删除、不复活。首个 Attempt 随同步物化创建并冻结 `executionDeadline`；后续 Attempt 必须沿用父 Run namespace，并要求父 Run 已 SEALED、Run / Unit 可派发、前序 Attempt 已 CLOSED 且结果属于可重试集合。
- Lifecycle、Quality、Hygiene 是独立状态轴；结果可先 CLOSED，Cleanup 再从 PENDING / RUNNING / CLEANUP_FAILED 继续推进，`cleanupResolvedAt` 可以晚于 `closedAt`。`CLOSED + PASSED + LEAKED` 可以是事实，但严格 Gate 必须拒绝；CLEANUP_FAILED 进入有界长期 retry，耗尽后只能记为 LEAKED，不能洗掉历史。Task Pause 只停止新 Unit 派发，不冒充后续浏览器 Safe Point / Human Takeover。
- Attempt 与 Event append 使用父行锁串行分配无间隙序号；状态推进只调用数据库拥有的 Revision CAS 函数。`20260716_0025` 的 tenant-scoped `SECURITY DEFINER atlas.lock_task_execution_chain(...)` 在数据库内按 Run → Unit → Attempt 固定顺序锁定同一 sealed 执行链；`atlas_app` 仅获得受信函数 EXECUTE，仍没有三张状态表的表级 UPDATE。事务内只做 PostgreSQL 验证和事实写入，不持锁等待 Temporal、HTTP、Playwright、SSE、execution port 或对象存储。
- `task_plan_version` 与 `task_run_manifest` 使用显式结构化列，不复制一份整对象 JSONB 形成双重事实源。Matrix、Profile、Policy 和 Manifest Unit 的结构 validator 对 exact key set、缺键、SQL `NULL` 与 JSON `null` fail-closed。Unit 必须逐字段匹配 Manifest，Event 必须匹配最窄 Run / Unit / Attempt 的三轴状态。
- P5-00B1 的初始物化协议最多 100,000 Units。不超过 64 Units 保持同步原子快路径；`20260718_0042` 对更大 Run 先提交 `MATERIALIZING` Root、完整 immutable Manifest 与连续 64-Unit 分区检查点，再由独立 `atlas_dispatcher` Consumer 以 Lease / Claim Token / `dispatchRevision` 分批创建 Unit 和首 Attempt。每个分区独立提交并冻结 30 天 execution deadline；只有全部分区 `COMPLETED`，Seal 才在短事务中重算所有 digest、核对 Manifest 的全部 Unit 与首 Attempt、重验 Profile / Case / Fixture / Environment / TestRole，随后切换 `SEALED` 并追加唯一 `PENDING` Workflow Start Intent。未 Seal、历史 `legacy_unsealed` 或依赖漂移的 Run 不能推进状态。Task Admission 同事务重读父 Run / Unit，只对 SEALED 且 QUEUED / RUNNING 的 Run 和仍为 QUEUED 的 Unit 放行，Pause / Cancel / Finalize / Closed 不会继续派发。
- Run 与 Attempt 的 Temporal Workflow ID 由 Tenant ID + 对象 ID 确定性生成，并写入 `(namespace, workflowId)` 全局 Registry；Start Intent 只是待启动事实。P5-00B2A 的 `STARTED` 只说明 Temporal 接受且其 identity 已通过 Describe 复核；P5-00B2B 的同 Root replay 复用 durable History 与确定性 Child ID，不重复执行已完成副作用，但仍不代表 Task 已产生可信通过证据。
- `20260716_0024` 把 TaskRun Start Intent 扩展为 `PENDING / CLAIMED / RETRY_WAIT / STARTED / FAILED` 数据库状态机。只有独立 `atlas_dispatcher` 登录角色可以执行四个 owner-owned `SECURITY DEFINER` 函数；该角色无表级 DML、不是 Superuser 且没有 `BYPASSRLS`，`atlas_app` 不能跨 Tenant 领取 Intent。Claim 必须带 exact Temporal namespace，并只选择 `TASK_RUN + AtlasTaskRunWorkflow + atlas-task-run`。
- Intent Consumer 严格使用三段式事务：短事务 Claim 并提交，事务外执行 Temporal RPC，短事务用 exact Intent ID + Claim Token + `dispatchRevision` 确认 STARTED / Retry / Failed。过期 Claim 可接管；数据库时钟生成确认时间和 Retry `availableAt`；CAS 失败只记为 lease lost，旧 Consumer 不能覆盖新 Claim。
- Temporal 首次 Start Input 固定为无秘密 `schemaVersion + tenantId + projectId + taskRunId + requestDigest + manifestHash`，内部续跑仅增加默认关闭的 `dispatchAfterOrdinal + continuationCancelRequested`；Workflow ID 由 Tenant / Run ID 确定性生成。稳定 `request_id=str(intent.id)` 与 `REJECT_DUPLICATE + USE_EXISTING` 只负责安全重试；每次仍必须 Describe 并精确核对 namespace、Workflow Type、Task Queue 和 Memo identity / digest，不一致永久 fail-closed。RPC 歧义 / 可用性错误只做有界重试，持久化只保存格式受限的安全 Error Code，不保存异常正文。
- P5-00B2B / P5-00E4 的 `AtlasTaskRunWorkflow` 兼容上述 Intent 契约并固定消费 `atlas-task-run`。它只接收已 Seal Run，每页最多加载 64 Units，并按固定 8-child batch 启动 `atlas-unit-attempt` Queue 上的 `AtlasUnitAttemptWorkflow`。当前页只有在无 active Child、无 unsettled batch且全部 Unit 终态已落库后才 `Continue-As-New`；末页不携带历史全部 outcome，而由 PostgreSQL 对 Unit / Attempt / `execution_unit.finalized` Event 做完整投影后关闭 Root。Child ID 由 Tenant ID + UnitAttempt ID 确定性生成并使用 `REJECT_DUPLICATE`；原生取消会等待当前 Child 收敛，排空当前页后携带取消状态续跑，已完成 Child 的真实结果不会被统一改写。
- P5-00D1 后 Child 采用 Prepare Ticket DB Activity → Begin DB Activity → Execute side-effect Activity → Finish DB Activity，Root 最后再用 DB Activity 收敛 Run。Prepare 创建或精确重放每 Attempt 唯一、不可变、secret-free 的 `TaskUnitExecutionTicket`；Ticket 未通过数据库 Scope / digest / RLS 门禁时不会调用 Port。数据库 Activity 每次只允许 30 秒短事务；连接、重启、序列化等瞬时基础设施错误由 Temporal 以 1 秒到 60 秒退避耐久重试，确定性 `TaskOrchestrationInvariantError` 则转换为不重试的安全错误码，未知数据库异常只以可重试安全码进入 History。副作用 Activity 固定 `maximumAttempts=1`，并持续 Heartbeat、等待取消完成。冻结 `executionDeadline` 由 PostgreSQL 与 Workflow 双重校验，执行 Activity 同时以 `scheduleToClose` 覆盖 Queue 排队时间，并以 `startToClose` 限制实际运行时间，两者都不越过 deadline。
- Child failure、取消与不可信返回通过安全类型化 fallback 持久化；异常或非法 adapter payload 在 Activity 内先归一化，敏感正文不进入 Temporal History。运行中的副作用被原生取消时，无法证明外部结果，必须收敛为 `INCONCLUSIVE / TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN`。Attempt finalize 事件冻结 exact `status + errorCode`，Run finalize 事件冻结 exact status 与计数，CLOSED replay 逐字段核对而不是只比较 Quality。P5-00B2B 当时没有 `AttemptSeal`，因此未封存执行只能得到 `EXECUTED_UNSEALED → FINISHED_UNSEALED`；P6-03A 后只有数据库 exact ResultRef 才能使 Workflow 表达可信 `PASSED`。
- `atlas-task-intent-consumer`、Root / Attempt Worker 与 Schedule Worker 使用独立开关和默认关闭的 Compose profiles。P5-00D1 已把 `TaskUnitExecutionPort` 输入收紧为 prepared `ticketId / ticketDigest`；P5-00E5 已提供可由 CLI / Compose 装配的正式 signed HTTPS Adapter。P5-00D2A/D2B/E4/E6 让同一 Consumer 进程并行轮询 Start Intent、TaskRun Command Intent、大 Run Materialization Partition 与 Schedule Sync Intent；四类事实各自 Claim / CAS，任何 Temporal I/O 都在事务外。P5-00E6 已建立数据库权威 Schedule、结构化 Calendar、IANA Timezone、DST、Overlap/Catchup/Jitter、Pause/Resume、Production 自动 Pause 和统一 Schedule fire。剩余执行面缺口是旧 Run RETRY command、签名外部回调与部署端真实 SaaS executor。
- P5-00D2A 的 Cancel API 要求 `If-Match`、`Idempotency-Key == clientMutationId` 和 `RUN_OPERATOR+`。接受事务锁定 exact sealed Run revision，原子写 `TaskRunCommandIntent`、推进 `CANCELING` 并追加 Event / Audit / Outbox。Dispatcher 在发送 Signal 前复核 deterministic Workflow ID、Type、Queue 与 Start Intent Memo；`NOT_FOUND` 代表 Workflow 可能尚未由 Start Intent 拉起，必须耐久重试。Root 同时从 DB 计划读取已持久化的 `cancelRequested`，消除 Cancel 先于 Workflow Start 的竞态；Signal 重投按 exact command 去重。
- Cancel 不把 active Child 伪装成已知取消成功：Root 停止后续 batch并请求取消当前 Child，已完成 Child 结果原样保留，尚在副作用中的 Child 继续按未知结果收敛为 `INCONCLUSIVE`。Root 只有在 Run 已 durable `CLOSED / CANCELED` 后才在同一数据库事务把 exact command 标记 `APPLIED`；若 Workflow 已先因 plan cancel 关闭，Dispatcher 的 terminal reconciliation 也只在确认 Run 已 CANCELED 后转为 `APPLIED`。
- P5-00D2B 的 Task Pause 只暂停新 Unit 派发，不暂停或接管浏览器 Action。Root 在启动每个最多 8 个 Child 的批次前，以一个短事务为整批创建或精确重放 immutable Ticket，形成不可追加的预授权边界；Pause 到达后当前已授权批次允许完成，下一批不能获得 Ticket。批次结束后的 checkpoint 在同一事务推进 `PAUSE_REQUESTED → PAUSED` 并把 exact Pause command 标记 `APPLIED`，随后 Workflow 使用 durable `wait_condition` 等待新 Signal，不轮询或占用数据库连接。
- Resume API 只接受 `PAUSED`，接受事务保持 Lifecycle 为 `PAUSED` 但推进 Revision 并写 durable command；只有 Root 被 exact Resume Signal 唤醒并在 checkpoint 同事务完成 `PAUSED → RUNNING + command APPLIED` 后，才允许准备下一批。Cancel 可从 `PAUSE_REQUESTED / PAUSED` 进入 `CANCELING`，并在同一接受事务把所有未完成 Pause / Resume 标记 `SUPERSEDED`；迟到或乱序 Signal 不能覆盖 Cancel。
- P5-00D3A 只实现自动基础设施重试。`task-run-manifest/0.2` 通过 `TaskRetryPolicy` 冻结 per-Unit 次数、Run 总预算、指数退避上限与稳定 jitter，并由 `policyDigests["infra-retry"]` 绑定完整内容；历史 `0.1` Manifest 等价于零次自动重试。只有受信 Adapter 明确返回 `INFRA_ERROR` 才能追加新 `UnitAttempt`，Assertion / 产品失败、非法返回、取消歧义与 `OUTCOME_UNKNOWN` 均不自动重试。
- Attempt Finish 只关闭一次物理 Attempt；Root 在独立短事务中锁定 Run 并对最多 8 个结果作原子结算。结算按 Unit ordinal 分配 Run 总预算，使用确定性 Attempt ID、gapless `attemptNumber`、同一 namespace 与原始 `executionDeadline` 追加重试；数据库返回的 `notBefore` 由 Temporal durable timer 等待，Pause / Resume / Cancel Signal 可中断等待。Pause / Cancel 竞态下不追加 Ticket 或重试决策，过期 deadline 也不创建新 Attempt。
- 重试 Attempt 必须由前序 `CLOSED / INFRA_ERROR`、仍为 `RUNNING` 的父 Unit 和当前 frozen Manifest 共同证明；Admission 与 immutable Ticket Insert Guard 都复核这条链。旧 Attempt 结果、错误码和可选 `retryAfterSeconds` 保持不可变。重试最终执行完成后，在 AttemptSeal 落地前仍只能得到 `FINISHED_UNSEALED / INCONCLUSIVE`，不能生成 `PASSED`。
- “仅重跑环境失败”不属于自动 retry。P5-00D3B 的 API 只接受 `SEALED / CLOSED` source Run，以 `If-Match`、`Idempotency-Key == clientMutationId` 和 `RUN_OPERATOR+` 创建绑定 `rerunOfTaskRunId + INFRA_FAILURES` 的新子 TaskRun；不得向已关闭 Run 发送 RETRY Signal，也不得复活或覆盖旧 Attempt。
- `20260717_0031` 的 owner-owned Manifest Insert Guard 在数据库内重算 source 中每个且仅有 `CLOSED / INFRA_ERROR` 的 Unit，并要求 child 保留 exact Plan、schema、iteration、policies、retry policy 与 compiler。Child 使用全新 Run / Unit / Attempt / Temporal identity，复用源首 Attempt 的 window 时长并从新 queued time 起算，随后按既有完整物化路径 Seal 并创建 Start Intent；客户端不能提交 Unit 白名单。
- P5-00E1 的 TaskPlan 编写使用稳定 Catalog 根与 append-only published Version，不新增可变 Draft。创建和发布均要求 `RUN_OPERATOR+`、可审计 Actor、`Idempotency-Key == clientMutationId`，并与 Audit / Outbox 同事务提交；版本的 ID、时间、`versionRef` 和 canonical digest 由服务端生成，PostgreSQL 继续重验 PUBLISHED Case / Profile / Fixture、ACTIVE TEST/STAGING Environment 与同作用域引用。E1 不创建 Profile，也不物化或启动 TaskRun。
- P5-00E2 的首次 Manual Launch 只接受 exact published TaskPlanVersion。Identity 只与其绑定的 CaseVersion 组合，Data 只与 Case Profile 指向的 Fixture Blueprint 组合，Environment / Browser 才作为全局轴展开；无兼容项或超过 100,000 Units 立即 fail-closed。请求携带的完整 `TaskRetryPolicy` 必须匹配版本冻结的 `infra-retry` digest；≤64 同步快路径的初始 Attempt 使用固定 15 分钟 execution window，E4 分区路径在实际物化时冻结 30 天容量窗口并仍于 Admission / Workflow 双重复核。
- Manual Launch 在同一短事务内完成 Manifest v0.2、Run / Unit / 首 Attempt、materialization Seal、唯一 Start Intent、`task_run.requested` Event、Audit / Outbox 与幂等完成。Stable trigger fingerprint 只绑定 TaskPlanVersion 与 `clientMutationId`；重复提交不能生成第二个逻辑 Run。
- P5-00E3 将 Schedule / CI / Webhook 收敛到统一 `POST /v1/task-runs` 强类型入口，并复用 E2 的 compatible-only compiler 与完整事实事务。P5-00E6 的 Temporal Schedule Workflow 也只通过该 compiler 创建 Run。Schedule 的永久身份是 `scheduleId + scheduledFireTimeUtc`，CI 是 `provider + pipelineRunId + jobId + rerunIndex`，Webhook 是 `sourceKey + deliveryId`；同一永久身份即使换用 HTTP Idempotency-Key，也只能 replay 同一个逻辑 Run。
- CI 的 commit / branch 与 Webhook event type 只进入允许列表内的审计 Event 元数据，不参与触发身份，也不能覆盖 TaskPlanVersion 冻结的 URL、Environment、Credential、Tool、Model 或 Policy。E6 已完成 Temporal Schedule catalog / overlap / catch-up 管理；签名外部回调仍属于独立部署与后续切片。
- Execution Ticket canonical deadline 必须与 PostgreSQL `timestamptz → jsonb` 一致：统一为 UTC `+00:00`，并去除 fractional-second 尾零。否则随机落在末位零的微秒值会使 Python / PostgreSQL digest 不一致。
- 协议与数据库共同执行 100,000 Units 硬上限。`20260718_0042` 通过 64-Unit 分区、独立提交、Lease takeover 与最终统一 Seal 实现恢复，不扩大 API 同步事务也不跳过完整性证明；生产容量基准、单 Tenant 并发配额与 P9 长稳压测仍需单独验收。
- P5-00E5 的 `HttpTaskUnitExecutionPort` 使用固定内部 Path 和版本化 secret-free envelope；Request HMAC 绑定 Worker / Tenant / Attempt / Ticket、timestamp、Nonce、Body digest 与 Attempt Idempotency-Key，Response HMAC 再回绑原 Request Nonce / Digest、HTTP status、response timestamp / digest 和相同 exact scope。Staging / Production 只允许 HTTPS，Client 禁止 redirect、环境代理和 transport retry，响应必须 `no-store + application/json` 且有界读取。
- 一次 UnitAttempt side-effect Activity 只允许一次 executor HTTP 调用。Transport ambiguity、非 200、超限、签名或 payload 异常都返回 `INCONCLUSIVE / TASK_EXECUTOR_*` 安全码，不能伪装为明确 `INFRA_ERROR` 并触发业务自动重试。`RESULT_FINALIZED` 仍必须被 Finish Activity 从 PostgreSQL 的 exact AttemptSeal / ResultRef 恢复和复核；远端回包不是 PASS 权威。
- P5-00E6 的 `20260718_0043` 把 Schedule desired state 与 Temporal RPC 解耦。API 同事务写 Schedule、Sync Intent、Audit、Outbox；Dispatcher 在事务外 create-or-describe，并精确复核 Schedule Memo、Workflow Action/Input、结构化 Calendar、Timezone、Overlap/Catchup/Jitter、Queue 与 policy，再以 Claim Token + Dispatch/Schedule Revision CAS 保存未来五个 fire。陈旧 Revision 自动 `SUPERSEDED`。
- Schedule Workflow 只相信 Temporal 保留的 `TemporalScheduledById / TemporalScheduledStartTime`，并把 `scheduleId + nominal fire UTC` 交回统一 compiler。Pause 依据 Workflow 实际开始时间阻止后续 action，不篡改已启动 TaskRun；Environment 变为 `PRODUCTION` 会同事务自动 Pause 关联 Schedule，数据库阻止恢复。Temporal 跨进程 payload 只用规范 ISO 字符串，避免 SDK 对 Python `datetime` dataclass 的沙箱反序列化差异。
- 真实 PostgreSQL 已验证 tenant scope、Run → Unit → Attempt 锁序、Revision CAS、exact-event replay、最小权限与 `20260716_0025` 升降级；真实 Temporal 已验证两个 Worker、固定双 Queue、1 / 9 Child 跨 batch、deterministic child ID、同 Root replay、排队跨 deadline 不执行副作用、数据库 Activity 连续三次瞬时失败后恢复、Adapter 异常与非法返回不泄漏进 History、非 PASS 结果、原生 Child 取消的未知结果与 Root 取消后已完成 Child 结果保留。完整 `make verify` 已通过 780 tests / coverage 90.15%。
- 本切片不修改 Launch、Task Control、Live Theatre 的页面结构、DOM、布局、CSS 或交互，前端既有原型仍是唯一权威。

## 不可破坏的领域链

```text
TaskPlanVersion
  -> TaskRun
    -> ExecutionUnit
      -> UnitAttempt
        -> AttemptSeal
      -> UnitResolutionRevision
    -> TaskResultSnapshot
    -> TaskGateDecision
```

- 重新执行必须创建新的 `UnitAttempt`，不得覆盖旧 Attempt。
- `AttemptSeal`、`TaskResultSnapshot` 和 `TaskGateDecision` 都是不可变事实。
- 无效或不完整的 Seal 不能产生通过结论。
- `WorkflowDraft` 是作者态资产，不是 Temporal Workflow。
- `DataNodeAttempt` 属于 Fixture 链路，不得与正式测试的 `UnitAttempt` 混用。

## 事实权威边界

- PostgreSQL：资产、租约、运行 Manifest、追加事实、结果、审计与 Outbox。
- Temporal：进行中的耐久编排；不承担业务查询和最终结果存储。
- 对象存储：截图、视频、Trace 和大型 Artifact 内容。
- SSE、WebSocket、缓存和洞察投影：可重建，只能消费权威事实。
- Browser Worker：只能通过内部协议和短期授权访问资源，不直接访问主数据库。

## 工程原则

- 先采用模块化单体，API、Temporal Worker、Browser Worker 和 Projector 作为独立进程部署。
- 领域代码不依赖 FastAPI、Psycopg、Temporal 或 Playwright。
- 外部 I/O 只出现在 Application Adapter 或 Temporal Activity 中。
- Published 资产只引用 exact version；运行时同时冻结 ID、版本、摘要和策略版本。
- Python 字段使用 `snake_case`，对外 JSON 使用 `camelCase`。
- 时间统一保存 UTC `timestamptz`，对外使用带时区的 RFC 3339。
- 对外 ID 由应用生成 UUIDv7；数据库不得依赖随机 UUIDv4 默认值。
- 必要代码注释使用 English，注释解释约束和原因，不复述代码。
- 不引入任意 Shell、SQL、JavaScript、Eval、URL 或 Header 执行能力。

## API 约定

- 公共接口：`/v1`；Worker 内部接口：`/internal/v1`。
- 命令使用 `Idempotency-Key`；并发更新使用 `If-Match` / revision。
- 异步命令返回 `202 Accepted`、资源 ID 与状态 URL。
- 错误统一使用 `application/problem+json`，包含稳定 `errorCode` 和 `requestId`。
- 列表使用不透明 Cursor，不使用会随数据变化漂移的 Offset 作为正式协议。
- SSE 使用单调 Cursor / `Last-Event-ID` 支持断线重放；WebSocket 只承载实时画面和控制帧。

## PostgreSQL 约定

- 运行进程必须使用连接池；事务范围内设置 Tenant / Actor 上下文。
- 多租户业务表启用并强制 RLS，策略字段必须有索引。
- 每个 Foreign Key 都显式评估并添加索引。
- Composite Index 将等值列放在范围列之前。
- Partial Index 只用于稳定的热子集，例如未处理 Outbox、活动 Lease 和待领取 Job。
- `FOR UPDATE SKIP LOCKED` 只允许用于队列领取，不用于普通业务读取。
- 外部调用不得发生在持有数据库锁的事务内；多行锁按稳定 ID 顺序获取。
- 核心事实采用追加式表；允许更新的投影不得反写事实表。

## 前端约定

- App Router Layout 和页面默认保持 Server Component，只把交互边界声明为 Client Component。
- API 类型由后端 OpenAPI 生成，禁止手工维护同名但不同义的接口。
- 查询使用 SWR 去重和缓存；命令使用统一 Mutation Client。
- Canvas、Live、Result Chart 等重组件按路由动态加载。
- 高频 Pointer / Frame 临时状态进入 `ref`，业务可见状态才进入 React state。
- URL 保存可分享的筛选和选择状态；不建立无边界的全局 Store。

## 每个切片的完成定义

一个功能只有同时满足以下条件才可标记完成：

1. 领域模型和不变量已经实现并具有单元测试。
2. Migration、约束、索引和 RLS 已在真实 PostgreSQL 验证。
3. API 契约、错误和权限路径已测试。
4. 前端已消费真实 API，不再读取该功能的硬编码业务数据。
5. OpenAPI / JSON Schema 没有漂移。
6. 质量门禁通过，进度、要点和实施矩阵已更新。
