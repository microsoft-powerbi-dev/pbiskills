"""
sqlserver_schema_server.py - launcher for the SQL Server schema MCP server.

The real, single source of truth for this server lives at
``../skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py``,
alongside the ``connection.py``, ``catalog.py``, ``guardrails.py``,
``analyze.py`` and ``skillgen.py`` modules it imports as plain siblings (so it
needs to stay next to them). This file is a thin, no-logic launcher placed here
only so every MCP server this project ships is discoverable from one `mcp/`
folder.

Like ``rdl_generation_server.py`` and unlike the two reference copies in this
folder, this server runs as-is. It needs two things the others do not: the
``pyodbc`` package, and a Microsoft ODBC driver for SQL Server (the highest
installed version is chosen automatically; nothing here assumes 17 or 18).

It connects using **Windows Integrated Auth only**. It authenticates as
whoever launched it, so it must run in the user's own session. A Windows
service running under a different account would authenticate as that account,
not as the user.

Run it directly::

    pip install pyodbc fastmcp
    python mcp/sqlserver_schema_server.py

Or point an MCP client straight at the real file instead of this launcher,
which is what the docs recommend::

    {
      "mcpServers": {
        "sql-server-schema": {
          "command": "python",
          "args": ["/absolute/path/to/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"],
          "env": {"SQLSERVER_MCP_SERVER": "SQLPROD01",
                  "SQLSERVER_MCP_DATABASE": "SalesDW"}
        }
      }
    }

A note on this folder's name. ``fastmcp`` depends on the ``mcp`` PyPI package,
and this folder is also called ``mcp``. Whenever the repository root is on
``sys.path``, ``import mcp`` resolves here instead and fastmcp fails with a
confusing error. Running this file by path is safe (it puts ``mcp/`` on
sys.path, not the repository root); running ``python -m mcp.sqlserver_schema_server``
from the repository root is not. Deleting ``__init__.py`` would not help,
because a directory without one is still importable as a namespace package.
The real server carries a startup check that detects the collision and says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REAL_SERVER_DIR = (
    Path(__file__).resolve().parent.parent
    / "skills" / "claude-skills" / "sql-server-schema" / "scripts"
)
if str(_REAL_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_REAL_SERVER_DIR))

# Named sqlserver_schema_mcp rather than mcp_server on purpose: the
# rdl-generation skill already ships a scripts/mcp_server.py, and both
# launchers put their skill's scripts/ directory on sys.path. Two modules
# called mcp_server would collide in sys.modules and one launcher would
# silently re-export the other's tools.
from sqlserver_schema_mcp import (  # noqa: E402,F401
    main,
    mssql_build_schema_digest,
    mssql_describe_table,
    mssql_generate_skill,
    mssql_get_definition,
    mssql_get_reference,
    mssql_list_databases,
    mssql_list_indexes,
    mssql_list_odbc_drivers,
    mssql_list_programmability,
    mssql_list_relationships,
    mssql_list_schemas,
    mssql_list_tables,
    mssql_run_query,
    mssql_table_stats,
    mssql_test_connection,
)

__all__ = [
    "main",
    "mssql_build_schema_digest",
    "mssql_describe_table",
    "mssql_generate_skill",
    "mssql_get_definition",
    "mssql_get_reference",
    "mssql_list_databases",
    "mssql_list_indexes",
    "mssql_list_odbc_drivers",
    "mssql_list_programmability",
    "mssql_list_relationships",
    "mssql_list_schemas",
    "mssql_list_tables",
    "mssql_run_query",
    "mssql_table_stats",
    "mssql_test_connection",
]

if __name__ == "__main__":
    main()
