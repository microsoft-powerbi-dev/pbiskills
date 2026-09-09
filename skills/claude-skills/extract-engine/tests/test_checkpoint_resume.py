"""Tests for run bookkeeping and --resume, against a FakeConnection.

Acceptance check #10: kill the process mid-run, restart with --resume, and
completed datasets are skipped while output remains correct. The subprocess-
kill variant needs a live database and is LocalDB-gated separately; this file
covers the unit-level contract with dependency injection, per the plan.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_engine import checkpoint  # noqa: E402
from extract_engine.metadata import Dataset  # noqa: E402
from fakes import FakeConnection  # noqa: E402


def _dataset(dataset_id, name):
    return Dataset(
        dataset_id=dataset_id, feed_id=1, dataset_name=name, source_schema="src", source_table=name.lower(),
        dataset_mode="primary", primary_key_columns=["Id"], window_date_column="Date",
        depends_on_dataset_id=None, dependency_source_column=None, dependency_target_column=None,
        include_own_window=False, output_file_stem=name, run_ordinal=1, is_active=True,
    )


def test_start_run_inserts_and_returns_the_new_run_id():
    conn = FakeConnection({r"SCOPE_IDENTITY": (["run_id"], [(42,)])})
    run_id = checkpoint.start_run(conn, 1, 1, "2026-09-01", 2, "polars", "DOMAIN\\svc")
    assert run_id == 42
    insert_sql, params = conn.writes[0]
    assert insert_sql.strip().upper().startswith("INSERT INTO META.RUN_LOG")
    assert params == (1, 1, "2026-09-01", 2, "polars", "DOMAIN\\svc")


def test_is_concurrent_run_active():
    conn_busy = FakeConnection({r"COUNT\(\*\)": (["n"], [(1,)])})
    assert checkpoint.is_concurrent_run_active(conn_busy, 1) is True

    conn_idle = FakeConnection({r"COUNT\(\*\)": (["n"], [(0,)])})
    assert checkpoint.is_concurrent_run_active(conn_idle, 1) is False


def test_complete_run_writes_status_and_metrics():
    conn = FakeConnection()
    checkpoint.complete_run(conn, run_id=42, duration_ms=1500, peak_rss_bytes=1024)
    sql, params = conn.writes[0]
    assert "UPDATE META.RUN_LOG" in sql.upper()
    assert params == ("completed", 1500, 1024, None, 42)


def test_mark_dataset_running_and_complete():
    conn = FakeConnection()
    checkpoint.mark_dataset_running(conn, run_id=1, dataset_id=5)
    checkpoint.mark_dataset_complete(conn, run_id=1, dataset_id=5, row_count=100, part_count=1, checksum="abc")
    assert "INSERT INTO META.RUN_DETAIL" in conn.writes[0][0].upper()
    assert "UPDATE META.RUN_DETAIL" in conn.writes[1][0].upper()
    assert conn.writes[1][1] == (100, 1, "abc", 1, 5)


def test_mark_dataset_failed():
    conn = FakeConnection()
    checkpoint.mark_dataset_failed(conn, run_id=1, dataset_id=5)
    assert "'failed'" in conn.writes[0][0]


def test_get_run_detail_statuses():
    fields = ["dataset_id", "status", "row_count", "part_count", "checksum"]
    rows = [(1, "completed", 100, 1, "abc"), (2, "failed", None, None, None)]
    conn = FakeConnection({r"FROM meta\.run_detail": (fields, rows)})
    statuses = checkpoint.get_run_detail_statuses(conn, run_id=99)
    assert statuses[1].status == "completed"
    assert statuses[1].row_count == 100
    assert statuses[2].status == "failed"


# ---------------------------------------------------------------------------
# The resume contract: a completed dataset is never re-processed.
# ---------------------------------------------------------------------------


def test_resume_skips_only_completed_datasets():
    datasets = [_dataset(1, "Claim"), _dataset(2, "Member"), _dataset(3, "Provider")]
    statuses = {
        1: checkpoint.RunDetailStatus(dataset_id=1, status="completed"),
        2: checkpoint.RunDetailStatus(dataset_id=2, status="failed"),
        # dataset 3 has no run_detail row at all yet (never started)
    }
    remaining = checkpoint.datasets_to_process(datasets, statuses, resume=True)
    assert [d.dataset_name for d in remaining] == ["Member", "Provider"]


def test_without_resume_every_dataset_runs_even_if_previously_completed():
    datasets = [_dataset(1, "Claim")]
    statuses = {1: checkpoint.RunDetailStatus(dataset_id=1, status="completed")}
    remaining = checkpoint.datasets_to_process(datasets, statuses, resume=False)
    assert [d.dataset_name for d in remaining] == ["Claim"]


def test_resume_with_no_prior_statuses_processes_everything():
    datasets = [_dataset(1, "Claim"), _dataset(2, "Member")]
    remaining = checkpoint.datasets_to_process(datasets, {}, resume=True)
    assert len(remaining) == 2


def test_a_completed_dataset_is_never_re_queried_spy_style():
    """The property acceptance check #10 actually cares about: given a
    'resume' decision, nothing downstream should ever be asked to process a
    completed dataset again - modeled here as a call-count spy rather than a
    real process kill."""
    datasets = [_dataset(1, "Claim"), _dataset(2, "Member")]
    statuses = {1: checkpoint.RunDetailStatus(dataset_id=1, status="completed")}

    processed = []

    def process(dataset):
        processed.append(dataset.dataset_name)

    for dataset in checkpoint.datasets_to_process(datasets, statuses, resume=True):
        process(dataset)

    assert processed == ["Member"]
    assert "Claim" not in processed


# ---------------------------------------------------------------------------
# Key staging
# ---------------------------------------------------------------------------


def test_stage_dataset_keys_inserts_every_value():
    conn = FakeConnection()
    checkpoint.stage_dataset_keys(conn, run_id=1, dataset_id=2, key_values=[10, 20, 30])
    assert len(conn.writes) == 3
    assert conn.writes[0][1] == (1, 2, "10")


def test_stage_dataset_keys_with_no_values_writes_nothing():
    conn = FakeConnection()
    checkpoint.stage_dataset_keys(conn, run_id=1, dataset_id=2, key_values=[])
    assert conn.writes == []
