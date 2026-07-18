# P9 Local Reference Baseline

- 执行日期：2026-07-18
- Python：3.14.6
- 进入 P9 的基线提交：`593a092`
- PostgreSQL / Temporal：真实本地服务
- Profile：`LOCAL_REFERENCE`
- 总状态：`CONDITIONAL_PASS`

## 已通过

| Gate | 结果 |
| --- | --- |
| Fault Injection | 12 passed；六类固定场景全部安全收敛 |
| Capacity / Isolation | 7 passed；多项目、账号不足、大 Evidence、跨项目不可见 |
| Account Lease | 10,000 次完整循环；重复 Active Slot 0；有界短暂背压 5,472 次 |
| Golden Stability | 30 / 30；本地参考平台失败率 0.00% |
| Cleanup | 30 / 30；最终清理断言 100% |
| Schedule Reference | 30 / 30；保守完整命令 P95 4,787 ms，目标 <60,000 ms |
| Live Reference | 100 样本；P95 4 ms，目标 <2,000 ms |
| Evidence Reference | 100 / 100 独立校验；总计 78,807,900 bytes |

每次运行会生成完整的 `tmp/p9/acceptance-report.json`，该临时文件不提交。上述结果是本地参考基线，不包括真实 SaaS、生产网络、Proxy、Browser Render、KMS 或多节点故障。

## 未评估的外部门禁

- 控制面 99.9% / 月。
- 人工 Failure Classification 准确率 ≥90%。
- 至少一个真实团队影子迭代。
- 经批准 RTO / RPO 的灾备恢复演练。

这些项目需要部署与试点输入，因此总状态保持 `CONDITIONAL_PASS`。完成方式见 [P9 Production Readiness Runbook](runbooks/P9_PRODUCTION_READINESS.md)。
