"""Add frozen infrastructure retry policy to TaskRun manifests.

Revision ID: 20260717_0030
Revises: 20260717_0029
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0030"
down_revision: str | None = "20260717_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_ticket_guard(old: str, new: str) -> str:
    """Replace one reviewed guard fragment and fail if the predecessor drifted."""

    return f"""
    do $migration$
    declare
      function_sql text;
      old_fragment constant text := $old${old}$old$;
      new_fragment constant text := $new${new}$new$;
    begin
      select pg_get_functiondef(
        'atlas.guard_task_unit_execution_ticket_insert()'::regprocedure
      )
      into function_sql;
      if position(old_fragment in function_sql) = 0 then
        if position(new_fragment in function_sql) > 0 then
          return;
        end if;
        raise exception 'task execution ticket guard predecessor is unexpected';
      end if;
      execute replace(function_sql, old_fragment, new_fragment);
    end;
    $migration$
    """


_LEGACY_TICKET_DISPATCH = """        and unit.lifecycle = 'QUEUED'
        and attempt.lifecycle = 'QUEUED'"""
_RETRY_TICKET_DISPATCH = """        and (
          (
            attempt.attempt_number = 1
            and unit.lifecycle = 'QUEUED'
          )
          or (
            attempt.attempt_number > 1
            and unit.lifecycle = 'RUNNING'
            and exists (
              select 1
              from atlas.unit_attempt previous
              where previous.execution_unit_id = attempt.execution_unit_id
                and previous.attempt_number = attempt.attempt_number - 1
                and previous.lifecycle = 'CLOSED'
                and previous.quality = 'INFRA_ERROR'
            )
          )
        )
        and attempt.lifecycle = 'QUEUED'
        and transaction_timestamp() >= attempt.queued_at"""


UPGRADE_STATEMENTS = (
    "alter table atlas.task_run_manifest add column retry_policy jsonb",
    "alter table atlas.task_run_manifest "
    "alter column schema_version set default 'atlas.task-run-manifest/0.2'",
    "alter table atlas.task_run_manifest "
    "drop constraint task_run_manifest_schema_valid",
    """
    alter table atlas.task_run_manifest
    add constraint task_run_manifest_schema_valid check (
      (
        schema_version = 'atlas.task-run-manifest/0.1'
        and retry_policy is null
      )
      or (
        schema_version = 'atlas.task-run-manifest/0.2'
        and jsonb_typeof(retry_policy) = 'object'
        and retry_policy ?& array[
          'schemaVersion',
          'infraRetryAttempts',
          'maxTotalInfraRetries',
          'initialBackoffSeconds',
          'maximumBackoffSeconds',
          'jitterPercent',
          'contentDigest'
        ]
        and retry_policy - array[
          'schemaVersion',
          'infraRetryAttempts',
          'maxTotalInfraRetries',
          'initialBackoffSeconds',
          'maximumBackoffSeconds',
          'jitterPercent',
          'contentDigest'
        ] = '{}'::jsonb
        and retry_policy ->> 'schemaVersion' = 'atlas.task-retry-policy/0.1'
        and jsonb_typeof(retry_policy -> 'infraRetryAttempts') = 'number'
        and jsonb_typeof(retry_policy -> 'maxTotalInfraRetries') = 'number'
        and jsonb_typeof(retry_policy -> 'initialBackoffSeconds') = 'number'
        and jsonb_typeof(retry_policy -> 'maximumBackoffSeconds') = 'number'
        and jsonb_typeof(retry_policy -> 'jitterPercent') = 'number'
        and (retry_policy ->> 'infraRetryAttempts')::numeric between 0 and 4
        and (retry_policy ->> 'maxTotalInfraRetries')::numeric between 0 and 256
        and (retry_policy ->> 'initialBackoffSeconds')::numeric between 1 and 300
        and (retry_policy ->> 'maximumBackoffSeconds')::numeric between 1 and 3600
        and (retry_policy ->> 'jitterPercent')::numeric between 0 and 50
        and trunc((retry_policy ->> 'infraRetryAttempts')::numeric)
          = (retry_policy ->> 'infraRetryAttempts')::numeric
        and trunc((retry_policy ->> 'maxTotalInfraRetries')::numeric)
          = (retry_policy ->> 'maxTotalInfraRetries')::numeric
        and trunc((retry_policy ->> 'initialBackoffSeconds')::numeric)
          = (retry_policy ->> 'initialBackoffSeconds')::numeric
        and trunc((retry_policy ->> 'maximumBackoffSeconds')::numeric)
          = (retry_policy ->> 'maximumBackoffSeconds')::numeric
        and trunc((retry_policy ->> 'jitterPercent')::numeric)
          = (retry_policy ->> 'jitterPercent')::numeric
        and (retry_policy ->> 'maximumBackoffSeconds')::numeric
          >= (retry_policy ->> 'initialBackoffSeconds')::numeric
        and retry_policy ->> 'contentDigest' ~ '^sha256:[0-9a-f]{64}$'
        and policy_digests ->> 'infra-retry'
          = retry_policy ->> 'contentDigest'
        and retry_policy ->> 'contentDigest'
          = atlas.task_sha256_json(retry_policy - 'contentDigest')
      )
    )
    """,
    _replace_ticket_guard(_LEGACY_TICKET_DISPATCH, _RETRY_TICKET_DISPATCH),
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_run_manifest
        where schema_version <> 'atlas.task-run-manifest/0.1'
          or retry_policy is not null
      ) then
        raise exception
          'cannot downgrade while task-run-manifest/0.2 retry policy facts exist'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    _replace_ticket_guard(_RETRY_TICKET_DISPATCH, _LEGACY_TICKET_DISPATCH),
    "alter table atlas.task_run_manifest "
    "alter column schema_version set default 'atlas.task-run-manifest/0.1'",
    "alter table atlas.task_run_manifest "
    "drop constraint task_run_manifest_schema_valid",
    """
    alter table atlas.task_run_manifest
    add constraint task_run_manifest_schema_valid check (
      schema_version = 'atlas.task-run-manifest/0.1'
    )
    """,
    "alter table atlas.task_run_manifest drop column retry_policy",
)


def upgrade() -> None:
    """Allow immutable v0.2 manifests with exact bounded retry policy facts."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove retry policy support only when no v0.2 fact would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
