# P9 Production Readiness Runbook

## 目的

本 Runbook 把仓库内可重复的工程验收与真实生产运营验收分开。任何 `FAILED` Gate 都阻止发布；`CONDITIONAL_PASS` 只表示本地参考门禁通过，不等于生产可上线。

## 本地参考门禁

前置依赖：

- Python 3.14 与已同步的 `backend/.venv`。
- 已升级到 Alembic Head 的 PostgreSQL，分别提供 `atlas_app` 与 `atlas_owner` 测试 DSN。
- 可连接的 Temporal Development Server。
- 工作树中的前端原型无需启动，也不会被 P9 修改。

执行：

```bash
make p9-acceptance
```

Runner 强制至少 30 次黄金链和 30 个 Schedule 样本。单个 Pytest 子进程上限 900 秒；任何超时都作为失败。机器报告写入：

```text
tmp/p9/acceptance-report.json
```

报告不包含数据库 URL、HMAC Key、Session、ObjectRef 或异常原文。`tmp/` 已被 Git 忽略，发布流水线应把报告作为不可变构建 Artifact 留存。

## 固定验收矩阵

| Gate | 自动证明 | 通过条件 |
| --- | --- | --- |
| Fault Injection | API timeout、账号 TTL、Worker interruption、Evidence Store failure、SSE disconnect/stall、Cleanup failure | 所有固定 Selector 安全收敛 |
| Capacity / Isolation | 2×本地参考峰值、账号不足、100 Evidence Objects、Task/Evidence/Live 跨 Project 隐藏 | 全部测试通过 |
| Account Lease | 100 并发 × 100 轮完整 Acquire / Release | 10,000 次循环、Active Slot 冲突 0、遗留 Active Lease 0 |
| Golden Stability | PostgreSQL 上完整 Task → Result → Classification → Gate → Callback 与 Cleanup | 至少 30 次、平台失败率 ≤5%、Cleanup 100% |
| Schedule Reference | 真实 Temporal Schedule → unified compiler → SEALED TaskRun | 至少 30 样本，保守完整命令 P95 <60s |
| Live Reference | 应用内 SSE event → client completion | 至少 100 样本，P95 <2s |
| Evidence Reference | Canonicalize → write-once → read-back → digest verify | 完整率 ≥99% |

`POOL_EXHAUSTED` 是无等待 Admission 的显式背压，不是 Slot 冲突。P9 会用同一命令身份执行有界重试并记录次数；次数明显上升时必须检查数据库连接池、账号容量和调度突发，而不能删除该指标。

## 生产外部门禁

以下项目没有真实部署证据时必须为 `NOT_EVALUATED`：

| Gate | 目标 | 所需证据 |
| --- | --- | --- |
| Control Plane Availability | 99.9% / 月 | 生产监控的完整月度窗口，排除被测系统故障的书面口径 |
| Schedule Start | P95 <60s | 从名义 Fire 到 TaskRun 开始的 Staging/Production Telemetry；账号或审批等待单列 |
| Live Event | P95 <2s | 服务端 Event Timestamp 到真实浏览器可见的 Trace，包含 Proxy / Network |
| Evidence Completeness | ≥99% | 真实失败 Run 的 Screenshot / Trace / Error Summary / Version Snapshot 抽样 |
| Classification Accuracy | 人工抽检 ≥90% | 独立标注集、Reviewer、混淆矩阵与误判处置 |
| Shadow Iteration | 至少一个完整迭代 | 真实项目、SaaS Executor、测试账号、业务 API 与人工复核记录 |
| Disaster Recovery | 审批后的 RTO / RPO | 备份、异地副本、恢复演练、数据完整性与 Callback/Outbox 重放检查 |

没有试点项目、真实 SaaS Executor、Receiver Endpoint/Key、生产 Object Store/KMS、部署 Network Policy 或 RTO/RPO 时，不得把这些 Gate 人工改为通过。

## 发布顺序

1. 备份 PostgreSQL，并验证备份可读；记录当前 Alembic Revision、镜像 Digest 和前端 Artifact Digest。
2. 在 Staging 执行 `alembic upgrade head`。存在 callback、Schedule、Result 或 Live Fact 时禁止依赖 destructive downgrade 回滚。
3. 先启动 API 与 Readiness，再按顺序启用 Auth/Fixture Worker、Task Root/Attempt Worker、`atlas_dispatcher`、Schedule Worker、Browser Worker、Callback Consumer。每项默认开关保持关闭，确认前一项健康后再启用下一项。
4. 使用一个 Tenant / Project / Environment 做 Canary；验证 RLS 隐藏、Account Lease Fence、Evidence 写读、Task Gate 和 Callback Receiver 的永久 `eventId` 去重。
5. 执行 `make p9-acceptance` 并归档 JSON；再收集真实 Staging Schedule / Live Trace。
6. 只有本地报告无失败、Staging 外部门禁完成且业务负责人批准，才扩大 Worker 并发或 Tenant 范围。

## 回滚与止损

- 首选止损是关闭对应 Worker / Consumer 开关并保留全部 Intent / Fact；不要删除 Pending 记录。
- API 回滚只能使用兼容当前 Schema 的镜像。已写入事实的 Migration 具有 fail-closed downgrade，不能通过手工删表规避。
- Callback timeout 可能表示远端已提交；恢复后必须使用同一 `eventId` 重投，Receiver 对重复事件返回 `2xx`。
- Browser / Evidence 异常时停止新 Dispatch，保留不可变 Fact 和 Object Version；不得把缺失 Evidence 的 Attempt 改写为 Pass。
- Cleanup 失败进入 Sweeper / Reconcile，测试结论与 Data Hygiene 分轴保留；人工清理也必须追加审计，不覆盖历史。
- Account 容量突发时尊重 `Retry-After`、限制 Admission 并检查 Pool 健康；不得取消独占约束或绕过 Fence。

## 灾备演练检查

1. 在隔离环境恢复 PostgreSQL 备份和对象存储 Version。
2. 升级到相同 Alembic Head，验证 Tenant RLS、不可变 Fact Hash 和 Outbox / Intent 状态。
3. 启动只读 API，抽查 TaskRun、Result Snapshot、Gate、Evidence Metadata 与 Schedule Catalog。
4. 启用 Dispatcher 前先确认 Temporal Namespace 和既有 Workflow Identity；使用 collision verification，禁止生成新 Identity 覆盖历史。
5. Callback Receiver 按永久 `eventId` 去重后再恢复 Consumer。
6. 记录实际 RTO / RPO、丢失范围、重放数量和完整性差异；未达到已批准目标则演练失败。
