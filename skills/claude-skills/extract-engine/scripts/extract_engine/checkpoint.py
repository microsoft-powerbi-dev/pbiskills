"""
checkpoint.py - run_log/run_detail read and write, and --resume logic.

Granularity is per-dataset for the MVP, matching the doc's acceptance check
#10 exactly ("Kill the process mid-run, restart with --resume, and the
completed datasets are skipped"). meta.run_detail deliberately has no
last-completed-part column yet - that is the documented extension point for
part-level resume later, not an oversight.

"Sleep and disconnect" (a laptop) and a Windows Server patch/reboot window
are the same failure shape from this module's point of view: a process died
mid-run, and the resume path only needs to know which datasets already
reached ``completed``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import connection as conn_mod
from .metadata import Dataset


@dataclass(frozen=True)
class RunDetailStatus:
    dataset_id: int
    status: str  # 'pending' | 'running' | 'completed' | 'failed'
    row_count: Optional[int] = None
    part_count: Optional[int] = None
    checksum: Optional[str] = None


def start_run(
    conn,
    feed_id: int,
    feed_config_version_id: int,
    anchor_date: datetime.date,
    window_years: int,
    execution_mode: str,
    requested_by: str,
) -> int:
    """Insert a new meta.run_log row, return its run_id."""
    cursor = conn.cursor()
    try:
        return conn_mod.execute_insert_return_identity(
            cursor,
            "INSERT INTO meta.run_log "
            "(feed_id, feed_config_version_id, anchor_date, window_years, "
            "execution_mode, status, requested_by) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?)",
            feed_id, feed_config_version_id, anchor_date, window_years, execution_mode, requested_by,
        )
    finally:
        cursor.close()


def is_concurrent_run_active(conn, feed_id: int) -> bool:
    """Advisory check: refuse a second concurrent run against the same feed.

    A risk that doesn't show up on a single laptop run but matters on a
    server where a scheduled task could overlap a still-running previous one.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM meta.run_log WHERE feed_id = ? AND status = 'running'", feed_id
        )
        return cursor.fetchone()[0] > 0
    finally:
        cursor.close()


def complete_run(conn, run_id: int, *, duration_ms: int, peak_rss_bytes: int, status: str = "completed",
                  error_message: Optional[str] = None) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE meta.run_log SET status = ?, completed_at = SYSUTCDATETIME(), "
            "duration_ms = ?, peak_rss_bytes = ?, error_message = ? WHERE run_id = ?",
            status, duration_ms, peak_rss_bytes, error_message, run_id,
        )
    finally:
        cursor.close()


def mark_dataset_running(conn, run_id: int, dataset_id: int) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO meta.run_detail (run_id, dataset_id, status, started_at) "
            "VALUES (?, ?, 'running', SYSUTCDATETIME())",
            run_id, dataset_id,
        )
    finally:
        cursor.close()


def mark_dataset_complete(
    conn, run_id: int, dataset_id: int, *, row_count: int, part_count: int, checksum: str
) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE meta.run_detail SET status = 'completed', row_count = ?, "
            "part_count = ?, checksum = ?, completed_at = SYSUTCDATETIME() "
            "WHERE run_id = ? AND dataset_id = ?",
            row_count, part_count, checksum, run_id, dataset_id,
        )
    finally:
        cursor.close()


def mark_dataset_failed(conn, run_id: int, dataset_id: int) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE meta.run_detail SET status = 'failed', completed_at = SYSUTCDATETIME() "
            "WHERE run_id = ? AND dataset_id = ?",
            run_id, dataset_id,
        )
    finally:
        cursor.close()


def get_run_detail_statuses(conn, run_id: int) -> Dict[int, RunDetailStatus]:
    """dataset_id -> its current run_detail row, for the resumed run's --run-id."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT dataset_id, status, row_count, part_count, checksum "
            "FROM meta.run_detail WHERE run_id = ?",
            run_id,
        )
        columns = [c[0] for c in cursor.description] if cursor.description else []
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
    return {row["dataset_id"]: RunDetailStatus(**row) for row in rows}


def datasets_to_process(
    datasets: List[Dataset], statuses: Dict[int, RunDetailStatus], *, resume: bool
) -> List[Dataset]:
    """The datasets a run should actually process.

    Without --resume, every dataset runs (a fresh run). With --resume, any
    dataset already 'completed' in the prior attempt is skipped entirely - no
    source-DB query, no file overwrite - matching acceptance check #10.
    """
    if not resume:
        return list(datasets)
    return [
        dataset
        for dataset in datasets
        if statuses.get(dataset.dataset_id) is None or statuses[dataset.dataset_id].status != "completed"
    ]


def stage_dataset_keys(conn, run_id: int, dataset_id: int, key_values: List) -> None:
    """Push a primary dataset's distinct key values for dependent-mode pull-in.

    Uses fast_executemany when the driver supports it (pyodbc), since the
    accumulated key set - while bounded by distinct values, not row volume -
    can still be large enough that row-by-row inserts would be slow.
    """
    if not key_values:
        return
    cursor = conn.cursor()
    try:
        try:
            cursor.fast_executemany = True
        except AttributeError:
            pass  # a fake/test cursor won't have this; real pyodbc does
        params = [(run_id, dataset_id, str(value)) for value in key_values]
        cursor.executemany(
            "INSERT INTO meta.run_dataset_key (run_id, dataset_id, key_value) VALUES (?, ?, ?)",
            params,
        )
    finally:
        cursor.close()
