# Existing SQL Server MCP servers, and why this repository has its own

Researched September 2026, before writing any code. Recorded here so the
build-versus-adopt decision is auditable rather than folklore, and so the next
person does not have to repeat the evaluation.

## What exists

### Microsoft's official SQL MCP Server

- Documentation: <https://learn.microsoft.com/en-us/sql/mcp/>
- Announced April 2026 on the Azure SQL Dev Corner blog.
- Built on **Data API builder 2.0**, and surfaced through the Data API builder
  UI in the MSSQL extension for VS Code (v1.41+).
- Implements MCP 2025-06-18 over both streamable HTTP and stdio.
- Exposes seven DML tools: `describe_entities`, `create_record`,
  `read_records`, `update_record`, `delete_record`, `execute_entity`,
  `aggregate_records`.
- Run locally with `dab init --database-type mssql --connection-string ...`,
  `dab add <entity> --source dbo.<Table> --permissions ...`, `dab start`.

**Why this repository does not use it.** Two reasons, and the first is
decisive:

1. **It is explicitly "designed to work with data, not schema".** Every entity
   must be declared with `dab add` before it is visible. That is backwards
   here, because entity *discovery* is the deliverable: the whole point is to
   interrogate a database nobody has catalogued yet.
2. Its authentication story centres on Microsoft Entra ID and custom OAuth,
   which is the right shape for Azure SQL and the wrong shape for an
   on-premises server reached with a Windows domain account.

**When you should use it instead.** If the requirement becomes typed CRUD
against a known set of entities, with RBAC, caching, and telemetry, that is
exactly what Data API builder is for and this server should not grow toward it.
This one's charter is read-only discovery plus skill generation.

### microsoft/skills-for-fabric SQL skills

The upstream repository ships `sqldb-authoring-cli`, `sqldb-consumption-cli`,
`sqldw-authoring-cli`, and `sqldw-consumption-cli` (MIT).

**Cited, not vendored.** Their operative content is `sqlcmd -G` against
`<workspace>.datawarehouse.fabric.microsoft.com` and `az rest` against Fabric
item APIs with Entra tokens. On-premises SQL Server shares the T-SQL surface
with those and essentially nothing else: no `az`, no workspace, no item id, no
long-running-operation polling, and Windows auth rather than Entra. An agent
that loaded `sqldw-consumption-cli` while working on-premises would get a
plausible-looking authentication path that cannot work, which is worse than no
skill at all.

`skills/vendor/microsoft-fabric/README.md` already documents that the SQL
DB/DW skills were deliberately excluded from what this repository vendored.
That decision stands. It would change if the migration target became Fabric
Warehouse, at which point those skills become the destination half of the story
this skill is the source half of.

### Community MSSQL MCP servers

| Project | Language | License | Notes |
| --- | --- | --- | --- |
| [JexinSam/mssql_mcp_server](https://github.com/JexinSam/mssql_mcp_server) (PyPI `mssql-mcp-server`) | Python | MIT | The closest prior art: pyodbc, and explicit `Trusted_Connection=yes` support. Three tools: `list_tables`, `query_sql`, `execute_sql` |
| [bytebase/dbhub](https://github.com/bytebase/dbhub) | TypeScript | MIT | Multi-database, the most widely used of the group |
| [RichardHan/mssql_mcp_server](https://github.com/RichardHan/mssql_mcp_server) | Python | MIT | Supports SQL, Windows, and Azure AD auth |
| [aadversteeg/mssqlclient-mcp-server](https://github.com/aadversteeg/mssqlclient-mcp-server) | C# | MIT | The richest schema tool surface of the community servers, and worth reading for tool naming |
| [Aaronontheweb/mssql-mcp](https://github.com/Aaronontheweb/mssql-mcp) | C# | Apache 2.0 | |

**Why none of them is a dependency here**, in order of weight:

1. **They stop where this skill starts.** Everything this repository actually
   needs (the foreign-key graph, index detail, partition-stats row counts,
   fact and dimension classification, naming-convention detection, the digest
   artifact, skill generation) would have to be written on top regardless. The
   dependency would buy `list_tables`.
2. **`execute_sql` is a disqualifier.** Both Python servers expose unguarded
   write execution, so the client's tool list advertises a write tool by
   default. Suppressing that from outside someone else's server process is not
   possible, and the read-only posture here is meant to be structural rather
   than configured.
3. **Repository fit is structural, not cosmetic.** Every server here is a plain
   Python module where each tool is also a directly callable function,
   `fastmcp` is optional, and heavy imports are lazy. A vendored community
   server would be the one file that does not follow that, and could not feed
   `mssql_generate_skill` in process.
4. **Supply chain.** A single-maintainer package holding a live connection to a
   production database is a conversation worth avoiding when the alternative is
   roughly a thousand lines of catalog SQL that this repository will own anyway.
5. **Windows Integrated Auth is one connection-string key.** There is no
   library value to buy: `Trusted_Connection=yes` plus driver selection is the
   whole of it.

## What does not exist

There is **no Microsoft-owned MCP server for on-premises SQL Server with
Windows Integrated Auth and schema discovery.** The
`Azure-Samples/SQL-AI-samples/MssqlMcp` path that many blog posts and
aggregators still link to is no longer present in that repository. If that
changes, this decision is worth revisiting, and this file is where to record
the revisit.

## No code was copied

Nothing here is derived from any of the projects above, so no `LICENSE` needs
vendoring. If a specific query is ever lifted verbatim from one of them, it
moves to `skills/vendor/` with the provenance discipline that
`skills/vendor/microsoft-fabric/README.md` already sets out: source URL, exact
path, fetch date, license, and any adaptation made.
