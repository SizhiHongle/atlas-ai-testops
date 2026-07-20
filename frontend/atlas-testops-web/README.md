# Atlas TestOps Web

Atlas AI TestOps 的生产前端。`../atlas-ai-testops-prototype` 只作为视觉与交互权威保留；本包不修改原型源码，而是在真实路由、Session、RBAC 和 OpenAPI 契约上重建同一产品体验。

## 技术边界

- Next.js App Router 兼容路由，由 Vinext/Vite 构建并运行在 Cloudflare Workers。
- React 19、TypeScript strict、CSS Modules 与 Design Tokens。
- TanStack Query 管理 Server State，Zustand 只管理移动导航等 Client State，URL 管理可分享的业务选择。
- `openapi-fetch` 使用 `contracts/openapi.json` 生成的 `shared/api/schema.d.ts`。
- 页面只调用 Feature Service/Query；后端 DTO 经 Mapper 转换为 ViewModel。
- `/api/atlas/v1/*` 是同源 BFF。浏览器不直接读取后端 Origin，也不接触服务端环境变量。
- 生产运行时没有 Mock、Demo fallback 或静默默认业务数据。后端失败会进入明确的 Loading/Error/Empty 状态。

## 业务覆盖

| 领域 | 已接入能力 |
| --- | --- |
| Auth | Password Login、HttpOnly Session、Logout、Workspace Boundary、RBAC |
| Space | Identity、Fixture、Case、Task、Result、Insight 的真实聚合看板 |
| Identity | Environment、Role、AccountPool、Account Capacity、Account |
| Fixture | DataAtom、DataBlueprint Catalog 与创建 |
| Case | TestCase、WorkflowDraft、WorkflowPatch 预检/应用、Layout Patch、DebugRun、CaseVersion 发布 |
| Task | 全局 Task Center、TaskPlan、精确 TaskPlanVersion 组装、Manual/Schedule Trigger、TaskRun、ExecutionUnit、Pause/Resume/Cancel |
| Live | 批量 TaskRun Cockpit、UnitAttempt Snapshot、Epoch/Fencing Control、Debug Test Theatre、单调事件/SSE、EvidenceManifest 与短期 Read Grant |
| Result | exact ResultSnapshot、FailureCluster/UnitResolution 影响图、人工 append-only Classification Revision、Task Gate |
| Insight | 7/30/90 天 Brief、TaskPlan Quality Terrain、DatasetCut Provenance、InsightSnapshot Pin / exact deep link |

界面中仍禁用的操作均对应后端没有公开契约的能力，例如 AI WorkflowPatch 生成、UnitAttempt screencast/证据列表/流式日志、完整证据包导出、Feishu OAuth、Identity 聚合创建和 Profile Catalog。DebugRun 已接入公开的 Live Snapshot、单调事件、SSE 与 Evidence Service；前端不会用手填 UUID、静态 CRM 画面或演示行为替代缺失 API。

## 环境要求

- Node.js `>= 22.13.0`
- pnpm `10.15.1`
- 可访问的 Atlas Backend

```bash
pnpm install --frozen-lockfile
cp .env.example .env.local
pnpm dev
```

必填部署配置：

```dotenv
ATLAS_API_ORIGIN=https://atlas-api.example.com
NEXT_PUBLIC_ATLAS_TENANT_ID=<tenant-uuid>
NEXT_PUBLIC_ATLAS_PROJECT_ID=<project-uuid>
NEXT_PUBLIC_ATLAS_WORKSPACE_LABEL=客户运营 · CRM
```

`ATLAS_API_ORIGIN` 只在 Worker/BFF 中读取；所有 `NEXT_PUBLIC_*` 值会进入浏览器构建，只能放公开的工作空间标识，不能放 Secret。

## 质量门禁

```bash
pnpm check
pnpm test:e2e
pnpm build
```

- `check:api`：确认生成类型与 `contracts/openapi.json` 没有漂移。
- `typecheck`：TypeScript strict。
- `lint`：Next/React/ESLint。
- `test`：Mapper、Digest、Permission Guard 与 WorkflowPatch Builder 单元/组件测试。
- `test:e2e`：桌面和移动端的登录、Case Workbench、Task Center/Builder、批量 Live Cockpit、Debug Theatre、Result Center、Insight Terrain、Evidence Read Grant、Control Fencing、Gate Evaluation、Classification Review、DatasetCut Pin 与 immutable Snapshot deep link。
- Visual Regression：Playwright 固定 Case Workbench、Task Center、Task Builder、Batch Live、Debug Live、Result Center 和 Insight Terrain 的桌面/移动截图。

契约更新后执行：

```bash
pnpm generate:api
pnpm check:api
```

## 安全与运行时

- BFF 只允许 `/v1`，剥离 hop-by-hop 与伪造的 forwarding headers。
- 鉴权 API 响应统一 `Cache-Control: no-store`，并贯穿 `X-Request-ID`。
- BFF 请求体上限为 10 MiB；超限返回 `application/problem+json`。
- 写操作使用后端要求的 `Idempotency-Key`、`If-Match` Revision 或 Control Epoch。
- 生产页面启用 CSP、HSTS、frame deny、nosniff、Referrer/Permissions/Cross-Origin Policy。
- Query 只重试可恢复的服务端故障；4xx 和 Mutation 不自动重放。
- Error Boundary 与 Query/Mutation 错误会发出无业务 Payload 的 `atlas:client-error` 事件，便于接入正式监控 SDK。

## 部署

生产构建：

```bash
pnpm build
```

构建结果位于 `dist/`，并包含既有 Sites 项目标识 `.openai/hosting.json`。部署环境必须通过 Secret/Environment Variables 注入上述配置；不要提交 `.env.local`。
