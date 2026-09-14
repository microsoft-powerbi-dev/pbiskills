# Extract Engine — developer guide

This is the human-facing setup/run/troubleshoot guide. `SKILL.md` and
`references/` are written for an agent working inside this codebase; this
file is written for a developer standing the thing up on their own machine
for the first time. Everything in it was run and verified on 2026-09-09
against a real SQL Server LocalDB instance — every command below, and every
line of sample output, is a real transcript, not a mock-up.

## Prerequisites

| Requirement | Verified version | Check with |
| --- | --- | --- |
| Python | 3.13.2 | `python --version` |
| `polars` | 1.44.1 | `python -m pip show polars` |
| `pyodbc` | 5.3.0 | `python -m pip show pyodbc` |
| `openpyxl` | 3.1.5 | `python -m pip show openpyxl` |
| `click` | 8.3.1 | `python -m pip show click` |
| A SQL Server ODBC driver | ODBC Driver 17 for SQL Server (18 also works) | `python -c "import pyodbc; print(pyodbc.drivers())"` |
| A reachable SQL Server | LocalDB instance `MSSQLLocalDB` | `sqllocaldb info MSSQLLocalDB` |
| Windows Integrated Auth | your own Windows session | no setup — the engine authenticates as whoever runs it, same as `sql-server-schema` |

`pytest` (9.0.2 verified) is needed only to run the test suite, not to run
the engine itself.

## Environment variables

The engine uses its own namespace, deliberately separate from
`sql-server-schema`'s `SQLSERVER_MCP_*` so the two skills' configuration
never bleeds together:

```bash
export EXTRACT_ENGINE_SERVER='(localdb)\MSSQLLocalDB'
export EXTRACT_ENGINE_DATABASE='ExtractEngineDev'
```

Both can also be passed explicitly per-command via `--server`/`--database`
where the CLI accepts them; the seed script takes them as flags directly.

## Running the CLI without installing a console script

There is no `pip install -e .` / `setup.py` for this skill (unlike
`skills/report-lineage/`), so `extract <command>` from `SKILL.md` is
shorthand. Run it for real as a module, from the repository root, using the
dotted path down to `cli.py` — Python's `-m` resolves directory names
literally, hyphens included, so this works despite `claude-skills` and
`extract-engine` not being valid Python identifiers:

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli --help
```

Every `extract <command>` in this document and in `SKILL.md` maps to
`python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli <command>`.

## 1. Run the offline test suite

This needs no database at all — it runs against `tests/fakes.py`'s
`FakeConnection`, which records every write it's given rather than executing
it.

```bash
python -m pytest skills/claude-skills/extract-engine/tests -q
```

**Actual output:**

```
........................................................................ [ 32%]
.....s.................................................................. [ 65%]
........................sssssssssssssssssssssssssssssssssssssssssssss... [ 97%]
.....                                                                    [100%]
175 passed, 46 skipped in 3.07s
```

The 46 skips are two test modules gated behind live SQL Server (see
`references/rule-catalog-and-conformance.md`), not failures:

```bash
# Rule conformance (Polars output vs. real T-SQL execution) — needs LocalDB
EXTRACT_ENGINE_LOCALDB=1 python -m pytest \
    skills/claude-skills/extract-engine/tests/test_rules_conformance.py -q

# Opts OUT of the 500,000-row RSS ceiling test instead, for a loaded CI box
EXTRACT_ENGINE_SKIP_RSS_TEST=1 python -m pytest \
    skills/claude-skills/extract-engine/tests/test_memory_ceiling.py -q
```

## 2. Stand up the sample feed against LocalDB

```bash
python skills/claude-skills/extract-engine/scripts/seed/seed_three_dataset_feed.py \
    --server "(localdb)\MSSQLLocalDB" --database ExtractEngineDev
```

**Actual output:**

```
extract-engine meta schema ready.
Deploying meta/src/gen schemas...
Seeding synthetic source data...
Synthetic src.* data seeded.
Loading the sample workbook into meta.*...
Loaded feed_config_version_id=2. Ready: extract dry-run --feed ClaimsExtract
```

This is idempotent (every `CREATE` in `seed_schema.sql` is
`IF OBJECT_ID(...) IS NULL` guarded) — rerunning it against an existing
`ExtractEngineDev` database is safe and just confirms everything is already
in place. It creates three schemas (`src`, `meta`, `gen`), seeds synthetic
`src.claim`/`src.member`/`src.claim_calc`/`src.status_lookup` rows, and loads
`examples/sample-workbook.xlsx` into `meta.*` as feed `ClaimsExtract` (three
datasets: `Claim` primary, `Member` dependent, `StatusLookup` reference).

### Known environment gotcha: `output_root` is hardcoded to `D:\Extracts\...`

`scripts/seed/build_sample_workbook.py` hardcodes
`output_root = "D:\\Extracts\\ClaimsExtract"` in the `Feed` sheet it
generates. On a machine with no `D:` drive, `extract run` fails with
`FileNotFoundError: The system cannot find the path specified: 'D:\\'`
partway through writing output files — **after** it has already inserted a
`running` row into `meta.run_log`, which then blocks every subsequent run
attempt (see the next section).

There is no CLI flag to override `output_root` per run — it comes only from
`meta.feed.output_root`, set by `config_loader.upsert()` from the workbook's
`Feed` sheet (see `metadata.py`'s `Feed.output_root` and `cli.run_cmd`'s
`output_dir`). To point it
somewhere that exists on your machine, either:

- edit `output_root` in the `Feed` sheet of a copy of
  `examples/sample-workbook.xlsx` and reload with
  `extract load-config --workbook <path> --feed ClaimsExtract --allow-config-write`, or
- issue the guardrail-permitted single-column update directly (this is the
  exact `UPDATE ... WHERE <col> = ?` shape `guardrails.classify_write_statement`
  allows on `meta.feed`):

  ```python
  import sys
  sys.path.insert(0, "skills/claude-skills/extract-engine/scripts")
  from extract_engine import connection as conn_mod
  with conn_mod.write_connection() as conn:
      conn.cursor().execute(
          "UPDATE meta.feed SET output_root = ? WHERE feed_name = ?",
          r"C:\some\writable\path\ClaimsExtract", "ClaimsExtract",
      )
  ```

## 3. Preview a run: `dry-run`

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli dry-run --feed ClaimsExtract
```

**Actual output** (the real generated query per dataset mode — no database
write happens for this command):

```
=== Claim (primary) ===
SELECT [t].[claim_id] AS [claim_id], [t].[member_id] AS [member_id], [t].[service_date] AS [service_date], [calc].[amount] AS [amount], [t].[status_code] AS [status_code], [t].[notes] AS [notes] FROM [src].[claim] AS [t] LEFT JOIN [src].[claim_calc] AS [calc] ON [calc].[claim_id] = [t].[claim_id] WHERE [t].[service_date] BETWEEN ? AND ? ORDER BY [t].[claim_id]

=== Member (dependent) ===
SELECT [t].[member_id] AS [member_id], [t].[member_name] AS [member_name] FROM [src].[member] AS [t]  WHERE [t].[member_id] IN (SELECT key_value FROM meta.run_dataset_key WHERE run_id = ? AND dataset_id = ?)

=== StatusLookup (reference) ===
SELECT [t].[status_code] AS [status_code], [t].[description] AS [description] FROM [src].[status_lookup] AS [t]  WHERE 1 = 1
```

Note the three distinct `WHERE`-clause shapes: a date-range window for
`primary`, a subquery against staged keys for `dependent`, and `1 = 1` (no
filter) for `reference` — see
`references/dataset-modes-and-metadata-schema.md`.

## 4. Run it for real: Polars backend

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli run --feed ClaimsExtract --mode polars
```

**Actual output:**

```
Claim: 1 rows, 1 part(s)
Member: 1 rows, 1 part(s)
StatusLookup: 2 rows, 1 part(s)
Run 5 complete in 536 ms.
```

**Actual generated files**, pipe-delimited with a trailer row
(`TRAILER|<row_count>`):

`Claim_part001.txt`:
```
C-IN-WINDOW|100|2026-06-15|200.00|OPEN|N/A|1
TRAILER|1
```

`Member_part001.txt`:
```
100|Alice Old-Timer
TRAILER|1
```

`StatusLookup_part001.txt`:
```
OPEN|Open
SHUT|Shut
TRAILER|2
```

This proves the design's own acceptance check live: member `100`
("Alice Old-Timer") was created decades before the current extraction
window, but is correctly **included** in `Member_part001.txt` because an
in-window `Claim` row references her (`dependent`-mode key pull-in, not a
date filter on `Member` itself — see
`references/dataset-modes-and-metadata-schema.md`'s "three dataset modes"
section).

`meta.run_detail` after this run — one row per dataset, each with a running
SHA-256 checksum over that dataset's output bytes:

```
run_id=5 dataset_id=1 status=completed row_count=1 checksum=0749edb69ee8c0346c2c28a22d26cb0138d2665cfdb63724a7c762828942d770
run_id=5 dataset_id=2 status=completed row_count=1 checksum=c665928cb9d099393ab102bcc96d43e69bfcf2afbfb3c95886c96b523b8d8b5b
run_id=5 dataset_id=3 status=completed row_count=2 checksum=48a73008c19c26f7ce7117f2e719306a35378c0d8ba1f2d7f4559479f12617b3
```

## 5. Exercise the SQL backend and prove byte-identical output

```bash
# Hand a DBA the generated view DDL before deploying anything
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli \
    explain --feed ClaimsExtract --dataset Claim --mode sql
```

**Actual output** (formatted for readability — the CLI prints it as one
line):

```sql
CREATE OR ALTER VIEW gen.v_Claim AS
SELECT
    REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM([t].[claim_id])), '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ') AS [ClaimNumber],
    REPLACE(REPLACE(REPLACE(REPLACE([t].[member_id], '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ')             AS [MemberId],
    REPLACE(REPLACE(REPLACE(REPLACE(CONVERT(VARCHAR(32), [t].[service_date], 23), '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ') AS [ServiceDate],
    REPLACE(REPLACE(REPLACE(REPLACE(FORMAT([calc].[amount], 'N2'), '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ') AS [PaidAmount],
    REPLACE(REPLACE(REPLACE(REPLACE(UPPER([t].[status_code]), '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ')    AS [StatusCode],
    REPLACE(REPLACE(REPLACE(REPLACE(ISNULL([t].[notes], 'N/A'), '|', ' '), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ')  AS [Notes],
    ROW_NUMBER() OVER (ORDER BY [t].[claim_id])                                                                            AS [SurrogateKey],
    [t].[service_date]  AS [__window_key],
    [t].[member_id]     AS [__key_source_member_id]
FROM [src].[claim] AS [t]
LEFT JOIN [src].[claim_calc] AS [calc] ON [calc].[claim_id] = [t].[claim_id]
```

Every value column is wrapped in the same four-deep `REPLACE` chain that
strips `|`/CR/LF/TAB (delimiter-collision sanitization, baked into the view
itself), and the view exposes two reserved columns
(`__window_key`, `__key_source_member_id`) that carry no meaning to a
consumer but exist purely so the client's read query can filter — the view
itself has no `WHERE` clause, ever (see `references/execution-engines.md`).

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli \
    deploy-views --feed ClaimsExtract --i-understand-this-writes-to-the-database
```

**Actual output:**

```
gen.v_Claim: DEPLOYED
gen.v_Member: DEPLOYED
gen.v_StatusLookup: DEPLOYED
```

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli \
    run --feed ClaimsExtract --mode sql
```

**Actual output:**

```
Claim: 1 rows, 1 part(s)
Member: 1 rows, 1 part(s)
StatusLookup: 2 rows, 1 part(s)
Run 6 complete in 814 ms.
```

**Verified live:** the SHA-256 checksums recorded in `meta.run_detail` for
run 6 (SQL backend) are **identical, byte for byte, to run 5's** (Polars
backend) for every one of the three datasets:

| Dataset | Polars run 5 checksum | SQL run 6 checksum | Match |
| --- | --- | --- | --- |
| Claim | `0749edb6...942d770` | `0749edb6...942d770` | ✅ |
| Member | `c665928c...23b8d8b5b` | `c665928c...23b8d8b5b` | ✅ |
| StatusLookup | `48a73008...9479f12617b3` | `48a73008...9479f12617b3` | ✅ |

This is the dual-backend equivalence the design calls for
(`--compare-modes` was meant to assert this mechanically but isn't
implemented — see `SKILL.md`'s "what actually exists here" section); running
both backends manually and diffing `meta.run_detail.checksum` is the
practical substitute today.

## 6. Concurrent-run guard and stale `running` rows

`checkpoint.is_concurrent_run_active` refuses a new `extract run` while any
`meta.run_log` row for that feed has `status = 'running'`. There is **no
automatic staleness timeout** — if a process dies (crash, killed, laptop
sleep) between `start_run()` inserting that row and `complete_run()` closing
it out, the row stays `running` forever and blocks every future run for that
feed until someone resolves it:

```
Error: A run is already in progress for feed 'ClaimsExtract'. Refusing to start a second one.
```

This happened live twice while producing this document — once from a stale
row left over from an earlier session, once from the `D:\Extracts` crash
above. Both were the exact same shape: a `running` row inserted, then the
process exiting before it could complete.

**Recovery**, using the engine's own bookkeeping function (a
guardrail-permitted `UPDATE meta.run_log SET status = ? WHERE run_id = ?` —
not a drop or delete of anything):

```python
import sys
sys.path.insert(0, "skills/claude-skills/extract-engine/scripts")
from extract_engine import connection as conn_mod, checkpoint

with conn_mod.write_connection() as conn:
    checkpoint.complete_run(
        conn, run_id, duration_ms=0, peak_rss_bytes=0,
        status="failed", error_message="stale row, manually resolved",
    )
```

After that, `extract run --resume --run-id <that id>` would reprocess every
dataset from scratch (checkpoint granularity is per-dataset, not
per-file-part — see `references/checkpoint-and-resume.md`), or a fresh
`extract run` starts a new run row entirely.

**If you are automating this**: treat "another run is already in progress"
as a signal to inspect `meta.run_log` before assuming corruption, and treat
marking a row `failed` as a normal, sanctioned operational action, not a
destructive one — it is bookkeeping metadata, not customer data or a schema
object.

## 7. Module-name collision when scripting against this package directly

If you write a one-off script (like the recovery snippet above) rather than
going through `cli.py`, import the way `cli.py` itself does — add
`scripts/` (the **parent** of the `extract_engine` package) to `sys.path`,
then `from extract_engine import connection as conn_mod`:

```python
sys.path.insert(0, "skills/claude-skills/extract-engine/scripts")
from extract_engine import connection as conn_mod
```

**Do not** add `scripts/extract_engine/` itself to `sys.path` and
`import connection` bare — `extract_engine/connection.py` internally does
`from connection import (...)` to reuse `sql-server-schema`'s pure
connection-string logic, and if `extract_engine/`'s own directory is on
`sys.path` ahead of `sql-server-schema/scripts/`, that inner import resolves
to itself and raises `ImportError: cannot import name 'ConnectionError_'
from partially initialized module 'connection' (most likely due to a
circular import)`. This is the same `mcp`-package-name-collision class of
bug documented in `mcp/README.md`, one level down.

## Where to look next

| Question | Read |
| --- | --- |
| Full step-by-step execution, setup, connection, mapping sheet, file formats, large-dataset batch runs | `RUNBOOK.md` |
| What does each CLI command actually do? | `SKILL.md`, "The CLI" section |
| What's the Excel mapping format? | `references/dataset-modes-and-metadata-schema.md` |
| What transform rules exist and how do Polars/SQL stay in sync? | `references/rule-catalog-and-conformance.md` |
| Why two execution backends, and what's the internal filter-column trick? | `references/execution-engines.md` |
| What exactly is allowed to write to the database? | `references/write-guardrails.md` |
| Exact resume semantics | `references/checkpoint-and-resume.md` |
| The original design brief vs. what got built | `docs/extract-engine-mvp-prompt-v2-polars.md`, then `SKILL.md`'s "what actually exists here" section |
