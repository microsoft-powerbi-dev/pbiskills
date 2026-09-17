"""
sqlserver_schema_mcp.py - an MCP server exposing this skill's SQL Server schema
tools over stdio, for an agentic IDE (Claude Code, Claude Desktop, VS Code,
Devin, Windsurf, or any other MCP client) to drive.

Read-only by construction. There is no write tool, no ``execute_sql``, and no
environment flag that unlocks one. Every connection is opened with
``autocommit=False`` and unconditionally rolled back, and every ad-hoc
statement is classified before it is sent. See ``references/read-only-guardrails.md``.

Authentication is Windows Integrated Auth only: the server authenticates as
whoever launched it, using their existing Kerberos or NTLM credentials. No
password is ever accepted, stored, or prompted for.

Note the filename. This is deliberately *not* ``mcp_server.py``: the
``rdl-generation`` skill already ships a ``scripts/mcp_server.py``, and both
launchers in ``mcp/`` insert their skill's ``scripts/`` directory onto
``sys.path`` and import by module name. Two modules both called ``mcp_server``
would collide in ``sys.modules`` and one launcher would silently re-export the
other's tools.

Every tool is also a plain, directly callable Python function; when the
optional ``fastmcp`` package is installed they are also registered as MCP tools
and ``main()`` serves them over stdio.

Direct usage (in-process, no MCP)::

    from sqlserver_schema_mcp import mssql_list_tables
    result = mssql_list_tables(schema="dbo")

Served over stdio::

    pip install pyodbc fastmcp   # fastmcp only needed for the stdio server
    python sqlserver_schema_mcp.py

Point an MCP client's config at this file, for example::

    {
      "mcpServers": {
        "sql-server-schema": {
          "command": "python",
          "args": ["/absolute/path/to/scripts/sqlserver_schema_mcp.py"],
          "env": {"SQLSERVER_MCP_SERVER": "SQLPROD01",
                  "SQLSERVER_MCP_DATABASE": "SalesDW"}
        }
      }
    }
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import analyze  # noqa: E402
import catalog  # noqa: E402
import connection as conn_mod  # noqa: E402
import guardrails  # noqa: E402
import skillgen  # noqa: E402


def _check_mcp_shadowing() -> None:
    """Fail with a useful message if this repository's mcp/ folder shadowed the SDK.

    This repository has a top-level ``mcp/`` directory. Whenever the repository
    root is on ``sys.path``, ``import mcp`` resolves to that folder instead of
    the ``mcp`` PyPI package that fastmcp depends on, and the resulting
    ImportError comes from deep inside fastmcp and says nothing useful.

    Deleting ``mcp/__init__.py`` would not help: since PEP 420 a directory
    without ``__init__.py`` is still importable as a namespace package and
    still shadows. Launch this file by absolute path (which puts *this*
    directory on sys.path, not the repository root) rather than with
    ``python -m mcp.something`` from the repository root.
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        return
    location = getattr(mcp, "__file__", None) or (
        list(getattr(mcp, "__path__", [])) or [""]
    )[0]
    if location and (Path(location).resolve().parent.name == "mcp") and (
        Path(location).resolve().parents[1] == _HERE.parents[3]
    ):
        raise SystemExit(
            "The 'mcp' package resolved to this repository's own mcp/ folder "
            "({}), not the MCP SDK. Launch this server by absolute path from a "
            "working directory other than the repository root, and do not put "
            "the repository root on PYTHONPATH.".format(location)
        )


try:
    from fastmcp import FastMCP

    _mcp: Optional[Any] = FastMCP("sql-server-schema")
except ImportError:
    _mcp = None


def _tool(fn):
    """Register with FastMCP when available; otherwise return the function unchanged."""
    if _mcp is not None:
        return _mcp.tool()(fn)
    return fn


def _envelope(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run a tool body, turning any failure into a payload rather than a raise.

    An MCP tool that raises gives the model a stack trace; one that returns
    ``{"ok": False, "error": ...}`` gives it something to act on.
    """
    try:
        result = fn()
        if isinstance(result, dict) and "ok" in result:
            return result
        return {"ok": True, **(result if isinstance(result, dict) else {"result": result})}
    except PermissionError as exc:
        return {"ok": False, "error": str(exc), "kind": "refused"}
    except conn_mod.ConnectionError_ as exc:
        return {"ok": False, "error": str(exc), "kind": "connection"}
    except Exception as exc:  # noqa: BLE001 - deliberate: tools never raise
        return {"ok": False, "error": conn_mod.explain_odbc_error(exc), "kind": "error"}


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


@_tool
def mssql_list_odbc_drivers() -> Dict[str, Any]:
    """List the ODBC drivers installed on this machine and which one will be used.

    Start here when a connection fails with "Data source name not found". Driver
    numbering is not assumed anywhere: the highest installed
    "ODBC Driver NN for SQL Server" wins, falling back to the legacy
    "SQL Server" driver that ships with Windows.
    """

    def body() -> Dict[str, Any]:
        installed = conn_mod.available_drivers()
        return {"installed": installed, "would_use": conn_mod.pick_driver(drivers=installed)}

    return _envelope(body)


@_tool
def mssql_test_connection(
    server: str = "", database: str = "", include_login: bool = False
) -> Dict[str, Any]:
    """Prove that Windows Integrated Auth reaches the server, and report how.

    The ``auth_scheme`` field is the useful part: KERBEROS or NTLM means
    integrated authentication genuinely happened rather than being assumed. It
    needs VIEW SERVER STATE, so a null value with ``missing_permissions`` set
    means "cannot confirm", not "failed".

    Args:
        server: Overrides SQLSERVER_MCP_SERVER for this call.
        database: Overrides SQLSERVER_MCP_DATABASE for this call.
        include_login: Include the Windows login name. Withheld by default
            because it is an identifiable AD account name.
    """

    def body() -> Dict[str, Any]:
        settings = conn_mod.connection_settings(server or None, database or None)
        with conn_mod.connection(server or None, database or None) as conn:
            info = catalog.get_server_info(conn)
        scheme = info.get("auth_scheme")
        payload = {
            "driver": settings["driver"],
            "encrypt_mode": settings["encrypt_mode"],
            "database": info.get("current_database"),
            "product_version": info.get("product_version"),
            "edition": info.get("edition"),
            "collation": info.get("server_collation"),
            "auth_scheme": scheme,
            "integrated_auth_confirmed": scheme in ("KERBEROS", "NTLM"),
            "max_rows": settings["max_rows"],
            "isolation": settings["isolation"],
        }
        if include_login:
            payload["login_name"] = info.get("login_name")
        if info.get("missing_permissions"):
            payload["missing_permissions"] = info["missing_permissions"]
            payload["hint"] = info.get("hint")
        return payload

    return _envelope(body)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@_tool
def mssql_list_databases(server: str = "", include_system: bool = False) -> Dict[str, Any]:
    """List the databases this Windows identity can actually open.

    Filtered by HAS_DBACCESS, so the list reflects real permission rather than
    everything on the instance.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server or None, "master") as conn:
            return {"databases": catalog.list_databases(conn, include_system)}

    return _envelope(body)


@_tool
def mssql_list_schemas(database: str = "", server: str = "") -> Dict[str, Any]:
    """List non-system schemas in a database, with their object counts."""

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server or None, database or None) as conn:
            return {"schemas": catalog.list_schemas(conn)}

    return _envelope(body)


@_tool
def mssql_list_tables(
    database: str = "",
    schema: str = "",
    name_like: str = "",
    include_views: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> Dict[str, Any]:
    """List tables and views with row counts, filterable by schema and name.

    Paginated deliberately: a large OLTP database can exceed several thousand
    tables, and returning all of them at once would consume the context window
    for no benefit.

    Args:
        name_like: A T-SQL LIKE pattern, for example "Claim%".
        limit: Maximum rows to return (page size).
        offset: How many to skip, for paging through a large catalog.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            rows = catalog.list_tables(
                conn,
                schema=schema or None,
                name_like=name_like or None,
                include_views=include_views,
            )
        page = rows[offset : offset + limit]
        return {
            "tables": page,
            "total": len(rows),
            "returned": len(page),
            "offset": offset,
            "truncated": offset + len(page) < len(rows),
        }

    return _envelope(body)


@_tool
def mssql_describe_table(table: str, database: str = "") -> Dict[str, Any]:
    """Describe one table or view: columns, keys, relationships, and indexes.

    Columns carry type, nullability, defaults, identity and computed
    definitions, and any MS_Description extended property. Columns whose names
    match the sensitivity heuristics are flagged so an agent knows not to
    select them, even in a schema-only session.

    Args:
        table: A table name, optionally schema-qualified ("dbo.Claim").
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            result = catalog.describe_table(conn, table)
        if not result.get("found"):
            return {"ok": False, "error": result.get("error")}
        for column in result["columns"]:
            sensitivity = guardrails.classify_column_sensitivity(
                column["name"], column.get("data_type", "")
            )
            column["sensitive"] = bool(sensitivity)
            column["sensitivity"] = sensitivity
        return result

    return _envelope(body)


@_tool
def mssql_list_relationships(database: str = "", infer: bool = True) -> Dict[str, Any]:
    """Return the join graph: declared foreign keys, plus inferred name matches.

    Inference matters in older estates, which routinely drop foreign key
    constraints for load performance and keep the join graph only as a naming
    convention. Inferred edges are labelled and should be verified before you
    rely on them.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            foreign_keys = catalog.get_foreign_keys(conn)
            table_rows = catalog.list_tables(conn, include_views=False)
            tables = [
                {
                    "schema": row["schema_name"],
                    "name": row["object_name"],
                    "row_count": row.get("row_count") or 0,
                    "columns": [],
                    "primary_key": None,
                }
                for row in table_rows
            ]
        graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
        edges = graph.edges
        return {
            "relationships": edges,
            "declared": len(edges),
            "note": "Inference needs full column detail; use mssql_build_schema_digest "
            "for inferred relationships." if infer else "",
        }

    return _envelope(body)


@_tool
def mssql_list_indexes(table: str, database: str = "") -> Dict[str, Any]:
    """List one table's indexes, with key columns, included columns, and filters.

    An index is a documented access path: it tells you the query shape the
    schema was designed for.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            resolved = catalog.resolve_object(conn, table)
            if resolved is None:
                return {"ok": False, "error": "No table or view named {!r}.".format(table)}
            return {
                "table": "{}.{}".format(resolved["schema_name"], resolved["object_name"]),
                "indexes": catalog.get_indexes(conn, resolved["object_id"]),
            }

    return _envelope(body)


@_tool
def mssql_table_stats(database: str = "", schema: str = "", top: int = 50) -> Dict[str, Any]:
    """Row counts and sizes for the largest tables, from partition metadata.

    Read from sys.dm_db_partition_stats rather than COUNT(*), which would scan
    every table. The numbers are approximate under concurrent writes and free
    to obtain.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            return {
                "tables": catalog.get_table_stats(conn, schema=schema or None, top=top),
                "approximate": True,
                "source": "sys.dm_db_partition_stats",
            }

    return _envelope(body)


@_tool
def mssql_list_programmability(
    database: str = "", kind: str = "", schema: str = "", name_like: str = ""
) -> Dict[str, Any]:
    """List stored procedures, functions, and views.

    Args:
        kind: One of procedure, scalar_function, inline_function,
            table_function, view. Empty for all of them.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            return {
                "objects": catalog.list_programmability(
                    conn,
                    kind=kind or None,
                    schema=schema or None,
                    name_like=name_like or None,
                )
            }

    return _envelope(body)


@_tool
def mssql_get_definition(name: str, database: str = "", max_chars: int = 40000) -> Dict[str, Any]:
    """Return the T-SQL body of a stored procedure, function, or view.

    This is what lets report lineage trace *through* a stored procedure to the
    base tables it reads, instead of stopping at the EXEC. See
    ``references/rdl-to-tables.md`` for wiring it into
    ``reportlineage.builder.build_estate(proc_body_lookup=...)``.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server=None, database=database or None) as conn:
            result = catalog.get_definition(conn, name, max_chars=max_chars)
        if not result.get("found"):
            return {"ok": False, "error": result.get("error")}
        return result

    return _envelope(body)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@_tool
def mssql_run_query(sql: str, database: str = "", max_rows: int = 200) -> Dict[str, Any]:
    """Run a single read-only SELECT and return the rows.

    Refused unless the statement is one SELECT (or a WITH clause feeding a
    SELECT). Anything that writes, executes, or changes schema is rejected
    before it reaches the server, and the transaction is rolled back regardless.

    Results are capped, binary columns are reported by size rather than value,
    and columns whose names match the sensitivity heuristics are masked to a
    shape descriptor rather than returned.
    """

    def body() -> Dict[str, Any]:
        verdict = guardrails.classify_statement(sql)
        if not verdict.allowed:
            return {
                "ok": False,
                "error": verdict.reason,
                "kind": verdict.kind,
                "classified_by": verdict.engine,
            }
        capped = conn_mod.clamp_rows(max_rows)
        with conn_mod.connection(server=None, database=database or None) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                columns = (
                    [column[0] for column in cursor.description]
                    if cursor.description
                    else []
                )
                fetched = cursor.fetchmany(capped) if columns else []
            finally:
                try:
                    cursor.close()
                except Exception:
                    pass

        sensitive = {
            name: bool(guardrails.classify_column_sensitivity(name)) for name in columns
        }
        rows = [
            {
                name: guardrails.redact_value(catalog._plain(value), sensitive[name])
                for name, value in zip(columns, row)
            }
            for row in fetched
        ]
        return {
            "columns": columns,
            "rows": rows,
            "returned": len(rows),
            "max_rows": capped,
            "truncated": len(rows) >= capped,
            "masked_columns": sorted(name for name, flag in sensitive.items() if flag),
            "classified_by": verdict.engine,
        }

    return _envelope(body)


# ---------------------------------------------------------------------------
# Digest and skill generation
# ---------------------------------------------------------------------------


@_tool
def mssql_build_schema_digest(
    database: str = "",
    server: str = "",
    schema: str = "",
    include_views: bool = False,
    include_routines: bool = False,
    out_path: str = "",
    allow_in_repo: bool = False,
) -> Dict[str, Any]:
    """Read a whole database's schema and write a portable digest JSON.

    The digest is the seam between the live server and the offline generator:
    once written it can be reviewed, diffed against a previous run to detect
    schema drift, and turned into a skill pack on a machine with no ODBC driver.

    Written outside this repository by default, because it names internal
    databases, tables, and columns.
    """

    def body() -> Dict[str, Any]:
        with conn_mod.connection(server or None, database or None) as conn:
            server_info = catalog.get_server_info(conn)
            schemas = catalog.list_schemas(conn)
            tables = []
            for row in catalog.list_tables(
                conn, schema=schema or None, include_views=include_views
            ):
                detail = catalog.describe_table(
                    conn, "{}.{}".format(row["schema_name"], row["object_name"])
                )
                if not detail.get("found"):
                    continue
                tables.append(
                    {
                        "schema": row["schema_name"],
                        "name": row["object_name"],
                        "object_type": row.get("type_desc"),
                        "row_count": row.get("row_count") or 0,
                        "size_mb": row.get("size_mb"),
                        "description": row.get("description"),
                        "columns": detail["columns"],
                        "primary_key": detail["primary_key"],
                        "indexes": detail["indexes"],
                    }
                )
            foreign_keys = catalog.get_foreign_keys(conn)
            routines = (
                catalog.list_programmability(conn, schema=schema or None)
                if include_routines
                else []
            )

        digest = analyze.build_digest(
            database=conn_mod.resolve_database(database or None),
            tables=tables,
            foreign_keys=foreign_keys,
            schemas=schemas,
            server_info=server_info,
            routines=routines,
        )
        target = guardrails.resolve_artifact_path(
            out_path or None,
            "digests/{}.schema-digest.json".format(
                skillgen.slugify(digest["source"]["database"])
            ),
            allow_in_repo=allow_in_repo,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(digest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return {
            "path": str(target),
            "tables": len(digest["tables"]),
            "relationships": digest["coverage"]["relationship_count"],
            "schemas": digest["coverage"]["schemas"],
        }

    return _envelope(body)


@_tool
def mssql_generate_skill(
    digest_path: str, out_dir: str = "", dry_run: bool = False, allow_in_repo: bool = False
) -> Dict[str, Any]:
    """Turn a schema digest into a loadable skill pack of markdown.

    Deterministic templating: no model call, no network. The pack contains
    internal identifiers and is written outside this repository by default;
    writing it anywhere git would pick it up is refused.

    Use ``dry_run`` first to see the file list and sizes without writing.
    """

    def body() -> Dict[str, Any]:
        digest = skillgen.load_digest(digest_path)
        return skillgen.generate_skill_pack(
            digest,
            out_dir or None,
            dry_run=dry_run,
            allow_in_repo=allow_in_repo,
        )

    return _envelope(body)


@_tool
def mssql_get_reference(topic: str = "") -> Dict[str, Any]:
    """Return one of this skill's reference documents, for an agent with no filesystem.

    Call with no topic to list what is available.
    """

    def body() -> Dict[str, Any]:
        references = _HERE.parent / "references"
        available = sorted(path.stem for path in references.glob("*.md"))
        if not topic:
            return {"topics": available}
        path = references / "{}.md".format(topic)
        if not path.exists():
            return {"ok": False, "error": "No such topic. Available: {}".format(available)}
        return {"topic": topic, "content": path.read_text(encoding="utf-8")}

    return _envelope(body)


def main() -> None:
    """Serve the tools over stdio for an MCP client."""
    _check_mcp_shadowing()
    if _mcp is None:
        raise SystemExit(
            "fastmcp is not installed. Install it (pip install fastmcp) to run "
            "the SQL Server schema MCP server, or import the tool functions "
            "directly."
        )
    _mcp.run()


if __name__ == "__main__":
    main()
