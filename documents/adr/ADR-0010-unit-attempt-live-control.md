# ADR-0010: UnitAttempt 现场控制、Epoch/Fence 与单次 ActionGrant

- Status: Accepted
- Date: 2026-07-18
- Scope: P6-02B2 正式 UnitAttempt Live Control

## 背景

DebugRun Live SSE 只能安全观察发布前试运行，不能代表正式批量执行的控制权。人工接管如果只依赖前端状态、WebSocket 连接或内存锁，会在重连、Worker 重启和并发请求下产生双 Controller；旧 Agent 还可能在接管后继续执行已经签发的浏览器动作。

P5 已提供不可变 `TaskUnitExecutionTicket` 和正式 `UnitAttempt`，因此现场控制必须精确绑定一个 Attempt，并在 PostgreSQL 中保存可审计、可恢复的控制事实。

## 决策

1. 每个正式 `UnitAttempt` 最多建立一个 `LiveSession`，并精确绑定同作用域 immutable Execution Ticket、BrowserSession ID、Browser Revision、TaskRun 和 ExecutionUnit。DebugRun 不得作为多态宿主。
2. 当前 Controller 由一个短期 `ControlLease` 表达。`controlEpoch` 与 `fencingToken` 一起单调递增；任何浏览器副作用都必须同时匹配 LiveSession、Lease、Epoch、Fence、Page Revision 和单次 Grant。
3. Agent Heartbeat 只能延长当前 ACTIVE Lease 的 `expiresAt`，不能更换 Owner、Epoch 或 Fence，也不能超过 UnitAttempt `executionDeadline`。Tenant Reconciler 使用有界 `FOR UPDATE ... SKIP LOCKED` 批次回收过期 Lease。
4. Lease 过期时原子执行：Lease → `EXPIRED`、未消费 Grant → `REVOKED`、待处理 Command → `REJECTED`、LiveSession → `NO_CONTROLLER`，并提升 Epoch/Fence、追加安全审计事件。已消费但尚未回执的动作可以写入 exact Receipt，但不能再签发新动作。
5. `PAUSE / RESUME / TAKEOVER / RETURN` 是持久化异步 REST Command。写接口要求强 `If-Match` Control Epoch 和 `Idempotency-Key`；状态交接只能由 Worker 在 Action Safe Point 或 Reconcile Checkpoint 后确认。
6. `PAUSE` 和 `TAKEOVER` 先进入 `QUIESCING`，停止新 Grant 并等待在途动作；`TAKEOVER` 完成后切换到 HUMAN Lease。`RETURN` 先进入 `RECONCILING`，刷新页面事实后才签发新的 Agent Lease。V1 禁止 Production Environment Human Takeover。
7. 每个 `LiveActionGrant` 持久化、单次消费，并绑定 exact Action Proposal、Policy Digest、Adapter、Page Revision、Lease、Epoch 和 Fence。Worker 必须在 Playwright 副作用之前原子消费 Grant，副作用之后写入唯一 Execution Receipt。
8. Browser Worker 继续不直连控制面数据库。初始化、Heartbeat、Safe Point acknowledgement、Grant 签发/读取/消费/完成全部通过 Permit + HMAC 内部 API；公共 UI 只能读取安全 Snapshot 和提交异步 Command。
9. WebSocket 或 SSE 只承载观察数据，不承载控制命令。当前前端在既有 Task Live 原型槽位轮询正式 Snapshot；不改变 DOM、布局、样式、className 或既有交互结构。
10. 一旦 Human Takeover 生效，`LiveSession.humanInfluenced` 永久为真。PostgreSQL 阻止该 Attempt 以 `AUTONOMOUS` 影响模式封存，避免人工动作被统计为自主执行。

## 结果

- Worker 重启、网络重试和 UI 重连不会创建第二个有效 Controller。
- 旧 Epoch/Fence、已撤销或已消费 Grant 无法再次触发副作用。
- 控制事实可以从 PostgreSQL 恢复并审计，前端连接不是事实权威。
- 正式 Task Worker 的 production `TaskUnitExecutionPort` 后续必须消费这组内部协议；没有受信 Adapter 时仍保持 fail-closed。
