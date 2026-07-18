# ADR-0013：P9 本地参考验收与生产门禁分离

- 状态：Accepted
- 日期：2026-07-18
- 决策范围：P9 容量、故障注入、黄金链与 SLO

## 背景

实施计划同时包含可在仓库内重复证明的不变量，以及只能在真实部署和试点周期中观测的运营指标。若把单机 PostgreSQL、Development Temporal、In-memory Evidence Store 或 Mock Provider 的结果直接标记为生产 SLO 通过，会制造错误的上线信心；若完全等待外部试点，又会让故障恢复、租约冲突和黄金链稳定性缺少自动化回归。

## 决策

1. `backend/scripts/run_p9_acceptance.py` 是固定的本地参考验收入口，生成 `atlas.p9-acceptance-report/0.1` JSON。报告只包含安全摘要、计数、整数毫秒和 Git Revision，不保存 DSN、Token、Secret、对象引用或测试 Payload。
2. 本地强门禁固定覆盖六类故障注入、2×参考峰值、100 并发×100 轮 Lease、账号不足、多项目与跨项目不可见、100 个大 Evidence Object、30 次完整黄金事实链、30 个真实 Temporal Schedule 纵向样本和 100 个应用内 Live Event 样本。
3. 本地 Gate 任一失败时 Runner 非零退出。所有本地 Gate 通过但仍存在外部门禁时，总状态是 `CONDITIONAL_PASS`，不能提升为 `PASSED`。
4. 月度控制面可用性、真实网络下 Schedule / Live SLO、人工 Failure Classification 准确率、真实团队影子迭代和灾备恢复演练必须保持 `NOT_EVALUATED`，直到部署端提供可审计证据。
5. 30 次黄金链使用真实 PostgreSQL 和完整 Task / Result / Gate / Callback 事实路径，但采用确定性 Adapter；它证明平台闭环，不代表真实 SaaS Connector 或业务行为已经验收。
6. 100×100 Lease 测试允许记录 `POOL_EXHAUSTED` 短暂背压并执行有界重试；通过条件是 10,000 次完整循环最终收敛、重复 Active Slot 为零、Fence 单调且无遗留 Active Lease。短暂背压次数必须写入报告，不能隐藏。
7. Heavy P9 Test 默认跳过，只有 `ATLAS_RUN_P9_ACCEPTANCE=1` 才执行。`make verify` 保持日常反馈速度，`make p9-acceptance` 是发布候选的额外强门禁。

## 结果

- 仓库能够自动证明确定性、安全性和本地容量回归，同时不会伪造生产运营证据。
- 报告可在 CI 或发布流水线中留档；临时报告位于 `tmp/p9/`，默认不进入 Git。
- 生产上线仍必须完成 [P9 Production Readiness Runbook](../runbooks/P9_PRODUCTION_READINESS.md) 中的外部门禁与负责人签字。
