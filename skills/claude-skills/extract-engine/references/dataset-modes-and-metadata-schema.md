# Dataset modes and the `meta.*` metadata schema

This is the piece of the design most grounded in inference rather than a
supplied spec. `docs/extract-engine-mvp-prompt-v2-polars.md` says the
metadata model "carries over" from an earlier MVP prompt; that earlier prompt
does not exist anywhere in this repository. Everything below beyond the two
v2-doc-explicit additions (`meta.feed.default_execution_mode`;
`meta.run_log.execution_mode`/`duration_ms`/`peak_rss_bytes`) was
reconstructed from context clues in the v2 doc itself — the "three data
files", the dependent-mode language, "no orphaned references" — and should be
reviewed against a real customer schema before production use. See
`scripts/seed/seed_schema.sql`'s own header comment, which says this in the
code, not just here.

## Schemas created

`seed_schema.sql` creates exactly three new schemas and never touches an
existing one:

- `src` — synthetic source data for dev/test only.
- `meta` — this catalog.
- `gen` — execution-path-B's generated views.

## `meta.feed` — one row per named extract job

| Column | Notes |
| --- | --- |
| `feed_name` | unique |
| `source_server`, `source_database` | where this feed reads from |
| `delimiter` | default `\|` |
| `collision_action` | `sanitize` \| `fail` |
| `collision_char` | default `' '`; a `CHECK` constraint requires `collision_char <> delimiter` |
| `line_ending` | `CRLF` \| `LF` |
| `null_sentinel` | literal text written for a NULL value |
| `max_rows_per_file` | default 1,000,000; part-rolling boundary |
| `emit_header_row`, `emit_trailer_row`, `emit_concat_ws_line` | output layout switches |
| `default_execution_mode` | `polars` \| `sql` — v2 doc's explicit addition |
| `output_root` | destination directory |
| `anchor_date_default`, `window_years_default` | defaults for `--anchor-date`/`--window-years` |

## `meta.dataset` — one row per dataset within a feed

`dataset_mode` is the reconstructed three-mode design:
`primary` \| `dependent` \| `reference`. Mode-specific columns are
loader-enforced (checked by `config_loader.validate`), not database `CHECK`
constraints:

- `primary` requires `window_date_column`.
- `dependent` requires `depends_on_dataset_id`, `dependency_source_column`
  (the FK column, on the *depends-on* dataset's own rows), and
  `dependency_target_column` (the matching column on *this* dataset's table).
  `include_own_window` (default 0) additionally requires `window_date_column`
  when set.
- `reference` requires neither.

`primary_key_columns` is a comma-separated list of source column names
(`metadata._split_columns`), used only for identification — it plays no role
in query construction beyond that.

## `meta.dataset_lookup` — "calculator table" joins

One row per joined lookup table: `lookup_alias` (the SQL alias),
`lookup_schema`/`lookup_table`, `join_source_column` (on the dataset's own
table), `join_lookup_column` (on the lookup table), `join_type`
(`left` \| `inner`). `build_join_clause` (`query_builder.py`) emits one
`LEFT JOIN`/`INNER JOIN` per row, shared by both execution paths.

## `meta.field_map` — one row per output column

| Column | Notes |
| --- | --- |
| `ordinal` | output column position; unique per dataset |
| `target_column` | output header name; unique per dataset; must not contain `\|`, `\r`, `\n`, or `\t` |
| `source_expression` | a bare source column name, or `lookup:<alias>.<column>` |
| `data_type` | a neutral token — see below |
| `rule_name` | must resolve in `rules.registry()`; default `passthrough` |
| `rule_params` | a JSON object, parsed by `rules.parse_rule_params` |
| `nullable`, `default_value` | `default_value` becomes a `fill_null`/`ISNULL` wrap in both renderings |

`data_type` is a neutral token validated against
`config_loader._DATA_TYPE_PATTERN`:
`int | bigint | bit | date | datetime | datetime2 | varchar(n) | nvarchar(n | MAX) | char(n) | nchar(n) | decimal(p,s)`.
`query_builder.data_type_to_polars` maps each token to the exact Polars dtype
used in `schema_overrides` — this is what pins every batch's schema instead
of letting Polars infer it per batch.

`FieldMap.source_column`/`FieldMap.lookup_alias` (properties on the
dataclass) parse the `lookup:<alias>.<column>` prefix; a bare column name has
no `lookup_alias` and resolves against the dataset's own base table alias.

## `meta.feed_config_version` — the Excel-load audit trail

One row per distinct workbook upload (`workbook_sha256` unique per feed).
Every run stamps the *current* version's id, so output always traces back to
an exact workbook. `workbook_filename` is filename-only (never a full
customer path); `sheet_summary_json` is shape only — row counts per sheet,
never values.

## `meta.run_log` / `meta.run_detail` / `meta.run_dataset_key`

Covered in full in `checkpoint-and-resume.md`. In one line: `run_log` is one
row per run, `run_detail` is one row per `(run_id, dataset_id)` and is what
`--resume` reads, `run_dataset_key` is the staged key set a dependent dataset
filters against.

## The three dataset modes' `WHERE`-clause shapes

Implemented once in `query_builder.build_where_clause` and reused by
`execution_sql.build_view_read_query` so both paths filter identically —
this identity is what makes `--compare-modes` (see
`execution-engines.md` for its actual, unimplemented status) meaningful in
principle.

- **`reference`** — `1 = 1`. No filter at all; ships in full every run.
- **`primary`** —
  `<window_date_column> BETWEEN ? AND ?`, where the window is
  `[anchor_date - window_years, anchor_date]` (computed via
  `anchor_date.replace(year=anchor_date.year - window_years)`, so no leap-day
  edge case is handled beyond what `datetime.date.replace` does natively).
- **`dependent`** —
  ```sql
  <dependency_target_column> IN (
      SELECT key_value FROM meta.run_dataset_key
      WHERE run_id = ? AND dataset_id = ?  -- dataset_id = the dataset it depends on
  )
  ```
  optionally `OR`-ed with its own primary-style window predicate when
  `include_own_window = 1`. **With `include_own_window = 0` (the
  reconstructed default), the dependent dataset's own date column plays no
  part in the filter at all** — this is deliberate, and is exactly what
  proves acceptance check #5: a member created in 1999 is included in the
  `Member` extract because an in-window `Claim` references it, even though
  the member's own `created_date` is decades outside any window.

`reference` and `dependent` datasets never accept `anchor_date`/`window_years`
unless `include_own_window` applies; `primary` always requires both, and
`dependent` always requires `run_id` to resolve its staged key set.

## Validation performed by `config_loader.validate`

Runs entirely offline except one check. In order:

1. `Feed`: `feed_name`, `source_server`, `source_database`, `output_root` are
   required; `collision_char` must not equal `delimiter`.
2. `Datasets`: no duplicate `dataset_name`; `dataset_mode` must be one of the
   three values; `source_schema`/`source_table`/`primary_key_columns`
   required; mode-specific requirements as above; a `depends_on_dataset` must
   name another row in the same workbook and must not name itself; a
   dependency cycle anywhere in the graph is rejected
   (`_check_dependency_cycles` walks each `dependent` dataset's chain).
3. `Lookups`: `lookup_alias`, `lookup_schema`, `lookup_table` required; if a
   `check_lookup_tables_exist(schema, table)` callable was supplied (the CLI
   wires this to a live `OBJECT_ID()` check), a missing table is rejected —
   the one check that genuinely needs a database connection.
4. `FieldMap`: dataset must exist; `ordinal` and `target_column` must each be
   unique per dataset; `target_column` must not contain a delimiter or
   newline character; a `lookup:<alias>.<column>` `source_expression` must
   reference an alias actually declared on `Lookups` for that dataset; a bare
   `source_expression` is required otherwise; `data_type` must match the
   recognized-token pattern; `rule_name` must resolve in the registry, and
   its `rule_params` must satisfy that rule's declared schema
   (`rules.validate_params`).

## Excel workbook sheet-to-table mapping

See `SKILL.md`'s table for the sheet list. Two things worth calling out
specifically:

- `KeyGeneration` rows are additional `FieldMap` rows authored on a separate
  sheet purely for authoring clarity — `load_workbook` concatenates them onto
  `field_maps` before validation ever runs, so a `KeyGeneration` row is
  indistinguishable from a `FieldMap` row downstream.
- `upsert()` is idempotent by `workbook_sha256`: reloading byte-identical
  bytes for the same feed returns the existing `feed_config_version_id`
  without writing anything. When it *is* a new version, it fully reinserts
  `meta.field_map` and `meta.dataset_lookup` per dataset (delete-scoped to
  `WHERE dataset_id = ?`, then reinsert) rather than diffing row by row, and
  it also **updates** `meta.dataset`'s core columns on an existing dataset —
  found necessary live, because the first cut only resolved the existing
  `dataset_id` and never refreshed columns like `window_date_column`, so a
  changed mapping silently kept querying the old column name forever.
