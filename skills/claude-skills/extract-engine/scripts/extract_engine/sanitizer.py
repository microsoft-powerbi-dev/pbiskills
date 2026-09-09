"""
sanitizer.py - delimiter/newline/tab collision handling, both renderings.

A value containing the output delimiter, a carriage return, a line feed, or a
tab corrupts a pipe-delimited flat file's structure for every downstream
consumer - it shifts every subsequent field on that line, or splits one
logical record into two lines. This module makes that handling deliberate
per ``meta.feed.collision_action``:

* ``sanitize`` - replace every occurrence of the delimiter, CR, LF, and TAB
  with ``collision_char`` in the value.
* ``fail`` - refuse to write the file at all if the offending character
  appears anywhere in the column, naming only the column and (for the batched
  Polars path) the batch ordinal. The value itself is never logged.

Both renderings must sanitize identically, or ``--compare-modes``' checksum
comparison would fail on any feed whose source data actually contains a
delimiter character - which, for free-text columns, is not a hypothetical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover
    import polars as pl


class CollisionError(RuntimeError):
    """Raised when collision_action='fail' and a collision is found.

    The message names only the column and (when known) the batch ordinal -
    never the offending value, per the project's PII/PHI posture.
    """

    def __init__(self, column: str, batch_ordinal: int = -1):
        self.column = column
        self.batch_ordinal = batch_ordinal
        if batch_ordinal >= 0:
            message = (
                "Delimiter/newline collision found in column {!r} (batch {}). "
                "Set collision_action='sanitize' to neutralize it instead of "
                "aborting.".format(column, batch_ordinal)
            )
        else:
            message = (
                "Delimiter/newline collision found in column {!r}. Set "
                "collision_action='sanitize' to neutralize it instead of "
                "aborting.".format(column)
            )
        super().__init__(message)


_COLLISION_CHARS_SQL = {
    "delimiter": None,  # substituted per-feed, not a fixed literal
    "cr": "CHAR(13)",
    "lf": "CHAR(10)",
    "tab": "CHAR(9)",
}


def sanitize_expr_polars(col: "pl.Expr", delimiter: str, collision_char: str) -> "pl.Expr":
    """Chain str.replace_all for delimiter, CR, LF, TAB - the Polars rendering."""
    result = col.cast(__import__("polars").Utf8)
    for bad in (delimiter, "\r", "\n", "\t"):
        result = result.str.replace_all(bad, collision_char, literal=True)
    return result


def sanitize_expr_sql(col_sql: str, delimiter: str, collision_char: str) -> str:
    """Nested REPLACE calls producing the identical result server-side.

    Composed purely from delimiter/collision_char, matching the doc's own
    example: REPLACE(REPLACE(REPLACE(REPLACE(expr,'|',' '),CHAR(13),' '),
    CHAR(10),' '),CHAR(9),' ').
    """
    escaped_delim = delimiter.replace("'", "''")
    escaped_char = collision_char.replace("'", "''")
    expr = col_sql
    expr = "REPLACE({}, '{}', '{}')".format(expr, escaped_delim, escaped_char)
    expr = "REPLACE({}, CHAR(13), '{}')".format(expr, escaped_char)
    expr = "REPLACE({}, CHAR(10), '{}')".format(expr, escaped_char)
    expr = "REPLACE({}, CHAR(9), '{}')".format(expr, escaped_char)
    return expr


def check_collision_polars(df, column: str, delimiter: str, batch_ordinal: int = -1) -> None:
    """Raise CollisionError if collision_action='fail' and a hit is found.

    Checked per-batch so a "fail" feed aborts as early as possible rather
    than after writing most of a large file.
    """
    import polars as pl

    col = df[column].cast(pl.Utf8)
    hit = (
        col.str.contains(delimiter, literal=True).fill_null(False).any()
        or col.str.contains("\r", literal=True).fill_null(False).any()
        or col.str.contains("\n", literal=True).fill_null(False).any()
        or col.str.contains("\t", literal=True).fill_null(False).any()
    )
    if hit:
        raise CollisionError(column, batch_ordinal)


@dataclass(frozen=True)
class CollisionProbe:
    """A one-time, pre-deployment check for execution path B's 'fail' mode."""

    column: str
    sql: str  # SELECT TOP 1 1 FROM <table> WHERE <collision predicate>


def build_collision_probe(table_sql: str, column_sql: str, column_name: str, delimiter: str) -> CollisionProbe:
    """A cheap existence probe run once before deploying a view with
    collision_action='fail', so the abort happens at deploy time, not on the
    first row a consumer happens to read.
    """
    escaped_delim = delimiter.replace("'", "''")
    predicate = (
        "{col} LIKE '%' + '{delim}' + '%' ESCAPE '\\' "
        "OR {col} LIKE '%' + CHAR(13) + '%' "
        "OR {col} LIKE '%' + CHAR(10) + '%' "
        "OR {col} LIKE '%' + CHAR(9) + '%'"
    ).format(col=column_sql, delim=escaped_delim)
    sql = "SELECT TOP (1) 1 AS hit FROM {table} WHERE {predicate}".format(
        table=table_sql, predicate=predicate
    )
    return CollisionProbe(column=column_name, sql=sql)
