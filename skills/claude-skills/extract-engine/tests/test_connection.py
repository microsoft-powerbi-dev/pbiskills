"""Tests for the extract engine's connection module.

Mostly a reuse/wiring test: confirms the sql-server-schema functions are
importable and behave as expected, and that read_connection/write_connection
have the correct commit/rollback discipline, which is the one genuinely new
behavior here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import connection as conn_mod  # noqa: E402


def test_reused_functions_are_importable():
    assert conn_mod.pick_driver(drivers=["SQL Server"]) == "SQL Server"
    built = conn_mod.build_connection_string("S", "D", drivers=["ODBC Driver 17 for SQL Server"])
    assert "Trusted_Connection=yes" in built
    conn_mod.assert_no_credentials(built)


def test_extract_engine_has_its_own_env_namespace():
    assert conn_mod.ENV_SERVER == "EXTRACT_ENGINE_SERVER"
    assert conn_mod.ENV_DATABASE == "EXTRACT_ENGINE_DATABASE"
    assert conn_mod.ENV_SERVER != "SQLSERVER_MCP_SERVER"


class _FakeCursor:
    def execute(self, *a):
        return self

    def close(self):
        pass


class _FakeConn:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.timeout = None

    def cursor(self):
        return _FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_read_connection_never_commits():
    fake = _FakeConn()
    with conn_mod.read_connection(conn=fake) as handle:
        assert handle is fake
    assert fake.rolled_back
    assert not fake.committed
    assert not fake.closed  # injected connection is owned by the caller


def test_write_connection_commits_on_clean_exit():
    fake = _FakeConn()
    with conn_mod.write_connection(conn=fake) as handle:
        assert handle is fake
    assert fake.committed
    assert not fake.rolled_back


def test_write_connection_rolls_back_on_exception():
    fake = _FakeConn()
    with pytest.raises(ValueError):
        with conn_mod.write_connection(conn=fake):
            raise ValueError("boom")
    assert fake.rolled_back
    assert not fake.committed


def test_read_and_write_connections_are_distinct_functions():
    assert conn_mod.read_connection is not conn_mod.write_connection
