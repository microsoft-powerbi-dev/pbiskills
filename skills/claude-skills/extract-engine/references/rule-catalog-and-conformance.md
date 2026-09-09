# The rule catalog and its conformance suite

Rules are **not** Python callables applied per value. Routing every value
through `map_elements`/`apply` round-trips it through the interpreter and is
slower than a plain cursor loop — it would waste the entire benefit of
Polars. Instead every registered rule (`rules.py`) provides two renderings of
the same semantic:

- a **Polars expression builder**: `(pl.Expr, params: dict) -> pl.Expr`,
  vectorized, no Python loop over rows.
- a **T-SQL fragment template**: a string producing the identical result
  server-side.

`meta.field_map.rule_name` is looked up in `rules._REGISTRY` by exact string
match (`rules.get_rule`). An unrecognized name is a `config_loader`
validation failure, never a code path that executes — nothing here is ever
`eval`'d from metadata.

## The ten registered rules

| Rule | Params (required in **bold**) | Polars | T-SQL |
| --- | --- | --- | --- |
| `passthrough` | none | `col` | `{col}` |
| `trim` | none | `str.strip_chars()` | `LTRIM(RTRIM({col}))` |
| `upper` | none | `str.to_uppercase()` | `UPPER({col})` |
| `pad_left` | **width**: int, **char**: str | `str.slice(0, width).str.pad_start(width, char)` | `RIGHT(REPLICATE('{char}', {width}) + CAST({col} AS VARCHAR({width})), {width})` |
| `truncate` | **length**: int | `str.slice(0, length)` | `LEFT({col}, {length})` |
| `date_format` | **style**: int, **polars_format**: str | `dt.strftime(polars_format)` | `CONVERT(VARCHAR(32), {col}, {style})` |
| `decimal_format` | **scale**: int, **mask**: str | `round(scale, mode="half_away_from_zero").cast(Utf8)` | `FORMAT({col}, '{mask}')` |
| `code_lookup` | **mapping**: dict, **default**: str | `replace_strict(mapping, default=default)` | `CASE WHEN ... ELSE '{default}' END`, composed per-mapping (see below) |
| `default_if_null` | **value**: str | `fill_null(value)` | `ISNULL({col}, '{value}')` |
| `row_sequence` | **order_by**: str | none — see "the stateful exception" | `ROW_NUMBER() OVER (ORDER BY {order_by})` |

All ten (not just the doc's original nine) are validated by
`config_loader.validate` and exercised by `tests/test_rules.py`.

## `render_sql`: escaping, not evaluation

`rule_params` values land inside `CREATE OR ALTER VIEW` DDL text — a view
cannot be parameterized the way a query can, so every string param is passed
through `sanitize_for_sql_literal` (doubles embedded single quotes) before
substitution. This is escaping for a literal context, not `eval`; numeric and
boolean params pass through unchanged.

`code_lookup` is composed specially rather than through the plain
`.format()` path every other rule uses:
`render_code_lookup_sql` builds one `WHEN {col} = '{key}' THEN '{value}'` per
mapping entry, **sorted by key** for deterministic output regardless of the
mapping dict's construction order — load-bearing for `--compare-modes`-style
checksum equality and for the conformance suite's own assertions.

## The stateful exception: `row_sequence`

Key generation needs a monotonically increasing counter across batches,
which does not fit the stateless `(pl.Expr, params) -> pl.Expr` contract: a
batch has no idea what number the previous batch stopped at. Rather than
force this into a closure over mutable state, `row_sequence` is a documented
special case with `polars_fn = None`:

- **Polars path**: `execution_polars.add_row_sequence_columns` tracks a
  per-target running offset in a dict the caller reuses across batches within
  one dataset (`pl.int_range(start, start + n) + ...`).
- **SQL path**: `execution_sql._column_expression` renders
  `ROW_NUMBER() OVER (ORDER BY {order_by})` directly into the view.

`rules.is_stateful(rule_obj)` is `True` only for `row_sequence` — check it
before treating a `Rule`'s `polars_fn` as callable. Both `build_select`
(Polars path) and `generate_view_ddl` (SQL path) skip stateful fields when
building their column list and instead append an explicit `ORDER BY` derived
from the rule's `order_by` param — **without it, Polars' batch-arrival
numbering and SQL's `ROW_NUMBER()` have no guaranteed relationship**, which
would assign different sequence numbers to the same logical row between the
two paths. `query_builder.resolve_order_by_sql` qualifies a bare `order_by`
column name against the dataset's base table alias automatically (an
unqualified name that also exists on a joined lookup table raises "Ambiguous
column name" otherwise — found live).

## Two bugs the conformance suite actually caught during development

Both are recorded in code comments, not just here — grep `rules.py` and
`execution_sql.py` for "found empirically"/"found live" for the exact
reasoning.

1. **Rounding mode.** Polars' default `round()` is `half_to_even` ("banker's
   rounding"); T-SQL's `ROUND`/`FORMAT` use `half_away_from_zero`
   ("commercial rounding"). `decimal_format`'s Polars rendering now passes
   `mode="half_away_from_zero"` explicitly. Confirmed to diverge at exact tie
   values (e.g. `12.325`); the conformance test parametrizes on tie values
   specifically to catch a regression here.
2. **`pad_left` truncation order.** `CAST(col AS VARCHAR(width))` in T-SQL
   silently truncates an over-width value to its **leftmost** `width`
   characters before the pad ever runs — not an error, not a right-truncate.
   `pad_left`'s Polars rendering slices to `width` *before* padding
   (`str.slice(0, width).str.pad_start(...)`) specifically to mirror this;
   without the slice, an over-width value would diverge silently between the
   two paths for any value wider than the configured pad width.

## Running the conformance suite

`tests/test_rules_conformance.py` is the one test module that legitimately
needs a live SQL Server: T-SQL semantics (rounding, date formatting, NULL
propagation) cannot be faithfully reimplemented in Python without the test
proving nothing but its own reimplementation. Gated behind
`EXTRACT_ENGINE_LOCALDB=1`:

```bash
EXTRACT_ENGINE_LOCALDB=1 python -m pytest \
    skills/claude-skills/extract-engine/tests/test_rules_conformance.py -q
```

It renders each rule's SQL template against a literal via
`SELECT {rendered} AS v`, evaluates the same rule's Polars function against a
single-row `DataFrame`, and asserts the two stringified outputs are equal —
across `None`, empty string, whitespace-only, ASCII, non-ASCII (`café`,
`日本語`), and an embedded single quote (`O'Brien`) for the string rules;
across `12.345`/`12.325`/`-1.005`/`999999.995` (half-way tie values) for
`decimal_format`, compared numerically via `decimal.Decimal` rather than
stringwise since T-SQL's `ROUND` and Polars' `round()` keep a different
number of trailing zeros for the same correctly-rounded value; and across
`1900-01-01`/`2099-12-31`/`None` for `date_format`.

**If a rule cannot pass conformance, it does not go in the catalog** — that
is the design's own quality gate, not a suggestion.
