"""Install the unvalidated DebugRun live event payload bound.

Revision ID: 20260716_0019
Revises: 20260715_0018
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0019"
down_revision: str | None = "20260715_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.debug_run_event
      add constraint debug_run_event_payload_size_valid check (
        octet_length(payload::text) <= 32768
      ) not valid
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    alter table atlas.debug_run_event
      drop constraint if exists debug_run_event_payload_size_valid
    """,
)


def upgrade() -> None:
    """Install the bound without scanning historical events."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove the unvalidated DebugRun event payload bound."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
