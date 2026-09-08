"""Tests for reportlineage.signature.table_signature."""
from __future__ import annotations

from reportlineage.signature import table_signature


def test_signature_basic_case_folding():
    assert table_signature("OrdersDW", "dbo", "Sales") == "ordersdw::dbo.sales"


def test_signature_schema_defaults_to_dbo():
    assert table_signature("OrdersDW", None, "Sales") == "ordersdw::dbo.sales"
    assert table_signature("OrdersDW", "", "Sales") == "ordersdw::dbo.sales"


def test_signature_database_defaults_to_empty_string():
    assert table_signature(None, "dbo", "Region") == "::dbo.region"


def test_signature_matches_across_case_and_whitespace():
    a = table_signature("OrdersDW", "DBO", " Sales ")
    b = table_signature("ordersdw", "dbo", "sales")
    assert a == b


def test_signature_distinguishes_schema():
    a = table_signature("OrdersDW", "staging", "Sales")
    b = table_signature("OrdersDW", "dbo", "Sales")
    assert a != b
