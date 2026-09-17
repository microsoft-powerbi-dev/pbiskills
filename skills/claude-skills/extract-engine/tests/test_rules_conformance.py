"""The rule-conformance suite: the quality gate for the whole two-renderings
design.

Per docs/extract-engine-mvp-prompt-v2-polars.md: "For every rule, for a fixed
set of inputs including nulls, empty strings, boundary values, and non-ASCII
characters, assert the Polars rendering and the T-SQL rendering produce
identical strings. If a rule cannot pass conformance, it does not go in the
catalog."

This is the one test module in the suite that legitimately needs a live SQL
Server: T-SQL semantics (rounding mode, date formatting, NULL propagation)
cannot be faithfully reimplemented in Python without risking the test proving
nothing but its own reimplementation. Gated behind
EXTRACT_ENGINE_LOCALDB=1 so the rest of the suite stays fully offline, the
same posture sql-server-schema's own tests already use.

Already found and fixed by this suite once, during development: Polars'
default round() mode is half_to_even ("banker's rounding"); T-SQL's ROUND/
FORMAT use half_away_from_zero ("commercial rounding"). decimal_format now
passes mode="half_away_from_zero" explicitly. See
references/rule-catalog-and-conformance.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import connection as conn_mod  # noqa: E402
from extract_engine import rules  # noqa: E402

LOCALDB_SERVER = r"(localdb)\MSSQLLocalDB"

pytestmark = pytest.mark.skipif(
    os.environ.get("EXTRACT_ENGINE_LOCALDB") != "1",
    reason="set EXTRACT_ENGINE_LOCALDB=1 to run conformance against a live LocalDB instance",
)


@pytest.fixture(scope="module")
def sql_conn():
    try:
        with conn_mod.read_connection(server=LOCALDB_SERVER, database="master") as conn:
            yield conn
    except conn_mod.ConnectionError_ as exc:  # pragma: no cover - env dependent
        pytest.skip("LocalDB unreachable: {}".format(exc))


def _sql_render(conn, rule_name: str, sql_literal: str, params: dict) -> Optional[str]:
    """Execute a rule's rendered T-SQL fragment against a literal and return the result."""
    rule_obj = rules.get_rule(rule_name)
    rendered = rules.render_sql(rule_obj, sql_literal, params)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT {} AS v".format(rendered))
        row = cursor.fetchone()
        return None if row is None or row[0] is None else str(row[0])
    finally:
        cursor.close()


def _polars_render(rule_name: str, value: Any, params: dict, dtype=pl.Utf8) -> Optional[str]:
    rule_obj = rules.get_rule(rule_name)
    df = pl.DataFrame({"v": pl.Series([value], dtype=dtype)})
    result = df.select(rule_obj.polars_fn(pl.col("v"), params).cast(pl.Utf8).alias("out"))
    value_out = result["out"][0]
    return None if value_out is None else str(value_out)


def _sql_str_literal(value: Optional[str]) -> str:
    """A T-SQL NVARCHAR literal (or NULL) for a Python string/None."""
    if value is None:
        return "CAST(NULL AS NVARCHAR(4000))"
    return "N'{}'".format(value.replace("'", "''"))


# ---------------------------------------------------------------------------
# String rules: trim, upper, pad_left, truncate, default_if_null, code_lookup
# ---------------------------------------------------------------------------

STRING_CASES = [None, "", "   ", "abc", "  abc  ", "café", "日本語", "O'Brien"]


@pytest.mark.parametrize("value", STRING_CASES)
def test_trim_conformance(sql_conn, value):
    polars_out = _polars_render("trim", value, {})
    sql_out = _sql_render(sql_conn, "trim", _sql_str_literal(value), {})
    assert polars_out == sql_out, "trim({!r}): polars={!r} sql={!r}".format(value, polars_out, sql_out)


@pytest.mark.parametrize("value", STRING_CASES)
def test_upper_conformance(sql_conn, value):
    polars_out = _polars_render("upper", value, {})
    sql_out = _sql_render(sql_conn, "upper", _sql_str_literal(value), {})
    assert polars_out == sql_out, "upper({!r}): polars={!r} sql={!r}".format(value, polars_out, sql_out)


@pytest.mark.parametrize("value", [None, "", "7", "12345", "999999", "123456"])
def test_pad_left_conformance(sql_conn, value):
    params = {"width": 5, "char": "0"}
    polars_out = _polars_render("pad_left", value, params)
    sql_out = _sql_render(sql_conn, "pad_left", _sql_str_literal(value), params)
    assert polars_out == sql_out, "pad_left({!r}): polars={!r} sql={!r}".format(value, polars_out, sql_out)


@pytest.mark.parametrize("value", [None, "", "ab", "hello world", "café résumé"])
def test_truncate_conformance(sql_conn, value):
    params = {"length": 5}
    polars_out = _polars_render("truncate", value, params)
    sql_out = _sql_render(sql_conn, "truncate", _sql_str_literal(value), params)
    assert polars_out == sql_out, "truncate({!r}): polars={!r} sql={!r}".format(value, polars_out, sql_out)


@pytest.mark.parametrize("value", [None, "", "x"])
def test_default_if_null_conformance(sql_conn, value):
    params = {"value": "N/A"}
    polars_out = _polars_render("default_if_null", value, params)
    sql_out = _sql_render(sql_conn, "default_if_null", _sql_str_literal(value), params)
    assert polars_out == sql_out, "default_if_null({!r}): polars={!r} sql={!r}".format(
        value, polars_out, sql_out
    )


@pytest.mark.parametrize("value", ["A", "B", "Z", None, "O'Brien"])
def test_code_lookup_conformance(sql_conn, value):
    params = {"mapping": {"A": "Active", "B": "Blocked", "O'Brien": "Weird"}, "default": "Unknown"}
    polars_out = _polars_render("code_lookup", value, params)
    sql_out = _sql_render(sql_conn, "code_lookup", _sql_str_literal(value), params)
    assert polars_out == sql_out, "code_lookup({!r}): polars={!r} sql={!r}".format(
        value, polars_out, sql_out
    )


# ---------------------------------------------------------------------------
# Numeric: decimal_format. Includes the exact half-way tie values that
# exposed the rounding-mode bug during development.
# ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402


@pytest.mark.parametrize(
    "value",
    [None, Decimal("0.00"), Decimal("12.345"), Decimal("12.325"), Decimal("-1.005"), Decimal("999999.995")],
)
def test_decimal_format_conformance(sql_conn, value):
    params = {"scale": 2, "mask": "N2"}
    rule_obj = rules.get_rule("decimal_format")
    df = pl.DataFrame({"v": pl.Series([value], dtype=pl.Decimal(18, 3))})
    polars_out = df.select(rule_obj.polars_fn(pl.col("v"), params).alias("out"))["out"][0]

    sql_literal = "CAST(NULL AS DECIMAL(18,3))" if value is None else "CAST({} AS DECIMAL(18,3))".format(value)
    rendered = "ROUND({}, {})".format(sql_literal, params["scale"])
    cursor = sql_conn.cursor()
    try:
        cursor.execute("SELECT {} AS v".format(rendered))
        row = cursor.fetchone()
        sql_out = None if row is None or row[0] is None else str(row[0])
    finally:
        cursor.close()

    # Compare numerically, not stringwise: T-SQL's ROUND(x, 2) on a
    # DECIMAL(18,3) keeps 3 decimal places (e.g. 12.350), Polars' round()
    # keeps the caller's own scale (12.35) - both are the correctly-rounded
    # value, just formatted with a different number of trailing zeros.
    from decimal import Decimal as D

    polars_num = None if polars_out is None else D(str(polars_out))
    sql_num = None if sql_out is None else D(sql_out)
    assert polars_num == sql_num, "decimal_format({}): polars={} sql={}".format(value, polars_num, sql_num)


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

import datetime  # noqa: E402


@pytest.mark.parametrize(
    "value", [None, datetime.date(2026, 9, 8), datetime.date(1900, 1, 1), datetime.date(2099, 12, 31)]
)
def test_date_format_conformance(sql_conn, value):
    params = {"style": 23, "polars_format": "%Y-%m-%d"}
    rule_obj = rules.get_rule("date_format")
    df = pl.DataFrame({"v": pl.Series([value], dtype=pl.Date)})
    polars_out = df.select(rule_obj.polars_fn(pl.col("v"), params).alias("out"))["out"][0]

    sql_literal = "CAST(NULL AS DATE)" if value is None else "'{}'".format(value.isoformat())
    sql_out = _sql_render(sql_conn, "date_format", sql_literal, params)
    assert polars_out == sql_out, "date_format({}): polars={!r} sql={!r}".format(value, polars_out, sql_out)
