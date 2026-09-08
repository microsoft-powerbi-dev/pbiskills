# MCP servers

This folder holds four Model Context Protocol (MCP) server files. Two are
reference copies that need backend code not included here to run; two run
as-is.

| Server | Runs as-is here? | Needs |
| --- | --- | --- |
| `rdl_generation_server.py` | **Yes** | Nothing beyond this folder: a thin launcher for the real server at `../skills/claude-skills/rdl-generation/scripts/mcp_server.py`, which is itself dependency-free (Python 3.9+ stdlib only, plus optional `fastmcp` to serve over stdio) |
| `sqlserver_schema_server.py` | **Yes**, with a driver | `pip install pyodbc fastmcp`, a Microsoft ODBC driver for SQL Server, and Windows Integrated Auth to the target instance. A thin launcher for `../skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py` |
| `report_studio_server.py` | No, reference only | the source repository's `backend/app/core/*` (see below) |
| `pbi_refine_server.py` | No, reference only | the source repository's `backend/app/core/*` (see below) |

## A note on this folder's name

`fastmcp` depends on the `mcp` package from PyPI, and this folder is also
called `mcp`. Whenever the repository root is on `sys.path`, `import mcp`
resolves here instead and fastmcp fails with a confusing error raised from
inside its own internals.

Deleting `__init__.py` would not help: since PEP 420 a directory without one is
still importable as a namespace package and still shadows. Launch a server by
absolute path (`python mcp/sqlserver_schema_server.py` is safe, because
`sys.path[0]` becomes `mcp/` rather than the repository root); do not run
`python -m mcp.<name>` from the repository root, and do not put the repository
root on `PYTHONPATH`. `sqlserver_schema_server.py` carries a startup check that
detects the collision and names the fix.

This also constrains the module names inside each skill's `scripts/` folder.
Both launchers insert their skill's `scripts/` directory onto `sys.path` and
import by module name, so no two skills may both ship a `mcp_server.py`: they
would collide in `sys.modules` and one launcher would silently re-export the
other's tools. That is why the SQL Server server is called
`sqlserver_schema_mcp.py`.

## `rdl_generation_server.py` (standalone, runs as-is)

Generates and validates SSRS `.rdl` files. No backend dependency: it wraps
`rdl_builder.py` and `validate_rdl.py`, the same dependency-free scripts the
`rdl-generation` skill ships at
`../skills/claude-skills/rdl-generation/scripts/`. Run it directly:

```bash
pip install fastmcp   # optional; only needed to serve over stdio
python mcp/rdl_generation_server.py
```

Or point an MCP client straight at the real file instead of the launcher:

```json
{
  "mcpServers": {
    "rdl-generation": {
      "command": "python",
      "args": ["/absolute/path/to/skills/claude-skills/rdl-generation/scripts/mcp_server.py"]
    }
  }
}
```

Tools:

| Tool | Purpose |
| --- | --- |
| `get_json_spec_schema()` | The JSON spec `build_rdl` accepts (datasources, datasets, parameters, tables) |
| `build_rdl(spec_json, out_path, validate=True)` | Build and save an `.rdl` from a JSON spec, then immediately validate it |
| `validate_rdl_file(path, strict=False)` | Validate an existing `.rdl` file: well-formedness, namespace, element order, field/dataset references, units, header/footer placement, body height |
| `field_expression(field_name, aggregate=None)` | The exact round-trip-safe expression, e.g. `=Fields!Amount.Value` or `=Sum(Fields!Amount.Value)` |
| `list_reference_topics()` / `get_reference(topic)` | The skill's seven reference docs, retrievable by an agent with no filesystem access |
| `list_examples()` / `get_example(name)` | The five bundled, validating example `.rdl` files, same way |

Every tool is also a plain Python function, callable directly with no MCP
client at all:

```python
from mcp_server import build_rdl
result = build_rdl(spec_json, "out/SalesByRegion.rdl")
```

## `sqlserver_schema_server.py` (standalone, runs as-is)

Connects to an on-premises SQL Server from this machine and reads its schema.
A thin launcher for the real server at
`../skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py`,
which lives next to the `connection.py`, `catalog.py`, `guardrails.py`,
`analyze.py` and `skillgen.py` modules it imports as plain siblings.

```bash
pip install pyodbc fastmcp sqlglot
python mcp/sqlserver_schema_server.py
```

Or point an MCP client straight at the real file, which is what
`skills/claude-skills/sql-server-schema/references/mcp-client-setup.md`
recommends and what the repository's own `.mcp.json` does:

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "python",
      "args": ["/absolute/path/to/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"],
      "env": {"SQLSERVER_MCP_SERVER": "SQLPROD01", "SQLSERVER_MCP_DATABASE": "SalesDW"}
    }
  }
}
```

Tools:

| Tool | Purpose |
| --- | --- |
| `mssql_list_odbc_drivers()` | Which ODBC drivers this machine has, and which one would be used |
| `mssql_test_connection(server, database, include_login=False)` | Prove Windows Integrated Auth reaches the server. `auth_scheme` of KERBEROS or NTLM confirms it |
| `mssql_list_databases(server, include_system=False)` | Databases this Windows identity can actually open |
| `mssql_list_schemas(database, server)` | Non-system schemas with object counts |
| `mssql_list_tables(database, schema, name_like, include_views, limit, offset)` | Paginated table and view inventory with row counts |
| `mssql_describe_table(table, database)` | Columns, keys, both directions of every relationship, indexes, and sensitivity flags |
| `mssql_list_relationships(database, infer)` | The declared foreign-key join graph |
| `mssql_list_indexes(table, database)` | Key columns, included columns, filter predicates |
| `mssql_table_stats(database, schema, top)` | Row counts and sizes from partition metadata, never `COUNT(*)` |
| `mssql_list_programmability(database, kind, schema, name_like)` | Procedures, functions, views |
| `mssql_get_definition(name, database, max_chars)` | One module's T-SQL body |
| `mssql_run_query(sql, database, max_rows)` | A single read-only SELECT, classified and capped |
| `mssql_build_schema_digest(...)` | Write a portable schema digest JSON |
| `mssql_generate_skill(digest_path, out_dir, dry_run)` | Turn a digest into a per-database skill pack |
| `mssql_get_reference(topic)` | The skill's nine reference docs, for an agent with no filesystem access |

Every tool is also a plain, directly callable Python function:

```python
from sqlserver_schema_mcp import mssql_describe_table
result = mssql_describe_table("dbo.Claim", database="OrdersDW")
```

Safety posture, in short:

- **Windows Integrated Auth only.** No password parameter exists anywhere in
  the API, and `assert_no_credentials` fails the build if one is ever added.
  The server authenticates as whoever launched it, so it must run in the user's
  own session.
- **Read-only structurally.** No write tool, no `execute_sql`, no flag that
  unlocks one. Every connection is `autocommit=False` and unconditionally
  rolled back; every ad-hoc statement is classified first.
- **No data by default.** Only `mssql_run_query` returns rows, and columns
  matching the sensitivity heuristics come back masked to a shape descriptor.
- **Generated artifacts stay out of this repository.** Digests and packs are
  written to `%LOCALAPPDATA%` by default, and writing into `skills/`, `mcp/` or
  `docs/` is refused outright.

## `report_studio_server.py` and `pbi_refine_server.py` (reference copies)

Copied as reference material from the source repository's `backend/app/mcp/`.
Both import from that repository's `app.core.*` package tree, which is not
copied here, so they are **not runnable as-is from this folder**. Treat these
two as documentation of the tool surface and the design pattern, not a
standalone package.

If you need them to actually run, either work from the source repository
directly, or vendor the specific `app.core.*` modules each file imports (listed
below) the same way `skills/report-lineage/` and `rdl_generation_server.py`
in this folder were extracted: narrow the imports down to what each tool
actually needs, and cut every dependency on `app.db`, `app.auth`, and the
FastAPI layer.

## `report_studio_server.py`

The Windsurf/Claude-Code-facing tool surface for the source backend's deterministic
report generation. The governing idea: the agent's own model designs a
report (as an `IRWorkbook` JSON document), and this server schema-validates
and renders it locally, no cloud call, no API key. The only model in the
loop is whatever the host IDE already uses.

| Tool | Purpose |
| --- | --- |
| `list_grounding(rdl_paths)` | Inventory an SSRS estate (tables, columns with types, measures, native-SQL M-queries) so the agent designs against real names instead of inventing them |
| `get_ir_schema()` | Return the IRWorkbook JSON contract the agent must emit |
| `validate_ir(ir_json)` | Cheap pre-check: normalize and validate an IRWorkbook without rendering |
| `render_report(ir_json, out_dir, project_name=None)` | Render an agent-designed IRWorkbook to a packaged Power BI project (PBIP) |
| `generate_from_rdl(rdl_path, out_dir, paginated=False)` | Deterministic, zero-LLM conversion of an existing SSRS `.rdl` straight to PBIP (or a re-pointed paginated RDL) |
| `batch_generate(manifest_json, out_dir)` | Render many reports from a manifest in one offline run, fail-soft per item |
| `fix_pbip(pbip_dir, report_md=None)` | Apply the Power BI Desktop Feb 2026 schema-compatibility fixes to a PBIP in place |

Every tool is also a plain, directly callable Python function:

```python
from app.mcp.report_studio_server import render_report, list_grounding
result = render_report(ir_json, "out/report1")
```

Served over stdio when `fastmcp` is installed: `python -m app.mcp.report_studio_server`.

Backend modules this file imports (needed to actually run it, all under the
source repository's `backend/app/`):

- `core/report_studio/grounding.py`
- `core/report_studio/pipeline.py`
- `core/report_studio/schema.py`
- `core/report_studio/render.py`
- `core/report_studio/batch.py`
- `core/parser/prompt_to_ir.py`
- `core/pbip/fixer.py`
- transitively, whatever those modules themselves import (the full
  `core/parser/`, `core/generator/`, `core/translator/`, `core/pbip/` trees,
  and `shared/ir_models.py`)

## `pbi_refine_server.py`

Eight tools for inspecting and patching an already-generated PBIP folder,
the agentic refinement loop that runs after a conversion job
succeeds.

| Tool | Purpose |
| --- | --- |
| `validate_dax(build_dir)` | Parse the semantic model (TMSL `model.bim` or TMDL) and return a schema summary, flagging stub/TODO measures |
| `get_schema(build_dir)` | Read back the full table/column/measure schema |
| `get_visual_config(build_dir, page_name, visual_name)` | Read one visual's PBIR JSON |
| `apply_patch(build_dir, file_rel_path, patch)` | Apply an RFC-6902 JSON Patch to one file in the build |
| `export_page_png(build_dir, page_name, out_path="")` | Render a report page to PNG for visual review |
| `export_visual_png(build_dir, page_name="", visual_name="")` | Render a single visual to PNG |
| `compare_visuals(build_dir, baseline_dir)` | Diff visuals against a known-good baseline build |
| `structural_compare(build_dir)` | Structural diff of the PBIP tree against a baseline |

Direct usage:

```python
from app.mcp.pbi_refine_server import structural_compare, apply_patch
result = structural_compare("/path/to/build/MyProject")
```

Backend modules this file imports (needed to actually run it):

- `core/validation/structural.py`
- `core/preview/layout_preview.py`
- transitively, whatever those modules themselves import

## Common to all four

- Optional dependency: `fastmcp` (`pip install fastmcp`). Without it, every
  file still imports cleanly and every tool function still works when called
  directly in Python; only the stdio MCP server (`main()` / module-level
  `FastMCP` registration) is unavailable.
- None of them makes a cloud AI call. `report_studio_server.py` ships an
  explicit no-egress guard (`ENFORCE_NO_EGRESS`) that refuses to start if a
  cloud AI API key is present in the environment, for air-gapped installs.
  `sqlserver_schema_server.py` reaches only the SQL Server you point it at, and
  its skill-pack generator is deterministic templating with no model call at
  all.
- Both are designed so an agentic IDE (Windsurf's Cascade, Claude Code, or
  similar) drives the tools over stdio, with the IDE's own model doing the
  reasoning and these servers doing the deterministic, schema-validated
  file work.

## Why this is a reference copy, not a working install

Making these two files actually runnable outside the source repository
means bringing over a meaningful slice of `backend/app/core/` (the parser,
generator, translator, and PBIP-fixer subsystems), which is a bigger job than
a straight file copy and was out of scope for this pass. `docs/` in this
folder includes `minimal-backend-keep-set.md`, which documents exactly
that keep-set (the smallest slice of the backend needed to run the
deterministic conversion pipeline these tools call into) for anyone who
wants to do that extraction later.
