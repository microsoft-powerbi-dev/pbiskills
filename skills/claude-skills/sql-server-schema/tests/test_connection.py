"""Tests for driver selection, connection strings, and the no-password invariant.

Every test injects the driver list, so these pass on a machine with no ODBC
driver installed and on one that has 18 rather than 17.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import connection as conn_mod  # noqa: E402


DRIVERS_18_AND_17 = ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]
DRIVERS_17_ONLY = ["SQL Server", "ODBC Driver 17 for SQL Server"]  # this laptop
DRIVERS_LEGACY_ONLY = ["SQL Server"]
DRIVERS_NONE = ["Microsoft Access Driver (*.mdb, *.accdb)"]


def test_prefers_the_highest_installed_driver():
    assert conn_mod.pick_driver(drivers=DRIVERS_18_AND_17) == "ODBC Driver 18 for SQL Server"


def test_falls_back_to_17_when_18_is_absent():
    # The reference laptop: driver 18 is not installed, so nothing may assume it.
    assert conn_mod.pick_driver(drivers=DRIVERS_17_ONLY) == "ODBC Driver 17 for SQL Server"


def test_falls_back_to_the_legacy_driver():
    assert conn_mod.pick_driver(drivers=DRIVERS_LEGACY_ONLY) == "SQL Server"


def test_raises_when_no_sql_server_driver_exists():
    with pytest.raises(conn_mod.ConnectionError_) as excinfo:
        conn_mod.pick_driver(drivers=DRIVERS_NONE)
    assert "ODBC Driver 18" in str(excinfo.value)


def test_explicit_driver_must_actually_be_installed():
    assert (
        conn_mod.pick_driver("ODBC Driver 17 for SQL Server", DRIVERS_17_ONLY)
        == "ODBC Driver 17 for SQL Server"
    )
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.pick_driver("ODBC Driver 18 for SQL Server", DRIVERS_17_ONLY)


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SQLPROD01", "SQLPROD01"),
        ("SQLPROD01\\INST1", "SQLPROD01\\INST1"),
        ("SQLPROD01,1433", "SQLPROD01,1433"),
        ("tcp:SQLPROD01,1433", "SQLPROD01,1433"),
        ("  SQLPROD01  ", "SQLPROD01"),
    ],
)
def test_target_round_trips(raw, expected):
    assert conn_mod.normalize_target(raw).server_token() == expected


def test_instance_and_port_together_are_rejected():
    with pytest.raises(conn_mod.ConnectionError_) as excinfo:
        conn_mod.normalize_target("HOST\\INST,1433")
    assert "SQL Browser" in str(excinfo.value)


def test_empty_server_is_rejected():
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.normalize_target("")


def test_non_numeric_port_is_rejected():
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.normalize_target("HOST,notaport")


# ---------------------------------------------------------------------------
# Connection strings: the no-password invariant
# ---------------------------------------------------------------------------


def _all_variants():
    for drivers in (DRIVERS_18_AND_17, DRIVERS_17_ONLY, DRIVERS_LEGACY_ONLY):
        for encrypt in ("auto", "strict", "off"):
            for database in (None, "Claims"):
                for readonly in (True, False):
                    yield drivers, encrypt, database, readonly


def test_connection_string_never_contains_credentials():
    for drivers, encrypt, database, readonly in _all_variants():
        built = conn_mod.build_connection_string(
            "SQLPROD01",
            database,
            encrypt=encrypt,
            readonly_intent=readonly,
            drivers=drivers,
        )
        lowered = built.lower()
        assert "pwd=" not in lowered
        assert "password=" not in lowered
        assert "uid=" not in lowered
        assert "user id=" not in lowered
        assert "trusted_connection=yes" in lowered


def test_assert_no_credentials_catches_a_password():
    for bad in (
        "DRIVER={x};SERVER=y;PWD=secret",
        "DRIVER={x};SERVER=y;Password=secret",
        "DRIVER={x};SERVER=y;UID=sa",
        "DRIVER={x};SERVER=y;User Id=sa",
    ):
        with pytest.raises(conn_mod.ConnectionError_):
            conn_mod.assert_no_credentials(bad)


def test_assert_no_credentials_allows_a_trusted_string():
    conn_mod.assert_no_credentials("DRIVER={x};SERVER=y;Trusted_Connection=yes")


# ---------------------------------------------------------------------------
# Encryption policy: the driver-18 trap
# ---------------------------------------------------------------------------


def test_driver_18_auto_encrypts_but_relaxes_chain_validation():
    built = conn_mod.build_connection_string(
        "S", "D", encrypt="auto", drivers=DRIVERS_18_AND_17
    )
    assert "Encrypt=yes" in built
    assert "TrustServerCertificate=yes" in built


def test_driver_17_auto_emits_no_encrypt_key():
    built = conn_mod.build_connection_string(
        "S", "D", encrypt="auto", drivers=DRIVERS_17_ONLY
    )
    assert "Encrypt=" not in built


def test_strict_validates_the_chain_on_every_driver():
    for drivers in (DRIVERS_18_AND_17, DRIVERS_17_ONLY):
        built = conn_mod.build_connection_string(
            "S", "D", encrypt="strict", drivers=drivers
        )
        assert "Encrypt=yes" in built
        assert "TrustServerCertificate=no" in built


def test_off_disables_encryption():
    built = conn_mod.build_connection_string("S", "D", encrypt="off", drivers=DRIVERS_17_ONLY)
    assert "Encrypt=no" in built


def test_unknown_encrypt_mode_is_rejected():
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.build_connection_string("S", "D", encrypt="maybe", drivers=DRIVERS_17_ONLY)


def test_application_name_is_set_so_a_dba_can_identify_the_session():
    built = conn_mod.build_connection_string("S", "D", drivers=DRIVERS_17_ONLY)
    assert "APP=sqlserver-schema-mcp" in built


def test_readonly_intent_is_opt_in():
    plain = conn_mod.build_connection_string("S", "D", drivers=DRIVERS_17_ONLY)
    assert "ApplicationIntent" not in plain
    intent = conn_mod.build_connection_string(
        "S", "D", readonly_intent=True, drivers=DRIVERS_17_ONLY
    )
    assert "ApplicationIntent=ReadOnly" in intent


# ---------------------------------------------------------------------------
# Redaction and env resolution
# ---------------------------------------------------------------------------


def test_redaction_removes_server_and_database_names():
    built = conn_mod.build_connection_string(
        "SQLPROD01", "CLAIMSDW", drivers=DRIVERS_17_ONLY
    )
    redacted = conn_mod.redact_connection_string(built)
    assert "SQLPROD01" not in redacted
    assert "CLAIMSDW" not in redacted
    assert "hashed:" in redacted


def test_resolve_server_prefers_the_argument_then_the_environment(monkeypatch):
    monkeypatch.setenv(conn_mod.ENV_SERVER, "FROM_ENV")
    assert conn_mod.resolve_server("EXPLICIT") == "EXPLICIT"
    assert conn_mod.resolve_server(None) == "FROM_ENV"


def test_resolve_server_requires_a_value(monkeypatch):
    monkeypatch.delenv(conn_mod.ENV_SERVER, raising=False)
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.resolve_server(None)


def test_allowlist_blocks_an_unlisted_server(monkeypatch):
    monkeypatch.setenv(conn_mod.ENV_ALLOWED_SERVERS, "SQLPROD01,SQLDEV02")
    assert conn_mod.resolve_server("SQLDEV02") == "SQLDEV02"
    with pytest.raises(conn_mod.ConnectionError_):
        conn_mod.resolve_server("SOMEWHERE-ELSE")


def test_allowlist_error_does_not_leak_the_hostname(monkeypatch):
    monkeypatch.setenv(conn_mod.ENV_ALLOWED_SERVERS, "SQLPROD01")
    with pytest.raises(conn_mod.ConnectionError_) as excinfo:
        conn_mod.resolve_server("SECRET-HOST")
    assert "SECRET-HOST" not in str(excinfo.value)


def test_row_ceiling_is_clamped(monkeypatch):
    monkeypatch.setenv(conn_mod.ENV_MAX_ROWS, "999999")
    assert conn_mod.max_rows_ceiling() == 1000
    monkeypatch.setenv(conn_mod.ENV_MAX_ROWS, "25")
    assert conn_mod.clamp_rows(500) == 25


def test_connection_rolls_back_and_never_commits():
    """The load-bearing read-only guarantee."""

    class FakeCursor:
        def execute(self, *args):
            return self

        def close(self):
            pass

    class FakeConn:
        def __init__(self):
            self.rolled_back = False
            self.committed = False
            self.closed = False
            self.timeout = None

        def cursor(self):
            return FakeCursor()

        def rollback(self):
            self.rolled_back = True

        def commit(self):  # pragma: no cover - must never be called
            self.committed = True

        def close(self):
            self.closed = True

    fake = FakeConn()
    with conn_mod.connection(conn=fake) as handle:
        assert handle is fake
    assert fake.rolled_back
    assert not fake.committed
    # An injected connection is owned by the caller, so it is not closed.
    assert not fake.closed


def test_connection_rolls_back_even_when_the_body_raises():
    class FakeConn:
        def __init__(self):
            self.rolled_back = False
            self.timeout = None

        def cursor(self):
            class C:
                def execute(self, *a):
                    return self

                def close(self):
                    pass

            return C()

        def rollback(self):
            self.rolled_back = True

    fake = FakeConn()
    with pytest.raises(ValueError):
        with conn_mod.connection(conn=fake):
            raise ValueError("boom")
    assert fake.rolled_back
