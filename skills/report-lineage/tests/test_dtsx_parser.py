"""Tests for reportlineage.parsers.dtsx.parse_dtsx_lineage."""
from __future__ import annotations

from reportlineage.parsers.dtsx import parse_dtsx_lineage


def test_parses_embedded_connection(sample_dtsx_path):
    pkg = parse_dtsx_lineage(sample_dtsx_path)
    assert pkg.name == "sample_package"
    names = {c.name for c in pkg.connections}
    assert "OrdersDW_Destination_DB" in names
    conn = next(c for c in pkg.connections if c.name == "OrdersDW_Destination_DB")
    assert conn.server == "SQL-PROD-01"
    assert conn.database == "OrdersDW"


def test_parses_oledb_source_and_destination(sample_dtsx_path):
    pkg = parse_dtsx_lineage(sample_dtsx_path)
    read_names = {r.qualified_name for r in pkg.reads}
    write_names = {w.qualified_name for w in pkg.writes}
    assert "dbo.SourceOrders" in read_names
    assert "dbo.Sales" in write_names
    # The destination's database comes from the resolved connection manager.
    dest = next(w for w in pkg.writes if w.table == "Sales")
    assert dest.database == "OrdersDW"


def test_parses_execute_sql_task(sample_dtsx_path):
    pkg = parse_dtsx_lineage(sample_dtsx_path)
    assert any("RefreshSalesSummary" in stmt for stmt in pkg.sql_tasks)
    exec_names = {r.qualified_name for r in pkg.reads if r.kind == "stored_procedure"}
    assert "Integration.RefreshSalesSummary" in exec_names


def test_malformed_dtsx_does_not_raise(tmp_path):
    bad = tmp_path / "broken.dtsx"
    bad.write_text("<DTS:Executable><Unclosed>")
    pkg = parse_dtsx_lineage(str(bad))
    assert pkg.name == "broken"
    assert pkg.diagnostics
    assert pkg.reads == []
    assert pkg.writes == []
