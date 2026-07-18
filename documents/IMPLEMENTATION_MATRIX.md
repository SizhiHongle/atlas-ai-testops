# Atlas AI 测试平台实施矩阵

更新时间：2026-07-18

状态含义：`未开始`、`基础中`、`后端完成`、`前端完成`、`已验收`。只有数据库、领域、API、前端和测试证据全部存在时才使用 `已验收`。

| 领域 | 权威设计 | 主要数据库对象 | API 范围 | 前端范围 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| Platform | 总体落地方案 | tenant、project、environment、platform_user、password_credential、platform_membership、platform_session、audit、outbox、idempotency | auth、session、projects、environments | 既有 Login、Space Header | 已验收：P1 真实数据库、API、RBAC 与浏览器 QA 完成 |
| Workflow Contract | AI 用例 v0.3 | workflow_draft、workflow_node、workflow_edge、draft_operation、debug_run、debug_run_event、case_version、case_version_node、case_version_edge、execution_contract、browser_runtime_report | draft validate / patch / layout、debug-runs、events、cancel、publish、versions；Browser Runtime 内部协议 | Case Canvas、Debug、Publish | 后端完成：P4-00 至 P4-03、P6-00 与 P6-01 已落地双 Revision、不可变 DebugRun、ExecutionContract、报告链和精确发布闭环 |
| Identity | 身份与测试账号 v1.1 | connector_installation、connector_capability、test_role、account_pool、test_account、account_slot、account_lease、credential_binding、secret_grant、account_health_check、account_state_transition、browser_session_artifact、auth_action_ticket、environment.allowed_origins | connectors、capability validation、roles、pools、accounts、leases、health verification / history、secret grants、ensure-session | Identities | 基础中：P2-01 至 P2-06 已验收；真实 SaaS Flow、生产 Secret/KMS 与 ExecutionIdentityGrant 延后接入 |
| Fixture | 数据预加载 v0.2 | data_atom_definition/version、data_blueprint_definition/version、fixture_run、fixture_actor_binding、data_node_run/attempt、data_node_reconcile_attempt、resource_record/dependency、resource_cleanup_attempt、fixture_manifest、fixture_validation_evidence | data-atoms、data-blueprints、validate、compile、publish、fixture-runs、manifest、resources、release、cancel、retry-cleanup、cleanup sweep | 既有 Atoms、Assets 数据槽位 | 已验收：P3-00 至 P3-03 的资产、耐久运行、取消补偿、Reconcile、Cleanup Retry / Sweeper 与发布证据闭环 |
| Case | AI 用例 v0.3 | test_case、workflow_draft、workflow_node、workflow_edge、draft_operation、debug_run、debug_run_event、case_version、case_version_node、case_version_edge | test-cases、workflow-draft、patch validate / apply、layout、debug-runs、events、cancel、publish、versions | Cases、Case Canvas、Debug、Publish | 后端完成：P4-00 至 P4-03 已验收；前端待按既有原型槽位接入真实状态 |
| Task | 任务中心 v0.2 | 四类 profile_version、task_plan/version、task_schedule、task_schedule_sync_intent、task_run、task_run_manifest、execution_unit、unit_attempt、task_run_event、workflow identity registry、durable intents、`20260716_0025` execution-chain lock 至 `20260718_0043` Schedule Catalog | P5-00B1/B2 契约与 Workflow；P5-00C 查询；P5-00D1/D2/D3 Ticket、Control、Retry/Rerun；P5-00E1/E2 Catalog/Launch；P5-00E3 unified Trigger；P5-00E4 100,000-Unit partition；P5-00E5 signed HTTPS Port；P5-00E6 database-authoritative Temporal Schedule；P6-02B2 Takeover；P6-03 Result projection | Launch、Task Control 原型保持不变；Live 既有槽位映射真实 Snapshot 与 Takeover / Return | 基础中：100,000-Unit、可恢复分区 / 分页 Workflow、只读查询、immutable Ticket、可靠控制 / retry / rerun、TaskPlan、统一 Trigger、Temporal Schedule Catalog / Sync / Fire、UnitAttempt Takeover、双向签名 HTTPS production Port 已验收。Worker / Consumer 默认关闭；部署端真实 SaaS executor 与签名回调尚未实现 |
| Live / Browser | 现场 v0.2 | debug_run、debug_run_event、execution_contract、browser_runtime_report、live_session、control_lease、live_control_command、live_action_grant、live_control_event | Permit + HMAC Browser Runtime 与 UnitAttempt Live Control 内部协议；DebugRun Live Snapshot / SSE；UnitAttempt Snapshot、异步 Pause / Resume / Takeover / Return、Heartbeat / Reaper 和单次 Grant | Live Theatre 既有槽位映射真实控制状态与接管/交还 | 后端完成：P6-02B1 已实现 DebugRun-scoped 只读 Live Snapshot / SSE；P6-02B2 已实现 exact UnitAttempt LiveSession、ControlLease、单调 Epoch/Fence、Safe Point、Human Takeover / Return、TTL 回收、持久化 ActionGrant、Production fail-closed 与人工影响 Seal guard。前端未改 DOM、布局、样式或 className |
| Evidence | 现场与结果 v0.2 | assertion_result、evidence_artifact、evidence_manifest、browser_runtime_report、evidence_read_grant、unit_attempt_result_fact | 内部报告链与受信终结、Manifest、scoped read-token、完整字节读取；annotations 待后续 | Live Evidence、Result Evidence | 基础中：P6-02A DOM Mask、canonical PNG、write / read-back verification、hash-only Read Grant 与二次完整性校验已验收；P6-03A 已建立正式 UnitAttempt 的签名 AttemptSeal Fact |
| Result | 结果中心 v0.2 | unit_attempt_result_fact、result_ref、result_integrity_incident、attempt_closure_notice、unit_resolution_revision、task_result_snapshot、attempt_fixture_binding、unit_hygiene_resolution_revision、task_result_reevaluation_command、failure_cluster_revision、failure_classification_revision、task_gate_decision | Result Snapshot / Unit Resolution / Cluster 查询、Classification Revision、Gate Evaluation | 既有 Results 槽位映射真实 TaskRun、Snapshot、Gate、Cluster 与 Classification | 已验收：P6-03A/P6-03B 与 P7-01A 至 P7-03 已形成可信 Result 链；Gate fail-closed、查询固定 Snapshot fence、API/ETag/OpenAPI 与前端回退接线均已验证，未改原型结构 |
| Insight | 洞察中心 v0.2 | insight_snapshot | 30 天为默认的 7/30/90 UTC brief preview、immutable snapshot pin / exact read | 既有 Insights terrain、指标与风险任务槽位 | 基础中：P8 V1 已实现固定 MetricDefinition、qualityFinalizedAt 归窗、ratio-of-sums、current/baseline、DatasetCut、Gate 风险信号、不可变 Snapshot、ETag 与真实前端映射；Projector generation、Signal/Review 与异步 Export 仍待后续扩展 |

## 第一批机器可读契约

| 契约 | Schema Version | 实现位置 | 状态 |
| --- | --- | --- | --- |
| Workflow Graph | `atlas.workflow-graph/0.1` | `backend/src/atlas_testops/domain/workflow` | 已实现并导出 |
| Workflow Draft | `atlas.workflow-draft/0.1` | `backend/src/atlas_testops/domain/workflow`、`domain/case` | 已实现并由 P4-01 持久化/API 使用 |
| Workflow Patch | `atlas.workflow-patch/0.1` | `backend/src/atlas_testops/domain/case` | 已实现并导出；AI / 人工共用原子 Patch |
| Test Intent | `atlas.test-intent/0.1` | `backend/src/atlas_testops/domain/case` | 已实现并导出 |
| Domain Event | `atlas.domain-event/0.1` | `backend/src/atlas_testops/domain/events.py` | 已实现并导出 |
| Atom Contract | `atlas.atom/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Fixture Blueprint | `atlas.fixture-blueprint/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Compiled Fixture Plan | `atlas.compiled-fixture-plan/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Fixture Manifest | `atlas.fixture-manifest/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现、导出并由 P3 耐久运行持久化 |
| Test IR | `atlas.test-ir/0.2` | `backend/src/atlas_testops/domain/case` | 已实现、导出并通过确定性编译测试 |
| Plan Template | `atlas.plan-template/0.1` | `backend/src/atlas_testops/domain/case` | 已实现、导出并由 P4-02 DebugRun 不可变持久化 |
| Case Version | `atlas.case-version/0.1` | `backend/src/atlas_testops/domain/case` | 已实现、导出并由 P4-03 精确发布事务不可变持久化 |
| Execution Contract | `atlas.execution-contract/0.1` | `backend/src/atlas_testops/domain/runtime` | 已实现、导出并由 P6-00 精确绑定 DebugRun / Fixture / Actor / Runtime 版本 |
| Assertion Result | `atlas.assertion-result/0.1` | `backend/src/atlas_testops/domain/runtime` | 已实现、导出并由冻结 Test IR 的确定性 Oracle 生成 |
| Evidence Manifest | `atlas.evidence-manifest/0.1` | `backend/src/atlas_testops/domain/runtime` | 已实现、导出并由 P6-00 不可变持久化；数据库重新验证结果推导 |
| Browser Execution Bundle | `atlas.browser-execution-bundle/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-01 已实现；冻结执行视图并携带加密 BrowserContext Restore Envelope |
| Browser Runtime Report | `atlas.browser-runtime-report/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-01 已实现；类型化 Payload、单调 Sequence、Previous Digest 与 Content Digest 形成追加链 |
| Debug Live Cursor | `atlas.debug-live-cursor/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-02B1 已实现；Opaque Base64URL Cursor 精确绑定 DebugRun 与 `afterSeq` |
| Debug Live Run Projection | `atlas.debug-live-run-projection/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-02B1 已实现；只包含 Live UI 所需的安全 DebugRun 状态 |
| Debug Live Event | `atlas.debug-live-event/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-02B1 已实现；event-type allowlist 投影，不原样转发事实 Payload |
| Debug Live Snapshot | `atlas.debug-live-snapshot/0.1` | `backend/src/atlas_testops/domain/runtime` | P6-02B1 已实现；单 SQL 只构造轻量 Run Projection，并与 latest event / head Cursor 形成一致快照，不物化完整 Test IR / PlanTemplate |
| Execution Profile | `atlas.execution-profile/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已实现并导出；冻结 exact Case / Model / Prompt / Tool / Feature 预绑定 |
| Identity Profile | `atlas.identity-profile/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已实现并导出；冻结 Case actor / TestRole snapshot，不含账号或秘密 |
| Browser Profile | `atlas.browser-profile/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已实现并导出；冻结 Chromium revision、Viewport、Locale、Timezone 与 attestation digest |
| Data Profile | `atlas.data-profile/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已实现并导出；冻结 exact Fixture Plan 与无秘密 Run Inputs |
| Task Plan Version | `atlas.task-plan/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已接入四类正式 Profile 宿主、发布态与同作用域门禁 |
| Task Run Manifest | `atlas.task-run-manifest/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 使用 `executionProfileVersionId`；完整 Unit 集、Manifest Hash 与 stable request digest 可由 PostgreSQL 重算 |
| Task Run | `atlas.task-run/0.1` | `backend/src/atlas_testops/domain/task` | P5-00D3B 已增加不可变 `rerunOfTaskRunId + rerunSelectionMode=INFRA_FAILURES`；child 仍经过完整 `MATERIALIZING → SEALED`、stable request digest 与确定性 Run Workflow identity |
| Task Run Trigger | `atlas.task-run-trigger/0.1` | `backend/src/atlas_testops/domain/task` | P5-00E3 已实现；Schedule、CI 与 Webhook 使用各自永久事件身份生成 Trigger Fingerprint，并复用 exact TaskPlanVersion 的编译、Manifest、Seal、Start Intent 与幂等事实链；展示元数据不具备执行配置权威性 |
| Task Schedule | `atlas.task-schedule/0.1` | `backend/src/atlas_testops/domain/task/schedules.py` | P5-00E6 已实现并导出创建/投影 Schema；数据库权威 desired state、结构化 Calendar、IANA Timezone、DST、Overlap/Catchup/Jitter、未来五次 fire、Temporal exact sync 与统一 Schedule TaskRun fire |
| Execution Unit | `atlas.execution-unit/0.1` | `backend/src/atlas_testops/domain/task` | P5-00A 已导出；冻结 Manifest 中一个 exact CaseVersion × Matrix Cell 逻辑执行槽位 |
| Unit Attempt | `atlas.unit-attempt/0.1` | `backend/src/atlas_testops/domain/task` | P5-00D3A 对显式 `INFRA_ERROR` 按冻结策略追加确定性、gapless 新 Attempt；Assertion / 产品失败与 `OUTCOME_UNKNOWN` 不自动重试；P6-03A 后只有 exact AttemptSeal Fact 才能产生 `PASSED` |
| Task Unit Execution Ticket | `atlas.task-unit-execution-ticket/0.1` | `backend/src/atlas_testops/domain/task` | P5-00D1 已实现并导出；每 UnitAttempt 唯一、不可变、secret-free，Port 只接收其 ID / digest，不包含动态身份或凭据 |
| Task Run Command | `atlas.task-run-command/0.2`（兼容 `0.1` Cancel） | `backend/src/atlas_testops/domain/task` | P5-00D2A/D2B 已实现并导出；支持 exact TaskRun Revision 的 durable `CANCEL / PAUSE / RESUME`，公共投影不暴露 Claim Token / Dispatcher identity，并以 `SUPERSEDED` 表达 Cancel 对未完成 Pause / Resume 的抢占 |
| Execution Event | `atlas.execution-event/0.1` | `backend/src/atlas_testops/domain/task` | P5-00A 已实现并导出；PostgreSQL 追加式无间隙 replay |
| Attempt Seal | `attempt-seal/1.0` | `backend/src/atlas_testops/domain/result` | P6-03A 已实现并导出；Ed25519 签名、canonical hash、Ticket / Policy / Runtime / Evidence / Event Chain exact binding |
| Result Ref | `atlas.result-ref/0.1` | `backend/src/atlas_testops/domain/result` | P6-03A 已实现并导出；同一 Attempt + 同一 digest 稳定 replay，不同有效 digest 追加 Integrity Incident |
| Attempt Closure Notice | `atlas.attempt-closure-notice/0.1` | `backend/src/atlas_testops/domain/result` | P6-03B 已实现并导出；只覆盖无 Seal CLOSED Attempt，只能表达 `INCONCLUSIVE / NOT_EVALUATED` |
| Unit Resolution Revision | `atlas.unit-resolution-revision/0.1` | `backend/src/atlas_testops/domain/result` | P6-03B 已实现并导出；追加绑定全部终态事实、固定解析策略、decisive Attempt 与 Stability |
| Task Result Snapshot | `atlas.task-result-snapshot/0.1 / 0.2 / 0.3` | `backend/src/atlas_testops/domain/result` | P7-01A 的 0.1 `QUALITY_FINAL` 与 P7-01B1 的 0.2 `FULLY_RESOLVED` 保持兼容；P7-01B2 的 0.3 `REEVALUATED` 绑定 exact Full 源与显式命令，复制冻结输入与聚合输出并切换到新 Policy，不改写旧 Snapshot |
| Task Result Reevaluation Command | `atlas.task-result-reevaluation-command/0.1` | `backend/src/atlas_testops/domain/result` | P7-01B2 已实现并导出；绑定 `clientMutationId`、exact `FULLY_RESOLVED` 源 Snapshot 与目标 Policy，只有显式内部应用命令可创建 |
| Failure Cluster Revision | `atlas.failure-cluster-revision/0.1` | `backend/src/atlas_testops/domain/result` | P7-02A 已实现并导出；绑定 exact Snapshot、manifest-ordered 完整同信号 UnitResolution 集合与冻结 fingerprint Policy |
| Failure Classification Revision | `atlas.failure-classification-revision/0.1` | `backend/src/atlas_testops/domain/result` | P7-02A 已实现并导出；绑定 exact Cluster Revision、typed Evidence Ref、basis-point confidence、author / judgment 与 append-only human review；不改变 Verdict |
| Task Gate Decision | `atlas.task-gate-decision/0.1` | `backend/src/atlas_testops/domain/result` | P7-02B 已实现并导出；绑定 exact Snapshot、完整 current Cluster / Classification 集合，三值结论默认 fail-closed |
| Attempt Fixture Binding | `atlas.attempt-fixture-binding/0.1` | `backend/src/atlas_testops/domain/result` | P7-01B0 已实现并导出；绑定 exact UnitAttempt / FixtureRun scope、Environment、Blueprint、Compiled Plan 与 execution identity |
| Unit Hygiene Resolution Revision | `atlas.unit-hygiene-resolution-revision/0.1` | `backend/src/atlas_testops/domain/result` | P7-01B0 已实现并导出；冻结全部 Attempt 的 Fixture cleanup / Resource / Reconcile 输入、策略、水位和最严重 Hygiene |
| Insight Snapshot | `atlas.insight-snapshot/0.1` | `backend/src/atlas_testops/domain/insight` | P8 V1 已实现并导出；固定 Metric catalog、相邻窗口、exact Result/Gate source set、query/auth scope hash、watermark 与 semantic snapshot hash |

## 跨领域验收矩阵

| 不变量 | 最低证明方式 | 计划阶段 |
| --- | --- | --- |
| Tenant 数据不可越权 | 两个 Tenant 的真实 PostgreSQL RLS 集成测试 | P1 |
| Account Slot 不重复租用 | P2：100 并发单轮与管理对撞；P9：100 并发 × 100 轮 | P2 / P9 |
| 旧 Worker 无法继续写入 | Heartbeat、Release、TTL、管理撤销与新 Lease 的 fencing token 测试 | P2 |
| Secret Grant 不可重放且不泄密 | 20 路并发兑换、Hash-only 存储、Origin / Worker / Fence、事件与持久化秘密扫描 | P2 |
| Adapter 无法读取或返回秘密定位信息 | `AdapterContext.with_password_secret(...)` 合约测试；无 `getSecret`、SecretRef 或 SecretVersion | P2 |
| Connector 验证不覆盖并发配置 | 事务外 Probe + Revision CAS；单连接池内并发 Revision 更新返回 412 | P2 |
| Connector 失效后身份链立即失效 | ACTIVE 状态 / Capability / Origin 复核；Lease Fence 与未消费 Grant 级联撤销 | P2 |
| 未验证账号不能进入可用池 | `HEALTHY` 验证证据数据库约束、身份 / 角色探针、失败阈值与 Connector 失效回退测试 | P2 |
| 浏览器登录状态不进入控制面或明文存储 | API 安全投影、Audit / Outbox 秘密扫描、AES-256-GCM + AAD、真实 MinIO 密文检查 | P2 |
| 同一 Lease 不产生并发登录会话 | 20 路 Single Flight、活动 Artifact Partial Unique Index、Fence / Origin / Revision CAS | P2 |
| Lease 或身份依赖变化后 Session 立即失效 | Lease / Account / Credential / Connector Trigger、旧 Fence 拒绝、Janitor 密文销毁 | P2 |
| Published 版本不可变 | P3 DataAtom / DataBlueprint DB Trigger、无 DELETE 权限与 API contract test；P4-P5 复用同一模式 | P3-P5 |
| Blueprint 编译可复现 | exact Atom Version、确定性拓扑层级、逆序 Cleanup 与 Plan Digest 重编译一致性测试 | P3 |
| Fixture 发布证据不能伪造 | Static / Runtime / Cleanup 三类独立 PASSED 证据、Revision 绑定与缺失证据 fail-closed | P3 |
| Provider I/O 不能先于 Attempt 事实 | Activity 调用前持久化 RUNNING Attempt；非法响应或未知提交结果进入 OUTCOME_UNCERTAIN，不盲重试 CREATE | P3 |
| 非 CREATED 资源不能被自动删除 | Resource Ownership 数据库约束、只领取 CREATED 资源的逆拓扑 Cleanup 与真实 Ledger 测试 | P3 |
| 取消后仍执行 Cleanup | 业务取消信号与原生 Temporal Cancellation、`finally` 补偿、Transient Failure 重试与真实 Temporal 测试 | P3 |
| DebugRun 快照不可漂移 | Test IR / PlanTemplate / compiledDigest 一致性、数据库不可变 Trigger、无 DELETE 权限与 dispatch 重放测试 | P4 |
| Draft 语义变化使旧调试证据失效 | semantic Patch 同事务标记 `OUTDATED`；layout-only 更新保持 `CURRENT` 的真实 PostgreSQL 测试 | P4 |
| DebugRun 不能自报或伪造通过 | ExecutionContract exact binding；Assertion / Artifact 不可变事实；数据库重推 completeness / integrity / outcome；CaseVersion 加载实际 EvidenceManifest | P4 / P6 |
| Browser Worker 不能绕过控制面或降级传输安全 | 独立无 `database_url` Settings / 镜像入口；所有读取与写入经 Permit + HMAC 内部网关；Staging / Production Runtime API 非 HTTPS 配置拒绝启动；部署 Credential 扫描 | P6 |
| Browser Action 不能重定向到漂移 DOM | Observation / Page Revision / Nonce、retained ElementHandle 与 Semantic Fingerprint 执行前复核；页面变化使 Target 失效 | P6 |
| Browser Artifact 不能由 Operation 自报 | Operation 直接返回 Artifact 拒绝；原始字节只能经可信 `BrowserArtifactWriter` Redaction / Store / Hash / Verify；完整 Artifact Input Digest 进入 Report Chain | P6 |
| Browser Report 不能删改、乱序或替换终态 | 类型化 Hash-chain、无间隙 Sequence、`actionId` 不可跨 Action 链复用、连续 Proposal / Policy / Receipt、PostgreSQL Trigger / RLS / 最小权限 | P6 |
| Finalization 不能替换 Assertion / Artifact 输入 | 对每个完整 `AssertionResultInput` / `EvidenceArtifactInput` 重算 Canonical Digest 并与 Report exact 集合匹配；Finalization Command Digest 只允许 exact replay | P6 |
| 浏览器未知副作用不能伪装成功 | Temporal Activity 不盲重试；`execution.blocked` 或任一非 `SUCCEEDED` Receipt 强制全部 Assertion 与最终 Outcome 为 `INCONCLUSIVE` | P6 |
| DebugRun 事件可可靠重放 | 每 Run 单调无间隙 `seq`、事件状态与主 Run 一致 Trigger、`afterSeq` API 与防绕过测试 | P4 |
| Live 观察流不能泄露任意运行 Payload | event-type allowlist、取消原因 / Digest / ObjectRef / Secret 字段排除、32 KiB Check、不可变 Trigger 与序列化秘密扫描；0019 提交 `NOT VALID` 可修复边界，0020 Validate + Trigger，0021 以 autocommit concurrent drop 清理冗余 replay index，并支持 concurrent downgrade recreate | P6 |
| CaseVersion 不能绕过发布证据 | 当前 Draft 复编 + exact Role / Published Fixture + CURRENT PASSED DebugRun 三类 Digest 一致 + Author / Reviewer 分离 | P4 |
| CaseVersion 历史不随 Draft 漂移 | version root、Node / Edge snapshot、Test IR、PlanTemplate、contentDigest 不可变 Trigger 与无 DELETE 权限 | P4-P5 |
| TaskRun 不重新解析或改写执行输入 | TaskPlanVersion / RunManifest canonical digest、Repository + PostgreSQL Plan-to-Manifest provenance、Manifest-to-Unit Insert Trigger、JSON 缺键 / null fail-closed、Published Version / Manifest 不可变与 exact replay 冲突测试 | P5 |
| 业务重试不覆盖旧 Attempt | `task-run-manifest/0.2` 冻结 retry policy；仅显式 `INFRA_ERROR` 在 per-Unit / per-Run / deadline 边界内追加确定性 gapless UnitAttempt，旧 Attempt 不可变且无 DELETE 权限；Pause / Cancel 结算边界不追加重试 | P5-00D3A |
| 手动重跑不能复活旧 Run、漏选或夹带 Unit | `20260717_0031` 将 child lineage 与 `INFRA_FAILURES` mode 绑定；数据库 Manifest Guard 从 sealed / closed parent 重算每个且仅有 `CLOSED / INFRA_ERROR` 的 Unit，并证明 frozen Plan / schema / iteration / policies / compiler 不漂移；新 Run / Unit / Attempt / Temporal identity 与 exact replay | P5-00D3B |
| Task 事件不能乱序或伪造状态 | 每 TaskRun gapless `seq`、父 Run 行锁、事件状态匹配最窄 Unit / Attempt Scope、UPDATE / DELETE Trigger | P5 |
| Task materialization 不完整不能进入调度 | `20260718_0042` 保留 ≤64 原子快路径；更大 Run 使用 64-Unit partition checkpoint、Claim Lease / Token / Revision Fence 与独立提交，只有连续完整覆盖全部 Manifest 后才由同一 `MATERIALIZING → SEALED` 数据库函数重算 Manifest / request / Unit digest、核对全部 Unit 与首 Attempt，并追加唯一 Pending Start Intent | P5-00B1 / P5-00E4 |
| Workflow Start Intent 不能丢失、重放错误或跨 Consumer 覆盖 | 独立 `atlas_dispatcher` 最小权限；namespace + exact Type / Queue allowlist；短事务 Claim / Lease、事务外 Temporal Start、Token + Revision CAS Ack；稳定 `request_id`、`REJECT_DUPLICATE + USE_EXISTING` 与 Describe Type / Queue / Memo collision verification；过期 Claim 接管和 DB-clock Retry | P5-00B2A |
| Task Worker 不能跨 Tenant、乱序锁或绕过状态权威 | `20260716_0025` tenant-scoped `SECURITY DEFINER` 执行链锁；数据库内 Run → Unit → Attempt 固定锁序；短事务 Revision CAS / 追加事件；`atlas_app` 无三张状态表的表级 UPDATE | P5-00B2B |
| Task 副作用不能因自动 retry、replay 或原生取消被误判 | 固定 Root / Attempt Type 与双 Queue；100,000-Unit 协议、每页最多 64 Units、8-child batch、deterministic child ID + `REJECT_DUPLICATE`；只在无 active Child / unsettled batch 且整页落库后 Continue-As-New，末页由数据库投影收口；副作用 Activity `maximumAttempts=1`、Heartbeat 与等待取消完成；`scheduleToClose` 不越过 deadline；数据库 Activity 瞬时故障耐久 retry、确定性不变量 non-retryable；取消先排空本页、保留已完成 Child，运行中副作用收敛为未知 | P5-00B2B / P5-00E4 |
| Task Port 不能消费未经授权或被篡改的裸执行输入 | `20260717_0027` 每 Attempt 唯一 immutable Ticket、Scope FK、canonical digest、owner-owned Insert Guard、FORCE RLS 与 SELECT / INSERT 最小权限；Workflow 先 Prepare Ticket，再 Begin / Execute；P5-00E5 signed HTTPS Port 只发送 exact Attempt + `ticketId / ticketDigest`，双向 HMAC 绑定 Worker / Tenant / Attempt / Ticket、时间窗、Nonce、Request / Response digest 与 Attempt Idempotency-Key；生产 HTTPS、无 redirect / proxy / transport retry、有界 `no-store` 响应，歧义一律 unknown outcome | P5-00D1 / P5-00E5 |
| Task Cancel 不能丢失、跨 Run、覆盖已完成结果或在行锁内等待 Temporal | `20260717_0028` durable command intent、`If-Match` + mutation idempotency、Run `CANCELING` 原子接受、独立 dispatcher Claim / Lease / Token / Revision Fence、事务外 Describe / Signal、Root exact dedupe、active Child cancel + completed outcome preservation、Run closure 与 `APPLIED` 同事务收口；Workflow 尚未 Start 时以 plan cancel projection + `NOT_FOUND` retry 收敛 | P5-00D2A |
| Task Pause 不能停在半批、误取消当前批或被 Resume 提前宣告 | `20260717_0029` durable `PAUSE / RESUME`、Root 每批最多 8 个 Child 的原子 Ticket 预授权、批次完成后的 DB checkpoint、`PAUSE_REQUESTED → PAUSED` 与 command `APPLIED` 同事务、`workflow.wait_condition` 耐久等待、Resume 仅在 Workflow ack 后 `PAUSED → RUNNING`；Cancel 原子把未完成 Pause / Resume 置为 `SUPERSEDED` | P5-00D2B |
| 未 Seal 的 Task 执行不能伪装通过 | Workflow 只接受带 exact `resultRefId + sealContentHash` 的 `RESULT_FINALIZED`；数据库无匹配 Fact 时拒绝 `PASSED`，`EXECUTED_UNSEALED → FINISHED_UNSEALED` 仍只持久化为 `INCONCLUSIVE`；Activity 回包丢失时从 Fact 恢复 | P5-00B2B / P6-03A |
| Attempt Seal 不完整或被篡改不能通过 | Pydantic 终态轴约束、canonical content hash、Ed25519 public key ring 验签、数据库 30-key 投影与 hash 重算、Execution Ticket / Attempt Scope FK、immutable Trigger、exact replay 与冲突 Incident 测试 | P6-03A |
| CLOSED Attempt 不能漏失终态事实或用 ClosureNotice 伪造业务结论 | Seal / ClosureNotice 每 Attempt 互斥；ClosureNotice 只能 `INCONCLUSIVE / NOT_EVALUATED`；Resolution Insert Guard 重算全部 CLOSED Attempt 的完整输入集合、canonical hash、decisive axes 与 Stability | P6-03B |
| Unit 重试解释不能覆盖历史或因 replay 产生漂移 | `unit_resolution_revision` 仅追加、gapless Revision、稳定 resolution root、exact predecessor、完整 input-set + policy digest 唯一；相同输入 exact replay 不新增 Revision | P6-03B |
| Task 聚合不能漏 Unit、重读可变 Case 或用旧 Resolution 伪造最终结论 | `task_result_snapshot` 只绑定 Manifest-ordered latest Resolution revision；Insert Guard 重新比对全部 CLOSED Attempt 的 Seal / Closure 集合、数量守恒、轴分布、四类通过率、policy digest、watermark 与 semantic hash；相同输入 exact replay 不新增 Revision | P7-01A |
| 后续重试成功不能掩盖较早 Attempt 的 Fixture 泄漏 | `attempt_fixture_binding` 精确绑定每个 UnitAttempt 的 FixtureRun；`unit_hygiene_resolution_revision` 对全部 CLOSED Attempt 做 gapless 输入覆盖，数据库重算 cleanup revision、Manifest、Resource / CleanupAttempt / Reconcile observation hash，并按 `LEAKED > CLEANUP_FAILED > PENDING > CLEANED / NOT_APPLICABLE` 聚合 | P7-01B0 |
| SSE 重连不丢事件且可幂等去重 | 轻量 Snapshot 高水位单 SQL、DebugRun-bound Opaque Cursor、`Last-Event-ID` 的 `seq > afterSeq` 有序 replay、Heartbeat 不推进 Cursor、跨 `terminated` 继续 replay 后续 `snapshot_outdated`；UnitAttempt Control Command 走独立 REST lane | P6；P6-02B2 已完成正式 UnitAttempt LiveSession，SSE 不承载控制命令 |
| 慢客户端不能无限占用 Live Observer | Route `_DebugLiveStreamingResponse` 负责生成、Source close 与 Slot release；最后安装的 pure-ASGI Middleware 负责 `BaseHTTPMiddleware` 后的真实 client-facing `send`。两层均使用 `maximum_connection_seconds` + 固定 1.0 秒 Close Grace，阻塞写入到期强制取消 | P6 |
| 洞察可由事实重建 | 清空投影后重放一致性测试 | P8 |
| 黄金链路稳定 | 30 次连续运行，平台失败率不高于 5% | P9 |
