"""
query_builder.py - the reconstructed three-dataset-mode SQL semantics.

This is the piece of the design most grounded in inference rather than a
supplied spec (see references/dataset-modes-and-metadata-schema.md) and the
one most likely to need correction once real customer schema is available.

Three dataset modes, three WHERE-clause shapes:

* ``primary``   - WHERE <window_date_column> BETWEEN ? AND ?
* ``dependent`` - WHERE <dependency_target_column> IN (
                    SELECT key_value FROM meta.run_dataset_key
                    WHERE run_id = ? AND dataset_id = ?)
                  optionally UNIONed with its own window filter when
                  include_own_window=1.
* ``reference`` - no filter at all; the dataset ships in full every run.

Both execution paths share this module: path A calls ``build_select`` to get
a parameterized query text plus bind params for ``pl.read_database``; path B
(``execution_sql.py``) reuses ``build_where_clause``/``build_join_clause`` to
compose the generated view's ``WHERE``/``FROM`` text, so the two paths cannot
silently drift on what "the window" or "the dependency" means.

No caller-supplied string is ever interpolated as a *value* here - window
bounds and key-set lookups are always bind parameters (``?``). Table, schema,
and column names come only from ``meta.*`` rows (already validated by
config_loader before they were ever stored), and are bracket-quoted, never
values a live caller supplies at query time.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .metadata import Dataset, DatasetLookup, FieldMap
from . import rules as rules_mod


# ---------------------------------------------------------------------------
# Data type mapping: meta.field_map.data_type -> Polars dtype, for schema
# pinning (never inference - see execution_polars.py's read_batches).
# ---------------------------------------------------------------------------


def data_type_to_polars(data_type: str):
    """Map a neutral data_type token to a Polars dtype for schema_overrides."""
    import re

    import polars as pl

    token = data_type.strip().lower()
    if token in ("int", "integer"):
        return pl.Int32
    if token == "bigint":
        return pl.Int64
    if token == "bit":
        return pl.Boolean
    if token == "date":
        return pl.Date
    if token in ("datetime", "datetime2"):
        return pl.Datetime
    decimal_match = re.match(r"decimal\((\d+)\s*,\s*(\d+)\)", token)
    if decimal_match:
        precision, scale = decimal_match.groups()
        return pl.Decimal(int(precision), int(scale))
    if token.startswith("varchar") or token.startswith("nvarchar") or token.startswith("char"):
        return pl.Utf8
    raise ValueError("Unrecognized data_type token: {!r}".format(data_type))


def _bracket(name: str) -> str:
    return "[{}]".format(name.replace("]", "]]"))


def _qualified_column(alias: str, column: str) -> str:
    return "{}.{}".format(_bracket(alias), _bracket(column))


def resolve_order_by_sql(order_by_raw: str, base_alias: str) -> str:
    """Qualify a row_sequence rule's ``order_by`` param against the base table.

    A bare column name (the common case - e.g. an author writing
    ``{"order_by": "claim_id"}`` in the KeyGeneration sheet) is qualified
    against ``base_alias`` automatically. A value that already contains a
    dot (an author who wrote ``t.claim_id``, or a lookup-qualified
    reference) is trusted as already resolved and passed through.

    Without this, an unqualified column name that also exists on a joined
    calculator/lookup table produces "Ambiguous column name" - found live
    when a dataset both joined a table sharing a column name with its own
    primary key AND used that column as a row_sequence order_by.
    """
    if "." in order_by_raw:
        return order_by_raw
    return _qualified_column(base_alias, order_by_raw)


# ---------------------------------------------------------------------------
# Source-expression resolution: a bare column, or 'lookup:<alias>.<column>'.
# ---------------------------------------------------------------------------


def resolve_source_column_sql(field_map: FieldMap, base_alias: str) -> str:
    """The qualified column reference for a field_map row's source expression."""
    if field_map.lookup_alias:
        return _qualified_column(field_map.lookup_alias, field_map.source_column)
    return _qualified_column(base_alias, field_map.source_column)


# ---------------------------------------------------------------------------
# JOIN clause for calculator/lookup tables - shared by both execution paths.
# ---------------------------------------------------------------------------


def build_join_clause(lookups: Sequence[DatasetLookup], base_alias: str) -> str:
    """One JOIN per calculator/lookup table, aliased as declared in metadata."""
    parts = []
    for lookup in lookups:
        join_kind = "LEFT JOIN" if lookup.join_type == "left" else "INNER JOIN"
        parts.append(
            "{kind} {table} AS {alias} ON {alias_col} = {base_col}".format(
                kind=join_kind,
                table=lookup.qualified_table,
                alias=_bracket(lookup.lookup_alias),
                alias_col=_qualified_column(lookup.lookup_alias, lookup.join_lookup_column),
                base_col=_qualified_column(base_alias, lookup.join_source_column),
            )
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# WHERE clause per dataset mode.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WhereClause:
    sql: str
    params: Tuple[Any, ...]


def build_where_clause(
    dataset: Dataset,
    base_alias: str,
    *,
    anchor_date: Optional[datetime.date] = None,
    window_years: Optional[int] = None,
    run_id: Optional[int] = None,
    window_column_sql: Optional[str] = None,
    dependency_column_sql: Optional[str] = None,
) -> WhereClause:
    """The mode-specific filter. See the module docstring for the three shapes.

    ``window_column_sql``/``dependency_column_sql`` let a caller filter
    against a different column reference than "base_alias.raw_column_name" -
    needed by execution_sql.py's view-read path, where filtering happens
    against the view's exposed internal filter columns rather than a table
    alias, since the view's own output columns are the *formatted* target
    values (e.g. a date rendered as a string), not something safe to filter
    on directly. Path A (this function's usual caller) never passes these -
    it filters the raw table directly, so the default (derived from
    base_alias) is what it needs.
    """
    if dataset.dataset_mode == "reference":
        return WhereClause(sql="1 = 1", params=())

    if dataset.dataset_mode == "primary":
        if not dataset.window_date_column:
            raise ValueError(
                "Dataset {!r} is mode 'primary' but has no window_date_column; "
                "this should have been caught by config_loader validation.".format(
                    dataset.dataset_name
                )
            )
        if anchor_date is None or window_years is None:
            raise ValueError("anchor_date and window_years are required for a primary dataset.")
        window_start = anchor_date.replace(year=anchor_date.year - window_years)
        col = window_column_sql or _qualified_column(base_alias, dataset.window_date_column)
        return WhereClause(sql="{} BETWEEN ? AND ?".format(col), params=(window_start, anchor_date))

    if dataset.dataset_mode == "dependent":
        if not (dataset.depends_on_dataset_id and dataset.dependency_target_column):
            raise ValueError(
                "Dataset {!r} is mode 'dependent' but is missing depends_on_dataset_id "
                "or dependency_target_column; this should have been caught by "
                "config_loader validation.".format(dataset.dataset_name)
            )
        if run_id is None:
            raise ValueError("run_id is required to resolve a dependent dataset's key set.")
        target_col = dependency_column_sql or _qualified_column(base_alias, dataset.dependency_target_column)
        key_set_predicate = (
            "{target} IN (SELECT key_value FROM meta.run_dataset_key "
            "WHERE run_id = ? AND dataset_id = ?)"
        ).format(target=target_col)
        params: List[Any] = [run_id, dataset.depends_on_dataset_id]

        if dataset.include_own_window:
            if not dataset.window_date_column:
                raise ValueError(
                    "Dataset {!r} has include_own_window=1 but no window_date_column.".format(
                        dataset.dataset_name
                    )
                )
            if anchor_date is None or window_years is None:
                raise ValueError("anchor_date and window_years are required when include_own_window=1.")
            window_start = anchor_date.replace(year=anchor_date.year - window_years)
            window_col = window_column_sql or _qualified_column(base_alias, dataset.window_date_column)
            sql = "({}) OR ({} BETWEEN ? AND ?)".format(key_set_predicate, window_col)
            params.extend([window_start, anchor_date])
        else:
            sql = key_set_predicate
        return WhereClause(sql=sql, params=tuple(params))

    raise ValueError("Unknown dataset_mode {!r}".format(dataset.dataset_mode))


# ---------------------------------------------------------------------------
# The full SELECT for execution path A.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadPlan:
    sql: str
    params: Tuple[Any, ...]
    schema_overrides: dict  # {target_column: pl.DataType}
    field_maps: Tuple[FieldMap, ...]  # ordinal order, for the transform step


def build_select(
    dataset: Dataset,
    field_maps: Sequence[FieldMap],
    lookups: Sequence[DatasetLookup],
    *,
    anchor_date: Optional[datetime.date] = None,
    window_years: Optional[int] = None,
    run_id: Optional[int] = None,
) -> ReadPlan:
    """Build the parameterized SELECT that feeds pl.read_database.

    Selects raw source columns (pre-transform, pre-rename): the transform
    step in execution_polars.py applies field_maps' rules and renames to
    target_column afterward, keeping this query's job purely "get the right
    rows with the right raw values" and nothing else.
    """
    base_alias = "t"
    ordered = sorted(field_maps, key=lambda f: f.ordinal)

    # One SELECT item per distinct source column actually referenced (a
    # row_sequence field has no source column at all and is skipped here -
    # execution_polars.py adds it after the read, from the batch offset).
    select_items = []
    seen = set()
    for fm in ordered:
        rule_obj = rules_mod.get_rule(fm.rule_name)
        if rules_mod.is_stateful(rule_obj):
            continue
        col_sql = resolve_source_column_sql(fm, base_alias)
        if col_sql in seen:
            continue
        seen.add(col_sql)
        select_items.append("{} AS {}".format(col_sql, _bracket(fm.source_column)))

    if not select_items:
        raise ValueError("Dataset {!r} has no non-stateful fields to select.".format(dataset.dataset_name))

    join_clause = build_join_clause(lookups, base_alias)
    where = build_where_clause(
        dataset, base_alias, anchor_date=anchor_date, window_years=window_years, run_id=run_id
    )

    # An explicit ORDER BY is required whenever any field uses row_sequence:
    # without one, the batch arrival order Polars numbers sequentially has no
    # defined relationship to SQL Server's own row order (never guaranteed
    # without an ORDER BY), which would make the two execution paths assign
    # different sequence numbers to the same logical row - undermining both
    # determinism and --compare-modes' checksum equality. The same order_by
    # value also drives the SQL path's ROW_NUMBER() OVER (ORDER BY ...), so
    # the two paths stay in lockstep.
    order_by_clause = ""
    for fm in ordered:
        rule_obj = rules_mod.get_rule(fm.rule_name)
        if rules_mod.is_stateful(rule_obj):
            order_by_raw = fm.rule_params.get("order_by")
            if order_by_raw:
                order_by_clause = " ORDER BY {}".format(resolve_order_by_sql(order_by_raw, base_alias))
            break

    sql = "SELECT {} FROM {} AS {} {} WHERE {}{}".format(
        ", ".join(select_items),
        dataset.qualified_table,
        _bracket(base_alias),
        join_clause,
        where.sql,
        order_by_clause,
    ).strip()

    schema_overrides = {
        fm.source_column: data_type_to_polars(fm.data_type)
        for fm in ordered
        if not rules_mod.is_stateful(rules_mod.get_rule(fm.rule_name))
    }

    return ReadPlan(sql=sql, params=where.params, schema_overrides=schema_overrides, field_maps=tuple(ordered))
