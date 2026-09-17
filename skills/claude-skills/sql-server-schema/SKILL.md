---
name: SQL Server Schema Analysis
description: >
  Use this skill when connecting to an on-premises SQL Server to understand a
  database you did not design, and when turning what you find into reusable
  knowledge. Covers connecting with Windows Integrated Auth over pyodbc and
  whichever ODBC driver is installed, the sys.* catalog views that answer each
  question, a breadth-then-depth interrogation order that survives a
  thousand-table database, fact and dimension classification, building a join
  graph from declared and inferred foreign keys, tracing an SSRS dataset back
  to its source tables, and generating a per-database schema skill pack from a
  portable digest. Read-only throughout: there is no write tool and no flag
  that adds one. Example requests: "connect to this SQL Server and describe the
  schema", "what tables does this RDL report actually read", "which tables join
  to Claims and how", "generate a schema skill for this database", "find the
  fact tables in this warehouse", "is it safe to share this schema summary".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
triggers:
  - sql server schema
  - connect to sql server
  - on prem sql server
  - analyze database
  - analyse database
  - describe table
  - list tables
  - database schema
  - table catalog
  - column data types
  - primary key
  - foreign key graph
  - join graph
  - fact table
  - dimension table
  - sys.tables
  - sys.columns
  - sys.foreign_keys
  - information_schema
  - row counts
  - index list
  - stored procedure body
  - pyodbc
  - odbc driver
  - trusted_connection
  - integrated auth sql
  - windows authentication sql
  - schema digest
  - schema skill
  - generate schema skill
  - database skill pack
  - what tables does this report use
  - rdl dataset tables
  - connection string database
  - redact schema
  - pii columns
---

# SQL Server Schema Analysis

Interrogate an on-premises SQL Server you did not design, using the Windows
credentials you already have, and turn what you find into knowledge an agent
can reuse.

This is the input side of the estate. For the reports sitting on top of these
tables use `skills/report-lineage/`; for writing the `.rdl` that queries them
use `skills/claude-skills/rdl-generation/`; for the report lifecycle around it
`skills/claude-skills/ssrs-report-creation/`. Once a migration target is a
`.pbip`, hand off to `skills/vendor/microsoft-fabric/`.

## When to use

- You have a report, a dataset, or a connection string, and you need to know
  what is actually in the database behind it.
- You are scoping a migration and need to know which tables matter, which are
  staging or audit noise, and which no report reads at all.
- You need a correct join between two tables and the estate's foreign keys are
  missing or untrustworthy.
- You need column names and types to write a query, and guessing is not
  acceptable.
- You want a per-database reference an agent can load in future sessions
  without reconnecting.

Do **not** use this to move or modify data. It cannot: see rule 1.

## The five rules that keep a schema sweep honest

1. **This server is read-only, structurally.** There is no write tool, no
   `execute_sql`, and no environment flag that unlocks one. Every connection is
   opened `autocommit=False` and unconditionally rolled back, and every ad-hoc
   statement is classified before it is sent. The rollback is the guarantee; the
   classifier just produces a good error message. See
   `references/read-only-guardrails.md`.

2. **Breadth before depth.** Never pull full column detail for every table in
   one pass. Go databases, then schemas, then a table list with row counts, and
   only then describe the tables that scored. A production OLTP database can
   exceed three thousand tables, and the tools are paginated for that reason.

3. **`sys.*`, never `INFORMATION_SCHEMA`.** The ANSI views cannot express
   identity columns, computed columns, included or filtered index columns, or an
   untrusted foreign key, and they truncate a routine definition at 4000
   characters. See `references/catalog-views.md`.

4. **Row counts come from `sys.dm_db_partition_stats`, filtered to
   `index_id IN (0,1)`.** Never `COUNT(*)`: that is a full scan on a table you
   know nothing about yet. The numbers are approximate under concurrent writes,
   and every payload built on them says so.

5. **A missing foreign key is not a missing relationship.** Older estates
   routinely drop constraints for load performance and keep the join graph only
   as a naming convention. Inference is on by default and every inferred edge is
   labelled, so you can see which joins the database enforces and which are a
   guess. Verify an inferred join before you trust a total built on it.

## Which question, which catalog view

| Question | Points to |
| --- | --- |
| Can I even reach this server, and as whom? | `mssql_test_connection`, then read `auth_scheme` |
| What databases can I open? | `sys.databases` filtered by `HAS_DBACCESS` |
| What tables exist, and how big? | `sys.objects` plus `sys.dm_db_partition_stats` |
| What columns, types, nullability, defaults? | `sys.columns` + `sys.types` + `sys.default_constraints` |
| Is this column an identity or computed? | `sys.identity_columns`, `sys.computed_columns` |
| What is the primary key? | `sys.key_constraints` + `sys.index_columns` |
| How do these two tables join? | `sys.foreign_keys` + `sys.foreign_key_columns` |
| What query shape was this designed for? | `sys.indexes` + `sys.index_columns` |
| What does this procedure actually read? | `sys.sql_modules.definition` |
| Did someone document this? | `sys.extended_properties`, `MS_Description` |

## Workflow

1. **Connect and confirm.** Run `mssql_list_odbc_drivers`, then
   `mssql_test_connection`. Check `auth_scheme` is `KERBEROS` or `NTLM` before
   going further. If it fails, prove it at the driver level first with
   `sqlcmd -S <server> -E -Q "SELECT SUSER_SNAME()"`.
   See `references/connection-and-auth.md`.

2. **Inventory breadth-first.** `mssql_list_databases`, `mssql_list_schemas`,
   then `mssql_list_tables` with a schema filter and paging. Then
   `mssql_table_stats` to see where the volume is.

3. **Describe only what matters.** `mssql_describe_table` on the tables the
   inventory surfaced. Note the sensitivity flags on the columns.

4. **Build the join graph.** `mssql_list_relationships` for declared foreign
   keys. See `references/catalog-views.md` for what the flags mean, and
   `references/schema-digest-format.md` for how inference works.

5. **Scope to what the reports actually use.** Wire this server's
   `mssql_get_definition` into `reportlineage.builder.build_estate` as a
   `proc_body_lookup` so lineage traces through stored procedures instead of
   stopping at the `EXEC`. This is the highest-value integration in the skill.
   See `references/rdl-to-tables.md`.

6. **Emit a digest.** `mssql_build_schema_digest`, or `cli.py digest`. This is
   the reviewable, portable artifact everything downstream reads.
   See `references/schema-digest-format.md`.

7. **Review before generating.** Read the digest. Check
   `coverage`, the role classifications, and which columns were flagged.
   See `references/pii-and-public-repo-safety.md`.

8. **Generate the pack.** `mssql_generate_skill` or `cli.py generate`, with
   `--dry-run` first. See `references/skill-pack-format.md`.

## Bundled assets

### `scripts/`

| Script | Use |
| --- | --- |
| `connection.py` | Driver selection, Windows-auth connection strings, the rollback-always connection context |
| `catalog.py` | The `sys.*` queries, as pure functions taking an open connection |
| `guardrails.py` | Statement classification, sensitivity rules, and the refusal to write into a repository |
| `analyze.py` | Join graph, role classification, conventions, and digest assembly |
| `skillgen.py` | Digest to skill pack, deterministic templating only |
| `sqlserver_schema_mcp.py` | The MCP server over all of the above |
| `cli.py` | The same functions from a terminal, with no MCP host |

`skillgen.py`, `analyze.py`, and `guardrails.py` are standard library only and
import no database driver, so a pack can be regenerated and reviewed on a
machine with no ODBC driver at all. Only `connection.py` and `catalog.py` need
`pyodbc`.

### MCP server

```bash
pip install pyodbc fastmcp sqlglot
python skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py
```

| Tool | Purpose |
| --- | --- |
| `mssql_list_odbc_drivers()` | Which ODBC drivers exist here, and which would be used |
| `mssql_test_connection(server, database, include_login)` | Prove Windows auth works, and report `auth_scheme` |
| `mssql_list_databases(server, include_system)` | Databases this identity can actually open |
| `mssql_list_schemas(database, server)` | Non-system schemas with object counts |
| `mssql_list_tables(database, schema, name_like, include_views, limit, offset)` | Paginated table and view inventory with row counts |
| `mssql_describe_table(table, database)` | Columns, keys, both directions of relationships, indexes, sensitivity flags |
| `mssql_list_relationships(database, infer)` | The declared join graph |
| `mssql_list_indexes(table, database)` | Key columns, included columns, filter predicates |
| `mssql_table_stats(database, schema, top)` | Row counts and sizes, largest first |
| `mssql_list_programmability(database, kind, schema, name_like)` | Procedures, functions, views |
| `mssql_get_definition(name, database, max_chars)` | One module's T-SQL body |
| `mssql_run_query(sql, database, max_rows)` | A single read-only SELECT, guarded and capped |
| `mssql_build_schema_digest(...)` | Write the portable digest artifact |
| `mssql_generate_skill(digest_path, out_dir, dry_run)` | Digest to skill pack |
| `mssql_get_reference(topic)` | This skill's reference docs, for an agent with no filesystem |

Every tool is also a plain Python function, callable with no MCP client:

```python
from sqlserver_schema_mcp import mssql_describe_table
result = mssql_describe_table("dbo.Claim", database="OrdersDW")
```

The launcher at `mcp/sqlserver_schema_server.py` re-exports them all.
`references/mcp-client-setup.md` has configs for Claude Code, Claude Desktop,
VS Code, Devin, and Windsurf, and explains the `mcp/` folder name collision you
will otherwise hit.

### `references/`

| File | Covers |
| --- | --- |
| `connection-and-auth.md` | Trusted connections, driver selection, the driver 18 encryption trap, named instances, every environment variable, and how to diagnose a failure |
| `catalog-views.md` | The `sys.*` cookbook, why not `INFORMATION_SCHEMA`, the byte-length and composite-key traps, and how permission trimming makes objects invisible rather than forbidden |
| `read-only-guardrails.md` | The four layers, which one is load-bearing, and what is still your responsibility |
| `schema-digest-format.md` | The digest contract field by field, the classification rules, the scoring formula |
| `skill-pack-format.md` | What the generator emits, and the context-budget contract that keeps a large database usable |
| `pii-and-public-repo-safety.md` | The sensitivity categories, why column names are shown rather than hidden, and the three layers stopping a schema reaching this public repository |
| `rdl-to-tables.md` | The RDL to database chain, the shared signature key, and wiring `mssql_get_definition` into `reportlineage`'s unused `ProcBodyLookup` |
| `existing-tools-and-alternatives.md` | Microsoft's SQL MCP Server and the community servers, with the reason each was not adopted |
| `mcp-client-setup.md` | Client configs for all four hosts, and the `mcp/` shadowing fix |

### `examples/`

| File | Demonstrates |
| --- | --- |
| `schema-digest.example.json` | A complete, entirely fictional six-table digest. Regenerate with `cli.py example` |
| `generated-pack/` | Exactly what the generator produces from that digest, checked in as the reference output |

### `tests/`

`python -m pytest skills/claude-skills/sql-server-schema/tests -q`

Runs with no database, no server, and no ODBC driver: `catalog.py` takes an
injected connection and `tests/fakes.py` supplies a fake one. The fixtures are
invented, because this repository is public.

## Common failures and their causes

| Symptom | Cause |
| --- | --- |
| `Data source name not found and no default driver specified` | The named driver is not installed. Run `mssql_list_odbc_drivers`; nothing here assumes a version |
| `SSL Provider: certificate chain ... not trusted` | ODBC Driver 18 defaults to validating the certificate; most on-prem servers are self-signed. `SQLSERVER_MCP_ENCRYPT=auto` handles it |
| `Login failed for user 'NT AUTHORITY\ANONYMOUS LOGON'` | Kerberos was unavailable and the connection fell back to anonymous NTLM. Usually a missing SPN on the SQL service account |
| Named instance will not connect | SQL Browser (UDP 1434) is firewalled. Use `HOST,PORT` with the instance's static port |
| The database appears to have no tables | Permission trimming: SQL Server hides objects the login has no rights on. "Not found" can mean "not permitted" |
| Every row count is zero | The login lacks `VIEW DATABASE STATE`, so `sys.dm_db_partition_stats` returns nothing |
| A procedure body comes back empty | The login lacks `VIEW DEFINITION`, or the module is encrypted |
| Every table classified `unknown` | The database declares no foreign keys and the naming gives nothing away. Check `role_reasons` |
| `nvarchar(50)` shows as length 100 | `max_length` is in bytes for `n`-prefixed types. `catalog.format_type` halves it |
| The generator refuses to write | The output path is inside the repository. That is deliberate: a pack names internal databases |
| fastmcp fails to import `mcp` | This repository's own `mcp/` folder shadowed the PyPI package. Launch by absolute path, not `python -m` from the repository root |

## Related material in this repository

- `skills/report-lineage/` - which reports read these tables.
  `reportlineage.sql_refs.TableRef.signature()` is the shared join key, and
  `reportlineage.builder.build_estate(proc_body_lookup=...)` is the integration
  point this skill fills.
- `skills/claude-skills/rdl-generation/SKILL.md` - writing the `.rdl` once you
  know the real column names. Its workflow step 2 forbids guessing them, and
  this skill is how you satisfy that.
- `skills/claude-skills/ssrs-report-creation/references/data-sources-and-datasets.md`
  - the report-side view of the same datasets.
- `mcp/README.md` - all four MCP servers this repository ships.
- `mcp/sqlserver_schema_server.py` - the launcher for this one.
- `docs/devin-windsurf-microsoft-skills-integration.md` - registering an MCP
  server with Devin or Windsurf.
