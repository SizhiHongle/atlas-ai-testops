"""Remove the redundant DebugRun event replay index concurrently.

Revision ID: 20260716_0021
Revises: 20260716_0020
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0021"
down_revision: str | None = "20260716_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENT = (
    "drop index concurrently if exists atlas.debug_run_event_replay_idx"
)
DOWNGRADE_STATEMENTS = (
    "drop index concurrently if exists atlas.debug_run_event_replay_idx",
    """
    create index concurrently if not exists debug_run_event_replay_idx
      on atlas.debug_run_event (debug_run_id, seq)
    """,
)


def upgrade() -> None:
    """Drop the redundant replay index without blocking event writes."""

    with op.get_context().autocommit_block():
        op.execute(UPGRADE_STATEMENT)


def downgrade() -> None:
    """Restore the ordinary replay index without blocking event writes."""

    with op.get_context().autocommit_block():
        for statement in DOWNGRADE_STATEMENTS:
            op.execute(statement)
