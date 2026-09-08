"""Tests for the MCP tool layer.

Two properties matter here. Tools never raise: a failure has to come back as a
payload the model can act on, not a stack trace. And no write tool exists: the
read-only posture is structural, not a runtime flag.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connection as conn_mod  # noqa: E402
import sqlserver_schema_mcp as server  # noqa: E402
from fakes import FakeConnection  # noqa: E402


TOOL_NAMES = [
    name for name in dir(server) if name.startswith("mssql_")
]


def test_every_tool_returns_a_payload_when_pyodbc_is_missing(monkeypatch):
    """No driver installed must produce an actionable message, not an exception."""

    def boom(*args, **kwargs):
        raise conn_mod.ConnectionError_("pyodbc is not installed. pip install pyodbc")

    monkeypatch.setattr(conn_mod, "_require_pyodbc", boom)
    monkeypatch.setattr(conn_mod, "available_drivers", boom)
    monkeypatch.setenv(conn_mod.ENV_SERVER, "TESTHOST")

    calls = {
        "mssql_list_odbc_drivers": (),
        "mssql_test_connection": (),
        "mssql_list_databases": (),
        "mssql_list_schemas": (),
        "mssql_list_tables": (),
        "mssql_describe_table": ("dbo.T",),
        "mssql_list_relationships": (),
        "mssql_list_indexes": ("dbo.T",),
        "mssql_table_stats": (),
        "mssql_list_programmability": (),
        "mssql_get_definition": ("dbo.P",),
        "mssql_run_query": ("SELECT 1",),
    }
    for name, args in calls.items():
        result = getattr(server, name)(*args)
        assert isinstance(result, dict), name
        assert result["ok"] is False, name
        assert "error" in result, name


def test_no_write_tool_is_exposed():
    """The read-only posture is structural: there is nothing to unlock."""
    forbidden = ("execute", "insert", "update", "delete", "drop", "create", "write")
    for name in TOOL_NAMES:
        assert not any(word in name.lower() for word in forbidden), name
    assert not hasattr(server, "mssql_execute_sql")
    assert not hasattr(server, "mssql_execute_statement")


def test_run_query_refuses_a_write_before_touching_the_server(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("a refused statement must never reach the connection")

    monkeypatch.setattr(conn_mod, "connection", explode)
    result = server.mssql_run_query("DROP TABLE dbo.Claim")
    assert result["ok"] is False
    assert "read-only" in result["error"].lower()
    assert result["kind"] == "forbidden"


def test_run_query_refuses_stacked_statements(monkeypatch):
    monkeypatch.setattr(
        conn_mod, "connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    result = server.mssql_run_query("SELECT 1; DROP TABLE x")
    assert result["ok"] is False


def test_run_query_masks_sensitive_columns(monkeypatch):
    from contextlib import contextmanager

    conn = FakeConnection(
        {
            r"SELECT": (
                ["ClaimId", "MemberSSN", "PaidAmount"],
                [(1, "123-45-6789", 42.5)],
            )
        }
    )

    @contextmanager
    def fake_connection(*args, **kwargs):
        yield conn

    monkeypatch.setattr(conn_mod, "connection", fake_connection)
    result = server.mssql_run_query("SELECT ClaimId, MemberSSN, PaidAmount FROM dbo.Claim")
    assert result["ok"] is True
    row = result["rows"][0]
    assert row["ClaimId"] == 1
    assert row["PaidAmount"] == 42.5
    # The value never comes back, only its shape.
    assert row["MemberSSN"]["masked"] is True
    assert "123-45-6789" not in str(row)
    assert result["masked_columns"] == ["MemberSSN"]


def test_describe_table_flags_sensitive_columns(monkeypatch):
    from contextlib import contextmanager

    conn = FakeConnection(
        {
            r"OBJECT_ID\(\?\)": (
                ["object_id", "schema_name", "object_name", "type_desc"],
                [(1, "dbo", "Member", "USER_TABLE")],
            ),
            r"FROM sys\.columns": (
                ["ordinal", "column_name", "data_type", "max_length", "precision",
                 "scale", "is_nullable", "is_identity", "is_computed",
                 "collation_name", "default_definition", "computed_definition",
                 "seed_value", "increment_value", "description"],
                [
                    (1, "MemberId", "int", 4, 10, 0, False, True, False, None, None, None, 1, 1, None),
                    (2, "MemberSSN", "char", 11, 0, 0, True, False, False, None, None, None, None, None, None),
                ],
            ),
        }
    )

    @contextmanager
    def fake_connection(*args, **kwargs):
        yield conn

    monkeypatch.setattr(conn_mod, "connection", fake_connection)
    result = server.mssql_describe_table("dbo.Member")
    assert result["ok"] is True
    columns = {c["name"]: c for c in result["columns"]}
    assert columns["MemberSSN"]["sensitive"] is True
    assert columns["MemberSSN"]["sensitivity"]["tier"] == "hard_deny"
    # MemberId matches the member_id heuristic, flagged but not hard-deny.
    assert columns["MemberId"]["sensitive"] is True
    assert columns["MemberId"]["sensitivity"]["tier"] == "high"


def test_get_reference_lists_and_returns_topics():
    listing = server.mssql_get_reference()
    assert listing["ok"] is True
    assert listing["topics"], "the skill should ship reference docs"
    topic = listing["topics"][0]
    fetched = server.mssql_get_reference(topic)
    assert fetched["ok"] is True
    assert fetched["content"]


def test_get_reference_rejects_an_unknown_topic():
    result = server.mssql_get_reference("no-such-topic")
    assert result["ok"] is False


def test_generate_skill_refuses_to_write_into_the_repository(tmp_path):
    import json

    import analyze

    digest = analyze.build_digest(
        database="Demo",
        tables=[{
            "schema": "dbo", "name": "T",
            "columns": [{"name": "Id", "data_type": "int", "type": "int", "nullable": False}],
            "primary_key": None, "row_count": 1, "object_type": "USER_TABLE",
        }],
        foreign_keys=[],
        generated_at="fixed",
    )
    path = tmp_path / "d.json"
    path.write_text(json.dumps(digest), encoding="utf-8")
    target = Path(__file__).resolve().parents[3] / "leaked-pack"
    result = server.mssql_generate_skill(str(path), str(target))
    assert result["ok"] is False
    assert result["kind"] == "refused"
    assert not target.exists()


def test_main_explains_a_missing_fastmcp(monkeypatch):
    monkeypatch.setattr(server, "_mcp", None)
    monkeypatch.setattr(server, "_check_mcp_shadowing", lambda: None)
    with pytest.raises(SystemExit) as excinfo:
        server.main()
    assert "fastmcp" in str(excinfo.value)
