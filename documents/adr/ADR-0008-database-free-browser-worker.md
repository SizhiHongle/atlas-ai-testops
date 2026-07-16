# ADR-0008: 无数据库 Browser Worker 与受限浏览器执行协议

- Status: Accepted
- Date: 2026-07-15
- Owners: Atlas Test Space
- Scope: P6-01 Browser Worker、Temporal 编排、内部 Runtime 网关、BrowserContext 恢复、Playwright Action 与报告链；P6-02A Evidence 与 P6-02B1 DebugRun Live 实施更新

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
- P6-02A 已补齐生产 `BrowserArtifactWriter` / Evidence Redaction、Object Store read-back 验证与短期 Read Grant；P6-02B1 已提供 DebugRun-scoped Live Snapshot、SSE、Opaque Cursor 和 `Last-Event-ID` 断线重放。P5-00A 已建立正式 UnitAttempt 宿主，P6-02B2 仍需在其上提供 LiveSession、ControlLease、浏览器控制 Epoch / Fence、Human Takeover 与持久化 ActionGrant。生产部署还必须实现 Bucket Object Lock / Versioning、读写 Credential 分离，以及容器级 Egress、DNS、UDP / WebRTC 限制；Playwright Request Routing 不是完整网络沙箱。
- 当前 BrowserContext Envelope 只支持单个活动 Key Version。生产 Key Ring、Rotation 与旧 Envelope 解密窗口尚未落地，配置切换必须采用有界排空或停机策略。
- Multi-actor 明确延后；在其状态机、Lease 与控制权协议完成前不会用单 Actor 实现模拟并发角色。

## P6-02A 实施更新

P6-02A 已在本 ADR 的 Writer 信任边界内实现截图证据链：Playwright 在截图编码前对输入控件、可编辑内容和显式敏感节点做 DOM Mask；Writer 再生成去元数据、RGB、白底 alpha flatten、固定压缩的 canonical PNG，以作用域化 write-once Key 写入 S3-compatible Store，并在独立完整回读的 SHA-256 与 Size 一致后才签发 `VERIFIED` Receipt。未完整配置 Store 时 `capture_view` 继续 fail-closed。

公共 Evidence API 只暴露不可变 Manifest 安全投影。内容读取需要普通 Platform Session 和独立 `Atlas-Evidence` Header；Opaque Token 只在签发时出现，数据库只保存 Hash，并绑定 Tenant / Project / Run / Contract / Artifact / Actor / Session / Purpose、最长 120 秒 TTL 与有界 Max Reads。Grant 兑换完成并关闭短事务后，API 才读取有界完整对象并重新核对 Receipt 的 SHA-256 与 Size，绝不返回未验证或部分字节。

P6-02A 不修改前端原型。后续 P6-02B1 同样只建立后端 Live 观察协议，不修改既有页面、DOM、布局、CSS 或交互。

## P6-02B1 实施更新

P6-02B1 以现有 `debug_run_event` 作为唯一事实源，实现 `DebugRun` 作用域的只读 Live Observer，而不提前创建尚无 `UnitAttempt` 宿主的 `LiveSession`。首次订阅通过单条无行锁 SQL 在同一 MVCC Snapshot 中直接构造轻量 `DebugLiveRunProjection`、最新事件与 `headSeq`，先发 `debug_run.live.snapshot`；查询不物化或反序列化完整 DebugRun 的 Test IR / PlanTemplate 等大快照。Opaque Cursor 使用 canonical、无填充 Base64URL JSON 绑定 exact DebugRun 与 `afterSeq`；重连只接受 `Last-Event-ID`，跨 Run、损坏、超长或超前 Cursor 在开始 SSE 响应前拒绝。

SSE 每次只在独立有界短事务内读取 `seq > afterSeq ORDER BY seq` 的一批事件，事务关闭后才向网络 yield；Poll Sleep、Heartbeat 与客户端背压均不持 PostgreSQL 连接。Heartbeat 是无 `id` 的 comment，不推进 Cursor。`DebugRun=TERMINATED` 不封存事件日志，Draft 的后续语义变化仍可能追加 Lifecycle 保持 `TERMINATED` 的 `debug_run.snapshot_outdated`；Stream 必须跨过 `debug_run.terminated` replay 到当前 head，然后继续 Poll 到客户端断开或 Service 的 `maximum_connection_seconds` 事件生成预算耗尽。

进程内 Observer Limiter 在容量耗尽时立即返回 429 + `Retry-After`，避免等待请求占用数据库或 Worker 资源。Route 内 `_DebugLiveStreamingResponse` 使用 `maximum_connection_seconds` 加固定 1.0 秒 Close Grace 约束生成与关闭路径，并在 `finally` 中关闭 Source、释放 Observer Slot；最后安装的 pure-ASGI `DebugLiveStreamSendDeadlineMiddleware` 使用相同的 maximum 与 Close Grace，包住 `BaseHTTPMiddleware` 重包装后的真实 client-facing `send`。两层职责不同：前者拥有业务 Source 生命周期，后者保证最终网络写入上界；Close Grace 只用于正常结束 Body、关闭 Source 与释放 Slot，不生成新业务事件。网络写到期仍阻塞时由 pure-ASGI 层强制取消，避免 Observer 容量被无限占用。

Live Event 不原样转发事实 Payload，而是按事件类型构造 allowlist 投影。取消 `reason`、Report / Chain Digest、ObjectRef、Authorization、Password、输入 Value 与未知字段不会进入 SSE。数据库加固分为三个版本：`20260716_0019` 只增加 32768 bytes `CHECK ... NOT VALID` 并提交，不扫描历史 Payload；`20260716_0020` 只先 `VALIDATE CONSTRAINT`，成功后创建 `atlas.prevent_fact_mutation()` UPDATE / DELETE Trigger；`20260716_0021` 独立进入 Alembic `autocommit_block()`，使用 `DROP INDEX CONCURRENTLY IF EXISTS` 删除已被 `(debug_run_id, seq)` Unique Constraint 覆盖的冗余 replay index，downgrade 使用 `CREATE INDEX CONCURRENTLY IF NOT EXISTS` 并发恢复。历史超限 Payload 使 0020 失败时，事务回滚且版本保持 0019；修复后可重试 0020，成功后才进入 0021 的低阻塞索引清理。没有新增重复事件表或长期数据库 Session。公共 Snapshot 与 Stream 都复用 Platform Session / Project 可见性，OpenAPI 明确声明 `PlatformSession`，响应禁止缓存与代理转换。

本更新不实现 Frame、Command、Action 或人工输入。P5-00A 已建立 `UnitAttempt`，P6-02B2 才能在该正式宿主上引入 `LiveSession`、`ControlLease`、浏览器控制 Epoch / Fence、Safe Point / Quiesce、Human Takeover，以及持久化且绑定 Epoch / Fence 的 `ActionGrant`；在这些协议完成前不能把 DebugRun SSE 或 P6-01 Worker 内部单次 Action 校验描述为人工控制面。
