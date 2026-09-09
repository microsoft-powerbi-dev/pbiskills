# Development prompt: metadata-driven extract engine, MVP prototype (Polars, two execution paths)

Supersedes the earlier MVP prompt. The metadata model, the three dataset modes, and the acceptance demo carry over. What changes is the transform engine and the addition of a second execution path.

## What this prototype has to prove

1. A mapping change is a metadata update and a rerun. No code edit, no deploy.
2. Memory stays bounded regardless of table size, on a Windows laptop.
3. Dependent records are pulled in correctly, so the file has no orphaned references.
4. Delimiter and newline collisions are handled deliberately, not silently.
5. **New.** One metadata catalog drives two execution backends that produce byte-identical output, so the team can pick on measured throughput rather than argument.

## Step zero, before writing any engine code

Build and run `extract benchmark --baseline --dataset Claim` first. It opens a connection, selects the mapped columns from the largest table over the real network from the target laptop, iterates every batch, and discards them. It reports rows per second and MB per second and nothing else.

This number decides whether the rest matters. If a bare read of the claims table takes forty minutes, transform engine choice is noise and the work belongs on the server side. Record the result in `BENCHMARK.md` before proceeding.

## Environment

Python 3.11+, `polars>=1.0`, `pyarrow`, `pyodbc`, `click`, `pytest`, `psutil`. **No pandas anywhere**, including in tests and scratch scripts.

Microsoft ODBC Driver 18 for SQL Server. Confirm whether installing it needs admin rights on the target laptop before planning around it.

SQL Server via Docker or LocalDB for development. Schemas: `src` for synthetic source data, `meta` for the metadata repository, `gen` for generated views.

## Windows laptop constraints

These are not footnotes. Each one has sunk a project of this shape.

**Antivirus.** Real-time scanning will dominate write throughput, especially across many output files. Get an exclusion on the output directory, or measure with and without it so you know what you are actually benchmarking. Include the measurement in `BENCHMARK.md`.

**Encoding.** Python's `open()` on Windows defaults to the locale encoding, usually cp1252. Every file handle in this codebase specifies encoding explicitly, or writes bytes. There are no exceptions to this.

**Line terminators.** Text mode silently translates `\n` to `\r\n`. Open output files in binary mode, or pass `newline=''`. Assert the exact byte sequence in a test rather than trusting it.

**Sleep and disconnect.** A laptop suspends mid-run. Implement per-dataset checkpointing: on completion, write the `run_detail` row, and on `--resume` skip datasets already recorded for that run id. Within a dataset, checkpoint at file part boundaries.

**Disk.** PHI landing on portable storage is a governance question, not a technical one. Note it in `FINDINGS.md` and raise it. If a jump box near the database is available, that is the better host and this prototype should say so.

## Transform rules: two renderings, one catalog

This is the central design change and the part to get right.

Rules are **not** Python callables applied per value. Applying a Python function through `map_elements` round-trips every value through the interpreter and is slower than a plain cursor loop, which would waste the entire benefit of Polars. Instead, each registered rule provides two renderings of the same semantic:

- a **Polars expression builder**: `(pl.Expr, params) -> pl.Expr`, running vectorized in Rust
- a **T-SQL fragment template**: a string producing the same result server-side

```python
@rule(
    "pad_left",
    sql="RIGHT(REPLICATE('{char}', {width}) + CAST({col} AS VARCHAR({width})), {width})",
)
def pad_left(col: pl.Expr, params: dict) -> pl.Expr:
    return col.cast(pl.Utf8).str.pad_start(params["width"], params["char"])
```

Implement the same nine rules as before, both ways:

| Rule | Polars | T-SQL |
|---|---|---|
| `passthrough` | `col` | `{col}` |
| `trim` | `str.strip_chars()` | `LTRIM(RTRIM({col}))` |
| `upper` | `str.to_uppercase()` | `UPPER({col})` |
| `pad_left` | `str.pad_start(w, c)` | `RIGHT(REPLICATE(...))` |
| `truncate` | `str.slice(0, n)` | `LEFT({col}, {n})` |
| `date_format` | `dt.strftime(mask)` | `FORMAT({col}, '{mask}')` or `CONVERT` |
| `decimal_format` | `round` + `cast(Utf8)` | `FORMAT({col}, '{mask}')` |
| `code_lookup` | `replace_strict(map, default)` | `CASE WHEN ... END` |
| `default_if_null` | `fill_null(value)` | `ISNULL({col}, '{value}')` |

Prefer `CONVERT` over `FORMAT` for dates in the T-SQL rendering. `FORMAT` is CLR-backed and notably slow at volume, which defeats the purpose of pushing work server-side. Benchmark it and note the finding.

**The conformance test is the quality gate for this whole design.** For every rule, for a fixed set of inputs including nulls, empty strings, boundary values, and non-ASCII characters, assert the Polars rendering and the T-SQL rendering produce identical strings. If a rule cannot pass conformance, it does not go in the catalog. This test is what lets you trust that switching execution mode does not change the file.

## Execution path A: Polars in process

**Reading.** `pl.read_database(query, connection, iter_batches=True, batch_size=100_000)` yields an iterator of DataFrames. Memory is bounded by batch size, not result size.

One gotcha to handle explicitly: schema is inferred per batch unless you pin it. A batch where a nullable column happens to be entirely null can come back with a different dtype than the previous batch, and your transform expression then fails or silently changes behaviour halfway through a file. Derive the schema from `meta.field_map` data types and pass `schema_overrides` on every call. Do not rely on inference.

Evaluate ConnectorX as an alternative read path and benchmark it, but do not assume it. Its SQL Server support has historically been weaker than its Postgres support.

**Transforming.** Build one `select` expression list from `field_map` ordered by ordinal, applying each rule's Polars expression. Apply it to each batch. The whole transform for a batch is a single `df.select(exprs)` call. No Python loops over rows, no `map_elements`, no `apply`.

**Sanitizing.** Chain `str.replace_all` for the delimiter, CR, LF, and tab, driven by `feed.collision_action` and `collision_char`. For `fail` mode, evaluate `col.str.contains(delim, literal=True).any()` per column per batch and abort with the column name and batch ordinal. Never log the value.

**Writing.** `df.write_csv(handle, separator="|", quote_style="never", include_header=False, null_value=sentinel)`. Open the handle in binary append mode so batches accumulate into one file part. Verify how your Polars version handles the line terminator and assert the exact bytes in a test rather than trusting the default. Roll to a new part at `max_rows_per_file`, never mid-batch-boundary confusion: track the running count and slice the batch if it straddles the threshold.

**Key sets.** Per batch, `df.get_column(key).unique()`. Accumulate into a list of Series and `pl.concat(...).unique()` at dataset end. Cardinality here is bounded by distinct members and providers, not claim volume, so this is safe. Push to the staging table with `fast_executemany=True` on the pyodbc cursor.

## Execution path B: transform in SQL

Same metadata, different backend. A generator reads `field_map` and emits one view per dataset into the `gen` schema, with columns already formatted, sanitized, and in ordinal order. The client then reads the view and writes it with zero per-value work.

```sql
CREATE OR ALTER VIEW gen.v_Claim AS
SELECT
    ISNULL(LTRIM(RTRIM(REPLACE(REPLACE(REPLACE(c.claim_id, '|',' '), CHAR(13),' '), CHAR(10),' '))), '') AS [ClaimNumber],
    CONVERT(VARCHAR(10), c.service_date, 23) AS [DateOfService],
    ...
FROM src.claim c;
```

Sanitization moves into the view as nested `REPLACE` calls. The generator composes them from `feed.collision_action`.

An optional third gear: have the view emit a single pre-joined line column via `CONCAT_WS`. The laptop then writes lines with no column handling at all. **If you do this, wrap every argument in `ISNULL` first.** `CONCAT_WS` in SQL Server skips NULL arguments entirely rather than emitting an empty position, which silently shifts every downstream field on any row with a null. That is a corrupt file that passes a row count check. Test for it deliberately.

Trade-off to document rather than hide: pushing transforms to SQL moves load onto a shared database server. That is fine on a dedicated replica and potentially unwelcome on production. Note it in `FINDINGS.md`.

## Metadata additions

Add to `meta.feed`:

```sql
default_execution_mode VARCHAR(10) NOT NULL DEFAULT 'polars'  -- polars | sql
```

Add to `meta.run_log`:

```sql
execution_mode  VARCHAR(10) NOT NULL,
duration_ms     BIGINT NULL,
peak_rss_bytes  BIGINT NULL
```

Everything else in the metadata schema is unchanged. That is the point: the catalog does not know which backend runs it.

## CLI

```
extract benchmark --baseline --dataset Claim
extract benchmark --feed ClaimsExtract --compare-modes
extract run     --feed ClaimsExtract --mode polars --anchor-date 2026-09-01 --window-years 2
extract run     --feed ClaimsExtract --mode sql
extract run     --feed ClaimsExtract --resume --run-id 42
extract dry-run --feed ClaimsExtract
extract explain --feed ClaimsExtract --dataset Claim --mode sql
extract deploy-views --feed ClaimsExtract
```

`--compare-modes` runs the same feed both ways, records wall time and peak RSS for each, compares the output checksums, and writes the result to `BENCHMARK.md`. It fails if the checksums differ.

`explain --mode sql` prints the generated view DDL. This is what you show a DBA to get buy-in on path B.

## Acceptance demo

Carries forward the seven checks from the previous prompt, with three added.

1. Clean run produces three data files, a manifest, and `run_log` and `run_detail` rows.
2. Change a date format mask in `field_map`, rerun, output changes, `git status` clean.
3. Insert a field at ordinal 8, rerun, column appears in the right position.
4. Rerun with `--window-years 1`, counts drop correspondingly.
5. Member file contains records whose `created_date` predates the window. This proves dependent mode and remains the most important thing you will show.
6. Poisoned rows are neutralized; trailer count matches line count. Flip `collision_action` to `fail` and show a clean abort naming the column, with no data in the log.
7. Memory ceiling test at 500,000 rows, asserted in CI so nobody reintroduces a materializing read.
8. **New.** `--compare-modes` produces identical checksums from both backends, with a timing table.
9. **New.** Rule conformance suite passes: every rule's Polars and T-SQL renderings agree across nulls, empties, boundaries, and non-ASCII input.
10. **New.** Kill the process mid-run, restart with `--resume`, and the completed datasets are skipped while the output remains correct and checksums match an uninterrupted run.

## Do not

- Do not use pandas, an ORM, `fetchall`, or `read_database` without `iter_batches`.
- Do not use `map_elements`, `apply`, or any per-row Python in the transform path. If a rule seems to need it, the rule does not belong in the catalog yet.
- Do not `eval` anything from metadata. Named registry entries only.
- Do not rely on Polars schema inference across batches.
- Do not open a file without an explicit encoding.
- Do not log row values, key values, or query parameter values.
- Do not connect to a real database or use real data. Synthetic only.
- Do not generate summary or plan markdown files beyond the two deliverables named below.

## Deliverables

1. Running prototype, `make setup` to `make demo` in under ten minutes on a clean Windows machine.
2. Seed scripts for synthetic source data and a working three-dataset metadata configuration covering all three modes.
3. Rule catalog with both renderings and a passing conformance suite.
4. Tests: unit coverage on rules, formatter, sanitizer, query builder, and view generator, plus the memory ceiling and resume tests.
5. `README.md` with the demo written out step by step so somebody else can present it.
6. `BENCHMARK.md`: the step-zero baseline, both execution modes at volume, with and without the antivirus exclusion, and a plain recommendation of which path to take to production.
7. `FINDINGS.md`, one page: what was confirmed, what was not covered, which assumptions turned out wrong, and the two governance items (PHI on laptop storage, database load from path B).

## Sizing

Roughly one and a half developer weeks, the extra half week being the second execution path and the conformance suite. That half week is the cheapest insurance you will buy on this project, because it converts the Polars versus SQL question from an architectural bet into a measurement.
