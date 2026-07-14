# Atlas 统一领域术语

更新时间：2026-07-14

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
| `TaskPlanVersion` | 已发布的任务选择、矩阵、触发和策略 | 发布后不可变，PostgreSQL |
| `TaskRun` | 一次触发形成的任务运行与冻结 Manifest | 追加式状态，PostgreSQL；Temporal 承载耐久编排 |
| `ExecutionUnit` | CaseVersion 与矩阵单元形成的逻辑测试槽位 | 创建后不可变，PostgreSQL |
| `UnitAttempt` | ExecutionUnit 的一次真实执行 | 每次重新执行创建新的 UnitAttempt，PostgreSQL + Temporal |
| `AttemptSeal` | Attempt 关闭时产生的不可变事实包 | 永久不可变，PostgreSQL 保存事实与对象存储引用 |
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
