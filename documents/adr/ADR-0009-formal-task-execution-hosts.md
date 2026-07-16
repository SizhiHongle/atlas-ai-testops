# ADR-0009: 正式任务执行宿主与不可变 Run Manifest

- Status: Accepted
- Date: 2026-07-16
- Owners: Atlas Test Space
- Scope: P5-00A 执行宿主；P5-00B1 正式 Profile、Workflow identity、materialization seal 与 Revision CAS

## 背景

P4 已建立作者态 `WorkflowDraft`、不可变 `DebugRun` 和已发布 `CaseVersion`，P6 也已为 DebugRun 建立受信 Browser 执行、Evidence 与只读 Live 观察链。但是正式批量执行不能以 DebugRun 作为宿主：DebugRun 绑定 Draft 语义且只服务发布前试运行，正式任务必须引用已发布的 exact CaseVersion，并且每次重试都要保留独立的物理执行历史。

P6-02B2 的 `LiveSession`、`ControlLease`、控制 Epoch / Fence 和持久化 `ActionGrant` 都必须精确绑定一个 `UnitAttempt`。在 P5 建立正式宿主前提前把这些事实挂到 DebugRun，会形成多态外键、错误的结果归属和无法解释的重试历史。

## 决策

1. 正式运行使用 `TaskPlanVersion → TaskRun → ExecutionUnit → UnitAttempt` 四层对象链。四层对象拥有独立 ID，不以名称替换或复用 DebugRun。
2. `TaskPlanVersion` 是已发布、不可变的任务方案快照。P5-00A 先支持结构化 pinned CaseVersion、矩阵 Profile 引用和 Policy Digest；Query-at-run、Schedule、CI Trigger 与复杂选择器在后续切片扩展。
3. 每个 TaskRun 由 `triggerSource + triggerFingerprint` 在 Tenant 内唯一标识，并绑定一份 `atlas.task-run-manifest/0.1`。Manifest 冻结 CaseVersion、Environment、Fixture、Browser、Identity、Data Profile 与 Policy Digest 的精确 ID 值，运行开始后不得修改或重新解析。Repository 与 PostgreSQL Trigger 都会加载 exact PlanVersion，要求 Manifest Policy 覆盖 Plan Policy 的全部同值键（允许增加编译后 resolved digest），每个 Unit 的 Case、四个矩阵轴以及 Execution Profile / Fixture Profile 都来源于该 PlanVersion；当前协议不臆造完整笛卡尔积。P5-00B1 要求四类 Profile 正式存在、同作用域、PUBLISHED 且与 exact Case / Fixture 兼容；DebugRun-scoped `ExecutionContract` 不得冒充 `ExecutionProfileVersion`。
4. ExecutionUnit 对应 Manifest 中一个稳定 `unitKey`，并精确绑定 CaseVersion 和矩阵单元。Unit 的身份字段不可变，运行状态使用独立的 Lifecycle、Quality 与 Hygiene 三轴。
5. UnitAttempt 表示一次物理执行。用例重跑必须创建递增 `attemptNumber` 的新 UnitAttempt；Activity 瞬时重试仍属于同一个 Attempt。旧 Attempt 不得覆盖、删除或复活。首个 Attempt 随 `MATERIALIZING` 聚合创建；后续 Attempt 必须沿用父 Run namespace，且只允许在 SEALED、可派发的 Run / Unit 下接续一个已 CLOSED、结果可重试的前序 Attempt。
6. 三轴状态遵循任务中心冻结协议：Lifecycle 只描述流程位置，Quality 只描述 Oracle / 依赖结论，Hygiene 只描述资源清理。结果可以先关闭而 Cleanup 继续推进，因此 `CLOSED + PASSED + PENDING/RUNNING/CLEANUP_FAILED` 与 `cleanupResolvedAt > closedAt` 都是合法事实；`CLOSED + PASSED + LEAKED` 也是有效但不能被严格 Gate 接受的组合。CLEANUP_FAILED 可进入下一次长期 Cleanup retry，只有 CLEANED、LEAKED 与 NOT_REQUIRED 终结当前投影。
7. PostgreSQL 是 Manifest、对象归属、三轴状态和追加事件的事实源。Temporal 在 P5-00B 后承载耐久编排，但不能成为产品查询、最终统计或证据事实源。
8. 所有主外键携带 Tenant / Project Scope，核心表启用并强制 RLS。应用角色没有 DELETE 权限；Published PlanVersion、RunManifest 和任务事件使用数据库 Trigger 阻止修改。结构化 JSONB 约束按 exact key set 校验，并对缺键、SQL `NULL`、JSON `null` 和嵌套 validator 的未知结果 fail-closed。
9. 事务只包含数据库验证和事实写入。Temporal、HTTP、Playwright、SSE 等网络或长时操作必须在提交后执行，不得持有 PostgreSQL 行锁等待外部系统。P5-00A 按设计稿 P1 的小批次边界，单次初始同步物化最多 64 Units；机器协议中的 100,000 上限是未来分区化能力的结构安全上限，不是当前生产 SLO。
10. P5-00A 不创建 `AttemptSeal`、`LiveSession`、`BrowserSession`、`ControlLease` 或持久化 `ActionGrant`。这些对象只能在正式 UnitAttempt 宿主和执行链就绪后逐步加入。
11. 前端原型继续作为页面结构、DOM、布局、样式和交互的唯一权威。本切片只提供后端宿主与机器契约，不修改 Launch、Task Control 或 Live Theatre 原型。
12. P5-00B1 为 logical Run input 计算不含服务端 Run ID / 时间的 stable request digest；Run / Attempt Workflow ID 由 Tenant ID 与对象 ID 确定性生成，并在 `(namespace, workflowId)` Registry 中跨 owner 统一占位。同一 trigger 自然键只有 request digest 和不可变 `rerunOfTaskRunId` lineage 都相同才可 replay。
13. 新 Run 从 `MATERIALIZING` 开始。数据库 Seal 必须重算 digest、证明全部 Unit 和首个 Attempt 完整、重验可变依赖，然后才切换 `SEALED` 并在同一事务追加唯一 `PENDING` Workflow Start Intent。Intent 不是 Temporal 已启动的声明，B1 不消费它。
14. 三轴状态只通过数据库拥有的 expected Revision CAS 函数推进，锁序固定为 Run → Unit → Attempt；未 Seal 或 `legacy_unsealed` 的 Run 不能推进。应用角色不再直接 UPDATE 状态列。未来 dispatcher 的 Admission 在同一短事务内重读父 Run 与 Unit，只接受 SEALED 且处于 QUEUED / RUNNING 的 Run，以及仍为 QUEUED 的 Unit；Pause / Cancel / Finalize / Closed 状态全部 fail-closed。

## 后果

- 任意 UnitAttempt 都能精确反向定位 ExecutionUnit、TaskRun、TaskPlanVersion 与 manifestHash。
- CLOSED 后只允许 Hygiene 沿 `PENDING → RUNNING → CLEANED/CLEANUP_FAILED/LEAKED` 及 `CLEANUP_FAILED → RUNNING/LEAKED` 单调推进；Lifecycle、Quality、身份与既有里程碑不可回写。Cleanup 事件继续追加并匹配最窄 Scope 的当前三轴状态。
- P5-00B1 已补齐四类正式版本宿主、发布门禁、同作用域 FK、stable request digest、Temporal identity registry、同步 materialization seal 与 Revision CAS；缺失或漂移依赖继续 fail-closed。
- 超过 64 Units 的 Manifest 仍必须在后续提供可恢复分区物化、checkpoint / resume 与容量验证；B1 的 Seal 只证明当前有界同步聚合，不能被解释为大批次能力。
- Schedule、CI、Webhook 与公共 API 接入必须复用 stable request digest / insert-or-get 协议；不能把服务端生成的 Run ID 与时间当作同一 triggerFingerprint 的幂等身份。
- Pending Start Intent 的 Claim / Lease / Retry / Started / Failed 状态机、Temporal Consumer 和恢复扫描属于后续切片；在这些能力落地前不会把意图事实伪装成 Workflow 已启动。
- 同一 Trigger 的重复提交可由数据库唯一约束和后续应用幂等协议收敛为一个逻辑 TaskRun。
- 任务重跑、失败项重跑和用例重试不会改写历史结果，为后续 Result Snapshot、Gate 和 Flaky 解释保留完整事实。
- DebugRun 的 ExecutionContract、EvidenceManifest 与 Live Cursor 继续保持原协议；正式 Attempt 将使用独立 Contract、AttemptSeal 和 Live 协议，避免可空多态宿主。
- P6-02B2 可以在后续切片把 ControlLease、Epoch / Fence 与 ActionGrant 绑定到明确 UnitAttempt，而不依赖 UI 投影或客户端自报控制权。
