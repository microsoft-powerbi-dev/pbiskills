---
name: Extract Engine
description: >
  Use this skill when building, running, or troubleshooting the
  metadata-driven SQL Server extract engine: a system that reads a catalog of
  feeds/datasets/field mappings out of `meta.*` tables (authored in an Excel
  workbook) and produces pipe-delimited flat files, with two interchangeable
  execution backends (in-process Polars, or a generated SQL Server view) that
  are required to produce byte-identical output. Covers the CLI
  (`extract load-config|validate-config|dry-run|explain|deploy-views|run`),
  the Excel-to-`meta.*` config loader and its validation, the rule catalog
  that renders every transform both as a Polars expression and a T-SQL
  fragment, the three dataset modes (primary/dependent/reference) and how a
  dependent dataset pulls in out-of-window records, the write-statement
  allow-list that is the only thing permitted to touch the database, and
  per-dataset checkpoint/resume. Example requests: "add a new field mapping to
  the ClaimsExtract feed", "why do the polars and sql outputs differ for this
  feed", "the extract died overnight, how do I resume it", "generate the view
  DDL for this dataset so I can show a DBA", "what happens if two rows in
  FieldMap collide".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
triggers:
  - extract engine
  - metadata driven extract
  - field map
  - meta.feed
  - meta.dataset
  - meta.field_map
  - dataset mode
  - primary dataset
  - dependent dataset
  - reference dataset
  - rule catalog
  - rule conformance
  - polars execution
  - sql execution mode
  - compare modes
  - gen.v_
  - generated view
  - deploy-views
  - extract run
  - extract dry-run
  - checkpoint resume
  - run_log
  - run_detail
  - run_dataset_key
  - collision_action
  - sanitize delimiter
  - pipe delimited file
  - trailer row
  - key generation
  - row_sequence
  - claims extract
---

# Extract Engine

A metadata-driven engine that reads tables out of SQL Server and writes
pipe-delimited flat files, where a mapping change is an Excel edit and a
rerun — never a code edit or a deploy.

This is a **prototype built from a design prompt**
(`docs/extract-engine-mvp-prompt-v2-polars.md`), and the metadata schema in
particular is explicitly labeled reconstructed rather than supplied — see
`scripts/seed/seed_schema.sql`'s own header comment. Read
`references/dataset-modes-and-metadata-schema.md` before assuming any
`meta.*` column is load-bearing against a real customer schema.

For understanding the source database this engine reads from, use
`skills/claude-skills/sql-server-schema/`. That skill's `connection.py` is
reused verbatim here (see `scripts/extract_engine/connection.py`); this
engine's own contribution is that it genuinely writes to the database, so it
does not reuse sql-server-schema's SELECT-only connection contract.

## When to use

- You are adding, changing, or debugging a field mapping, a lookup join, a
  dataset mode, or a transform rule for a feed.
- Polars-mode and SQL-mode output disagree for a feed and you need to know why.
- A run died (laptop sleep, disconnect, crash) and you need to resume it
  without reprocessing completed datasets or re-pulling rows.
- You need to hand a DBA the generated view DDL for buy-in before deploying it.
- You are deciding whether a feed should run transform-in-Polars or
  transform-in-SQL.

Do **not** use this to run ad-hoc queries against the source database or to
write arbitrary SQL: see the guardrails section below. There is no code path
here that sends anything but the narrow, enumerated set of statements
`guardrails.classify_write_statement` allows.

## What actually exists here (read this before trusting the design doc)

The design doc (`docs/extract-engine-mvp-prompt-v2-polars.md`) and even
`cli.py`'s own module docstring describe pieces that are **not** implemented
in this codebase today:

- **`extract benchmark`** (`--baseline --dataset <name>` and
  `--feed <name> --compare-modes`) does not exist as a CLI command. `cli.py`
  defines exactly six commands — `load-config`, `validate-config`, `dry-run`,
  `explain`, `deploy-views`, `run` — and none of them is `benchmark`. The
  step-zero baseline read and the compare-modes checksum/timing comparison
  the doc requires are not wired up anywhere in `scripts/`.
- **`extract_engine_authoring_mcp.py`**, which `cli.py`'s docstring says
  exposes the read-only commands (`dry-run`, `explain`, `validate-config`,
  `load-config`'s dry-run path) as MCP tools, does not exist anywhere in this
  repository. This skill is currently **CLI-only**: there is no MCP server to
  point a client at, unlike `sql-server-schema` or `rdl-generation`.
- The design doc's required deliverables `BENCHMARK.md`, `FINDINGS.md`, and a
  step-by-step `README.md` are not present in this skill folder. Only code,
  tests, and the seed/example assets exist.
- `examples/sample-extract-output/` exists as an empty directory — no sample
  output has been committed there, unlike `sql-server-schema`'s
  `generated-pack/` reference output.

Treat all of the above as gaps to fill in, not as evidence a feature was
removed on purpose.

## The five things that will bite you

1. **Rule names are looked up, never `eval`'d.** `meta.field_map.rule_name`
   resolves against `rules.py`'s in-process registry by exact string match. An
   unrecognized name is a `config_loader` validation failure before any
   database write happens, not a code path that ever executes. See
   `references/rule-catalog-and-conformance.md`.
2. **A generated view (`gen.v_*`) has no `WHERE` clause, ever.** It is a
   static, deploy-once transform. Per-run window/dependency filtering happens
   in the client's read query against reserved internal columns
   (`__window_key`, `__dependency_key`, `__key_source_<col>`) the view exposes
   alongside the real target columns. See `references/execution-engines.md`.
3. **Nothing reaches the database except through
   `guardrails.classify_write_statement`'s enumerated allow-list**, and
   `deploy-views`/`run`'s write path is CLI-only — never one agent decision
   away from firing DDL or a real extract. See `references/write-guardrails.md`.
4. **Checkpoint granularity is per-dataset, not per-file-part.** `--resume`
   skips a dataset only if `meta.run_detail` already shows it `completed`; a
   dataset that was `running` or `failed` when the process died is
   reprocessed **from scratch**, including its source query. A resumed run
   takes its `anchor_date`, `window_years` and `execution_mode` from the
   original `meta.run_log` row, never from today's defaults, so every dataset
   in one run shares one window — an explicit `--anchor-date`/`--window-years`
   /`--mode` that contradicts the record is refused. See
   `references/checkpoint-and-resume.md`.
5. **Schema is always pinned, never inferred.** Both `pl.read_database` calls
   pass `schema_overrides` derived from `meta.field_map.data_type`
   (`query_builder.data_type_to_polars`). A batch where a nullable column
   happens to be entirely null must not be allowed to silently change dtype
   mid-run.

## The CLI

Entry point: `scripts/extract_engine/cli.py`, a Click group. All six commands,
exactly as the parser defines them:

```
extract load-config     --workbook <path> --feed <name> [--dry-run] [--allow-config-write]
extract validate-config --feed <name>
extract dry-run         --feed <name>
extract explain         --feed <name> --dataset <name> --mode sql [--out <path>]
extract deploy-views    --feed <name> [--i-understand-this-writes-to-the-database]
extract run             --feed <name> [--mode polars|sql] [--anchor-date YYYY-MM-DD]
                         [--window-years N] [--resume --run-id N]
```

- `load-config` parses and validates the workbook every time; it only
  upserts into `meta.*` if you additionally pass `--allow-config-write`. It
  refuses with a `ClickException` if the workbook's `Feed` sheet names a
  different feed than `--feed`.
- `validate-config` re-checks the already-loaded `meta.dataset` rows for a
  feed (primary needs `window_date_column`, dependent needs
  `dependency_target_column`) with no Excel file involved.
- `dry-run` prints, per dataset in `run_ordinal` order, either the generated
  view DDL (`feed.default_execution_mode == "sql"`) or the parameterized
  `SELECT` `query_builder.build_select` would issue (polars mode).
- `explain --mode sql` is the only mode `explain` accepts. It needs no
  database write and is what you hand a DBA; pass `--out` to save it as an
  artifact via `guardrails.resolve_artifact_path` instead of printing it.
- `deploy-views` always prints what it *would* deploy; it only actually runs
  `CREATE OR ALTER VIEW` when you pass
  `--i-understand-this-writes-to-the-database`.
- `run` resolves `effective_mode` from `--mode` or else
  `feed.default_execution_mode`, refuses to start if another run is already
  `'running'` for the same feed (`checkpoint.is_concurrent_run_active`), and
  requires a **current** `meta.feed_config_version` row for the feed (i.e.
  `load-config` must have run first).

## Configuration: Excel authors, metadata executes

Neither execution path ever reads the workbook directly — only
`config_loader.py`'s `load_workbook`/`validate`/`upsert` bridge into
`meta.*`, and everything downstream reads the typed dataclasses in
`metadata.py`. Sheet-to-table mapping:

| Sheet | Lands in |
| --- | --- |
| `Feed` (required, one row) | `meta.feed` |
| `Datasets` (required) | `meta.dataset` |
| `Lookups` (optional) | `meta.dataset_lookup` |
| `FieldMap` (required) | `meta.field_map` |
| `KeyGeneration` (optional) | folded into `FieldMap` rows before validation — a separate authoring sheet for `rule_name = row_sequence`, merged by `load_workbook` |
| `ExtractParameters` (optional) | `feed.anchor_date_default` / `feed.window_years_default` |
| `OutputLayout` (optional) | `feed.emit_header_row` / `emit_trailer_row` / `emit_concat_ws_line` / `line_ending` / `null_sentinel` / `max_rows_per_file` — column order is not here, it is always `FieldMap.ordinal` |

`validate()` runs entirely offline (structural checks against the workbook
and the Python rule registry) except one check: whether a `Lookups` row's
table actually exists in the source database, via an injected
`check_lookup_tables_exist(schema, table)` callable. `upsert()` is
transactional and idempotent — reloading a workbook with an unchanged
SHA-256 for the same feed is a no-op that returns the existing
`feed_config_version_id` rather than duplicating it, and every `DELETE` it
issues is scoped to `WHERE dataset_id = ?`, matching the guardrail allow-list
exactly (`meta.field_map` and `meta.dataset_lookup` are fully reinserted per
dataset, never patched row-by-row).

Full schema, dataset modes, and validation rules:
`references/dataset-modes-and-metadata-schema.md`.

## Two execution backends, one rule catalog

`feed.default_execution_mode` (`'polars'` or `'sql'`) picks the backend;
`extract run --mode` overrides it for one run. Both read the same
`meta.field_map`/`meta.dataset_lookup` rows and the same rule catalog
(`rules.py`), and are required to produce byte-identical files — that
equivalence is what the (unimplemented) `--compare-modes` was meant to prove
mechanically.

- **Polars (`execution_polars.py`)** — transform runs in the client process.
  `pl.read_database(..., iter_batches=True, batch_size=100_000,
  schema_overrides=...)` streams raw source rows; `apply_transform` is one
  `df.select(exprs)` per batch built from each rule's Polars expression
  builder; `apply_sanitization` chains `str.replace_all` for
  delimiter/CR/LF/TAB or raises `sanitizer.CollisionError` under
  `collision_action='fail'`; `PartWriter` writes binary-mode pipe-delimited
  parts with an explicit `line_ending`, rolling at `max_rows_per_file` and
  accumulating a running SHA-256 checksum.
- **SQL (`execution_sql.py`)** — transform runs server-side. Each dataset
  gets a `CREATE OR ALTER VIEW gen.v_<dataset>` with formatting, sanitization,
  and ordinal ordering already baked into its `SELECT`; the client then reads
  the view and does no per-value work. The view is static (no `WHERE`); the
  client's read query filters on the view's reserved internal columns.
  Deploying is a distinct, explicit action (`extract deploy-views`) — `extract
  run --mode sql` never creates or alters a view as a side effect; it fails
  if the view does not already exist.

Tradeoffs, the internal filter-column design, and the CONCAT_WS null-shift
trap: `references/execution-engines.md`. The rule catalog itself (all ten
entries, both renderings, the conformance suite): `references/rule-catalog-and-conformance.md`.

## Guardrails: the write boundary

`guardrails.classify_write_statement(sql) -> WriteVerdict(allowed, kind,
reason)` is the only thing standing between this engine and the database on
every write path — the config loader's upsert, the checkpoint bookkeeping,
and view deployment all route through it (directly, or by construction: their
SQL text is only ever one of the shapes it recognizes).

Permitted, and nothing else:

- A parameterized `INSERT` into a config table (`meta.feed`, `meta.dataset`,
  `meta.field_map`, `meta.dataset_lookup`, `meta.feed_config_version`) or a
  run-bookkeeping table (`meta.run_log`, `meta.run_detail`,
  `meta.run_dataset_key`).
- An `UPDATE ... WHERE <col> = ?` (single equality) on those same tables.
- A `DELETE FROM ... WHERE <col> = ?` (single equality only — a bare `DELETE`
  or a compound `WHERE` is refused unconditionally, regardless of table).
- `CREATE OR ALTER VIEW gen.<name> AS SELECT ...`.

`DROP`, `TRUNCATE`, `EXEC`/`EXECUTE`, `xp_`/`sp_` procedures, `BACKUP`,
`RESTORE`, `SHUTDOWN`, `RECONFIGURE`, `GRANT`/`REVOKE`/`DENY`, and any other
`ALTER` are refused unconditionally — this is the code-level backstop behind
"never drop or delete without asking." Only one statement per call is ever
allowed, with one carve-out: the fixed `; SELECT
CAST(SCOPE_IDENTITY() AS BIGINT) AS id` suffix
`connection.execute_insert_return_identity` appends is recognized as part of
one logical INSERT, not a second stacked statement.

Two connection contexts, named so they can never be confused at a call site:
`read_connection()` (always rolled back on exit, used for every `meta.*`
read) and `write_connection()` (commits on a clean exit, rolls back on
exception — used only for the narrow permitted writes). Detail:
`references/write-guardrails.md`.

## Checkpoint and resume

`meta.run_log` holds one row per run (`execution_mode`, `duration_ms`,
`peak_rss_bytes`, `status`); `meta.run_detail` holds one row per
`(run_id, dataset_id)` with a `pending|running|completed|failed` status, row
count, part count, and a SHA-256 checksum over the file's concatenated bytes.
`extract run --resume --run-id N` reads `run_detail` for that run and skips
every dataset already `completed` — a dataset that was merely `running` or
`failed` reprocesses fully, including its source query and its output files.
`checkpoint.is_concurrent_run_active` refuses to start a second run against
the same feed while one is still `running`. Dependent-mode key staging
(`meta.run_dataset_key`) happens per primary dataset, right after that
dataset finishes, so any dependent dataset later in the same run can filter
against it. Full mechanics: `references/checkpoint-and-resume.md`.

## Quickstart: seed scripts + the sample workbook

Requires a reachable SQL Server (LocalDB or Docker), the ODBC Driver 18 for
SQL Server, and `EXTRACT_ENGINE_SERVER`/`EXTRACT_ENGINE_DATABASE` set (or
passed explicitly) — this engine's own env-var namespace, deliberately
separate from `sql-server-schema`'s `SQLSERVER_MCP_*` so the two skills never
bleed into each other's configuration.

1. **One-command setup.**
   ```bash
   python skills/claude-skills/extract-engine/scripts/seed/seed_three_dataset_feed.py \
       --server "(localdb)\MSSQLLocalDB" --database ExtractEngineDev
   ```
   This deploys the `src`/`meta`/`gen` schemas (`seed_schema.sql`, safe to
   rerun — every `CREATE` is `IF OBJECT_ID(...) IS NULL` guarded), builds
   `examples/sample-workbook.xlsx` if it isn't already committed, seeds
   synthetic `src.claim`/`src.member`/`src.claim_calc`/`src.status_lookup`
   rows, and loads the workbook into `meta.*`. The workbook defines feed
   `ClaimsExtract` with three datasets exercising all three modes: `Claim`
   (primary, windowed on `service_date`), `Member` (dependent on `Claim` via
   `member_id`), and `StatusLookup` (reference, ships in full every run) —
   plus a `calc` lookup join and most of the rule catalog, including a
   `row_sequence` surrogate key authored on the `KeyGeneration` sheet.
2. **See what a run would do.**
   ```bash
   extract dry-run --feed ClaimsExtract
   ```
3. **Run it, Polars mode.**
   ```bash
   extract run --feed ClaimsExtract --mode polars
   ```
   Writes `Claim_part001.txt`, `Member_part001.txt`, `StatusLookup_part001.txt`
   under `feed.output_root`, each with a trailer row, and records
   `run_log`/`run_detail` rows. The seeded data is deliberately built to prove
   dependent-mode pull-in: member 100 was created in 1999 but is referenced by
   an in-window claim (must appear in `Member_part001.txt`); member 200 is
   recent but referenced only by an out-of-window claim (must not appear).
4. **Exercise the SQL backend.**
   ```bash
   extract explain --feed ClaimsExtract --dataset Claim --mode sql   # show a DBA first
   extract deploy-views --feed ClaimsExtract --i-understand-this-writes-to-the-database
   extract run --feed ClaimsExtract --mode sql
   ```
5. **Exercise resume.** Kill the `run` process mid-flight, note the `run_id`
   it printed at start (or query `meta.run_log`), then:
   ```bash
   extract run --feed ClaimsExtract --resume --run-id <id>
   ```

## Bundled assets

### `scripts/extract_engine/`

| Module | Role |
| --- | --- |
| `cli.py` | The Click CLI — the only entry point; no MCP server exists for this skill |
| `config_loader.py` | Excel workbook → `WorkbookConfig` → offline validation → transactional `meta.*` upsert |
| `metadata.py` | Typed dataclasses (`Feed`, `Dataset`, `DatasetLookup`, `FieldMap`) over the `meta.*` catalog — the only module that knows the exact DDL shape |
| `rules.py` | The rule registry: name → `(Polars expr builder, T-SQL template)`. Never `eval`s anything |
| `query_builder.py` | The three dataset modes' `WHERE` shapes, the join clause, and Polars path A's full `SELECT` builder; shared by both execution paths |
| `execution_polars.py` | Path A: streamed read, batch transform, sanitize, `PartWriter` |
| `execution_sql.py` | Path B: generated `gen.v_*` view DDL, the client-side view-read query, `deploy_views`, `explain` |
| `sanitizer.py` | Delimiter/CR/LF/TAB collision handling, both renderings, and the deploy-time collision probe for `fail` mode |
| `checkpoint.py` | `run_log`/`run_detail`/`run_dataset_key` read/write and the `--resume` skip logic |
| `connection.py` | Windows-auth `read_connection()`/`write_connection()`, reusing `sql-server-schema`'s pure connection-string logic |
| `guardrails.py` | `classify_write_statement`, plus reused artifact-path/PII helpers from `sql-server-schema` |

### `scripts/seed/`

| Script | Role |
| --- | --- |
| `seed_schema.sql` | Creates `src`/`meta`/`gen` schemas and every `meta.*` table, idempotently |
| `build_sample_workbook.py` | Regenerates `examples/sample-workbook.xlsx` — entirely fictional |
| `seed_source_data.py` | Synthetic `src.claim`/`src.member`/`src.claim_calc`/`src.status_lookup` rows, keyed so only known synthetic keys are ever deleted |
| `seed_three_dataset_feed.py` | Runs the three scripts above in order, then loads the workbook — the one-command demo setup |

### `tests/`

```bash
python -m pytest skills/claude-skills/extract-engine/tests -q
```

Runs fully offline against `tests/fakes.py`'s `FakeConnection` (same
scripted-read shape as `sql-server-schema`'s fakes, extended with a `.writes`
list every INSERT/UPDATE/DELETE/CREATE is recorded into). Two things are
gated behind live SQL Server and skipped otherwise:

- `EXTRACT_ENGINE_LOCALDB=1` — `test_rules_conformance.py` (every rule's
  Polars output vs. its actual T-SQL execution against LocalDB) and the
  `CONCAT_WS` null-shift regression in `test_execution_sql_view_generator.py`.
- `EXTRACT_ENGINE_SKIP_RSS_TEST=1` — opts *out* of
  `test_memory_ceiling.py`'s 500,000-row RSS assertion (acceptance check #7),
  for a loaded CI runner where RSS measurement is flaky.

### `examples/`

| Path | Contents |
| --- | --- |
| `sample-workbook.xlsx` | The committed three-dataset `ClaimsExtract` feed, regenerate with `build_sample_workbook.py` |
| `sample-extract-output/` | Present, but **empty** — no sample output file is committed here |

### `references/`

| File | Covers |
| --- | --- |
| `dataset-modes-and-metadata-schema.md` | The full `meta.*` schema, the three dataset modes' semantics, the Excel sheet-to-table mapping, and `config_loader`'s validation rules |
| `rule-catalog-and-conformance.md` | All ten registry entries (nine doc rules + `row_sequence`), both renderings, params schemas, and the conformance suite |
| `execution-engines.md` | Path A vs. path B in detail, the internal filter-column design, the CONCAT_WS trap, and the tradeoffs the design doc asks to be documented rather than hidden |
| `write-guardrails.md` | `classify_write_statement`'s full allow-list, the two connection contexts, and `deploy_views`'s confirmation gate |
| `checkpoint-and-resume.md` | `run_log`/`run_detail`/`run_dataset_key` mechanics, concurrent-run refusal, and exactly what `--resume` does and does not cover |

## Related material in this repository

- `RUNBOOK.md` — the developer-facing execution runbook in this same
  folder: setup, the Windows-auth/trusted-connection details, every
  mapping-workbook sheet and column, the generated source queries, the
  output file format, and batch/large-dataset operation. Its "defects found
  and fixed" section records six bugs that were reproduced live and then
  fixed, with the live evidence for each: the `--resume` `uq_run_dataset`
  unique-key violation, `nvarchar(n)`/`varchar(MAX)` being rejected by the
  config loader's validator, `nchar(n)` validating and then failing at
  read-plan time, the `ExtractParameters`/`OutputLayout`/
  `default_execution_mode` values the loader silently dropped, `--resume`
  re-deriving the extraction window from today's defaults instead of the
  original run's, and `--resume` without `--run-id` silently starting a
  whole new run.
- `DEVELOPER.md` — a human setup/run/troubleshoot guide in this same folder,
  with a real, verified end-to-end transcript (offline tests, a live LocalDB
  run on both backends with matching checksums, and the two operational
  gotchas that actually occurred while producing it: the sample workbook's
  hardcoded `D:\Extracts` output path, and stale `running` rows blocking the
  concurrent-run guard after a crash).
- `docs/extract-engine-mvp-prompt-v2-polars.md` — the design prompt this
  skill was built from. Read the "What actually exists here" section above
  before treating anything in it as implemented.
- `skills/claude-skills/sql-server-schema/` — the read-only sibling this
  skill's `connection.py` and `guardrails.py` reuse verbatim; use it to
  understand the source database this engine reads from.
