"""Tests for reportlineage.parsers.rdl.parse_rdl_lineage."""
from __future__ import annotations

from reportlineage.parsers.rdl import parse_rdl_lineage


def test_parses_connections(sample_rdl_path):
    report = parse_rdl_lineage(sample_rdl_path)
    assert report.name == "sample_report"
    assert "OrdersDB" in report.connections
    server, db = report.connections["OrdersDB"]
    assert server == "SQL-PROD-01"
    assert db == "OrdersDW"


def test_parses_one_dataset_with_query_and_fields(sample_rdl_path):
    report = parse_rdl_lineage(sample_rdl_path)
    assert len(report.datasets) == 1
    dset = report.datasets[0]
    assert dset.name == "SalesRegionData"
    assert dset.datasource_name == "OrdersDB"
    assert dset.command_type == "Text"
    assert "dbo.Sales" in dset.command_text
    assert "dbo.Region" in dset.command_text
    assert set(dset.fields) == {"SaleId", "Amount", "RegionName"}


def test_parses_parameters(sample_rdl_path):
    report = parse_rdl_lineage(sample_rdl_path)
    assert report.parameters == ["FiscalYear"]


def test_parses_visual_inventory(sample_rdl_path):
    report = parse_rdl_lineage(sample_rdl_path)
    assert report.visual_count == 1
    assert report.visual_kinds == ["Tablix"]


def test_malformed_rdl_does_not_raise(tmp_path):
    bad = tmp_path / "broken.rdl"
    bad.write_text("<Report><Unclosed>")
    report = parse_rdl_lineage(str(bad))
    assert report.name == "broken"
    assert report.datasets == []
    assert report.connections == {}
    assert report.parameters == []


def test_missing_file_does_not_raise(tmp_path):
    missing = tmp_path / "does_not_exist.rdl"
    report = parse_rdl_lineage(str(missing))
    assert report.name == "does_not_exist"
    assert report.datasets == []
