# The two execution engines, in detail

Same metadata (`meta.feed`, `meta.dataset`, `meta.field_map`,
`meta.dataset_lookup`), same rule catalog, two backends. `feed.
default_execution_mode` picks one; `extract run --mode polars|sql`
overrides it for a single run. Both are meant to produce byte-identical
output — see the end of this file for the actual, unimplemented state of the
mechanism the design doc specifies to prove that.

## Path A: Polars in process (`execution_polars.py`)

1. **Read.** `read_batches` calls `pl.read_database(plan.sql, conn,
   iter_batches=True, batch_size=100_000, execute_options={"parameters":
   plan.params}, schema_overrides=plan.schema_overrides)`. `plan` comes from
   `query_builder.build_select`, which selects **raw, pre-transform, pre-rename**
   source columns — one `SELECT` item per distinct source column actually
   referenced by a non-stateful field, deduplicated (two target columns
   reading the same source column select it once). `schema_overrides` is
   derived from `meta.field_map.data_type` via `data_type_to_polars`, never
   left to per-batch inference.
2. **Transform.** `build_transform_exprs` builds one `(target_column,
   pl.Expr)` pair per non-stateful field, applying that field's rule
   (`rule_obj.polars_fn(pl.col(source_column), rule_params)`) and then a
   `fill_null(default_value)` if one is configured. `apply_transform` runs
   the whole batch through **a single `df.select(exprs)` call** — no Python
   loop over rows anywhere in this path.
3. **Row sequence.** `add_row_sequence_columns` runs *after* the transform,
   using a per-target running-offset dict the caller (`cli.run_cmd`) threads
   across batches for one dataset.
4. **Reorder.** `reorder_columns` re-selects into `ordinal` order — the
   transform step's own selection order is not guaranteed to match it.
5. **Sanitize.** `apply_sanitization` inspects every `pl.Utf8` column. Under
   `collision_action='fail'` it runs `sanitizer.check_collision_polars`
   (a `str.contains` check for the delimiter, CR, LF, TAB) per string column
   and raises `CollisionError` naming only the column and batch ordinal —
   never the value — on the first hit, leaving the batch otherwise untouched.
   Under `'sanitize'` it always chains `str.replace_all` for all four
   characters, whether or not a collision is present.
6. **Write.** `PartWriter` opens every part in **binary** mode
   (`open(path, "wb")`) with an explicit `encoding` and an explicit
   `line_ending` (bytes, `b"\r\n"` or `b"\n"`) — text mode on Windows would
   silently translate `\n` to `\r\n` regardless of what the feed configures.
   It rolls to a new part at `max_rows_per_file`, slicing a batch that would
   straddle the boundary rather than ever splitting mid-write, and
   accumulates a running SHA-256 over every byte written (`_hasher`), which
   becomes `run_detail.checksum`. A trailer row (`TRAILER|<row_count>`) is
   written on `_close_part` when `emit_trailer_row` is set.
7. **Key accumulation.** For any dataset another dataset in the feed depends
   on, `KeyAccumulator.add_batch` collects `df.get_column(key).drop_nulls().unique()`
   per batch; `.finish()` concatenates and dedupes across the whole dataset.
   This is safe to hold in memory because cardinality is bounded by distinct
   key values, not row volume.

## Path B: transform in SQL via a generated view (`execution_sql.py`)

1. **Generate the view.** `generate_view_ddl` builds
   `CREATE OR ALTER VIEW gen.v_<dataset> AS SELECT ... FROM <table> AS [t]
   <joins>` — **no `WHERE` clause, ever**. It is a static, deploy-once
   transform: per-run window/dependency filtering cannot be baked into a view
   that is deployed once and reused across many runs with different anchor
   dates. Each field's expression (`_column_expression`) composes
   source → rule (`rules.render_sql`) → sanitize
   (`sanitizer.sanitize_expr_sql`, only under `collision_action='sanitize'`)
   → `ISNULL(..., default)` if a default is configured, in that order.
2. **The internal filter-column design (reconstructed, flagged in code as
   "confirm with user").** The view's real output columns are the
   *formatted* target values — e.g. a date already rendered as `VARCHAR` —
   which are not safe or reliable to filter on (string-sorting a formatted
   date is fragile, and a dependent dataset's target column may not even be
   a plain passthrough). The fix: the view *additionally* exposes the raw,
   unformatted filter column(s) under reserved names:
   - `__window_key` — the dataset's own raw `window_date_column`, emitted
     whenever the dataset is `primary`, or `dependent` with
     `include_own_window=1`.
   - `__dependency_key` — the raw `dependency_target_column`, emitted for
     every `dependent` dataset.
   - `__key_source_<raw_column>` — the raw column a *dependent* dataset
     elsewhere in the feed needs staged from *this* (primary) dataset's own
     view, via `key_columns_needed_from`/`extra_key_columns`. This exists
     because the view's `SELECT` list is target-named (e.g. `MemberId`), not
     raw-named (e.g. `member_id`); the raw column is not otherwise guaranteed
     retrievable from the deployed view at all. Found live: Polars mode
     happened to work by accident, because it reads raw column names before
     the rename-to-target step; SQL mode reads only the view's already-renamed
     output, so key staging silently produced an *empty* set there until this
     column existed.
   `build_view_read_query` then does `SELECT <target columns> FROM
   gen.v_<dataset> WHERE <window/dependency predicate against the reserved
   columns>` — mirroring `query_builder.build_where_clause`'s mode semantics
   exactly (`window_column_sql`/`dependency_column_sql` overrides point it at
   `[__window_key]`/`[__dependency_key]` instead of a raw table alias).
3. **The "third gear": `CONCAT_WS` line collapse.** When
   `feed.emit_concat_ws_line` is set, the view emits a single pre-joined
   `[Line]` column instead of individual target columns, and the client
   writes lines with no column handling at all. **The CONCAT_WS null-shifting
   trap**: SQL Server's `CONCAT_WS` skips a `NULL` argument entirely rather
   than emitting an empty position, silently shifting every downstream field
   on any row with a null — a corrupt file that still passes a row-count
   check. `render_concat_ws_line` wraps **every** argument in `ISNULL(...,
   '')` unconditionally; there is no code path that omits it. A LocalDB-gated
   regression test (`test_concat_ws_null_shift_regression_against_localdb`)
   demonstrates the bug exists in SQL Server itself (`CONCAT_WS('|', 'A',
   NULL, 'C')` → `'A|C'`, one pipe lost) against the wrapped, correct form
   (`'A||C'`).
4. **Deploy, explicitly.** `deploy_views` never sends DDL without
   `confirmed=True` (the CLI's `--i-understand-this-writes-to-the-database`).
   Even when confirmed, every generated statement still passes through
   `guardrails.classify_write_statement` before it is sent. `explain` needs
   no connection at all — it is pure DDL generation, which is why "what you
   show a DBA" works without touching the database.
5. **Read.** `cli._read_dataset_batches` runs the view-read query through
   `pl.read_database(..., iter_batches=True, batch_size=100_000)` exactly
   like path A, but the transform step is skipped entirely — the batch is
   only reordered to the target column list (or reduced to `["Line"]`) and
   written. No `apply_transform`/`apply_sanitization` call happens on this
   path; formatting and sanitization already happened inside the view.

## Tradeoffs (per the design doc, documented rather than hidden)

- **Path A** keeps all transform load on the client machine. Memory is
  bounded by batch size (`test_memory_ceiling.py` asserts this at 500,000
  rows), and the database only ever runs a plain `SELECT`. Cost: the client
  needs Polars and enough CPU to do the transform work itself.
- **Path B** pushes transform work onto the SQL Server via the generated
  view — "zero per-value work" on the client. Cost: it moves load onto a
  shared database server. That is fine on a dedicated replica and
  potentially unwelcome on a production instance; this is a governance
  question the design doc asks to be raised explicitly (`FINDINGS.md` in the
  original spec — not present in this skill folder), not solved in code.
- `date_format` deliberately renders as `CONVERT`, not `FORMAT`, in the T-SQL
  path: `FORMAT` is CLR-backed and notably slow at volume, which would
  defeat the point of pushing work server-side.

## What the design doc asks for here that does not exist

`docs/extract-engine-mvp-prompt-v2-polars.md` specifies `extract benchmark
--feed <name> --compare-modes`: run the same feed both ways, record wall
time and peak RSS for each, compare output checksums, fail on a mismatch,
and write the result to `BENCHMARK.md`. **None of this is implemented.**
`cli.py` has no `benchmark` command at all — not `--baseline`, not
`--compare-modes`. The only mechanism actually in the codebase that connects
the two paths is that they share `query_builder`'s mode semantics and the
rule catalog's conformance suite; there is no automated, end-to-end proof
that a real feed run through both paths produces identical files. Treat
"the two paths produce byte-identical output" as a design intent backed by
shared logic and unit tests, not as something CI currently verifies
end-to-end.
