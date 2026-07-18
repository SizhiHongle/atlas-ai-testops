# ADR-0009: 正式任务执行宿主与不可变 Run Manifest

- Status: Accepted
- Date: 2026-07-16
- Owners: Atlas Test Space
- Scope: P5-00A 执行宿主；P5-00B1 正式 Profile、Workflow identity、materialization seal 与 Revision CAS；P5-00B2A durable Start Intent 交付；P5-00B2B 有界 Root / Attempt 耐久编排；P5-00D1 immutable Execution Ticket 与 Port 输入边界；P5-00D2A durable TaskRun Cancel；P5-00D2B batch-boundary Pause / Resume；P5-00E1/E2/E3 TaskPlan Catalog、Manual Launch 与统一 Trigger ingress；P5-00E4 100,000-Unit 分区执行；P5-00E5 signed HTTPS production Port

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
15. P5-00B2A 将 Start Intent 交付状态固定为 `PENDING → CLAIMED → RETRY_WAIT / STARTED / FAILED`。Claim 带有短期 Lease、Opaque Token、Dispatcher Identity、递增 Attempt 与 `dispatchRevision`；只有 Lease + Token + Revision 全部匹配的当前 Consumer 才能确认结果，到期 Claim 可被其他 Consumer 接管。确认时间和 Retry `availableAt` 由 PostgreSQL 时钟生成。
16. 跨 Tenant 领取只能由独立 `atlas_dispatcher` 登录角色完成。该角色无 Superuser / `BYPASSRLS`、无 Intent 表级 DML，只获四个 owner-owned `SECURITY DEFINER` 函数的 EXECUTE；API 使用的 `atlas_app` 不获这些权限。Claim 必须指定 exact namespace，并只领取 `TASK_RUN + AtlasTaskRunWorkflow + atlas-task-run`。
17. Consumer 使用三段式边界：短事务 Claim 并提交，事务外 Temporal Start / Describe，再以新短事务 CAS Ack / Retry / Fail，绝不持锁等待网络。Temporal Input 只包含版本、Tenant / Project / Run identity、request digest 与 manifest hash；稳定 `request_id=str(intent.id)`、确定性 Workflow ID、`REJECT_DUPLICATE + USE_EXISTING` 处理不确定重试，但每次仍必须 Describe 并验证 namespace、Workflow Type、Task Queue 与 Memo identity / digest。
18. `STARTED` 只表示 Temporal 接受并可验证该 Workflow identity，不表示 Task 已运行或成功。B2A 本身不注册 no-op / placeholder Workflow；P5-00B2B 在不改变 Intent 线协议的前提下接入真实 Root / Attempt Workflow。
19. P5-00B2B 的初始 `AtlasTaskRunWorkflow` 固定消费 `atlas-task-run`，只接受与 Intent identity / digest 完全一致且已 Seal 的首 Attempt 计划；该切片的同步事实边界与 Workflow 输入限制为最多 64 Units。Root 按固定 8-child batch 启动 `AtlasUnitAttemptWorkflow`，Child 固定消费 `atlas-unit-attempt`。Child Workflow ID 由 Tenant ID + UnitAttempt ID 确定性生成并使用 `REJECT_DUPLICATE`，Workflow caller 不能指定另一身份或 Queue；P5-00E4 在不改变这些 identity / queue 边界的前提下扩展为分页执行。
20. P5-00D1 后 UnitAttempt 执行固定分为 Prepare Ticket DB Activity、Begin DB Activity、Execute side-effect Activity 与 Finish DB Activity；Root 另以短 DB Activity 加载计划和收敛 Run。Prepare 必须先创建或精确重放每 Attempt 唯一的 immutable secret-free Ticket；Ticket 未通过数据库门禁时不能调用 Port。数据库 Activity 每次使用 30 秒边界；瞬时基础设施错误以 1 秒到 60 秒退避耐久重试，确定性 `TaskOrchestrationInvariantError` 转换为安全 non-retryable failure，未知数据库异常只转换为可重试安全码。事务内只复核 / 推进 PostgreSQL 事实，不等待外部 I/O。副作用 Activity 的 Temporal Retry Policy 固定 `maximumAttempts=1`，并持续 Heartbeat、等待取消完成，避免未知提交结果被自动重做或把敏感异常写入 History。
21. 首 Attempt 的 `executionDeadline` 是冻结输入。计划加载以 PostgreSQL 时钟计算安全 timeout，Child 在执行前再以 deterministic Workflow clock 检查；副作用 Activity 的 `scheduleToClose` 覆盖 Queue 排队，`startToClose` 限制实际运行，两者都不得越过 deadline。过期 Attempt 不调用 execution port。
22. Root / Child 使用类型化安全 fallback 收敛异常、取消和不可信结果；Activity 在返回前解码、校验并归一化 payload，任何未知异常只留下稳定安全码。Attempt / Run finalize 追加事件分别冻结 exact status / error code 与 exact status / counts，CLOSED replay 逐字段核对。固定 Workflow identity、durable History、exact-event replay 与 Start collision verification 共同支持 Worker / Consumer 重启恢复。取消只阻止后续 batch，新 Unit 不再启动；原生取消中已开始的副作用一律收敛为 `INCONCLUSIVE` 的未知结果，已完成 Child 不被覆盖也不因同 Root replay 再次执行副作用。
23. `20260716_0025` 增加 tenant-scoped `SECURITY DEFINER atlas.lock_task_execution_chain(...)`。函数要求 Tenant context、sealed 非 legacy Run，并在数据库内按 Run → Unit → Attempt 固定顺序锁定 exact Project / Manifest / Unit identity；`atlas_dispatcher` 与 `PUBLIC` 无执行权，`atlas_app` 仅获受信函数 EXECUTE，仍无 `task_run`、`execution_unit`、`unit_attempt` 的表级状态 UPDATE。
24. P5-00B2B 不创建 `AttemptSeal`，因此 Workflow Payload、execution port 协议和数据库收敛都不包含 `PASSED`。副作用执行成功只表示 `EXECUTED_UNSEALED → FINISHED_UNSEALED`，数据库 Quality 仍为 `INCONCLUSIVE`；失败、歧义、跳过与取消只能收敛为 `FAILED / INCONCLUSIVE / CANCELED`。
25. Intent Consumer 与 Task Worker 是独立进程和开关，均默认关闭。Root / Attempt Worker 使用固定双 Queue 与独立有界并发；仓库不提供 no-op、placeholder 或假 SaaS executor。P5-00E5 之前未配置经评审的 Adapter 时必须在连接 PostgreSQL / Temporal 前 fail-closed；E5 后 CLI 只会从完整签名 HTTPS 配置构造 production Port，部署端 executor 缺失时仍不能启用。
26. 本切片仍不修改 Launch、Task Control 或 Live Theatre 的页面结构、DOM、布局、样式和交互；前端已设计原型继续是唯一权威。
27. `20260717_0027` 的 `TaskUnitExecutionTicket` 每 `UnitAttempt` 唯一，冻结 request / manifest、Unit / Case、四类 Profile、Fixture、Environment revision / allowed origins、deadline 与内容摘要。表使用完整 Scope FK、不可变 Trigger、`FORCE RLS` 和 SELECT / INSERT 最小权限；owner-owned `SECURITY DEFINER` Insert Guard 使用固定 search path，重读并锁定 exact 依赖、当前发布态与 canonical digest。Ticket 不保存账号、Credential、Lease、Session、Token 或 Secret。
28. `TaskUnitExecutionPort` 不再接收裸 `UnitAttemptWorkflowInput`，只接收 `TaskUnitExecutionRequest(attempt, ticketId, ticketDigest)`。这只建立生产 Adapter 的授权输入协议，不代表目标 SaaS、Login Flow、Operation Registry、Secret Provider 或 Vault 已就绪；仓库继续不注册 no-op、placeholder 或假 Adapter。
29. P5-00D2A 首期只接受 TaskRun `CANCEL`。公共 API 必须同时验证 exact Revision ETag、`Idempotency-Key == clientMutationId` 与 `RUN_OPERATOR+`；同一短事务写 immutable command intent、推进 Run 到 `CANCELING` 并追加 Event / Audit / Outbox，事务中不得调用 Temporal。
30. `20260717_0028` 的内部 Command 状态为 `PENDING / CLAIMED / RETRY_WAIT / DELIVERED / APPLIED / FAILED`，公共投影折叠 Claim / Retry 为 `PENDING`。独立 `atlas_dispatcher` 只有 fenced function EXECUTE 权限；Claim 后在事务外 Describe exact Workflow Type / Queue / Memo 并发送 secret-free Signal，再按 Lease + Token + Revision CAS 确认。`NOT_FOUND` 在 Start Intent 尚未完成时属于 transient，不可直接永久失败。
31. Root 按 exact command ID + payload 去重 Signal，停止新 batch并取消 active Child；已完成 Child 结果不可覆盖，未证明完成的副作用保持 `INCONCLUSIVE`。Run 计划额外投影数据库已有的 `cancelRequested`，因此 Cancel 早于 Workflow Start 也不会启动新副作用。
32. Command `APPLIED` 只可在 exact Run 已 `CLOSED / CANCELED` 后写入，并与 Workflow 的 finish transaction 同一原子边界；若 Root 仅凭 plan cancel 先关闭，Dispatcher terminal reconciliation 也必须先重读该终态。Pause / Resume / automatic retry / manual rerun / Takeover 需要独立状态语义和安全点协议，不从 Cancel 行为类推。
33. P5-00D2B 将 Task Pause 定义为“暂停新 Unit 派发”，不是 Browser Action Safe Point、Human Takeover 或运行中副作用冻结。Root 在每个最多 8 个 Child 的批次前以单一事务为整批创建 immutable Ticket；事务提交后的批次是不可追加的预授权集合。
34. Pause 只接受 `RUNNING → PAUSE_REQUESTED`。当前预授权批次不取消；Root 等待全部 Child 收敛后调用数据库 checkpoint，以同一事务完成 `PAUSE_REQUESTED → PAUSED`、追加 `task_run.paused` Event 并把 exact Pause command 标记 `APPLIED`。随后使用 Temporal durable `wait_condition`，不持有数据库连接。
35. Resume 只接受 `PAUSED`。API 接受时保持 `PAUSED` 并推进 Revision；Root 只有在收到 exact Resume Signal 后，才能通过 checkpoint 同事务完成 `PAUSED → RUNNING`、追加 `task_run.resumed` Event 与 command `APPLIED`，随后才准备下一批。
36. `CANCEL` 从 `PAUSE_REQUESTED / PAUSED` 抢占控制权；接受事务在推进 `CANCELING` 后，把未完成 `PAUSE / RESUME` 置为 `SUPERSEDED` 并记录 superseding Cancel ID。迟到 Signal 由 Root 的 Cancel 状态和数据库 checkpoint fail-closed。
37. `atlas.task-run-command/0.2` 支持 `CANCEL / PAUSE / RESUME`，并兼容读取和投递历史 `0.1` Cancel；旧 schema 不得承载 Pause / Resume。
38. P5-00D3A 的自动 retry 只对 frozen policy 允许的明确 `INFRA_ERROR` 追加 gapless 新 UnitAttempt；旧 Attempt、原始 deadline 与逻辑 Unit identity 不覆盖。
39. P5-00D3B 的 manual infra-failure rerun 不属于 command 或 Workflow Signal。它只接受 `SEALED / CLOSED` parent，创建不可变 `rerunOfTaskRunId + INFRA_FAILURES` child Run，并使用全新 Run / Unit / Attempt / Temporal identity。
40. `20260717_0031` 的数据库 Manifest Guard 必须从 parent 当前最终事实重算每个且仅有 `CLOSED / INFRA_ERROR` 的 Unit，并证明 Plan、schema、iteration、policies、retry policy 与 compiler 不漂移；客户端不能提供或缩减选择列表。
41. P5-00E1 将 TaskPlan 编写定义为稳定 Catalog 根加 append-only published Version，不引入可变 TaskPlan Draft。`RUN_OPERATOR+` 可以创建与发布，但每次写入必须有可审计 Actor、`Idempotency-Key == clientMutationId`，并与 Audit / Outbox 同事务提交。
42. TaskPlanVersion 公共发布请求只携带 exact Case / Profile / Fixture / Environment / Policy 引用；应用层生成 ID、时间、`versionRef` 与 canonical digest，PostgreSQL Guard 仍是同作用域和发布态的最终事实门禁。E1 不创建 Profile、不物化 TaskRun，也不把发布描述为已启动。
43. P5-00E2 的 Manual Launch 只从 exact published TaskPlanVersion 编译。Identity 必须与 Unit CaseVersion 兼容，Data 必须与该 Case Profile 的 Fixture Blueprint 兼容；Environment 与 Browser 才可跨轴组合。编译后超过 64 Units 或任一 Case 缺少兼容 Profile 时 fail-closed。
44. P5-00E3 的统一 Trigger 入口不接受执行配置覆盖。Schedule、CI 与 Webhook 分别用 `scheduleId + scheduledFireTimeUtc`、`provider + pipelineRunId + jobId + rerunIndex`、`sourceKey + deliveryId` 生成永久 Fingerprint；展示元数据不能改变同一外部事件的逻辑身份。
45. 所有 Trigger 都复用同一个 compatible-only compiler、Manifest、materialization Seal、Start Intent、Event、Audit、Outbox 与 database-backed idempotency 事务。HTTP Idempotency-Key 只保护传输重试，不能为同一永久 Trigger 身份创建第二个 TaskRun。
46. Manual Launch 请求的完整 `TaskRetryPolicy` 必须匹配 Plan Version 冻结的 `infra-retry` digest。稳定 trigger fingerprint 绑定 Plan Version 与 `clientMutationId`；Manifest v0.2、Run / Unit / 首 Attempt、15 分钟 execution window、Seal、Start Intent、Event、Audit / Outbox 与幂等完成在同一短事务提交。
47. P5-00E4 将 Manifest 协议与数据库硬上限兑现到 100,000 Units。超过 64 Units 的 Run 使用 fenced materialization partition，每页最多 64 Units；只有当前页无 active Child、无未结算 batch 且全部结果已持久化时才 Continue-As-New。最终页必须从 PostgreSQL 投影验证全部 Unit / Attempt / Finalization Event 后关闭，Cancel 必须排空当前页并从数据库恢复未应用 command。
48. P5-00E5 的 `HttpTaskUnitExecutionPort` 只通过固定内部 Path 发送 secret-free Ticket scope。Request / Response HMAC 双向绑定 Worker、Tenant、Attempt、Ticket、时间窗、Nonce、Body digest、HTTP status 与 Attempt Idempotency-Key；生产强制 HTTPS，禁止 redirect、环境代理和 transport retry，响应必须 `no-store`、JSON 且有界。传输或响应歧义只能收敛为 `OUTCOME_UNKNOWN`，不能自动重做副作用；数据库 Finish Activity 仍是 ResultRef / AttemptSeal 的最终权威。

## 后果

- 任意 UnitAttempt 都能精确反向定位 ExecutionUnit、TaskRun、TaskPlanVersion 与 manifestHash。
- CLOSED 后只允许 Hygiene 沿 `PENDING → RUNNING → CLEANED/CLEANUP_FAILED/LEAKED` 及 `CLEANUP_FAILED → RUNNING/LEAKED` 单调推进；Lifecycle、Quality、身份与既有里程碑不可回写。Cleanup 事件继续追加并匹配最窄 Scope 的当前三轴状态。
- P5-00B1 已补齐四类正式版本宿主、发布门禁、同作用域 FK、stable request digest、Temporal identity registry、同步 materialization seal 与 Revision CAS；缺失或漂移依赖继续 fail-closed。
- P5-00E4 已为 65–100,000 Units 提供可恢复分区物化、fenced checkpoint、分页 Root 与安全 Continue-As-New；不超过 64 Units 保留同步快路径。100,000 是协议与数据库硬上限，不等于未经 P9 容量门禁即可无限放大并发。
- Schedule、CI、Webhook 与公共 API 接入必须复用 stable request digest / insert-or-get 协议；不能把服务端生成的 Run ID 与时间当作同一 triggerFingerprint 的幂等身份。
- P5-00B2A 已落地 Pending Start Intent 的 Claim / Lease / Retry / Started / Failed 状态机、Temporal Consumer 和到期 Claim 恢复；稳定 Request ID 与 collision verification 覆盖 Start 成功但 Ack 前崩溃。该能力只证明交付，不把 `STARTED` 伪装成 Workflow 已完成或 Task 已成功。
- P5-00B2B 已落地最多 64 Units 的真实 Task Root / UnitAttempt Workflow、固定双 Queue、8-child batch、deadline、短数据库 Activity、瞬时故障耐久重试、单次副作用与 exact-event replay。真实 PostgreSQL 已验证 tenant scope、Run → Unit → Attempt 锁、Revision CAS、最小权限和 migration 往返；真实 Temporal 已验证双 Worker、跨 batch、deterministic child ID、deadline 排队、三次数据库瞬时故障后恢复、同 Root replay、History 脱敏、非 PASS 收敛、原生 Child 取消的未知结果与 Root 取消后已完成 Child 结果保留。
- P5-00C 已补充 TaskRun / Manifest / Unit / Attempt / Event 查询；P5-00D1 已落地 immutable Execution Ticket、Prepare Activity 与 ticket-bound Port Protocol；P5-00D2A/D2B 已落地 durable Cancel、batch-boundary Pause / Resume、Signal delivery、Cancel 抢占与 command status；P5-00D3A/D3B 已分别落地 automatic infra retry 与创建新 child Run 的 manual infra-failure rerun；P5-00E1/E2/E3 已落地 TaskPlan Catalog、不可变版本发布、Manual Launch 与统一 Schedule / CI / Webhook Trigger ingress；P5-00E4/E5 已补齐 100,000-Unit 分区执行与 signed HTTPS production Port，E6/E7 已补齐数据库权威 Temporal Schedule 与 signed Gate Callback。Intent Consumer、Task Worker、Schedule Worker 与 Callback Consumer 保持默认关闭；部署仍须提供真实 SaaS executor、Callback Receiver endpoint/key 与运行凭据。
- 同一 Trigger 的重复提交可由数据库唯一约束和后续应用幂等协议收敛为一个逻辑 TaskRun。
- 任务重跑、失败项重跑和用例重试不会改写历史结果，为后续 Result Snapshot、Gate 和 Flaky 解释保留完整事实。
- DebugRun 的 ExecutionContract、EvidenceManifest 与 Live Cursor 继续保持原协议；正式 Attempt 已使用独立 AttemptSeal 和 Live Control 协议，避免可空多态宿主。
- P6-02B2 已按 [ADR-0010](ADR-0010-unit-attempt-live-control.md) 把 ControlLease、Epoch / Fence 与 ActionGrant 绑定到明确 UnitAttempt，不依赖 UI 投影或客户端自报控制权。
