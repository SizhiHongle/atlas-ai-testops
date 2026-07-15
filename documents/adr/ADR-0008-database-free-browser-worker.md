# ADR-0008: 无数据库 Browser Worker 与受限浏览器执行协议

- Status: Accepted
- Date: 2026-07-15
- Owners: Atlas Test Space
- Scope: P6-01 Browser Worker、Temporal 编排、内部 Runtime 网关、BrowserContext 恢复、Playwright Action 与报告链

## 背景

P6-00 已经建立不可变 `ExecutionContract`、确定性 Oracle 和 `EvidenceManifest`，但尚无进程可以安全地消费冻结合约并执行真实浏览器副作用。若 Browser Worker 直接连接主数据库、接受任意 URL / Locator / Script，或让 Temporal 自动重试未知结果的点击和输入，运行进程就能绕过控制面、重定向 DOM 目标、重复外部副作用或伪造证据链。

同时，`BrowserContextRef` 只是控制面安全句柄。恢复 Playwright Storage State 所需的 ObjectRef、Key Version、Lease 与 Fence 元数据不能进入 Temporal History、公共 API 或普通日志，也不能只凭一个可泄漏的 Bearer Token 授予跨 Run 权限。

## 决策

1. P6-01 使用独立 `atlas-browser-worker` 进程和专用 Temporal Task Queue。该进程使用不包含 `database_url` 的 `BrowserWorkerSettings`，不构造或导入控制面 `Database`；所有状态读取、生命周期推进、报告追加和证据终结只通过 FastAPI 的受信内部 HTTP 协议完成。
2. API 只对已经处于精确 `BINDING` 状态且已绑定唯一 `ExecutionContract` 的 DebugRun 提交 Browser Workflow。Workflow 以一个有 Heartbeat 的长时 Activity 承载浏览器副作用，Activity Retry 固定为一次，避免对结果未知的导航、点击或输入进行盲重试；Temporal History 只保存 Run、终态、Outcome 和 EvidenceManifest 的安全摘要。
3. Temporal Dispatch 为一个 exact Tenant / DebugRun / Worker Identity 签发短期 Execution Permit，Permit 不能越过 ExecutionContract Deadline。Worker 的每个内部 HTTP 请求还必须使用独立 HMAC Key，对 Method、Path、Tenant、Worker、Timestamp、Nonce、Body Digest 和 Permit Digest 一并签名。API 同时验证签名与 Permit Scope，响应使用 `no-store`，请求体有固定上限；网关只允许安全的 API Origin，并只对可幂等重放的精确签名请求进行有界重试。Local / Test / Development 可以使用 HTTP 调试，Staging / Production 的 Browser Worker 启动配置必须使用 HTTPS Runtime API Origin，不能只依赖 HMAC 保护明文传输。
4. API 从 PostgreSQL 验证 SessionArtifact 后生成 `BrowserContextRestoreDescriptor`，再以 AES-256-GCM 加密为 `atlas.browser-context-restore-envelope/0.1`。AAD 绑定 ExecutionContract ID / Digest、Worker Identity、Actor Slot、BrowserContextRef 与 Expiry；Descriptor 绑定 Tenant / Project / Environment、Lease / Fence 和 Vault 元数据。Worker 只在内存中解密，并经 `SessionArtifactVault` 恢复到隔离的非持久化 BrowserContext；过期、篡改、Key Version 或 Scope 不一致一律拒绝。
5. Browser Worker 只追加类型化 `atlas.browser-runtime-report/0.1` 事实。链首必须是 `execution.started`；一个 `actionId` 在整条 Contract Report Chain 中只能提出一次，`action.proposed`、同 Actor 的 `policy.decided` 以及 ALLOW 后同 Proposal 的 `action.executed` 必须连续，不能插入 Node、Observation、Assertion、Artifact 或另一个 Action Report；Policy 后唯一允许替代 Receipt 的事实是 `execution.blocked`，用于明确终止无法形成可信 Receipt 的 Action，并强制最终结果不确定。完整链尾必须是 `execution.completed`。Sequence 与 `occurredAt` 单调，每条记录绑定前序 Digest 和自身 Content Digest。完成后不得继续追加。PostgreSQL 的不可变 Trigger、RLS 与最小权限再次执行基础链约束。
6. Playwright Adapter 只执行部署时显式注册的 exact-version `BrowserOperationRegistry` 和 `BrowserRouteRegistry`，不从请求、资产或 Agent 动态导入代码、解析任意绝对 URL 或执行任意 Locator。实际 Playwright / Chromium Revision 必须与冻结合约一致，Tool Catalog、Policy Bundle 和空 MCP Manifest Digest 必须与真正可执行的 Action、Risk、Semantic Role、Key、Route 与单次 Grant 规则一致。
7. DOM Action 必须引用当前 Observation 的 Target Handle、Page Revision 与单次 Nonce。执行前再次校验同一个 `ElementHandle` 的可见性、Element Key、Accessible Name 和 Semantic Fingerprint；导航或页面变化立即使旧 Observation 失效。Action ID 和 Grant 只能消费一次；可能产生副作用的 Timeout 或连接丢失记为 `OUTCOME_UNKNOWN`，任何失败或未知 Receipt 都强制本次执行终结为 `INCONCLUSIVE`。
8. HTTP 与 WebSocket 只允许 Session / Published Route Scope 中的精确 Origin。截图等字节证据只能由 `BrowserToolSession` 交给受信 `BrowserArtifactWriter` 完成 Redaction、对象存储、独立 Hash 与 Integrity Verification；`BrowserPlanOperation` 不得直接构造或返回 `EvidenceArtifactInput`，即使其 ID、Digest 与 `VERIFIED` 字段形状有效也必须拒绝。未配置 Writer 时 `CAPTURE_VIEW` 明确 fail-closed，不能把内存 Hash 或 Operation 自报元数据冒充已验证 Evidence。
9. Evidence Finalization 不只比较 Assertion / Artifact ID、Count 或 Content Digest。每条 `assertion.evaluated` 和 `artifact.captured` Report 必须携带对应完整 `AssertionResultInput` / `EvidenceArtifactInput` 的 Canonical Digest；服务从 Finalize Command 中重新计算每个完整输入 Digest，并与 Report Chain 的 exact 集合匹配。任何字段变化、缺失、重复或替换均拒绝，终结命令本身再以 Digest 支持 exact replay，不同命令不能覆盖既有终态。
10. Report Chain 只要出现 `execution.blocked`，或任一 `action.executed` Receipt 不是 `SUCCEEDED`，所有 Finalize Assertion Input 就必须是 `INCONCLUSIVE`，最终 Outcome 也只能是 `INCONCLUSIVE`。Policy Denial、`FAILED` 与 `OUTCOME_UNKNOWN` 不得被后续 Operation、Assertion 或完整 Artifact 集合覆盖为 `FAILED` / `PASSED`。
11. P6-01 只支持一个 Actor Slot。Multi-actor 调度、控制权仲裁和并行 BrowserContext 属于后续切片；当前绑定到多个 Actor 的 DebugRun 必须在副作用前拒绝。
12. 前端原型继续作为页面结构、DOM、布局、样式与交互的唯一权威。P6-01 不修改任何现有前端原型页面，只建立后端执行平面和内部协议。

## 后果

- Chromium 的 CPU、Memory、Crash 与并发上限被隔离在独立 Worker，API 保持控制面职责；Worker 即使被攻破也没有主数据库 Credential。
- Permit 泄漏本身不足以调用内部 API，HMAC Key 泄漏也不能跨越 Permit 的 Tenant / Run / Worker / Deadline Scope；报告和终结仍受应用层与 PostgreSQL 双重状态机约束。
- Staging / Production 不允许以 HTTP 连接 Runtime API，避免 Permit、签名请求元数据和执行包在传输链路上暴露；HMAC 仍负责请求真实性，TLS 负责机密性与服务端身份。
- Temporal 不会盲重试浏览器副作用。Blocked、连接中断或任何非成功 Receipt 会使本次结果强制进入 `INCONCLUSIVE`，需要后续显式恢复策略，而不是重复点击、借后续 Assertion 改写结果或伪造成功。
- Operation 不能自行产出 Artifact Receipt；所有被 Finalize 接受的 Artifact 都必须可追溯到可信 Writer，且其完整输入 Digest 已进入不可变 Report Chain。
- 当前默认 Operation / Route Registry 为空，未注册首个真实 SaaS Operation / Published Route 时执行 fail-closed；公共 DebugRun Start 尚未自动完成 Runtime Preparation、Contract Bind 与 Dispatch。
- P6-02 仍需提供生产 `BrowserArtifactWriter` / Evidence Redaction 与 Object Store 验证、Live Event / View Token 和断线重放。生产部署还必须实现容器级 Egress、DNS、UDP / WebRTC 限制；Playwright Request Routing 不是完整网络沙箱。
- 当前 BrowserContext Envelope 只支持单个活动 Key Version。生产 Key Ring、Rotation 与旧 Envelope 解密窗口尚未落地，配置切换必须采用有界排空或停机策略。
- Multi-actor 明确延后；在其状态机、Lease 与控制权协议完成前不会用单 Actor 实现模拟并发角色。
