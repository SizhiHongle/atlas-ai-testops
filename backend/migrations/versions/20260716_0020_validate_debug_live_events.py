"""Validate and make bounded DebugRun live events immutable.

Revision ID: 20260716_0020
Revises: 20260716_0019
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0020"
down_revision: str | None = "20260716_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.debug_run_event
      validate constraint debug_run_event_payload_size_valid
    """,
    """
    create trigger debug_run_event_prevent_mutation
      before update or delete on atlas.debug_run_event
      for each row execute function atlas.prevent_fact_mutation()
    """,
)


DOWNGRADE_STATEMENTS = (
    "drop trigger if exists debug_run_event_prevent_mutation on atlas.debug_run_event",
    """
    alter table atlas.debug_run_event
      drop constraint if exists debug_run_event_payload_size_valid
    """,
    """
    alter table atlas.debug_run_event
      add constraint debug_run_event_payload_size_valid check (
        octet_length(payload::text) <= 32768
      ) not valid
    """,
)


def upgrade() -> None:
    """Validate historical payloads before enabling append-only enforcement."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Restore the unvalidated, repairable 0019 state."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
