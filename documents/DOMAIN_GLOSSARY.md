# Atlas 统一领域术语

更新时间：2026-07-16

本文件是跨文档对象命名的规范来源。Word 设计稿负责解释产品和实现背景，机器可读 Schema 负责约束线协议。

## 人员主体与被测身份

| 对象 | 定义 | 禁止混用 |
| --- | --- | --- |
| `PlatformPrincipal` | 登录 Atlas 的人员主体；由 `PlatformUser`、`PlatformMembership` 和 `PlatformSession` 表达 | 不代表被测系统中的销售、主管、客服等业务角色 |
| `PlatformRole` | 人员主体在 Tenant 或 Project 范围内的 Atlas 管理权限 | 不得作为 TestCase 的业务身份矩阵 |
| `TestRole` | 被测系统中的业务角色定义，例如销售、主管、客服 | 不授予 Atlas 管理权限 |
| `TestAccount` | 被测系统中的可租用账号 | 不得登录 Atlas Platform Session |
| `AccountPool` | 一个 Environment 内绑定 TestRole、TTL 与冷却策略的测试账号集合 | 不表示平台成员组，也不直接保存秘密 |
| `AccountSlot` | TestAccount 的稳定独占调度槽；MVP 每个账号一个 Slot | 不等同于账号本身，也不得出现多个活动租约 |
| `CredentialBinding` | TestAccount 到 Secret Manager 不透明引用的用途约束 | 不保存、返回或记录密码、Token、Cookie、TOTP 等秘密值 |
| `AccountLease` | Execution / Debug 对 TestAccount Slot 的有期限独占占用；包含 TTL、Heartbeat、终态与 Fencing Token | 不得替代 Platform Session，也不得在终态后修改 |
| `AccountHandle` | Lease 向 Worker 暴露的短期不透明账号句柄 | 不得编码或泄露 Account ID、登录提示、Slot ID 或 SecretRef |
| `FencingToken` | 每次 Acquire 或管理撤销推进的 Account Lease Epoch | 旧 Token 不得续租、释放或影响后续 Lease |
| `AllowedOrigin` | Environment 与 Secret Grant 共同绑定的规范化精确 HTTP Origin | 不得使用通配域名、Path、任意 URL 或仅由客户端声明的范围 |
| `SecretGrant` | 有效 Lease 为固定 Worker、Purpose、Fence 与 Origin 签发的短 TTL、一次性秘密使用授权 | 不是 Credential；不得重放、跨 Lease 使用或持久化原始 Grant Ref |
| `SecretProvider` | 通过受控闭包向 Auth / Browser Worker 临时提供秘密材料的端口 | 不提供返回密码、Token、Cookie 或 SecretRef 的普通读取接口 |
| `PasswordSecretScope` | Broker 私下绑定 SecretRef 与版本后生成的密码闭包作用域 | Adapter 只能调用 `with_password_secret(...)`，不能读取定位信息 |
| `ProviderAdapter` | 以版本化 Capability 隔离具体 SaaS / IdP 差异的实现 | 不得把厂商 SDK、原始响应、任意 URL 或管理 API 穿透到核心域与 Agent |
| `AdapterContext` | Provider 操作的 Tenant / Project / Environment / Origin / Request 上下文及受控 Secret 闭包 | 不提供 `getSecret()`，不暴露 SecretRef、SecretVersion 或秘密值 |
| `ConnectorInstallation` | 一个 Environment 中显式安装、配置并验证的 Provider Connection 权威记录 | 不等同于 Adapter 代码；不得由 Worker 临时传入 Endpoint、配置或动态 Module |
| `CapabilitySnapshot` | Connector 最近一次验证后实际协商出的版本化 `{name, version, mode}` 集合 | 不等同于 Adapter Manifest 的理论能力；未验证或过期快照不得用于账号与 Grant |
| `ConfigurationRef` | Connector 指向外部配置或 Secret Manager 的控制面内部不透明引用 | 不得进入公共响应、事件 Payload、Problem Details、日志或前端状态 |
| `IdentityFingerprint` | Provider Subject 在 Connector 作用域内生成的不可逆 SHA-256 身份锚点，用于后续登录检测身份漂移 | 不是 External Subject；不得反解、跨 Connector 比较或作为公共账号标识 |
| `AccountHealthCheck` | 一次 TestAccount 登录、身份与角色验证的不可变安全事实；保存快照 Revision、稳定结果分类和安全摘要 | 不保存 SecretRef、登录名、原始 Subject、Provider 请求 / 响应、Cookie 或 Token |
| `AccountStateTransition` | TestAccount lifecycle、health、operational、sync 与 cooldown 正交状态的一次追加式前后快照 | 不得覆盖历史状态，也不能用可变 `available` 标志替代 |
| `AccountHealthPolicy` | AccountPool 上的连续账号失败阈值与重试冷却策略 | 基础设施失败不得误计为账号失败；身份或角色漂移不等待阈值 |
| `AuthSessionWorker` | 独立消费 `atlas-auth-session` Task Queue、执行 Provider / Playwright 登录与加密存储的进程 | 不得与 FastAPI API 进程合并，不得向控制面返回 Storage State 或 Vault Key |
| `SessionArtifact` | 一个 AccountLease / Fence 下被加密保存的 Playwright Storage State 生命周期元数据 | 不是 PlatformSession，也不是 P6 实时 BrowserSession；PostgreSQL 不保存明文 Cookie / Token |
| `BrowserContextRef` | Worker 可安全持有的短期不透明 READY Session Handle | 不得编码 ObjectRef、Account ID、Credential ID、Key Version 或 Storage State |
| `AuthActionTicket` | 自动认证无法确定完成时创建的有 TTL、Lease / Fence / Origin 绑定人工处置请求 | 不是 Session，不代表认证成功，不得绕过 Connector Capability 或身份复核 |
| `SessionArtifactVault` | 以 AEAD 加密并通过受控闭包解密 Session Artifact 的对象存储端口 | API 不得加载该端口的 Key；静态 Key 仅允许本地开发，生产必须接入 KMS |

## 正式执行对象链

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

| 对象 | 定义 | 可变性与事实源 |
| --- | --- | --- |
| `ExecutionProfileVersion` | exact CaseVersion 的 Model / Prompt / Tool / Feature 预绑定 | P5-00B1 已落地；内容不可变，状态只可 PUBLISHED → DEPRECATED / REVOKED；不是 DebugRun `ExecutionContract` |
| `IdentityProfileVersion` | Case actor 到 exact TestRole revision / capabilities 的无秘密映射 | P5-00B1 已落地；不保存账号、Credential、Lease 或 Session |
| `BrowserProfileVersion` | Chromium revision、Viewport、Locale、Timezone 与 runtime attestation | P5-00B1 已落地；内容不可变，调度前仍须匹配实际 Worker 能力 |
| `DataProfileVersion` | exact Fixture Blueprint / Plan 与无秘密 Run Inputs | P5-00B1 已落地；Profile 冻结 digest，dispatch admission 再按 exact Fixture `run_input_schema` 复验；不保存动态 Secret 或运行资源 |
| `TaskPlan` | Project 内稳定、可复用的任务计划 Catalog 根 | P5-00E1 已开放创建、Catalog 与 Detail；`RUN_OPERATOR+`、幂等、Audit / Outbox，当前不提供可变 Draft |
| `TaskPlanVersion` | 已发布的任务选择、矩阵、触发和策略 | P5-00E1 已开放不可变发布、历史与精确读取；四类 Profile、Case / Fixture、Environment 和 canonical digest 由 PostgreSQL fail-closed |
| `Manual Launch` | 人工触发 exact TaskPlanVersion 的首次正式运行 | P5-00E2 已开放；只编译 Case / Fixture 兼容矩阵，最多 64 Units，并原子产生 Manifest、首 Attempt、Seal 与 Start Intent |
| `TaskRun` | 一次触发形成的任务运行与冻结 Manifest | stable request digest 幂等；只有 `MATERIALIZING → SEALED` 完整证明后才能推进状态；P5-00D3B child 以不可变 `rerunOfTaskRunId + INFRA_FAILURES` 记录 lineage / selection |
| `TaskWorkflowStartIntent` | 与 sealed TaskRun / deterministic Workflow ID 同事务生成，并由可靠交付状态机推进的待启动事实 | P5-00B2A 支持 `PENDING / CLAIMED / RETRY_WAIT / STARTED / FAILED`；Claim Token + Revision 防旧 Consumer 覆盖，`STARTED` 只表示 Temporal 接受，不表示 Task 已执行或成功 |
| `TaskWorkflowIntentConsumer` | 使用独立 `atlas_dispatcher` 权限把 TaskRun Start Intent 可靠提交到 Temporal 的后台进程 | 短事务 Claim → 事务外 Start / Describe → 短事务 CAS Ack；默认关闭，只提交 exact `AtlasTaskRunWorkflow + atlas-task-run`，P5-00B2B 的 Root Worker 再消费该固定 Queue |
| `TaskRunCommandIntent` | API 已接受、尚待可靠作用到 exact TaskRun Workflow 的 secret-free 控制事实 | P5-00D2A/D2B 支持 `CANCEL / PAUSE / RESUME`；公开状态为 `PENDING / DELIVERED / APPLIED / FAILED / SUPERSEDED`，内部 Claim / Retry 状态不暴露；`clientMutationId + expectedRunRevision + requestDigest + manifestHash + Workflow identity` 共同进入 canonical digest |
| `TaskRunCommandIntentConsumer` | 复用独立 `atlas_dispatcher` 权限向 exact Root Workflow 可靠发送控制 Signal | 短事务 Claim → 事务外 Describe / Signal → 短事务 CAS；Signal 重投由 Root 按 command ID + exact payload 去重，`NOT_FOUND` 视为可能尚未 Start 并持久重试 |
| `AtlasTaskRunWorkflow` | 一个 sealed TaskRun 的真实 Temporal Root，加载完整首 Attempt 计划并耐久聚合 Child 结果 | P5-00D3A 已接入 durable Cancel / Pause / Resume 与自动 infra retry；固定 `atlas-task-run`，最多 64 Units，按 8-child wave 原子预授权、结算并调度；冻结 backoff 使用 durable timer 且可被控制 Signal 中断 |
| `AtlasUnitAttemptWorkflow` | 一个 UnitAttempt 的真实 Temporal Child，按 Prepare Ticket → Begin → Execute → Finish 边界执行一次物理尝试 | P5-00D1 已收紧；固定 `atlas-unit-attempt`，deterministic child ID，冻结 deadline；数据库 Activity 耐久 retry，副作用 Activity `maximumAttempts=1`；Ticket 未准备成功时不调用 Port |
| `TaskUnitExecutionTicket` | 一个 UnitAttempt 在副作用前生成的不可变、secret-free 执行授权快照 | P5-00D1 已落地；每 Attempt 唯一，冻结 exact Case / Profile / Fixture / Environment / Origin / deadline 摘要，强制 RLS 且不可更新删除；不包含账号、Credential、Lease、Session 或 Token |
| `TaskUnitExecutionPort` | UnitAttempt Workflow 调用的受信副作用执行 Adapter 边界 | P5-00D1 Protocol 只接收 `attempt + ticketId + ticketDigest`；仓库没有内置 production 实现，Task Worker 默认关闭，启用却未注入真实 Adapter 时 fail-closed |
| `ExecutionUnit` | CaseVersion 与矩阵单元形成的逻辑测试槽位 | P5-00A 已落地；Manifest 身份创建后不可变，PostgreSQL |
| `TaskRetryPolicy` | TaskRun Manifest 冻结的自动基础设施重试边界 | `atlas.task-retry-policy/0.1`；限制 per-Unit 次数、Run 总预算、指数退避 / jitter，并以 `infra-retry` policy digest 绑定；只作用于显式 `INFRA_ERROR` |
| `UnitAttempt` | ExecutionUnit 的一次真实执行 | 自动基础设施重试创建确定性、gapless 新 Attempt；旧 Attempt 不覆盖，Activity retry 不创建新 Attempt，原始 deadline 不延长；Assertion / 产品失败与 `OUTCOME_UNKNOWN` 不自动重试 |
| `AttemptSeal` | Attempt 关闭时产生的不可变事实包 | P5-00A 已提供正式宿主；Seal 待 P6 后续，永久不可变。当前 Task Workflow 因没有 Seal 而绝不表达 `PASSED`；成功执行只得到 `FINISHED_UNSEALED` / `INCONCLUSIVE` |
| `UnitResolutionRevision` | 对多个 Attempt 的追加式解释 | 只追加 Revision，可重建 |
| `TaskResultSnapshot` | 绑定 Manifest 和策略版本的可复现任务结论 | 不可变快照 |
| `TaskGateDecision` | 针对确定 Result Snapshot 作出的门禁决定 | 追加式审计事实 |

## 作者态与发布态对象链

```text
TestCase
  -> WorkflowDraft@semanticRevision/layoutRevision
    -> DebugRun
  -> CaseVersion
    -> PlanTemplate
```

- `WorkflowDraft` 是作者态资产，不是 Temporal Workflow。
- `DebugRun` 绑定 Draft snapshot，不进入正式质量统计。
- `CaseVersion` 发布后不可变，TaskRun 只能引用 exact CaseVersion。
- `PlanTemplate` 环境无关；运行时绑定后形成 `ExecutionContract`。

## Debug 受信执行与证据链

```text
DebugRun
  -> ExecutionContract
    -> AssertionResult
    -> EvidenceArtifact
  -> EvidenceManifest
```

| 对象 | 定义 | 禁止混用或当前状态 |
| --- | --- | --- |
| `ExecutionContract` | DebugRun 第一次执行副作用前冻结的 Test IR、Plan、Fixture、Actor Lease / Fence / Session、Browser、Model / Prompt、Tool / MCP 与 Policy 精确版本合约 | P6-00 已落地且每个 DebugRun 唯一、不可变；P6-01 Browser Worker 只能消费 exact binding，不得临时改写配置 |
| `AssertionResult` | 由冻结 Assertion Program 和 exact Evaluator 产生的单条确定性 Oracle 事实 | P6-00 已落地；Agent 不能传入或决定 Case 级 `PASSED` |
| `EvidenceArtifact` | 截图、Trace、DOM / ARIA / Network 等对象内容的不可变元数据和完整性状态 | PostgreSQL 只保存安全元数据；对象地址不进入 EvidenceManifest 或公共投影；生产 Redaction / Writer 属于 P6-02 |
| `EvidenceManifest` | 绑定 ExecutionContract、FixtureManifest、AssertionResult、Artifact 和事件链的不可变证据根 | P6-00 已落地；只有完整、已验证且所有 HARD Oracle 通过时才能得到 `PASSED` |

- P6-01 Browser Worker 通过机器认证的内部 Runtime Gateway 调用 `DebugRuntimeService`，不是公共完成 API，且 Worker 不得直接访问主数据库。
- `EvidenceManifest` 服务于 DebugRun 的发布试运行；P5-00A 已创建正式 `UnitAttempt`，后续 `AttemptSeal` 必须精确绑定该宿主，二者不得混为同一对象。

## Browser 执行平面对象链

```text
TemporalBrowserExecutionDispatcher
  -> BrowserExecutionWorkflow
    -> BrowserExecutionActivity
      -> BrowserExecutionBundle
        -> BrowserContextRestoreEnvelope
      -> BrowserRuntimeReport*
      -> EvidenceManifest
```

| 对象 | 定义 | 禁止混用或当前状态 |
| --- | --- | --- |
| `BrowserWorker` | 独立消费 `atlas-browser` Task Queue、恢复隔离 BrowserContext 并执行冻结 Plan 的无控制面数据库进程 | 不得获得主数据库 DSN，不得与 API 进程合并；Staging / Production Runtime API 必须使用 HTTPS；默认 Operation / Route Registry 为空 |
| `BrowserExecutionPermit` | API 为 exact Tenant / DebugRun / Worker / Deadline 签发的短期 Runtime Authority | 不是用户 Session 或通用 Bearer Token；单独泄漏仍不能替代 HMAC Request Signature |
| `BrowserExecutionBundle` | 内部网关投递的 ExecutionContract、Test IR、PlanTemplate、Fixture Export 与每 Actor 加密 Restore Envelope | `atlas.browser-execution-bundle/0.1`；不是 Agent 可修改的运行参数，也不得包含原始 Storage State |
| `BrowserContextRestoreEnvelope` | 以 AES-256-GCM 封装 SessionArtifact Restore Descriptor 的 Worker-only 密文 | AAD 绑定 Contract / Worker / Actor / BrowserContextRef / Expiry；当前只支持单活动 Key Version，Key Ring Rotation 待后续 |
| `BrowserObservation` | Playwright Adapter 从当前 Page Revision 捕获的目标候选、Semantic Fingerprint 与单次 Next-step Nonce | Page 变化后立即过期；不信任页面文本作为策略或成功结论 |
| `BrowserActionProposal` | 引用 frozen Node、Actor、Observation / Target 与结构化 Risk 的候选浏览器动作 | 不能携带任意 Locator、Script、绝对 URL 或动态 Callable |
| `BrowserPolicyDecision` | exact Policy Digest 对 Action、Risk、Semantic Role、Route、Origin 与 Observation Freshness 的确定性裁决 | Agent 不能自批；只有 `ALLOW` 才能生成短期单次 Grant |
| `BrowserActionGrant` | Contract / Proposal / Page Revision 绑定的短期一次性执行授权 | Action ID 与 Grant 都只能消费一次；同一 `actionId` 在完整 Contract Report Chain 也只能提出一次 |
| `BrowserExecutionReceipt` | Adapter 对一次 Grant 的客观 `SUCCEEDED / FAILED / OUTCOME_UNKNOWN` 回执 | 只要 Receipt 非 `SUCCEEDED`，所有终结 Assertion 和最终 Outcome 都必须为 `INCONCLUSIVE`，不能被 Operation 或后续证据覆盖 |
| `BrowserRuntimeReport` | 按 Sequence、Previous Digest 与 Content Digest 形成的类型化追加事实 | `atlas.browser-runtime-report/0.1`；Action Proposal / Policy / Receipt 必须连续且同 Actor / Action，完成后不可追加或修改 |
| `BrowserArtifactWriter` | 接收原始浏览器字节并执行 Redaction、对象存储、独立 Hash 与 Integrity Verification 的受信端口 | 是 `EvidenceArtifactInput` 的唯一生产边界；Operation 不得直接构造或返回 Artifact 元数据 |
| `BrowserOperationRegistry` | 部署代码登记 frozen Plan Node exact version 到受审 Operation 的映射 | 不从数据库、HTTP、资产或 Agent 动态导入 Module / Script / Callable；Operation 只能经 Browser Tool 触发 Artifact Writer |
| `BrowserRouteRegistry` | Published Surface / Route Key 到 exact HTTP(S) URL 的部署映射 | URL Origin 必须落在 Session Scope；不是任意导航代理，也不替代容器 Egress Policy |

- P6-01 仅支持单 Actor；Multi-actor、控制权仲裁和并行 Context 延后。
- Playwright Request / WebSocket Route 只能限制浏览器协议层的精确 Origin。生产容器仍必须另外限制 Egress、DNS、UDP 与 WebRTC。
- Evidence Finalization 使用完整 `AssertionResultInput` / `EvidenceArtifactInput` 的 Canonical Digest 与 Report Chain 对账，不以 ID、Count、Content Digest 或部分字段替代完整输入绑定；出现 `execution.blocked` 时同样只能终结为 `INCONCLUSIVE`。

## Fixture 执行对象链

```text
DataAtomDefinition
  -> DataAtomVersion
DataBlueprintDefinition
  -> DataBlueprintVersion
    -> CompiledFixturePlan
      -> FixtureRun
        -> DataNodeRun
          -> DataNodeAttempt
        -> ResourceRecord
        -> FixtureManifest
        -> Cleanup / Reconcile
```

| 对象 | 定义 | 禁止混用或当前状态 |
| --- | --- | --- |
| `DataAtomDefinition` | 一个可维护的数据能力目录实体 | 不是可执行版本；名称和归档状态使用 Revision CAS |
| `DataAtomVersion` | 声明 Input / Output Port、结构化 Connector Operation、Resource、Cleanup 与 Reconcile 的 exact 版本协议 | Published 后不可变；不得包含动态代码、任意 URL / Header 或秘密语义 |
| `DataBlueprintDefinition` | 一个可维护的数据组合目录实体 | 不直接保存运行状态 |
| `DataBlueprintVersion` | 以 exact DataAtom Version、Edge、Literal 与 Export 组成的 DAG | Published 后不可变；不得解析 floating version |
| `CompiledFixturePlan` | 静态编译产生的确定性执行层级、逆序 Cleanup 与完整性 Digest | 不是运行事实，不执行外部 I/O |
| `FixtureRun` | 一次冻结 Compiled Plan、Environment、调用作用域和策略的 Fixture 执行 | P3 已落地；PostgreSQL 是权威事实，Temporal 只负责耐久编排与补偿 |
| `DataNodeRun` | Fixture 中一个逻辑节点的运行记录 | P3 已落地并包含 Reconcile 状态；不等同于正式测试的 `ExecutionUnit` |
| `DataNodeAttempt` | DataNodeRun 的一次 Connector Operation 调用尝试 | P3 已落地且必须先于外部 I/O；Reconcile 使用独立 Attempt，不等同于正式测试的 `UnitAttempt` |
| `ResourceRecord` | Fixture 外部资源的追加式身份、Ownership、状态与 Cleanup 账本 | P3 已落地；只有 CREATED 自动清理，并由 Generation Attempt、Sweeper 与孤儿扫描恢复 |
| `FixtureManifest` | 冻结 Atom Version、Plan Digest 与显式输出的可复现运行清单 | P3 已实现不可变运行持久化；不得导出未声明或敏感输出 |

```text
FixtureRun
  -> DataNodeRun
    -> DataNodeAttempt
```

- Fixture 通过 `ResourceRecord` 与 `accountLeaseId` 接入正式执行，但保持独立的生命周期、Fencing 与幂等键。

## 禁止继续使用的旧称

| 旧称 | 统一名称 | 说明 |
| --- | --- | --- |
| `RunTask` | `ExecutionUnit` | 前者容易与 TaskRun 混淆 |
| `execution item` | `ExecutionUnit` | 统一为领域实体名称 |
| `CaseRun` | `ExecutionUnit` | 逻辑槽位不是一次真实尝试 |
| 裸 `Attempt` | `UnitAttempt` | 文档和 API 必须使用完整名称 |
| `Run` | 按上下文使用 `TaskRun` 或 `UnitAttempt` | 禁止作为无上下文领域实体 |

## 权威边界

- PostgreSQL：资产、租约、运行 Manifest、追加事实、结果、审计和 Outbox 的业务权威。
- Temporal：进行中耐久编排的执行权威，不是业务查询库或最终结果库。
- 对象存储：加密 Session State、Trace、截图、视频和大型 Artifact 内容权威；PostgreSQL 保存引用与完整性摘要。
- SSE、WebSocket、缓存、搜索索引和洞察投影：均可重建，不得反写权威事实。
