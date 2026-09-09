"""
rules.py - the transform rule catalog: two renderings of the same semantic.

Per docs/extract-engine-mvp-prompt-v2-polars.md: rules are NOT Python
callables applied per value. A Polars ``map_elements`` round-trips every value
through the interpreter and is slower than a plain cursor loop, which would
waste the entire benefit of Polars. Instead, each registered rule provides two
renderings:

* a Polars expression builder: ``(pl.Expr, params) -> pl.Expr``, vectorized
* a T-SQL fragment template: a string producing the same result server-side

Nothing here is ever ``eval``'d from metadata. ``meta.field_map.rule_name`` is
looked up in this module's registry by exact name; an unknown name is a
config-loader validation error, not a code path that executes.

The one rule that isn't a stateless per-batch expression is ``row_sequence``
(key generation) - see the docstring on that function for why it's a
documented special case rather than forced into this contract.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Optional

try:
    import polars as pl
except ImportError:  # pragma: no cover - polars is a hard dependency for path A,
    pl = None  # but rule *definitions* (and the SQL rendering) should still be
    # importable without it, e.g. for a machine that only runs path B.


@dataclass(frozen=True)
class Rule:
    name: str
    polars_fn: Optional[Callable[[Any, Dict[str, Any]], Any]]
    sql_template: str
    params_schema: Dict[str, type] = field(default_factory=dict)
    required_params: FrozenSet[str] = field(default_factory=frozenset)


_REGISTRY: Dict[str, Rule] = {}


def rule(name: str, sql: str, params_schema: Optional[Dict[str, type]] = None,
         required: Optional[tuple] = None):
    """Register a rule under ``name`` with both renderings."""

    def _decorator(fn):
        _REGISTRY[name] = Rule(
            name=name,
            polars_fn=fn,
            sql_template=sql,
            params_schema=params_schema or {},
            required_params=frozenset(required or ()),
        )
        return fn

    return _decorator


def registry() -> Dict[str, Rule]:
    """The full rule catalog, keyed by name."""
    return dict(_REGISTRY)


def get_rule(name: str) -> Rule:
    """Look up a rule by name, or raise a clear error - never eval anything."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            "No rule named {!r} is registered. Available: {}".format(
                name, sorted(_REGISTRY)
            )
        )


def validate_params(rule_obj: Rule, params: Dict[str, Any]) -> None:
    """Raise ValueError if params don't satisfy the rule's declared schema."""
    missing = rule_obj.required_params - set(params or {})
    if missing:
        raise ValueError(
            "Rule {!r} is missing required params: {}".format(rule_obj.name, sorted(missing))
        )
    for key, value in (params or {}).items():
        expected = rule_obj.params_schema.get(key)
        if expected is not None and not isinstance(value, expected):
            raise ValueError(
                "Rule {!r} param {!r} must be {}, got {}".format(
                    rule_obj.name, key, expected.__name__, type(value).__name__
                )
            )


def parse_rule_params(rule_params_json: Optional[str]) -> Dict[str, Any]:
    """Parse meta.field_map.rule_params (a JSON object) into a dict."""
    if not rule_params_json:
        return {}
    parsed = json.loads(rule_params_json)
    if not isinstance(parsed, dict):
        raise ValueError("rule_params must be a JSON object, got {}".format(type(parsed).__name__))
    return parsed


# ---------------------------------------------------------------------------
# T-SQL literal safety: rule_params flow into a generated CREATE OR ALTER VIEW
# statement's text (not a parameterized query - a view can't be parameterized
# the way a query can). This is not "eval", but string params still need
# escaping before they land in DDL text.
# ---------------------------------------------------------------------------

_UNSAFE_SQL_LITERAL = re.compile(r"[^\w\s\-.,:/]")


def sanitize_for_sql_literal(value: Any) -> Any:
    """Escape a rule param for safe substitution into a SQL string literal.

    Doubles embedded single quotes (the standard T-SQL escape). Numeric and
    boolean params pass through unchanged - only strings need escaping for a
    literal context.
    """
    if isinstance(value, str):
        return value.replace("'", "''")
    return value


def render_code_lookup_sql(col_sql: str, mapping: Dict[str, str], default: str) -> str:
    """Compose the CASE WHEN ... END for code_lookup.

    Not a simple .format() substitution like the other rules: the mapping
    dict's size and contents vary per field, so the SQL text is built, one
    WHEN per mapping entry. Sorted by key for deterministic output regardless
    of the mapping dict's construction order.
    """
    cases = " ".join(
        "WHEN {col} = '{key}' THEN '{value}'".format(
            col=col_sql,
            key=sanitize_for_sql_literal(key),
            value=sanitize_for_sql_literal(value),
        )
        for key, value in sorted(mapping.items())
    )
    default_sql = "'{}'".format(sanitize_for_sql_literal(default))
    return "CASE {} ELSE {} END".format(cases, default_sql)


def render_sql(rule_obj: Rule, col_sql: str, params: Dict[str, Any]) -> str:
    """Render a rule's T-SQL fragment, with every string param escaped first.

    ``code_lookup`` is composed specially (see ``render_code_lookup_sql``)
    rather than through the plain-template path every other rule uses.
    """
    if rule_obj.name == "code_lookup":
        return render_code_lookup_sql(col_sql, params["mapping"], params["default"])
    safe_params = {k: sanitize_for_sql_literal(v) for k, v in (params or {}).items()}
    return rule_obj.sql_template.format(col=col_sql, **safe_params)


# ---------------------------------------------------------------------------
# The nine rules from docs/extract-engine-mvp-prompt-v2-polars.md, both
# renderings, transcribed to match the doc's own examples.
# ---------------------------------------------------------------------------


@rule("passthrough", sql="{col}")
def passthrough(col, params):
    return col


@rule("trim", sql="LTRIM(RTRIM({col}))")
def trim(col, params):
    return col.cast(pl.Utf8).str.strip_chars()


@rule("upper", sql="UPPER({col})")
def upper(col, params):
    return col.cast(pl.Utf8).str.to_uppercase()


@rule(
    "pad_left",
    sql="RIGHT(REPLICATE('{char}', {width}) + CAST({col} AS VARCHAR({width})), {width})",
    params_schema={"width": int, "char": str},
    required=("width", "char"),
)
def pad_left(col, params):
    # T-SQL's CAST(col AS VARCHAR(width)) silently truncates an over-width
    # value to its LEFTMOST `width` characters before the REPLICATE+RIGHT pad
    # ever runs (confirmed empirically: CAST('123456' AS VARCHAR(5)) = '12345',
    # not an error and not a right-truncation). A naive pad_start alone leaves
    # an over-width value untouched, which would silently diverge from the SQL
    # path's output for any value wider than the configured pad width. Slicing
    # to `width` first mirrors the CAST's truncation exactly, so pad_start
    # becomes a no-op on an already-width-length string, matching either path.
    width = params["width"]
    return col.cast(pl.Utf8).str.slice(0, width).str.pad_start(width, params["char"])


@rule(
    "truncate",
    sql="LEFT({col}, {length})",
    params_schema={"length": int},
    required=("length",),
)
def truncate(col, params):
    return col.cast(pl.Utf8).str.slice(0, params["length"])


@rule(
    "date_format",
    # CONVERT preferred over FORMAT: FORMAT is CLR-backed and notably slow at
    # volume (per the doc). style 23 = ODBC canonical yyyy-mm-dd; a mask
    # requiring a different style must be added deliberately, not assumed.
    sql="CONVERT(VARCHAR(32), {col}, {style})",
    params_schema={"style": int, "polars_format": str},
    required=("style", "polars_format"),
)
def date_format(col, params):
    return col.dt.strftime(params["polars_format"])


@rule(
    "decimal_format",
    sql="FORMAT({col}, '{mask}')",
    params_schema={"scale": int, "mask": str},
    required=("scale", "mask"),
)
def decimal_format(col, params):
    # mode="half_away_from_zero" (T-SQL's "commercial rounding") is required,
    # not optional: Polars' default is half_to_even ("banker's rounding"),
    # which SQL Server's ROUND/FORMAT do not use. Confirmed empirically:
    # ROUND(12.345, 2) diverges between the two modes at exact tie values -
    # this is precisely the class of mismatch the rule-conformance suite
    # exists to catch, and it is why decimal_format's source column should be
    # read as pl.Decimal (via schema_overrides from meta.field_map.data_type)
    # rather than as a binary float, which cannot even represent "12.345"
    # exactly and would make ties non-reproducible across platforms.
    return col.round(params["scale"], mode="half_away_from_zero").cast(pl.Utf8)


@rule(
    "code_lookup",
    # Composed by query_builder from the params' mapping dict, not a fixed
    # template - see query_builder.render_code_lookup_sql.
    sql="CASE {cases} ELSE {default_sql} END",
    params_schema={"mapping": dict, "default": str},
    required=("mapping", "default"),
)
def code_lookup(col, params):
    mapping = params["mapping"]
    default = params.get("default")
    return col.replace_strict(mapping, default=default, return_dtype=pl.Utf8)


@rule(
    "default_if_null",
    sql="ISNULL({col}, '{value}')",
    params_schema={"value": str},
    required=("value",),
)
def default_if_null(col, params):
    return col.fill_null(params["value"])


# ---------------------------------------------------------------------------
# row_sequence - reconstructed extension, not one of the doc's nine rules.
# ---------------------------------------------------------------------------
#
# Key generation ("additional identity columns") needs a monotonically
# increasing counter across batches, which does not fit the pure
# (pl.Expr, params) -> pl.Expr per-batch shape every other rule uses: a batch
# has no idea what number the previous batch stopped at. Rather than force
# this into the registry's stateless contract (which would mean either a
# closure capturing mutable state - fragile under retries/reordering - or a
# fake "pl.Expr" that lies about being one), it is a documented special case
# implemented directly in each execution path:
#
#   execution_polars.py:  pl.int_range(running_offset, running_offset + len(batch)) + 1
#   execution_sql.py:     ROW_NUMBER() OVER (ORDER BY <dataset primary key>)
#
# It is still registered here as a *named* rule (so meta.field_map.rule_name
# = 'row_sequence' resolves and validates like any other, and the config
# loader can confirm it's spelled correctly) - its polars_fn is intentionally
# None, and callers must check for that before treating it as an ordinary
# stateless rule.
_REGISTRY["row_sequence"] = Rule(
    name="row_sequence",
    polars_fn=None,
    sql_template="ROW_NUMBER() OVER (ORDER BY {order_by})",
    params_schema={"order_by": str},
    required_params=frozenset({"order_by"}),
)


def is_stateful(rule_obj: Rule) -> bool:
    """True for rules (currently just row_sequence) that need special-case
    handling in the execution paths rather than a plain per-batch expression."""
    return rule_obj.polars_fn is None
