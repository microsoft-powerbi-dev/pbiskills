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


@dataclass(frozen=True)
class RunHeader:
    """The parameters a run was started with, as recorded in meta.run_log."""

    run_id: int
    feed_id: int
    anchor_date: datetime.date
    window_years: int
    execution_mode: str
    status: str


def get_run(conn, run_id: int) -> Optional[RunHeader]:
    """Read back the parameters a run was started with.

    ``--resume`` has to extract the SAME window the original run did.
    Re-deriving anchor_date/window_years from the CLI defaults at resume time
    instead would silently mix windows within one run: anchor_date defaults to
    *today*, so a run started yesterday and resumed today would give its
    already-completed datasets yesterday's window and its remaining datasets
    today's - a file set that looks complete and is not self-consistent.
    meta.run_log records these three values precisely so they can be
    recovered, and this is what recovers them.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT run_id, feed_id, anchor_date, window_years, execution_mode, status "
            "FROM meta.run_log WHERE run_id = ?",
            run_id,
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
    if row is None:
        return None
    anchor = row[2]
    if isinstance(anchor, datetime.datetime):
        anchor = anchor.date()
    elif isinstance(anchor, str):
        anchor = datetime.date.fromisoformat(anchor)
    return RunHeader(
        run_id=int(row[0]),
        feed_id=int(row[1]),
        anchor_date=anchor,
        window_years=int(row[3]),
        execution_mode=row[4],
        status=row[5],
    )


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
    """Mark a dataset in-flight, whether or not a prior attempt left a row.

    Idempotent by necessity, not by preference. meta.run_detail carries
    UNIQUE (run_id, dataset_id) and ``--resume`` reuses the original run_id,
    so any dataset that reached 'running' or 'failed' before the process died
    ALREADY has a row - which is exactly the set of datasets --resume exists
    to retry. A plain INSERT here therefore failed every resume of a run that
    died inside a dataset, with "Violation of UNIQUE KEY constraint
    'uq_run_dataset'" (reproduced live against a run whose first dataset was
    left 'failed').

    Reprocessing a dataset also invalidates the previous attempt's results, so
    the update path clears row_count/part_count/checksum/completed_at rather
    than leaving a stale checksum attached to files that are about to be
    rewritten - a resumed dataset that failed again would otherwise still
    advertise the old run's checksum.

    Uses the same select-then-branch upsert shape as
    ``config_loader.upsert``'s dataset handling rather than MERGE or a
    rowcount check, so both paths stay single, parameterized statements on
    the guardrail's allow-list and stay testable against a fake cursor.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT run_detail_id FROM meta.run_detail WHERE run_id = ? AND dataset_id = ?",
            run_id, dataset_id,
        )
        existing = cursor.fetchone()
        if existing:
            cursor.execute(
                "UPDATE meta.run_detail SET status = 'running', started_at = SYSUTCDATETIME(), "
                "completed_at = NULL, row_count = NULL, part_count = NULL, checksum = NULL "
                "WHERE run_detail_id = ?",
                int(existing[0]),
            )
        else:
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
        # Skip anything already staged for this (run_id, dataset_id).
        # meta.run_dataset_key's primary key is (run_id, dataset_id,
        # key_value), and --resume reprocesses a dataset that did not reach
        # 'completed' - including one that crashed in the window between
        # staging its keys (one committed transaction) and being marked
        # complete (a separate one). Re-staging blind would then abort the
        # resumed run on a primary-key violation.
        cursor.execute(
            "SELECT key_value FROM meta.run_dataset_key WHERE run_id = ? AND dataset_id = ?",
            run_id, dataset_id,
        )
        already_staged = {row[0] for row in cursor.fetchall()}
        # dict.fromkeys dedupes while preserving order. The accumulator hands
        # over distinct *typed* values, but key_value is VARCHAR and two
        # distinct values can share one string form (1 and "1"), which would
        # itself violate the primary key.
        pending = [
            text
            for text in dict.fromkeys(str(value) for value in key_values)
            if text not in already_staged
        ]
        if not pending:
            return
        try:
            cursor.fast_executemany = True
        except AttributeError:
            pass  # a fake/test cursor won't have this; real pyodbc does
        cursor.executemany(
            "INSERT INTO meta.run_dataset_key (run_id, dataset_id, key_value) VALUES (?, ?, ?)",
            [(run_id, dataset_id, text) for text in pending],
        )
    finally:
        cursor.close()
