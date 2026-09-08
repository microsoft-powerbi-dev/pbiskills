"""Tests for reportlineage.sql_refs."""
from __future__ import annotations

from reportlineage.sql_refs import (
    TableRef,
    extract_table_refs,
    make_table_ref,
    split_object_name,
)


def test_split_object_name_three_part():
    assert split_object_name("OrdersDW.dbo.Sales") == ("OrdersDW", "dbo", "Sales")


def test_split_object_name_two_part_bracketed():
    assert split_object_name("[dbo].[Sales]") == (None, "dbo", "Sales")


def test_split_object_name_one_part():
    assert split_object_name("Sales") == (None, None, "Sales")


def test_make_table_ref_rejects_keyword():
    assert make_table_ref("select") is None


def test_table_ref_signature_and_qualified_name():
    ref = TableRef(table="Sales", schema="dbo", database="OrdersDW")
    assert ref.signature() == "ordersdw::dbo.sales"
    assert ref.qualified_name == "dbo.Sales"


def test_extract_reads_from_select_join():
    sql = "SELECT s.SaleId, r.RegionName FROM dbo.Sales s JOIN dbo.Region r ON s.RegionId = r.RegionId"
    refs = extract_table_refs(sql, database="OrdersDW")
    read_names = {r.qualified_name for r in refs.reads}
    assert "dbo.Sales" in read_names
    assert "dbo.Region" in read_names
    assert not refs.writes
    assert not refs.dynamic


def test_extract_write_from_insert_into():
    sql = "INSERT INTO dbo.SalesSummary (SaleId, Amount) SELECT SaleId, Amount FROM dbo.Sales"
    refs = extract_table_refs(sql, database="OrdersDW")
    write_names = {w.qualified_name for w in refs.writes}
    assert "dbo.SalesSummary" in write_names


def test_extract_exec_stored_procedure():
    sql = "EXEC Integration.RefreshSalesSummary @Year = 2026"
    refs = extract_table_refs(sql, database="OrdersDW")
    exec_names = {e.qualified_name for e in refs.execs}
    assert "Integration.RefreshSalesSummary" in exec_names
    assert all(e.kind == "stored_procedure" for e in refs.execs)


def test_dynamic_sql_is_flagged():
    sql = "EXEC sp_executesql @sql"
    refs = extract_table_refs(sql, database="OrdersDW")
    assert refs.dynamic is True
    assert refs.problems


def test_comments_are_stripped_before_scanning():
    sql = "-- SELECT * FROM dbo.ShouldNotAppear\nSELECT * FROM dbo.Sales /* FROM dbo.AlsoIgnored */"
    refs = extract_table_refs(sql, database="OrdersDW")
    names = {r.qualified_name for r in refs.reads}
    assert "dbo.Sales" in names
    assert "dbo.ShouldNotAppear" not in names
    assert "dbo.AlsoIgnored" not in names


def test_empty_sql_returns_empty_refs():
    refs = extract_table_refs(None)
    assert refs.all == []
    assert refs.dynamic is False
