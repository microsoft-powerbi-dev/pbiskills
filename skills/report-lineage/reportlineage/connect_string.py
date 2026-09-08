"""
Shared OLE DB / ADO.NET connection-string parsing.

Both the RDL parser and the SSIS ``.dtsx``/``.conmgr`` parsers (whose
connection managers use the same ``key=value;`` form) resolve a connection's
server + database through this one function, so the parsing logic is not
duplicated across sources.
"""
from __future__ import annotations

from typing import Optional, Tuple

# Keys that name the host/instance vs. the catalog, across OLE DB + ADO.NET.
_SERVER_KEYS = ("data source", "server", "address", "addr", "network address")
_DATABASE_KEYS = ("initial catalog", "database")


def parse_connect_string(connect: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extract ``(server, database)`` from an OLE DB / ADO.NET connection string.

    Returns ``(None, None)`` for an empty/None input. Unknown keys are ignored.
    """
    server: Optional[str] = None
    db: Optional[str] = None
    if not connect:
        return server, db
    for part in connect.split(";"):
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        k = key.strip().lower()
        v = val.strip()
        if k in _SERVER_KEYS:
            server = v
        elif k in _DATABASE_KEYS:
            db = v
    return server, db
