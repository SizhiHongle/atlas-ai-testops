# ADR-0005: Auth Session Worker 与加密浏览器状态

- Status: Accepted
- Date: 2026-07-14
- Owners: Atlas Test Space
- Scope: TestAccount 自动登录、Playwright Storage State、对象存储与清理

## 背景

测试执行需要复用被测系统登录态，但 Playwright Storage State 可能包含 Cookie、Local Storage 与 IndexedDB Token。若 API 进程直接执行登录、持有解密 Key 或返回 Storage State，会把浏览器资源、秘密材料和控制面扩缩容耦合，并扩大泄漏与资源异常的影响范围。

## 决策

1. 自动登录只在独立 Auth Session Worker 中执行。FastAPI 通过 Temporal 的 `atlas-auth-session` Task Queue 提交不含秘密的命令，API 不加载 Playwright、不读取 Vault Key、不调用 Secret Provider。
2. PostgreSQL 的 `browser_session_artifact` 只保存 Tenant / Project / Environment、Lease / Fence、Account / Connector / Credential Revision、Allowed Origins、不透明 ObjectRef、Digest、Size、Key Version 与生命周期。每个 Lease 同时最多一个 `CREATING / READY` Artifact。
3. Playwright Storage State 在内存中使用 AES-256-GCM 加密后写入 S3-compatible Object Store。AAD 绑定全部安全作用域和 Format Version；对象引用、摘要或 Key Version 不进入公共响应。
4. 登录协议固定为“短事务预留与消费 Secret Grant → 事务外 Provider / Playwright 登录和密文上传 → 短事务 Revision CAS 发布”。任何依赖变化都阻止 `READY` 发布。
5. API 只返回 `BrowserContextRef` 或 `AuthActionTicket`。不支持确定自动完成的认证方式与 Provider Challenge 必须返回有界人工票据，不能模拟成功。
6. Lease、Account、Credential 与 Connector 失效通过数据库 Trigger 同步撤销活动 Artifact。Janitor 在事务外删除密文，并在删除成功后写入 `DESTROYED`。
7. Playwright 共享 Browser Process，但每次登录创建新的非持久化 BrowserContext，并设置 Worker 级最大并发。认证路径不自动保存 Trace、Video、Screenshot 或 Download。
8. Local / Test / Development 可以使用静态 AES Key 和自动建 Bucket；Staging / Production 禁止该配置，必须注入 KMS-backed `SessionArtifactVault`。

## 后果

- API 延迟与内存不会直接受 Chromium 并发影响，Browser Worker 可以独立扩缩容和设置资源上限。
- Session 明文只在受控闭包内短暂存在，数据库、HTTP、Audit 与 Outbox 不承担秘密内容。
- 密文上传可能先于最终 CAS；失败或竞态产生的对象由终态记录和 Janitor 可恢复地清理。
- 首个真实 SaaS 仍需提供 Provider-specific `PasswordLoginFlow`、生产 Secret Provider、KMS 与 Object Store 配置；缺失时系统明确 fail-closed。
