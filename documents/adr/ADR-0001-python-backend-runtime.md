# ADR-0001: Python 后端运行时与契约边界

- Status: Accepted
- Date: 2026-07-13
- Owners: Atlas Test Space
- Scope: API、领域模型、持久化编排、Browser Worker 与数据访问

## 背景

早期设计稿以 Node.js / TypeScript 作为控制面和 Worker 的默认实现语言，仓库中的后端基础包已经采用 Python 3.14、FastAPI 和 Pydantic。两套基线并存会导致领域对象、Schema、运行时约束和招聘运维成本持续分叉。

截至 2026-07-13，Temporal Python SDK、Playwright Python 和 Psycopg 均已覆盖 Python 3.14 所需能力，因此无需为了持久化编排、浏览器自动化或 PostgreSQL 接入额外维护 Node.js 后端运行时。

## 决策

Atlas 采用单一 Python 后端运行时，前端继续使用 Next.js / TypeScript。

| 层次 | 技术基线 | 边界 |
| --- | --- | --- |
| Web 前端 | Next.js / TypeScript | 只消费版本化 HTTP、SSE 和 WebSocket 契约 |
| API / 控制面 | Python 3.14 / FastAPI | 认证、资产、命令、查询与策略入口 |
| 领域与契约 | Pydantic / JSON Schema 2020-12 | Python 模型是实现源，导出的 JSON Schema 是跨语言线协议 |
| 持久化编排 | Temporal Python SDK | Workflow 仅包含确定性编排，外部 I/O 全部进入 Activity |
| Browser Worker | Playwright Python async API | BrowserContext 隔离、动作、观察、证据与清理 |
| 数据访问 | PostgreSQL / Psycopg 3 | 资产、追加事实、租约、Outbox、投影与审计 |
| 可观测性 | OpenTelemetry | 贯通 API、Workflow、Activity、Browser 与数据层 |

## 契约规则

1. Python 内部字段使用 `snake_case`，对外 JSON 使用 `camelCase`。
2. 跨进程和跨语言契约不得以 TypeScript interface、Python class 或 Word 代码块作为唯一事实源。
3. 已实现契约必须具有稳定 `schemaVersion`，并提交由 Pydantic 导出的 JSON Schema。
4. Published 资产只引用 exact version；运行快照同时保存版本 ID、内容摘要和策略版本。
5. Workflow Graph v0.1 的 `semanticType` 采用精确相等。类型继承和可赋值关系延后到显式 Type Registry 上线后实现。

## 校验边界

Workflow Graph 使用两阶段校验，避免把数据库查询塞进纯函数：

1. Pure structural validation：ID、端口、Edge、DAG、必填输入、终止节点与 HARD Oracle 覆盖。
2. Publication validation：查询资产注册表，检查 exact version、Published 状态、权限、能力、敏感数据边界、环境可用性和 Cleanup 合同。

第一阶段由当前 `validate_workflow_graph` 实现；第二阶段在资产仓储和发布 API 落地时实现。

## 进程与资源模型

- FastAPI、Temporal Worker 和 Browser Worker 独立进程或容器扩缩，不在 Web 进程内启动浏览器。
- I/O 使用 `asyncio`；CPU 密集型解析、图像处理或模型本地推理使用独立进程池或专用 Worker。
- Browser Worker 必须配置并发上限、内存上限、超时、BrowserContext 回收和崩溃后的 Janitor。
- 不使用 `gevent` patch；Temporal 和 Browser Worker 保持原生 `asyncio` 事件循环。

## 后果

### 正向

- 后端只维护一种语言、依赖管理、类型检查和部署基线。
- FastAPI、Pydantic、Temporal 与 Playwright 可以共享领域模型和观测上下文。
- 文档示例、测试和生产实现可以直接对应。

### 代价

- 早期 TypeScript 示例需要迁移为 Python 或语言中立 JSON。
- 如果未来引入只能运行于 Node.js 的第三方插件，必须通过独立 Connector Adapter 接入，不能改变平台主运行时。

## 官方依据

- Temporal Python SDK: https://pypi.org/project/temporalio/
- Temporal Python SDK repository: https://github.com/temporalio/sdk-python
- Playwright Python installation: https://playwright.dev/python/docs/intro
- Psycopg: https://pypi.org/project/psycopg/
