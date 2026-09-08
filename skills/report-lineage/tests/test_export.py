"""Tests for reportlineage.export."""
from __future__ import annotations

import csv
import io
import json

from reportlineage.builder import build_estate
from reportlineage.duplicates import analyze_duplicates
from reportlineage.export import to_csv_edges, to_csv_nodes, to_html_summary, to_json, to_mermaid
from reportlineage.fingerprint import fingerprint_rdl


def test_to_json_round_trips_through_json_loads(sample_rdl_path, sample_dtsx_path, sample_conmgr_path):
    graph = build_estate(
        rdl_paths=[sample_rdl_path], dtsx_paths=[sample_dtsx_path], conmgr_paths=[sample_conmgr_path],
    )
    text = to_json(graph)
    parsed = json.loads(text)
    assert parsed["meta"]["name"]
    assert isinstance(parsed["nodes"], list)


def test_to_json_writes_file_when_path_given(tmp_path, sample_rdl_path):
    graph = build_estate(rdl_paths=[sample_rdl_path])
    out_path = tmp_path / "estate.json"
    text = to_json(graph, path=str(out_path))
    assert out_path.exists()
    assert json.loads(out_path.read_text(encoding="utf-8")) == json.loads(text)


def test_to_mermaid_produces_a_flowchart(sample_rdl_path, sample_dtsx_path, sample_conmgr_path):
    graph = build_estate(
        rdl_paths=[sample_rdl_path], dtsx_paths=[sample_dtsx_path], conmgr_paths=[sample_conmgr_path],
    )
    text = to_mermaid(graph)
    assert text.startswith("graph TD")
    assert "-->" in text


def test_to_csv_nodes_and_edges_are_parseable(sample_rdl_path, sample_dtsx_path, sample_conmgr_path):
    graph = build_estate(
        rdl_paths=[sample_rdl_path], dtsx_paths=[sample_dtsx_path], conmgr_paths=[sample_conmgr_path],
    )
    nodes_text = to_csv_nodes(graph)
    nodes_reader = csv.reader(io.StringIO(nodes_text))
    header = next(nodes_reader)
    assert header == [
        "id", "kind", "name", "qualified_name", "layer", "schema", "domain",
        "confidence", "provenance", "orphan", "source_path", "source_locator",
    ]
    node_rows = list(nodes_reader)
    assert len(node_rows) == len(graph.nodes)

    edges_text = to_csv_edges(graph)
    edges_reader = csv.reader(io.StringIO(edges_text))
    edges_header = next(edges_reader)
    assert edges_header == ["upstream_id", "downstream_id", "kind", "provenance", "confidence", "evidence"]
    edge_rows = list(edges_reader)
    assert len(edge_rows) == len(graph.edges)


def test_to_html_summary_contains_meta_name_and_duplicate_clusters(fixtures_dir):
    graph = build_estate(rdl_paths=[
        str(fixtures_dir / "sample_report.rdl"),
        str(fixtures_dir / "sample_report_near_dup.rdl"),
    ])
    fingerprints = [
        fingerprint_rdl(str(fixtures_dir / "sample_report.rdl"), key="a"),
        fingerprint_rdl(str(fixtures_dir / "sample_report_near_dup.rdl"), key="b"),
    ]
    duplicates = analyze_duplicates(fingerprints)

    html = to_html_summary(graph, duplicates=duplicates)
    assert "<html" in html
    assert graph.meta.name in html
    if duplicates.clusters:
        assert "Duplicate clusters" in html
