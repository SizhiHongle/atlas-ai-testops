# ADR-0006: Fixture 资产契约、确定性编译与发布门禁

- Status: Accepted
- Date: 2026-07-14
- Owners: Atlas Test Space
- Scope: DataAtom、DataBlueprint、CompiledFixturePlan、FixtureManifest 与发布证据

## 背景

Fixture 需要组合多个数据准备能力，并在失败、取消或重试后可靠清理资源。若资产允许动态代码、任意 URL 或 floating version，控制面无法静态验证权限与数据流，运行也无法复现。若发布只依赖 Schema 校验，CREATE Atom 可能在无法清理、无法对账或尚未真实执行时进入正式任务。

## 决策

1. DataAtom 与 DataBlueprint 采用 Definition / Version 双层模型。Definition 可用 Revision CAS 维护，Version 一旦进入 `PUBLISHED` 或 `DEPRECATED` 即由 PostgreSQL Trigger 保证不可修改；应用角色没有这些表的 DELETE 权限。
2. DataAtom 只允许引用部署时登记的结构化 `ConnectorOperationRef`，不得携带动态 Module、Callable、URL、Header、Shell、SQL、JavaScript 或任意代码。CREATE Atom 必须同时声明 Resource Descriptor、Cleanup 与 Reconcile Operation。
3. 当前协议拒绝密码、Secret、Cookie、Token、Storage State 等秘密语义类型，也拒绝 Production Environment。秘密访问必须复用 P2 的 Lease、SecretGrant 和 SessionArtifact 安全边界。
4. DataBlueprint 只引用同一 Project 的 exact DataAtom Version。Compiler 只做静态验证，不执行 Connector 或外部 I/O；它验证 Port、JSON Schema Literal、必填输入、SourceRef、Semantic Type、Classification、DAG 与 Export。
5. CompiledFixturePlan 使用稳定 Node ID 形成确定性并行层级，Cleanup 按逆拓扑顺序执行；Digest 覆盖 Blueprint、Atom Version 与完整计划。运行时必须冻结该计划和 Digest，不能重新解析目录当前版本。
6. 发布要求 `STATIC`、`RUNTIME`、`CLEANUP` 三类独立 `PASSED` Evidence 并绑定当前 Version Revision。DataBlueprint 还必须保存当前 Revision 编译出的计划。任一证据缺失、失败或过期时发布 fail-closed。
7. PostgreSQL 是资产、Validation Evidence、Compiled Plan、FixtureRun / ResourceRecord / FixtureManifest 的业务权威；Temporal 只负责 P3-02/P3-03 的耐久执行与补偿。
8. 前端保持现有原型为视觉和交互权威，只把真实 Catalog 映射到已经存在的 DataAtom 与 Blueprint 数据槽位，不新增页面、卡片或样式。

## 后果

- 资产可在执行前发现端口、类型、数据分级、循环依赖和缺失输入问题，Compiled Plan 可由相同输入稳定重建。
- CREATE 能力不能脱离资源账本和清理协议发布；P3-02 提供 Runtime Evidence，P3-03 只在正常释放并完成真实清理后提供 Cleanup Evidence，其他情况会明确拒绝发布而不是模拟成功。
- Connector Operation 必须先在受信部署中登记，新增 Provider 能力需要代码和部署变更，换取可审计、可授权且无任意代码执行面的协议。
- P3-02 已实现 FixtureRun、DataNodeRun / Attempt、ResourceRecord、FixtureManifest 与 Runtime Evidence；P3-03 已实现取消后必清理、Reconcile Attempt、Cleanup Generation Attempt、Retry / Sweeper、孤儿扫描、故障注入和 Cleanup Evidence。
