"""Run the reproducible local P9 hardening suite and emit a safe JSON report."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Literal
from xml.etree import ElementTree

REPORT_SCHEMA_VERSION = "atlas.p9-acceptance-report/0.1"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_REPORT_PATH = REPOSITORY_ROOT / "tmp" / "p9" / "acceptance-report.json"
COMMAND_TIMEOUT_SECONDS = 900
PYTEST_COUNT_PATTERN = re.compile(
    r"(?<![0-9])([0-9]+) "
    r"(failed|passed|skipped|xfailed|xpassed|deselected|warnings?|errors?)\b"
)
PYTEST_DURATION_PATTERN = re.compile(r"\bin ([0-9]+(?:\.[0-9]+)?)s\b")

FAULT_INJECTION_SELECTORS = (
    "tests/infrastructure/test_http_task_execution_port.py::"
    "test_http_port_fails_closed_without_replaying_unknown_side_effect",
    "tests/integration/test_account_lease_concurrency.py::"
    "test_heartbeat_expiry_commit_and_reaper_use_server_clock",
    "tests/integration/test_task_workflow_runtime_temporal.py::"
    "test_real_attempt_deadline_expires_while_execution_activity_is_queued",
    "tests/infrastructure/test_evidence_store.py::"
    "test_minio_transport_errors_are_mapped_to_controlled_unavailability",
    "tests/api/test_live_sse.py::test_stream_send_failure_always_releases_capacity",
    "tests/api/test_live_sse.py::test_stream_hard_deadline_releases_capacity_when_send_stalls",
    "tests/integration/test_fixture_runs_api.py::"
    "test_cleanup_retry_and_sweeper_complete_transient_failure",
)
CAPACITY_ISOLATION_SELECTORS = (
    "tests/acceptance/test_p9_capacity.py",
    "tests/integration/test_account_lease_concurrency.py::"
    "test_one_hundred_concurrent_acquires_never_duplicate_a_slot",
    "tests/integration/test_task_run_queries_pg.py::"
    "test_task_run_queries_respect_parent_scope_and_tenant_rls",
    "tests/integration/test_evidence_read_grants_pg.py::"
    "test_finalized_artifact_scope_and_bounded_grant_lifecycle",
    "tests/integration/test_debug_live_pg.py::"
    "test_debug_live_snapshot_replay_isolation_and_event_hardening",
)
GOLDEN_SELECTOR = (
    "tests/integration/test_task_fixture_hygiene_pg.py::"
    "test_task_fixture_cleanup_projects_exact_hygiene_revision"
)
SCHEDULE_SELECTOR = (
    "tests/integration/test_task_schedule_runtime_pg_temporal.py::"
    "test_real_schedule_dispatch_creates_exact_unified_task_run"
)

GateStatus = Literal["PASSED", "FAILED", "NOT_EVALUATED"]


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One subprocess result with only bounded, non-sensitive evidence."""

    label: str
    passed: bool
    passed_tests: int
    duration_milliseconds: int
    summary: str


@dataclass(frozen=True, slots=True)
class GateResult:
    """One exact acceptance result."""

    key: str
    status: GateStatus
    target: str
    observed: str | None
    sample_count: int
    evidence: tuple[str, ...]
    note: str | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--golden-runs",
        type=int,
        default=30,
        help="complete golden-chain repetitions; production gate requires at least 30",
    )
    parser.add_argument(
        "--schedule-samples",
        type=int,
        default=30,
        help="real Temporal schedule samples; production gate requires at least 30",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="safe JSON report output path",
    )
    arguments = parser.parse_args()
    if arguments.golden_runs < 30:
        parser.error("--golden-runs must be at least 30")
    if arguments.schedule_samples < 30:
        parser.error("--schedule-samples must be at least 30")
    return arguments


def _require_environment() -> dict[str, str]:
    required = (
        "ATLAS_TEST_DATABASE_URL",
        "ATLAS_TEST_OWNER_DATABASE_URL",
        "ATLAS_TEST_TEMPORAL_ADDRESS",
    )
    missing = tuple(name for name in required if not os.environ.get(name))
    if missing:
        raise SystemExit(
            "P9 acceptance requires configured local infrastructure: " + ", ".join(missing)
        )
    selected = os.environ.copy()
    selected["ATLAS_RUN_P9_ACCEPTANCE"] = "1"
    return selected


def _pytest(
    *,
    label: str,
    selectors: tuple[str, ...],
    environment: dict[str, str],
    junit_path: Path | None = None,
) -> CommandResult:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--no-cov",
        "-q",
        *selectors,
    ]
    if junit_path is not None:
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.unlink(missing_ok=True)
        command.extend(("-o", "junit_family=legacy"))
        command.append(f"--junitxml={junit_path}")
    started_ns = perf_counter_ns()
    try:
        completed = subprocess.run(
            command,
            cwd=BACKEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        duration_ms = (perf_counter_ns() - started_ns + 999_999) // 1_000_000
        result = CommandResult(
            label=label,
            passed=False,
            passed_tests=0,
            duration_milliseconds=duration_ms,
            summary=f"timed out after {COMMAND_TIMEOUT_SECONDS} seconds",
        )
        print(
            f"[{label}] FAIL {duration_ms / 1_000:.3f}s {result.summary}",
            flush=True,
        )
        if error.stdout:
            print(str(error.stdout)[-8_000:], file=sys.stderr)
        if error.stderr:
            print(str(error.stderr)[-8_000:], file=sys.stderr)
        return result
    duration_ms = (perf_counter_ns() - started_ns + 999_999) // 1_000_000
    summary = _pytest_summary(completed.stdout, completed.stderr)
    result = CommandResult(
        label=label,
        passed=completed.returncode == 0,
        passed_tests=_passed_test_count(summary),
        duration_milliseconds=duration_ms,
        summary=summary,
    )
    print(
        f"[{label}] {'PASS' if result.passed else 'FAIL'} {duration_ms / 1_000:.3f}s {summary}",
        flush=True,
    )
    if not result.passed:
        print(completed.stdout[-8_000:], file=sys.stderr)
        print(completed.stderr[-8_000:], file=sys.stderr)
    return result


def _pytest_summary(stdout: str, stderr: str) -> str:
    lines = [line.strip() for line in (stdout + "\n" + stderr).splitlines() if line.strip()]
    for line in reversed(lines):
        counts = PYTEST_COUNT_PATTERN.findall(line)
        if not counts:
            continue
        summary = ", ".join(f"{count} {outcome}" for count, outcome in counts)
        duration = PYTEST_DURATION_PATTERN.search(line)
        if duration is not None:
            summary += f" in {duration.group(1)}s"
        return summary
    return "pytest completed without a canonical count summary"


def _passed_test_count(summary: str) -> int:
    matched = re.search(r"(?<![0-9])([0-9]+) passed\b", summary)
    return int(matched.group(1)) if matched is not None else 0


def _repeat(
    *,
    label: str,
    selector: str,
    count: int,
    environment: dict[str, str],
) -> tuple[CommandResult, ...]:
    results: list[CommandResult] = []
    for index in range(count):
        results.append(
            _pytest(
                label=f"{label}-{index + 1:02d}-of-{count:02d}",
                selectors=(selector,),
                environment=environment,
            )
        )
    return tuple(results)


def _junit_properties(path: Path) -> dict[str, int]:
    root = ElementTree.parse(path).getroot()
    properties: dict[str, int] = {}
    for value in root.findall(".//property"):
        name = value.get("name")
        raw_value = value.get("value")
        if name is None or raw_value is None:
            continue
        try:
            properties[name] = int(raw_value)
        except ValueError as error:
            raise RuntimeError(f"P9 JUnit property {name!r} must be an integer") from error
    return properties


def _nearest_rank_p95_milliseconds(results: tuple[CommandResult, ...]) -> int:
    if not results:
        raise ValueError("P95 requires at least one sample")
    ordered = sorted(item.duration_milliseconds for item in results)
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return ordered[rank - 1]


def _git_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "unknown"


def _git_worktree_dirty() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode != 0 or bool(completed.stdout.strip())


def _safe_result_evidence(results: tuple[CommandResult, ...]) -> tuple[str, ...]:
    return tuple(
        f"{item.label}: {item.summary} ({item.duration_milliseconds} ms)" for item in results
    )


def _gate_document(gate: GateResult) -> dict[str, object]:
    return {
        "key": gate.key,
        "status": gate.status,
        "target": gate.target,
        "observed": gate.observed,
        "sampleCount": gate.sample_count,
        "evidence": list(gate.evidence),
        "note": gate.note,
    }


def main() -> int:
    """Run all local P9 gates and return non-zero on any evaluated failure."""

    arguments = _parse_args()
    environment = _require_environment()
    started_at = datetime.now(UTC)
    report_path = arguments.report.resolve()
    junit_path = report_path.parent / "capacity-isolation.xml"

    fault = _pytest(
        label="fault-injection-matrix",
        selectors=FAULT_INJECTION_SELECTORS,
        environment=environment,
    )
    capacity = _pytest(
        label="capacity-isolation",
        selectors=CAPACITY_ISOLATION_SELECTORS,
        environment=environment,
        junit_path=junit_path,
    )
    capacity_metric_error: str | None = None
    if capacity.passed:
        try:
            capacity_properties = _junit_properties(junit_path)
        except (OSError, ElementTree.ParseError, RuntimeError) as error:
            capacity_properties = {}
            capacity_metric_error = type(error).__name__
    else:
        capacity_properties = {}
    golden = _repeat(
        label="golden-chain",
        selector=GOLDEN_SELECTOR,
        count=arguments.golden_runs,
        environment=environment,
    )
    schedule = _repeat(
        label="schedule-vertical",
        selector=SCHEDULE_SELECTOR,
        count=arguments.schedule_samples,
        environment=environment,
    )

    golden_passes = sum(item.passed for item in golden)
    golden_basis_points = golden_passes * 10_000 // len(golden)
    platform_failure_basis_points = 10_000 - golden_basis_points
    schedule_p95_ms = _nearest_rank_p95_milliseconds(schedule)
    expected_properties = {
        "leaseOperations",
        "leaseConflicts",
        "leaseTransientShortages",
        "leaseStressDurationMilliseconds",
        "evidenceObjects",
        "evidenceVerifiedObjects",
        "evidenceBytes",
        "evidenceLoadDurationMilliseconds",
        "liveEventSamples",
        "liveEventP95Milliseconds",
    }
    missing_properties = expected_properties - capacity_properties.keys()
    capacity_passed = capacity.passed and not missing_properties
    live_p95_ms = capacity_properties.get("liveEventP95Milliseconds")
    lease_operations = capacity_properties.get("leaseOperations", 0)
    lease_conflicts = capacity_properties.get("leaseConflicts")
    lease_transient_shortages = capacity_properties.get(
        "leaseTransientShortages",
        0,
    )
    evidence_objects = capacity_properties.get("evidenceObjects", 0)
    verified_objects = capacity_properties.get("evidenceVerifiedObjects", 0)

    gates = (
        GateResult(
            key="faultInjectionMatrix",
            status="PASSED" if fault.passed else "FAILED",
            target="API timeout, account expiry, Worker interruption, storage failure, "
            "disconnect, and cleanup failure converge safely",
            observed="all fixed selectors passed" if fault.passed else "selector failure",
            sample_count=fault.passed_tests,
            evidence=(f"{fault.label}: {fault.summary}",),
        ),
        GateResult(
            key="capacityAndIsolation",
            status="PASSED" if capacity_passed else "FAILED",
            target="2x local reference peak, multi-project/account shortage/large evidence, "
            "cross-project invisibility",
            observed=(
                f"{evidence_objects} evidence objects; all isolation selectors passed"
                if capacity_passed
                else "capacity command failed or metrics were incomplete"
            ),
            sample_count=evidence_objects,
            evidence=(f"{capacity.label}: {capacity.summary}",),
            note=(
                None
                if not missing_properties and capacity_metric_error is None
                else (
                    "capacity metric read failed: " + capacity_metric_error
                    if capacity_metric_error is not None
                    else "missing JUnit properties: " + ", ".join(sorted(missing_properties))
                )
            ),
        ),
        GateResult(
            key="accountLeaseConflicts",
            status=(
                "PASSED"
                if capacity_passed and lease_operations == 10_000 and lease_conflicts == 0
                else "FAILED"
            ),
            target="100 concurrent x 100 rounds; duplicate active lease conflicts = 0",
            observed=(
                f"{lease_operations} operations; {lease_conflicts} conflicts; "
                f"{lease_transient_shortages} bounded transient shortages"
                if lease_conflicts is not None
                else None
            ),
            sample_count=lease_operations,
            evidence=(f"{capacity.label}: {capacity.summary}",),
        ),
        GateResult(
            key="goldenChainStability",
            status="PASSED" if golden_basis_points >= 9_500 else "FAILED",
            target="at least 30 consecutive runs; platform success >= 95%",
            observed=(
                f"{golden_passes}/{len(golden)} passed; "
                f"platform failure {platform_failure_basis_points / 100:.2f}%"
            ),
            sample_count=len(golden),
            evidence=_safe_result_evidence(golden),
            note="Local reference chain uses deterministic adapters, real PostgreSQL, "
            "and the complete Task/Result/Gate/Callback fact path.",
        ),
        GateResult(
            key="cleanupFinalization",
            status="PASSED" if golden_passes == len(golden) else "FAILED",
            target="reference golden-chain cleanup assertions = 100%",
            observed=f"{golden_passes}/{len(golden)} cleanup-verified chains passed",
            sample_count=len(golden),
            evidence=(GOLDEN_SELECTOR,),
        ),
        GateResult(
            key="localScheduleVerticalP95",
            status=(
                "PASSED"
                if all(item.passed for item in schedule) and schedule_p95_ms < 60_000
                else "FAILED"
            ),
            target="conservative full local vertical command P95 < 60000 ms",
            observed=f"{schedule_p95_ms} ms",
            sample_count=len(schedule),
            evidence=_safe_result_evidence(schedule),
            note="This includes test setup and is an upper bound for the local reference path; "
            "it is not a production deployment SLO measurement.",
        ),
        GateResult(
            key="localLiveEventP95",
            status=(
                "PASSED"
                if capacity_passed and live_p95_ms is not None and live_p95_ms < 2_000
                else "FAILED"
            ),
            target="in-process event-to-client completion P95 < 2000 ms",
            observed=f"{live_p95_ms} ms" if live_p95_ms is not None else None,
            sample_count=capacity_properties.get("liveEventSamples", 0),
            evidence=(f"{capacity.label}: {capacity.summary}",),
            note="Network, proxy, and browser rendering latency require staging telemetry.",
        ),
        GateResult(
            key="evidenceCompleteness",
            status=(
                "PASSED"
                if capacity_passed and evidence_objects > 0 and evidence_objects == verified_objects
                else "FAILED"
            ),
            target="local required evidence verified completeness >= 99%",
            observed=f"{verified_objects}/{evidence_objects} independently verified",
            sample_count=evidence_objects,
            evidence=(f"{capacity.label}: {capacity.summary}",),
        ),
        GateResult(
            key="productionControlPlaneAvailability",
            status="NOT_EVALUATED",
            target="99.9% per month",
            observed=None,
            sample_count=0,
            evidence=(),
            note="Requires a deployed month-long availability window and monitoring backend.",
        ),
        GateResult(
            key="productionClassificationAccuracy",
            status="NOT_EVALUATED",
            target="human-audited accuracy >= 90%",
            observed=None,
            sample_count=0,
            evidence=(),
            note="Requires pilot failures and an independent human-labelled evaluation set.",
        ),
        GateResult(
            key="productionShadowIteration",
            status="NOT_EVALUATED",
            target="at least one complete real-team shadow iteration",
            observed=None,
            sample_count=0,
            evidence=(),
            note="Requires the pilot project, real SaaS executor, accounts, and business API.",
        ),
        GateResult(
            key="productionDisasterRecovery",
            status="NOT_EVALUATED",
            target="operator-approved RTO/RPO and restore drill",
            observed=None,
            sample_count=0,
            evidence=(),
            note="Requires deployment topology, backup provider, and approved RTO/RPO.",
        ),
    )
    evaluated_failed = any(gate.status == "FAILED" for gate in gates)
    not_evaluated = any(gate.status == "NOT_EVALUATED" for gate in gates)
    overall_status = (
        "FAILED" if evaluated_failed else "CONDITIONAL_PASS" if not_evaluated else "PASSED"
    )
    completed_at = datetime.now(UTC)
    report = {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "profile": "LOCAL_REFERENCE",
        "overallStatus": overall_status,
        "revision": _git_revision(),
        "workingTreeDirty": _git_worktree_dirty(),
        "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        "completedAt": completed_at.isoformat().replace("+00:00", "Z"),
        "environment": {
            "python": sys.version.split()[0],
            "postgresql": "configured",
            "temporal": "configured",
            "realSaasExecutor": "not_configured",
        },
        "gates": [_gate_document(gate) for gate in gates],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"P9 report: {report_path}", flush=True)
    print(f"P9 overall status: {overall_status}", flush=True)
    return 1 if evaluated_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
