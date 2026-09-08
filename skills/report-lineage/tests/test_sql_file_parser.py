"""Tests for reportlineage.parsers.sql_file.classify_sql_object."""
from __future__ import annotations

from pathlib import Path

from reportlineage.parsers.sql_file import classify_sql_object


def test_classifies_create_view(sample_sql_path):
    text = Path(sample_sql_path).read_text()
    result = classify_sql_object(text, filename=sample_sql_path)
    assert result["kind"] == "view"
    assert result["name"] == "dbo.SalesByRegionView"
    read_names = {r.qualified_name for r in result["reads"]}
    assert "dbo.Sales" in read_names
    assert "dbo.Region" in read_names
    assert result["writes"] == []


def test_classifies_create_table():
    sql = "CREATE TABLE dbo.Staging_Orders (OrderId INT, Amount DECIMAL(18,2))"
    result = classify_sql_object(sql, filename="staging_orders.sql")
    assert result["kind"] == "table"
    assert result["name"] == "dbo.Staging_Orders"


def test_classifies_create_procedure():
    sql = "CREATE OR ALTER PROCEDURE Integration.RefreshSalesSummary AS BEGIN SELECT 1 END"
    result = classify_sql_object(sql, filename="refresh.sql")
    assert result["kind"] == "stored_procedure"
    assert result["name"] == "Integration.RefreshSalesSummary"


def test_falls_back_to_filename_when_no_create_statement():
    sql = "SELECT * FROM dbo.Sales"
    result = classify_sql_object(sql, filename="ad_hoc_query.sql")
    assert result["kind"] == "unknown"
    assert result["name"] == "ad_hoc_query"
    read_names = {r.qualified_name for r in result["reads"]}
    assert "dbo.Sales" in read_names


def test_empty_sql_and_no_filename():
    result = classify_sql_object("", filename="")
    assert result["kind"] == "unknown"
    assert result["name"] is None
    assert result["reads"] == []
    assert result["writes"] == []
