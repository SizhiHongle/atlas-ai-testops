"""Unit coverage for the P9 machine-report helpers."""

from pathlib import Path

from scripts.run_p9_acceptance import (
    CommandResult,
    GateResult,
    _gate_document,
    _junit_properties,
    _nearest_rank_p95_milliseconds,
    _passed_test_count,
    _pytest_summary,
)


def test_p9_report_uses_camel_case_and_exact_pass_counts() -> None:
    gate = GateResult(
        key="goldenChainStability",
        status="PASSED",
        target="30 runs",
        observed="30/30",
        sample_count=30,
        evidence=("30 passed",),
    )

    assert _gate_document(gate) == {
        "key": "goldenChainStability",
        "status": "PASSED",
        "target": "30 runs",
        "observed": "30/30",
        "sampleCount": 30,
        "evidence": ["30 passed"],
        "note": None,
    }
    assert _passed_test_count("12 passed in 7.61s") == 12
    assert _passed_test_count("1 failed, 11 passed") == 11
    assert _passed_test_count("no pytest summary") == 0
    assert (
        _pytest_summary("", "postgresql://user:secret@database/atlas")
        == "pytest completed without a canonical count summary"
    )


def test_p9_p95_and_junit_properties_are_integer_exact(tmp_path: Path) -> None:
    results = tuple(
        CommandResult(
            label=f"sample-{index}",
            passed=True,
            passed_tests=1,
            duration_milliseconds=index,
            summary="1 passed",
        )
        for index in range(1, 101)
    )
    assert _nearest_rank_p95_milliseconds(results) == 95

    junit = tmp_path / "p9.xml"
    junit.write_text(
        """
        <testsuites>
          <testsuite>
            <testcase>
              <properties>
                <property name="leaseOperations" value="10000"/>
                <property name="leaseConflicts" value="0"/>
              </properties>
            </testcase>
          </testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )
    assert _junit_properties(junit) == {
        "leaseOperations": 10_000,
        "leaseConflicts": 0,
    }
