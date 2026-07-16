# Atlas TestOps Backend

Atlas AI 测试平台的 Python 3.14 模块化后端。

## 当前范围

- FastAPI Application Factory 与 `/v1` Router。
- 环境变量配置和 CORS。
- Liveness / Readiness 健康检查。
- 严格的 Workflow Graph Pydantic Schema。
- 版本化 `atlas.workflow-graph/0.1` 线协议和 JSON Schema。
- `snake_case` Python 字段与 `camelCase` JSON 字段双向映射。
- WorkflowDraft 的 semantic / layout revision 分离。
- exact version、Node、Port、Edge、DAG、必填输入、终止节点与 HARD Oracle 覆盖校验。
- FastAPI lifespan 管理的 Psycopg 3 异步连接池与数据库 readiness。
- Alembic Migration、Tenant RLS、不可变 Audit、Transactional Outbox 和 Idempotency 基表。
- 统一 `application/problem+json`、Request ID 与稳定错误码。
- Temporal 独立 Worker 入口和真实 Runtime Probe Workflow。
- FastAPI OpenAPI 导出与前端 TypeScript 类型漂移检查。
- Tenant、Project、Environment 的类型化 API、Cursor Pagination、幂等与 Revision CAS。
- PlatformUser、Membership、RBAC、Argon2id Password 与 HttpOnly Opaque Session。
- Session 实时授权复核、Idle / Absolute Expiry、Origin 防护、审计与 Outbox。
- TestRole、AccountPool、TestAccount、CredentialBinding 与实时容量投影。
- PostgreSQL 独占 AccountLease、Fencing Token、Heartbeat、TTL、Cooldown、隔离和 Reaper。
- Hash-only、短 TTL、最多一次兑换的 SecretGrant，以及精确 Environment Origin 策略。
- 闭包式 SecretProvider、Mock Identity Provider 与 Capability-driven Generic Password Adapter。
- ConnectorInstallation、实际 Capability Snapshot、事务外 Probe / Revision CAS，以及 Connector 级 Origin / Mode / Lease / Grant 失效策略。
- TestAccount 登录身份与角色健康检查、失败阈值、Cooldown / Quarantine、身份指纹与不可变状态迁移事实。
- 独立 Auth Session Temporal Worker、每 Lease Single Flight、Fence / Origin / Revision CAS 与受限人工操作票据。
- Playwright 非持久化 BrowserContext、AES-256-GCM + AAD SessionArtifact、S3-compatible Vault 与 Session Janitor。
- DataAtom / DataBlueprint exact-version 资产、确定性编译与三类发布证据门禁。
- PostgreSQL 权威的 FixtureRun、DataNodeAttempt、ResourceRecord、FixtureManifest 与 Runtime Evidence。
- exact operation registry、事务外 Provider I/O、保守 `OUTCOME_UNCERTAIN` 和独立 Fixture Temporal Worker。
- TestCase Catalog、WorkflowDraft 双 Revision、AI / 人工统一 Patch 与确定性 Test IR / PlanTemplate 编译。
- 不可变 DebugRun、单调事件、语义变更自动 OUTDATED，以及缺少 Browser Runtime 时的 fail-closed dispatch。
- `atlas.case-version/0.1`、精确 Role / Published Fixture / PASSED DebugRun 发布门禁、Author / Reviewer 分离与不可变 Node / Edge 快照。
- P6-00 `ExecutionContract`、确定性 `AssertionResult` / `EvidenceManifest`、受信 DebugRuntimeService、数据库结果重推导与 CaseVersion 实证复核；尚未开放公共完成 API。
- P6-01 独立、无控制面数据库配置的 Browser Temporal Worker；通过短期 Run-scoped Permit + HMAC 内部网关读取执行包、推进生命周期、追加报告和终结 Evidence。
- AES-256-GCM BrowserContext Restore Envelope，AAD 精确绑定 Contract / Worker / Actor / Session Scope，并只在 Worker 内存中打开后恢复隔离 Playwright BrowserContext。
- 严格 `BrowserRuntimeReport` Hash-chain / State Machine、Contract 内不可跨 Action 链复用 `actionId`、连续 Proposal / Policy / Receipt、完整 Assertion / Artifact Input Digest 绑定和 exact Finalization replay，以及 PostgreSQL 不可变 Trigger / RLS / 最小权限复核。
- 受限 Playwright Adapter：实际 Browser Revision 校验、部署注册 exact Operation / Route、精确 HTTP / WebSocket Origin、DOM Target Handle 重验证、单次 Action Grant 与保守 `INCONCLUSIVE`；Operation 不得直接返回 Artifact，证据字节必须经过可信 `BrowserArtifactWriter`。
- P6-02A 可信截图链：Playwright DOM Mask、canonical PNG、S3-compatible write-once 对象写入、独立 read-back SHA-256 / Size Verification，以及未配置 Evidence Store 时的 `CAPTURE_VIEW` fail-closed。
- 不暴露 ObjectRef 的 Evidence Manifest API、hash-only 且 Actor / Platform Session / Purpose / TTL / Max Reads 绑定的短期 Read Grant，以及响应前完整字节二次校验。
- P6-02B1 DebugRun Live 安全观察流：版本化 Snapshot / Event / Opaque Base64URL Cursor、`Last-Event-ID` 有序 replay、轻量 Snapshot / head 单 SQL、短事务轮询、Heartbeat comment、连接生命周期与进程内 Observer 容量上限；Snapshot 查询只构造安全 Run Projection，不物化完整 Test IR / PlanTemplate。
- P5-00A 正式任务执行宿主：`TaskPlanVersion → TaskRun → ExecutionUnit → UnitAttempt`、不可变 Run Manifest、Lifecycle / Quality / Hygiene 三轴、追加 Attempt / Event exact replay，以及 `20260716_0022` 的复合 Scope FK、Repository / PostgreSQL 双层 Plan-to-Manifest provenance、JSON 缺键 / null fail-closed、gapless 父行锁、不可变 Trigger、`FORCE RLS` 与最小权限。
- P5-00B1 调度前置边界：四类不可变 `ExecutionProfileVersion` / `IdentityProfileVersion` / `BrowserProfileVersion` / `DataProfileVersion` 正式宿主与发布门禁、`executionProfileVersionId` 统一命名、稳定 Run request digest 与 exact rerun lineage、Run / Attempt 确定性 Temporal identity 与 namespace 全局注册表、`MATERIALIZING → SEALED` 完整性证明、SEALED / Lifecycle / QUEUED Admission、受约束的后续 Attempt、Pending Workflow Start Intent，以及数据库拥有的 Run → Unit → Attempt Revision CAS。
- Profile、TaskPlanVersion、TaskRunManifest、TaskRun、ExecutionUnit、UnitAttempt 与 TaskExecutionEvent 的机器 Schema 已导出；P5-00B1 仍未开放公共 Task API，也不消费 Start Intent 或启动 Temporal 编排。
- 同步初始物化仍严格限制为最多 64 Units；Seal 会重算 Plan / Manifest / Unit / request digest，证明每个 Unit 和首个 Attempt 完整落地，并重验 PUBLISHED Profile、Case / Fixture exact binding、ACTIVE TEST/STAGING Environment 与当前 TestRole。超过 64 Units 的可恢复分区物化和容量验证留给后续 P5 切片，不能通过放宽当前事务伪装完成。
- `DebugRun=TERMINATED` 不封存事件日志；SSE 会跨过 `debug_run.terminated` replay 后续 `debug_run.snapshot_outdated`，到达当前 head 后继续 Poll，直到客户端断开或 Service 事件生成预算耗尽。Route 内 `_DebugLiveStreamingResponse` 使用 `maximum_connection_seconds` 加固定 1.0 秒 Close Grace 管理生成、Source close 与 Slot release；最后安装的 pure-ASGI `DebugLiveStreamSendDeadlineMiddleware` 使用相同 maximum 与 Close Grace，包住 `BaseHTTPMiddleware` 后的真实 client-facing `send`，阻塞写入到期会被取消。
- Live Event 使用 event-type allowlist，不原样转发事实 Payload；取消原因、Report / Chain Digest、ObjectRef、Authorization、Password、输入 Value 与未知字段不进入 SSE。`20260716_0019` 只提交 32 KiB Payload `CHECK ... NOT VALID` 可修复边界；`20260716_0020` 只先 Validate，再创建 UPDATE / DELETE 防护 Trigger；`20260716_0021` 通过 Alembic autocommit 执行 `DROP INDEX CONCURRENTLY IF EXISTS` 清理冗余 replay index，downgrade 以 `CREATE INDEX CONCURRENTLY IF NOT EXISTS` 恢复。若历史超限 Payload 使 0020 失败，版本保持 0019；修复后重试 0020，成功后再进入 0021。

首个真实 SaaS `PasswordLoginFlow`、生产 Secret Provider / KMS-backed Vault 和真实 SaaS Browser Operation / Route Registry 仍需部署侧提供。生产 Evidence Bucket 的 Object Lock / Versioning、Credential 分离与生命周期策略也属于部署责任。P6-02B1 只提供 DebugRun-scoped 只读 Observer；P5-00B1 已提供正式 UnitAttempt、Profile、Seal、CAS 与 Start Intent 前置事实，但 Task Temporal Workflow / Intent Consumer、公共 Task 控制面、正式 LiveSession、ControlLease、浏览器控制 Epoch / Fence、Human Takeover、持久化 ActionGrant、容器级 Egress / DNS / UDP / WebRTC 限制、BrowserContext Envelope Key Ring Rotation、公共 Start 自动 Preparation / Bind / Dispatch 与 Multi-actor 尚未完成；缺失时对应能力明确 fail-closed。架构决策见 `../documents/adr/`。

## 开发

```bash
uv sync
cp .env.example .env
ATLAS_DATABASE_URL='postgresql://atlas_owner:atlas_owner@127.0.0.1:5432/atlas' uv run alembic upgrade head
uv run uvicorn atlas_testops.main:app --reload
```

服务启动后可访问：

- API 文档：`http://127.0.0.1:8000/docs`
- OpenAPI：`http://127.0.0.1:8000/openapi.json`
- Liveness：`http://127.0.0.1:8000/v1/health/live`
- Readiness：`http://127.0.0.1:8000/v1/health/ready`
- 登录：`POST http://127.0.0.1:8000/v1/auth/login`
- 当前 Session：`GET http://127.0.0.1:8000/v1/session`
- 内部 Lease Acquire：`POST http://127.0.0.1:8000/internal/v1/account-leases`
- 内部 Secret Grant：`POST http://127.0.0.1:8000/internal/v1/account-leases/{leaseId}:issue-secret-grant`
- 内部 Auth Session：`POST http://127.0.0.1:8000/internal/v1/account-leases/{leaseId}:ensure-session`
- Connector 创建：`POST http://127.0.0.1:8000/v1/connector-installations`
- Connector 验证：`POST http://127.0.0.1:8000/v1/connector-installations/{connectorId}:validate`
- 账号健康验证：`POST http://127.0.0.1:8000/v1/test-accounts/{accountId}:verify`
- 健康检查历史：`GET http://127.0.0.1:8000/v1/test-accounts/{accountId}/health-checks`
- 账号状态迁移：`GET http://127.0.0.1:8000/v1/test-accounts/{accountId}/state-transitions`
- Fixture 启动：`POST http://127.0.0.1:8000/v1/projects/{projectId}/fixture-runs`
- Fixture 详情：`GET http://127.0.0.1:8000/v1/fixture-runs/{runId}`
- Fixture Manifest：`GET http://127.0.0.1:8000/v1/fixture-runs/{runId}/manifest`
- Fixture 资源账本：`GET http://127.0.0.1:8000/v1/fixture-runs/{runId}/resources`
- Fixture 释放：`POST http://127.0.0.1:8000/v1/fixture-runs/{runId}:release`
- CaseVersion 发布：`POST http://127.0.0.1:8000/v1/test-cases/{caseId}:publish`
- CaseVersion 详情：`GET http://127.0.0.1:8000/v1/case-versions/{versionId}`
- 内部 Browser 执行包：`GET http://127.0.0.1:8000/internal/v1/debug-runs/{runId}/browser-execution`
- 内部 Browser Ready / Start：`POST .../browser-execution:ready`、`POST .../browser-execution:start`
- 内部 Browser Report：`POST http://127.0.0.1:8000/internal/v1/debug-runs/{runId}/browser-reports`
- 内部 Browser Evidence Finalize：`POST .../browser-execution:finalize-evidence`
- Evidence Manifest：`GET http://127.0.0.1:8000/v1/debug-runs/{runId}/evidence`
- Evidence Read Grant：`POST http://127.0.0.1:8000/v1/debug-runs/{runId}/evidence/{artifactId}/read-tokens`
- Evidence Content：`GET http://127.0.0.1:8000/v1/evidence/artifacts/{artifactId}/content?purpose=INLINE`
- DebugRun Live Snapshot：`GET http://127.0.0.1:8000/v1/debug-runs/{runId}/live`
- DebugRun Live SSE：`GET http://127.0.0.1:8000/v1/debug-runs/{runId}/events/stream`（重连使用 `Last-Event-ID`）

上述以“内部 Browser”标记的 Runtime 端点不是公共用户 API；必须同时通过短期 Execution Permit 与 `Atlas-HMAC` Request Signature，且响应禁止缓存。Evidence 与 DebugRun Live 端点属于 Platform Session 保护的公共控制面读取接口。

## 质量检查

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
uv run python scripts/export_contracts.py --check
uv run python scripts/export_openapi.py --check
uv build
```

重新生成机器可读契约：

```bash
uv run python scripts/export_contracts.py
uv run python scripts/export_openapi.py
```

运行 Temporal Worker：

```bash
uv run atlas-temporal-worker
```

运行独立 Auth Session Worker：

```bash
uv run atlas-auth-session-worker
```

API 需要设置 `ATLAS_AUTH_SESSION_DISPATCH_ENABLED=true` 才会连接该 Task Queue。Vault Key、Object Store Credential 和 Secret Provider 只允许注入 Auth Session Worker，不能注入 API。`compose.yaml` 提供仅限本地开发的 MinIO 与静态 AES Key 示例；Staging / Production 配置校验会拒绝静态 Key 和自动建 Bucket。

运行独立 Fixture Worker：

```bash
uv run atlas-fixture-worker
```

API 需要设置 `ATLAS_FIXTURE_DISPATCH_ENABLED=true` 才会向 `ATLAS_FIXTURE_TASK_QUEUE` 提交耐久 Workflow。Provider Operation 只能通过部署时构造的 exact registry 注册；请求、资产协议和数据库均不能注入动态 URL、Module、Script 或 Callable。本地与测试环境提供确定性 Mock Provider，生产环境缺少已审核 Provider 时会 fail-closed。

运行独立 Browser Worker：

```bash
uv run atlas-browser-worker
```

API 需要设置 `ATLAS_BROWSER_RUNTIME_ENABLED=true`，并配置 Permit Key、Request HMAC Key 与 BrowserContext Envelope Key；Browser Worker 只获得 Request HMAC / Envelope Key、SessionArtifact Vault、受信 Tool / Policy Digest 和 API Origin，不获得主数据库 DSN。Local / Test / Development 可使用 HTTP Runtime API 调试，Staging / Production 必须配置 HTTPS Origin，否则 Worker 启动校验失败。默认 Operation / Route Registry 为空，未由部署代码注册 exact SaaS Operation / Published Route 时执行会 fail-closed。

`CAPTURE_VIEW` 由 P6-02A `BrowserArtifactWriter` 在 Playwright DOM 中先 Mask 输入控件、可编辑内容和显式敏感节点，再生成去元数据、RGB、白底 alpha flatten、固定压缩的 canonical PNG。对象以作用域化 write-once Key 写入，并在独立回读的完整 SHA-256 与大小一致后才生成 `VERIFIED` Receipt。`BrowserPlanOperation` 不能直接返回 `EvidenceArtifactInput` 绕过 Writer；Evidence Store 未配置时不会退化为内存 Screenshot Hash 或 Operation 自报元数据。

Evidence Manifest 不返回 ObjectRef。先用有效 Platform Session 签发短期 Read Grant，再以 `Authorization: Atlas-Evidence <token>` 读取内容；Token 不接受 Query 传递，数据库只保存 SHA-256 Hash。Grant 绑定 Actor、Platform Session、Artifact、Purpose、TTL 与 Max Reads；API 在数据库事务外读取完整对象，并在发送响应前重新校验不可变 Receipt 的字节数和 SHA-256。所有响应均使用 `private, no-store` 与 `nosniff`。

Browser Workflow 使用一个有 Heartbeat、`maximum_attempts=1` 的长时 Activity，避免对结果未知的导航、点击或输入进行盲重试。Action Report 必须连续，且同一 `actionId` 在完整 Contract Chain 中只能对应一次 Proposal / Policy / Receipt 链；Policy 后可用 `execution.blocked` 明确终止无法形成可信 Receipt 的 Action。`execution.blocked` 或任一非 `SUCCEEDED` Receipt 强制所有 Assertion 和最终 Outcome 为 `INCONCLUSIVE`。Finalize 会重算完整 Assertion / Artifact Input Digest，不接受只匹配 ID、Count 或 Content Digest 的替换输入。Playwright Request / WebSocket Origin 限制不是容器网络沙箱；生产部署仍必须额外限制 Egress、DNS、UDP 与 WebRTC。

## 环境变量

所有服务变量使用 `ATLAS_` 前缀。数组值使用 JSON，例如：

```bash
ATLAS_CORS_ORIGINS='["http://localhost:5173","http://127.0.0.1:5173"]'
ATLAS_SECRET_GRANT_TTL_SECONDS=60
ATLAS_ACCOUNT_HEALTH_VERIFICATION_TIMEOUT_SECONDS=30
ATLAS_ACCOUNT_HEALTH_ATTEMPT_TTL_SECONDS=120
ATLAS_AUTH_SESSION_DISPATCH_ENABLED=true
ATLAS_AUTH_SESSION_TASK_QUEUE=atlas-auth-session
ATLAS_AUTH_SESSION_WORKER_MAX_CONCURRENCY=4
ATLAS_FIXTURE_DISPATCH_ENABLED=true
ATLAS_FIXTURE_TASK_QUEUE=atlas-fixture
ATLAS_FIXTURE_ACTIVITY_TIMEOUT_SECONDS=330
ATLAS_FIXTURE_CLEANUP_GRACE_SECONDS=900
ATLAS_FIXTURE_WORKER_MAX_CONCURRENCY=8
ATLAS_BROWSER_RUNTIME_ENABLED=true
ATLAS_BROWSER_RUNTIME_TASK_QUEUE=atlas-browser
ATLAS_BROWSER_RUNTIME_WORKER_IDENTITY=browser-worker
ATLAS_BROWSER_RUNTIME_ACTIVITY_TIMEOUT_SECONDS=900
ATLAS_BROWSER_RUNTIME_HEARTBEAT_TIMEOUT_SECONDS=20
ATLAS_BROWSER_RUNTIME_PERMIT_TTL_SECONDS=1020
ATLAS_DEBUG_LIVE_POLL_INTERVAL_MS=500
ATLAS_DEBUG_LIVE_HEARTBEAT_SECONDS=10
ATLAS_DEBUG_LIVE_MAX_CONNECTION_SECONDS=30
ATLAS_DEBUG_LIVE_BATCH_SIZE=100
ATLAS_DEBUG_LIVE_MAXIMUM_CONNECTIONS=64
ATLAS_EVIDENCE_OBJECT_STORE_ENDPOINT=127.0.0.1:9000
ATLAS_EVIDENCE_OBJECT_STORE_BUCKET=atlas-evidence-artifacts
ATLAS_EVIDENCE_OBJECT_STORE_SECURE=false
ATLAS_EVIDENCE_READ_GRANT_TTL_SECONDS=60
ATLAS_EVIDENCE_READ_GRANT_MAX_READS=8
ATLAS_EVIDENCE_READ_MAXIMUM_BYTES=67108864
ATLAS_EVIDENCE_CAPTURE_MAXIMUM_RAW_BYTES=33554432
ATLAS_EVIDENCE_CAPTURE_MAXIMUM_PIXELS=33177600
```

Browser Runtime 的 Key 和执行平面配置必须按 API / Worker 最小权限拆分。以下只展示变量名，值必须由部署 Secret Manager 注入，不得提交到仓库：

```bash
# API only
ATLAS_BROWSER_RUNTIME_PERMIT_KEY_BASE64=<base64-at-least-32-bytes>

# API and Browser Worker
ATLAS_BROWSER_RUNTIME_REQUEST_HMAC_KEY_BASE64=<base64-at-least-32-bytes>
ATLAS_BROWSER_CONTEXT_ENVELOPE_KEY_BASE64=<base64-exactly-32-bytes>
ATLAS_BROWSER_CONTEXT_ENVELOPE_KEY_VERSION=<key-version>

# Browser Worker only
ATLAS_BROWSER_RUNTIME_API_BASE_URL=http://127.0.0.1:8000
ATLAS_BROWSER_REVISION=<playwright-and-chromium-revision>
ATLAS_BROWSER_TOOL_CATALOG_REF=<exact-version-ref>
ATLAS_BROWSER_POLICY_BUNDLE_REF=<exact-version-ref>
ATLAS_BROWSER_MCP_SERVER_MANIFEST_DIGEST=sha256:<digest>
ATLAS_BROWSER_TOOL_SCHEMA_DIGEST=sha256:<digest>
ATLAS_BROWSER_POLICY_DIGEST=sha256:<digest>

# API and Browser Worker; inject different read/write credentials per process
ATLAS_EVIDENCE_OBJECT_STORE_ACCESS_KEY=<process-specific-access-key>
ATLAS_EVIDENCE_OBJECT_STORE_SECRET_KEY=<process-specific-secret-key>
```

上例的 HTTP Origin 仅用于 Local / Test / Development；Staging / Production 必须使用 `https://...`。

Evidence Object Store 的 Endpoint、Access Key 与 Secret Key 必须完整且非空。`ATLAS_EVIDENCE_OBJECT_STORE_CREATE_BUCKET=true` 只允许 Local / Test / Development；Staging / Production 强制 `ATLAS_EVIDENCE_OBJECT_STORE_SECURE=true` 并拒绝自动建 Bucket。连接 / 读取 Timeout、有限 Retry 与并发上限可配置；Browser Worker 允许 `capture_view` 时必须配置 Store，API 未配置独立 Reader 时 Manifest 仍可查询，但 Read Grant 与 Content 读取返回受控 503。

当前 Envelope Codec 只接受一个 Key Version；生产 Key Ring / Rotation 尚未落地，切换 Key 前必须排空旧 Envelope 或采用停机窗口。
