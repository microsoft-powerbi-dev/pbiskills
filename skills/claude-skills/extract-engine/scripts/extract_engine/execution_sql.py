"""
execution_sql.py - execution path B: transform in SQL via generated views.

Same metadata as path A, different backend: a view per dataset in the gen
schema, columns already formatted, sanitized, and in ordinal order. The
client then reads the view and writes it with zero per-value work.

**Design note (reconstructed, not from the v2 doc's own example - confirm
with user).** The doc's own ``gen.v_Claim`` example has no WHERE clause at
all, and per-run filtering (anchor_date/window_years/run_id) obviously can't
be baked into a view that is deployed once and reused across runs. The gap:
the view's *output* columns are the formatted target values (e.g. a date
rendered as ``VARCHAR``), which are not safe or reliable to filter on
(string-sorting a formatted date is fragile, and a dependent dataset's target
column may not even be a plain passthrough). The design here resolves that by
having the view ALSO expose the dataset's *raw* window/dependency column(s),
unformatted, under a reserved internal name (``__window_key`` /
``__dependency_key``). The client-side read query (``build_view_read_query``)
filters on those reserved columns and selects only the real target columns -
so the view stays static (redeployed only when field_map/lookups change,
never per run) while window/dependent filtering is applied identically in
shape to path A's, just against the view instead of the raw table.

Two things this module exists specifically to get right:

* The CONCAT_WS null-shifting trap (see ``render_concat_ws_line``): SQL
  Server's CONCAT_WS skips a NULL argument entirely rather than emitting an
  empty position, which silently shifts every downstream field on any row
  with a null. Every argument is wrapped in ISNULL first, unconditionally.
* Never deploying implicitly: ``deploy_views`` is a distinct, explicit action
  from ``generate_view_ddl``/``explain``, gated by
  ``guardrails.classify_write_statement`` and a CLI flag one level up. A
  scheduled production run (``extract run --mode sql``) never triggers DDL as
  a side effect - it fails fast if the view does not already exist.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import rules as rules_mod
from .metadata import Dataset, DatasetLookup, Feed, FieldMap
from .query_builder import (
    build_join_clause,
    build_where_clause,
    resolve_order_by_sql,
    resolve_source_column_sql,
)
from .sanitizer import build_collision_probe, sanitize_expr_sql

WINDOW_KEY_COLUMN = "__window_key"
DEPENDENCY_KEY_COLUMN = "__dependency_key"
KEY_SOURCE_PREFIX = "__key_source_"


def key_source_alias(raw_column: str) -> str:
    """The reserved internal name a primary dataset's view exposes a raw
    column under, for a dependent dataset elsewhere in the feed to stage as
    its key set.

    Necessary because the view's real SELECT list is target-named (e.g.
    "MemberId"), not raw-named (e.g. "member_id") - a dependent dataset's
    dependency_source_column names the RAW column on the referencing
    dataset's own source table, which is not otherwise guaranteed to be
    retrievable from the deployed view at all. Found live: Polars mode
    happened to work because it reads raw column names before the rename-to-
    target step, but SQL mode reads only the view's already-renamed output,
    so key staging silently produced an empty set there until this existed.
    """
    return "{}{}".format(KEY_SOURCE_PREFIX, raw_column)


def key_columns_needed_from(dataset_id: int, all_datasets: "Sequence[Dataset]") -> List[str]:
    """Which raw columns a PRIMARY dataset's view must expose for key staging,
    derived from every DEPENDENT dataset elsewhere in the feed that references it.
    """
    needed = []
    for other in all_datasets:
        if other.dataset_mode == "dependent" and other.depends_on_dataset_id == dataset_id:
            if other.dependency_source_column and other.dependency_source_column not in needed:
                needed.append(other.dependency_source_column)
    return needed


def _bracket(name: str) -> str:
    return "[{}]".format(name.replace("]", "]]"))


def _column_expression(fm: FieldMap, feed: Feed, base_alias: str) -> str:
    """One field_map row's full expression: source -> rule -> sanitize -> default."""
    rule_obj = rules_mod.get_rule(fm.rule_name)
    col_sql = resolve_source_column_sql(fm, base_alias)

    if rules_mod.is_stateful(rule_obj):
        # row_sequence: ORDER BY the dataset's own primary key, not a generic
        # column reference - it has no single source column. Qualified
        # against base_alias unless the author already wrote a qualified
        # reference: an unqualified column name that also exists on a joined
        # calculator/lookup table raises "Ambiguous column name" otherwise
        # (found live, see query_builder.resolve_order_by_sql).
        order_by_raw = fm.rule_params.get("order_by", fm.source_column)
        order_by = resolve_order_by_sql(order_by_raw, base_alias)
        return rules_mod.render_sql(rule_obj, col_sql, {"order_by": order_by})

    rendered = rules_mod.render_sql(rule_obj, col_sql, fm.rule_params)

    if feed.collision_action == "sanitize":
        rendered = sanitize_expr_sql(rendered, feed.delimiter, feed.collision_char)
    # collision_action == 'fail': no sanitize wrapping here - deploy-time
    # probes (build_collision_probe) confirm the column is already clean.

    if fm.default_value is not None:
        escaped_default = fm.default_value.replace("'", "''")
        rendered = "ISNULL({}, '{}')".format(rendered, escaped_default)

    return rendered


def generate_view_ddl(
    dataset: Dataset,
    field_maps: Sequence[FieldMap],
    lookups: Sequence[DatasetLookup],
    feed: Feed,
    *,
    extra_key_columns: Optional[Sequence[str]] = None,
) -> str:
    """Build CREATE OR ALTER VIEW gen.v_<dataset> AS SELECT ... FROM ...

    No WHERE clause: the view is a static, deploy-once transform. Per-run
    window/dependency filtering happens in the client's read query (see
    ``build_view_read_query``), against the internal filter columns this view
    exposes alongside the real target columns.
    """
    base_alias = "t"
    ordered = sorted(field_maps, key=lambda f: f.ordinal)

    select_items = [
        "{} AS {}".format(_column_expression(fm, feed, base_alias), _bracket(fm.target_column))
        for fm in ordered
    ]

    if feed.emit_concat_ws_line:
        select_items = [render_concat_ws_line(ordered, feed, base_alias)]

    if dataset.dataset_mode == "primary" or (dataset.dataset_mode == "dependent" and dataset.include_own_window):
        if not dataset.window_date_column:
            raise ValueError(
                "Dataset {!r} needs a window filter but has no window_date_column.".format(
                    dataset.dataset_name
                )
            )
        select_items.append(
            "{} AS {}".format(
                resolve_source_column_sql_by_name(dataset.window_date_column, base_alias),
                _bracket(WINDOW_KEY_COLUMN),
            )
        )
    if dataset.dataset_mode == "dependent":
        if not dataset.dependency_target_column:
            raise ValueError(
                "Dataset {!r} is mode 'dependent' but has no dependency_target_column.".format(
                    dataset.dataset_name
                )
            )
        select_items.append(
            "{} AS {}".format(
                resolve_source_column_sql_by_name(dataset.dependency_target_column, base_alias),
                _bracket(DEPENDENCY_KEY_COLUMN),
            )
        )

    for raw_column in extra_key_columns or ():
        select_items.append(
            "{} AS {}".format(
                resolve_source_column_sql_by_name(raw_column, base_alias),
                _bracket(key_source_alias(raw_column)),
            )
        )

    join_clause = build_join_clause(lookups, base_alias)
    view_name = "gen.v_{}".format(dataset.dataset_name)
    sql = "CREATE OR ALTER VIEW {} AS SELECT {} FROM {} AS {} {}".format(
        view_name,
        ", ".join(select_items),
        dataset.qualified_table,
        _bracket(base_alias),
        join_clause,
    ).strip()
    return sql


def resolve_source_column_sql_by_name(column_name: str, base_alias: str) -> str:
    """A qualified reference to a bare source column (not through field_map)."""
    return "{}.{}".format(_bracket(base_alias), _bracket(column_name))


def render_concat_ws_line(field_maps: Sequence[FieldMap], feed: Feed, base_alias: str) -> str:
    """The 'third gear': one pre-joined line column via CONCAT_WS.

    Every argument is wrapped in ISNULL(..., '') first, unconditionally.
    CONCAT_WS skips a NULL argument entirely rather than emitting an empty
    position, which would silently shift every downstream field on any row
    with a null - a corrupt file that still passes a row-count check.
    Deliberately never optional: there is no code path here that omits the
    ISNULL wrapping, because a "sometimes wrapped" version is exactly the kind
    of thing that regresses quietly.
    """
    wrapped = [
        "ISNULL({}, '')".format(_column_expression(fm, feed, base_alias))
        for fm in sorted(field_maps, key=lambda f: f.ordinal)
    ]
    escaped_delim = feed.delimiter.replace("'", "''")
    return "CONCAT_WS('{}', {}) AS [Line]".format(escaped_delim, ", ".join(wrapped))


@dataclass(frozen=True)
class ViewReadPlan:
    sql: str
    params: Tuple[Any, ...]


def build_view_read_query(
    dataset: Dataset,
    field_maps: Sequence[FieldMap],
    feed: Feed,
    *,
    anchor_date=None,
    window_years=None,
    run_id=None,
    extra_key_columns: Optional[Sequence[str]] = None,
) -> ViewReadPlan:
    """The client-side query for path B: SELECT the real target columns FROM
    the deployed view, WHERE clause against its internal filter columns.

    Mirrors query_builder.build_select's mode semantics exactly, so path A
    and path B filter identically - the whole point of --compare-modes
    producing matching checksums. ``extra_key_columns`` (raw column names)
    additionally selects the view's internal __key_source_ columns, so a
    caller staging this (primary) dataset's keys for a dependent dataset
    elsewhere in the feed can actually read them back - the view computes
    them, but only if the client also asks for them.
    """
    view_name = "gen.v_{}".format(dataset.dataset_name)
    if feed.emit_concat_ws_line:
        target_columns = ["[Line]"]
    else:
        target_columns = [
            _bracket(fm.target_column) for fm in sorted(field_maps, key=lambda f: f.ordinal)
        ]
    for raw_column in extra_key_columns or ():
        target_columns.append(_bracket(key_source_alias(raw_column)))

    where = build_where_clause(
        dataset,
        base_alias="v",  # unused when the overrides below are supplied
        anchor_date=anchor_date,
        window_years=window_years,
        run_id=run_id,
        window_column_sql=_bracket(WINDOW_KEY_COLUMN),
        dependency_column_sql=_bracket(DEPENDENCY_KEY_COLUMN),
    )
    sql = "SELECT {} FROM {} WHERE {}".format(", ".join(target_columns), view_name, where.sql)
    return ViewReadPlan(sql=sql, params=where.params)


@dataclass(frozen=True)
class DeployResult:
    view_name: str
    ddl: str
    deployed: bool
    blocked_reason: Optional[str] = None


def deploy_views(
    conn,
    feed: Feed,
    datasets: Sequence[Dataset],
    field_maps_by_dataset: Dict[int, Sequence[FieldMap]],
    lookups_by_dataset: Dict[int, Sequence[DatasetLookup]],
    *,
    confirmed: bool = False,
) -> List[DeployResult]:
    """Deploy CREATE OR ALTER VIEW for every dataset - only with explicit confirmation.

    ``confirmed`` mirrors the CLI's --i-understand-this-writes-to-the-database
    flag: this function refuses to run any DDL at all without it, so a caller
    cannot accidentally deploy by forgetting to check for the flag itself.
    """
    from . import guardrails

    results: List[DeployResult] = []
    for dataset in datasets:
        ddl = generate_view_ddl(
            dataset,
            field_maps_by_dataset[dataset.dataset_id],
            lookups_by_dataset.get(dataset.dataset_id, []),
            feed,
            extra_key_columns=key_columns_needed_from(dataset.dataset_id, datasets),
        )
        view_name = "gen.v_{}".format(dataset.dataset_name)
        if not confirmed:
            results.append(DeployResult(view_name, ddl, deployed=False, blocked_reason="not confirmed"))
            continue
        verdict = guardrails.classify_write_statement(ddl)
        if not verdict.allowed:
            results.append(DeployResult(view_name, ddl, deployed=False, blocked_reason=verdict.reason))
            continue
        cursor = conn.cursor()
        try:
            cursor.execute(ddl)
        finally:
            cursor.close()
        results.append(DeployResult(view_name, ddl, deployed=True))
    return results


def explain(
    dataset: Dataset,
    field_maps: Sequence[FieldMap],
    lookups: Sequence[DatasetLookup],
    feed: Feed,
    *,
    all_datasets: Optional[Sequence[Dataset]] = None,
) -> str:
    """The generated DDL, for a DBA to review before granting buy-in on path B.

    No connection required at all - this is why 'what you show a DBA' works
    without touching the database. ``all_datasets``, when given, lets the
    output include the internal key-source columns a dependent dataset
    elsewhere in the feed needs - omit it and the DDL is still valid, just
    without those extra columns.
    """
    extra_key_columns = key_columns_needed_from(dataset.dataset_id, all_datasets) if all_datasets else None
    return generate_view_ddl(dataset, field_maps, lookups, feed, extra_key_columns=extra_key_columns)
