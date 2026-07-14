# ADR-0002: 模块化单体与事实权威边界

- Status: Accepted
- Date: 2026-07-13
- Owners: Atlas Test Space
- Scope: 后端模块、进程部署、运行事实与投影

## 背景

设计文档覆盖身份、数据、用例、任务、现场、结果和洞察多个领域。过早拆成微服务会在领域模型尚未稳定时引入跨服务事务、契约复制和部署负担；全部能力塞入一个 Web 进程又会让浏览器、编排和查询互相争用资源。

## 决策

首期采用一个 Python 模块化单体代码库，并部署为四类独立进程：

1. FastAPI API / Control Plane。
2. Temporal Worker。
3. Playwright Browser Worker。
4. Projector / Janitor Worker。

模块按 `domain`、`application`、`infrastructure`、`api` 和 `workers` 分层。领域模块不得直接依赖框架或基础设施。

事实权威固定如下：

- PostgreSQL 保存业务事实、版本、租约、审计和 Outbox。
- Temporal 保存进行中 Workflow History，不作为查询库。
- 对象存储保存大型 Artifact 内容，PostgreSQL 保存引用和摘要。
- SSE、WebSocket、缓存与投影均可重建。

## 后果

- 领域内事务保持本地，早期开发和测试成本可控。
- 不同进程可以独立扩缩容和设置资源上限。
- 未来只有在容量和团队边界得到证据后才拆服务；拆分时沿现有模块和版本化契约进行。
