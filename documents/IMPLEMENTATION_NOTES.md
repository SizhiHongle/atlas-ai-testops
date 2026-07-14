# Atlas AI 测试平台实施要点

更新时间：2026-07-14

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
