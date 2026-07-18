# ADR-0012：签名 Task Gate Callback 与可靠投递

- 状态：Accepted
- 日期：2026-07-18
- 决策范围：P5-00E7 / P7 Gate 外部通知

## 背景

`TaskGateDecision` 是数据库复核后的追加式三值事实。外部 CI/CD 或发布系统需要收到该决定，但 API 请求不能携带任意 Callback URL，HMAC Key 不能进入控制面数据库，HTTP 调用也不能发生在 Gate 事务内。网络超时还可能发生在接收方已经提交事件之后，因此“发送失败”不能简单等同于“接收方未处理”。

## 决策

1. `ResultGateService` 在插入一个新 `TaskGateDecision` 的同一数据库事务中插入唯一 `task_gate_callback_intent`。Idempotency replay 不重复创建 intent。
2. Intent 只保存 `eventId`、Gate/Task scope、`manifestHash`、三值 `gateDecision` 和投递状态。URL、HMAC Key、signature、HTTP response body 与异常文本永不持久化。
3. 外部 wire body 固定为六个字段：`eventId / taskRunId / manifestHash / gateDecision / timestamp / signature`。HMAC-SHA256 覆盖前五项；timestamp 使用 UTC 整秒并受回放窗口约束。
4. `eventId` 是接收方的永久幂等键。每次 retry 可使用新的 timestamp/signature，但必须保持相同 eventId 与业务内容。接收方必须先按 eventId 原子去重，再执行副作用；重复事件也应返回 2xx。
5. 独立 `atlas-task-gate-callback-consumer` 使用 `atlas_dispatcher` 登录角色。该角色没有 callback 表的 SELECT/INSERT/UPDATE/DELETE，只能调用 Claim/Delivered/Retry/Fail 四类窄函数。
6. Claim 事务先提交，HTTP 在事务外执行，最终用 exact eventId + claimToken + dispatchRevision 在新事务中 CAS。过期 Claim 可接管，旧 Consumer 只能得到 lease-lost。
7. Endpoint 与 HMAC Key 只从该 Consumer 的进程级配置读取。请求模型和数据库没有 URL 字段；Staging/Production 强制 HTTPS。HTTP 禁止 redirect、禁用环境代理并限制单次 timeout。
8. 2xx 表示本次送达；408/425/429/5xx 和 transport error 进入有界 retry；其他状态永久失败。HTTP timeout 后允许重投相同 eventId，因为远端结果未知且协议要求幂等去重。

## 结果

- Gate 事实和“需要通知”事实原子一致，同时 API 事务不等待外部网络。
- Secret 与部署 endpoint 保持在独立进程内，Tenant 请求无法形成 SSRF 配置。
- 投递是可恢复的 at-least-once，而不是伪造 exactly-once；eventId 明确承载去重责任。
- 未启用 Consumer 时 intent 保持 `PENDING`，不会伪装为已通知。
- 任意 callback fact 存在时，`0044` downgrade fail-closed，避免静默丢失投递审计。
