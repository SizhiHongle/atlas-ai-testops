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

首个真实 SaaS `PasswordLoginFlow`、生产 Secret Provider / KMS-backed Vault、生产 Evidence / Redaction Writer 和真实 SaaS Browser Operation / Route Registry 仍需部署侧提供。容器级 Egress / DNS / UDP / WebRTC 限制、BrowserContext Envelope Key Ring Rotation、公共 Start 自动 Preparation / Bind / Dispatch 与 Multi-actor 也尚未完成；缺失时 Password Session、Artifact Capture 和 Debug execution 明确 fail-closed。架构决策见 `../documents/adr/`。

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

上述 Browser Runtime 端点不是公共用户 API；必须同时通过短期 Execution Permit 与 `Atlas-HMAC` Request Signature，且响应禁止缓存。

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

`CAPTURE_VIEW` 要求 P6-02 的生产 `BrowserArtifactWriter` 完成 Redaction、对象存储、独立 Hash 与 Verification。`BrowserPlanOperation` 不能直接返回 `EvidenceArtifactInput` 绕过 Writer；当前不会把内存 Screenshot Hash 或 Operation 自报元数据冒充可信 Evidence。

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
```

上例的 HTTP Origin 仅用于 Local / Test / Development；Staging / Production 必须使用 `https://...`。

当前 Envelope Codec 只接受一个 Key Version；生产 Key Ring / Rotation 尚未落地，切换 Key 前必须排空旧 Envelope 或采用停机窗口。
