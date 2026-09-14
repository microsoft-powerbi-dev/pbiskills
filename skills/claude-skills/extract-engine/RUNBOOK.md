# Extract Engine — developer runbook

Step-by-step guide to standing up and executing the SQL extractor: setup,
the Windows-authentication connection, the mapping workbook, the source
queries it generates, the output file format, and how to run it against
large datasets.

This is the **operational** companion to the two other documents in this
folder — read them for different reasons:

| Document | Read it for |
| --- | --- |
| `RUNBOOK.md` (this file) | Executing the extractor end to end, and every knob you can turn |
| `DEVELOPER.md` | A short verified first-run transcript and two environment gotchas |
| `SKILL.md` + `references/` | Design rationale, agent-facing internals, and what the design doc specifies but never got built |

Every command, output, and defect in this document was executed on
**2026-09-14** against SQL Server LocalDB (`MSSQLLocalDB`, database
`ExtractEngineDev`) with ODBC Driver 17, Python 3.13, polars 1.44.1,
pyodbc 5.3.0. Nothing below is illustrative — see
[Verification log](#verification-log) for what was run.

Writing this guide surfaced six bugs. All six are now **fixed**, with the
reproduction and the post-fix evidence recorded in
[§10](#10-defects-found-and-fixed); this document describes the engine's
behaviour after those fixes.

---

## Contents

1. [How the extractor works](#1-how-the-extractor-works)
2. [Setup](#2-setup)
3. [The SQL connection (trusted connection / Windows credentials)](#3-the-sql-connection-trusted-connection--windows-credentials)
4. [The mapping workbook](#4-the-mapping-workbook)
5. [The source query the engine generates](#5-the-source-query-the-engine-generates)
6. [Output file format](#6-output-file-format)
7. [Batch runs for large datasets](#7-batch-runs-for-large-datasets)
8. [Full step-by-step execution](#8-full-step-by-step-execution)
9. [Troubleshooting](#9-troubleshooting)
10. [Defects found and fixed](#10-defects-found-and-fixed)
11. [Verification log](#verification-log)

---

## 1. How the extractor works

```
  mapping workbook (.xlsx)      meta.* tables              flat files
  ┌───────────────────┐        ┌──────────────┐        ┌────────────────┐
  │ Feed              │        │ meta.feed    │        │ Claim_part001  │
  │ Datasets          │  load  │ meta.dataset │  run   │ Member_part001 │
  │ Lookups           │ ─────► │ meta.field_  │ ─────► │ Status_part001 │
  │ FieldMap          │ config │   map        │        └────────────────┘
  │ KeyGeneration     │        │ meta.dataset_│              ▲
  │ ExtractParameters │        │   lookup     │              │
  │ OutputLayout      │        └──────────────┘         SQL Server src.*
  └───────────────────┘               │                       │
                                      └── generates ──────────┘
                                          the source query
```

The contract is: **Excel authors, metadata executes.** Neither execution
backend ever reads the workbook. `load-config` is the only bridge from Excel
into `meta.*`; `run` reads `meta.*` only. A mapping change is therefore an
Excel edit plus a reload plus a rerun — never a code edit or a deploy.

Two interchangeable backends produce byte-identical output:

- **`--mode polars`** — the source query returns raw columns; every transform
  runs in the client process as a vectorized Polars expression.
- **`--mode sql`** — a deploy-once view `gen.v_<dataset>` bakes every
  transform into its `SELECT`; the client reads the view and does no
  per-value work.

Pick with `--mode`, or fall back to `meta.feed.default_execution_mode`.

---

## 2. Setup

### 2.1 Prerequisites

```bash
python --version                                   # 3.13.2 verified
python -m pip show polars pyodbc openpyxl click psutil
python -c "import pyodbc; print(pyodbc.drivers())" # needs a SQL Server driver
```

| Requirement | Verified | Notes |
| --- | --- | --- |
| Python | 3.13.2 | |
| `polars` | 1.44.1 | Path A transform engine |
| `pyodbc` | 5.3.0 | |
| `openpyxl` | 3.1.5 | Reads the mapping workbook |
| `click` | 8.3.1 | CLI |
| `psutil` | any | `run` records `peak_rss_bytes`; import fails without it |
| ODBC driver | ODBC Driver 17 for SQL Server | 18 also works; 11/13/13.1 and Native Client are accepted fallbacks |
| `sqlcmd` | any | Only the schema seeder shells out to it |
| SQL Server | LocalDB `MSSQLLocalDB` | Any reachable instance works |

`pytest` is needed only for the test suite.

### 2.2 There is no installed `extract` command

This skill has no `setup.py` or console-script entry point, so the `extract
<command>` shorthand used in `SKILL.md` and below is **not** a real binary.
Run it as a module from the repository root:

```bash
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli --help
```

`python -m` resolves directory names literally, so the hyphens in
`claude-skills` and `extract-engine` are fine. A convenience alias:

```bash
# bash
alias extract='python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli'
```
```powershell
# PowerShell
function extract { python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli @args }
```

Every `extract <command>` below assumes one of those.

### 2.3 Run the offline test suite first

No database required — this runs against `tests/fakes.py`'s `FakeConnection`.

```bash
python -m pytest skills/claude-skills/extract-engine/tests -q
# 193 passed, 46 skipped
```

The 46 skips are the two live-SQL-gated modules, not failures:

```bash
EXTRACT_ENGINE_LOCALDB=1 python -m pytest \
    skills/claude-skills/extract-engine/tests/test_rules_conformance.py -q
```

### 2.4 Seed a development database

One command deploys the schemas, builds the sample workbook, seeds synthetic
source rows, and loads the workbook into `meta.*`:

```bash
python skills/claude-skills/extract-engine/scripts/seed/seed_three_dataset_feed.py \
    --server "(localdb)\MSSQLLocalDB" --database ExtractEngineDev
```

It creates three **new** schemas and nothing else: `src` (synthetic source
data), `meta` (the catalog), `gen` (generated views). Every `CREATE` is
`IF OBJECT_ID(...) IS NULL` guarded, so rerunning is safe and idempotent.

> Run this against a database you control. It is safe to rerun, but point it
> at a scratch database, not one that already holds objects you care about.

After seeding, set `output_root` to a path that exists on your machine — the
committed workbook hardcodes `D:\Extracts\ClaimsExtract`, and `run` will fail
partway through writing files if there is no `D:` drive:

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

There is no CLI flag for `output_root`; it comes only from `meta.feed`. The
`UPDATE ... WHERE <col> = ?` shape above is exactly what the write guardrail
permits (see §3.5).

### 2.5 Production setup checklist

| Step | What to do |
| --- | --- |
| 1 | Create a database for the `meta` catalog. It can be the source database or a separate one, but `meta.*` and the source tables must be reachable on the **same connection** — `run` uses one connection per dataset for both the catalog read and the source read. |
| 2 | Deploy `scripts/seed/seed_schema.sql` (only the `meta` and `gen` sections matter in production; `src` is dev-only synthetic data). |
| 3 | Grant the run account `SELECT` on the source tables, and `SELECT/INSERT/UPDATE/DELETE` on `meta.*`. For `--mode sql`, also `CREATE VIEW` plus `ALTER` on the `gen` schema. |
| 4 | Set `EXTRACT_ENGINE_SERVER` / `EXTRACT_ENGINE_DATABASE` for the account that will run it (§3.1). |
| 5 | Author the mapping workbook (§4) and `load-config --allow-config-write`. |
| 6 | Set the output-format columns on the workbook's `OutputLayout` sheet (`line_ending`, `null_sentinel`, `max_rows_per_file`, the `emit_*` flags) and `default_execution_mode` on `Feed`. These are all persisted by `load-config` (§4.8). |
| 7 | `validate-config`, then `dry-run`, then a real `run`. |

---

## 3. The SQL connection (trusted connection / Windows credentials)

### 3.1 Authentication is Windows Integrated Auth, and only that

The engine authenticates as **whoever launched the process**, using their
existing Kerberos or NTLM credentials. There is deliberately no `password`
parameter, no password environment variable, and no secrets file anywhere in
the skill. `connection.assert_no_credentials` enforces this in code: it runs
on every connection string built, and raises if the string contains `UID`,
`User Id`, `PWD`, or `Password`. A future edit that tries to add SQL
authentication fails a test rather than shipping.

Configure the target with two environment variables — this engine's own
namespace, kept separate from the `sql-server-schema` skill's
`SQLSERVER_MCP_*` so the two never bleed into each other:

```bash
export EXTRACT_ENGINE_SERVER='(localdb)\MSSQLLocalDB'
export EXTRACT_ENGINE_DATABASE='ExtractEngineDev'
```
```powershell
$env:EXTRACT_ENGINE_SERVER = '(localdb)\MSSQLLocalDB'
$env:EXTRACT_ENGINE_DATABASE = 'ExtractEngineDev'
```

`EXTRACT_ENGINE_DATABASE` falls back to `master` if unset — always set it
explicitly. The seed scripts also accept `--server` / `--database` flags; the
CLI commands do not, they read the environment only.

### 3.2 Server address forms

`normalize_target` parses four forms:

| Form | Example |
| --- | --- |
| Host | `SQLPROD01` |
| Host + named instance | `SQLPROD01\INST2` |
| Host + port | `SQLPROD01,1433` |
| `tcp:` prefixed | `tcp:SQLPROD01,1433` |

A named instance and an explicit port are **mutually exclusive** and supplying
both is rejected: a named instance is resolved to its port through the SQL
Browser service, so giving both is a contradiction, not belt-and-braces. Use a
comma before a port, never a colon.

### 3.3 The connection string it builds

Verified output for `(localdb)\MSSQLLocalDB` / `ExtractEngineDev` on a machine
with driver 17 (server and database hashed by `redact_connection_string`,
which is what gets logged):

```
DRIVER={ODBC Driver 17 for SQL Server};SERVER=<hashed:b4423cdc9e24>;
DATABASE=<hashed:6a99edbc4c80>;Trusted_Connection=yes;Timeout=10;
APP=sqlserver-schema-mcp
```

- `Trusted_Connection=yes` — always emitted. This is the whole point.
- `APP=` — lets a DBA find and, if necessary, kill these sessions by
  `program_name` in `sys.dm_exec_sessions`.
- `Timeout=` — the **login** timeout. The query timeout is separate, set on
  the connection object.
- Server and database names are hashed in every log line and error message, so
  a stack trace pasted into a ticket leaks a hash, not an internal hostname.

Driver selection is best-first and never hardcoded, so a machine with 17 but
not 18 (or the reverse) both work:

```
ODBC Driver 18 → 17 → 13.1 → 13 → 11 → SQL Server Native Client 11.0 → SQL Server
```

### 3.4 Encryption modes

Set `SQLSERVER_MCP_ENCRYPT` (the shared connection module's variable, reused
verbatim here):

| Mode | Driver 18+ | Driver 17 and older | When |
| --- | --- | --- | --- |
| `auto` (default) | `Encrypt=yes;TrustServerCertificate=yes` | emits nothing | On-prem server with a self-signed certificate — matches what SSMS and sqlcmd do |
| `strict` | `Encrypt=yes;TrustServerCertificate=no` | same | Once a CA-issued certificate is installed on the SQL host. **This is the target state.** |
| `off` | `Encrypt=no;TrustServerCertificate=yes` | same | Legacy servers that cannot negotiate TLS |

Driver 18 flipped the default to `Encrypt=yes` *with* certificate validation,
which is what breaks a typical on-prem server using a self-signed certificate.
`auto` keeps the encryption and relaxes the chain check.

Other tunables, all read from the shared namespace:

| Variable | Default | Effect |
| --- | --- | --- |
| `SQLSERVER_MCP_DRIVER` | best installed | Force one driver; errors if it is not installed |
| `SQLSERVER_MCP_ENCRYPT` | `auto` | Above |
| `SQLSERVER_MCP_LOGIN_TIMEOUT` | 10 s | Connection-string `Timeout=` |
| `SQLSERVER_MCP_QUERY_TIMEOUT` | 30 s | Per-statement timeout. **Raise this for large datasets** (§7.7) |

### 3.5 Two connection contexts, and the write allow-list

The engine opens connections through exactly two functions, named differently
so they can never be confused at a call site:

| Function | Transaction contract | Used for |
| --- | --- | --- |
| `read_connection()` | **Rolled back unconditionally** on exit | Every `meta.*` read, every source-data read, the lookup-table existence check |
| `write_connection()` | Commits on clean exit, rolls back on exception | Only the narrow permitted writes below |

Every write is additionally classified by
`guardrails.classify_write_statement(sql)` before it is sent. The allow-list is
enumerated, not a general "anything but DROP" rule:

- Parameterized `INSERT` into a config table (`meta.feed`, `meta.dataset`,
  `meta.field_map`, `meta.dataset_lookup`, `meta.feed_config_version`) or a
  bookkeeping table (`meta.run_log`, `meta.run_detail`,
  `meta.run_dataset_key`).
- `UPDATE <table> SET ...` on those same tables. Note that the classifier
  constrains the table but **not** the `WHERE` clause here, so a compound
  `WHERE` is accepted (`mark_dataset_complete` relies on that) — and so, as
  written, is an `UPDATE` with no `WHERE` at all. Treat the single-equality
  wording in `SKILL.md` as describing intent rather than what the regex
  enforces.
- `DELETE FROM ... WHERE <col> = ?` — single equality only, and here it
  genuinely is enforced: a bare `DELETE` or a compound `WHERE` is refused
  unconditionally, whatever table it names.
- `CREATE OR ALTER VIEW gen.<name> AS SELECT ...`.

`DROP`, `TRUNCATE`, `EXEC`/`EXECUTE`, `xp_`/`sp_` procedures, `BACKUP`,
`RESTORE`, `SHUTDOWN`, `RECONFIGURE`, `GRANT`/`REVOKE`/`DENY`, and any other
`ALTER` are refused unconditionally. Only one statement per call is permitted,
with one carve-out: the fixed
`; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS id` suffix that
`execute_insert_return_identity` appends is recognized as part of one logical
INSERT rather than a stacked statement.

### 3.6 Verifying the connection

```bash
python - <<'PY'
import sys
sys.path.insert(0, "skills/claude-skills/extract-engine/scripts")
from extract_engine import connection as c
print("drivers:", c.available_drivers())
print("string :", c.redact_connection_string(
    c.build_connection_string(r"(localdb)\MSSQLLocalDB", "ExtractEngineDev")))
with c.read_connection() as conn:          # rolled back on exit
    cur = conn.cursor()
    cur.execute("SELECT SUSER_SNAME(), DB_NAME(), @@VERSION")
    print(cur.fetchone())
PY
```

`explain_odbc_error` rewrites the common failures into actionable messages, so
read the raised error before reaching for a network trace.

---

## 4. The mapping workbook

One workbook defines exactly **one feed**. Sheet names are case-sensitive.
`examples/sample-workbook.xlsx` is the committed, entirely fictional reference
copy; regenerate it with:

```bash
python skills/claude-skills/extract-engine/scripts/seed/build_sample_workbook.py
```

| Sheet | Required | Rows | Lands in |
| --- | --- | --- | --- |
| `Feed` | yes | exactly 1 | `meta.feed` |
| `Datasets` | yes | 1 per dataset | `meta.dataset` |
| `FieldMap` | yes | 1 per output column | `meta.field_map` |
| `Lookups` | no | 1 per join | `meta.dataset_lookup` |
| `KeyGeneration` | no | 1 per generated key | merged into `FieldMap` before validation |
| `ExtractParameters` | no | 1 | `meta.feed` defaults — **but see §4.8** |
| `OutputLayout` | no | 1 | `meta.feed` layout flags — **but see §4.8** |

Row 1 of every sheet is the header; column order does not matter, names do.
Blank rows are skipped. Unknown columns are ignored silently.

### 4.1 `Feed`

| Column | Required | Persisted | Notes |
| --- | --- | --- | --- |
| `feed_name` | yes | yes | Must match `--feed` on the CLI or `load-config` refuses |
| `source_server` | yes | yes | Recorded for audit; the live connection comes from `EXTRACT_ENGINE_SERVER` |
| `source_database` | yes | yes | Same |
| `output_root` | yes | yes | Directory for output files; created if missing |
| `delimiter` | no (`\|`) | yes | Single character |
| `collision_action` | no (`sanitize`) | yes | `sanitize` or `fail` |
| `collision_char` | no (space) | yes | Must differ from `delimiter` — checked in validation *and* by a DB `CHECK` constraint |
| `default_execution_mode` | no | **no** | Present in the sample workbook but never written — §4.8 |

Sample row:

| feed_name | source_server | source_database | delimiter | collision_action | collision_char | output_root |
| --- | --- | --- | --- | --- | --- | --- |
| ClaimsExtract | SQLPROD01 | ClaimsDW | `\|` | sanitize | (space) | `D:\Extracts\ClaimsExtract` |

### 4.2 `Datasets`

| Column | Required for | Notes |
| --- | --- | --- |
| `dataset_name` | all | Unique within the feed |
| `source_schema`, `source_table` | all | |
| `dataset_mode` | all | `primary` \| `dependent` \| `reference` |
| `primary_key_columns` | all | Comma-separated source column names |
| `window_date_column` | `primary`; `dependent` with `include_own_window` | The date column the extraction window filters on |
| `depends_on_dataset` | `dependent` | A `dataset_name` in this same workbook; resolved to `depends_on_dataset_id` on load |
| `dependency_source_column` | `dependent` | The FK column **on the parent dataset's table** whose values get staged |
| `dependency_target_column` | `dependent` | The matching column **on this dataset's own table** |
| `include_own_window` | no (0) | `1` = also emit rows inside this dataset's own window, OR'd with the key set |
| `output_file_stem` | no (`dataset_name`) | Output filename prefix |
| `run_ordinal` | no (1) | Execution order. **A dependent dataset must have a higher `run_ordinal` than its parent** |

The three modes are the heart of the design:

- **`primary`** — windowed on a date. `WHERE <window_date_column> BETWEEN ? AND ?`
- **`dependent`** — no date filter of its own. It emits exactly the rows the
  parent dataset referenced, however old they are. This is what prevents
  orphaned references in the output.
- **`reference`** — no filter. Ships in full every run.

The seeded sample proves dependent-mode pull-in: member `100` was created in
1999 but is referenced by an in-window claim, so it **must** appear in the
Member file; member `200` is recent but referenced only by an out-of-window
claim, so it must **not**. Row counts and creation dates would both suggest
the opposite.

Validation rejects a dataset that depends on itself, a `depends_on_dataset`
naming no row in the workbook, and any dependency cycle.

### 4.3 `Lookups` — "calculator table" joins

| Column | Notes |
| --- | --- |
| `dataset_name` | Which dataset this join belongs to |
| `lookup_alias` | Alias used in `FieldMap.source_expression` and in the generated SQL |
| `lookup_schema`, `lookup_table` | |
| `join_source_column` | Column on the dataset's base table |
| `join_lookup_column` | Column on the lookup table |
| `join_type` | `left` (default) or `inner` |

This is the one validation that touches the database: `load-config` calls
`SELECT OBJECT_ID(?)` to confirm each lookup table actually exists. Every other
check is offline.

### 4.4 `FieldMap` — one row per output column

| Column | Required | Notes |
| --- | --- | --- |
| `dataset_name` | yes | Must match a `Datasets` row |
| `ordinal` | yes | **This, and only this, defines output column order.** Unique per dataset |
| `target_column` | yes | Output column name. Unique per dataset; must not contain the delimiter, CR, LF, or TAB |
| `source_expression` | yes | A bare column name, or `lookup:<alias>.<column>` |
| `data_type` | yes | Neutral token — see §4.6. Drives the pinned read schema |
| `rule_name` | no (`passthrough`) | Must exist in the registry — §4.5 |
| `rule_params` | depends on rule | A JSON **object** string, e.g. `{"width": 10, "char": "0"}` |
| `nullable` | no (1) | Recorded; not currently enforced at write time |
| `default_value` | no | Applied **after** the rule, as `fill_null` / `ISNULL` |

### 4.5 The rule catalog

Ten registered rules. `rule_name` is resolved by **exact string match** against
an in-process registry and is never `eval`'d — an unrecognized name is a
validation failure before any database write happens. Each rule carries two
renderings that must produce identical output.

| `rule_name` | Required `rule_params` | T-SQL rendering |
| --- | --- | --- |
| `passthrough` | — | `{col}` |
| `trim` | — | `LTRIM(RTRIM({col}))` |
| `upper` | — | `UPPER({col})` |
| `pad_left` | `width` (int), `char` (str) | `RIGHT(REPLICATE('{char}',{width}) + CAST({col} AS VARCHAR({width})),{width})` |
| `truncate` | `length` (int) | `LEFT({col},{length})` |
| `date_format` | `style` (int), `polars_format` (str) | `CONVERT(VARCHAR(32),{col},{style})` |
| `decimal_format` | `scale` (int), `mask` (str) | `FORMAT({col},'{mask}')` |
| `code_lookup` | `mapping` (object), `default` (str) | `CASE WHEN … THEN … ELSE … END` |
| `default_if_null` | `value` (str) | `ISNULL({col},'{value}')` |
| `row_sequence` | `order_by` (str) | `ROW_NUMBER() OVER (ORDER BY {order_by})` |

Three of these have subtleties worth knowing before you author them:

- **`date_format`** needs *both* params, and they must agree: `style` is the
  T-SQL `CONVERT` style (23 = `yyyy-mm-dd`) and `polars_format` is the
  strftime equivalent (`%Y-%m-%d`). Nothing cross-checks them — set one
  without matching the other and the two backends diverge silently.
  `CONVERT` is used rather than `FORMAT` because `FORMAT` is CLR-backed and
  slow at volume.
- **`decimal_format`** rounds half-away-from-zero (T-SQL "commercial
  rounding"), not Polars' default banker's rounding. For this to be
  reproducible the source column must be read as a decimal, which is why its
  `data_type` must be `decimal(p,s)` and not a float token.
- **`pad_left`** slices to `width` before padding, mirroring T-SQL's
  `CAST(col AS VARCHAR(width))` left-truncation of an over-width value. An
  over-width value is truncated, not an error.

### 4.6 `data_type` tokens

The token drives `schema_overrides` on every read — schema is always pinned,
never inferred, so a batch where a nullable column happens to be entirely null
cannot silently change dtype mid-run.

Verified behaviour of each token, against both the loader's validator and the
Polars dtype mapping:

| Token | Loader accepts | Polars dtype |
| --- | --- | --- |
| `int` | yes | `Int32` |
| `bigint` | yes | `Int64` |
| `bit` | yes | `Boolean` |
| `date` | yes | `Date` |
| `datetime`, `datetime2` | yes | `Datetime` |
| `varchar(n)`, `nvarchar(n)` | yes | `String` |
| `varchar(MAX)`, `nvarchar(MAX)` | yes | `String` |
| `char(n)`, `nchar(n)` | yes | `String` |
| `decimal(p,s)` | yes | `Decimal(p,s)` |
| `float`, `varchar` (no length), anything else | no | unsupported |

The validator and the dtype mapping are held in lockstep by a test that walks
every token in the accepted set and asserts both sides agree — a token one
side admits and the other cannot map is a config write that commits and then
fails when the read plan is built. `nvarchar(n)`, the `MAX` lengths, and
`nchar(n)` were all broken in one direction or the other before §10.2/§10.3.

### 4.7 `KeyGeneration`, `ExtractParameters`, `OutputLayout`

`KeyGeneration` is an authoring convenience only: its rows are appended to the
`FieldMap` rows before validation, using the same column set. Use it for
`row_sequence` surrogate keys so they do not clutter the real field map. The
sample workbook generates `SurrogateKey` at `ordinal` 7 with
`{"order_by": "claim_id"}`.

`row_sequence` needs an `order_by` because SQL Server guarantees no row order
without an explicit `ORDER BY`. The value is auto-qualified against the base
table unless it already contains a dot — necessary because an unqualified name
that also exists on a joined lookup table raises "Ambiguous column name".

`ExtractParameters` (`anchor_date_default`, `window_years_default`) and
`OutputLayout` (`emit_header_row`, `emit_trailer_row`, `emit_concat_ws_line`)
are parsed and available to validation, but are **not written to the
database** — see next.

### 4.8 The optional feed settings, and what a blank cell means

`config_loader.upsert()` always writes `feed_name`, `source_server`,
`source_database`, `delimiter`, `collision_action`, `collision_char` and
`output_root`. On top of those it writes whichever of these the workbook
supplied — all of them were silently dropped before §10.4:

| Column | Sheet | Accepted values | DDL default |
| --- | --- | --- | --- |
| `collision_action` | `Feed` | `sanitize` / `fail` | `sanitize` |
| `default_execution_mode` | `Feed` | `polars` / `sql` | `polars` |
| `line_ending` | `OutputLayout` | `CRLF` / `LF` | `CRLF` |
| `null_sentinel` | `OutputLayout` | any string | `''` (empty) |
| `max_rows_per_file` | `OutputLayout` | positive integer | `1000000` |
| `emit_header_row` | `OutputLayout` | `0`/`1` (or `true`/`false`/`yes`/`no`) | `0` |
| `emit_trailer_row` | `OutputLayout` | same | `1` |
| `emit_concat_ws_line` | `OutputLayout` | same | `0` |
| `anchor_date_default` | `ExtractParameters` | `YYYY-MM-DD` or a real Excel date | `NULL` |
| `window_years_default` | `ExtractParameters` | positive integer | `2` |

Three rules govern them:

- **A blank or absent cell means "leave what the database already has"** —
  its DDL default for a brand-new feed, its current value on a reload. An
  omitted optional sheet therefore never resets a setting an operator
  configured on purpose, and is never written as `NULL`.
- **Values are normalised** to what `meta.feed`'s `CHECK` constraints accept,
  so `lf`, `SQL` and `Fail` all load. Enum, integer and date values are
  parsed and range-checked during `validate()`, before any write, rather than
  surfacing later as a raw constraint violation mid-upsert.
- **`validate()` and `upsert()` share one coercion function**
  (`coerce_feed_settings`), so a value validation accepted is exactly the
  value that gets written.

Verified live — loading a workbook with `line_ending=LF`,
`max_rows_per_file=1`, `emit_header_row=1`, `default_execution_mode=sql`,
`anchor_date_default=2026-06-30`, `window_years_default=5` reads back:

```
meta.feed.line_ending              = 'LF'
meta.feed.max_rows_per_file        = 1
meta.feed.emit_header_row          = True
meta.feed.default_execution_mode   = 'sql'
meta.feed.anchor_date_default      = '2026-06-30'
meta.feed.window_years_default     = 5
```

`output_root` still has no CLI flag and no sheet other than `Feed`; it is the
one setting you will most often want to override per environment, and doing
it by hand is §2.4's `UPDATE`.

### 4.9 Loading the workbook

```bash
# Parse + validate only. No database write, even without --dry-run.
extract load-config --workbook path/to/mapping.xlsx --feed ClaimsExtract --dry-run

# Actually upsert into meta.*
extract load-config --workbook path/to/mapping.xlsx --feed ClaimsExtract --allow-config-write
```

- Validation always runs. `--allow-config-write` is the only thing that lets
  it write; without it the command validates and then refuses.
- The workbook's `Feed.feed_name` must equal `--feed`, or it refuses.
- `upsert()` is transactional and **idempotent by content**: reloading a
  workbook whose bytes hash to the same SHA-256 for the same feed is a no-op
  that returns the existing `feed_config_version_id`. Change one cell and you
  get a new version row; the old one is flipped to `is_current = 0`.
- `meta.field_map` and `meta.dataset_lookup` are deleted and fully reinserted
  **per dataset** (`WHERE dataset_id = ?`), never patched row by row. A field
  you deactivated by setting `is_active = 0` directly in the database is
  therefore reactivated by the next reload.
- Only the filename is recorded in `meta.feed_config_version`, never the full
  path, and `sheet_summary_json` records row counts per sheet — shape only,
  never values.

Validation failures print as `Sheet:row N:column — message`, all of them at
once:

```
FieldMap:row 4: rule_name 'uppercase' is not registered. Available: ['code_lookup', ...]
Datasets:row 3: mode 'primary' requires window_date_column
```

Re-check what is already loaded, with no Excel file involved:

```bash
extract validate-config --feed ClaimsExtract
# Feed 'ClaimsExtract': 3 dataset(s), no issues found.
```

---

## 5. The source query the engine generates

### 5.1 Preview it without touching the database

```bash
extract dry-run --feed ClaimsExtract
```

Real output for the seeded feed, one query per dataset in `run_ordinal` order:

```sql
=== Claim (primary) ===
SELECT [t].[claim_id] AS [claim_id], [t].[member_id] AS [member_id],
       [t].[service_date] AS [service_date], [calc].[amount] AS [amount],
       [t].[status_code] AS [status_code], [t].[notes] AS [notes]
FROM [src].[claim] AS [t]
LEFT JOIN [src].[claim_calc] AS [calc] ON [calc].[claim_id] = [t].[claim_id]
WHERE [t].[service_date] BETWEEN ? AND ?
ORDER BY [t].[claim_id]

=== Member (dependent) ===
SELECT [t].[member_id] AS [member_id], [t].[member_name] AS [member_name]
FROM [src].[member] AS [t]
WHERE [t].[member_id] IN (SELECT key_value FROM meta.run_dataset_key
                          WHERE run_id = ? AND dataset_id = ?)

=== StatusLookup (reference) ===
SELECT [t].[status_code] AS [status_code], [t].[description] AS [description]
FROM [src].[status_lookup] AS [t]
WHERE 1 = 1
```

`dry-run` shows the Polars-mode `SELECT` when
`feed.default_execution_mode = 'polars'`, and the generated view DDL when it
is `'sql'`.

### 5.2 How it is composed

- **`SELECT` list** — raw source columns, pre-transform and pre-rename, one
  per *distinct* column actually referenced, in `ordinal` order. The rename to
  `target_column` happens after the read, in the transform step. A
  `row_sequence` field contributes nothing here; it is added from the batch
  offset after the read.
- **`FROM`/`JOIN`** — the base table aliased `t`, then one join per `Lookups`
  row using its declared alias.
- **`WHERE`** — the three mode shapes:
  - `primary`: `<window_date_column> BETWEEN ? AND ?`, bound to
    `(anchor_date - window_years, anchor_date)`.
  - `dependent`: `<dependency_target_column> IN (SELECT key_value FROM
    meta.run_dataset_key WHERE run_id = ? AND dataset_id = ?)`. With
    `include_own_window = 1` this becomes
    `(<key set predicate>) OR (<window_date_column> BETWEEN ? AND ?)`.
  - `reference`: `1 = 1`.
- **`ORDER BY`** — emitted only when a field uses `row_sequence`, using that
  rule's `order_by`. Without it, the batch arrival order Polars numbers has no
  defined relationship to SQL Server's row order, and the two backends would
  assign different sequence numbers to the same logical row.
- **Parameters** — window bounds and key-set lookups are **always** bind
  parameters (`?`). No caller-supplied string is ever interpolated as a value.
  Table, schema, and column names come only from validated `meta.*` rows and
  are bracket-quoted.

The window is computed as
`anchor_date.replace(year=anchor_date.year - window_years)`. Resolution order
for both: CLI flag → `meta.feed` default → today's date (anchor only).

```bash
extract run --feed ClaimsExtract --anchor-date 2026-06-30 --window-years 3
```

### 5.3 SQL mode: the generated view

In `--mode sql` the transform moves server-side. Preview the DDL — no
database write, no connection to the source needed, this is what you hand a
DBA for buy-in:

```bash
extract explain --feed ClaimsExtract --dataset Claim --mode sql
extract explain --feed ClaimsExtract --dataset Claim --mode sql --out review.sql
```

Real output for the `Member` dataset (reformatted; the CLI prints one line):

```sql
CREATE OR ALTER VIEW gen.v_Member AS
SELECT
  REPLACE(REPLACE(REPLACE(REPLACE([t].[member_id],'|',' '),CHAR(13),' '),CHAR(10),' '),CHAR(9),' ')                  AS [MemberId],
  REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM([t].[member_name])),'|',' '),CHAR(13),' '),CHAR(10),' '),CHAR(9),' ')  AS [MemberName],
  [t].[member_id] AS [__dependency_key]
FROM [src].[member] AS [t]
```

Two things to notice:

1. Every value column is wrapped in the same four-deep `REPLACE` chain that
   strips the delimiter, CR, LF, and TAB — sanitization is baked into the view.
2. **The view has no `WHERE` clause, ever.** It is a static, deploy-once
   transform; per-run filtering cannot be baked into it. Instead the view
   exposes the raw filter columns under reserved internal names —
   `__window_key`, `__dependency_key`, and `__key_source_<col>` — and the
   client's read query filters on *those* while selecting only the real target
   columns. Filtering on the view's output columns would be wrong: they are
   the *formatted* values (a date rendered as a string sorts fragilely).

Deploying is a separate, explicit action. `extract run --mode sql` never
creates or alters a view as a side effect — it fails if the view does not
already exist:

```bash
extract deploy-views --feed ClaimsExtract                                        # prints what it WOULD do
extract deploy-views --feed ClaimsExtract --i-understand-this-writes-to-the-database
# gen.v_Claim: DEPLOYED
# gen.v_Member: DEPLOYED
# gen.v_StatusLookup: DEPLOYED
```

Redeploy the views whenever `FieldMap` or `Lookups` change — never per run.

### 5.4 Choosing a backend

| | `--mode polars` | `--mode sql` |
| --- | --- | --- |
| Transform runs | Client process | SQL Server |
| Setup | None | `deploy-views` first, redeploy on mapping change |
| DBA involvement | None | Needs `CREATE VIEW` on `gen`, and usually a review |
| Load moves to | The client box's CPU | The database server |
| Debuggability | Full Polars introspection per batch | `SELECT * FROM gen.v_*` in SSMS |

Both are required to produce byte-identical files. The `--compare-modes`
command that was meant to assert this mechanically does not exist; run both and
diff `meta.run_detail.checksum` instead (§8, step 7).

---

## 6. Output file format

### 6.1 Layout

Files are written to `meta.feed.output_root` (created if missing), named
`<output_file_stem>_part<NNN>.txt` — zero-padded to three digits, starting at
`part001`.

Real output from the verified run:

```
Claim_part001.txt
C-IN-WINDOW|100|2026-06-15|200.00|OPEN|N/A|1
TRAILER|1

Member_part001.txt
100|Alice Old-Timer
TRAILER|1

StatusLookup_part001.txt
OPEN|Open
SHUT|Shut
TRAILER|2
```

Byte-level, confirmed with `od -c` — note the explicit CRLF including on the
final line:

```
0000000   C   -   I   N   -   W   I   N   D   O   W   |   1   0   0   |
0000020   2   0   2   6   -   0   6   -   1   5   |   2   0   0   .   0
0000040   0   |   O   P   E   N   |   N   /   A   |   1  \r  \n   T   R
0000060   A   I   L   E   R   |   1  \r  \n
```

### 6.2 The format contract

| Property | Source | Default | Notes |
| --- | --- | --- | --- |
| Field delimiter | `feed.delimiter` | `\|` | Single character |
| Column order | `field_map.ordinal` | — | Never the `SELECT` order |
| Encoding | fixed | UTF-8 | No BOM |
| Line ending | `feed.line_ending` | `CRLF` | `CRLF` or `LF`. Files are opened in **binary** mode with an explicit byte sequence — text mode on Windows would silently translate `\n` to `\r\n` regardless |
| Terminator | fixed | every line, including the last | |
| Header row | `feed.emit_header_row` | off | `target_column` names joined by the delimiter. Written **per part** |
| Trailer row | `feed.emit_trailer_row` | on | `TRAILER<delimiter><row count>`. Written **per part**, counting only that part's data rows |
| NULL rendering | `feed.null_sentinel` | `''` (empty field) | |
| Quoting / escaping | none | — | There is no quoting mechanism at all; collisions are handled by substitution, not escaping |
| Checksum | computed | — | SHA-256 over **all parts concatenated**, stored in `meta.run_detail.checksum` |

There is deliberately no CSV quoting. A value containing the delimiter, CR,
LF, or TAB would shift every following field or split one record across two
lines, so the engine neutralizes those characters instead:

- **`collision_action = 'sanitize'`** (default) — replace every occurrence of
  the delimiter, CR, LF, and TAB with `collision_char` (default space). Applied
  unconditionally to every string column, whether or not a collision exists,
  so both backends stay byte-identical.
- **`collision_action = 'fail'`** — check every string column per batch and
  raise `CollisionError` naming **only the column and batch ordinal, never the
  value**. Nothing is written. In SQL mode a cheap `SELECT TOP (1) 1` probe
  runs at deploy time so the abort happens then, not on the first row a
  consumer reads.

`collision_char` must differ from `delimiter` — enforced in validation and by a
DB `CHECK` constraint.

### 6.3 Single-column `CONCAT_WS` output

With `emit_concat_ws_line = 1` the SQL backend emits one pre-joined `Line`
column instead of N columns. Every argument is wrapped in `ISNULL(..., '')`
first, unconditionally: SQL Server's `CONCAT_WS` **skips** a NULL argument
rather than emitting an empty position, which would silently shift every
downstream field on any row containing a null — a corrupt file that still
passes a row-count check. There is no code path that omits that wrapping.

### 6.4 Consuming the output

Row count per part is in its own trailer; row count per dataset is in
`meta.run_detail.row_count`. To reassemble a multi-part dataset in order, sort
by the `partNNN` suffix and drop each part's trailer (and header, if enabled).

---

## 7. Batch runs for large datasets

### 7.1 What "batch" means here — two independent numbers

These are separate knobs and are easy to confuse:

| Knob | What it controls | Where it lives | Default |
| --- | --- | --- | --- |
| **`batch_size`** | Rows per in-memory batch read from SQL Server. Bounds memory | **Hardcoded** in `execution_polars.read_batches` and in `cli._read_dataset_batches` | 100,000 |
| **`max_rows_per_file`** | Rows per output file before rolling to the next part. Bounds file size | `meta.feed.max_rows_per_file` | 1,000,000 |

`batch_size` has no CLI flag and no environment variable — changing it is a
two-line code edit in both places (both must change, or the two backends will
still agree on output but not on memory profile). `max_rows_per_file` is not
persisted by the config loader either, so set it with the direct `UPDATE` in
§4.8.

### 7.2 Streaming and the memory ceiling

Reads use `pl.read_database(..., iter_batches=True, batch_size=100_000,
schema_overrides=...)`, so a dataset is never materialized whole. Per batch
the engine does one `df.select(exprs)` for the entire transform — no Python
loop over rows, no `map_elements`, no `apply` anywhere. Schema is pinned from
`field_map.data_type` on every read, never inferred.

The committed ceiling test asserts RSS growth stays **under 250 MB while
processing 500,000 rows in 50,000-row batches**, measuring real process RSS:

```bash
python -m pytest skills/claude-skills/extract-engine/tests/test_memory_ceiling.py -q
EXTRACT_ENGINE_SKIP_RSS_TEST=1 python -m pytest ...   # opt out on a loaded CI box
```

Every run records its own `peak_rss_bytes` and `duration_ms` in
`meta.run_log`, so regressions are visible without a profiler:

```sql
SELECT run_id, execution_mode, status, duration_ms,
       peak_rss_bytes / 1048576 AS peak_mb
FROM meta.run_log ORDER BY run_id DESC;
```

### 7.3 File part rolling

Verified directly against `PartWriter` — 2,700 rows arriving as three
900-row batches, `max_rows_per_file = 1000`, header and trailer both on:

```
row_count: 2700  part_count: 3
Big_part001.txt: 1002 lines | first='Id|Name' second='0|x'    last='TRAILER|1000'
Big_part002.txt: 1002 lines | first='Id|Name' second='1000|x' last='TRAILER|1000'
Big_part003.txt: 702 lines  | first='Id|Name' second='2000|x' last='TRAILER|700'
```

What this confirms:

- A batch that would straddle the row boundary is **sliced exactly at the
  boundary** before either part receives it — never split mid-write, and
  `max_rows_per_file` is never exceeded even though batches do not divide into
  it evenly.
- Header and trailer are written **per part**. The trailer counts that part's
  data rows only; the header is not counted.
- One SHA-256 covers all parts concatenated — a per-part checksum is not
  recorded.

### 7.4 Dependent-mode key staging at volume

After a primary dataset finishes, its distinct key values are staged into
`meta.run_dataset_key` and the dependent dataset filters against them with
`IN (SELECT key_value ...)`.

- The accumulator holds only the **distinct** key set, bounded by distinct
  members/providers rather than row volume, so it is safe in memory even when
  the source table is enormous.
- Staging uses `fast_executemany` where the driver supports it.
- `key_value` is `VARCHAR(256)` and every key is stringified on insert — the
  `IN` comparison is against a string column. Confirm the implicit conversion
  is sargable against your key's real type before assuming index usage on a
  large dependent table.
- Keys are staged per primary dataset, immediately after it completes, so any
  dependent dataset later in the same run can see them. This is why
  `run_ordinal` ordering matters.

### 7.5 Checkpoint and resume

`meta.run_log` holds one row per run; `meta.run_detail` holds one row per
`(run_id, dataset_id)` with status `pending|running|completed|failed`, row
count, part count, and checksum.

```bash
extract run --feed ClaimsExtract --resume --run-id 12
```

**Granularity is per dataset, not per file part.** `--resume` skips a dataset
only if `run_detail` already shows it `completed`. A dataset that was
`running` or `failed` when the process died is reprocessed **from scratch**,
including re-running its source query and rewriting its output files. For a
feed with one enormous dataset, resume buys you nothing — split it, or accept
the full re-read.

A resumed run continues the *original* extraction rather than starting a new
one, which has four consequences worth knowing:

- **The window comes from `meta.run_log`, not from today's defaults.**
  `anchor_date` defaults to today, so re-deriving it at resume time would give
  the already-completed datasets one window and the remaining ones another —
  a file set that looks complete and is not self-consistent. `anchor_date`,
  `window_years` and `execution_mode` are all read back from the run row.
- **A flag that contradicts the record is refused**, not silently honoured:

  ```
  Error: --mode sql conflicts with run 4, which was started with polars.
  Omit the flag to continue that run, or start a fresh run instead.
  ```
- **`--resume` requires `--run-id`.** It used to fall through and silently
  start a brand-new run, re-extracting everything you were trying to skip.
- **`--run-id` must belong to `--feed`.** Resuming another feed's run is
  refused rather than extracting one feed's datasets against another's run.

Resuming a fully completed run is a no-op that reprocesses nothing:

```bash
extract run --feed ClaimsExtract --resume --run-id 4
# Run 4 complete in 0 ms.
```

### 7.6 Concurrency guard and stale `running` rows

`run` refuses to start while any `meta.run_log` row for that feed has
`status = 'running'`:

```
Error: A run is already in progress for feed 'ClaimsExtract'. Refusing to start a second one.
```

There is **no staleness timeout**. If a process dies between `start_run()`
inserting that row and `complete_run()` closing it, the row stays `running`
forever and blocks every future run for that feed. On a server with a
scheduled task this is the failure you will hit first.

Resolve it with the engine's own bookkeeping call — a guardrail-permitted
`UPDATE meta.run_log SET status = ? WHERE run_id = ?`:

```python
import sys
sys.path.insert(0, "skills/claude-skills/extract-engine/scripts")
from extract_engine import connection as conn_mod, checkpoint
with conn_mod.write_connection() as conn:
    checkpoint.complete_run(conn, run_id, duration_ms=0, peak_rss_bytes=0,
                            status="failed", error_message="stale row, manually resolved")
```

If you automate this: treat "a run is already in progress" as a signal to
inspect `meta.run_log` first, and treat marking a row `failed` as normal
operational bookkeeping — it is metadata, not customer data or a schema
object.

### 7.7 Tuning checklist for a large feed

| Symptom | Change |
| --- | --- |
| Query timeout mid-read | Raise `SQLSERVER_MCP_QUERY_TIMEOUT` (default 30 s) — the single most common large-dataset failure |
| RSS too high | Lower `batch_size` (code edit, both call sites) |
| Output files too large for the consumer | Lower `meta.feed.max_rows_per_file` |
| Too many small files | Raise `max_rows_per_file` |
| Client box is the bottleneck | Switch to `--mode sql` and push the transform to the server |
| Database server is the bottleneck | Switch to `--mode polars` |
| Resume gains nothing after a failure | Split the monolithic dataset into several datasets — resume granularity is per dataset |
| Window too wide to finish in the maintenance slot | Run narrower windows with explicit `--anchor-date`/`--window-years` and concatenate |
| Slow window scan | Index the `window_date_column`; check the `IN (SELECT key_value ...)` plan on dependent datasets (§7.4) |

### 7.8 Scheduling it

```powershell
$env:EXTRACT_ENGINE_SERVER   = 'SQLPROD01'
$env:EXTRACT_ENGINE_DATABASE = 'ClaimsDW'
$env:SQLSERVER_MCP_QUERY_TIMEOUT = '600'
cd C:\path\to\repo
python -m skills.claude-skills.extract-engine.scripts.extract_engine.cli `
    run --feed ClaimsExtract --mode polars --anchor-date 2026-06-30
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Run it under a Windows account with the SQL permissions from §2.5 — the engine
authenticates as whoever launched it, so a Task Scheduler job must run as that
account (and "Run whether user is logged on or not" needs the stored Windows
credential, which is the Task Scheduler's business, not the engine's).

Before wiring the schedule, decide how the job will handle a **stale
`running` row** after a crash (§7.6) — that is the one failure the engine
still does not resolve for you, and it blocks every subsequent run until
someone marks the dead run `failed`. A wrapper that checks `meta.run_log` for
a `running` row older than the job's own timeout, marks it `failed`, and then
resumes it with `--resume --run-id` covers the common overnight case.

---

## 8. Full step-by-step execution

The complete sequence, from nothing to verified output.

**1. Environment**

```bash
export EXTRACT_ENGINE_SERVER='(localdb)\MSSQLLocalDB'
export EXTRACT_ENGINE_DATABASE='ExtractEngineDev'
```

**2. Offline tests** — proves the install before any database is involved.

```bash
python -m pytest skills/claude-skills/extract-engine/tests -q
# 193 passed, 46 skipped
```

**3. Seed the dev database** (skip in production; deploy `seed_schema.sql`'s
`meta`/`gen` sections instead).

```bash
python skills/claude-skills/extract-engine/scripts/seed/seed_three_dataset_feed.py \
    --server "(localdb)\MSSQLLocalDB" --database ExtractEngineDev
# Loaded feed_config_version_id=N. Ready: extract dry-run --feed ClaimsExtract
```

**4. Load and validate the mapping workbook**

```bash
extract load-config --workbook .../sample-workbook.xlsx --feed ClaimsExtract --dry-run
extract load-config --workbook .../sample-workbook.xlsx --feed ClaimsExtract --allow-config-write
extract validate-config --feed ClaimsExtract
# Feed 'ClaimsExtract': 3 dataset(s), no issues found.
```

Then set the feed columns the loader skips (§4.8) — at minimum
`max_rows_per_file` and `output_root`.

**5. Preview the source queries** — no writes, safe against production.

```bash
extract dry-run --feed ClaimsExtract
```

**6. Run it, Polars backend**

```bash
extract run --feed ClaimsExtract --mode polars
# Claim: 1 rows, 1 part(s)
# Member: 1 rows, 1 part(s)
# StatusLookup: 2 rows, 1 part(s)
# Run 7 complete in 1109 ms.
```

**7. Optional — SQL backend, and prove the two agree**

```bash
extract explain --feed ClaimsExtract --dataset Claim --mode sql     # show a DBA
extract deploy-views --feed ClaimsExtract --i-understand-this-writes-to-the-database
extract run --feed ClaimsExtract --mode sql
```

Then diff the per-dataset checksums between the two runs:

```sql
SELECT d.dataset_name, r.execution_mode, rd.row_count, rd.checksum
FROM meta.run_detail rd
JOIN meta.run_log r  ON r.run_id = rd.run_id
JOIN meta.dataset d  ON d.dataset_id = rd.dataset_id
WHERE r.run_id IN (<polars_run_id>, <sql_run_id>)
ORDER BY d.dataset_name, r.execution_mode;
```

Identical checksums per dataset is the dual-backend equivalence check. (This
matched across all three datasets on the earlier verified polars/sql run pair
recorded in `DEVELOPER.md`.)

**8. Verify the output**

```bash
ls "$OUTPUT_ROOT"
od -c "$OUTPUT_ROOT/Claim_part001.txt" | head   # confirm line endings
```

```sql
SELECT dataset_id, status, row_count, part_count, checksum
FROM meta.run_detail WHERE run_id = <run_id>;
```

---

## 9. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No SQL Server target. Pass server= or set EXTRACT_ENGINE_SERVER.` | Environment variable unset. CLI commands read the environment only |
| `No SQL Server ODBC driver is installed.` | Install ODBC Driver 18 for SQL Server |
| `Connection string contains 'pwd'` | Something tried to add SQL auth. This engine is Windows Integrated Auth only, by design |
| `Give either a named instance or a port, not both.` | `HOST\INSTANCE,PORT` is a contradiction — pick one |
| Driver 18 TLS/certificate failure | Set `SQLSERVER_MCP_ENCRYPT=auto` (self-signed cert) or install a CA-issued cert and use `strict` |
| `No feed named 'X'.` | `load-config --allow-config-write` has not run for that feed |
| `No current feed_config_version for 'X'. Run load-config first.` | `meta.feed` exists but no `is_current = 1` version row |
| `Workbook's Feed sheet says 'A', not 'B'.` | `--feed` does not match the workbook's `feed_name` |
| `Pass --allow-config-write to actually upsert into meta.*.` | Validation passed; the write is opt-in |
| `rule_name 'X' is not registered.` | Typo. The message lists every valid name (§4.5) |
| `data_type '...' is not a recognized token` | Not one of §4.6's tokens. `nvarchar(n)`, `varchar(MAX)` and `nchar(n)` are all valid now (§10.2, §10.3); bare `varchar` with no length and `float` are not |
| `A run is already in progress for feed 'X'.` | Stale `running` row. §7.6 |
| `--resume needs --run-id.` | Pass the `run_id` from `meta.run_log`, or drop `--resume` |
| `--mode sql conflicts with run N, which was started with polars` | A resumed run reuses the original run's window and backend; omit the flag (§7.5) |
| `Run N belongs to another feed` | `--run-id` is from a different feed's run |
| `emit_header_row: expected 0/1 (or true/false)` and similar | An `OutputLayout`/`ExtractParameters` value the loader cannot use (§4.8) |
| `FileNotFoundError: ... 'D:\\'` partway through a run | `output_root` points at a nonexistent drive, and a `running` row is now blocking. Fix `output_root` (§2.4), then clear the row (§7.6) |
| `CollisionError` naming a column | Source data contains the delimiter/CR/LF/TAB and `collision_action = 'fail'`. Switch to `sanitize` or clean the source |
| `Ambiguous column name` | A `row_sequence` `order_by` names a column that also exists on a joined lookup. Qualify it as `t.<col>` |
| `Invalid object name 'gen.v_X'` | `--mode sql` without `deploy-views`. Run never deploys implicitly |
| `ImportError: cannot import name 'ConnectionError_' ... circular import` | You put `scripts/extract_engine/` on `sys.path`. Add the **parent** `scripts/` and `from extract_engine import ...` |
| Polars and SQL checksums differ | Usually a `date_format` whose `style` and `polars_format` disagree (§4.5), or a float `data_type` under `decimal_format` |
| Two runs of the same feed give different output | Check `meta.run_log.anchor_date` — an unset `anchor_date_default` means "today", so the window moves daily |

---

## 10. Defects found and fixed

Six bugs were reproduced live on 2026-09-14 while this guide was written, and
all six are fixed. Each entry records the reproduction, the fix, and the
post-fix evidence. The offline suite went from 175 to 193 tests; the 18 new
ones pin these behaviours.

### 10.1 `--resume` failed with a unique-key violation

`cli.run_cmd` calls `checkpoint.mark_dataset_running()` for every dataset it
is about to process, and that function was a plain `INSERT` into
`meta.run_detail` — which carries
`CONSTRAINT uq_run_dataset UNIQUE (run_id, dataset_id)`. A resumed run reuses
the original `run_id`, so any dataset that had reached `running` or `failed`
already had that row, and the insert collided. That is precisely the set of
datasets `--resume` exists to retry, so a run that died *inside* a dataset
could never be resumed.

Reproduced against run 4, whose dataset 1 was left `failed`:

```
$ extract run --feed ClaimsExtract --resume --run-id 4
pyodbc.IntegrityError: ('23000', "... Violation of UNIQUE KEY constraint
'uq_run_dataset'. Cannot insert duplicate key in object 'meta.run_detail'.
The duplicate key value is (4, 1).")
```

**Fix.** `mark_dataset_running` is now an upsert, using the same
select-then-branch shape `config_loader.upsert` already uses for datasets, so
both paths stay single parameterized statements on the guardrail's allow-list
and stay testable against a fake cursor. The update path additionally clears
`row_count`, `part_count`, `checksum` and `completed_at`: those describe the
previous attempt's files, which are about to be rewritten, and a retry that
failed again would otherwise still advertise the old checksum.

**After the fix**, the same command completes, and the resumed run's
checksums are byte-identical to the known-good run 5 for all three datasets:

```
$ extract run --feed ClaimsExtract --resume --run-id 4
Claim: 1 rows, 1 part(s)
Member: 1 rows, 1 part(s)
StatusLookup: 2 rows, 1 part(s)
Run 4 complete in 1368 ms.

dataset 1: completed rows=1 MATCHES run 5
dataset 2: completed rows=1 MATCHES run 5
dataset 3: completed rows=2 MATCHES run 5
```

### 10.2 `nvarchar(n)` and `varchar(MAX)` were rejected by the loader

`config_loader._DATA_TYPE_PATTERN` contained the alternation
`nvarchar\(\d+|MAX\)`. The `(` and the closing `\)` sat on opposite sides of
the `|`, so the pattern matched the unterminated `nvarchar(50` or the bare
literal `MAX)` — but never `nvarchar(50)` or `varchar(MAX)`. Every NVARCHAR
and every MAX-length column was unauthorable, even though
`data_type_to_polars` handled both and `seed_schema.sql`'s own comment listed
`nvarchar` as a valid token.

**Fix.** The character types are now written with the length group inside the
parentheses — `n?varchar\((?:\d+|MAX)\)` and `n?char\(\d+\)` — so all four
spellings work.

**After the fix**, a workbook mapping `src.member.member_name` (genuinely
`NVARCHAR(100)`) as `nvarchar(100)` and `src.status_lookup.description` as
`nvarchar(MAX)` validates and loads:

```
$ extract load-config --workbook variant-workbook.xlsx --feed ClaimsExtract --allow-config-write
Validation passed.
Loaded feed_config_version_id=4

field_map Member.MemberName        data_type = 'nvarchar(100)'
field_map StatusLookup.Description data_type = 'nvarchar(MAX)'
```

### 10.3 `nchar(n)` validated, then crashed mid-run

The inverse problem. `nchar\(\d+\)` was a correct alternative in the
validator, so it loaded cleanly — but `data_type_to_polars` only tested the
prefixes `varchar`, `nvarchar` and `char`, and `"nchar(5)"` starts with none
of them. It raised `ValueError: Unrecognized data_type token: 'nchar(5)'`
when the read plan was built, *after* the config write had committed, leaving
a feed that could not run until someone edited the workbook.

**Fix.** One regex, `^n?(?:var)?char\b`, now covers char/nchar/varchar/
nvarchar with or without a length. A new test walks every token the validator
accepts and asserts `data_type_to_polars` maps it, so the two sides cannot
drift apart again.

**After the fix**, a dataset with an `nchar(10)` field map runs:

```
$ extract run --feed ClaimsExtract --mode polars
StatusLookup: 2 rows, 2 part(s)
Run 8 complete in 1346 ms.
```

### 10.4 `ExtractParameters`, `OutputLayout` and `default_execution_mode` were silently dropped

`upsert()` wrote seven `meta.feed` columns while the workbook supplied values
for nine more. They were parsed, validated, and then discarded without
warning, so every feed ran on `seed_schema.sql`'s DDL defaults — the sample
workbook's own `default_execution_mode` and `window_years_default` cells were
inert.

**Fix.** `upsert()` now builds its column list at runtime from the settings
the workbook actually supplied, so all of them persist while a blank or
omitted cell still means "keep the value already in the database" rather than
overwriting it with `NULL`. A shared `coerce_feed_settings` normalises and
range-checks the values, and `validate()` reports an unusable one against its
authoring sheet instead of letting it reach a `CHECK` constraint.
`OutputLayout` also now carries `line_ending`, `null_sentinel` and
`max_rows_per_file`, so that sheet expresses the whole physical file format;
the committed sample workbook was regenerated to show them. Full table and
semantics: §4.8.

**After the fix**, the settings reach the database and change the output.
With `line_ending=LF`, `emit_header_row=1` and `max_rows_per_file=1`, the
two-row `StatusLookup` dataset splits into two parts, each with its own
header and trailer, and LF-only line endings:

```
$ od -c StatusLookup_part001.txt
0000000   S   t   a   t   u   s   C   o   d   e   |   D   e   s   c   r
0000020   i   p   t   i   o   n  \n   O   P   E   N   |   O   p   e   n
0000040  \n   T   R   A   I   L   E   R   |   1  \n
```

`dry-run` also switches to generated view DDL once
`default_execution_mode=sql` is actually persisted, which it never did before.

### 10.5 `--resume` re-derived the extraction window from today's defaults

Found while fixing §10.1. `run_cmd` computed `effective_anchor`,
`effective_window` and `effective_mode` from the CLI flags and the feed
defaults *before* branching on `--resume`, and used those for the resumed
run. `anchor_date` defaults to today, so a run started yesterday and resumed
today gave its already-completed datasets yesterday's window and its
remaining datasets today's — a file set that looks complete, passes every
row-count check, and is not self-consistent. `meta.run_log` records all three
values precisely so this cannot happen.

**Fix.** A new `checkpoint.get_run()` reads the run header back, and the
resume path uses the recorded `anchor_date`, `window_years` and
`execution_mode`. An explicit flag that contradicts the record is now an
error rather than something silently honoured or silently ignored, and
`--run-id` is checked to belong to `--feed`:

```
$ extract run --feed ClaimsExtract --resume --run-id 4 --anchor-date 2020-01-01
Error: --anchor-date 2020-01-01 conflicts with run 4, which was started with
2026-09-09. Omit the flag to continue that run, or start a fresh run instead.
```

### 10.6 `--resume` without `--run-id` silently started a whole new run

Also found while fixing §10.1. The branch was `if resume and run_id:`, so
`--resume` on its own fell through to `start_run()` and re-extracted
everything the operator was trying to skip — with no warning, and looking
exactly like a successful resume.

**Fix.** `--resume` without `--run-id` is refused:

```
$ extract run --feed ClaimsExtract --resume
Error: --resume needs --run-id. Pass the run_id of the run to continue
(see meta.run_log), or drop --resume to start a fresh run.
```

### Also hardened: duplicate key staging on a resumed dataset

Not a separate reproduction, but the same crash window as §10.1.
`stage_dataset_keys` commits a primary dataset's keys in one transaction and
`mark_dataset_complete` runs in another, so a process that dies between them
leaves keys staged for a dataset still marked `running`. Resuming then
re-staged them blind against `PRIMARY KEY (run_id, dataset_id, key_value)`.
Staging now skips keys already present for that `(run_id, dataset_id)`, and
dedupes values that share one string form — `key_value` is `VARCHAR`, so the
int `1` and the string `"1"` are one row, and staging both would violate the
primary key on its own.

---

## Verification log

Executed 2026-09-14 against LocalDB `MSSQLLocalDB`, database
`ExtractEngineDev`, ODBC Driver 17, from the repository root:

| What | Result |
| --- | --- |
| `build_connection_string` + `available_drivers` | Trusted-connection string in §3.3; six drivers found |
| `read_connection` against `meta.feed` | Confirmed every unpersisted column sat at its DDL default — the §10.4 reproduction |
| `extract dry-run --feed ClaimsExtract` | The three source queries in §5.1 |
| `extract explain --feed ClaimsExtract --dataset Member --mode sql` | The view DDL in §5.3 |
| `extract validate-config --feed ClaimsExtract` | `3 dataset(s), no issues found` |
| `extract run --feed ClaimsExtract --mode polars` | Run 7, 1109 ms; the three files in §6.1, `od -c` bytes confirmed |
| `PartWriter` with `max_rows_per_file=1000`, 3×900-row batches | The part-rolling result in §7.3 |
| `_DATA_TYPE_PATTERN` × `data_type_to_polars` over 15 tokens | The §10.2 and §10.3 reproductions |
| `extract run --resume --run-id 4` | `uq_run_dataset` violation — the §10.1 reproduction |

Then, after the fixes in §10:

| What | Result |
| --- | --- |
| `python -m pytest .../tests -q` | `193 passed, 46 skipped` — 18 new tests, no warnings |
| `extract run --resume --run-id 4` | Run 4 completed; all three datasets' checksums identical to run 5's (§10.1) |
| `extract run --resume --run-id 4` again | `Run 4 complete in 0 ms.` — every dataset already `completed`, nothing reprocessed |
| `--resume` with no `--run-id`, a conflicting `--mode`, a conflicting `--anchor-date`, an unknown `--run-id` | All four refused with the messages in §7.5, §10.5 and §10.6 |
| `load-config` of a workbook using `nvarchar(100)`, `nvarchar(MAX)`, `nchar(10)` | `Validation passed. Loaded feed_config_version_id=4` (§10.2, §10.3) |
| `metadata.get_feed` after that load | All ten optional settings persisted, including `line_ending='LF'`, `max_rows_per_file=1`, `default_execution_mode='sql'` (§10.4) |
| `extract run --mode polars` on that feed | Run 8; `StatusLookup` split into 2 parts, LF endings, per-part header — `od -c` bytes in §10.4 |
| `extract dry-run` with `default_execution_mode='sql'` persisted | Switched to generated view DDL (§10.4) |
| Reload of the committed sample workbook, then `extract run --mode polars` | Run 9; all three checksums match run 5 — no regression on the default path |
| `meta.run_log` | No stale `running` rows left behind by any of the above |

Two things were changed in the dev database to run the above, and are worth
resetting to suit your own machine: `meta.feed.output_root` for
`ClaimsExtract` points at this session's scratchpad directory, and the feed
has been reloaded from the committed sample workbook (so it is back on
`CRLF` / `max_rows_per_file=1000000` / `polars`). The variant workbook used
for the §10.2–§10.4 evidence was written to the scratchpad, not the repo.
