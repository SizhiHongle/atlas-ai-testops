# ADR-0004: API 契约与前端状态边界

- Status: Accepted
- Date: 2026-07-13
- Owners: Atlas Test Space
- Scope: HTTP、SSE、WebSocket、OpenAPI 与 Next.js 前端

## 背景

当前前端原型在一个 Client Component 内维护模拟业务数据和执行状态，并自行实现了一份简化 Workflow 校验。继续复制类型和规则会造成前后端协议漂移。

## 决策

- 公共 HTTP API 统一为 `/v1`，Worker 协议统一为 `/internal/v1`。
- FastAPI OpenAPI 与版本化 JSON Schema 是前端类型生成源。
- 查询使用 SWR 缓存和去重；命令通过统一 Client 携带 Idempotency、revision 和 Request ID。
- App Router 页面和 Layout 默认是 Server Component；只有表单、Canvas、Live 等交互边界使用 Client Component。
- Canvas、Live、Result Chart 和 Insight Chart 按路由动态加载。
- REST 负责命令，SSE 负责可重放事件，WebSocket 只负责实时画面和短生命周期控制帧。
- 前端不重复实现服务端 Publication Validation；本地只做即时交互提示。

## 后果

- 前后端类型由同一契约演进，CI 可以检测漂移。
- 当前单页原型需要逐路由拆分，但视觉资产可以复用。
- 查询缓存不是事实源，命令完成后必须按资源键重新验证。
