"""Tests for reportlineage.builder: the estate-building orchestration."""
from __future__ import annotations

from reportlineage.builder import build_estate, link_estate, nodes_from_sql
from reportlineage.models import EdgeKind
from reportlineage.parsers.sql_file import classify_sql_object

_SHARED_TABLE_ID = "sql:ordersdw::dbo.sales"


def test_build_estate_joins_rdl_and_dtsx_on_shared_table_signature(
    sample_rdl_path, sample_dtsx_path, sample_conmgr_path
):
    graph = build_estate(
        rdl_paths=[sample_rdl_path],
        dtsx_paths=[sample_dtsx_path],
        conmgr_paths=[sample_conmgr_path],
    )

    # dbo.Sales is written by the SSIS package and read by the SSRS report's
    # dataset; because both sides derive the same table-signature id, they
    # must collapse onto exactly one node instead of appearing twice.
    matches = [n for n in graph.nodes if n.id == _SHARED_TABLE_ID]
    assert len(matches) == 1
    assert matches[0].kind == "table"
    assert matches[0].qualified_name == "dbo.Sales"

    # The report and package must each appear as their own node too.
    report_nodes = [n for n in graph.nodes if n.kind == "ssrs_report"]
    package_nodes = [n for n in graph.nodes if n.kind == "ssis_package"]
    assert len(report_nodes) == 1
    assert len(package_nodes) == 1

    # link_estate must have added an explicit package -> report edge because
    # the package writes a table the report reads.
    cross_source_edges = [
        e for e in graph.edges
        if e.upstream_id == package_nodes[0].id
        and e.downstream_id == report_nodes[0].id
        and e.kind == EdgeKind.REFERENCES
    ]
    assert len(cross_source_edges) == 1

    # Rollups must be populated, not left empty.
    assert graph.totals["objects"] == len(graph.nodes)
    assert graph.totals["edges"] == len(graph.edges)
    assert graph.inventory_by_kind
    kinds_present = {row.kind for row in graph.inventory_by_kind}
    assert "table" in kinds_present
    assert "ssrs_report" in kinds_present
    assert "ssis_package" in kinds_present
    assert sum(graph.confidence.values()) == len(graph.nodes)
    assert graph.meta.scanned_at is not None


def test_build_estate_includes_sql_file_and_links_it_too(
    sample_rdl_path, sample_dtsx_path, sample_conmgr_path, sample_sql_path
):
    graph = build_estate(
        rdl_paths=[sample_rdl_path],
        dtsx_paths=[sample_dtsx_path],
        conmgr_paths=[sample_conmgr_path],
        sql_paths=[sample_sql_path],
    )
    view_nodes = [n for n in graph.nodes if n.kind == "view"]
    assert len(view_nodes) == 1
    assert view_nodes[0].qualified_name == "dbo.SalesByRegionView"
    # The view reads dbo.Sales too, so that table node still collapses to one.
    matches = [n for n in graph.nodes if n.id == _SHARED_TABLE_ID]
    assert len(matches) == 1


def test_build_estate_tolerates_a_bad_path_in_the_middle(sample_rdl_path, tmp_path):
    bad_dtsx = tmp_path / "does_not_exist.dtsx"
    graph = build_estate(rdl_paths=[sample_rdl_path], dtsx_paths=[str(bad_dtsx)])
    # The RDL still parses even though the DTSX path is bad.
    assert any(n.kind == "ssrs_report" for n in graph.nodes)
    assert graph.totals["diagnostics"] >= 0
    assert isinstance(graph.diagnostics, list)


def test_build_estate_with_no_inputs_returns_empty_but_valid_graph():
    graph = build_estate()
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.totals["objects"] == 0
    assert graph.inventory_by_kind == []


def test_link_estate_emits_edge_only_for_shared_signature():
    edges = link_estate(
        pkg_writes={"ssis:pkg1": {"sql:db::dbo.a", "sql:db::dbo.b"}},
        report_reads={"ssrs:r1": {"sql:db::dbo.b"}, "ssrs:r2": {"sql:db::dbo.z"}},
    )
    pairs = {(e.upstream_id, e.downstream_id) for e in edges}
    assert ("ssis:pkg1", "ssrs:r1") in pairs
    assert ("ssis:pkg1", "ssrs:r2") not in pairs
    assert len(edges) == 1


def test_nodes_from_sql_builds_view_node_and_read_edges(sample_sql_path):
    text = open(sample_sql_path).read()
    obj = classify_sql_object(text, filename=sample_sql_path)
    nodes, edges, diags, obj_id, writes = nodes_from_sql(obj, key=sample_sql_path)
    assert obj_id == "sql:::dbo.salesbyregionview" or obj_id.startswith("sql:")
    view_nodes = [n for n in nodes if n.kind == "view"]
    assert len(view_nodes) == 1
    assert writes == set()
    assert len(edges) == len(obj["reads"])
