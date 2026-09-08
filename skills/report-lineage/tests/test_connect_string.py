"""Tests for reportlineage.connect_string.parse_connect_string."""
from __future__ import annotations

from reportlineage.connect_string import parse_connect_string


def test_none_and_empty_input():
    assert parse_connect_string(None) == (None, None)
    assert parse_connect_string("") == (None, None)


def test_oledb_style_connection_string():
    connect = "Provider=SQLNCLI11.1;Data Source=SQL-PROD-01;Initial Catalog=OrdersDW;"
    server, db = parse_connect_string(connect)
    assert server == "SQL-PROD-01"
    assert db == "OrdersDW"


def test_ado_net_style_connection_string():
    connect = "Server=tcp:sql01.database.windows.net,1433;Database=Sales;User ID=x;"
    server, db = parse_connect_string(connect)
    assert server == "tcp:sql01.database.windows.net,1433"
    assert db == "Sales"


def test_unknown_keys_are_ignored():
    connect = "Timeout=30;Encrypt=True;Initial Catalog=Reporting"
    server, db = parse_connect_string(connect)
    assert server is None
    assert db == "Reporting"


def test_keys_are_case_insensitive():
    connect = "DATA SOURCE=srv01;INITIAL CATALOG=warehouse"
    server, db = parse_connect_string(connect)
    assert server == "srv01"
    assert db == "warehouse"
