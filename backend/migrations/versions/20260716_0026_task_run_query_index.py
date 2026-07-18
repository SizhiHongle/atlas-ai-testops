"""Add the Project TaskRun keyset query index.

Revision ID: 20260716_0026
Revises: 20260716_0025
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0026"
down_revision: str | None = "20260716_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Support stable Project TaskRun keyset reads under tenant RLS."""

    op.execute(
        """
        create index task_run_project_requested_idx
          on atlas.task_run (
            tenant_id, project_id, requested_at desc, id desc
          )
        """
    )


def downgrade() -> None:
    """Remove the Project TaskRun keyset query index."""

    op.execute("drop index atlas.task_run_project_requested_idx")
