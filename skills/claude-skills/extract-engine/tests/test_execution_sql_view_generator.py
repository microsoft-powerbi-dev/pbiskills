"""Tests for execution path B's view generator, deploy gating, and the
CONCAT_WS null-shifting trap.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_engine.metadata import Dataset, DatasetLookup, Feed, FieldMap  # noqa: E402
from extract_engine import execution_sql as es  # noqa: E402
from extract_engine import connection as conn_mod  # noqa: E402
from fakes import FakeConnection  # noqa: E402


def _dataset(**overrides):
    base = dict(
        dataset_id=1, feed_id=1, dataset_name="Claim", source_schema="src", source_table="claim",
        dataset_mode="primary", primary_key_columns=["ClaimId"], window_date_column="ServiceDate",
        depends_on_dataset_id=None, dependency_source_column=None, dependency_target_column=None,
        include_own_window=False, output_file_stem="Claim", run_ordinal=1, is_active=True,
    )
    base.update(overrides)
    return Dataset(**base)


def _field(ordinal, target, source, rule_name="passthrough", rule_params=None, default_value=None):
    return FieldMap(
        field_map_id=ordinal, dataset_id=1, ordinal=ordinal, target_column=target,
        source_expression=source, data_type="varchar(20)", rule_name=rule_name,
        rule_params=rule_params or {}, nullable=True, default_value=default_value, is_active=True,
    )


def _feed(**overrides):
    base = dict(
        feed_id=1, feed_name="Test", source_server="S", source_database="D", delimiter="|",
        collision_action="sanitize", collision_char=" ", line_ending="CRLF", null_sentinel="",
        max_rows_per_file=1000000, emit_header_row=False, emit_trailer_row=True,
        emit_concat_ws_line=False, default_execution_mode="sql", output_root="C:\\out",
        anchor_date_default=None, window_years_default=2, is_active=True,
    )
    base.update(overrides)
    return Feed(**base)


# ---------------------------------------------------------------------------
# generate_view_ddl
# ---------------------------------------------------------------------------


def test_view_ddl_has_no_where_clause():
    """Design note: the view is static (deployed once), so it must not bake
    in a per-run window filter."""
    dataset = _dataset()
    fields = [_field(1, "ClaimNumber", "claim_id", "trim")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed())
    assert "WHERE" not in ddl.upper() or "WHERE" not in ddl  # no WHERE at all
    assert ddl.startswith("CREATE OR ALTER VIEW gen.v_Claim AS SELECT")


def test_view_ddl_exposes_internal_window_key_for_primary_mode():
    dataset = _dataset(dataset_mode="primary", window_date_column="ServiceDate")
    fields = [_field(1, "ClaimNumber", "claim_id", "trim")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed())
    assert "[t].[ServiceDate] AS [__window_key]" in ddl


def test_view_ddl_exposes_internal_dependency_key_for_dependent_mode():
    dataset = _dataset(
        dataset_mode="dependent", window_date_column=None,
        depends_on_dataset_id=1, dependency_target_column="member_id",
    )
    fields = [_field(1, "MemberName", "name", "trim")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed())
    assert "[t].[member_id] AS [__dependency_key]" in ddl
    assert "__window_key" not in ddl  # no window column when include_own_window is False


def test_view_ddl_reference_mode_has_no_internal_filter_columns():
    dataset = _dataset(dataset_mode="reference", window_date_column=None)
    fields = [_field(1, "StatusCode", "status_code", "passthrough")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed())
    assert "__window_key" not in ddl
    assert "__dependency_key" not in ddl


def test_view_ddl_applies_rules_and_sanitization():
    dataset = _dataset()
    fields = [_field(1, "Notes", "notes", "trim")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed(collision_action="sanitize", delimiter="|"))
    assert "LTRIM(RTRIM(" in ddl
    assert "REPLACE(" in ddl  # sanitization wrapping applied


# ---------------------------------------------------------------------------
# key_source_alias / extra_key_columns: the SQL-mode dependent-key-staging fix
# ---------------------------------------------------------------------------


def test_key_columns_needed_from_finds_dependents_referencing_this_dataset():
    claim = _dataset(dataset_id=1, dataset_name="Claim")
    member = _dataset(
        dataset_id=2, dataset_name="Member", dataset_mode="dependent", window_date_column=None,
        depends_on_dataset_id=1, dependency_source_column="member_id", dependency_target_column="member_id",
    )
    needed = es.key_columns_needed_from(1, [claim, member])
    assert needed == ["member_id"]


def test_key_columns_needed_from_returns_empty_for_a_leaf_dataset():
    claim = _dataset(dataset_id=1, dataset_name="Claim")
    assert es.key_columns_needed_from(1, [claim]) == []


def test_view_ddl_exposes_extra_key_source_column():
    dataset = _dataset()
    fields = [_field(1, "ClaimNumber", "claim_id", "trim")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed(), extra_key_columns=["member_id"])
    assert "[t].[member_id] AS [__key_source_member_id]" in ddl


def test_view_ddl_extra_key_column_present_even_with_concat_ws():
    """The internal key-staging column must survive the concat_ws collapse -
    it is metadata for the client, never folded into [Line]."""
    dataset = _dataset()
    fields = [_field(1, "A", "a"), _field(2, "B", "b")]
    ddl = es.generate_view_ddl(dataset, fields, [], _feed(emit_concat_ws_line=True), extra_key_columns=["member_id"])
    assert "AS [Line]" in ddl
    assert "[__key_source_member_id]" in ddl


def test_view_read_query_includes_extra_key_column_when_requested():
    dataset = _dataset(dataset_mode="reference", window_date_column=None)
    fields = [_field(1, "ClaimNumber", "claim_id", "trim")]
    plan = es.build_view_read_query(dataset, fields, _feed(), extra_key_columns=["member_id"])
    assert plan.sql == "SELECT [ClaimNumber], [__key_source_member_id] FROM gen.v_Claim WHERE 1 = 1"


def test_view_ddl_row_sequence_order_by_is_qualified_when_ambiguous():
    """The exact bug found live: an unqualified row_sequence order_by naming
    a column that also exists on a joined lookup table raised 'Ambiguous
    column name'. Must be qualified against the base table alias."""
    dataset = _dataset()
    fields = [
        _field(1, "PaidAmount", "lookup:calc.amount", "decimal_format", {"scale": 2, "mask": "N2"}),
        _field(2, "SurrogateKey", "n/a", "row_sequence", {"order_by": "claim_id"}),
    ]
    lookups = [DatasetLookup(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left")]
    ddl = es.generate_view_ddl(dataset, fields, lookups, _feed())
    assert "ROW_NUMBER() OVER (ORDER BY [t].[claim_id])" in ddl
    assert "ORDER BY claim_id)" not in ddl  # never bare


def test_view_ddl_includes_join_clause():
    dataset = _dataset()
    fields = [_field(1, "Amount", "lookup:calc.amount", "decimal_format", {"scale": 2, "mask": "N2"})]
    lookups = [DatasetLookup(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left")]
    ddl = es.generate_view_ddl(dataset, fields, lookups, _feed())
    assert "LEFT JOIN [src].[claim_calc] AS [calc]" in ddl


# ---------------------------------------------------------------------------
# build_view_read_query: must mirror path A's mode semantics exactly
# ---------------------------------------------------------------------------


def test_view_read_query_primary_mode():
    import datetime

    dataset = _dataset(dataset_mode="primary", window_date_column="ServiceDate")
    fields = [_field(1, "ClaimNumber", "claim_id", "trim")]
    plan = es.build_view_read_query(
        dataset, fields, _feed(), anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert plan.sql == "SELECT [ClaimNumber] FROM gen.v_Claim WHERE [__window_key] BETWEEN ? AND ?"
    assert plan.params == (datetime.date(2024, 9, 1), datetime.date(2026, 9, 1))


def test_view_read_query_dependent_mode_matches_the_most_important_check():
    """Same acceptance-check-#5 property as query_builder's test: a Member
    file must include out-of-window records referenced by a windowed Claim,
    via the SAME staged-key-set mechanism path A uses."""
    dataset = _dataset(
        dataset_id=2, dataset_name="Member", dataset_mode="dependent", window_date_column=None,
        depends_on_dataset_id=1, dependency_target_column="member_id", include_own_window=False,
    )
    fields = [_field(1, "MemberName", "name", "trim")]
    plan = es.build_view_read_query(dataset, fields, _feed(), run_id=42)
    assert plan.sql == (
        "SELECT [MemberName] FROM gen.v_Member WHERE [__dependency_key] IN "
        "(SELECT key_value FROM meta.run_dataset_key WHERE run_id = ? AND dataset_id = ?)"
    )
    assert plan.params == (42, 1)


def test_view_read_query_concat_ws_selects_only_line_column():
    dataset = _dataset(dataset_mode="reference", window_date_column=None)
    fields = [_field(1, "A", "a"), _field(2, "B", "b")]
    plan = es.build_view_read_query(dataset, fields, _feed(emit_concat_ws_line=True))
    assert plan.sql.startswith("SELECT [Line] FROM gen.v_Claim")


# ---------------------------------------------------------------------------
# deploy_views: never fires without explicit confirmation
# ---------------------------------------------------------------------------


def test_deploy_views_refuses_without_confirmation():
    conn = FakeConnection()
    dataset = _dataset()
    fields = {1: [_field(1, "ClaimNumber", "claim_id", "trim")]}
    results = es.deploy_views(conn, _feed(), [dataset], fields, {}, confirmed=False)
    assert results[0].deployed is False
    assert results[0].blocked_reason == "not confirmed"
    assert conn.writes == []  # nothing was ever sent


def test_deploy_views_sends_ddl_when_confirmed():
    conn = FakeConnection()
    dataset = _dataset()
    fields = {1: [_field(1, "ClaimNumber", "claim_id", "trim")]}
    results = es.deploy_views(conn, _feed(), [dataset], fields, {}, confirmed=True)
    assert results[0].deployed is True
    assert len(conn.writes) == 1
    assert conn.writes[0][0].startswith("CREATE OR ALTER VIEW")


# ---------------------------------------------------------------------------
# The CONCAT_WS null-shifting trap
# ---------------------------------------------------------------------------


def test_concat_ws_wraps_every_arg_in_isnull_text_level():
    fields = [_field(1, "A", "a", "passthrough"), _field(2, "B", "b", "passthrough")]
    rendered = es.render_concat_ws_line(fields, _feed(delimiter="|"), "t")
    # Every argument, not just some, must be wrapped.
    assert rendered.count("ISNULL(") == 2
    assert rendered.startswith("CONCAT_WS('|', ISNULL(")


@pytest.mark.skipif(
    os.environ.get("EXTRACT_ENGINE_LOCALDB") != "1",
    reason="set EXTRACT_ENGINE_LOCALDB=1 to run against a live LocalDB instance",
)
def test_concat_ws_null_shift_regression_against_localdb():
    """The corrupted-file case the doc calls out by name: CONCAT_WS skips a
    NULL argument entirely (not an empty position), silently shifting every
    downstream field. Proven two ways: the naive (unwrapped) form loses a
    pipe; the ISNULL-wrapped form this module always produces does not.
    """
    try:
        with conn_mod.read_connection(server=r"(localdb)\MSSQLLocalDB", database="master") as conn:
            cursor = conn.cursor()
            try:
                # Naive form: demonstrates the bug exists in SQL Server itself.
                cursor.execute("SELECT CONCAT_WS('|', 'A', NULL, 'C') AS v")
                naive = cursor.fetchone()[0]
                # Our rendering: every arg wrapped in ISNULL first.
                cursor.execute("SELECT CONCAT_WS('|', ISNULL('A',''), ISNULL(NULL,''), ISNULL('C','')) AS v")
                wrapped = cursor.fetchone()[0]
            finally:
                cursor.close()
    except conn_mod.ConnectionError_ as exc:  # pragma: no cover - env dependent
        pytest.skip("LocalDB unreachable: {}".format(exc))

    assert naive == "A|C"  # the bug: only one pipe, B's position vanished
    assert wrapped == "A||C"  # correct: an empty position, pipe count preserved
    assert naive.count("|") != wrapped.count("|")
