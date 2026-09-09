# SQL Server Schema Analysis: implementation deep dive

Companion to `skills/claude-skills/sql-server-schema/SKILL.md`. That file is
the workflow and the rules; this file is what the code actually does, line by
line where it matters. Read the skill first if you have not.

All paths below are relative to `skills/claude-skills/sql-server-schema/`
unless stated otherwise.

## 1. The digest-is-the-seam architecture

The skill is built around one split: a live connection produces a **digest**
(a JSON document describing a database), and a separate, offline generator
turns a digest into a **skill pack** (markdown an agent loads). The generator
never opens a connection, and the code enforces that as an import-graph fact,
not a convention.

```
live server --[connection.py, catalog.py]--> raw catalog rows
                                                      |
                                        [analyze.py] build_digest()
                                                      v
                                              schema_digest.json  <-- the seam
                                                      |
                                        [skillgen.py] generate_skill_pack()
                                                      v
                                              a skill pack on disk
```

`connection.py` is the only module that imports `pyodbc`, and it does so
lazily inside `_require_pyodbc()` so the module still imports cleanly with no
driver present. `catalog.py` takes an already-open connection as its first
argument to every function and never imports `pyodbc` itself or opens one.
`analyze.py`, `guardrails.py`, and `skillgen.py` import neither `pyodbc` nor
`connection`/`catalog` for network purposes — `skillgen.py` reads a digest
JSON file off disk with the standard library and calls into `analyze.py` only
for `table_signature`/`validate_digest`, both pure functions over dicts.
`sqlserver_schema_mcp.py` and `cli.py` are the only two modules that import
both halves, because they are the orchestrators that call a live connection
*and* the offline generator in the same process — but even they call them in
sequence, never intermixed within one function body except at the top level
(`mssql_build_schema_digest` connects, then hands the resulting dict to
`analyze.build_digest`, which touches no connection).

Why this matters in practice:

- **Review before generation.** The digest is a plain JSON file. A human (or a
  different, sandboxed agent) can read it, check `coverage`, spot a table that
  should not be shared, and only then run `generate`.
- **Regenerate with no driver.** `skillgen.py` and `analyze.py` are standard
  library only (`json`, `re`, `math`, `dataclasses`, `pathlib`). A pack can be
  rebuilt on a machine that has never had `pyodbc` installed, as long as a
  digest file exists.
- **Unit-testable without a server.** `catalog.py`'s functions take an
  injectable connection, so `tests/fakes.py` supplies a fake cursor and the
  whole catalog layer is tested with no network, no driver, no server. The
  generator is tested against fixture digests the same way.
- **Schema-drift detection for free.** `analyze.build_digest` sorts every
  collection before emitting JSON with `sort_keys=True`, so two digests of an
  unchanged database are byte-identical (`generated_at` aside) and `diff`
  becomes a drift detector.

## 2. Full CLI reference (`scripts/cli.py`)

`argparse` with subparsers, `dest="command", required=True`. Entry point
`python scripts/cli.py <command> [args]`.

### `drivers`

No arguments. Calls `connection.available_drivers()` then
`connection.pick_driver(drivers=installed)`. Prints
`{"ok": true, "installed": [...], "would_use": "..."}`. Needs no server, no
network — only the ODBC driver manager.

### `test-connection`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--server` | `None` (falls back to `SQLSERVER_MCP_SERVER`) | Target host, `HOST`, `HOST\INSTANCE`, or `HOST,PORT` |
| `--database` | `None` (falls back to `SQLSERVER_MCP_DATABASE`, then `master`) | Target catalog |
| `--show-login` | `False` (flag) | Include the AD login name in the output; withheld by default because it identifies an account |

Opens a real connection via `connection.connection(...)`, reads
`catalog.get_server_info`, and prints driver, encrypt mode, database, product
version/edition, `auth_scheme`, and a derived `integrated_auth_confirmed`
boolean (`True` when `auth_scheme` is `KERBEROS` or `NTLM`). Exit code 1 on
any exception, with `{"ok": false, "error": ...}`.

### `digest`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--server` | `None` | Overrides `SQLSERVER_MCP_SERVER` |
| `--database` | `None` | Overrides `SQLSERVER_MCP_DATABASE` |
| `--schema` | `None` | Limit collection to one schema |
| `--out` | `None` | Output path; default resolves outside the repo via `guardrails.default_artifact_root()` |
| `--server-alias` | `None` | Label stored in `source.server_alias` instead of the real hostname |
| `--generated-at` | `None` | Pin the timestamp, for exact byte-for-byte comparison across runs |
| `--include-views` | `False` (flag) | Include views alongside tables |
| `--include-routines` | `False` (flag) | Include procedures/functions/views in `routines` |
| `--no-infer` | `False` (flag) | Declared foreign keys only; skip name-based inference |
| `--allow-in-repo` | `False` (flag) | Permit a gitignored path inside the repo (still refuses `skills/`, `mcp/`, `docs/` unconditionally) |

Connects once, reads server info, schemas, every table's full detail via
`_collect_tables` (which calls `catalog.list_tables` then
`catalog.describe_table` per row), foreign keys, and optionally
programmability. Builds the digest with `analyze.build_digest`, resolves the
output path with `guardrails.resolve_artifact_path`, writes indented,
sorted-key JSON with a trailing newline. Prints table/relationship counts and
a reminder that the file names internal objects. Exit 1 on connection failure
or a `PermissionError` from the path resolver.

### `generate`

| Argument | Default | Meaning |
| --- | --- | --- |
| `digest` (positional) | required | Path to a schema digest JSON |
| `--out` | `None` | Output directory; default resolves outside the repo |
| `--max-columns` | `60` | Columns shown per table before collapsing to "N further columns" |
| `--max-tables-per-file` | `40` | Tables per per-schema reference file before splitting |
| `--allow-in-repo` | `False` (flag) | Same meaning as above |
| `--dry-run` | `False` (flag) | Render and measure every file, write nothing |

Loads and validates the digest (`skillgen.load_digest`, which raises if
`analyze.validate_digest` finds problems), then calls
`skillgen.generate_skill_pack` with `include_samples=False` hardcoded — the
CLI never collects sample data. Needs no database connection at all. Prints
the manifest; exit 0 only when `result["ok"]` is true.

### `example`

No arguments. Regenerates the committed
`examples/schema-digest.example.json` from the fictional six-table star
schema in `tests/test_analyze.py::_star()`. Pins `generated_at` to
`"2026-01-01T00:00:00Z"` and `server_alias` to `"demo-sql"` so the committed
fixture never drifts on an unrelated code change. Safe to commit because the
schema is invented.

## 3. Full MCP tool reference (`scripts/sqlserver_schema_mcp.py`)

14 tools. Every one is a plain Python function decorated with `@_tool`, which
registers it with `FastMCP` when `fastmcp` is importable and otherwise returns
the function unchanged — so every tool is directly callable in-process with no
MCP client. Every tool body runs inside `_envelope(fn)`, which catches
`PermissionError` (kind `"refused"`), `connection.ConnectionError_` (kind
`"connection"`), and any other exception (kind `"error"`, message from
`connection.explain_odbc_error`) and turns them into
`{"ok": False, "error": ..., "kind": ...}` instead of raising — an MCP tool
that raises hands the model a stack trace; one that returns a payload gives it
something to act on.

### Connectivity

**`mssql_list_odbc_drivers() -> Dict[str, Any]`**
No parameters. Returns `{"installed": [...], "would_use": "..."}`.

**`mssql_test_connection(server: str = "", database: str = "", include_login: bool = False) -> Dict[str, Any]`**
Opens a connection, reads server info, returns driver, `encrypt_mode`,
database, product version/edition, collation, `auth_scheme`,
`integrated_auth_confirmed`, `max_rows`, `isolation`. Adds `login_name` only
when `include_login=True`. Adds `missing_permissions`/`hint` when
`VIEW SERVER STATE` is absent.

### Discovery

**`mssql_list_databases(server: str = "", include_system: bool = False) -> Dict[str, Any]`**
Connects to `master`, returns `{"databases": [...]}` filtered by
`HAS_DBACCESS`.

**`mssql_list_schemas(database: str = "", server: str = "") -> Dict[str, Any]`**
Returns `{"schemas": [...]}` — non-system schemas with object counts.

**`mssql_list_tables(database: str = "", schema: str = "", name_like: str = "", include_views: bool = True, limit: int = 500, offset: int = 0) -> Dict[str, Any]`**
Paginated in Python after the full filtered row set comes back from
`catalog.list_tables`. Returns `tables` (the page), `total`, `returned`,
`offset`, `truncated`.

**`mssql_describe_table(table: str, database: str = "") -> Dict[str, Any]`**
Calls `catalog.describe_table`, then annotates every column with
`sensitive: bool` and `sensitivity: {category, tier} | None` from
`guardrails.classify_column_sensitivity`. Returns `{"ok": False, "error": ...}`
when the object is not found or not visible (permission trimming — see
section 4).

**`mssql_list_relationships(database: str = "", infer: bool = True) -> Dict[str, Any]`**
Note the `infer` parameter is accepted but **not actually wired to
inference** in this tool: it always builds the graph with `infer=False`
internally (declared foreign keys only, since building an inferred graph
needs full column and primary-key detail this tool does not fetch), and
attaches a `note` field pointing at `mssql_build_schema_digest` for inferred
relationships when `infer=True` was requested. Returns `relationships` and
`declared` (edge count).

**`mssql_list_indexes(table: str, database: str = "") -> Dict[str, Any]`**
Resolves the object, returns `{"table": "schema.name", "indexes": [...]}`.

**`mssql_table_stats(database: str = "", schema: str = "", top: int = 50) -> Dict[str, Any]`**
Returns `{"tables": [...], "approximate": True, "source": "sys.dm_db_partition_stats"}`.

**`mssql_list_programmability(database: str = "", kind: str = "", schema: str = "", name_like: str = "") -> Dict[str, Any]`**
`kind` is one of `procedure`, `scalar_function`, `inline_function`,
`table_function`, `view`, or empty for all. Returns `{"objects": [...]}`.

**`mssql_get_definition(name: str, database: str = "", max_chars: int = 40000) -> Dict[str, Any]`**
Returns the T-SQL body (truncated at `max_chars`), its parameters, `length`,
and `truncated`. `{"ok": False, "error": ...}` when the object does not exist,
is encrypted, or `VIEW DEFINITION` is missing.

### Query

**`mssql_run_query(sql: str, database: str = "", max_rows: int = 200) -> Dict[str, Any]`**
Runs `guardrails.classify_statement(sql)` first; refuses with
`{"ok": False, "error": ..., "kind": verdict.kind, "classified_by": verdict.engine}`
when not allowed. Caps rows with `connection.clamp_rows(max_rows)` (see
section 4). Every returned column whose name matches
`guardrails.classify_column_sensitivity` is redacted per-value with
`guardrails.redact_value` rather than withheld as a whole column. Returns
`columns`, `rows`, `returned`, `max_rows`, `truncated`, `masked_columns`,
`classified_by`.

### Digest and skill generation

**`mssql_build_schema_digest(database: str = "", server: str = "", schema: str = "", include_views: bool = False, include_routines: bool = False, out_path: str = "", allow_in_repo: bool = False) -> Dict[str, Any]`**
The live-connection half of the seam: reads server info, schemas, every
table's full detail, foreign keys, and optional routines, then calls
`analyze.build_digest` and writes the JSON via
`guardrails.resolve_artifact_path`. Returns `path`, `tables`,
`relationships`, `schemas`.

**`mssql_generate_skill(digest_path: str, out_dir: str = "", dry_run: bool = False, allow_in_repo: bool = False) -> Dict[str, Any]`**
The offline half: `skillgen.load_digest` then `skillgen.generate_skill_pack`.
No connection is opened by this tool at all.

**`mssql_get_reference(topic: str = "") -> Dict[str, Any]`**
With no `topic`, returns `{"topics": [...]}` — every `.md` stem under
`references/`. With a topic, returns `{"topic": ..., "content": "..."}` or a
`{"ok": False, "error": ...}` listing what's available. Exists so an agent
with no filesystem access (a bare MCP client) can still read the reference
docs.

**`main()`** calls `_check_mcp_shadowing()` (see section 3.1) then
`_mcp.run()`, raising `SystemExit` if `fastmcp` was never importable.

### 3.1 The `mcp/` shadowing guard

`fastmcp` depends on the `mcp` PyPI package. This repository has its own
top-level `mcp/` folder. If the repository root ever lands on `sys.path`,
`import mcp` silently resolves to that folder (a PEP 420 namespace package,
importable with no `__init__.py`) instead of the real dependency, and
`fastmcp` fails with an unhelpful error from deep inside itself.
`_check_mcp_shadowing()` imports `mcp`, checks whether its resolved location's
parent directory is named `mcp` and sits where this repository's own `mcp/`
would (`_HERE.parents[3]`), and raises a `SystemExit` naming the fix (launch
by absolute path, don't set `PYTHONPATH` to the repo root) instead of letting
fastmcp's own confusing ImportError surface.

## 4. The read-only guardrail mechanism

Four independent layers, described in `references/read-only-guardrails.md`,
of which exactly one is load-bearing.

### Layer 1 — no write tool exists

There is no `execute_sql`/`mssql_execute_statement` tool and no environment
flag anywhere in `sqlserver_schema_mcp.py` that adds one. `mssql_run_query` is
the only tool accepting arbitrary SQL, and it refuses anything but a single
SELECT.

### Layer 2 — the transaction never commits (the actual guarantee)

`connection.connection()` (a `@contextmanager` in `connection.py`) opens the
pyodbc connection with `autocommit=False` and its `finally` block
unconditionally calls `handle.rollback()`, swallowing any exception from the
rollback itself. Nothing anywhere in the skill calls `commit()`. Statement
classification (layer 3) is a heuristic that can in principle be fooled; a
transaction that is never committed cannot be. If a write statement ever slipped
past the classifier, it would still be undone when the connection context
exits.

### Layer 3 — statement classification (`guardrails.classify_statement`)

`classify_statement(sql: str) -> StatementVerdict` (a frozen dataclass:
`allowed: bool`, `kind: str`, `reason: Optional[str]`, `engine: str`,
`statement_count: int`). Order of operations:

1. `strip_sql_noise` removes block comments, then line comments, then
   replaces every string literal with `''` — comments first so
   `-- harmless\nDROP TABLE x` cannot hide behind one, literals second so a
   string containing the word DELETE cannot trigger a false positive.
2. Counts statements by splitting on `;` (ignoring a trailing empty
   fragment); more than one is refused with `kind="multi"`.
3. Runs the **keyword veto** (`_FORBIDDEN`, a tuple of 20+ regex patterns
   covering `INSERT UPDATE DELETE MERGE TRUNCATE DROP CREATE ALTER GRANT
   REVOKE DENY BACKUP RESTORE SHUTDOWN RECONFIGURE WAITFOR EXEC(UTE)
   sp_executesql xp_* sp_* (except sp_helptext) SELECT...INTO OPENROWSET
   OPENDATASOURCE OPENQUERY BULK INSERT USE`) against the cleaned text,
   **regardless of what any parser concludes** — this runs even if `sqlglot`
   later says the root is a `Select`.
4. If `sqlglot` is importable, parses with `read="tsql"`. Requires exactly one
   parsed statement, requires the root to be `exp.Select` or an `exp.With`
   whose body is a `Select` (`kind="cte_select"`); anything else is refused
   with `kind="forbidden"`. A parse exception refuses with `kind="unparsed"`
   — **failure to parse fails closed**, it is never treated as permission to
   proceed.
5. Without `sqlglot` (`ImportError`), falls back to a regex check that the
   cleaned text must visibly start with `SELECT` or a `WITH` clause feeding
   one — the policy gets **stricter**, not looser, when the optional
   dependency is missing.

The sqlglot-first, regex-always structure deliberately mirrors
`reportlineage/sql_refs.py`, which solves the same read-vs-write
classification problem for lineage extraction.

### Layer 4 — blast radius controls

Applied via `_apply_session_settings` (in `connection.py`) immediately after
every connect: `SET LOCK_TIMEOUT 5000` (never wait more than 5s on a lock) and
`SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED` (configurable via
`SQLSERVER_MCP_ISOLATION`; deliberate dirty reads rather than shared locks on
a production table just to learn its shape). Query timeout defaults to 30s
(`SQLSERVER_MCP_QUERY_TIMEOUT`), applied as `handle.timeout`. Row count is
capped by `connection.clamp_rows()`/`max_rows_ceiling()`: default 100
(`SQLSERVER_MCP_MAX_ROWS`), hard ceiling 1000 regardless of what is requested.
Rows are limited with `cursor.fetchmany(n)`, not by injecting `TOP (n)` into
the caller's SQL — rewriting a user's statement is its own bug surface.
Binary columns are reported as `{"binary": True, "bytes": N}` rather than
their raw bytes (`catalog._plain`). Every connection string carries
`APP=sqlserver-schema-mcp` so a DBA can find and kill these sessions via
`sys.dm_exec_sessions`.

### `assert_no_credentials` (`connection.py`)

```python
def assert_no_credentials(conn_str: str) -> None
```

Runs on every connection string `build_connection_string` produces. Matches
`_CREDENTIAL_KEYS = re.compile(r"(?:^|;)\s*(pwd|password|uid|user\s*id)\s*=", re.IGNORECASE)`
against the string and raises `ConnectionError_` if any of `pwd`, `password`,
`uid`, or `user id` appears as a key. There is no password parameter, no
`SQLSERVER_MCP_PASSWORD` environment variable, and no secrets file anywhere in
this skill; this function is the coded enforcement of that promise, so a
future edit that accidentally introduces SQL authentication fails immediately
rather than shipping quietly. `build_connection_string` always appends
`Trusted_Connection=yes` and never emits `UID`/`PWD`.

### Permission trimming is a distinct failure mode

SQL Server hides catalog objects a login has no rights on rather than
returning a permission error — an empty table list or a "not found" table can
mean "you cannot see it", not "it does not exist". `catalog.describe_table`'s
not-found message says this explicitly. Missing `VIEW DATABASE STATE` zeroes
every row count silently (partition stats return nothing); missing
`VIEW DEFINITION` nulls out a routine body; missing `VIEW SERVER STATE` nulls
`auth_scheme`. None of these are treated as connection failures.

## 5. The schema digest format

Built by `analyze.build_digest(*, database, tables, foreign_keys, schemas=None, server_info=None, routines=None, report_reads=None, generated_at=None, server_alias=None, infer=True, warnings=None) -> Dict[str, Any]`.
Top-level keys, verified against the function body:

```
digest_schema_version   "1.0" (DIGEST_SCHEMA_VERSION)
generated_at            caller-supplied or ""
generator               "sqlserver-schema-mcp"
source                  {server_alias, database, product_version, edition, collation}
coverage                {schemas, table_count, relationship_count,
                         declared_relationships, inferred_relationships}
schemas                 raw sys.schemas rows, passed through
tables                  list, sorted by (schema.lower(), name.lower())
relationships           list, sorted by (from_table.lower(), to_table.lower(), confidence)
routines                list, sorted by (schema_name.lower(), object_name.lower())
conventions             see detect_conventions() below
warnings                sorted(set(...))
```

Each entry in `tables` (built in `build_digest`'s loop) carries:
`signature`, `schema`, `name`, `object_type`, `row_count`, `size_mb`,
`description`, `role`, `role_reasons`, `tier`, `score`, `report_reads`,
`measure_columns`, `date_columns`, `primary_key`, `columns` (full detail from
`catalog.get_columns`), `indexes`. `signature` comes from
`table_signature(database, schema, table)` = `"{db}::{schema}.{table}"`,
lower-cased, schema defaulting to `dbo` — byte-for-byte identical to
`reportlineage.sql_refs.TableRef.signature()`, which is what lets a lineage
scan join to a digest with no mapping layer (asserted by
`tests/test_analyze.py::test_signature_matches_reportlineage_when_it_is_importable`).

Each entry in `relationships` carries `from_table`, `to_table`, `from_columns`,
`to_columns`, `constraint` (`None` for an inferred edge), `confidence`
(`"declared"` or `"inferred"`), `trusted` (`False` when the FK was created
`WITH NOCHECK`, i.e. `is_not_trusted`).

### Join graph (`JoinGraph`, `build_join_graph`, `infer_relationships`)

`JoinGraph` is a dataclass: `nodes: Dict[str, Dict]` keyed by `"schema.table"`,
`edges: List[Dict]`. `build_join_graph(tables, foreign_keys, *, infer=True)`
adds one node per table and one `confidence="declared"` edge per foreign key
(grouped by constraint in `catalog.get_foreign_keys`, so a composite key is
one edge with multiple column pairs), then optionally extends with
`infer_relationships`. Inference indexes every table's single-column primary
key by lower-cased name, then for every other table's non-PK column whose
name ends in `id`/`key`/`code`/`no`, looks up a table whose PK has that exact
name; a hit that is not already a declared edge becomes a
`confidence="inferred", trusted=False` edge. `shortest_join_path` is a plain
BFS over the combined edge list, capped at `max_hops=3`, treating edges as
undirected for traversal.

### Table classification (`classify_table`, first-match order)

Signals computed per table: `in_degree`/`out_degree` from the join graph,
`row_count`, `_measure_columns` (numeric type in `_MEASURE_TYPES` whose name
matches `_MEASURE_NAME` and which is not part of the primary key),
`_date_columns` (type in `_DATE_TYPES`), `column_count`, `pk_columns`. Rules,
in the literal order the code checks them (first match wins):

1. `staging` — schema in `{stg, staging, tmp, temp, etl, load, work}`, or the
   name matches `_STAGING_NAME` (a `stg_`/`tmp_`/`bak_` prefix, a
   `_bak`/`_old`/`_copy` suffix, or a trailing 8-digit date stamp).
2. `audit` — name matches `_AUDIT_NAME` (`audit`, `_log$`, `history`, `_hist$`,
   `archive`, `changetracking`) **and** `in_degree == 0`.
3. `fact` — name starts with an explicit `fact`/`Fact`/`FACT` prefix
   (`_FACT_NAME`, case-sensitive boundary so `Factory` does not match).
4. `dimension` — same for a `dim` prefix (`_DIM_NAME`).
5. `bridge` — composite primary key (2+ columns) where every PK column is
   also an outgoing FK column, and the table has at most 3 non-key columns.
6. `lookup` — `row_count < 1000` and `column_count <= 8`, **and** one of:
   `in_degree >= 1`; or the name matches `_LOOKUP_NAME`
   (`lookup`, `_ref$`, `reference`, `_type$`, `_status$`, `_code$`,
   `codes?$`); or `_has_code_and_description` (a code/id/key/abbr-suffixed
   column plus a name/description/desc/label/text/title-suffixed column).
7. `fact` (cardinality path) — `out_degree >= 2` and at least one measure
   column and (`row_count >= median_rows * 10` or the table has a date
   column).
8. `dimension` (cardinality path) — `in_degree >= 2` and `out_degree <= 1`.
9. `dimension` (early/small-model path) — `in_degree >= 1`, `out_degree == 0`,
   and at least 2 `varchar`/`nvarchar`/`char`/`nchar` descriptive columns —
   covers a dimension nothing has got round to referencing yet.
10. `operational` — participates in the join graph (`in_degree` or
    `out_degree` nonzero) with no clearer signal.
11. `unknown` — no keys and no name signal.

Every branch returns `(role, reasons)`; `reasons` is what a reviewer checks
when a call looks wrong.

### Scoring and tiering (`classify_tables`)

```
score = 5.0 * (report_reads / max_report_reads)
      + 3.0 * (in_degree    / max_in_degree)
      + 2.0 * (out_degree   / max_out_degree)
      + 1.5 * (log10(max(row_count, 1)) / 9)
      + 1.0 * role_weight[role]
      + (-2.0 if row_count == 0 else 0)
```

`role_weight`: `fact=1.0, dimension=0.9, bridge=0.5, lookup=0.4,
operational=0.3, unknown=0.2, audit=0.1, staging=0.0`. Every denominator is
`max(..., 1)` so an all-zero column never divides by zero. Ranked by
`(-score, key)` for a stable, reproducible order across runs. `max_tier1=40`
and `max_tier2=150` (both are `classify_tables` keyword defaults, not exposed
as CLI/tool arguments): tier 1 is the first 40 ranked entries with
`score >= 0.5`; tier 2 is the next 150 or any entry scoring `>= 0.25`; else
tier 3. Independent of score, **any table with `report_reads > 0` is forced to
`tier = min(tier, 2)`** — ground truth about what a real report reads beats a
heuristic guess.

### `detect_conventions`

Counts, over every table: dominant case style (`_case_style`: `UPPER_SNAKE`,
`snake_case`, `PascalCase`, `camelCase`), fraction of plural table names,
dominant primary-key naming style (`Id`, `<Table>Id`, `<Table>Key`,
`composite`, `other`), count/rate of tables with no primary key, every
soft-delete column matching `_SOFT_DELETE` (`is_deleted`, `is_active`,
`deleted_date`, `record_status`, `row_status`), every SCD2 column matching
`_SCD2` (`effective_from/to`, `valid_from/to`, `is_current`,
`row_start/end_date`), the top 10 recurring audit-column names matching
`_AUDIT_COLUMN` (`created/modified/updated/inserted_by/date/on/at/utc`,
`rowversion`, `timestamp`), and the distribution of string column types.
Every reported rule carries `{value, count, hit_rate}` so a consumer can judge
confidence rather than treat it as certain.

### `validate_digest`

Returns a list of problem strings (empty = usable). Checks:
`digest_schema_version` present and its **major** component matches the
generator's; `source.database` present; `tables` is a list and every entry
has `schema`, `name`, and a `columns` list; `relationships` is a list. A major
version mismatch is rejected outright rather than best-guessed.
`skillgen.load_digest` calls this and raises `ValueError` on any problem.

## 6. The skill-pack generator (`skillgen.py`)

`generate_skill_pack(digest, out_dir=None, *, include_samples=False, max_columns_per_table=60, max_tables_per_file=40, allow_in_repo=False, dry_run=False) -> Dict[str, Any]`
is pure, deterministic templating: f-strings over sorted collections, no
Jinja, no network, no model call. Same digest and options in, byte-identical
pack out — which is what makes `tests/test_skillgen.py`'s golden-file test and
the "diff two packs to see what changed" workflow both meaningful.

Files produced, always, regardless of database size:

| File | Renderer |
| --- | --- |
| `.gitignore` (body `*`) | constant `_PACK_GITIGNORE` |
| `SKILL.md` | `render_skill_md` |
| `REDACTIONS.md` | `render_redactions` |
| `schema_digest.json` | the input digest, re-serialized sorted |
| `references/00-index.md` | `render_index` |
| `references/01-join-graph.md` | `render_join_graph` |
| `references/02-conventions.md` | `render_conventions` |
| `references/03-query-recipes.md` | `render_query_recipes` |
| `references/schema-<schema>[-N].md` | `render_schema_reference`, one call per chunk |

Per-schema files are chunked at `max_tables_per_file` (default 40) tables,
tables ordered `(tier, name.lower())` before chunking, so tier 1 tables land
in the first file of a schema. `SKILL.md` never inlines the full table
catalog — it lists only the top 10 tier-1 tables and points everything else
at `00-index.md`, which is the mechanism that keeps a 3000-table database's
entry point loadable.

### `render_skill_md`

Builds real skill-pack frontmatter: `name: "Schema: {database}"` (quoted,
because unquoted `Schema: X` parses as a YAML mapping, not a string —
asserted by a skillgen test), a generated `description`, `allowed-tools:
[Read, Glob, Grep]` only (no `Write`/`Edit`/`Bash` — a schema pack is
reference material, not something that should be able to modify anything),
`triggers` (tier-1 table names + database name + schema names, lower-cased,
capped at 40), and a `metadata` block carrying `generator_version`,
`digest_schema_version`, `source_database`, `source_server_alias`,
`table_count`, `samples_included`, and two safety flags always set true:
`contains_internal_identifiers` and `do_not_commit`. Body: a do-not-commit
banner immediately under the H1, a role-mix summary, a "read this in order"
list pointing at the reference files, a top-10 tier-1 table table, the
convention bullets (`_convention_lines`), and the bundled-asset index.

### `_column_note` (per-column "Notes" cell)

Order: `PK` if in the primary key; `identity` if `is_identity`; `` FK to
`target` `` (suffixed `(inferred)` when the edge's confidence is not
`"declared"`) if the column drives an outbound relationship; `computed` if
computed; `default {expr}` if a default exists; the sensitivity flag last —
`**withheld: sensitive ({category})**` for a hard-deny category, or
`**sensitive ({category})**` otherwise — then the `MS_Description` text if
present.

### Fact/dimension/bridge/lookup classification and importance scoring

Both live in `analyze.py`, not `skillgen.py` — `skillgen.py` only renders what
`classify_tables` already decided. See section 5 above for the exact
first-match rule order and the score formula; `skillgen.py` consumes the
resulting `role`, `role_reasons`, `tier`, and `score` fields verbatim when
building `SKILL.md`'s top-10 table and `00-index.md`'s tiered tables.

### PII safety in the generator

`_sensitive_note` calls `guardrails.classify_column_sensitivity` for every
column in every table. `render_redactions` builds `REDACTIONS.md`
unconditionally (even when nothing was flagged) listing every flagged
`{table, column, category, tier}`, explicitly states that column *names* are
shown deliberately (an agent that does not know a sensitive column exists
will write `SELECT *` and pull it — naming and marking it is the safer
failure mode), and ends with a "not withheld, review before sharing" section
naming the server alias, the database name, and the raw count of table/column
names left in the clear, closing with "this pack is not safe for a public
repository."

### Write-location safety: `guardrails.resolve_artifact_path`

Both `mssql_build_schema_digest`/`mssql_generate_skill` and the CLI's
`digest`/`generate` commands route their output path through
`resolve_artifact_path(out_path, default_name, *, allow_in_repo=False)`.
Resolution order:

1. No `out_path` given -> `guardrails.default_artifact_root() / default_name`,
   which is `%LOCALAPPDATA%\sqlserver-schema-mcp` (or `$XDG_DATA_HOME`, or
   `~/.local/share`) unless `SQLSERVER_MCP_ARTIFACT_DIR` overrides it —
   outside any repository by default.
2. `find_repo_root` walks up from the target looking for `.git`. If none is
   found, the path is accepted as-is (there is nothing to protect).
3. If the target's first path component under the found repo root is `skills`,
   `mcp`, or `docs` (`_NEVER_WRITE`), raise `PermissionError`
   **unconditionally** — no flag overrides this, because those are exactly the
   directories that get committed and a generated pack names internal
   database identifiers.
4. Otherwise, shell out to `git check-ignore -q <path>` (a real check against
   real ignore rules, not a naming convention). If git would **not** ignore
   the path, raise `PermissionError`.
5. If it would be ignored but `allow_in_repo` was not passed, raise
   `PermissionError` telling the caller to pass it explicitly.

This is tested against the real repository path in
`tests/test_skillgen.py::test_refuses_to_write_into_the_repository` and
`tests/test_tools.py::test_generate_skill_refuses_to_write_into_the_repository`.

## 7. The nine reference docs, one paragraph each

**`connection-and-auth.md`** — Windows Integrated Auth only, no password path
anywhere; driver selection order and how to check what's installed; the
single most common failure (`ODBC Driver 18`'s default `Encrypt=yes` with
certificate validation breaking a self-signed on-prem server, fixed by
`SQLSERVER_MCP_ENCRYPT=auto`); every server address form
(`HOST`/`HOST\INSTANCE`/`HOST,PORT`); the full environment variable table;
session settings (`LOCK_TIMEOUT`, `READ UNCOMMITTED`); and a SQLSTATE-to-cause
table for diagnosing a failed connection, starting from `sqlcmd -E` before
blaming Python at all. Open this first for any connection problem.

**`catalog-views.md`** — why `sys.*` and never `INFORMATION_SCHEMA` (identity
columns, computed columns, included/filtered index columns, untrusted FKs,
and the 4000-character routine-definition truncation `INFORMATION_SCHEMA`
imposes), a table mapping every schema question to the exact `sys.*` view
that answers it, the traps (`max_length` in bytes for `n`-types, resolving
types through `user_type_id` not `system_type_id`, foreign keys arriving one
row per column and needing grouping, `is_not_trusted` on a `WITH NOCHECK` FK),
the object_id-first identifier-safety pattern that keeps every query
parameterized, and the permission-trimming table (missing `VIEW DEFINITION`/
`VIEW DATABASE STATE`/`VIEW SERVER STATE`/object rights, and what each one
silently breaks). Open this when writing or debugging a catalog query.

**`read-only-guardrails.md`** — the four-layer read-only boundary in full,
explicit about which layer is actually load-bearing (the unconditional
rollback, not the classifier), the exact keyword veto list and the
sqlglot-first/regex-always structure, the blast-radius control table (lock
timeout, isolation, query timeout, row cap, binary handling, `APP=`), what
remains the operator's own responsibility (an expensive-but-legal SELECT,
`READ UNCOMMITTED` dirty reads, treating "the tool accepted it" as proof of
safety), and where the classifier's test corpus lives. Open this to understand
or extend the write-prevention mechanism.

**`schema-digest-format.md`** — the digest's full JSON shape field by field,
the determinism guarantee and how it is tested, the `signature` cross-tool
join key and its exact equivalence to `reportlineage`'s `TableRef.signature()`,
the classification rule table (role by role) and the score formula with tier
thresholds, the `conventions` block, `validate_digest`'s checks, and what a
digest deliberately omits (no data values, no procedure bodies, no real
hostname). Open this before writing or reading a digest, or before trusting
a role classification.

**`skill-pack-format.md`** — the generated pack's file layout, the
context-budget contract table (which file has which size ceiling and what
happens when it's exceeded — this is the section explaining *why* the layout
looks the way it does for a huge database), the exact `SKILL.md` frontmatter
shape including the YAML-quoting gotcha, the per-table markdown block format
and its Notes-cell ordering, the query-recipe generation rules (soft-delete
injection, half-open date ranges, no `SELECT *`), and how to regenerate with
`--dry-run` first. Open this to understand what a generated pack will contain
before running the generator, or to change what it renders.

**`pii-and-public-repo-safety.md`** — the two distinct risks handled
separately: reading data that should not be read (the sensitivity-category
table, the value-shape detector, what happens to a flagged column in each of
the three tools/artifacts that can surface one, and why the column *name* is
shown rather than hidden), and a schema reaching this public repository (the
three defensive layers: default-outside-the-repo, the unconditional
`skills/mcp/docs` refusal plus the real `git check-ignore` call, and every
pack's self-gitignore plus do-not-commit banner). Open this before sharing a
digest or a generated pack with anyone, or when deciding whether a column
needs withholding.

**`rdl-to-tables.md`** — the concrete chain from an `.rdl`'s connection string
through to a generated query recipe, the three routes `report-lineage`
already provides for getting table names out of RDL/DTSX, why the join
signature format is shared byte-for-byte with `reportlineage.sql_refs.TableRef`,
and the highest-value integration this skill enables but does not itself wire
up: passing `mssql_get_definition` as `reportlineage.builder`'s
`proc_body_lookup=` so lineage traces through a stored procedure's body
instead of stopping at the `EXEC`. Open this when connecting this skill to
`skills/report-lineage/`, or when `report_reads` counts in a digest look too
low.

**`existing-tools-and-alternatives.md`** — the build-versus-adopt research
trail: Microsoft's official SQL MCP Server (rejected because it is
entity-first/data-first via Data API builder rather than schema-discovery
first, and its auth story targets Entra ID, not an on-prem domain account),
the `microsoft/skills-for-fabric` SQL skills (cited but not vendored — they
share only the T-SQL surface, nothing about `az`/workspace/Entra applies
on-prem), and five community MSSQL MCP servers evaluated and rejected (mainly
because each ships an unguarded `execute_sql`, which is a disqualifier against
this skill's structural read-only posture). Open this to understand why this
skill exists as bespoke code instead of a dependency, or before evaluating
whether to adopt one of those projects later.

**`mcp-client-setup.md`** — the `mcp/` folder name-collision problem and its
fix in full (point at the real script by absolute path, set `cwd` to its
`scripts/` directory, never `PYTHONSAFEPATH=1`), then per-client configs for
Claude Code (project-scoped `.mcp.json` with variable expansion so no
hostname is committed, plus a user-scope `claude mcp add` alternative), Claude
Desktop, VS Code/Copilot agent mode (with an `inputs` prompt for the same
reason), SSMS 22.7+, Devin and Windsurf (and the caveat that Windows
Integrated Auth needs a real Windows session with a Kerberos ticket, so a
Linux-hosted cloud agent cannot use this skill at all), and a four-call
verification sequence ending in confirming a `DROP TABLE` is refused. Open
this when wiring the server into any specific MCP host.

## 8. End-to-end worked example: test-connection -> digest -> generate

Assumes `pip install pyodbc fastmcp sqlglot` has been run and a SQL Server
ODBC driver is installed. Run from
`skills/claude-skills/sql-server-schema/scripts/`.

```bash
# 0. Confirm a driver is present before touching a server.
python cli.py drivers
# {"ok": true, "installed": ["ODBC Driver 18 for SQL Server", ...], "would_use": "ODBC Driver 18 for SQL Server"}

# 1. Set the target once, per session (never committed — these stay in the environment).
export SQLSERVER_MCP_SERVER="SQLPROD01"
export SQLSERVER_MCP_DATABASE="OrdersDW"
# PowerShell equivalent:
#   $env:SQLSERVER_MCP_SERVER = "SQLPROD01"
#   $env:SQLSERVER_MCP_DATABASE = "OrdersDW"

# 2. Prove Windows Integrated Auth actually reaches the server.
python cli.py test-connection
# {"ok": true, "driver": "ODBC Driver 18 for SQL Server", "encrypt_mode": "auto",
#  "database": "OrdersDW", "product_version": "15.0.4345.5",
#  "auth_scheme": "KERBEROS", "integrated_auth_confirmed": true}
#
# If auth_scheme is not KERBEROS or NTLM, stop here and check with:
#   sqlcmd -S SQLPROD01 -d OrdersDW -E -Q "SELECT @@VERSION, SUSER_SNAME()"

# 3. Read the schema and write a portable digest. Written outside the repo
#    by default (%LOCALAPPDATA%\sqlserver-schema-mcp on Windows).
python cli.py digest --server-alias prod-orders --include-routines
# {"ok": true, "path": "C:\\Users\\<you>\\AppData\\Local\\sqlserver-schema-mcp\\digests\\ordersdw.schema-digest.json",
#  "tables": 812, "relationships": 340,
#  "note": "This file names internal database objects. It is written outside the repository by default."}

# 4. Read the digest before generating anything from it. Check `coverage`,
#    spot-check a few `role`/`role_reasons` entries, and look at `warnings`.

# 5. Dry-run the generator first: renders and measures every file, writes nothing.
python cli.py generate "C:\Users\<you>\AppData\Local\sqlserver-schema-mcp\digests\ordersdw.schema-digest.json" --dry-run
# {"ok": true, "out_dir": "...\\schema-skills\\ordersdw", "dry_run": true, "files": [...]}

# 6. Generate for real.
python cli.py generate "C:\Users\<you>\AppData\Local\sqlserver-schema-mcp\digests\ordersdw.schema-digest.json"
# {"ok": true, "out_dir": "...\\schema-skills\\ordersdw", "dry_run": false,
#  "files": [{"path": "...\\SKILL.md", "bytes": 6120}, ...],
#  "manifest": {"table_count": 812, "samples_included": false,
#               "contains_internal_identifiers": true, "do_not_commit": true}}

# 7. Before sharing the pack with anyone, read REDACTIONS.md inside it.
```

The same sequence, driven as MCP tools instead of the CLI, from an MCP client
configured per `references/mcp-client-setup.md` (server env carrying
`SQLSERVER_MCP_SERVER=SQLPROD01`, `SQLSERVER_MCP_DATABASE=OrdersDW`):

```
mssql_test_connection()
mssql_build_schema_digest(server_alias="prod-orders", include_routines=True)
# -> {"path": "...\\ordersdw.schema-digest.json", "tables": 812, ...}
mssql_generate_skill(digest_path="...\\ordersdw.schema-digest.json", dry_run=True)
mssql_generate_skill(digest_path="...\\ordersdw.schema-digest.json")
```

Both paths land on the same generator and the same write-location refusal:
attempting `--out skills/schema-ordersdw` (CLI) or
`out_dir="skills/schema-ordersdw"` (tool) is refused unconditionally, because
`skills/` is one of the three directories `guardrails.resolve_artifact_path`
never allows.

## Source files referenced in this document

- `skills/claude-skills/sql-server-schema/scripts/connection.py`
- `skills/claude-skills/sql-server-schema/scripts/catalog.py`
- `skills/claude-skills/sql-server-schema/scripts/analyze.py`
- `skills/claude-skills/sql-server-schema/scripts/guardrails.py`
- `skills/claude-skills/sql-server-schema/scripts/skillgen.py`
- `skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py`
- `skills/claude-skills/sql-server-schema/scripts/cli.py`
- `skills/claude-skills/sql-server-schema/references/*.md`
- `skills/claude-skills/sql-server-schema/examples/schema-digest.example.json`
- `skills/claude-skills/sql-server-schema/examples/generated-pack/`
- `mcp/README.md`, `mcp/sqlserver_schema_server.py`
