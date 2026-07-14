# Atlas AI 测试平台交付包 v0.3

## 目录

- `documents/`
  - 8 份版本化技术设计文档、Python 后端 ADR、统一领域术语和文档状态索引。
- `contracts/`
  - 由 Pydantic / FastAPI 导出的版本化 JSON Schema、OpenAPI 与前端类型源。
- `frontend/atlas-ai-testops-prototype/`
  - 当前 Atlas AI 测试平台前端原型源码，包含主工作台与登录页。
- `backend/`
  - Python 3.14 模块化后端，包含 Platform RBAC、Identity Catalog、Connector/Capability、Account Health Verification、Account Lease/Fencing、Secret Grant/Adapter、独立 Auth Session Worker、加密 SessionArtifact、DataAtom / DataBlueprint 资产控制面、耐久 FixtureRun 与资源账本。
- `compose.yaml`
  - 本地 PostgreSQL、Temporal Dev Server、MinIO、独立 Auth Session Worker 与 Fixture Worker。
- `documents/IMPLEMENTATION_PROGRESS.md`
  - 持续更新的阶段进度、验证证据和下一步。

## 启动本地基础设施

环境要求：Docker Desktop、`uv >= 0.11`、Node.js `>= 22.13.0`。

```bash
make infra-up
make migrate
```

默认本地入口：

- PostgreSQL：`127.0.0.1:5432`
- Temporal：`127.0.0.1:7233`，Web UI：`http://127.0.0.1:8233`
- MinIO：`http://127.0.0.1:9000`，Console：`http://127.0.0.1:9001`

## 本地运行前端

环境要求：Node.js `>= 22.13.0`。

```bash
cd frontend/atlas-ai-testops-prototype
npm ci
cp .env.example .env.local
npm run dev
```

代码检查与生产构建：

```bash
npm run lint
npm run build
```

## 本地运行后端

环境要求：`uv >= 0.11`，Python 由 `uv` 按 `.python-version` 自动管理。

```bash
cd backend
uv sync
cp .env.example .env
uv run uvicorn atlas_testops.main:app --reload
```

后端质量检查：

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest
uv run python scripts/export_contracts.py --check
uv run python scripts/export_openapi.py --check
uv build
```

前后端启动后，`http://127.0.0.1:5173/system-status` 会使用生成的 TypeScript API 类型读取真实 readiness；`/login` 会在配置 Tenant / Project 后使用 HttpOnly Cookie Session 登录。

## 完整验证

基础设施和 Migration 已启动后执行：

```bash
make verify
```

## 说明

- 交付包未包含 `node_modules`、`dist`、`.wrangler`、Git 历史和本地缓存。
- 当前前端属于可交互产品原型，界面结构、布局、样式和既有交互是实现的视觉权威；业务数据按阶段替换为真实 API，尚未实施的执行过程仍使用演示数据。
- 打包前已通过 TypeScript 检查与生产构建。
- 后端已接入 PostgreSQL/Psycopg 连接池、Alembic、RLS、Transactional Outbox、幂等、Platform RBAC、Argon2id 与 Opaque Session、测试账号目录、ConnectorInstallation / Capability Snapshot、账号登录身份与角色健康检查、Lease/Fencing、一次性 Secret Grant、独立 Auth Session Worker、AES-GCM SessionArtifact Vault，以及由独立 Temporal Worker 驱动的 FixtureRun、Node Attempt、Resource Ledger、Manifest 和 Runtime Evidence。首个真实 SaaS Flow、生产 Secret Provider、KMS 配置，以及 Fixture 取消后补偿、Reconcile 和孤儿资源扫描仍需后续落地。
