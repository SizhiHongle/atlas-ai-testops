# Atlas AI 测试平台实施矩阵

更新时间：2026-07-16

状态含义：`未开始`、`基础中`、`后端完成`、`前端完成`、`已验收`。只有数据库、领域、API、前端和测试证据全部存在时才使用 `已验收`。

| 领域 | 权威设计 | 主要数据库对象 | API 范围 | 前端范围 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| Platform | 总体落地方案 | tenant、project、environment、platform_user、password_credential、platform_membership、platform_session、audit、outbox、idempotency | auth、session、projects、environments | 既有 Login、Space Header | 已验收：P1 真实数据库、API、RBAC 与浏览器 QA 完成 |
| Workflow Contract | AI 用例 v0.3 | workflow_draft、workflow_node、workflow_edge、draft_operation、debug_run、debug_run_event、case_version、case_version_node、case_version_edge、execution_contract、browser_runtime_report | draft validate / patch / layout、debug-runs、events、cancel、publish、versions；Browser Runtime 内部协议 | Case Canvas、Debug、Publish | 后端完成：P4-00 至 P4-03、P6-00 与 P6-01 已落地双 Revision、不可变 DebugRun、ExecutionContract、报告链和精确发布闭环 |
| Identity | 身份与测试账号 v1.1 | connector_installation、connector_capability、test_role、account_pool、test_account、account_slot、account_lease、credential_binding、secret_grant、account_health_check、account_state_transition、browser_session_artifact、auth_action_ticket、environment.allowed_origins | connectors、capability validation、roles、pools、accounts、leases、health verification / history、secret grants、ensure-session | Identities | 基础中：P2-01 至 P2-06 已验收；真实 SaaS Flow、生产 Secret/KMS 与 ExecutionIdentityGrant 延后接入 |
| Fixture | 数据预加载 v0.2 | data_atom_definition/version、data_blueprint_definition/version、fixture_run、fixture_actor_binding、data_node_run/attempt、data_node_reconcile_attempt、resource_record/dependency、resource_cleanup_attempt、fixture_manifest、fixture_validation_evidence | data-atoms、data-blueprints、validate、compile、publish、fixture-runs、manifest、resources、release、cancel、retry-cleanup、cleanup sweep | 既有 Atoms、Assets 数据槽位 | 已验收：P3-00 至 P3-03 的资产、耐久运行、取消补偿、Reconcile、Cleanup Retry / Sweeper 与发布证据闭环 |
| Case | AI 用例 v0.3 | test_case、workflow_draft、workflow_node、workflow_edge、draft_operation、debug_run、debug_run_event、case_version、case_version_node、case_version_edge | test-cases、workflow-draft、patch validate / apply、layout、debug-runs、events、cancel、publish、versions | Cases、Case Canvas、Debug、Publish | 后端完成：P4-00 至 P4-03 已验收；前端待按既有原型槽位接入真实状态 |
| Task | 任务中心 v0.2 | 四类 profile_version、task_plan/version、task_run、task_run_manifest、execution_unit、unit_attempt、task_run_event、workflow identity registry，以及 durable start intent 状态机；schedule 待后续 | P5-00B1 Application / Repository 契约；P5-00B2A 独立 Intent Consumer；task-plans、task-runs、commands、events 公共 API 待后续 | Launch、Task Control | 基础中：P5-00A / P5-00B1 / P5-00B2A 已验收；独立 `atlas_dispatcher`、Claim / Lease / Retry / Started / Failed 和稳定 Temporal Start collision verification 已落地；真实 Task Workflow / Activity、大批次分区物化和公共控制面未实现 |
| Live / Browser | 现场 v0.2 | debug_run、debug_run_event、execution_contract、browser_runtime_report、unit_attempt 宿主；live_session、control_lease 待后续 | Permit + HMAC Browser Runtime 内部协议；轻量 DebugRun Live Snapshot、SSE、Opaque Cursor / `Last-Event-ID` replay；无公共完成或控制 API | Live Theatre | 基础中：P6-02B1 已实现 DebugRun-scoped 只读 Live Snapshot / SSE、安全事件 allowlist、跨 `TERMINATED` 的短事务 replay、有界 Observer、Route lifecycle deadline 与 pure-ASGI client-facing `send` deadline；P5-00A 已提供 UnitAttempt，LiveSession、ControlLease、控制 Epoch / Fence、Human Takeover 和持久化 ActionGrant 属于 P6-02B2，未实现 |
| Evidence | 现场与结果 v0.2 | assertion_result、evidence_artifact、evidence_manifest、browser_runtime_report、evidence_read_grant；attempt_seal 待正式 UnitAttempt | 内部报告链与受信终结、Manifest、scoped read-token、完整字节读取；annotations 待后续 | Live Evidence、Result Evidence | 基础中：P6-02A DOM Mask、canonical PNG、write / read-back verification、hash-only Read Grant 与二次完整性校验已验收；AttemptSeal 未开始 |
| Result | 结果中心 v0.2 | result_fact、resolution_revision、result_snapshot、classification、gate | results、clusters、reruns、gate | Results | 未开始 |
| Insight | 洞察中心 v0.2 | insight_event、entity_state、metric_bucket、snapshot、card、review | insight queries、snapshots、reviews、exports | Insights | 未开始 |

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
| Task Run | `atlas.task-run/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已导出；表达 `MATERIALIZING → SEALED`、stable request digest、完整 Seal facts 与确定性 Run Workflow identity |
| Execution Unit | `atlas.execution-unit/0.1` | `backend/src/atlas_testops/domain/task` | P5-00A 已导出；冻结 Manifest 中一个 exact CaseVersion × Matrix Cell 逻辑执行槽位 |
| Unit Attempt | `atlas.unit-attempt/0.1` | `backend/src/atlas_testops/domain/task` | P5-00B1 已导出确定性 Attempt Workflow identity；业务重试追加新 Attempt |
| Execution Event | `atlas.execution-event/0.1` | `backend/src/atlas_testops/domain/task` | P5-00A 已实现并导出；PostgreSQL 追加式无间隙 replay |
| Attempt Seal | `atlas.attempt-seal/0.1` | P5-00A 已提供正式 UnitAttempt；待 P6 后续切片创建 | 未开始；不创建无宿主 Seal |
| Result Snapshot | `atlas.result-snapshot/0.1` | 待 P7 创建 | 未开始 |
| Insight Event | `atlas.insight-event/0.1` | 待 P8 创建 | 未开始 |

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
| 业务重试不覆盖旧 Attempt | 每 ExecutionUnit gapless `attemptNumber`、父 Unit 行锁、追加新 UnitAttempt、旧 Attempt 不可变与无 DELETE 权限 | P5 |
| Task 事件不能乱序或伪造状态 | 每 TaskRun gapless `seq`、父 Run 行锁、事件状态匹配最窄 Unit / Attempt Scope、UPDATE / DELETE Trigger | P5 |
| Task materialization 不完整不能进入调度 | `MATERIALIZING → SEALED` 数据库函数重算 Manifest / request / Unit digest，核对全部 Unit 与首个 Attempt，并在同事务追加 Pending Start Intent | P5-00B1 |
| Workflow Start Intent 不能丢失、重放错误或跨 Consumer 覆盖 | 独立 `atlas_dispatcher` 最小权限；namespace + exact Type / Queue allowlist；短事务 Claim / Lease、事务外 Temporal Start、Token + Revision CAS Ack；稳定 `request_id`、`REJECT_DUPLICATE + USE_EXISTING` 与 Describe Type / Queue / Memo collision verification；过期 Claim 接管和 DB-clock Retry | P5-00B2A |
| Attempt Seal 不完整不能通过 | 领域属性测试 + Gate 集成测试 | P6-P7 |
| SSE 重连不丢事件且可幂等去重 | 轻量 Snapshot 高水位单 SQL、DebugRun-bound Opaque Cursor、`Last-Event-ID` 的 `seq > afterSeq` 有序 replay、Heartbeat 不推进 Cursor、跨 `terminated` 继续 replay 后续 `snapshot_outdated` | P6；正式 UnitAttempt 宿主已由 P5-00A 提供，LiveSession 待 P6-02B2 |
| 慢客户端不能无限占用 Live Observer | Route `_DebugLiveStreamingResponse` 负责生成、Source close 与 Slot release；最后安装的 pure-ASGI Middleware 负责 `BaseHTTPMiddleware` 后的真实 client-facing `send`。两层均使用 `maximum_connection_seconds` + 固定 1.0 秒 Close Grace，阻塞写入到期强制取消 | P6 |
| 洞察可由事实重建 | 清空投影后重放一致性测试 | P8 |
| 黄金链路稳定 | 30 次连续运行，平台失败率不高于 5% | P9 |
