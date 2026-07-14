# Atlas AI 测试平台实施矩阵

更新时间：2026-07-14

状态含义：`未开始`、`基础中`、`后端完成`、`前端完成`、`已验收`。只有数据库、领域、API、前端和测试证据全部存在时才使用 `已验收`。

| 领域 | 权威设计 | 主要数据库对象 | API 范围 | 前端范围 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| Platform | 总体落地方案 | tenant、project、environment、platform_user、password_credential、platform_membership、platform_session、audit、outbox、idempotency | auth、session、projects、environments | 既有 Login、Space Header | 已验收：P1 真实数据库、API、RBAC 与浏览器 QA 完成 |
| Workflow Contract | AI 用例 v0.3 | workflow_draft、node、edge、asset_version | draft validate / patch | Case Canvas | 基础中：结构契约已实现 |
| Identity | 身份与测试账号 v1.1 | connector_installation、connector_capability、test_role、account_pool、test_account、account_slot、account_lease、credential_binding、secret_grant、account_health_check、account_state_transition、browser_session_artifact、auth_action_ticket、environment.allowed_origins | connectors、capability validation、roles、pools、accounts、leases、health verification / history、secret grants、ensure-session | Identities | 基础中：P2-01 至 P2-06 已验收；真实 SaaS Flow、生产 Secret/KMS 与 ExecutionIdentityGrant 延后接入 |
| Fixture | 数据预加载 v0.2 | data_atom_definition/version、data_blueprint_definition/version、fixture_run、fixture_actor_binding、data_node_run/attempt、resource_record/dependency、fixture_manifest、fixture_validation_evidence；Cleanup Sweeper 待 P3-03 | data-atoms、data-blueprints、validate、compile、publish、fixture-runs、manifest、resources、release | 既有 Atoms、Assets 数据槽位 | 后端完成：P3-00/P3-01 资产控制面与 P3-02 耐久运行完成；P3-03 取消补偿、Reconcile 与 Cleanup Evidence 待实现 |
| Case | AI 用例 v0.3 | test_case、draft、operation、case_version、plan_template、debug_run | test-cases、drafts、debug-runs、publish | Cases、Assets | 未开始 |
| Task | 任务中心 v0.2 | task_plan/version、schedule、task_run、manifest、execution_unit、unit_attempt | task-plans、task-runs、commands、events | Launch、Task Control | 未开始 |
| Live / Browser | 现场 v0.2 | browser_session、action、policy、grant、receipt、observation、control_lease | attempts、events、view-token、takeover、commands | Live Theatre | 未开始 |
| Evidence | 现场与结果 v0.2 | evidence_event、artifact、link、attempt_seal | evidence、read-token、annotations | Live Evidence、Result Evidence | 未开始 |
| Result | 结果中心 v0.2 | result_fact、resolution_revision、result_snapshot、classification、gate | results、clusters、reruns、gate | Results | 未开始 |
| Insight | 洞察中心 v0.2 | insight_event、entity_state、metric_bucket、snapshot、card、review | insight queries、snapshots、reviews、exports | Insights | 未开始 |

## 第一批机器可读契约

| 契约 | Schema Version | 实现位置 | 状态 |
| --- | --- | --- | --- |
| Workflow Graph | `atlas.workflow-graph/0.1` | `backend/src/atlas_testops/domain/workflow` | 已实现并导出 |
| Workflow Draft | `atlas.workflow-draft/0.1` | `backend/src/atlas_testops/domain/workflow` | 模型已实现，持久化/API 未开始 |
| Domain Event | `atlas.domain-event/0.1` | `backend/src/atlas_testops/domain/events.py` | 已实现并导出 |
| Atom Contract | `atlas.atom/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Fixture Blueprint | `atlas.fixture-blueprint/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Compiled Fixture Plan | `atlas.compiled-fixture-plan/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现并导出 |
| Fixture Manifest | `atlas.fixture-manifest/0.1` | `backend/src/atlas_testops/domain/fixture` | 已实现、导出并由 P3-02 运行持久化 |
| Test IR | `atlas.test-ir/0.1` | 待 P4 创建 | 未开始 |
| Execution Event | `atlas.execution-event/0.1` | 待 P5 创建 | 未开始 |
| Attempt Seal | `atlas.attempt-seal/0.1` | 待 P6 创建 | 未开始 |
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
| 取消后仍执行 Cleanup | Temporal replay + 故障注入 | P5-P6 |
| Seal 不完整不能通过 | 领域属性测试 + Gate 集成测试 | P6-P7 |
| SSE 重连不丢不重 | Cursor replay 集成测试 | P5-P6 |
| 洞察可由事实重建 | 清空投影后重放一致性测试 | P8 |
| 黄金链路稳定 | 30 次连续运行，平台失败率不高于 5% | P9 |
