# Atlas Database Migrations

Migration 由数据库 Owner 执行，API 使用独立的 `atlas_app` Runtime Role。开发环境命令：

```bash
ATLAS_DATABASE_URL='postgresql://atlas_owner:atlas_owner@127.0.0.1:5432/atlas' \
  uv run alembic upgrade head
```

约束：

- Migration 必须显式声明 Constraint、Foreign Key Index、RLS Policy 和降级影响。
- 不依赖 ORM 自动生成核心表。
- 不在 Migration 中写入环境相关业务数据。
- 生产变更先生成离线 SQL 并审查锁表风险。
