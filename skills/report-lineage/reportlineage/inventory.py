"""
Inventory rollups over an estate's nodes: counts by object kind, plus the
confidence/provenance totals a trust view would render. Pure functions over a
node list, no I/O.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List

from reportlineage.models import DEFAULT_LAYERS, InventoryByKind, LineageNode

_LAYER_LABEL = {layer.id: layer.label for layer in DEFAULT_LAYERS}

# Human labels for the kinds this package can emit.
_KIND_LABEL = {
    "table": "Tables",
    "view": "Views",
    "stored_procedure": "Stored Procedures",
    "ssis_package": "SSIS Packages",
    "ssis_task": "SSIS Tasks",
    "ssis_component": "SSIS Components",
    "ssrs_report": "SSRS Reports",
    "ssrs_dataset": "SSRS Datasets",
}


def _kind_label(kind: str) -> str:
    return _KIND_LABEL.get(kind, kind.replace("_", " ").title())


def rollup_by_kind(nodes: List[LineageNode]) -> List[InventoryByKind]:
    """Count nodes by kind, labelling each with its representative layer."""
    counts: Counter = Counter(n.kind for n in nodes)
    # Representative layer = the most common layer among nodes of that kind.
    layer_by_kind: Dict[str, Counter] = {}
    for n in nodes:
        layer_by_kind.setdefault(n.kind, Counter())[n.layer] += 1

    out: List[InventoryByKind] = []
    for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        layer_id = layer_by_kind[kind].most_common(1)[0][0]
        out.append(InventoryByKind(
            kind=kind,
            label=_kind_label(kind),
            count=count,
            layer=_LAYER_LABEL.get(layer_id, ""),
        ))
    return out


def confidence_totals(nodes: List[LineageNode]) -> Dict[str, int]:
    c: Counter = Counter(n.confidence.value for n in nodes)
    return {k: c.get(k, 0) for k in ("high", "medium", "low", "inferred")}


def provenance_totals(nodes: List[LineageNode]) -> Dict[str, int]:
    c: Counter = Counter(n.provenance.value for n in nodes)
    return {k: c.get(k, 0) for k in ("parser", "referenced", "llm", "sidecar")}
