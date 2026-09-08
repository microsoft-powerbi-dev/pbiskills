"""
Pure export functions for an :class:`~reportlineage.models.EstateGraph` (and
optionally a duplicate-analysis summary): JSON, Mermaid, CSV, and a
self-contained HTML report. Every function returns the rendered text; passing
``path`` additionally writes it to disk. No other side effects.
"""
from __future__ import annotations

import csv
import io
import json
import re
from html import escape
from typing import List, Optional

from reportlineage.models import EstateGraph

_MERMAID_STRIP = re.compile(r'["\[\]{}()]')


def to_json(graph: EstateGraph, path: Optional[str] = None) -> str:
    """Pretty-printed JSON of :meth:`EstateGraph.to_graph_json`."""
    text = json.dumps(graph.to_graph_json(), indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


def _sanitize_label(text: str) -> str:
    """Strip characters that break Mermaid node-label syntax."""
    cleaned = _MERMAID_STRIP.sub("", text or "").replace("\n", " ").strip()
    return cleaned or "node"


def to_mermaid(graph: EstateGraph, max_nodes: int = 200) -> str:
    """A ``graph TD`` Mermaid flowchart, one line per edge, truncated to ``max_nodes``."""
    nodes = graph.nodes[:max_nodes]
    truncated = len(graph.nodes) > max_nodes
    included_ids = {n.id for n in nodes}
    mermaid_id = {n.id: f"n{i}" for i, n in enumerate(nodes)}
    label_by_id = {n.id: _sanitize_label(n.name) for n in nodes}

    lines: List[str] = ["graph TD"]
    seen_pairs = set()
    for edge in graph.edges:
        if edge.upstream_id not in included_ids or edge.downstream_id not in included_ids:
            continue
        pair = (edge.upstream_id, edge.downstream_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        up_id = mermaid_id[edge.upstream_id]
        down_id = mermaid_id[edge.downstream_id]
        up_label = label_by_id[edge.upstream_id]
        down_label = label_by_id[edge.downstream_id]
        lines.append(f'    {up_id}["{up_label}"] --> {down_id}["{down_label}"]')

    if not seen_pairs:
        # No edges among the included nodes: still show the nodes themselves,
        # so a graph with objects but no relationships is not an empty diagram.
        for node_id, mid in mermaid_id.items():
            lines.append(f'    {mid}["{label_by_id[node_id]}"]')

    if truncated:
        lines.append(f"    %% truncated: showing {len(nodes)} of {len(graph.nodes)} nodes")

    return "\n".join(lines)


def _write_csv(rows: List[dict], fieldnames: List[str], path: Optional[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    text = buf.getvalue()
    if path:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return text


def to_csv_nodes(graph: EstateGraph, path: Optional[str] = None) -> str:
    """CSV of every node, one row each."""
    fieldnames = [
        "id", "kind", "name", "qualified_name", "layer", "schema", "domain",
        "confidence", "provenance", "orphan", "source_path", "source_locator",
    ]
    rows = []
    for n in graph.nodes:
        rows.append({
            "id": n.id,
            "kind": n.kind,
            "name": n.name,
            "qualified_name": n.qualified_name,
            "layer": n.layer,
            "schema": n.schema_name or "",
            "domain": n.domain or "",
            "confidence": n.confidence.value,
            "provenance": n.provenance.value,
            "orphan": n.orphan,
            "source_path": n.source_ref.path or "",
            "source_locator": n.source_ref.locator or "",
        })
    return _write_csv(rows, fieldnames, path)


def to_csv_edges(graph: EstateGraph, path: Optional[str] = None) -> str:
    """CSV of every edge, one row each."""
    fieldnames = ["upstream_id", "downstream_id", "kind", "provenance", "confidence", "evidence"]
    rows = []
    for e in graph.edges:
        rows.append({
            "upstream_id": e.upstream_id,
            "downstream_id": e.downstream_id,
            "kind": e.kind.value,
            "provenance": e.provenance.value,
            "confidence": e.confidence.value,
            "evidence": e.evidence or "",
        })
    return _write_csv(rows, fieldnames, path)


_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 24px; color: #1a1a1a; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 16px; margin-top: 28px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 8px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
th {{ background: #f2f2f2; }}
.kpis span {{ display: inline-block; margin-right: 24px; font-size: 13px; }}
.kpis b {{ font-size: 18px; display: block; }}
</style>
</head>
<body>
<h1>{title}</h1>
<p>Source platform: {source_platform}, target platform: {target_platform}, scanned at: {scanned_at}</p>
<div class="kpis">{kpis_html}</div>
<h2>Inventory by kind</h2>
{inventory_table}
<h2>Diagnostics</h2>
{diagnostics_table}
{duplicates_section}
</body>
</html>
"""


def _table(headers: List[str], rows: List[List[object]]) -> str:
    if not rows:
        return "<p>None.</p>"
    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(str(c))}</td>" for c in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def to_html_summary(
    graph: EstateGraph, duplicates: Optional[object] = None, path: Optional[str] = None
) -> str:
    """A single self-contained HTML report: meta, kpis, inventory, diagnostics, duplicates."""
    kpis_html = "".join(
        f"<span><b>{escape(str(v))}</b>{escape(str(k))}</span>" for k, v in graph.kpis.items()
    )
    inventory_rows = [[row.kind, row.label, row.count, row.layer] for row in graph.inventory_by_kind]
    inventory_table = _table(["Kind", "Label", "Count", "Layer"], inventory_rows)

    diagnostics_rows = [
        [d.severity.value, d.message, d.source_ref.path or ""] for d in graph.diagnostics
    ]
    diagnostics_table = _table(["Severity", "Message", "Source"], diagnostics_rows)

    duplicates_section = ""
    clusters = getattr(duplicates, "clusters", None) if duplicates is not None else None
    if clusters:
        rows = []
        for cluster in clusters:
            members = getattr(cluster, "members", []) or []
            member_names = ", ".join(str(getattr(m, "name", getattr(m, "key", ""))) for m in members)
            keeper_key = getattr(cluster, "keeper", "")
            keeper_name = keeper_key
            for m in members:
                if getattr(m, "key", None) == keeper_key:
                    keeper_name = getattr(m, "name", keeper_key)
                    break
            rows.append([keeper_name, member_names, getattr(cluster, "verdict", ""), getattr(cluster, "action", "")])
        duplicates_table = _table(["Keeper", "Members", "Verdict", "Action"], rows)
        duplicates_section = f"<h2>Duplicate clusters</h2>{duplicates_table}"

    html = _HTML_TEMPLATE.format(
        title=escape(graph.meta.name),
        source_platform=escape(graph.meta.source_platform or "unknown"),
        target_platform=escape(graph.meta.target_platform or "unknown"),
        scanned_at=escape(graph.meta.scanned_at or ""),
        kpis_html=kpis_html,
        inventory_table=inventory_table,
        diagnostics_table=diagnostics_table,
        duplicates_section=duplicates_section,
    )
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
    return html
