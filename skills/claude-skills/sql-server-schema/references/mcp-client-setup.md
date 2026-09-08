# Registering this server with an MCP client

## The `mcp/` folder name collision, and how to avoid it

Read this first, because it causes an error whose message points nowhere near
the cause.

`fastmcp` depends on the `mcp` package from PyPI. This repository has a
top-level folder **also called `mcp/`**. Whenever the repository root is on
`sys.path`, `import mcp` resolves to the folder instead of the package, and
fastmcp fails with an ImportError raised from somewhere deep inside its own
internals.

Three things worth knowing:

- **Deleting `mcp/__init__.py` does not fix it.** Since PEP 420 a directory
  with no `__init__.py` is still importable as a namespace package, and still
  shadows.
- **The repository root reaches `sys.path`** by running
  `python -m mcp.sqlserver_schema_server` from the repository root, by running a
  script whose own directory is the repository root, or via `PYTHONPATH`.
- `python mcp/sqlserver_schema_server.py` is *safe*: `sys.path[0]` becomes the
  `mcp/` directory, not the repository root.

**The fix, and the recommended configuration for every client: point at the
real server file by absolute path, and set `cwd` to its own directory.**

```
command: C:\Python313\python.exe
args:    C:\path\to\pbiskills\skills\claude-skills\sql-server-schema\scripts\sqlserver_schema_mcp.py
cwd:     C:\path\to\pbiskills\skills\claude-skills\sql-server-schema\scripts
```

That puts the `scripts/` directory on `sys.path` (which is what makes
`connection`, `catalog`, `analyze` resolve as sibling modules, the same design
`rdl-generation/scripts/mcp_server.py` uses), and the repository root never
appears at all.

Do **not** set `PYTHONSAFEPATH=1`: it removes the script's directory from
`sys.path` and breaks the sibling imports.

The server carries a startup check that detects the collision and raises a
message naming the fix, rather than letting fastmcp's own error surface.

## Prerequisites

```bash
pip install pyodbc fastmcp sqlglot
```

`sqlglot` is optional but recommended: without it the read-only statement
classifier falls back to a stricter regex-only mode. A Microsoft ODBC driver
for SQL Server must be installed; run
`python scripts/cli.py drivers` to see which one will be used.

## Claude Code

Project scope, `.mcp.json` at the repository root. Because that file is
committed to a public repository, use variable expansion and keep the real
values in your machine environment:

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "python",
      "args": [
        "C:\\path\\to\\pbiskills\\skills\\claude-skills\\sql-server-schema\\scripts\\sqlserver_schema_mcp.py"
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

Set the values once, per user:

```powershell
setx SQLSERVER_MCP_SERVER "SQLPROD01"
setx SQLSERVER_MCP_DATABASE "SalesDW"
```

Then restart the client so it picks them up. If even the placeholder shape is
unwanted in the repository, register at user scope instead:

```bash
claude mcp add --scope user sql-server-schema -- python "C:/.../scripts/sqlserver_schema_mcp.py"
```

## Claude Desktop

`%APPDATA%\Claude\claude_desktop_config.json`. This file is not in the
repository, so literal values are fine:

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "C:\\Python313\\python.exe",
      "args": [
        "C:\\path\\to\\pbiskills\\skills\\claude-skills\\sql-server-schema\\scripts\\sqlserver_schema_mcp.py"
      ],
      "cwd": "C:\\path\\to\\pbiskills\\skills\\claude-skills\\sql-server-schema\\scripts",
      "env": {
        "SQLSERVER_MCP_SERVER": "SQLPROD01",
        "SQLSERVER_MCP_DATABASE": "SalesDW"
      }
    }
  }
}
```

Claude Desktop launches the server as the logged-in user, which is what makes
Windows Integrated Auth work: the process inherits your Kerberos ticket.

## VS Code and GitHub Copilot agent mode

`.vscode/mcp.json`. Use an input prompt so no server name is committed:

```json
{
  "inputs": [
    {"type": "promptString", "id": "sqlserver", "description": "SQL Server host, or HOST\\INSTANCE"},
    {"type": "promptString", "id": "sqldb", "description": "Database"}
  ],
  "servers": {
    "sql-server-schema": {
      "type": "stdio",
      "command": "python",
      "args": [
        "${workspaceFolder}/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"
      ],
      "cwd": "${workspaceFolder}/skills/claude-skills/sql-server-schema/scripts",
      "env": {
        "SQLSERVER_MCP_SERVER": "${input:sqlserver}",
        "SQLSERVER_MCP_DATABASE": "${input:sqldb}"
      }
    }
  }
}
```

SQL Server Management Studio 22.7+ reads `%USERPROFILE%\.mcp.json` in the same
`{"servers": {...}}` shape, if you want the tools inside SSMS Copilot too.
Remember that MCP tools are disabled by default there until you enable each one
in the Tools panel.

## Devin and Windsurf

`docs/devin-windsurf-microsoft-skills-integration.md` documents the pattern
these two follow: their skill loaders read `SKILL.md` and ignore any bundled
`.mcp.json`, so the MCP server has to be registered separately in the host's
own configuration.

**Devin**: vendor the skill folder to `.agents/skills/sql-server-schema/` at the
target repository root, then register the server through Devin's own MCP
configuration as a stdio entry with the command and args above.

**Windsurf**: `~/.codeium/windsurf/mcp_config.json`, same stdio shape:

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "python",
      "args": ["C:/path/to/pbiskills/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"],
      "env": {"SQLSERVER_MCP_SERVER": "SQLPROD01"}
    }
  }
}
```

Both of these are `VERIFY` in the sense that doc uses: the shape follows the
documented pattern, but confirm it against the Devin seat and the Windsurf
version you actually have.

An important caveat for Devin's cloud environment: Windows Integrated Auth
needs a Windows session with a Kerberos ticket for your domain account. A
Linux-hosted agent has neither, and cannot reach an on-premises server this
way. Devin and Windsurf are practical here only when running locally on a
domain-joined machine.

## Verifying the registration

Call these in order:

1. `mssql_list_odbc_drivers` - no server needed, confirms the process can see a driver.
2. `mssql_test_connection` - confirms Windows auth. Check `auth_scheme` is
   `KERBEROS` or `NTLM`.
3. `mssql_list_databases` - confirms your account can see the target.
4. `mssql_run_query` with `DROP TABLE x` - should be **refused**. If it is not,
   stop and investigate.
