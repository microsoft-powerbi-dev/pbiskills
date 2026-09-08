"""
reportlineage: a portable, standalone lineage toolkit for SSRS/SSIS estates.

Scope: parse SSRS ``.rdl`` reports, SSIS ``.dtsx`` packages, SSIS project
``.conmgr`` connection managers, and standalone ``.sql`` DDL files, then join
them by table signature (``database::schema.table``) into a single
:class:`~reportlineage.models.EstateGraph` of nodes, edges, and diagnostics.

This package has zero import-time dependency on any host application. It does
not import from ``app.*``, ``backend.*``, or ``shared.*``. It depends only on
``pydantic`` (required) and, optionally, ``sqlglot`` (a guarded import that
degrades gracefully to a regex-based SQL scanner when absent). Everything
else is Python standard library. It is designed to be copied into another
repository and used as-is, or installed as its own distribution.

Typical usage::

    from reportlineage import build_estate

    graph = build_estate(
        rdl_paths=["report.rdl"],
        dtsx_paths=["package.dtsx"],
        conmgr_paths=["package.conmgr"],
    )
    graph_json = graph.to_graph_json()

The most-used names are re-exported here for a friendly top-level API; the
full surface is available from the individual submodules.
"""
from __future__ import annotations

from reportlineage.builder import build_estate
from reportlineage.duplicates import analyze_duplicates, find_reuse
from reportlineage.fingerprint import ReportFingerprint, build_fingerprint_from_rdl_report, fingerprint_rdl
from reportlineage.models import EstateGraph, LineageEdge, LineageNode
from reportlineage.parsers.dtsx import parse_dtsx_lineage
from reportlineage.parsers.rdl import parse_rdl_lineage
from reportlineage.search import SearchIndex
from reportlineage.signature import table_signature

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "EstateGraph",
    "LineageNode",
    "LineageEdge",
    "table_signature",
    "build_estate",
    "parse_rdl_lineage",
    "parse_dtsx_lineage",
    "ReportFingerprint",
    "build_fingerprint_from_rdl_report",
    "fingerprint_rdl",
    "analyze_duplicates",
    "find_reuse",
    "SearchIndex",
]
