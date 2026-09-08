"""
A narrow, lineage-only SSIS ``.dtsx`` package parser.

Extracts the data-lineage evidence from an Integration Services package
without executing it: OLE DB data-flow source/destination tables and
Execute SQL task statements. Connection managers (project ``.conmgr`` files
or package-embedded ones) resolve each table's database. This is a static,
best-effort pass; anything it cannot resolve (a missing connection manager, a
variable-driven rowset, dynamic SQL) is appended to ``diagnostics`` rather
than raised, so a single malformed package never aborts a caller's scan.

DTSX shape (namespaces ``DTS:`` and ``SQLTask:`` are navigated by local name):

    <DTS:Executable ExecutableType="Microsoft.Package">
      <DTS:Executables>
        <DTS:Executable ExecutableType="Microsoft.Pipeline">
          <DTS:ObjectData><pipeline><components>
            <component componentClassID="Microsoft.OLEDBSource|Destination">
              <properties><property name="OpenRowset|SqlCommand">...</property>
              <connections><connection connectionManagerRefId="...[NAME]"
                                        connectionManagerID="{GUID}:external"/>
        <DTS:Executable ExecutableType="Microsoft.ExecuteSQLTask">
          <DTS:ObjectData><SQLTask:SqlTaskData SQLTask:Connection="{GUID}"
                                               SQLTask:SqlStatementSource="..."/>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree.ElementTree import Element, ParseError, parse as et_parse

from reportlineage.connect_string import parse_connect_string
from reportlineage.sql_refs import SqlRefs, TableRef, extract_table_refs, make_table_ref
from reportlineage.xml_utils import child, descendants

# Component class IDs this parser recognizes.
_OLEDB_SOURCE = "microsoft.oledbsource"
_OLEDB_DEST = "microsoft.oledbdestination"

# ...ConnectionManagers[NAME] -> NAME
_RE_CM_REF = re.compile(r"\[([^\]]+)\]")
# {7C3E3ECC-...}:external -> 7C3E3ECC-...
_RE_GUID = re.compile(r"\{?([0-9A-Fa-f\-]{36})\}?")


@dataclass(frozen=True)
class SsisConnection:
    name: str
    server: Optional[str] = None
    database: Optional[str] = None
    raw: Optional[str] = None
    dtsid: Optional[str] = None


@dataclass
class SsisTableRef:
    table_ref: TableRef
    operation: str = "read"


@dataclass
class SsisPackage:
    name: str
    path: str
    connections: List[SsisConnection] = field(default_factory=list)
    reads: List[TableRef] = field(default_factory=list)
    writes: List[TableRef] = field(default_factory=list)
    sql_tasks: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)


def norm_guid(raw: Optional[str]) -> Optional[str]:
    """Normalize a ``{GUID}`` or ``{GUID}:external``-style value to bare upper-case hex.

    Public (not underscore-prefixed) because :mod:`reportlineage.parsers.conmgr`
    reuses it to match a ``.conmgr`` file's ``DTSID`` against a package's
    connection references.
    """
    if not raw:
        return None
    m = _RE_GUID.search(raw)
    return m.group(1).upper() if m else None


def ns_attr(el: Optional[Element], local: str) -> Optional[str]:
    """Attribute value by local name, ignoring the namespace prefix.

    SSIS stores most data in ``DTS:``/``SQLTask:``-prefixed attributes whose
    ElementTree keys are ``{namespace}local``; component-level attributes
    (``componentClassID``, ``name``, ``connectionManagerRefId``) are
    unprefixed. Both are matched here. Public because
    :mod:`reportlineage.parsers.conmgr` reuses it for the same namespace-agnostic
    lookup on a standalone connection-manager document.
    """
    if el is None:
        return None
    target = local.lower()
    for key, val in el.attrib.items():
        k = key.split("}", 1)[1] if "}" in key else key
        if k.lower() == target:
            return val
    return None


# Private aliases kept for readability at call sites within this module.
_norm_guid = norm_guid
_ns_attr = ns_attr


def _build_lookup(connections: List[SsisConnection]):
    by_name = {c.name.lower(): c for c in connections if c.name}
    by_dtsid = {c.dtsid: c for c in connections if c.dtsid}
    return by_name, by_dtsid


def _component_property(component: Element, name: str) -> Optional[str]:
    for prop in descendants(component, "property"):
        if (prop.get("name") or "").lower() == name.lower():
            return (prop.text or "").strip()
    return None


def _resolve_component_connection(
    component: Element, by_name: Dict[str, SsisConnection], by_dtsid: Dict[str, SsisConnection]
) -> Optional[SsisConnection]:
    conn_el = None
    for c in descendants(component, "connection"):
        conn_el = c
        break
    if conn_el is None:
        return None
    ref_id = conn_el.get("connectionManagerRefId") or ""
    m = _RE_CM_REF.search(ref_id)
    if m and m.group(1).lower() in by_name:
        return by_name[m.group(1).lower()]
    guid = _norm_guid(conn_el.get("connectionManagerID"))
    if guid and guid in by_dtsid:
        return by_dtsid[guid]
    return None


def _parse_embedded_connections(root: Element) -> List[SsisConnection]:
    out: List[SsisConnection] = []
    for cm in descendants(child(root, "ConnectionManagers"), "ConnectionManager"):
        cm_name = _ns_attr(cm, "ObjectName")
        if not cm_name:
            continue
        connect = None
        for inner in descendants(cm, "ConnectionManager"):
            connect = _ns_attr(inner, "ConnectionString")
            if connect:
                break
        server, db = parse_connect_string(connect)
        out.append(SsisConnection(
            name=cm_name, dtsid=_norm_guid(_ns_attr(cm, "DTSID")),
            server=server, database=db, raw=connect,
        ))
    return out


def _parse_data_flow_component(
    component: Element,
    by_name: Dict[str, SsisConnection],
    by_dtsid: Dict[str, SsisConnection],
    pkg: SsisPackage,
) -> None:
    class_id = (component.get("componentClassID") or "").lower()
    if class_id not in (_OLEDB_SOURCE, _OLEDB_DEST):
        return
    comp_name = component.get("name") or "component"
    conn = _resolve_component_connection(component, by_name, by_dtsid)
    db = conn.database if conn else None
    if conn is None:
        pkg.diagnostics.append(
            f"connection manager for component '{comp_name}' could not be resolved; "
            "database is unknown for this component"
        )

    open_rowset = _component_property(component, "OpenRowset")
    sql_command = _component_property(component, "SqlCommand")
    rowset_var = _component_property(component, "OpenRowsetVariable")

    if class_id == _OLEDB_DEST:
        if open_rowset:
            ref = make_table_ref(open_rowset, database=db, operation="write")
            if ref:
                pkg.writes.append(ref)
        elif rowset_var:
            pkg.diagnostics.append(
                f"destination '{comp_name}' uses a variable-driven rowset; target table unresolved"
            )
    else:  # OLE DB Source
        if sql_command:
            refs = extract_table_refs(sql_command, database=db)
            pkg.reads.extend(refs.reads)
            pkg.reads.extend(refs.execs)
            if refs.dynamic:
                pkg.diagnostics.append(
                    f"source '{comp_name}' uses dynamic SQL; some source tables may be missing"
                )
        elif open_rowset:
            ref = make_table_ref(open_rowset, database=db, operation="read")
            if ref:
                pkg.reads.append(ref)
        elif rowset_var:
            pkg.diagnostics.append(
                f"source '{comp_name}' uses a variable-driven rowset; source table unresolved"
            )


def _parse_data_flow(
    pipeline_exec: Element,
    by_name: Dict[str, SsisConnection],
    by_dtsid: Dict[str, SsisConnection],
    pkg: SsisPackage,
) -> None:
    for component in descendants(pipeline_exec, "component"):
        try:
            _parse_data_flow_component(component, by_name, by_dtsid, pkg)
        except Exception as exc:
            pkg.diagnostics.append(f"could not parse data-flow component: {exc}")


def _parse_sql_task(
    exec_el: Element,
    by_dtsid: Dict[str, SsisConnection],
    pkg: SsisPackage,
) -> None:
    task_data = None
    for td in descendants(exec_el, "SqlTaskData"):
        task_data = td
        break
    if task_data is None:
        return
    statement = _ns_attr(task_data, "SqlStatementSource") or ""
    if not statement.strip():
        return
    conn_guid = _norm_guid(_ns_attr(task_data, "Connection"))
    conn = by_dtsid.get(conn_guid) if conn_guid else None
    db = conn.database if conn else None
    pkg.sql_tasks.append(statement)
    refs = extract_table_refs(statement, database=db)
    if refs.dynamic:
        pkg.diagnostics.append(
            "an Execute SQL task contains dynamic SQL; referenced tables may be incomplete"
        )
    pkg.reads.extend(refs.reads)
    pkg.reads.extend(refs.execs)
    pkg.writes.extend(refs.writes)


def parse_dtsx_lineage(path: str, connections: Optional[List[SsisConnection]] = None) -> SsisPackage:
    """Parse a ``.dtsx`` package into an :class:`SsisPackage` of lineage evidence.

    ``connections`` are typically parsed ahead of time from sibling ``.conmgr``
    project files (see :mod:`reportlineage.parsers.conmgr`); they are merged
    with any connection managers embedded directly in the package.
    """
    pkg_name = Path(path).stem or "package"
    pkg = SsisPackage(name=pkg_name, path=str(path), connections=list(connections or []))

    try:
        root = et_parse(path).getroot()
    except (ParseError, OSError) as exc:
        pkg.diagnostics.append(f"could not parse package XML: {exc}")
        return pkg

    pkg.connections = pkg.connections + _parse_embedded_connections(root)
    by_name, by_dtsid = _build_lookup(pkg.connections)

    for exec_el in descendants(root, "Executable"):
        try:
            etype = _ns_attr(exec_el, "ExecutableType") or ""
            if etype == "Microsoft.Pipeline":
                _parse_data_flow(exec_el, by_name, by_dtsid, pkg)
            elif etype == "Microsoft.ExecuteSQLTask":
                _parse_sql_task(exec_el, by_dtsid, pkg)
        except Exception as exc:
            pkg.diagnostics.append(f"could not parse executable: {exc}")

    return pkg
