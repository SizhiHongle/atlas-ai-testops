# ADR-0011：数据库权威的 Temporal Task Schedule

- 状态：Accepted
- 日期：2026-07-18

## 背景

P5-00E3 已统一 Manual、Schedule、CI 与 Webhook 的 `TaskRun` 编译入口，但 HTTP Schedule Trigger 只证明永久事件身份，不能代替周期计划的 Catalog、时区、DST、Overlap、Catchup、Pause/Resume 和 Temporal desired-state 管理。若 API 事务直接调用 Temporal，会把 PostgreSQL 提交与外部 RPC 绑定在一起；若只信任 Temporal，又会失去 Tenant/RBAC、Production Environment 门禁、Audit/Outbox 和可恢复同步事实。

## 决策

1. PostgreSQL `task_schedule` 是产品 desired state 的唯一权威；定义创建后不可变，只允许带 Revision/ETag 的 `ACTIVE ↔ PAUSED`。
2. `task_schedule_sync_intent` 在同一事务记录 `CREATE / PAUSE / RESUME / AUTO_PAUSE`。独立 `atlas_dispatcher` 角色只能调用 Claim、Apply、Retry、Fail 四类窄函数；Temporal I/O 永远发生在数据库事务外，结果用 Claim Token + Dispatch Revision + Schedule Revision CAS。
3. Temporal Schedule ID 固定为 `atlas-task/schedule/{tenantIdHex}/{scheduleIdHex}`。顶层 Memo、Workflow Action、Action Memo、结构化 Calendar、Timezone、Overlap、Catchup、Jitter 和固定 Queue 全部精确校验；同 ID 不同定义 fail-closed。
4. V1 只允许 `QUEUE_ONE → BUFFER_ONE` 与 `SKIP`；Catchup 只允许有限窗口的 `RUN_ONCE` 或 60 秒窗口的 `SKIP`；`pause_on_failure=true`。不开放 `BUFFER_ALL`、`ALLOW_ALL` 或无界补跑。
5. Calendar 直接映射 Temporal `ScheduleCalendarSpec`，同时以 IANA `zoneinfo` 计算 API 展示的未来五个真实 UTC 触发时间。DST gap 跳过，fold 保留两个真实时刻。
6. Temporal 注入的 `TemporalScheduledById` 与 `TemporalScheduledStartTime` 是触发上下文权威。Workflow 将其与不可变 Action Input 复核后，经 Activity 重读 PostgreSQL，再调用统一 `TaskPlanLaunchService`。永久 Fingerprint 仍是 `scheduleId + scheduledFireTimeUTC`。
7. Pause 只阻止 Pause 后启动的 Schedule Workflow；已经启动的 Workflow 可完成并创建 TaskRun。Environment 被重分类为 `PRODUCTION` 时，数据库在同一事务自动 Pause 所有关联 Schedule，并拒绝恢复。
8. Schedule Workflow/Activity 的 Temporal 线协议只传 UUID、Digest 和规范 ISO-8601 字符串，不传 Secret、执行配置或 Python `datetime` 对象。
9. `atlas-task-schedule-worker` 与 Schedule 同步能力默认关闭。Schedule Worker 使用 `atlas_app` Tenant/RBAC 数据边界；同步消费者继续使用无 Tenant Context 的专用 `atlas_dispatcher` DSN。

## 结果

- API 提交不会因 Temporal 短暂不可用而回滚产品事实；Dispatcher 可安全接管、重试和识别陈旧 Revision。
- Temporal Schedule 不可通过同 ID 覆盖定义，周期触发也不能注入 Environment、Credential、URL、Tool、Model 或 Policy。
- 同一名义 fire 的重放只得到同一逻辑 TaskRun；Pause、DST 和生产环境重分类都有数据库与真实 Temporal 证据。
- Backfill API、每 Schedule 独立预算和外部签名 Callback 不属于本 ADR；签名 Callback 在 P5-00E7 单独落地。
