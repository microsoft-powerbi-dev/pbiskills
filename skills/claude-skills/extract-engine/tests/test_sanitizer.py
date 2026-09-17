"""Tests for delimiter/newline/tab collision handling: both renderings must
agree, and 'fail' mode must abort naming only the column, never the value.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import sanitizer  # noqa: E402


def test_sanitize_expr_polars_replaces_all_four_collision_chars():
    df = pl.DataFrame({"v": ["a|b", "c\rd", "e\nf", "g\th", "clean", None]})
    out = df.select(sanitizer.sanitize_expr_polars(pl.col("v"), "|", " ").alias("out"))["out"].to_list()
    assert out == ["a b", "c d", "e f", "g h", "clean", None]


def test_sanitize_expr_sql_composes_nested_replace():
    rendered = sanitizer.sanitize_expr_sql("[Col]", "|", " ")
    assert rendered == (
        "REPLACE(REPLACE(REPLACE(REPLACE([Col], '|', ' '), CHAR(13), ' '), "
        "CHAR(10), ' '), CHAR(9), ' ')"
    )


def test_sanitize_expr_sql_escapes_delimiter_and_collision_char():
    rendered = sanitizer.sanitize_expr_sql("[Col]", "'", "'")
    # A delimiter/collision char that happens to be a quote must not break
    # the generated DDL's string literals.
    assert "''" in rendered


def test_check_collision_polars_finds_delimiter_hit():
    df = pl.DataFrame({"v": ["clean", "has|pipe"]})
    with pytest.raises(sanitizer.CollisionError) as excinfo:
        sanitizer.check_collision_polars(df, "v", "|", batch_ordinal=3)
    assert excinfo.value.column == "v"
    assert excinfo.value.batch_ordinal == 3
    # The value must never appear in the error message.
    assert "has|pipe" not in str(excinfo.value)


def test_check_collision_polars_finds_cr_lf_tab():
    for bad_value in ["a\rb", "a\nb", "a\tb"]:
        df = pl.DataFrame({"v": [bad_value]})
        with pytest.raises(sanitizer.CollisionError):
            sanitizer.check_collision_polars(df, "v", "|")


def test_check_collision_polars_passes_clean_data():
    df = pl.DataFrame({"v": ["clean", "also clean", None]})
    sanitizer.check_collision_polars(df, "v", "|")  # no raise


def test_check_collision_polars_handles_nulls_without_raising_on_null():
    df = pl.DataFrame({"v": [None, None]})
    sanitizer.check_collision_polars(df, "v", "|")  # no raise


def test_build_collision_probe_shape():
    probe = sanitizer.build_collision_probe("src.claim", "c.notes", "notes", "|")
    assert probe.column == "notes"
    assert "src.claim" in probe.sql
    assert "TOP (1)" in probe.sql
