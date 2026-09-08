"""Tests for reportlineage.inventory."""
from __future__ import annotations

from reportlineage.inventory import confidence_totals, provenance_totals, rollup_by_kind
from reportlineage.models import Confidence, LAYER_DW, LAYER_REPORT, LineageNode, Provenance, SourceRef


def _node(node_id, kind, layer, confidence=Confidence.HIGH, provenance=Provenance.PARSER):
    return LineageNode(
        id=node_id, kind=kind, name=node_id, qualified_name=f"dbo.{node_id}",
        layer=layer, confidence=confidence, provenance=provenance,
        source_ref=SourceRef(),
    )


def test_rollup_by_kind_counts_and_labels():
    nodes = [
        _node("t1", "table", LAYER_DW),
        _node("t2", "table", LAYER_DW),
        _node("r1", "ssrs_report", LAYER_REPORT),
    ]
    rollup = rollup_by_kind(nodes)
    by_kind = {r.kind: r for r in rollup}
    assert by_kind["table"].count == 2
    assert by_kind["table"].label == "Tables"
    assert by_kind["ssrs_report"].count == 1
    assert by_kind["ssrs_report"].label == "SSRS Reports"
    # Sorted by count descending, so "table" (2) comes before "ssrs_report" (1).
    assert rollup[0].kind == "table"


def test_rollup_unknown_kind_gets_title_cased_label():
    nodes = [_node("x1", "semantic_widget", LAYER_DW)]
    rollup = rollup_by_kind(nodes)
    assert rollup[0].label == "Semantic Widget"


def test_confidence_totals_covers_all_buckets():
    nodes = [
        _node("t1", "table", LAYER_DW, confidence=Confidence.HIGH),
        _node("t2", "table", LAYER_DW, confidence=Confidence.LOW),
        _node("t3", "table", LAYER_DW, confidence=Confidence.LOW),
    ]
    totals = confidence_totals(nodes)
    assert totals == {"high": 1, "medium": 0, "low": 2, "inferred": 0}


def test_provenance_totals_covers_all_buckets():
    nodes = [
        _node("t1", "table", LAYER_DW, provenance=Provenance.PARSER),
        _node("t2", "table", LAYER_DW, provenance=Provenance.REFERENCED),
    ]
    totals = provenance_totals(nodes)
    assert totals == {"parser": 1, "referenced": 1, "llm": 0, "sidecar": 0}


def test_empty_node_list():
    assert rollup_by_kind([]) == []
    assert confidence_totals([]) == {"high": 0, "medium": 0, "low": 0, "inferred": 0}
    assert provenance_totals([]) == {"parser": 0, "referenced": 0, "llm": 0, "sidecar": 0}
