# MCP servers

This repository ships four MCP (Model Context Protocol) server files under
`mcp/`. Two run standalone from that folder as thin launchers over
dependency-free skill scripts. Two are reference copies from a source
repository's `backend/app/mcp/` and cannot run from this repository as-is
because the `app.core.*` package tree they import is not present here.

`mcp/__init__.py` is an empty file. Its only role is to make `mcp/` an
ordinary (non-namespace) package for tooling that expects one; it exports
nothing and is not read by either launcher.

| Server | Runs as-is here? | Needs |
| --- | --- | --- |
| `rdl_generation_server.py` | Yes | Nothing beyond this repo: launches `skills/claude-skills/rdl-generation/scripts/mcp_server.py`, itself Python 3.9+ stdlib only, plus optional `fastmcp` |
| `sqlserver_schema_server.py` | Yes, with a driver | `pip install pyodbc fastmcp`, a Microsoft ODBC driver for SQL Server, Windows Integrated Auth to the target instance |
| `report_studio_server.py` | No, reference only | source repo's `backend/app/core/report_studio/*`, `core/parser/prompt_to_ir.py`, `core/pbip/fixer.py`, and everything those transitively import |
| `pbi_refine_server.py` | No, reference only | source repo's `backend/app/core/validation/structural.py`, `core/preview/layout_preview.py`, and everything those transitively import |

## The `mcp/` name collision

`fastmcp` depends on the `mcp` package from PyPI. This repository also has a
top-level folder called `mcp/`. Whenever the repository root is on
`sys.path`, `import mcp` resolves to this folder instead of the PyPI package,
and `fastmcp` fails with a confusing error raised from inside its own
internals rather than a plain `ModuleNotFoundError`.

Deleting `mcp/__init__.py` would not fix it: since PEP 420, a directory
without `__init__.py` is still importable as a namespace package and still
shadows the real one.

The safe pattern: launch a server by absolute (or relative-from-cwd) file
path, e.g. `python mcp/sqlserver_schema_server.py` — this puts `mcp/` itself
on `sys.path[0]`, not the repository root. Do not run
`python -m mcp.sqlserver_schema_server` from the repository root, and do not
put the repository root on `PYTHONPATH`.

`skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py`
carries a runtime check for exactly this, `_check_mcp_shadowing()` (called
from `main()`), which imports `mcp`, inspects `mcp.__file__`/`mcp.__path__`,
and raises `SystemExit` with a corrective message if the resolved location is
this repository's own `mcp/` folder rather than the real SDK package.

This also constrains module naming inside each skill's `scripts/` folder.
Both launchers (`rdl_generation_server.py`, `sqlserver_schema_server.py`)
insert their skill's `scripts/` directory onto `sys.path` and import their
real server by module name. Two skills both shipping a module named
`mcp_server.py` would collide in `sys.modules`, and whichever launcher
imports second would silently receive the first one's already-imported
module — re-exporting the wrong tools with no error at all. This is why the
SQL Server script is deliberately named `sqlserver_schema_mcp.py` and not
`mcp_server.py`.

---

## `rdl_generation_server.py`

### What it is

Generates and validates SSRS `.rdl` (Report Definition Language) files from a
JSON spec, and validates existing `.rdl` files for structural correctness
(well-formed XML, correct namespace, element order, field/dataset reference
resolution, unit presence, header/footer nesting, nonzero body height). It
solves the problem of hand-writing SSRS XML: an agent emits a JSON spec, the
server deterministically builds and checks the RDL, with no model call in the
generation/validation path itself.

### Standalone or needs backend?

Fully standalone. `mcp/rdl_generation_server.py` (repo root) is a thin
launcher: it inserts
`skills/claude-skills/rdl-generation/scripts/` onto `sys.path` and re-exports
everything from that directory's `mcp_server.py`. The real server —
`skills/claude-skills/rdl-generation/scripts/mcp_server.py` — imports only
two sibling modules in the same folder, `rdl_builder.py` (`build_from_spec`,
`field_ref`, `aggregate_ref`) and `validate_rdl.py` (`validate`), both pure
Python 3.9+ standard library. No `app.*`/backend import anywhere in this
chain. `fastmcp` is optional — imported in a `try/except ImportError`; when
absent, every tool function still works as a plain Python call, only the
stdio server (`main()`) is unavailable.

### Tools

| Tool | Parameters | Returns | Description |
| --- | --- | --- | --- |
| `get_json_spec_schema()` | none | `str` | Returns the JSON spec schema documented in `rdl_builder.py`'s module docstring (sliced from the `"JSON spec schema"` marker onward), so an agent can emit a spec in the exact shape `build_rdl` expects. |
| `build_rdl(spec_json: str, out_path: str, validate: bool = True)` | `spec_json`: report spec as JSON text. `out_path`: where to write the `.rdl` (parent dirs created as needed). `validate`: when true, immediately runs the structural validator on the file just written. | `Dict[str, Any]`: `{ok: bool, path: str, error: str\|None, findings: [{severity, code, message}], report_name: str}` | Builds and saves an `.rdl` from a JSON spec via `build_from_spec`, then optionally validates it. `ok` is true only when the file was written AND (if `validate`) no error-severity finding was raised. Malformed JSON or a spec `build_from_spec` cannot build never raises — it returns `ok: false` with `error` set. |
| `validate_rdl_file(path: str, strict: bool = False)` | `path`: path to the `.rdl` file. `strict`: when true, any warning-severity finding also fails `ok`. | `Dict[str, Any]`: `{ok: bool, findings: [{severity, code, message}], error_count: int, warning_count: int}` | Runs the same checks as `validate_rdl.py`: well-formed XML, recognised RDL namespace, `Query` child element order, every `Fields!X.Value` reference resolves to a declared `Field`, every `DataSetName` resolves to a declared `DataSet`, every measurement carries a unit, `PageHeader`/`PageFooter` nest inside `Page`, nonzero `Body` height. |
| `field_expression(field_name: str, aggregate: Optional[str] = None)` | `field_name`: dataset field name. `aggregate`: optional, one of `Sum`, `Avg`, `Min`, `Max`, `Count`, `CountDistinct`, `First`, `Last`. | `str` | Returns the round-trip-safe RDL expression: `=Fields!Name.Value` without an aggregate, or `=Sum(Fields!Name.Value)` etc. with one. Use instead of hand-composing the string, since this is the exact form the repository's RDL parser reads back. |
| `list_reference_topics()` | none | `List[str]` | Sorted list of reference-doc topic names (`.md` stem) shipped under `skills/claude-skills/rdl-generation/references/`. |
| `get_reference(topic: str)` | `topic`: a name from `list_reference_topics` (with or without `.md`). | `Dict[str, Any]`: `{ok, topic, content: str\|None, error: str\|None}` | Returns the full content of one reference doc — document skeleton/element order, DataSource/DataSet/Field/Parameter XML, Tablix/Chart object models, expression syntax, layout units, schema versions, pre-flight validation checklist — for an agent with no filesystem access. |
| `list_examples()` | none | `List[str]` | Sorted filenames of the five bundled, validating example `.rdl` files under `skills/claude-skills/rdl-generation/examples/`. |
| `get_example(name: str)` | `name`: a name from `list_examples` (with or without `.rdl`). | `Dict[str, Any]`: `{ok, name, content: str\|None, error: str\|None}` | Returns the full XML of one bundled example `.rdl`. |
| `main()` | none | none (`SystemExit` or serves forever) | Serves the tools over stdio via `_mcp.run()`. Raises `SystemExit` if `fastmcp` is not installed. |

### Install / run

```bash
pip install fastmcp   # optional; only needed to serve over stdio
python mcp/rdl_generation_server.py
```

No environment variables are read by this server at all.

### Worked example

MCP client config, pointed at the real file directly (recommended over the
launcher):

```json
{
  "mcpServers": {
    "rdl-generation": {
      "command": "python",
      "args": ["skills/claude-skills/rdl-generation/scripts/mcp_server.py"]
    }
  }
}
```

Direct in-process call, no MCP client needed:

```python
from mcp_server import build_rdl, field_expression

spec = {
    "report_name": "SalesByRegion",
    "datasources": [{"name": "DS1", "connect_string": "..."}],
    "datasets": [{"name": "SalesDS", "fields": ["Region", "Amount"]}],
}
import json
result = build_rdl(json.dumps(spec), "out/SalesByRegion.rdl")
# {"ok": True, "path": "out/SalesByRegion.rdl", "error": None, "findings": [...], "report_name": "SalesByRegion"}

expr = field_expression("Amount", aggregate="Sum")
# "=Sum(Fields!Amount.Value)"
```

### Safety / guardrails

No cloud call anywhere in this file. No credentials, no network access, no
write target outside the `out_path`/`out_dir` the caller supplies. `build_rdl`
never raises on bad input — it always returns `ok: false` with a message.

### Gotchas

- The launcher (`mcp/rdl_generation_server.py`) inserts
  `skills/claude-skills/rdl-generation/scripts` onto `sys.path` at import
  time and then does `from mcp_server import (...)`. If a second skill ever
  ships its own `scripts/mcp_server.py`, importing both launchers in the same
  process would collide in `sys.modules`; this is precisely the reason the
  SQL Server skill's server module is named differently (see the top-level
  collision note above).
- Prefer pointing an MCP client straight at
  `skills/claude-skills/rdl-generation/scripts/mcp_server.py` over the
  `mcp/` launcher — the module docstring in `mcp_server.py` itself recommends
  this, since it avoids adding another layer of `sys.path` mutation.

---

## `sqlserver_schema_server.py`

### What it is

Connects to an on-premises SQL Server and reads its schema: databases,
schemas, tables/views with row counts, column/key/relationship/index detail,
programmability (procedures/functions/views) with T-SQL bodies, and a single
capped read-only ad-hoc query. It also has a two-stage offline path — build a
portable schema-digest JSON from a live connection, then turn that digest
into a per-database "skill pack" of markdown on a machine with no database
access at all. It solves the problem of grounding an agent's SQL/reporting
work in a real schema without ever giving it write access or exposing raw
sensitive data.

### Standalone or needs backend?

Runs as-is, but with two real external dependencies: the `pyodbc` PyPI
package, and a Microsoft ODBC driver for SQL Server installed on the machine
(driver selection is automatic — highest installed
`"ODBC Driver NN for SQL Server"` wins, falling back to the legacy `"SQL
Server"` driver; see `connection.py`'s `_DRIVER_PREFERENCE`). No backend
(`app.*`) import anywhere.

`mcp/sqlserver_schema_server.py` (repo root) is a thin launcher: it inserts
`skills/claude-skills/sql-server-schema/scripts/` onto `sys.path` and
re-exports from that directory's `sqlserver_schema_mcp.py`. The real server
imports five plain sibling modules in that folder: `connection.py`,
`catalog.py`, `guardrails.py`, `analyze.py`, `skillgen.py`. `sqlglot` is an
optional dependency used by `guardrails.classify_statement` for precise
SQL-statement classification; without it, classification falls back to a
stricter regex-only mode (an unparseable statement is refused, not allowed
through).

Authentication is **Windows Integrated Auth only** — the process
authenticates as whoever launched it (Kerberos or NTLM), using their existing
session. There is no password parameter anywhere in the API. A service
running under a different account authenticates as that account, not the
interactive user, so this must run in the user's own logon session.

### Tools

| Tool | Parameters | Returns (key fields) | Description |
| --- | --- | --- | --- |
| `mssql_list_odbc_drivers()` | none | `{installed: [str], would_use: str}` | Lists ODBC drivers installed on this machine and which one connection calls will actually use. First stop when a connection fails with "Data source name not found." |
| `mssql_test_connection(server: str = "", database: str = "", include_login: bool = False)` | `server`/`database` override the `SQLSERVER_MCP_SERVER`/`SQLSERVER_MCP_DATABASE` env vars for this call. `include_login`: include the Windows login name (withheld by default — an identifiable AD account name). | `{driver, encrypt_mode, database, product_version, edition, collation, auth_scheme, integrated_auth_confirmed: bool, max_rows, isolation, [login_name], [missing_permissions], [hint]}` | Proves Windows Integrated Auth reaches the server. `auth_scheme` of `KERBEROS` or `NTLM` confirms real integrated auth happened. Needs `VIEW SERVER STATE`; a null `auth_scheme` with `missing_permissions` set means "cannot confirm," not "failed." |
| `mssql_list_databases(server: str = "", include_system: bool = False)` | as named | `{databases: [...]}` | Lists databases this Windows identity can actually open, filtered by `HAS_DBACCESS` (real permission, not everything on the instance). |
| `mssql_list_schemas(database: str = "", server: str = "")` | as named | `{schemas: [...]}` | Lists non-system schemas with object counts. |
| `mssql_list_tables(database: str = "", schema: str = "", name_like: str = "", include_views: bool = True, limit: int = 500, offset: int = 0)` | `name_like`: a T-SQL `LIKE` pattern, e.g. `"Claim%"`. `limit`/`offset`: page size and skip count. | `{tables, total, returned, offset, truncated}` | Paginated table/view inventory with row counts. Deliberately paginated — a large OLTP database can exceed several thousand tables. |
| `mssql_describe_table(table: str, database: str = "")` | `table`: optionally schema-qualified, e.g. `"dbo.Claim"`. | `{found, columns: [{..., sensitive: bool, sensitivity}], primary_key, indexes, ...}` (or `{ok: False, error}`) | Columns (type, nullability, defaults, identity/computed definitions, `MS_Description` extended property), keys, both directions of every relationship, indexes. Columns matching sensitivity heuristics are flagged via `guardrails.classify_column_sensitivity`. |
| `mssql_list_relationships(database: str = "", infer: bool = True)` | `infer`: currently informational only — see note below. | `{relationships, declared: int, note: str}` | Returns the declared foreign-key join graph via `analyze.build_join_graph(..., infer=False)`. Note: despite the `infer` parameter, the join-graph call always passes `infer=False`; the returned `note` field says inference needs full column detail and points to `mssql_build_schema_digest` instead. |
| `mssql_list_indexes(table: str, database: str = "")` | as named | `{table, indexes: [...]}` (or `{ok: False, error}`) | Key columns, included columns, filter predicates for one table's indexes. |
| `mssql_table_stats(database: str = "", schema: str = "", top: int = 50)` | as named | `{tables, approximate: True, source: "sys.dm_db_partition_stats"}` | Row counts and sizes for the largest tables, read from partition metadata rather than `COUNT(*)` (no full scans). Numbers are approximate under concurrent writes. |
| `mssql_list_programmability(database: str = "", kind: str = "", schema: str = "", name_like: str = "")` | `kind`: one of `procedure`, `scalar_function`, `inline_function`, `table_function`, `view`; empty for all. | `{objects: [...]}` | Lists stored procedures, functions, and views. |
| `mssql_get_definition(name: str, database: str = "", max_chars: int = 40000)` | as named | `{found, ...}` (or `{ok: False, error}`) | Returns one module's T-SQL body, truncated to `max_chars`. Used to trace report lineage through a stored procedure to base tables. |
| `mssql_run_query(sql: str, database: str = "", max_rows: int = 200)` | `sql`: a single SELECT statement (or `WITH ... SELECT`). `max_rows`: capped by the server-side ceiling (see below). | `{columns, rows, returned, max_rows, truncated, masked_columns, classified_by}` (or `{ok: False, error, kind, classified_by}` if refused) | Runs one read-only SELECT. Refused before reaching the server unless `guardrails.classify_statement` verdicts it allowed. Binary columns are reported by size, not value; sensitive-named columns are masked to a shape descriptor via `guardrails.redact_value`. |
| `mssql_build_schema_digest(database: str = "", server: str = "", schema: str = "", include_views: bool = False, include_routines: bool = False, out_path: str = "", allow_in_repo: bool = False)` | as named | `{path, tables: int, relationships: int, schemas: [...]}` | Reads a whole database's schema and writes a portable digest JSON — the seam between a live server and the offline generator; diffable across runs to detect schema drift. Written outside the repo by default (see artifact-path guardrail below). |
| `mssql_generate_skill(digest_path: str, out_dir: str = "", dry_run: bool = False, allow_in_repo: bool = False)` | as named | dict from `skillgen.generate_skill_pack` (file list, sizes, etc.) | Turns a schema digest into a loadable markdown skill pack. Deterministic templating — no model call, no network. Use `dry_run=True` first to preview without writing. |
| `mssql_get_reference(topic: str = "")` | `topic`: empty to list available topics. | `{topics: [...]}` or `{topic, content}` or `{ok: False, error}` | Returns one of the skill's nine reference docs for an agent with no filesystem access. |
| `main()` | none | none | Calls `_check_mcp_shadowing()` then serves over stdio via `_mcp.run()`. `SystemExit` if `fastmcp` is not installed. |

Every tool body is wrapped by `_envelope()`, which converts any exception
into `{"ok": False, "error": ..., "kind": ...}` rather than letting a raw
traceback reach the MCP client: `PermissionError` becomes `kind: "refused"`,
`connection.ConnectionError_` becomes `kind: "connection"`, anything else is
passed through `conn_mod.explain_odbc_error` and returned as `kind: "error"`.

### Install / run

```bash
pip install pyodbc fastmcp sqlglot   # sqlglot optional but recommended
python mcp/sqlserver_schema_server.py
```

Environment variables (all defined in `connection.py`, none of them a
credential):

| Variable | Purpose | Default |
| --- | --- | --- |
| `SQLSERVER_MCP_SERVER` | Target server (`HOST`, `HOST\INSTANCE`, or `HOST,PORT`) | required (or pass `server=`) |
| `SQLSERVER_MCP_DATABASE` | Target database | `master` |
| `SQLSERVER_MCP_DRIVER` | Force a specific ODBC driver name | auto-picked |
| `SQLSERVER_MCP_ENCRYPT` | `auto` \| `strict` \| `off` | `auto` |
| `SQLSERVER_MCP_LOGIN_TIMEOUT` | Login timeout, seconds | `10` |
| `SQLSERVER_MCP_QUERY_TIMEOUT` | Query timeout, seconds | `30` |
| `SQLSERVER_MCP_MAX_ROWS` | Row cap ceiling, clamped to at most 1000 | `100` |
| `SQLSERVER_MCP_ISOLATION` | Transaction isolation level | `READ UNCOMMITTED` |
| `SQLSERVER_MCP_READONLY_INTENT` | Set `ApplicationIntent=ReadOnly` | off |
| `SQLSERVER_MCP_ALLOWED_SERVERS` | Comma-separated allowlist; connecting to a server outside it is refused | unset (no allowlist) |
| `SQLSERVER_MCP_ARTIFACT_DIR` | Where digests/skill packs are written by default | `%LOCALAPPDATA%\sqlserver-schema-mcp` (or `$XDG_DATA_HOME`/`~/.local/share`) |
| `SQLSERVER_MCP_ALLOW_REPO_WRITES` | Referenced by convention; the actual per-call gate is the `allow_in_repo` tool parameter | n/a |

### Worked example

The repository's own `.mcp.json` (repo root) configures it this way:

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "python",
      "args": [
        "skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"
      ],
      "env": {
        "SQLSERVER_MCP_SERVER": "${SQLSERVER_MCP_SERVER}",
        "SQLSERVER_MCP_DATABASE": "${SQLSERVER_MCP_DATABASE:-master}",
        "SQLSERVER_MCP_MAX_ROWS": "100"
      }
    }
  }
}
```

`.vscode/mcp.json` does the same but prompts interactively for server/database
via VS Code's `inputs`:

```json
{
  "inputs": [
    {"type": "promptString", "id": "sqlserver", "description": "SQL Server host, or HOST\\INSTANCE, or HOST,PORT"},
    {"type": "promptString", "id": "sqldb", "description": "Database to connect to"}
  ],
  "servers": {
    "sql-server-schema": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"],
      "cwd": "${workspaceFolder}/skills/claude-skills/sql-server-schema/scripts",
      "env": {
        "SQLSERVER_MCP_SERVER": "${input:sqlserver}",
        "SQLSERVER_MCP_DATABASE": "${input:sqldb}"
      }
    }
  }
}
```

Direct in-process call:

```python
from sqlserver_schema_mcp import mssql_describe_table
result = mssql_describe_table("dbo.Claim", database="OrdersDW")
```

### Safety / guardrails

- **Windows Integrated Auth only.** No password parameter exists anywhere in
  the API. `connection.assert_no_credentials` scans every built connection
  string for `pwd`/`password`/`uid`/`user id` and raises `ConnectionError_`
  if found — a coded invariant, not just a convention. `Trusted_Connection=yes`
  is always emitted; `UID`/`PWD` never are.
- **Read-only, in two independent layers.** `guardrails.classify_statement`
  regex-vetoes `INSERT/UPDATE/DELETE/MERGE/TRUNCATE/DROP/CREATE/ALTER/GRANT/
  REVOKE/DENY/BACKUP/RESTORE/SHUTDOWN/RECONFIGURE/WAITFOR/EXEC/
  sp_executesql/xp_*/most sp_*/SELECT...INTO/OPENROWSET/OPENDATASOURCE/
  OPENQUERY/BULK INSERT/USE` regardless of what sqlglot concludes, and demands
  exactly one statement rooted at `SELECT` or a `WITH` feeding a `SELECT`.
  But the actual guarantee is underneath that: `connection.connection()` is a
  context manager that opens every connection with `autocommit=False` and
  unconditionally calls `handle.rollback()` in its `finally` block, so even a
  classifier that is fooled cannot leave a write committed.
- **Sensitivity masking.** `guardrails.classify_column_sensitivity` matches
  column names (normalized for camelCase/snake_case) against patterns for
  SSN, national ID, payment card, passport, driver's license, patient
  record/PHI, credentials, DOB, person name, contact info, address, member
  ID, demographic, and financial-compensation categories. Categories in
  `HARD_DENY` (ssn, national_id, payment_card, passport, drivers_license,
  patient_record, credential) are never returned by value.
  `mssql_run_query` and `mssql_describe_table` both apply this;
  `guardrails.redact_value` replaces a sensitive value with a shape
  descriptor (`{masked: True, len, shape}` — digits become `9`, letters
  become `a`) rather than the value itself.
- **No `execute_sql`, no write tool, no flag that unlocks one.** This is
  structural, not policy: the tool surface simply does not define such a
  function.
- **Artifact writes stay out of the repository.**
  `guardrails.resolve_artifact_path` refuses unconditionally to write into a
  repo's `skills/`, `mcp/`, or `docs/` top-level folders (these are what get
  committed), and refuses any other path inside a git work tree that git
  would not ignore, unless `allow_in_repo=True` and the path is genuinely
  gitignored. Default artifact root is `%LOCALAPPDATA%\sqlserver-schema-mcp`
  (or equivalent), never inside this repository.
- **Row cap enforced server-side**, not just as a default: `max_rows_ceiling()`
  clamps `SQLSERVER_MCP_MAX_ROWS` to at most 1000 regardless of what is
  configured, and `clamp_rows()` further clamps any per-call `max_rows`
  argument to that ceiling.
- **TLS defaults sensibly for on-prem self-signed certs.** `encrypt="auto"`
  (the default) sets `Encrypt=yes;TrustServerCertificate=yes` only on ODBC
  Driver 18+ (which changed its own default to validate the certificate
  chain); older drivers get no explicit encryption flags, matching SSMS/
  sqlcmd behavior there. `"strict"` validates the certificate; `"off"`
  disables encryption entirely for legacy servers.
- **READ UNCOMMITTED by default**, with a 5-second lock timeout
  (`_apply_session_settings`), so a schema/profiling read on a live
  production database cannot block writers. Every result that depends on it
  reports `approximate: true` (see `mssql_table_stats`).
- **Server allowlist.** If `SQLSERVER_MCP_ALLOWED_SERVERS` is set,
  `resolve_server()` refuses to connect to anything not on the (comma
  separated) list.
- **No raw server/database names in errors.**
  `redact_connection_string()` replaces `SERVER=`/`DATABASE=` values with a
  12-character SHA-256 hash before any connection string appears in an error
  message.

### Gotchas

- The `mcp`/`fastmcp` package-name collision described above; guarded by
  `_check_mcp_shadowing()` in `sqlserver_schema_mcp.py`, called at the top of
  `main()`.
- Two skills' `scripts/` folders both getting `mcp_server.py` module names
  would collide in `sys.modules`; this is why this file is
  `sqlserver_schema_mcp.py`, not `mcp_server.py` (explicit module docstring
  note, lines 15–20 of the real server).
- `pyodbc` is imported lazily (`_require_pyodbc()` in `connection.py`), so
  the module — and its unit tests — import cleanly even with no ODBC driver
  present; the error only surfaces on first actual connection attempt, with
  an explicit `pip install pyodbc` message (and a note that Python 3.13
  may need MSVC build tools if no prebuilt wheel is available).
- A named SQL instance and an explicit port are mutually exclusive in
  `normalize_target()` — `HOST\INSTANCE` and `HOST,PORT` cannot both be
  supplied, because a named instance is resolved to its port via the SQL
  Browser service (UDP 1434), which is a different mechanism entirely.
- `mssql_list_relationships`'s `infer` parameter does not actually toggle
  inference in the current implementation — the join graph is always built
  with `infer=False`; the returned `note` string points callers at
  `mssql_build_schema_digest` for genuinely inferred relationships.
- `explain_odbc_error()` maps common ODBC SQLSTATEs (`IM002` driver missing,
  `28000` login failure, `08001` unreachable, `HYT00` timeout, `01000` TLS
  negotiation) to a specific next action, worth reading before treating a
  connection failure as opaque.

---

## `report_studio_server.py`

### What it is

The agent-facing tool surface for a deterministic Power BI report generation
pipeline. The design principle stated in the module docstring: "the LLM
proposes, a deterministic oracle disposes" — the calling IDE's own model
designs a report as an `IRWorkbook` JSON document, and this server
schema-validates and renders it locally with no cloud call and no API key of
its own. It also wraps a fully deterministic SSRS-`.rdl`-to-PBIP converter
that needs no LLM at all, a batch runner over a manifest, and a Power BI
Desktop schema-compatibility fixer for PBIP output.

### Standalone or needs backend?

**Not runnable as-is from this repository.** Every tool function does its
real import inside the function body (not at module load time), from
`app.core.*` — a package tree that lives in the source repository this file
was copied from, not in this one. Confirmed missing imports, one per tool:

- `list_grounding`: `from app.core.report_studio.grounding import GroundingSources`, `from app.core.report_studio.pipeline import _grounding_m_queries`, `from app.core.report_studio.schema import build_grounded_schema`
- `get_ir_schema` (via `_resolve_ir_schema_doc`): falls back to `from app.core.parser.prompt_to_ir import _load_template` only if `prompts/sophia-ir.md` is not found on disk
- `validate_ir`: `from app.core.report_studio.render import coerce_ir`
- `render_report`: `from app.core.report_studio.render import render_ir_to_pbip`
- `generate_from_rdl`: `from app.core.report_studio.render import render_rdl_to_pbip`
- `batch_generate`: `from app.core.report_studio.batch import run_batch`
- `fix_pbip`: `from app.core.pbip.fixer import PBIPFixer`

None of `app`, `app.core`, or any submodule under it exists in this
repository's tree. `fastmcp` itself is optional (`try/except ImportError`
around the module-level `FastMCP("report-studio")` instantiation) and does
not need to be installed just to read/import this file — but calling any
tool function still fails on the `app.core.*` import inside it.

To make it runnable, either work from the source repository directly, or
vendor the specific `app.core.*` modules listed above (see
`docs/minimal-backend-keep-set.md` in this repository for the documented
keep-set of backend files this would require).

### Tools

| Tool | Parameters | Returns (key fields) | Description |
| --- | --- | --- | --- |
| `list_grounding(rdl_paths: List[str])` | `rdl_paths`: paths to existing SSRS `.rdl` reports. | `{tables: [{name, columns: [{name, data_type}], measures: [name]}], m_queries: {table: m_query}, errors: [str]}` | Parses each SSRS `.rdl` and returns authoritative tables/columns/measures plus native-SQL M-queries reusing the source connection, so a model designs against real names instead of inventing them. Missing paths are recorded in `errors`, not raised. |
| `get_ir_schema()` | none | `str` | Returns the `IRWorkbook` JSON contract markdown. Resolved from `prompts/sophia-ir.md` at either `parents[3]` or `parents[2]` of this file (repo-root `prompts/` or `backend/prompts/`), falling back to `app.core.parser.prompt_to_ir._load_template()` if neither file exists. |
| `validate_ir(ir_json: str)` | `ir_json`: the IRWorkbook as JSON text (a fenced ` ```json ` block is accepted). | `{valid: bool, error: str\|None, name, datasource_count: int, visual_count: int}` | Cheap pre-check: normalizes and validates an IRWorkbook via `coerce_ir` without rendering it. |
| `render_report(ir_json: str, out_dir: str, project_name: Optional[str] = None)` | as named | `{success, status, output_zip, build_dir, project_name, error, warnings, logs}` (`.to_dict()` of the render result) | Renders an agent-designed IRWorkbook to a packaged Power BI project (PBIP) via the deterministic pipeline. No LLM/cloud call here — the model already ran, in the host IDE, before this tool is called. |
| `generate_from_rdl(rdl_path: str, out_dir: str, paginated: bool = False)` | `paginated`: re-point to paginated RDL instead of interactive PBIP. | same shape as `render_report` | Converts an existing SSRS `.rdl` straight to PBIP, fully deterministic and zero-LLM — reuse or re-point an existing report with no model involved. |
| `batch_generate(manifest_json: str, out_dir: str)` | `manifest_json`: JSON list of items, each `{kind: "rdl", rdl, [paginated], [out]}` or `{kind: "ir", ir \| ir_file, [project_name], [out]}`. `out_dir`: root; each item renders to `out_dir/<slug>/`. | `{total, succeeded, failed, out_dir, report_path, results: [...]}` (or `{total: 0, succeeded: 0, failed: 0, error}` on a manifest-level failure) | Renders many reports from a manifest in one offline run, fail-soft per item — a bad item becomes one failed result and the run continues; invalid JSON or an invalid manifest shape returns an error dict rather than raising. |
| `fix_pbip(pbip_dir: str, report_md: Optional[str] = None)` | `pbip_dir`: PBIP directory (contains `*.pbip`, `*.Report/`, `*.SemanticModel/`). `report_md`: optional path to write a compatibility report markdown. | `{ok, files_changed: [str], fixes: [str], error: str\|None}` | Applies Power BI Desktop Feb 2026 schema-compatibility fixes to a PBIP in place via `PBIPFixer`. Returns `ok: False` (never raises) if `pbip_dir` doesn't exist or the fixer throws. |
| `offline_violations()` | none | `List[str]` | Returns which cloud-AI env vars are currently set (from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_FOUNDRY_API_KEY`, plus a non-`none` `AI_PROVIDER`) — empty when clean. Not itself an MCP tool (no `@_tool` decorator); a plain helper used by the no-egress guard. |
| `main()` | none | none | Calls `_enforce_offline_if_requested()`, then serves over stdio via `_mcp.run()`. `SystemExit` if `fastmcp` is not installed. |
| `get_mcp_app()` | none | the FastMCP ASGI app, or `None` | For mounting under FastAPI's SSE transport at e.g. `/mcp/report-studio`; not an MCP tool itself. |

### Install / run

Cannot actually be run from this repository (see above). As documented in its
own module docstring, in the source repository it is:

```bash
pip install fastmcp   # optional, only for the stdio server
python -m app.mcp.report_studio_server
```

Environment variables read (offline-enforcement only):

| Variable | Purpose |
| --- | --- |
| `ENFORCE_NO_EGRESS` | When `1`/`true`/`yes`/`on`, `main()` refuses to start if any cloud-AI env var below is set |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_FOUNDRY_API_KEY` | Checked by `offline_violations()`; presence of any (non-empty, non-whitespace) triggers a violation |
| `AI_PROVIDER` | Any value other than empty or `none` (case-insensitive) is also reported as a violation |

### Worked example

Direct in-process call (from the source repository, where `app.core.*`
exists):

```python
from app.mcp.report_studio_server import render_report, list_grounding

grounding = list_grounding(["reports/SalesByRegion.rdl"])
ir_json = '{"name": "SalesByRegion", "datasources": [...], "worksheets": [...]}'
result = render_report(ir_json, "out/report1", project_name="SalesByRegion")
# {"success": True, "status": "rendered", "output_zip": "out/report1/SalesByRegion.zip", ...}
```

MCP client config, once vendored into a runnable location:

```json
{
  "mcpServers": {
    "report-studio": {
      "command": "python",
      "args": ["-m", "app.mcp.report_studio_server"],
      "cwd": "/path/to/source-repo/backend"
    }
  }
}
```

### Safety / guardrails

No cloud AI call is made anywhere in this file's own logic — the only model
in the loop is the host IDE's, before this server is ever invoked. For
air-gapped installs, `ENFORCE_NO_EGRESS=1` makes `main()` call
`_enforce_offline_if_requested()`, which raises `SystemExit` naming every
violating env var if any cloud-AI key/provider setting is present — this is a
defense-in-depth assertion the operator opts into, not something the
render/convert tools check on every call.

### Gotchas

- All `app.core.*` imports are deferred to inside each function body, so
  `import app.mcp.report_studio_server` (or, in this repo, `from
  mcp.report_studio_server import ...`, which will fail earlier since the
  module itself isn't here) succeeds even without the backend — the failure
  only surfaces when a tool is actually called.
- `get_ir_schema`'s fallback chain checks two hardcoded relative paths
  (`parents[3]/prompts/sophia-ir.md` and `parents[2]/prompts/sophia-ir.md`)
  before falling back to the backend loader — a vendoring effort needs to
  either supply one of those files or the `app.core.parser.prompt_to_ir`
  module.

---

## `pbi_refine_server.py`

### What it is

Seven tools (an eighth, `export_visual_png`, delegates to one of them) for
inspecting and patching an already-generated PBIP folder — the agentic
refinement loop that runs after a conversion job has produced output: read
back the semantic model schema, flag stub/TODO measures, read and patch one
visual's PBIR JSON, render a schematic page preview, and structurally diff a
build against a baseline.

### Standalone or needs backend?

**Not runnable as-is from this repository, but only partially** — most tools
are pure stdlib (`json`, `pathlib`, `re`) and work with zero external
imports; only two tools import from the missing backend:

- `get_schema` (TMDL branch only): `from app.core.validation.structural import relationship_inventory` — only reached when the model is in TMDL format (`database.tmdl` present) rather than legacy TMSL (`model.bim`)
- `export_page_png` (and therefore `export_visual_png`, which delegates to it): `from app.core.preview.layout_preview import find_report_dir, render_page_preview`

`validate_dax`, `get_schema` (TMSL/`model.bim` branch), `get_visual_config`,
`apply_patch`, `compare_visuals`, and `structural_compare` have no backend
dependency at all and would run today if this file were imported directly
(they only need the standard library, plus the optional third-party
`jsonpatch` package for `apply_patch` and optional `fastmcp`/`Pillow` for the
PNG path). `fastmcp` is optional exactly as in the other two backend-style
servers (`try/except ImportError` around `FastMCP("pbi-refine")`).

### Tools

| Tool | Parameters | Returns (key fields) | Description |
| --- | --- | --- | --- |
| `validate_dax(build_dir: str)` | `build_dir`: PBIP build directory. | `{tables: [{name, columns, measures: [{name, expression, has_stub}]}], errors: [str]}` | Parses the semantic model (TMSL `model.bim` via `_validate_dax_bim`, or TMDL `database.tmdl` via `_validate_dax_tmdl`) and returns a schema summary, flagging measures whose expression contains `// TODO` or starts with `/*` as stub measures (also added to `errors`). Returns `{"tables": [], "errors": [...]}` if no semantic model is found. |
| `get_schema(build_dir: str)` | as named | `{tables: [{name, column_count, measure_count}], relationships: [{from, to, cross_filter}]}` (or with `error`) | Lightweight schema summary. TMSL branch reads `model.bim` relationships directly (`crossFilteringBehavior`); TMDL branch reparses via `_validate_dax_tmdl` for table/column/measure counts and (backend-dependent) `relationship_inventory` for relationships. |
| `get_visual_config(build_dir: str, page_name: str, visual_name: str)` | as named | `{page, visual, config: {...}, path}` (or `{error}`) | Reads one visual's PBIR `visual.json`, resolved at `<build_dir>/<Project>.Report/definition/pages/<page_name>/visuals/<visual_name>/visual.json`; falls back to a glob (`*{page_name}*/visuals/*{visual_name}*/visual.json`) for case-insensitive/partial matches. |
| `apply_patch(build_dir: str, file_rel_path: str, patch: List[Dict])` | `patch`: an RFC 6902 JSON Patch document. | `{status: "ok", path}` or `{status: "error", detail}` | Applies a JSON Patch to one file. Reads original bytes first; on any failure (missing `jsonpatch` package, bad patch, file error) restores the original bytes and returns an error rather than leaving a partially-patched file. Requires `pip install jsonpatch` — checked at call time with a specific install-hint error, not import time. |
| `export_page_png(build_dir: str, page_name: str, out_path: str = "")` | `out_path`: defaults to `<build_dir>/_preview/<page_name>.png`. | `{status: "ok", path}` or `{status: "not_available", reason}` or `{status: "error", detail}` | Renders a schematic PNG of a page's PBIR layout (positions, sizes, titles, colours) via `app.core.preview.layout_preview` — a layout render, not a true Power BI engine render, but sufficient for comparing position/size/colour against a source screenshot. `not_available` covers both a missing Pillow install and a missing `page.json`. |
| `export_visual_png(build_dir: str, page_name: str = "", visual_name: str = "")` | `visual_name` accepted for API compatibility only. | same as `export_page_png` | Delegates entirely to `export_page_png(build_dir, page_name)` — single-visual cropping is not implemented; the whole page renders. Returns `{"status": "error", "detail": "page_name is required"}` if `page_name` is empty. |
| `compare_visuals(build_dir: str, baseline_dir: str)` | as named | `{diffs: [{path, visual_type, type_mismatch: {build, baseline}}], mode: "structural", visual_count: int}` | Structural diff of every `visual.json` under `build_dir` against the same relative path under `baseline_dir`, currently comparing only `visualType`. Pixel-level diff is noted as deferred until PNG export matures. Files that fail to parse as JSON are silently skipped. |
| `structural_compare(build_dir: str)` | as named | `{page_count, pages: [{name, visual_count}], table_count, measure_count, semantic_format: "tmsl"\|"tmdl"\|"none", issues: [str]}` | L1 structural inspection: page/visual counts, missing `visual.json` files, visuals missing `name` or `visualType`, table/measure counts from whichever semantic model format is present (TMDL measure counting is not implemented — always 0 in that branch), and a list of structural issues found. |
| `get_mcp_app()` | none | FastMCP ASGI app, or `None` | For mounting under FastAPI SSE transport at `/mcp/refine`; not an MCP tool itself. |

Internal (non-tool) helpers worth knowing when reading the source:
`_find_report_dir` (first `*.Report/` dir directly inside `build_dir`),
`_find_model_bim` (first `model.bim` anywhere under `build_dir`, via
`rglob`), `_find_tmdl_database` (first `database.tmdl` via `rglob`),
`_tmdl_table_count` (counts `.tmdl` files under
`*.SemanticModel/definition/tables/`), `_load_json`.

### Install / run

Cannot run standalone from this repository as an MCP server because the
module itself (`app.mcp.pbi_refine_server`) does not exist here — only the
reference copy at `mcp/pbi_refine_server.py`, which imports two backend
modules for two of its eight tools (see above). To exercise the
backend-independent tools today, copy the file and import the functions
directly, or run it from the source repository:

```bash
pip install fastmcp jsonpatch pillow   # jsonpatch for apply_patch, pillow for PNG export
python -m app.mcp.pbi_refine_server
```

No environment variables are read by this file.

### Worked example

Direct in-process call — this part works with zero backend, from any Python
that has this file on its path:

```python
from app.mcp.pbi_refine_server import structural_compare, apply_patch

result = structural_compare("/path/to/build/MyProject")
# {"page_count": 3, "pages": [{"name": "Page1", "visual_count": 5}, ...],
#  "table_count": 12, "measure_count": 40, "semantic_format": "tmsl", "issues": []}

patch = [{"op": "replace", "path": "/visual/title/text", "value": "Revenue by Region"}]
apply_patch(
    "/path/to/build/MyProject",
    "MyProject.Report/definition/pages/Page1/visuals/Chart1/visual.json",
    patch,
)
# {"status": "ok", "path": "MyProject.Report/definition/pages/Page1/visuals/Chart1/visual.json"}
```

MCP client config, once vendored:

```json
{
  "mcpServers": {
    "pbi-refine": {
      "command": "python",
      "args": ["-m", "app.mcp.pbi_refine_server"],
      "cwd": "/path/to/source-repo/backend"
    }
  }
}
```

### Safety / guardrails

No cloud call anywhere in this file. `apply_patch` is the only tool that
mutates anything, and it does so with a read-original/write-then-rollback
pattern: original bytes are captured before the patch attempt, and restored
verbatim on any exception, so a bad patch never leaves a half-written file.
Every tool returns a status/error dict rather than raising on the failure
paths tested in the source (missing files, unparseable JSON, missing
`jsonpatch`).

### Gotchas

- `get_schema` and `structural_compare` silently return `measure_count: 0`
  for TMDL-format models — TMDL measure counting via text parsing is
  explicitly marked "not implemented yet" in the source comment, so a TMDL
  build will always undercount measures in these two tools even though
  `validate_dax` (which does implement TMDL measure parsing via regex) will
  show them correctly.
- `export_visual_png`'s `visual_name` parameter is accepted but not actually
  used to crop or select a single visual — the whole page renders every
  time, per the docstring's own "future refinement" note.
- `compare_visuals` matches visuals purely by relative path
  (`root.rglob("visual.json")` then path-relative key) between the two
  directories, so renaming a page or visual folder between the build and the
  baseline will show up as an add/remove rather than a rename/diff.
- The TMDL parser inside `_validate_dax_tmdl` is a hand-rolled regex/line
  state machine (not a real TMDL grammar parser) — multi-line measure
  expressions are collected between a `measure` line and the next
  non-indented or `///`-prefixed line, which is a reasonable approximation
  but not a formal parse.

---

## Common to all four

- **Optional `fastmcp` dependency.** All four files wrap the module-level
  `FastMCP(...)` instantiation in `try/except ImportError`, and gate `_tool`
  (the tool-registration decorator) on whether that succeeded. Without
  `fastmcp` installed, every file still imports and every tool function
  still works as a plain, directly callable Python function — only `main()`
  (the stdio server entry point) is unavailable, raising `SystemExit` with an
  install hint.
- **No cloud AI call from any of the four servers themselves.**
  `report_studio_server.py` is the only one with an explicit, opt-in guard
  for it (`ENFORCE_NO_EGRESS`); the other three simply never make such a call
  in the first place.
- **Two are genuinely standalone** (`rdl_generation_server.py`,
  `sqlserver_schema_server.py`), each a thin launcher over a
  dependency-minimal script pair/set living under
  `skills/claude-skills/<skill>/scripts/`. **Two are reference copies**
  (`report_studio_server.py`, `pbi_refine_server.py`) that document a tool
  surface and design pattern extracted from a source repository's
  `backend/app/mcp/`, not a package meant to run from this repository.
- All four are designed for the same operating model: an agentic IDE (Claude
  Code, Windsurf's Cascade, VS Code with MCP support, or similar) drives the
  tools over stdio, with the IDE's own model doing the reasoning and these
  servers doing deterministic, schema-validated, or read-only-guarded file
  and database work.

## See also

- `mcp/README.md` — the original, more compact version of this reference.
- `docs/minimal-backend-keep-set.md` — the documented keep-set of
  `backend/app/core/*` files needed to actually vendor and run
  `report_studio_server.py`/`pbi_refine_server.py` outside the source
  repository.
- `skills/claude-skills/sql-server-schema/references/mcp-client-setup.md` —
  further MCP client setup guidance for the SQL Server schema server.
- `skills/claude-skills/sql-server-schema/references/read-only-guardrails.md`
  — the fuller guardrails writeup referenced from `sqlserver_schema_mcp.py`'s
  own module docstring.
