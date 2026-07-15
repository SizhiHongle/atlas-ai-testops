"""Create append-only Browser Worker runtime reports.

Revision ID: 20260715_0017
Revises: 20260715_0016
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0017"
down_revision: str | None = "20260715_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.evidence_manifest
      add column finalization_command_digest text
    """,
    """
    alter table atlas.evidence_manifest
      add constraint evidence_manifest_finalization_command_digest_valid check (
        finalization_command_digest is null
        or finalization_command_digest ~ '^sha256:[0-9a-f]{64}$'
      )
    """,
    """
    create table atlas.browser_runtime_report (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      execution_contract_id uuid not null,
      execution_contract_digest text not null,
      report_sequence bigint not null,
      report_kind text not null,
      actor_slot text,
      action_id uuid,
      payload jsonb not null,
      payload_digest text not null,
      previous_chain_digest text not null,
      chain_digest text not null,
      occurred_at timestamptz not null,
      recorded_at timestamptz not null,
      constraint browser_report_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint browser_report_contract_sequence_unique unique (
        execution_contract_id, report_sequence
      ),
      constraint browser_report_contract_id_unique unique (
        execution_contract_id, id
      ),
      constraint browser_report_sequence_valid check (
        report_sequence between 1 and 10000000
      ),
      constraint browser_report_kind_valid check (
        report_kind in (
          'execution.started', 'node.started', 'observation.captured',
          'action.proposed', 'policy.decided', 'action.executed',
          'artifact.captured', 'assertion.evaluated', 'node.completed',
          'execution.blocked', 'execution.completed'
        )
      ),
      constraint browser_report_actor_slot_valid check (
        actor_slot is null
        or actor_slot ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}$'
      ),
      constraint browser_report_digest_valid check (
        execution_contract_digest ~ '^sha256:[0-9a-f]{64}$'
        and payload_digest ~ '^sha256:[0-9a-f]{64}$'
        and previous_chain_digest ~ '^sha256:[0-9a-f]{64}$'
        and chain_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint browser_report_payload_valid check (
        jsonb_typeof(payload) = 'object'
      ),
      constraint browser_report_time_order check (
        occurred_at <= recorded_at
      )
    )
    """,
    """
    create index browser_runtime_report_replay_idx
      on atlas.browser_runtime_report (debug_run_id, report_sequence)
    """,
    """
    create index browser_runtime_report_contract_scope_fk_idx
      on atlas.browser_runtime_report (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      )
    """,
    """
    create unique index browser_runtime_report_action_kind_unique
      on atlas.browser_runtime_report (
        execution_contract_id, action_id, report_kind
      )
      where action_id is not null
    """,
    """
    create function atlas.guard_browser_runtime_report_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      contract_record record;
      previous_record record;
      proposal_record record;
    begin
      select contract.contract_digest, contract.created_at,
             contract.execution_deadline, contract.worker_identity,
             run.lifecycle, run.cancel_requested_at
      into contract_record
      from atlas.execution_contract contract
      join atlas.debug_run run
        on run.id = contract.debug_run_id
       and run.tenant_id = contract.tenant_id
       and run.project_id = contract.project_id
       and run.environment_id = contract.environment_id
      where contract.id = new.execution_contract_id
        and contract.debug_run_id = new.debug_run_id
        and contract.tenant_id = new.tenant_id
        and contract.project_id = new.project_id
        and contract.environment_id = new.environment_id
      for share of run;
      if not found
        or contract_record.lifecycle <> 'RUNNING'
        or contract_record.cancel_requested_at is not null
        or contract_record.contract_digest <> new.execution_contract_digest
        or new.occurred_at < contract_record.created_at
        or new.occurred_at > contract_record.execution_deadline
        or new.recorded_at < new.occurred_at
        or new.recorded_at > contract_record.execution_deadline
      then
        raise exception 'browser report does not match a running execution contract';
      end if;

      if new.report_kind in (
        'action.proposed', 'policy.decided', 'action.executed'
      ) then
        if new.action_id is null or new.actor_slot is null then
          raise exception 'browser action reports require an action and actor';
        end if;
      elsif new.action_id is not null then
        raise exception 'only browser action reports may carry an action id';
      end if;

      if new.report_sequence = 1 then
        if new.report_kind <> 'execution.started'
          or new.previous_chain_digest
             <> 'sha256:0000000000000000000000000000000000000000000000000000000000000000'
        then
          raise exception 'browser report chain must start at execution.started';
        end if;
      else
        if new.report_kind = 'execution.started' then
          raise exception 'execution.started may only be the first browser report';
        end if;
        select report_sequence, report_kind, actor_slot, action_id, payload,
               chain_digest, occurred_at
        into previous_record
        from atlas.browser_runtime_report
        where execution_contract_id = new.execution_contract_id
          and report_sequence = new.report_sequence - 1;
        if not found
          or previous_record.chain_digest <> new.previous_chain_digest
          or previous_record.report_kind = 'execution.completed'
          or new.occurred_at < previous_record.occurred_at
        then
          raise exception 'browser report sequence, state, time, or previous hash is invalid';
        end if;

        if new.report_kind = 'action.proposed' and exists (
          select 1
          from atlas.browser_runtime_report report
          where report.execution_contract_id = new.execution_contract_id
            and report.action_id = new.action_id
        ) then
          raise exception 'browser action id is already present in this report chain';
        end if;

        if previous_record.report_kind = 'action.proposed' and (
          new.report_kind <> 'policy.decided'
          or previous_record.action_id is distinct from new.action_id
          or previous_record.actor_slot is distinct from new.actor_slot
        ) then
          raise exception 'action proposal must be followed by its policy decision';
        end if;

        if new.report_kind = 'policy.decided' and (
          previous_record.report_kind <> 'action.proposed'
          or previous_record.action_id is distinct from new.action_id
          or previous_record.actor_slot is distinct from new.actor_slot
        ) then
          raise exception 'policy decision must follow its action proposal';
        end if;

        if previous_record.report_kind = 'policy.decided'
          and previous_record.payload ->> 'decision' = 'ALLOW'
          and new.report_kind <> 'execution.blocked'
          and (
            new.report_kind <> 'action.executed'
            or previous_record.action_id is distinct from new.action_id
            or previous_record.actor_slot is distinct from new.actor_slot
          )
        then
          raise exception 'allowed policy decision must be followed by its action receipt';
        end if;

        if previous_record.report_kind = 'policy.decided'
          and previous_record.payload ->> 'decision' is distinct from 'ALLOW'
          and new.report_kind <> 'execution.blocked'
        then
          raise exception 'non-allowed policy decision must block execution';
        end if;

        if new.report_kind = 'action.executed' and (
          previous_record.report_kind <> 'policy.decided'
          or previous_record.payload ->> 'decision' is distinct from 'ALLOW'
          or previous_record.action_id is distinct from new.action_id
          or previous_record.actor_slot is distinct from new.actor_slot
        ) then
          raise exception 'action receipt must follow its allowed policy decision';
        end if;

        if new.report_kind = 'action.executed' then
          select report_kind, actor_slot, action_id, payload
          into proposal_record
          from atlas.browser_runtime_report
          where execution_contract_id = new.execution_contract_id
            and report_sequence = new.report_sequence - 2;
          if not found
            or proposal_record.report_kind <> 'action.proposed'
            or proposal_record.action_id is distinct from new.action_id
            or proposal_record.actor_slot is distinct from new.actor_slot
            or proposal_record.payload ->> 'action'
               is distinct from new.payload ->> 'action'
          then
            raise exception 'action receipt does not match its unique proposal';
          end if;
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger browser_runtime_report_guard_insert
      before insert on atlas.browser_runtime_report
      for each row execute function atlas.guard_browser_runtime_report_insert()
    """,
    """
    create function atlas.reject_browser_runtime_report_mutation()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      raise exception 'browser runtime reports are immutable';
    end;
    $$
    """,
    """
    create trigger browser_runtime_report_reject_update
      before update or delete on atlas.browser_runtime_report
      for each row execute function atlas.reject_browser_runtime_report_mutation()
    """,
    """
    create function atlas.guard_evidence_manifest_browser_chain()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      report_count bigint;
      latest_sequence bigint;
      latest_chain_digest text;
      latest_kind text;
      unsafe_execution boolean;
    begin
      select count(*), max(report_sequence)
      into report_count, latest_sequence
      from atlas.browser_runtime_report
      where execution_contract_id = new.execution_contract_id;
      if report_count = 0 or new.finalization_command_digest is null then
        raise exception 'evidence manifest requires browser runtime reports';
      end if;
      select chain_digest, report_kind
      into latest_chain_digest, latest_kind
      from atlas.browser_runtime_report
      where execution_contract_id = new.execution_contract_id
        and report_sequence = latest_sequence;
      if report_count <> latest_sequence
        or latest_kind <> 'execution.completed'
        or new.event_count <> report_count
        or new.event_chain_head_digest <> latest_chain_digest
      then
        raise exception 'evidence manifest browser report chain is inconsistent';
      end if;

      select exists (
        select 1
        from atlas.browser_runtime_report report
        where report.execution_contract_id = new.execution_contract_id
          and (
            report.report_kind = 'execution.blocked'
            or (
              report.report_kind = 'action.executed'
              and report.payload ->> 'status' is distinct from 'SUCCEEDED'
            )
          )
      ) into unsafe_execution;
      if unsafe_execution and (
        new.outcome <> 'INCONCLUSIVE'
        or new.passed_assertions <> 0
        or new.failed_assertions <> 0
        or exists (
          select 1
          from atlas.assertion_result result
          where result.execution_contract_id = new.execution_contract_id
            and result.status <> 'INCONCLUSIVE'
        )
        or exists (
          select 1
          from jsonb_array_elements(new.manifest -> 'assertionResults') item
          where item ->> 'status' is distinct from 'INCONCLUSIVE'
        )
      ) then
        raise exception 'unsafe browser execution only permits inconclusive assertions';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger evidence_manifest_browser_chain_guard
      before insert on atlas.evidence_manifest
      for each row execute function atlas.guard_evidence_manifest_browser_chain()
    """,
    "alter table atlas.browser_runtime_report enable row level security",
    "alter table atlas.browser_runtime_report force row level security",
    """
    create policy browser_runtime_report_tenant_isolation
      on atlas.browser_runtime_report for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "revoke all on atlas.browser_runtime_report from atlas_app",
    "grant select, insert on atlas.browser_runtime_report to atlas_app",
)


DOWNGRADE_STATEMENTS = (
    "drop trigger if exists evidence_manifest_browser_chain_guard on atlas.evidence_manifest",
    "drop function if exists atlas.guard_evidence_manifest_browser_chain()",
    "drop policy if exists browser_runtime_report_tenant_isolation on atlas.browser_runtime_report",
    "drop trigger if exists browser_runtime_report_reject_update on atlas.browser_runtime_report",
    "drop function if exists atlas.reject_browser_runtime_report_mutation()",
    "drop trigger if exists browser_runtime_report_guard_insert on atlas.browser_runtime_report",
    "drop function if exists atlas.guard_browser_runtime_report_insert()",
    "drop table if exists atlas.browser_runtime_report",
    "alter table atlas.evidence_manifest drop column if exists finalization_command_digest",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
