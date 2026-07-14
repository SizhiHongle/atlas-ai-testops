# ADR-0003: PostgreSQL Migration、RLS 与 Outbox

- Status: Accepted
- Date: 2026-07-13
- Owners: Atlas Test Space
- Scope: 数据访问、Tenant 隔离、Schema 演进与异步发布

## 背景

平台数据同时包含多租户资产、排他租约、不可变执行事实和可重建投影。仅依赖应用层过滤无法充分证明 Tenant 隔离，直接在事务中发布消息又会产生数据库提交与消息发送不一致。

## 决策

- Runtime 使用 Psycopg 3 `AsyncConnectionPool`，不为每个请求创建新连接。
- Alembic 管理版本化 migration；核心约束和 RLS 使用显式 SQL，禁止依赖自动推断。
- 多租户表保存 `tenant_id`，启用并强制 PostgreSQL RLS。
- 请求事务使用 `set_config` 设置 `atlas.tenant_id` 和 `atlas.actor_id`，不接受客户端直接指定数据库上下文。
- 业务状态与 Outbox Event 在同一事务写入，由独立 Projector / Dispatcher 至少一次投递。
- Consumer 必须使用 `event_id` 去重；Outbox 领取可使用 `FOR UPDATE SKIP LOCKED`。
- 对外实体使用应用生成的 UUIDv7，时间使用 UTC `timestamptz`。

## 后果

- Tenant 隔离可通过真实数据库测试验证。
- 事件可能重复但不会静默丢失，Consumer 必须具备幂等性。
- Migration 审查需要同时检查约束、Foreign Key Index、RLS Policy 和回滚影响。
