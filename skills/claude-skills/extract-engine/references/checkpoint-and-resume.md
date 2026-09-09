# Checkpoint and resume mechanics

Granularity is **per dataset** for this MVP, matching the design doc's
acceptance check #10 exactly: "kill the process mid-run, restart with
`--resume`, and the completed datasets are skipped." `meta.run_detail`
deliberately has no last-completed-part column yet — `checkpoint.py`'s own
docstring calls this out as "the documented extension point for part-level
resume later, not an oversight." A laptop going to sleep mid-run and a
Windows Server patch/reboot window are, from this module's point of view,
the identical failure shape: a process died mid-run, and resume only needs
to know which datasets already reached `completed`.

## The three tables

- **`meta.run_log`** — one row per run: `feed_id`, `feed_config_version_id`
  (which workbook version this run used), `anchor_date`, `window_years`,
  `execution_mode`, `status` (`running|completed|failed|aborted`),
  `duration_ms`, `peak_rss_bytes`, `requested_by` (`SUSER_SNAME()`), and
  `error_message` (never row/key/parameter values — text only).
- **`meta.run_detail`** — one row per `(run_id, dataset_id)`, unique on that
  pair: `status` (`pending|running|completed|failed`), `row_count`,
  `part_count`, `checksum` (a `CHAR(64)` SHA-256 hex digest over the
  concatenated bytes of every part file written for that dataset). This is
  what `--resume` actually reads.
- **`meta.run_dataset_key`** — the staged key set implementing dependent-mode
  pull-in: `(run_id, dataset_id, key_value)`, primary keyed on all three,
  where `dataset_id` here is the **primary** dataset whose keys were staged
  (not the dependent dataset consuming them).

## The run lifecycle (`cli.run_cmd` orchestrates; `checkpoint.py` persists)

1. `checkpoint.is_concurrent_run_active(conn, feed_id)` — a `COUNT(*)`
   against `run_log WHERE status = 'running'` for this feed. If nonzero,
   `run` refuses to start a second one. This is an advisory check against a
   race that doesn't show up on a single laptop but matters if a scheduled
   task overlaps a still-running previous run on a server.
2. Fresh run: `checkpoint.start_run` inserts a new `run_log` row with
   `status='running'` and returns its `run_id`.
   Resumed run (`--resume --run-id N`): no new `run_log` row; `N` is reused
   as `active_run_id` directly.
3. `checkpoint.get_run_detail_statuses(conn, run_id)` is read only when
   `--resume` is set — otherwise every dataset in the feed is scheduled with
   an empty status map.
4. `checkpoint.datasets_to_process(datasets, statuses, resume=...)` is the
   actual skip logic:
   - `resume=False` → every dataset in the feed, unconditionally, even one
     already marked `completed` from a previous run.
   - `resume=True` → every dataset **except** one whose `statuses[dataset_id]
     .status == "completed"`. A dataset with **no** `run_detail` row at all
     (never started), or one whose status is `pending`/`running`/`failed`,
     is (re)processed **from scratch** — its source query runs again, its
     output part files are rewritten from the first row. There is no partial-
     file or partial-batch resume within a dataset.
5. For each dataset actually processed, in `run_ordinal` order:
   `mark_dataset_running` inserts a `run_detail` row (`status='running'`)
   before any read happens; on success, `mark_dataset_complete` updates that
   row with `row_count`/`part_count`/`checksum`; on any exception,
   `mark_dataset_failed` updates it to `status='failed'` and the exception is
   re-raised (the run itself is left in `status='running'` in `run_log` —
   there is no automatic `run_log.status='failed'` transition on a dataset
   failure; only a clean run reaches `complete_run`).
6. If the dataset that just finished is a **primary** dataset some other
   *dependent* dataset in the feed depends on
   (`cli._key_accumulator_for` finds this by scanning `all_datasets` for a
   `dependent` whose `depends_on_dataset_id` matches), its distinct key
   values were accumulated during the read (`ep.KeyAccumulator`, or in SQL
   mode via the view's `__key_source_<col>` column) and are staged into
   `meta.run_dataset_key` via `checkpoint.stage_dataset_keys` immediately
   after that dataset completes — so a dependent dataset later in the same
   `run_ordinal` sequence can filter against them. `stage_dataset_keys` uses
   `cursor.fast_executemany = True` when the driver supports it (real
   pyodbc; a fake test cursor lacks the attribute and is skipped over), since
   the accumulated key set — while bounded by distinct values, not row
   volume — can still be large enough that row-by-row inserts would be slow.
   Key values are stored as `str(value)` regardless of source type.
7. After every scheduled dataset finishes, `checkpoint.complete_run` updates
   `run_log` to `status='completed'` with the total `duration_ms` and the
   peak RSS observed across the whole run (`psutil.Process().memory_info()
   .rss`, sampled after each dataset).

## What resume does **not** do

- It does not resume a partially-written file part — a `failed`/`running`
  dataset's output is rewritten from scratch, not appended to.
- It does not re-validate the feed's `meta.*` configuration against the
  workbook again; it trusts the `feed_config_version_id` the *original* run
  recorded.
- It does not detect a configuration change made between the failed attempt
  and the resume — `run --resume` reuses whatever `anchor_date`/
  `window_years`/`execution_mode` the original `start_run` call recorded
  implicitly by not re-deriving them; a resumed run re-enters at
  `checkpoint.datasets_to_process` with the *same* `run_id`, so it is
  querying the same `feed_config_version_id` and the same effective
  anchor/window the original attempt used (those aren't re-read from
  `run_log` on resume in `cli.run_cmd` today — `effective_mode`/
  `effective_anchor`/`effective_window` are recomputed from the current
  `--mode`/`--anchor-date`/`--window-years` flags or feed defaults each
  invocation, so passing different flags on a `--resume` invocation than the
  original run used will not raise an error, but will run any newly-started
  dataset under the new parameters rather than the original ones. Pass the
  same flags on both invocations if reproducibility across the interruption
  matters).
