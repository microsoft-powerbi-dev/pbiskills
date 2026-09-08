"""Tests for reportlineage.parsers.conmgr.parse_conmgr."""
from __future__ import annotations

from reportlineage.parsers.conmgr import parse_conmgr


def test_parses_connection_manager(sample_conmgr_path):
    conn = parse_conmgr(sample_conmgr_path)
    assert conn is not None
    assert conn.name == "OrdersDW_Destination_DB"
    assert conn.server == "SQL-PROD-01"
    assert conn.database == "OrdersDW"
    assert conn.dtsid == "11111111-1111-1111-1111-111111111111"


def test_missing_file_returns_none(tmp_path):
    missing = tmp_path / "nope.conmgr"
    assert parse_conmgr(str(missing)) is None


def test_malformed_conmgr_returns_none(tmp_path):
    bad = tmp_path / "broken.conmgr"
    bad.write_text("<Not<Valid XML")
    assert parse_conmgr(str(bad)) is None


def test_conmgr_without_connection_string_still_returns_a_connection(tmp_path):
    p = tmp_path / "empty.conmgr"
    p.write_text('<DTS:ConnectionManager xmlns:DTS="www.microsoft.com/SqlServer/Dts" DTS:ObjectName="Empty"/>')
    conn = parse_conmgr(str(p))
    assert conn is not None
    assert conn.name == "Empty"
    assert conn.server is None
    assert conn.database is None
