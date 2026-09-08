"""
connection.py - Windows Integrated Auth connections to an on-premises SQL Server.

This module is the only place in the skill that opens a database connection.
Everything downstream (``catalog.py``, ``analyze.py``) takes an already-open
connection as its first argument, which is what lets the whole catalog layer be
unit-tested against a fake cursor with no driver and no server present. The
pattern mirrors ``reportlineage/scanners/report_server.py``, which takes an
injectable ``session=`` for the same reason.

Authentication is Windows Integrated Auth and nothing else. There is
deliberately no ``password`` parameter, no ``MSSQL_PASSWORD`` environment
variable, and no secrets file anywhere in this skill. The process authenticates
as whoever launched it, using their existing Kerberos or NTLM credentials.
``assert_no_credentials`` enforces that invariant in code rather than leaving it
to convention.

``pyodbc`` is imported lazily through ``_require_pyodbc`` so this module (and
every test over it) imports cleanly on a machine that has no ODBC driver at all.
"""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence

# Ordered best-first. Never hardcode a single driver: the reference laptop has
# 17 but not 18, and a machine provisioned next year will have the reverse.
_DRIVER_PREFERENCE = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13.1 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "ODBC Driver 11 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",  # legacy, present on every Windows install
)

_APPLICATION_NAME = "sqlserver-schema-mcp"

# Environment variables. Note what is absent: there is no user or password var.
ENV_SERVER = "SQLSERVER_MCP_SERVER"
ENV_DATABASE = "SQLSERVER_MCP_DATABASE"
ENV_DRIVER = "SQLSERVER_MCP_DRIVER"
ENV_ENCRYPT = "SQLSERVER_MCP_ENCRYPT"
ENV_LOGIN_TIMEOUT = "SQLSERVER_MCP_LOGIN_TIMEOUT"
ENV_QUERY_TIMEOUT = "SQLSERVER_MCP_QUERY_TIMEOUT"
ENV_MAX_ROWS = "SQLSERVER_MCP_MAX_ROWS"
ENV_ISOLATION = "SQLSERVER_MCP_ISOLATION"
ENV_READONLY_INTENT = "SQLSERVER_MCP_READONLY_INTENT"
ENV_ALLOWED_SERVERS = "SQLSERVER_MCP_ALLOWED_SERVERS"

_DEFAULT_LOGIN_TIMEOUT = 10
_DEFAULT_QUERY_TIMEOUT = 30
_DEFAULT_MAX_ROWS = 100
_HARD_MAX_ROWS = 1000
_DEFAULT_ISOLATION = "READ UNCOMMITTED"
_LOCK_TIMEOUT_MS = 5000

_CREDENTIAL_KEYS = re.compile(
    r"(?:^|;)\s*(pwd|password|uid|user\s*id)\s*=", re.IGNORECASE
)


class ConnectionError_(RuntimeError):
    """Raised for connection problems this module can explain better than pyodbc can."""


def _require_pyodbc():
    """Import pyodbc, or raise an error that says exactly how to fix it.

    Mirrors ``_require_requests`` in ``reportlineage/scanners/report_server.py``:
    the dependency is optional at import time so the module stays testable, and
    the error names the install command rather than surfacing an ImportError.
    """
    try:
        import pyodbc  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConnectionError_(
            "pyodbc is not installed. Install it with 'pip install pyodbc'. "
            "On Python 3.13 a wheel is available for pyodbc 5.1+; if pip falls "
            "back to a source build it needs the MSVC build tools."
        ) from exc
    return pyodbc


# ---------------------------------------------------------------------------
# Driver selection
# ---------------------------------------------------------------------------


def available_drivers(drivers: Optional[Sequence[str]] = None) -> List[str]:
    """Return the ODBC drivers installed on this machine.

    ``drivers`` is injectable purely so tests can exercise driver selection
    without touching the real ODBC registry.
    """
    if drivers is not None:
        return list(drivers)
    pyodbc = _require_pyodbc()
    return list(pyodbc.drivers())


def pick_driver(
    preferred: Optional[str] = None, drivers: Optional[Sequence[str]] = None
) -> str:
    """Return the best installed SQL Server ODBC driver.

    Honours an explicit ``preferred`` name when it is actually installed, then
    falls back through ``_DRIVER_PREFERENCE``. Raises when nothing usable is
    present, naming the download.
    """
    installed = available_drivers(drivers)
    if preferred:
        if preferred in installed:
            return preferred
        raise ConnectionError_(
            "Requested ODBC driver {!r} is not installed. Installed drivers: {}".format(
                preferred, ", ".join(installed) or "(none)"
            )
        )
    for candidate in _DRIVER_PREFERENCE:
        if candidate in installed:
            return candidate
    raise ConnectionError_(
        "No SQL Server ODBC driver is installed. Install 'ODBC Driver 18 for "
        "SQL Server' from Microsoft. Drivers found: {}".format(
            ", ".join(installed) or "(none)"
        )
    )


def _driver_major_version(driver: str) -> Optional[int]:
    """Extract the major version from a driver name, or None for legacy names."""
    match = re.search(r"ODBC Driver (\d+)", driver)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A parsed SQL Server address: host plus at most one of instance or port."""

    host: str
    instance: Optional[str] = None
    port: Optional[int] = None

    def server_token(self) -> str:
        """Re-render the address in the form ODBC expects."""
        if self.instance:
            return "{}\\{}".format(self.host, self.instance)
        if self.port:
            return "{},{}".format(self.host, self.port)
        return self.host


def normalize_target(server: str) -> Target:
    """Parse HOST, HOST\\INSTANCE, HOST,PORT or tcp:HOST,PORT into a Target.

    A named instance and an explicit port are mutually exclusive: SQL Server
    resolves a named instance *through* the SQL Browser service to find its
    port, so supplying both is a contradiction rather than a belt-and-braces.
    """
    raw = (server or "").strip()
    if not raw:
        raise ConnectionError_(
            "No server given. Pass server= or set the {} environment "
            "variable.".format(ENV_SERVER)
        )
    if raw.lower().startswith("tcp:"):
        raw = raw[4:]

    instance: Optional[str] = None
    port: Optional[int] = None

    if "," in raw:
        raw, _, port_text = raw.partition(",")
        try:
            port = int(port_text.strip())
        except ValueError:
            raise ConnectionError_(
                "Port {!r} is not a number. Use HOST,PORT (a comma, not a "
                "colon).".format(port_text)
            )
    if "\\" in raw:
        raw, _, instance = raw.partition("\\")
        instance = instance.strip() or None

    host = raw.strip()
    if not host:
        raise ConnectionError_("Could not parse a host out of {!r}.".format(server))
    if instance and port:
        raise ConnectionError_(
            "Give either a named instance (HOST\\INSTANCE) or a port "
            "(HOST,PORT), not both. A named instance is resolved to its port "
            "by the SQL Browser service."
        )
    return Target(host=host, instance=instance, port=port)


# ---------------------------------------------------------------------------
# Connection strings
# ---------------------------------------------------------------------------


def assert_no_credentials(conn_str: str) -> None:
    """Raise if a connection string carries a username or password.

    This is the coded form of this skill's central promise. It runs on every
    string built here, so a future edit that adds SQL authentication fails a
    test rather than shipping.
    """
    match = _CREDENTIAL_KEYS.search(conn_str)
    if match:
        raise ConnectionError_(
            "Connection string contains {!r}. This server supports Windows "
            "Integrated Auth only and never handles passwords.".format(
                match.group(1)
            )
        )


def build_connection_string(
    server: str,
    database: Optional[str] = None,
    *,
    driver: Optional[str] = None,
    encrypt: str = "auto",
    login_timeout: int = _DEFAULT_LOGIN_TIMEOUT,
    application_name: str = _APPLICATION_NAME,
    readonly_intent: bool = False,
    drivers: Optional[Sequence[str]] = None,
) -> str:
    """Build a Windows Integrated Auth ODBC connection string.

    ``encrypt`` is one of:

    * ``"auto"``    - on driver 18+, ``Encrypt=yes;TrustServerCertificate=yes``:
                      the wire is encrypted but the certificate chain is not
                      validated, which is what most on-premises servers with a
                      self-signed certificate need. On driver 17 and older,
                      emits nothing, matching what SSMS and sqlcmd do there.
    * ``"strict"``  - ``Encrypt=yes;TrustServerCertificate=no`` on every driver.
                      The correct setting once a CA-issued certificate is
                      installed on the SQL host. This is the target state.
    * ``"off"``     - ``Encrypt=no``, for legacy servers that cannot negotiate TLS.

    Always emits ``Trusted_Connection=yes``. Never emits UID or PWD.
    """
    chosen_driver = pick_driver(driver, drivers)
    target = normalize_target(server)
    major = _driver_major_version(chosen_driver)

    parts = [
        "DRIVER={{{}}}".format(chosen_driver),
        "SERVER={}".format(target.server_token()),
    ]
    if database:
        parts.append("DATABASE={}".format(database))
    # The whole point of this module.
    parts.append("Trusted_Connection=yes")

    mode = (encrypt or "auto").strip().lower()
    if mode == "strict":
        parts.append("Encrypt=yes")
        parts.append("TrustServerCertificate=no")
    elif mode == "off":
        parts.append("Encrypt=no")
        parts.append("TrustServerCertificate=yes")
    elif mode == "auto":
        # Driver 18 flipped the default to Encrypt=yes *with* certificate
        # validation, which is what breaks a typical on-prem server using a
        # self-signed certificate. Keep encryption, relax the chain check.
        if major is not None and major >= 18:
            parts.append("Encrypt=yes")
            parts.append("TrustServerCertificate=yes")
    else:
        raise ConnectionError_(
            "encrypt must be 'auto', 'strict' or 'off', got {!r}.".format(encrypt)
        )

    # Timeout= in a connection string is the LOGIN timeout. The query timeout is
    # a separate thing, set on the connection object in connect().
    parts.append("Timeout={}".format(int(login_timeout)))
    # Lets a DBA identify and, if necessary, kill these sessions by
    # program_name in sys.dm_exec_sessions.
    parts.append("APP={}".format(application_name))
    if readonly_intent:
        parts.append("ApplicationIntent=ReadOnly")

    conn_str = ";".join(parts)
    assert_no_credentials(conn_str)
    return conn_str


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]


def redact_connection_string(conn_str: str) -> str:
    """Replace SERVER and DATABASE values with short hashes, for logs and errors.

    A stack trace pasted into a public issue should leak a hash, not the name of
    an internal server.
    """

    def _sub(match: "re.Match") -> str:
        key, value = match.group(1), match.group(2)
        return "{}=<hashed:{}>".format(key, _hash_identifier(value))

    return re.sub(
        r"\b(SERVER|DATABASE)=([^;]*)", _sub, conn_str, flags=re.IGNORECASE
    )


# ---------------------------------------------------------------------------
# Environment-backed defaults
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_server(server: Optional[str] = None) -> str:
    """Resolve the target server from an argument or the environment.

    Keeping the default in the environment rather than in a committed config is
    what stops an internal hostname reaching this public repository.
    """
    value = (server or os.environ.get(ENV_SERVER) or "").strip()
    if not value:
        raise ConnectionError_(
            "No SQL Server target. Pass server= or set {}.".format(ENV_SERVER)
        )
    allowed = [
        item.strip()
        for item in (os.environ.get(ENV_ALLOWED_SERVERS) or "").split(",")
        if item.strip()
    ]
    if allowed and value not in allowed:
        raise ConnectionError_(
            "Server {} is not in the {} allowlist.".format(
                _hash_identifier(value), ENV_ALLOWED_SERVERS
            )
        )
    return value


def resolve_database(database: Optional[str] = None) -> str:
    return (database or os.environ.get(ENV_DATABASE) or "master").strip() or "master"


def max_rows_ceiling() -> int:
    """The hard row cap applied to every result set, from the environment."""
    return max(1, min(_env_int(ENV_MAX_ROWS, _DEFAULT_MAX_ROWS), _HARD_MAX_ROWS))


def clamp_rows(requested: int) -> int:
    return max(1, min(int(requested), max_rows_ceiling()))


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------


def connection_settings(
    server: Optional[str] = None, database: Optional[str] = None
) -> Dict[str, Any]:
    """The effective connection settings, without connecting.

    Surfaced by the ``test_connection`` tool so the encryption and driver
    choices are never invisible.
    """
    resolved_server = resolve_server(server)
    resolved_db = resolve_database(database)
    driver = pick_driver(os.environ.get(ENV_DRIVER) or None)
    return {
        "driver": driver,
        "driver_major": _driver_major_version(driver),
        "server_hash": _hash_identifier(resolved_server),
        "database": resolved_db,
        "encrypt_mode": (os.environ.get(ENV_ENCRYPT) or "auto").strip().lower(),
        "login_timeout": _env_int(ENV_LOGIN_TIMEOUT, _DEFAULT_LOGIN_TIMEOUT),
        "query_timeout": _env_int(ENV_QUERY_TIMEOUT, _DEFAULT_QUERY_TIMEOUT),
        "isolation": os.environ.get(ENV_ISOLATION) or _DEFAULT_ISOLATION,
        "max_rows": max_rows_ceiling(),
        "readonly_intent": _env_flag(ENV_READONLY_INTENT),
        "integrated_auth": True,
    }


@contextmanager
def connection(
    server: Optional[str] = None,
    database: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
    query_timeout: Optional[int] = None,
) -> Iterator[Any]:
    """Yield a connection, and roll it back unconditionally on the way out.

    The rollback is the load-bearing read-only guarantee in this skill. The
    statement classifier in ``guardrails.py`` exists to produce a good error
    message; *this* is what makes a write impossible even if the classifier is
    fooled. Nothing here ever commits.

    Pass ``conn=`` to inject an already-open (or fake) connection: the caller
    then owns its lifecycle and this function will not close it.
    """
    injected = conn is not None
    if injected:
        handle = conn
    else:
        pyodbc = _require_pyodbc()
        conn_str = build_connection_string(
            resolve_server(server),
            resolve_database(database),
            driver=os.environ.get(ENV_DRIVER) or None,
            encrypt=(os.environ.get(ENV_ENCRYPT) or "auto"),
            login_timeout=_env_int(ENV_LOGIN_TIMEOUT, _DEFAULT_LOGIN_TIMEOUT),
            readonly_intent=_env_flag(ENV_READONLY_INTENT),
        )
        try:
            handle = pyodbc.connect(conn_str, autocommit=False)
        except Exception as exc:  # pragma: no cover - needs a real driver
            raise ConnectionError_(explain_odbc_error(exc, conn_str)) from exc

    handle.timeout = int(
        query_timeout
        if query_timeout is not None
        else _env_int(ENV_QUERY_TIMEOUT, _DEFAULT_QUERY_TIMEOUT)
    )
    try:
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


def _apply_session_settings(handle: Any) -> None:
    """Make this session incapable of blocking production for long.

    READ UNCOMMITTED is deliberate: these are metadata and profiling reads
    against a live production database, and taking shared locks on a claims
    table to learn its shape is not an acceptable trade. Every payload that
    depends on it reports ``approximate: true``.
    """
    isolation = (os.environ.get(ENV_ISOLATION) or _DEFAULT_ISOLATION).strip()
    if not re.fullmatch(r"[A-Z ]+", isolation.upper() or ""):
        isolation = _DEFAULT_ISOLATION
    cursor = handle.cursor()
    try:
        cursor.execute("SET LOCK_TIMEOUT {}".format(_LOCK_TIMEOUT_MS))
        cursor.execute("SET TRANSACTION ISOLATION LEVEL {}".format(isolation.upper()))
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def explain_odbc_error(exc: Exception, conn_str: str = "") -> str:
    """Turn a pyodbc error into something with a next action in it."""
    text = str(exc)
    state = ""
    match = re.search(r"\('?([0-9A-Z]{5})'?[,)]", text)
    if match:
        state = match.group(1)

    hints = {
        "IM002": (
            "The named ODBC driver is not installed. Run the "
            "list_odbc_drivers tool to see what this machine actually has."
        ),
        "28000": (
            "Login failed. With Integrated Auth this usually means the Windows "
            "account has no SQL login, or a Kerberos SPN is missing. Confirm "
            "with: sqlcmd -S <server> -d <db> -E -Q \"SELECT SUSER_SNAME()\""
        ),
        "08001": (
            "Could not reach the server. For a named instance the SQL Browser "
            "service (UDP 1434) must be reachable; if it is blocked, use "
            "HOST,PORT with the instance's static TCP port instead."
        ),
        "HYT00": "Timed out. Raise {} if the server is simply slow.".format(
            ENV_LOGIN_TIMEOUT
        ),
        "01000": (
            "TLS negotiation problem. On ODBC Driver 18 the default is "
            "Encrypt=yes with certificate validation; set {}=auto (the default "
            "here) or install a trusted certificate on the SQL host.".format(
                ENV_ENCRYPT
            )
        ),
    }
    hint = hints.get(state, "")
    safe = redact_connection_string(conn_str) if conn_str else ""
    pieces = [text]
    if hint:
        pieces.append(hint)
    if safe:
        pieces.append("Connection string (redacted): {}".format(safe))
    return " | ".join(pieces)
