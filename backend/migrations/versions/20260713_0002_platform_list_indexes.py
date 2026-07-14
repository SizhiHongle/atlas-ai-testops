"""补充 Platform Cursor 查询索引。

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0002"
down_revision: str | None = "20260713_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """按 Tenant 和稳定时间顺序支持 Cursor Pagination。"""

    op.execute(
        """
        create index project_tenant_created_idx
          on atlas.project (tenant_id, created_at desc, id desc)
        """
    )
    op.execute(
        """
        create index environment_project_created_idx
          on atlas.environment (tenant_id, project_id, created_at desc, id desc)
        """
    )


def downgrade() -> None:
    """移除本 Migration 创建的索引。"""

    op.execute("drop index if exists atlas.environment_project_created_idx")
    op.execute("drop index if exists atlas.project_tenant_created_idx")
