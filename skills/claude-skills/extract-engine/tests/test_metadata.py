"""Tests for the typed meta.* accessors, against a FakeConnection."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_engine import metadata  # noqa: E402
from fakes import FakeConnection  # noqa: E402

FEED_FIELDS = [
    "feed_id", "feed_name", "source_server", "source_database", "delimiter",
    "collision_action", "collision_char", "line_ending", "null_sentinel",
    "max_rows_per_file", "emit_header_row", "emit_trailer_row", "emit_concat_ws_line",
    "default_execution_mode", "output_root", "anchor_date_default",
    "window_years_default", "is_active",
]
FEED_ROW = (
    1, "ClaimsExtract", "SQLPROD01", "ClaimsDW", "|", "sanitize", " ", "CRLF", "",
    1000000, 0, 1, 0, "polars", "D:\\Extracts\\ClaimsExtract", None, 2, 1,
)


def test_get_feed_maps_all_fields():
    conn = FakeConnection({r"FROM meta\.feed": (FEED_FIELDS, [FEED_ROW])})
    feed = metadata.get_feed(conn, "ClaimsExtract")
    assert feed.feed_id == 1
    assert feed.feed_name == "ClaimsExtract"
    assert feed.collision_action == "sanitize"
    assert feed.emit_trailer_row is True
    assert feed.emit_header_row is False
    assert feed.default_execution_mode == "polars"
    assert feed.window_years_default == 2


def test_get_feed_returns_none_when_not_found():
    conn = FakeConnection({r"FROM meta\.feed": (FEED_FIELDS, [])})
    assert metadata.get_feed(conn, "Nope") is None


DATASET_FIELDS = [
    "dataset_id", "feed_id", "dataset_name", "source_schema", "source_table",
    "dataset_mode", "primary_key_columns", "window_date_column",
    "depends_on_dataset_id", "dependency_source_column", "dependency_target_column",
    "include_own_window", "output_file_stem", "run_ordinal", "is_active",
]


def test_list_datasets_splits_composite_pk():
    # The real ORDER BY run_ordinal in list_datasets' SQL is enforced by SQL
    # Server, not by this Python code - FakeConnection just replays whatever
    # rows the test hands it, in that order, so the fixture supplies them
    # already in run_ordinal order (as the live engine would return them).
    rows = [
        (1, 1, "Claim", "src", "claim", "primary", "ClaimId,LineNo", "ServiceDate", None, None, None, 0, "Claim", 1, 1),
        (2, 1, "Member", "src", "member", "dependent", "MemberId", None, 1, "MemberId", "MemberId", 0, "Member", 2, 1),
    ]
    conn = FakeConnection({r"FROM meta\.dataset": (DATASET_FIELDS, rows)})
    datasets = metadata.list_datasets(conn, 1)
    assert [d.dataset_name for d in datasets] == ["Claim", "Member"]
    claim = datasets[0]
    assert claim.primary_key_columns == ["ClaimId", "LineNo"]
    assert claim.dataset_mode == "primary"
    assert claim.qualified_table == "[src].[claim]"
    member = datasets[1]
    assert member.dataset_mode == "dependent"
    assert member.depends_on_dataset_id == 1


FIELD_MAP_FIELDS = [
    "field_map_id", "dataset_id", "ordinal", "target_column", "source_expression",
    "data_type", "rule_name", "rule_params", "nullable", "default_value", "is_active",
]


def test_list_field_maps_parses_rule_params_json():
    rows = [
        (1, 1, 1, "ClaimNumber", "claim_id", "varchar(20)", "trim", None, 0, None, 1),
        (2, 1, 2, "PaidAmount", "lookup:calc.amount", "decimal(18,2)", "decimal_format",
         '{"scale": 2, "mask": "N2"}', 1, "0.00", 1),
    ]
    conn = FakeConnection({r"FROM meta\.field_map": (FIELD_MAP_FIELDS, rows)})
    fields = metadata.list_field_maps(conn, 1)
    assert fields[0].rule_params == {}
    assert fields[1].rule_params == {"scale": 2, "mask": "N2"}
    assert fields[1].lookup_alias == "calc"
    assert fields[1].source_column == "amount"
    assert fields[0].lookup_alias is None
    assert fields[0].source_column == "claim_id"


LOOKUP_FIELDS = [
    "dataset_lookup_id", "dataset_id", "lookup_alias", "lookup_schema",
    "lookup_table", "join_source_column", "join_lookup_column", "join_type",
]


def test_list_lookups():
    rows = [(1, 1, "calc", "src", "claim_calc", "claim_id", "claim_id", "left")]
    conn = FakeConnection({r"FROM meta\.dataset_lookup": (LOOKUP_FIELDS, rows)})
    lookups = metadata.list_lookups(conn, 1)
    assert lookups[0].qualified_table == "[src].[claim_calc]"
    assert lookups[0].join_type == "left"
