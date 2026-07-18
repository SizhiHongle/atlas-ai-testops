"""Contract tests for the Project TaskRun keyset query index."""

from pathlib import Path
from runpy import run_path

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260716_0026_task_run_query_index.py"
)


def test_query_index_revision_is_linear_and_matches_repository_sort() -> None:
    namespace = run_path(str(MIGRATION_PATH))
    source = " ".join(MIGRATION_PATH.read_text(encoding="utf-8").casefold().split())

    assert namespace["revision"] == "20260716_0026"
    assert namespace["down_revision"] == "20260716_0025"
    assert "create index task_run_project_requested_idx" in source
    assert (
        "tenant_id, project_id, requested_at desc, id desc" in source
    )
    assert "drop index atlas.task_run_project_requested_idx" in source
