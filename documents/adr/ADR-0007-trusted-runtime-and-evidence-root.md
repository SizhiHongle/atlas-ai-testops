# ADR-0007: 受信 Runtime、确定性 Oracle 与证据根

- Status: Accepted
- Date: 2026-07-15
- Owners: Atlas Test Space
- Scope: DebugRun、ExecutionContract、AssertionResult、EvidenceArtifact 与 EvidenceManifest

## 背景

P4 已经冻结 DebugRun 的 Test IR 与 PlanTemplate，但只有证据 ID / Digest 占位约束，尚不能证明账号、Fixture、浏览器、模型、Prompt、Tool 和 Policy 与本次执行精确一致。若 Browser Agent 可以直接提交 `PASSED`，或 CaseVersion 只相信 DebugRun 上的两个引用字段，调用方仍可能绕过 Oracle 和证据完整性门禁。

## 决策

1. 每个 DebugRun 在第一次执行副作用前创建唯一、不可变的 `atlas.execution-contract/0.1`。合约冻结 exact Test IR / Plan Digest、FixtureRun / FixtureManifest、Role Revision、AccountLease / Fence、BrowserContextRef、Browser Revision、Locale / Timezone、Model / Prompt / Reasoning Policy、Tool / MCP Schema 与 Policy Digest。
2. Runtime 只接受数据库实时验证通过的 `READY EXECUTION FixtureRun`、完整 exports、ACTIVE Lease、精确 Fence、ACTIVE Role Revision 与 READY SessionArtifact；Fixture 和 Lease 的 `executionId` 必须等于 `debug-run:{debugRunId}`，并覆盖整个 execution deadline。
3. Browser Agent 不拥有结果裁决权。`AssertionResult` 必须匹配冻结 Test IR 中的 Assertion ID、Node、Strength、Evaluator Version 和 expected program digest；Case outcome 只由确定性 Oracle 规则推导。
4. `PASSED` 只在所有 HARD Oracle 通过、所有声明 Assertion 均有结果和证据、Artifact integrity 为 `VERIFIED`、事件链存在且时间位于冻结执行窗口内时成立。缺证据或 HARD 不确定统一得到 `INCONCLUSIVE`；HARD 失败得到 `FAILED`。
5. `EvidenceManifest` 是不可变证据根，不包含对象存储地址。PostgreSQL Trigger 重新验证 Runtime scope、Actor binding、Assertion / Artifact 引用、计数、completeness、integrity 与 outcome；应用角色只能 `SELECT/INSERT` 这些事实，不能更新或删除。
6. CaseVersion 发布事务必须加载实际 EvidenceManifest，并复核 DebugRun、ExecutionContract、Test IR、Plan、Fixture 与 Manifest Digest 全部一致；不能只信任 DebugRun 上的引用。
7. 当前 P6-00 只提供领域契约、数据库事实和内部 `DebugRuntimeService`，不开放公共完成接口。后续 Browser Worker 通过受信内部协议调用服务，仍不得直接访问主数据库。
8. `AttemptSeal` 必须归属于 P5 的正式 `UnitAttempt`，因此不在 DebugRun 基础切片中创建无宿主协议；P5-00A 已建立宿主，后续 Seal 复用本 ADR 的合约和证据根。
9. 前端原型继续作为页面结构、布局、样式与交互权威。P6-00 只更新生成类型，不修改任何现有原型页面。

## 后果

- DebugRun 无法通过普通 API、Agent 自报或直接状态更新伪造成功；旧的无证据 `PASSED` 在 Migration 中安全回退为 `INCONCLUSIVE`。
- 证据不完整、时间越界、Actor / Fence / Session 漂移和 Fixture 不一致都在应用层与 PostgreSQL 双重 fail-closed。
- 本 ADR 在 P6-00 接受时尚未提供 Browser Worker、Live Action/Event、对象字节验证服务或正式 AttemptSeal；Browser Worker 的后续实现状态见下方实施更新，其余能力仍属于后续 P6 切片和 P5/P7 正式任务链。

## 实施更新

P6-01 已按 [ADR-0008](ADR-0008-database-free-browser-worker.md) 实现不直连主数据库的独立 Browser Worker、受信内部网关、加密 BrowserContext Restore Envelope 与严格报告链。P6-02A 又在同一信任边界内补齐 DOM Mask、canonical PNG、write-once / read-back verified Writer，以及 hash-only、Actor / Session / Purpose / TTL / Max Reads 绑定的 Read Grant 和完整字节二次校验。P6-02B Live SSE / Action 与正式 AttemptSeal 仍保持在后续切片。
