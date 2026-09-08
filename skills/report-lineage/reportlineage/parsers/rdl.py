"""
A narrow, lineage-only SSRS RDL parser.

Unlike a full IR-producing RDL parser, this module only extracts the facts
lineage needs: which data sources a report connects to (and their server /
database), which datasets it declares (and their query text), which
parameters it exposes, and a rough inventory of its visuals. Anything it
cannot parse cleanly is skipped rather than raised; a malformed sub-element
never aborts the whole parse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree.ElementTree import Element, ParseError
import xml.etree.ElementTree as ET

from reportlineage.connect_string import parse_connect_string
from reportlineage.xml_utils import child, descendants, text_of, sanitize_rdl_bytes

# Report-item tags this parser counts as "visuals" for the inventory.
_VISUAL_KINDS = {"Tablix", "Chart", "Gauge", "Map", "Subreport"}


@dataclass
class RdlDataset:
    name: str
    datasource_name: Optional[str] = None
    command_type: str = "Text"
    command_text: Optional[str] = None
    fields: List[str] = field(default_factory=list)


@dataclass
class RdlReport:
    name: str
    path: str
    connections: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    datasets: List[RdlDataset] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    visual_count: int = 0
    visual_kinds: List[str] = field(default_factory=list)


def _report_section(report: Element) -> Element:
    """The element that directly contains Body/Width/Page.

    RDL 2010/2016 wraps these in Report/ReportSections/ReportSection; RDL 2008
    places them directly under Report. Return whichever applies.
    """
    sections = child(report, "ReportSections")
    if sections is not None:
        rs = child(sections, "ReportSection")
        if rs is not None:
            return rs
    return report


def _parse_connections(report: Element) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Map embedded DataSource name to (server, database)."""
    out: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for ds_el in descendants(child(report, "DataSources"), "DataSource"):
        name = ds_el.get("Name")
        if not name:
            continue
        try:
            conn = child(ds_el, "ConnectionProperties")
            connect = text_of(conn, "ConnectString") if conn is not None else None
            out[name] = parse_connect_string(connect) if connect else (None, None)
        except Exception:
            out[name] = (None, None)
    return out


def _parse_datasets(report: Element) -> List[RdlDataset]:
    out: List[RdlDataset] = []
    for dset in descendants(child(report, "DataSets"), "DataSet"):
        try:
            name = dset.get("Name") or "DataSet"
            query = child(dset, "Query")
            command_type_raw = text_of(query, "CommandType") if query is not None else None
            command_type = (
                "StoredProcedure"
                if (command_type_raw or "").strip().lower() == "storedprocedure"
                else "Text"
            )
            command = text_of(query, "CommandText") if query is not None else None
            ds_name = text_of(query, "DataSourceName") if query is not None else None
            fields: List[str] = []
            for fld in descendants(child(dset, "Fields"), "Field"):
                fname = fld.get("Name")
                if fname:
                    fields.append(fname)
            out.append(RdlDataset(
                name=name,
                datasource_name=ds_name,
                command_type=command_type,
                command_text=command,
                fields=fields,
            ))
        except Exception:
            # A single malformed dataset must not abort the whole parse.
            continue
    return out


def _parse_parameters(report: Element) -> List[str]:
    out: List[str] = []
    for p in descendants(child(report, "ReportParameters"), "ReportParameter"):
        name = p.get("Name")
        if name:
            out.append(name)
    return out


def _count_visuals(report: Element) -> Tuple[int, List[str]]:
    kinds: List[str] = []
    section = _report_section(report)
    body = child(section, "Body")
    if body is None:
        return 0, kinds
    for kind in _VISUAL_KINDS:
        found = descendants(body, kind)
        kinds.extend([kind] * len(found))
    return len(kinds), kinds


def parse_rdl_lineage(path: str) -> RdlReport:
    """Parse an SSRS ``.rdl`` file into a narrow :class:`RdlReport`.

    Never raises: a file that cannot be parsed at all yields an
    :class:`RdlReport` with empty connections/datasets/parameters.
    """
    report_name = Path(path).stem or "Report"
    result = RdlReport(name=report_name, path=str(path))

    try:
        tree = ET.parse(sanitize_rdl_bytes(path))
    except (ParseError, OSError):
        return result

    root = tree.getroot()
    try:
        result.connections = _parse_connections(root)
    except Exception:
        result.connections = {}
    try:
        result.datasets = _parse_datasets(root)
    except Exception:
        result.datasets = []
    try:
        result.parameters = _parse_parameters(root)
    except Exception:
        result.parameters = []
    try:
        count, kinds = _count_visuals(root)
        result.visual_count = count
        result.visual_kinds = kinds
    except Exception:
        result.visual_count = 0
        result.visual_kinds = []

    return result
