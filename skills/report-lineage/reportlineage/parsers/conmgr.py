"""
A narrow parser for SSIS project connection-manager files (``.conmgr``).

A ``.conmgr`` file is a small standalone XML document describing one
connection manager: its name and its connection string. The connection
string typically lives on a nested ``DTS:ConnectionManager`` element's
``DTS:ConnectionString`` attribute; the outer root carries the object's name
(``DTS:ObjectName``) and id (``DTS:DTSID``). Navigation is namespace-agnostic
by local attribute/tag name, matching every other parser in this package, so
it tolerates whichever DTS schema version produced the file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from xml.etree.ElementTree import ParseError, parse as et_parse

from reportlineage.connect_string import parse_connect_string
from reportlineage.parsers.dtsx import SsisConnection, norm_guid, ns_attr
from reportlineage.xml_utils import descendants


def parse_conmgr(path: str) -> Optional[SsisConnection]:
    """Parse a project ``.conmgr`` file into an :class:`SsisConnection`.

    Returns ``None`` (never raises) when the file cannot be read or parsed.
    """
    try:
        root = et_parse(path).getroot()
    except (ParseError, OSError):
        return None

    name = ns_attr(root, "ObjectName") or Path(path).stem
    dtsid = norm_guid(ns_attr(root, "DTSID"))

    connect = ns_attr(root, "ConnectionString")
    if not connect:
        for cm in descendants(root, "ConnectionManager"):
            connect = ns_attr(cm, "ConnectionString")
            if connect:
                break
    if not connect:
        for settings in descendants(root, "ConnectionManagerSettings"):
            connect = ns_attr(settings, "ConnectionString")
            if connect:
                break

    server, db = parse_connect_string(connect)
    return SsisConnection(name=name, server=server, database=db, raw=connect, dtsid=dtsid)
