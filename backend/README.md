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

首个真实 SaaS `PasswordLoginFlow`、生产 Secret Provider 与 KMS-backed Vault 仍需部署侧提供；缺失时 Password Session 明确 fail-closed。架构决策见 `../documents/adr/`。

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
```
