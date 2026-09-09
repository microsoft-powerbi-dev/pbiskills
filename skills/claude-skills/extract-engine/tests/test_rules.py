"""Tests for the rule catalog's Polars side (no database needed).

Covers registry mechanics, param validation, and the actual Polars expression
behavior for each of the nine doc rules plus row_sequence, across nulls,
empty strings, and boundary values. Non-ASCII and the Polars-vs-T-SQL
agreement itself are covered by the LocalDB-gated
tests/test_rules_conformance.py, since only a live engine can produce a
faithful T-SQL rendering to compare against.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import rules  # noqa: E402


def _apply(name, values, params=None, dtype=pl.Utf8):
    rule_obj = rules.get_rule(name)
    df = pl.DataFrame({"v": pl.Series(values, dtype=dtype)})
    return df.select(rule_obj.polars_fn(pl.col("v"), params or {}).alias("out"))["out"].to_list()


def test_registry_has_all_ten_rules():
    names = set(rules.registry())
    expected = {
        "passthrough", "trim", "upper", "pad_left", "truncate", "date_format",
        "decimal_format", "code_lookup", "default_if_null", "row_sequence",
    }
    assert expected <= names


def test_unknown_rule_name_raises_and_lists_available():
    with pytest.raises(KeyError) as excinfo:
        rules.get_rule("does_not_exist")
    assert "does_not_exist" in str(excinfo.value)
    assert "trim" in str(excinfo.value)


def test_row_sequence_is_marked_stateful():
    row_seq = rules.get_rule("row_sequence")
    assert rules.is_stateful(row_seq)
    assert row_seq.polars_fn is None
    for other in rules.registry().values():
        if other.name != "row_sequence":
            assert not rules.is_stateful(other)


# ---------------------------------------------------------------------------
# Param validation
# ---------------------------------------------------------------------------


def test_validate_params_catches_missing_required():
    pad = rules.get_rule("pad_left")
    with pytest.raises(ValueError, match="missing required params"):
        rules.validate_params(pad, {"char": "0"})


def test_validate_params_catches_wrong_type():
    pad = rules.get_rule("pad_left")
    with pytest.raises(ValueError, match="must be int"):
        rules.validate_params(pad, {"width": "10", "char": "0"})


def test_validate_params_accepts_correct_shape():
    pad = rules.get_rule("pad_left")
    rules.validate_params(pad, {"width": 10, "char": "0"})  # no raise


def test_parse_rule_params_handles_empty_and_json():
    assert rules.parse_rule_params(None) == {}
    assert rules.parse_rule_params("") == {}
    assert rules.parse_rule_params('{"width": 5, "char": "0"}') == {"width": 5, "char": "0"}


def test_parse_rule_params_rejects_non_object_json():
    with pytest.raises(ValueError):
        rules.parse_rule_params("[1, 2, 3]")


# ---------------------------------------------------------------------------
# Polars-side rule behavior: nulls, empty strings, boundary values
# ---------------------------------------------------------------------------


def test_passthrough():
    assert _apply("passthrough", ["a", None, ""]) == ["a", None, ""]


def test_trim():
    assert _apply("trim", ["  a  ", "", None, "\tb\t"]) == ["a", "", None, "b"]


def test_upper():
    assert _apply("upper", ["abc", "", None, "café"]) == ["ABC", "", None, "CAFÉ"]


def test_pad_left():
    result = _apply("pad_left", ["7", "", None, "12345"], {"width": 5, "char": "0"})
    assert result == ["00007", "00000", None, "12345"]


def test_pad_left_value_already_at_width_is_unchanged():
    assert _apply("pad_left", ["99999"], {"width": 5, "char": "0"}) == ["99999"]


def test_pad_left_over_width_value_is_truncated_to_leftmost_chars():
    # Matches T-SQL's CAST(col AS VARCHAR(width)) truncation exactly - found
    # by the conformance suite. Not a right-truncate: SQL keeps the leftmost
    # `width` characters, confirmed empirically against LocalDB.
    assert _apply("pad_left", ["123456"], {"width": 5, "char": "0"}) == ["12345"]


def test_truncate():
    result = _apply("truncate", ["hello world", "", None, "ab"], {"length": 5})
    assert result == ["hello", "", None, "ab"]  # shorter-than-length is unchanged


def test_truncate_zero_length():
    assert _apply("truncate", ["hello"], {"length": 0}) == [""]


def test_date_format():
    import datetime

    rule_obj = rules.get_rule("date_format")
    df = pl.DataFrame({"v": [datetime.date(2026, 9, 8), None]})
    result = df.select(
        rule_obj.polars_fn(pl.col("v"), {"style": 23, "polars_format": "%Y-%m-%d"}).alias("out")
    )["out"].to_list()
    assert result == ["2026-09-08", None]


def test_decimal_format():
    rule_obj = rules.get_rule("decimal_format")
    df = pl.DataFrame({"v": [12.345, 0.0, None]})
    result = df.select(
        rule_obj.polars_fn(pl.col("v"), {"scale": 2, "mask": "N2"}).alias("out")
    )["out"].to_list()
    assert result == ["12.35", "0.0", None]


def test_code_lookup():
    result = _apply(
        "code_lookup",
        ["A", "B", "Z", None],
        {"mapping": {"A": "Active", "B": "Blocked"}, "default": "Unknown"},
    )
    assert result == ["Active", "Blocked", "Unknown", "Unknown"]


def test_default_if_null():
    result = _apply("default_if_null", ["x", "", None], {"value": "N/A"})
    assert result == ["x", "", "N/A"]


# ---------------------------------------------------------------------------
# T-SQL rendering (text-level, no DB): code_lookup's special composition
# ---------------------------------------------------------------------------


def test_render_sql_simple_rule():
    rule_obj = rules.get_rule("pad_left")
    rendered = rules.render_sql(rule_obj, "[Col]", {"width": 5, "char": "0"})
    assert rendered == "RIGHT(REPLICATE('0', 5) + CAST([Col] AS VARCHAR(5)), 5)"


def test_render_sql_escapes_string_literals():
    rule_obj = rules.get_rule("default_if_null")
    rendered = rules.render_sql(rule_obj, "[Col]", {"value": "O'Brien"})
    assert rendered == "ISNULL([Col], 'O''Brien')"


def test_render_code_lookup_sql_is_deterministic_and_sorted():
    rendered = rules.render_sql(
        rules.get_rule("code_lookup"),
        "[Col]",
        {"mapping": {"B": "Blocked", "A": "Active"}, "default": "Unknown"},
    )
    assert rendered == (
        "CASE WHEN [Col] = 'A' THEN 'Active' WHEN [Col] = 'B' THEN 'Blocked' "
        "ELSE 'Unknown' END"
    )


def test_render_code_lookup_sql_escapes_mapping_values():
    rendered = rules.render_sql(
        rules.get_rule("code_lookup"),
        "[Col]",
        {"mapping": {"X": "O'Brien"}, "default": "N/A"},
    )
    assert "O''Brien" in rendered
