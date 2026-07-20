"""Allow truthful browser planner runtime reports.

Revision ID: 20260720_0046
Revises: 20260720_0045
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0046"
down_revision: str | None = "20260720_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_REPORT_KINDS_WITH_PLANNER = """
  'execution.started', 'node.started', 'observation.captured',
  'planner.completed',
  'action.proposed', 'policy.decided', 'action.executed',
  'artifact.captured', 'assertion.evaluated', 'node.completed',
  'execution.blocked', 'execution.completed'
"""

_LEGACY_REPORT_KINDS = """
  'execution.started', 'node.started', 'observation.captured',
  'action.proposed', 'policy.decided', 'action.executed',
  'artifact.captured', 'assertion.evaluated', 'node.completed',
  'execution.blocked', 'execution.completed'
"""


def upgrade() -> None:
    """Permit the bounded planner receipt in the append-only report chain."""

    op.execute(
        "alter table atlas.browser_runtime_report "
        "drop constraint browser_report_kind_valid"
    )
    op.execute(
        "alter table atlas.browser_runtime_report "
        "add constraint browser_report_kind_valid "
        f"check (report_kind in ({_REPORT_KINDS_WITH_PLANNER}))"
    )


def downgrade() -> None:
    """Stop accepting new planner receipts while retaining historical rows."""

    op.execute(
        "alter table atlas.browser_runtime_report "
        "drop constraint browser_report_kind_valid"
    )
    op.execute(
        "alter table atlas.browser_runtime_report "
        "add constraint browser_report_kind_valid "
        f"check (report_kind in ({_LEGACY_REPORT_KINDS})) not valid"
    )
