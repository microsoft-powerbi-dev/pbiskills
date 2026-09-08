"""Tests for reportlineage.fingerprint."""
from __future__ import annotations

from reportlineage.fingerprint import fingerprint_rdl, normalize_name, normalize_sql, tokenize


def test_normalize_name_collapses_case_and_punctuation():
    assert normalize_name("Sales_Amount") == "sales amount"
    assert normalize_name(" Sales  Amount ") == "sales amount"


def test_normalize_sql_collapses_whitespace_and_case():
    assert normalize_sql("SELECT  *\nFROM  dbo.Sales") == "select * from dbo.sales"


def test_tokenize_drops_stopwords_and_short_tokens():
    tokens = tokenize(["The Sales Report", "by Region"])
    assert "sales" in tokens
    assert "region" in tokens
    assert "the" not in tokens
    assert "by" not in tokens
    assert "report" not in tokens


def test_fingerprint_rdl_extracts_tables_columns_and_parameters(sample_rdl_path):
    fp = fingerprint_rdl(sample_rdl_path, key=sample_rdl_path)

    assert fp.name == "sample_report"
    assert not fp.is_empty
    assert any(t.endswith("dbo.sales") for t in fp.tables)
    assert any(t.endswith("dbo.region") for t in fp.tables)
    assert "saleid" in fp.columns
    assert "amount" in fp.columns
    assert "fiscalyear" in fp.parameters
    assert fp.visual_count >= 1
    assert "tablix" in fp.visuals
    assert "ordersdb" in fp.datasource_names


def test_fingerprint_near_identical_reports_share_tables_and_columns(fixtures_dir):
    fp_a = fingerprint_rdl(str(fixtures_dir / "sample_report.rdl"), key="a")
    fp_b = fingerprint_rdl(str(fixtures_dir / "sample_report_near_dup.rdl"), key="b")
    fp_c = fingerprint_rdl(str(fixtures_dir / "sample_report_unrelated.rdl"), key="c")

    shared_tables_ab = fp_a.tables & fp_b.tables
    shared_tables_ac = fp_a.tables & fp_c.tables
    assert shared_tables_ab
    assert not shared_tables_ac

    shared_columns_ab = fp_a.columns & fp_b.columns
    assert shared_columns_ab


def test_fingerprint_rdl_never_raises_on_a_missing_file(tmp_path):
    fp = fingerprint_rdl(str(tmp_path / "does_not_exist.rdl"))
    assert fp.is_empty
    assert fp.key
