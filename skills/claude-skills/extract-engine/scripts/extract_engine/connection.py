"""
connection.py - Windows Integrated Auth connections for the extract engine.

Reuses the ``sql-server-schema`` skill's pure, stateless connection-string
building and driver-detection logic verbatim (none of that is coupled to that
skill's read-only guarantee). Does NOT reuse its ``connection()`` context
manager or ``classify_statement``: this engine genuinely needs to write
(``meta.run_log``/``run_detail``/``run_dataset_key``, ``CREATE OR ALTER VIEW
gen.*``, and the Excel-config loader's upsert into ``meta.feed``/``dataset``/
``field_map``), so it needs its own connection contract rather than
sql-server-schema's SELECT-only one.

Two connection functions exist, deliberately named differently so neither is
ever mistaken for the other:

* ``read_connection()`` - same rollback-always shape as sql-server-schema's
  ``connection()``. Used for reading ``meta.*`` and for the config loader's
  lookup-table-existence check. Genuinely read-only.
* ``write_connection()`` - the opposite default: commits on a clean exit,
  rolls back on an exception. Used only for the narrow write surface
  ``guardrails.classify_write_statement`` actually permits.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

# sql-server-schema's connection.py is pure and stateless; reuse it verbatim
# rather than duplicating driver-detection/connection-string logic. This is a
# sibling skill in the same repo, not an external dependency.
_SQL_SERVER_SCHEMA_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "sql-server-schema" / "scripts"
)
if str(_SQL_SERVER_SCHEMA_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SQL_SERVER_SCHEMA_SCRIPTS))

from connection import (  # noqa: E402  (re-exported, pure functions only)
    ConnectionError_,
    Target,
    _apply_session_settings,
    _env_flag,
    _env_int,
    assert_no_credentials,
    available_drivers,
    build_connection_string,
    clamp_rows,
    connection_settings,
    explain_odbc_error,
    max_rows_ceiling,
    normalize_target,
    pick_driver,
    redact_connection_string,
    resolve_database,
    resolve_server,
)
from connection import ENV_DRIVER, ENV_ENCRYPT, ENV_LOGIN_TIMEOUT, ENV_QUERY_TIMEOUT  # noqa: E402

__all__ = [
    "ConnectionError_",
    "Target",
    "assert_no_credentials",
    "available_drivers",
    "build_connection_string",
    "clamp_rows",
    "connection_settings",
    "explain_odbc_error",
    "max_rows_ceiling",
    "normalize_target",
    "pick_driver",
    "redact_connection_string",
    "resolve_database",
    "resolve_server",
    "read_connection",
    "write_connection",
]

# Extract-engine has its own env-var namespace, separate from
# SQLSERVER_MCP_* (that belongs to the sql-server-schema skill's own server;
# running both skills side by side must not have one's config bleed into the
# other's).
ENV_SERVER = "EXTRACT_ENGINE_SERVER"
ENV_DATABASE = "EXTRACT_ENGINE_DATABASE"


def _require_pyodbc():
    try:
        import pyodbc  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConnectionError_(
            "pyodbc is not installed. Install it with 'pip install pyodbc'."
        ) from exc
    return pyodbc


def _resolve(server: Optional[str], database: Optional[str]):
    import os

    resolved_server = server or os.environ.get(ENV_SERVER)
    if not resolved_server:
        raise ConnectionError_(
            "No SQL Server target. Pass server= or set {}.".format(ENV_SERVER)
        )
    resolved_db = database or os.environ.get(ENV_DATABASE) or "master"
    return resolved_server, resolved_db


def _open_raw(server: Optional[str], database: Optional[str], *, autocommit: bool):
    import os

    pyodbc = _require_pyodbc()
    resolved_server, resolved_db = _resolve(server, database)
    conn_str = build_connection_string(
        resolved_server,
        resolved_db,
        driver=os.environ.get(ENV_DRIVER) or None,
        encrypt=(os.environ.get(ENV_ENCRYPT) or "auto"),
        login_timeout=_env_int(ENV_LOGIN_TIMEOUT, 10),
    )
    try:
        return pyodbc.connect(conn_str, autocommit=autocommit)
    except Exception as exc:  # pragma: no cover - needs a real driver
        raise ConnectionError_(explain_odbc_error(exc, conn_str)) from exc


@contextmanager
def read_connection(
    server: Optional[str] = None,
    database: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
    query_timeout: Optional[int] = None,
) -> Iterator[Any]:
    """Yield a connection that is rolled back unconditionally on exit.

    Same guarantee as sql-server-schema's read-only ``connection()``: nothing
    read through this path can ever be mistaken for something committed.
    Pass ``conn=`` to inject a fake for tests.
    """
    import os

    injected = conn is not None
    handle = conn if injected else _open_raw(server, database, autocommit=False)
    handle.timeout = int(
        query_timeout if query_timeout is not None else _env_int(ENV_QUERY_TIMEOUT, 30)
    )
    try:
        if not injected:
            _apply_session_settings(handle)
        yield handle
    finally:
        try:
            handle.rollback()
        except Exception:
            pass
        if not injected:
            try:
                handle.close()
            except Exception:
                pass


def execute_insert_return_identity(cursor: Any, insert_sql: str, *params: Any) -> int:
    """Run a parameterized INSERT and return the identity value it generated.

    Not ``cursor.execute(insert_sql, *params)`` followed by a separate
    ``cursor.execute("SELECT SCOPE_IDENTITY()")`` call: pyodbc runs a
    parameterized INSERT via sp_prepare/sp_execute (an RPC call), and SQL
    Server treats each RPC as a new scope, so a SEPARATE SCOPE_IDENTITY()
    call afterward reliably returns NULL even though the insert succeeded
    (confirmed empirically against LocalDB; @@IDENTITY still returns the
    right value in that broken shape, which is a trap of its own since
    @@IDENTITY ignores scope and can return a wrong value if a trigger fired
    an identity insert of its own). The fix is to keep the INSERT and the
    identity read in the SAME batch/execute call, then advance to the second
    result set with ``nextset()`` before fetching.
    """
    combined = "{}; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS id".format(insert_sql.rstrip().rstrip(";"))
    cursor.execute(combined, *params)
    cursor.nextset()
    row = cursor.fetchone()
    if row is None or row[0] is None:
        raise ConnectionError_(
            "INSERT did not produce an identity value. SQL: {}".format(insert_sql)
        )
    return int(row[0])


@contextmanager
def write_connection(
    server: Optional[str] = None,
    database: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
    query_timeout: Optional[int] = None,
) -> Iterator[Any]:
    """Yield a connection that commits on a clean exit, rolls back on error.

    Named differently from ``read_connection`` on purpose, so the two can
    never be confused at a call site. Use only for the narrow write surface
    ``guardrails.classify_write_statement`` permits - never for arbitrary SQL.
    """
    injected = conn is not None
    handle = conn if injected else _open_raw(server, database, autocommit=False)
    handle.timeout = int(
        query_timeout if query_timeout is not None else _env_int(ENV_QUERY_TIMEOUT, 30)
    )
    try:
        if not injected:
            _apply_session_settings(handle)
        yield handle
        # Commit/rollback are this function's actual transaction contract, so
        # they run regardless of injection - only closing (lifecycle
        # ownership) is skipped for an injected connection. Gating commit()
        # behind "not injected" would mean the one behavior this function
        # exists to provide could never be observed or tested.
        handle.commit()
    except Exception:
        try:
            handle.rollback()
        except Exception:
            pass
        raise
    finally:
        if not injected:
            try:
                handle.close()
            except Exception:
                pass
