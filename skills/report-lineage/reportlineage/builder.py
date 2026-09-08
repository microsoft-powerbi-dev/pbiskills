"""
Estate graph builder: turns parsed SSIS packages, SSRS reports, and standalone
SQL files into a single :class:`~reportlineage.models.EstateGraph` of nodes,
edges, and diagnostics.

Linking is by table signature (``database::schema.table``): because every
table node's id is derived from its signature, an SSIS destination table and
an RDL dataset source table that are the same physical table collapse to one
node, so the path "source -> package -> table -> dataset -> report" forms
automatically. :func:`link_estate` adds the explicit "package -> consumer"
edge that directly answers "which reports depend on which ETL packages".

All functions tolerate malformed input: a single bad file yields a
diagnostic, never an exception, so an estate scan always completes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from reportlineage.models import (
    Confidence,
    EdgeKind,
    EstateGraph,
    EstateMeta,
    LAYER_DW,
    LAYER_ETL,
    LAYER_REPORT,
    LAYER_SOURCE,
    LAYER_STAGING,
    LineageDiagnostic,
    LineageEdge,
    LineageNode,
    NodeKind,
    Provenance,
    Severity,
    SourceRef,
)
from reportlineage.parsers.conmgr import parse_conmgr
from reportlineage.parsers.dtsx import SsisPackage, parse_dtsx_lineage
from reportlineage.parsers.rdl import parse_rdl_lineage
from reportlineage.parsers.sql_file import classify_sql_object
from reportlineage.sql_refs import TableRef, extract_table_refs, make_table_ref

_STAGING_SCHEMAS = {"integration", "staging", "stg", "stage"}

_CONFIDENCE_RANK = {
    Confidence.HIGH: 3,
    Confidence.MEDIUM: 2,
    Confidence.LOW: 1,
    Confidence.INFERRED: 0,
}
_PROVENANCE_RANK = {
    Provenance.PARSER: 3,
    Provenance.REFERENCED: 2,
    Provenance.SIDECAR: 1,
    Provenance.LLM: 0,
}

# Callback: (server, database, qualified_proc_name_or_command) -> proc body T-SQL, or None.
ProcBodyLookup = Callable[[Optional[str], Optional[str], str], Optional[str]]

_DATA_KINDS = {NodeKind.TABLE, NodeKind.VIEW}
_NODE_KIND_BY_SQL_KIND = {
    "table": NodeKind.TABLE,
    "view": NodeKind.VIEW,
    "stored_procedure": NodeKind.STORED_PROCEDURE,
}


# ---------------------------------------------------------------------------
# Node / edge helpers
# ---------------------------------------------------------------------------


def _table_node_id(ref: TableRef) -> str:
    return f"sql:{ref.signature()}"


def _layer_for(ref: TableRef) -> int:
    db = (ref.database or "").lower()
    is_dw = db.endswith("dw") or db.endswith("dwh")
    sch = (ref.schema or "").lower()
    if ref.kind == "stored_procedure":
        return LAYER_STAGING if is_dw else (LAYER_SOURCE if db else LAYER_STAGING)
    if is_dw:
        if sch in _STAGING_SCHEMAS:
            return LAYER_STAGING
        return LAYER_DW
    if db:
        return LAYER_SOURCE
    return LAYER_DW  # unknown database: reports usually read the warehouse


def _table_node(ref: TableRef) -> LineageNode:
    db_known = bool(ref.database)
    return LineageNode(
        id=_table_node_id(ref),
        kind=ref.kind,
        name=ref.table,
        qualified_name=ref.qualified_name,
        layer=_layer_for(ref),
        schema=ref.schema,
        confidence=Confidence.HIGH if db_known else Confidence.MEDIUM,
        provenance=Provenance.PARSER if db_known else Provenance.REFERENCED,
        source_ref=SourceRef(locator=ref.signature()),
        attributes={"database": ref.database} if ref.database else {},
    )


def _edge(
    up: str,
    down: str,
    evidence: str,
    kind: EdgeKind = EdgeKind.DERIVES_FROM,
    confidence: Confidence = Confidence.HIGH,
) -> LineageEdge:
    return LineageEdge(upstream_id=up, downstream_id=down, kind=kind, evidence=evidence, confidence=confidence)


# ---------------------------------------------------------------------------
# RDL -> nodes
# ---------------------------------------------------------------------------


def nodes_from_rdl(
    path: str,
    proc_body_lookup: Optional[ProcBodyLookup] = None,
) -> Tuple[List[LineageNode], List[LineageEdge], List[LineageDiagnostic], str, Set[str]]:
    """Return (nodes, edges, diagnostics, report_id, read_table_ids) for one RDL file.

    When ``proc_body_lookup`` is supplied and a dataset invokes a stored
    procedure, the callback is asked for the proc body; table refs extracted
    from that body are added as extra reads on the dataset, letting lineage
    trace through the proc instead of stopping at its EXEC frontier.
    """
    nodes: List[LineageNode] = []
    edges: List[LineageEdge] = []
    diags: List[LineageDiagnostic] = []
    reads: Set[str] = set()

    report = parse_rdl_lineage(path)
    report_id = f"ssrs:{report.name}"
    nodes.append(LineageNode(
        id=report_id, kind=NodeKind.SSRS_REPORT, name=report.name,
        qualified_name=f"{report.name}.rdl", layer=LAYER_REPORT, schema="SSRS",
        source_ref=SourceRef(path=report.path, locator=report.name),
        attributes={"visuals": str(report.visual_count)},
    ))

    if not report.datasets and not report.connections:
        diags.append(LineageDiagnostic(
            kind="rdl_unresolved", severity=Severity.WARN,
            message="RDL could not be parsed, or declares no datasources/datasets",
            source_ref=SourceRef(path=report.path, locator=report.name), node_id=report_id,
        ))
        return nodes, edges, diags, report_id, reads

    for dset in report.datasets:
        ds_id = f"ssrs:{report.name}/{dset.name}"
        nodes.append(LineageNode(
            id=ds_id, kind=NodeKind.SSRS_DATASET, name=dset.name,
            qualified_name=f"{report.name}/{dset.name}", layer=LAYER_REPORT, schema="SSRS",
            source_ref=SourceRef(path=report.path, locator=f"{report.name}/{dset.name}"),
        ))
        edges.append(_edge(report_id, ds_id, "report contains dataset", kind=EdgeKind.CONTAINS))

        server, db = report.connections.get(dset.datasource_name or "", (None, None))
        refs = extract_table_refs(dset.command_text, database=db)
        source_refs: List[TableRef] = [*refs.reads, *refs.execs]

        if dset.command_type == "StoredProcedure" and proc_body_lookup is not None and dset.command_text:
            body = proc_body_lookup(server, db, dset.command_text.strip())
            if body:
                inner = extract_table_refs(body, database=db)
                source_refs.extend(inner.reads)
                source_refs.extend(inner.writes)
                source_refs.extend(inner.execs)

        unresolved = not source_refs or refs.dynamic
        if unresolved:
            diags.append(LineageDiagnostic(
                kind="ssrs_sql_unresolved", severity=Severity.WARN,
                message=(
                    f"dataset '{dset.name}' query could not be resolved to source tables"
                    if not source_refs else
                    f"dataset '{dset.name}' uses dynamic/expression SQL; lineage may be incomplete"
                ),
                source_ref=SourceRef(path=report.path, locator=f"{report.name}/{dset.name}"),
                node_id=ds_id,
                impact="The report-to-warehouse lineage for this dataset is unknown.",
            ))
            nodes[-1] = nodes[-1].model_copy(update={"confidence": Confidence.LOW})

        for ref in source_refs:
            nodes.append(_table_node(ref))
            tid = _table_node_id(ref)
            reads.add(tid)
            edges.append(_edge(tid, ds_id, f"Dataset CommandText: reads {ref.qualified_name}"))

    return nodes, edges, diags, report_id, reads


# ---------------------------------------------------------------------------
# SSIS -> nodes
# ---------------------------------------------------------------------------


def nodes_from_dtsx(
    pkg: SsisPackage,
    proc_body_lookup: Optional[ProcBodyLookup] = None,
) -> Tuple[List[LineageNode], List[LineageEdge], List[LineageDiagnostic], str, Set[str]]:
    """Return (nodes, edges, diagnostics, package_id, written_table_ids).

    ``proc_body_lookup``, when supplied, is used to trace through any
    stored-procedure call captured among ``pkg.reads`` (kind
    ``"stored_procedure"``): the proc body's own reads/writes are added as
    extra edges into the package.
    """
    nodes: List[LineageNode] = []
    edges: List[LineageEdge] = []
    diags: List[LineageDiagnostic] = []
    writes: Set[str] = set()

    pkg_id = f"ssis:{pkg.name}"
    nodes.append(LineageNode(
        id=pkg_id, kind=NodeKind.SSIS_PACKAGE, name=pkg.name,
        qualified_name=f"{pkg.name}.dtsx", layer=LAYER_ETL, schema="SSIS",
        source_ref=SourceRef(path=pkg.path, locator=pkg.name),
        attributes={"sql_tasks": str(len(pkg.sql_tasks))},
    ))

    for r in pkg.reads:
        nodes.append(_table_node(r))
        edges.append(_edge(_table_node_id(r), pkg_id, f"SSIS reads {r.qualified_name}"))
        if proc_body_lookup is not None and r.kind == "stored_procedure":
            body = proc_body_lookup(None, r.database, r.qualified_name)
            if body:
                inner = extract_table_refs(body, database=r.database)
                for inner_ref in [*inner.reads, *inner.writes]:
                    nodes.append(_table_node(inner_ref))
                    edges.append(_edge(
                        _table_node_id(inner_ref), pkg_id,
                        f"via proc {r.qualified_name}: {inner_ref.qualified_name}",
                    ))

    for w in pkg.writes:
        nodes.append(_table_node(w))
        tid = _table_node_id(w)
        writes.add(tid)
        edges.append(_edge(pkg_id, tid, f"SSIS writes {w.qualified_name}"))

    for message in pkg.diagnostics:
        diags.append(LineageDiagnostic(
            kind="ssis_diagnostic", severity=Severity.WARN, message=message,
            source_ref=SourceRef(path=pkg.path, locator=pkg.name), node_id=pkg_id,
            impact="Lineage for this package may be incomplete until the reference is resolved.",
        ))

    return nodes, edges, diags, pkg_id, writes


# ---------------------------------------------------------------------------
# .sql -> nodes
# ---------------------------------------------------------------------------


def nodes_from_sql(
    sql_obj: dict, key: str
) -> Tuple[List[LineageNode], List[LineageEdge], List[LineageDiagnostic], str, Set[str]]:
    """Return (nodes, edges, diagnostics, obj_id, written_table_ids) for one classified ``.sql`` object.

    ``sql_obj`` is the dict returned by
    :func:`reportlineage.parsers.sql_file.classify_sql_object`. ``key`` is a
    stable identifier for the source file (typically its path), used when the
    object's own name cannot be resolved to a qualified table/view/proc name.
    """
    nodes: List[LineageNode] = []
    edges: List[LineageEdge] = []
    diags: List[LineageDiagnostic] = []
    writes: Set[str] = set()

    kind = sql_obj.get("kind") or "unknown"
    name = sql_obj.get("name") or key
    node_kind = _NODE_KIND_BY_SQL_KIND.get(kind, NodeKind.VIEW)

    self_ref = make_table_ref(name, kind=kind if kind in ("table", "view") else "table") if name else None
    if self_ref is not None:
        obj_id = f"sql:{self_ref.signature()}"
        qualified_name = self_ref.qualified_name
        schema = self_ref.schema
        obj_name = self_ref.table
    else:
        obj_id = f"sql:unresolved::{key}"
        qualified_name = str(name)
        schema = None
        obj_name = str(name)

    nodes.append(LineageNode(
        id=obj_id, kind=node_kind, name=obj_name, qualified_name=qualified_name,
        layer=LAYER_DW, schema=schema,
        source_ref=SourceRef(path=key, locator=qualified_name),
        attributes={"sql_object_kind": kind},
    ))

    for r in sql_obj.get("reads") or []:
        nodes.append(_table_node(r))
        edges.append(_edge(_table_node_id(r), obj_id, f"{qualified_name} reads {r.qualified_name}"))

    for w in sql_obj.get("writes") or []:
        nodes.append(_table_node(w))
        tid = _table_node_id(w)
        writes.add(tid)
        edges.append(_edge(obj_id, tid, f"{qualified_name} writes {w.qualified_name}"))

    return nodes, edges, diags, obj_id, writes


# ---------------------------------------------------------------------------
# Linking, dedupe, orphans
# ---------------------------------------------------------------------------


def link_estate(pkg_writes: Dict[str, Set[str]], report_reads: Dict[str, Set[str]]) -> List[LineageEdge]:
    """Explicit "package -> consumer" edges where a written table is read downstream.

    ``report_reads`` is keyed by any consuming node id (an SSRS report or
    dataset), so an ETL package links to every downstream artifact that reads
    a table it writes.
    """
    edges: List[LineageEdge] = []
    for pkg_id, written in pkg_writes.items():
        for consumer_id, read in report_reads.items():
            shared = written & read
            if shared:
                names = ", ".join(sorted(s.split("::", 1)[-1] for s in shared))
                edges.append(_edge(
                    pkg_id, consumer_id,
                    f"writes {names}, consumed downstream",
                    kind=EdgeKind.REFERENCES,
                ))
    return edges


def _merge_node(into: LineageNode, other: LineageNode) -> LineageNode:
    """Combine two nodes with the same id, keeping the stronger evidence."""
    best_conf = into.confidence if _CONFIDENCE_RANK[into.confidence] >= _CONFIDENCE_RANK[other.confidence] else other.confidence
    best_prov = into.provenance if _PROVENANCE_RANK[into.provenance] >= _PROVENANCE_RANK[other.provenance] else other.provenance
    attrs = {**other.attributes, **into.attributes}
    return into.model_copy(update={
        "confidence": best_conf,
        "provenance": best_prov,
        "schema_name": into.schema_name or other.schema_name,
        "domain": into.domain or other.domain,
        "attributes": attrs,
        "source_ref": into.source_ref if into.source_ref.path else other.source_ref,
    })


def _dedupe_nodes(nodes: List[LineageNode]) -> List[LineageNode]:
    by_id: Dict[str, LineageNode] = {}
    for n in nodes:
        existing = by_id.get(n.id)
        by_id[n.id] = _merge_node(existing, n) if existing else n
    return list(by_id.values())


def _dedupe_edges(edges: List[LineageEdge]) -> List[LineageEdge]:
    seen: Set[Tuple[str, str, str]] = set()
    out: List[LineageEdge] = []
    for e in edges:
        key = (e.upstream_id, e.downstream_id, e.kind.value)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _mark_orphans(nodes: List[LineageNode], edges: List[LineageEdge]) -> None:
    has_downstream = {e.upstream_id for e in edges}
    for n in nodes:
        if n.kind in _DATA_KINDS and n.id not in has_downstream:
            n.orphan = True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_estate(
    rdl_paths: Sequence[str] = (),
    dtsx_paths: Sequence[str] = (),
    conmgr_paths: Sequence[str] = (),
    sql_paths: Sequence[str] = (),
    meta: Optional[EstateMeta] = None,
    proc_body_lookup: Optional[ProcBodyLookup] = None,
) -> EstateGraph:
    """Build an :class:`~reportlineage.models.EstateGraph` from RDL reports, SSIS
    packages, SSIS connection managers, and standalone SQL files.

    Every file is parsed independently inside a try/except: a single
    unreadable or malformed file yields a diagnostic and the build continues,
    it never aborts the whole scan.
    """
    rdl_paths = list(rdl_paths)
    dtsx_paths = list(dtsx_paths)
    conmgr_paths = list(conmgr_paths)
    sql_paths = list(sql_paths)

    all_diags: List[LineageDiagnostic] = []

    connections = []
    for p in conmgr_paths:
        try:
            conn = parse_conmgr(p)
            if conn is not None:
                connections.append(conn)
            else:
                all_diags.append(LineageDiagnostic(
                    kind="conmgr_unreadable", severity=Severity.WARN,
                    message=f"could not interpret connection manager: {p}",
                    source_ref=SourceRef(path=p),
                ))
        except Exception as exc:
            all_diags.append(LineageDiagnostic(
                kind="conmgr_unreadable", severity=Severity.WARN,
                message=f"error parsing connection manager '{p}': {exc}",
                source_ref=SourceRef(path=p),
            ))

    all_nodes: List[LineageNode] = []
    all_edges: List[LineageEdge] = []
    pkg_writes: Dict[str, Set[str]] = {}
    report_reads: Dict[str, Set[str]] = {}

    for p in dtsx_paths:
        try:
            pkg = parse_dtsx_lineage(p, connections=connections)
            nodes, edges, diags, pkg_id, writes = nodes_from_dtsx(pkg, proc_body_lookup)
            all_nodes += nodes
            all_edges += edges
            all_diags += diags
            pkg_writes[pkg_id] = writes
        except Exception as exc:
            all_diags.append(LineageDiagnostic(
                kind="dtsx_parse_error", severity=Severity.ERROR,
                message=f"failed to parse SSIS package '{p}': {exc}",
                source_ref=SourceRef(path=p),
            ))

    for p in sql_paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
            obj = classify_sql_object(text, filename=p)
            nodes, edges, diags, obj_id, writes = nodes_from_sql(obj, key=p)
            all_nodes += nodes
            all_edges += edges
            all_diags += diags
            if writes:
                pkg_writes[obj_id] = pkg_writes.get(obj_id, set()) | writes
        except Exception as exc:
            all_diags.append(LineageDiagnostic(
                kind="sql_parse_error", severity=Severity.ERROR,
                message=f"failed to parse SQL file '{p}': {exc}",
                source_ref=SourceRef(path=p),
            ))

    for p in rdl_paths:
        try:
            nodes, edges, diags, report_id, reads = nodes_from_rdl(p, proc_body_lookup)
            all_nodes += nodes
            all_edges += edges
            all_diags += diags
            report_reads[report_id] = reads
        except Exception as exc:
            all_diags.append(LineageDiagnostic(
                kind="rdl_parse_error", severity=Severity.ERROR,
                message=f"failed to parse RDL report '{p}': {exc}",
                source_ref=SourceRef(path=p),
            ))

    all_edges += link_estate(pkg_writes, report_reads)

    nodes = _dedupe_nodes(all_nodes)
    edges = _dedupe_edges(all_edges)
    _mark_orphans(nodes, edges)

    # Local import: avoids a module-load cycle (inventory imports models, not builder).
    from reportlineage.inventory import confidence_totals, provenance_totals, rollup_by_kind

    orphan_count = sum(1 for n in nodes if n.orphan)
    blocker_count = sum(1 for d in all_diags if d.severity == Severity.BLOCKER)
    totals = {
        "objects": len(nodes),
        "edges": len(edges),
        "orphans": orphan_count,
        "diagnostics": len(all_diags),
        "datasets": len(rdl_paths) + len(dtsx_paths) + len(sql_paths),
    }
    kpis = {
        "objects": float(len(nodes)),
        "orphans": float(orphan_count),
        "orphanPct": round(100.0 * orphan_count / len(nodes), 1) if nodes else 0.0,
        "edges": float(len(edges)),
        "diagnostics": float(len(all_diags)),
        "blockers": float(blocker_count),
    }

    scanned_at = datetime.now().isoformat()
    if meta is None:
        meta = EstateMeta(name="BI Estate", source_platform="SSRS / SSIS", scanned_at=scanned_at)
    elif meta.scanned_at is None:
        meta = meta.model_copy(update={"scanned_at": scanned_at})

    return EstateGraph(
        meta=meta,
        nodes=nodes,
        edges=edges,
        diagnostics=all_diags,
        inventory_by_kind=rollup_by_kind(nodes),
        confidence=confidence_totals(nodes),
        provenance=provenance_totals(nodes),
        totals=totals,
        kpis=kpis,
    )
