"""Tests for the reconstructed three-dataset-mode SQL semantics.

This module (dataset modes, the meta schema) is the part of the design most
grounded in inference rather than a supplied spec - see
references/dataset-modes-and-metadata-schema.md. These tests exist to make
the reconstructed behavior explicit and checkable, not to claim it is
definitely what a real v1 prompt would have specified.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine.metadata import Dataset, DatasetLookup, FieldMap  # noqa: E402
from extract_engine import query_builder  # noqa: E402


def _dataset(**overrides):
    base = dict(
        dataset_id=1, feed_id=1, dataset_name="Claim", source_schema="src", source_table="claim",
        dataset_mode="primary", primary_key_columns=["ClaimId"], window_date_column="ServiceDate",
        depends_on_dataset_id=None, dependency_source_column=None, dependency_target_column=None,
        include_own_window=False, output_file_stem="Claim", run_ordinal=1, is_active=True,
    )
    base.update(overrides)
    return Dataset(**base)


def _field(ordinal, target, source, data_type="varchar(20)", rule_name="passthrough", rule_params=None):
    return FieldMap(
        field_map_id=ordinal, dataset_id=1, ordinal=ordinal, target_column=target,
        source_expression=source, data_type=data_type, rule_name=rule_name,
        rule_params=rule_params or {}, nullable=True, default_value=None, is_active=True,
    )


# ---------------------------------------------------------------------------
# data_type_to_polars
# ---------------------------------------------------------------------------


def test_data_type_mapping():
    assert query_builder.data_type_to_polars("int") == pl.Int32
    assert query_builder.data_type_to_polars("bigint") == pl.Int64
    assert query_builder.data_type_to_polars("bit") == pl.Boolean
    assert query_builder.data_type_to_polars("date") == pl.Date
    assert query_builder.data_type_to_polars("varchar(50)") == pl.Utf8
    assert query_builder.data_type_to_polars("nvarchar(MAX)") == pl.Utf8
    assert query_builder.data_type_to_polars("decimal(18,2)") == pl.Decimal(18, 2)


def test_data_type_mapping_rejects_unknown_token():
    with pytest.raises(ValueError):
        query_builder.data_type_to_polars("geography")


# ---------------------------------------------------------------------------
# Source expression resolution: bare column vs lookup:<alias>.<column>
# ---------------------------------------------------------------------------


def test_resolve_source_column_bare():
    fm = _field(1, "ClaimNumber", "claim_id")
    assert query_builder.resolve_source_column_sql(fm, "t") == "[t].[claim_id]"


def test_resolve_source_column_lookup():
    fm = _field(1, "PaidAmount", "lookup:calc.amount")
    assert query_builder.resolve_source_column_sql(fm, "t") == "[calc].[amount]"


# ---------------------------------------------------------------------------
# Join clause
# ---------------------------------------------------------------------------


def test_build_join_clause_left_and_inner():
    lookups = [
        DatasetLookup(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left"),
        DatasetLookup(2, 1, "prov", "src", "provider", "provider_id", "provider_id", "inner"),
    ]
    clause = query_builder.build_join_clause(lookups, "t")
    assert "LEFT JOIN [src].[claim_calc] AS [calc] ON [calc].[claim_id] = [t].[claim_id]" in clause
    assert "INNER JOIN [src].[provider] AS [prov] ON [prov].[provider_id] = [t].[provider_id]" in clause


def test_build_join_clause_empty():
    assert query_builder.build_join_clause([], "t") == ""


# ---------------------------------------------------------------------------
# WHERE clause: the three dataset modes
# ---------------------------------------------------------------------------


def test_primary_mode_filters_on_window():
    dataset = _dataset(dataset_mode="primary", window_date_column="ServiceDate")
    where = query_builder.build_where_clause(
        dataset, "t", anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert where.sql == "[t].[ServiceDate] BETWEEN ? AND ?"
    assert where.params == (datetime.date(2024, 9, 1), datetime.date(2026, 9, 1))


def test_primary_mode_requires_anchor_and_window():
    dataset = _dataset(dataset_mode="primary")
    with pytest.raises(ValueError, match="anchor_date and window_years"):
        query_builder.build_where_clause(dataset, "t")


def test_primary_mode_requires_a_window_date_column():
    dataset = _dataset(dataset_mode="primary", window_date_column=None)
    with pytest.raises(ValueError, match="window_date_column"):
        query_builder.build_where_clause(dataset, "t", anchor_date=datetime.date(2026, 1, 1), window_years=1)


def test_reference_mode_has_no_filter():
    dataset = _dataset(dataset_mode="reference", window_date_column=None)
    where = query_builder.build_where_clause(dataset, "t")
    assert where.sql == "1 = 1"
    assert where.params == ()


def test_dependent_mode_filters_on_staged_key_set():
    dataset = _dataset(
        dataset_id=2, dataset_name="Member", dataset_mode="dependent", window_date_column=None,
        depends_on_dataset_id=1, dependency_source_column="MemberId", dependency_target_column="member_id",
        include_own_window=False,
    )
    where = query_builder.build_where_clause(dataset, "t", run_id=42)
    assert where.sql == (
        "[t].[member_id] IN (SELECT key_value FROM meta.run_dataset_key "
        "WHERE run_id = ? AND dataset_id = ?)"
    )
    assert where.params == (42, 1)


def test_dependent_mode_requires_run_id():
    dataset = _dataset(
        dataset_id=2, dataset_mode="dependent", window_date_column=None,
        depends_on_dataset_id=1, dependency_target_column="member_id",
    )
    with pytest.raises(ValueError, match="run_id"):
        query_builder.build_where_clause(dataset, "t")


def test_dependent_mode_with_include_own_window_unions_both_predicates():
    dataset = _dataset(
        dataset_id=2, dataset_mode="dependent", window_date_column="CreatedDate",
        depends_on_dataset_id=1, dependency_target_column="member_id", include_own_window=True,
    )
    where = query_builder.build_where_clause(
        dataset, "t", run_id=42, anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "IN (SELECT key_value FROM meta.run_dataset_key" in where.sql
    assert "[t].[CreatedDate] BETWEEN ? AND ?" in where.sql
    assert " OR " in where.sql
    assert where.params == (42, 1, datetime.date(2024, 9, 1), datetime.date(2026, 9, 1))


def test_this_is_the_dependent_mode_the_doc_calls_the_most_important_thing():
    """docs/extract-engine-mvp-prompt-v2-polars.md, acceptance check #5:
    'Member file contains records whose created_date predates the window' -
    proving dependent records are pulled in even when their own date falls
    outside the primary dataset's window. This asserts exactly that: with
    include_own_window=False (the reconstructed default), a Member's own
    date plays no part in the filter at all - only membership in the staged
    key set from the referencing Claim dataset does.
    """
    dataset = _dataset(
        dataset_id=2, dataset_name="Member", dataset_mode="dependent", window_date_column="CreatedDate",
        depends_on_dataset_id=1, dependency_target_column="member_id", include_own_window=False,
    )
    where = query_builder.build_where_clause(dataset, "t", run_id=1, anchor_date=datetime.date(2026, 9, 1), window_years=2)
    # No date predicate anywhere - a Member created in 1999 is included as
    # long as a windowed Claim references it.
    assert "CreatedDate" not in where.sql
    assert "BETWEEN" not in where.sql


def test_unknown_dataset_mode_raises():
    dataset = _dataset(dataset_mode="bogus")
    with pytest.raises(ValueError, match="Unknown dataset_mode"):
        query_builder.build_where_clause(dataset, "t")


# ---------------------------------------------------------------------------
# The full SELECT
# ---------------------------------------------------------------------------


def test_build_select_orders_by_ordinal_and_dedupes_columns():
    dataset = _dataset()
    fields = [
        _field(2, "MemberId", "member_id", "int"),
        _field(1, "ClaimNumber", "claim_id", "varchar(20)"),
        _field(3, "ClaimNumberAgain", "claim_id", "varchar(20)"),  # same source col, reused
    ]
    plan = query_builder.build_select(
        dataset, fields, [], anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "[t].[member_id] AS [member_id]" in plan.sql
    assert plan.sql.count("[claim_id]") == 2  # selected once, not twice
    assert "WHERE [t].[ServiceDate] BETWEEN ? AND ?" in plan.sql
    assert plan.params == (datetime.date(2024, 9, 1), datetime.date(2026, 9, 1))
    assert plan.schema_overrides["member_id"] == pl.Int32
    assert [fm.target_column for fm in plan.field_maps] == ["ClaimNumber", "MemberId", "ClaimNumberAgain"]


def test_build_select_skips_stateful_rule_columns():
    dataset = _dataset()
    fields = [
        _field(1, "ClaimNumber", "claim_id", "varchar(20)"),
        _field(2, "SurrogateKey", "n/a", "bigint", rule_name="row_sequence",
               rule_params={"order_by": "claim_id"}),
    ]
    plan = query_builder.build_select(
        dataset, fields, [], anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "n/a" not in plan.sql
    assert "SurrogateKey" not in plan.schema_overrides


def test_build_select_raises_when_only_stateful_fields_exist():
    dataset = _dataset()
    fields = [_field(1, "SurrogateKey", "n/a", "bigint", rule_name="row_sequence",
                      rule_params={"order_by": "x"})]
    with pytest.raises(ValueError, match="no non-stateful fields"):
        query_builder.build_select(dataset, fields, [], anchor_date=datetime.date(2026, 9, 1), window_years=2)


def test_resolve_order_by_sql_qualifies_a_bare_column():
    assert query_builder.resolve_order_by_sql("claim_id", "t") == "[t].[claim_id]"


def test_resolve_order_by_sql_trusts_an_already_qualified_reference():
    assert query_builder.resolve_order_by_sql("t.claim_id", "t") == "t.claim_id"


def test_build_select_adds_order_by_when_row_sequence_present():
    """Without this, batch arrival order (Polars) and ROW_NUMBER() OVER
    (ORDER BY ...) (SQL) would have no guaranteed relationship - a real
    determinism gap found via the live end-to-end walkthrough."""
    dataset = _dataset()
    fields = [
        _field(1, "ClaimNumber", "claim_id", rule_name="trim"),
        _field(2, "SurrogateKey", "n/a", rule_name="row_sequence", rule_params={"order_by": "claim_id"}),
    ]
    plan = query_builder.build_select(
        dataset, fields, [], anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert plan.sql.rstrip().endswith("ORDER BY [t].[claim_id]")


def test_build_select_has_no_order_by_without_row_sequence():
    dataset = _dataset()
    fields = [_field(1, "ClaimNumber", "claim_id", rule_name="trim")]
    plan = query_builder.build_select(
        dataset, fields, [], anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "ORDER BY" not in plan.sql


def test_build_select_order_by_ambiguous_column_is_qualified_not_bare():
    """The exact scenario that failed live: a row_sequence order_by naming a
    column that ALSO exists on a joined lookup table must be qualified
    against the base table, not left bare."""
    dataset = _dataset()
    fields = [
        _field(1, "PaidAmount", "lookup:calc.amount", rule_name="decimal_format",
               rule_params={"scale": 2, "mask": "N2"}),
        _field(2, "SurrogateKey", "n/a", rule_name="row_sequence", rule_params={"order_by": "claim_id"}),
    ]
    lookups = [DatasetLookup(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left")]
    plan = query_builder.build_select(
        dataset, fields, lookups, anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "ORDER BY [t].[claim_id]" in plan.sql
    assert "ORDER BY claim_id" not in plan.sql  # never bare


def test_build_select_includes_join_clause():
    dataset = _dataset()
    fields = [_field(1, "PaidAmount", "lookup:calc.amount", "decimal(18,2)")]
    lookups = [DatasetLookup(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left")]
    plan = query_builder.build_select(
        dataset, fields, lookups, anchor_date=datetime.date(2026, 9, 1), window_years=2
    )
    assert "LEFT JOIN [src].[claim_calc] AS [calc]" in plan.sql
