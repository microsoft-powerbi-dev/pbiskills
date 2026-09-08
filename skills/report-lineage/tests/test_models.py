"""Tests for reportlineage.models."""
from __future__ import annotations

from reportlineage.models import (
    Confidence,
    DEFAULT_LAYERS,
    EdgeKind,
    EstateGraph,
    EstateMeta,
    LineageEdge,
    LineageNode,
    Provenance,
    SourceRef,
)


def test_default_layers_has_six_layers_in_order():
    assert [layer.key for layer in DEFAULT_LAYERS] == [
        "source", "etl", "staging", "dw", "semantic", "report",
    ]
    assert [layer.id for layer in DEFAULT_LAYERS] == [0, 1, 2, 3, 4, 5]


def test_lineage_node_schema_alias_round_trip():
    node = LineageNode(
        id="sql:ordersdw::dbo.sales", kind="table", name="Sales",
        qualified_name="dbo.Sales", layer=3, schema="dbo",
    )
    assert node.schema_name == "dbo"
    dumped = node.model_dump(by_alias=True)
    assert dumped["schema"] == "dbo"
    assert "schema_name" not in dumped


def test_lineage_node_accepts_schema_name_kwarg_too():
    node = LineageNode(
        id="x", kind="table", name="X", qualified_name="dbo.X", layer=3,
        schema_name="dbo",
    )
    assert node.schema_name == "dbo"


def test_estate_graph_to_graph_json_uses_schema_alias_and_json_native_values():
    graph = EstateGraph(
        meta=EstateMeta(name="Test Estate"),
        nodes=[
            LineageNode(
                id="sql:ordersdw::dbo.sales", kind="table", name="Sales",
                qualified_name="dbo.Sales", layer=3, schema="dbo",
                confidence=Confidence.HIGH, provenance=Provenance.PARSER,
                source_ref=SourceRef(path="pkg.dtsx"),
            ),
        ],
        edges=[
            LineageEdge(upstream_id="a", downstream_id="b", kind=EdgeKind.DERIVES_FROM),
        ],
    )
    payload = graph.to_graph_json()
    assert payload["meta"]["name"] == "Test Estate"
    assert payload["nodes"][0]["schema"] == "dbo"
    assert "schema_name" not in payload["nodes"][0]
    # mode="json" renders enums as their plain string values.
    assert payload["nodes"][0]["confidence"] == "high"
    assert payload["edges"][0]["kind"] == "derives_from"


def test_estate_graph_defaults_are_empty_and_layers_populated():
    graph = EstateGraph()
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.diagnostics == []
    assert len(graph.layers) == 6
