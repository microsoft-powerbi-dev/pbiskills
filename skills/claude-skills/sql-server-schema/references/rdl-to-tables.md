# From an RDL report to the database behind it

This is the reason the skill exists. Before it, `skills/report-lineage/` could
tell you an RDL dataset points at `Data Source=SQL-PROD-01;Initial
Catalog=OrdersDW` and which table names appear in its SQL, and nothing in the
repository could go any further. Now the chain completes:

```
SalesByRegion.rdl
  -> parse_connect_string(ConnectString)         -> (SQL-PROD-01, OrdersDW)
  -> nodes_from_rdl(..., proc_body_lookup=...)   -> {"ordersdw::dbo.claimheader", ...}
  -> the schema pack's 00-index.md locator       -> references/schema-dbo.md
  -> the per-table block                         -> columns, keys, joins, indexes
  -> 03-query-recipes.md                         -> a correct, filtered query
  -> the rdl-generation skill                    -> a new .rdl with correct <Fields>
```

That last step matters for a reason `rdl-generation/SKILL.md` already states:
you cannot emit a correct `<Fields>` block without knowing the column names and
types the query returns, and guessing is not allowed. A schema pack is the
artifact that satisfies that instruction offline.

## Step 1: the connection string

`reportlineage.connect_string.parse_connect_string` already handles both OLE DB
and ADO.NET forms and returns `(server, database)`:

```python
from reportlineage.connect_string import parse_connect_string

server, database = parse_connect_string(
    "Provider=SQLNCLI11.1;Data Source=SQL-PROD-01;Initial Catalog=OrdersDW;"
)
```

Two caveats:

- `database` is frequently `None`, because the RDL relies on the login's
  default database. Supply it yourself; the digest's database name is the
  tiebreaker.
- The function ignores authentication keys entirely, so it will not tell you
  whether a dataset uses Windows auth or a stored SQL login. If you need that,
  look for `Trusted_Connection` or `Integrated Security` in the raw string. A
  dataset using a stored SQL login is worth flagging during a migration; never
  surface the password value.

## Step 2: the tables

Three routes, all already implemented in `report-lineage`:

**Whole estate.** `python -m reportlineage scan <folder> --out ./reportlineage-out`
writes `estate.json`. Its nodes include `kind == "table"` entries; walking the
edges back to `ssrs_report` nodes gives a per-table report count, which is what
feeds the `report_reads` field in a digest.

**One report.** `reportlineage.builder.nodes_from_rdl(path)` returns
`(nodes, edges, diagnostics, report_id, read_table_ids)`. That fifth element is
the signature set for a single RDL.

**Table set only.** `reportlineage.fingerprint.fingerprint_rdl(path)` returns a
`ReportFingerprint` whose `tables` is a frozenset of signatures, when you do
not need the graph.

## Step 3: the join key

All of these speak `database::schema.table`, lower-cased, schema defaulting to
`dbo`. `analyze.table_signature` produces exactly the same string for every
table in a digest, so the two sides join directly with no mapping table.

```python
import analyze
from reportlineage.sql_refs import TableRef

TableRef(table="ClaimHeader", schema="dbo", database="OrdersDW").signature()
# 'ordersdw::dbo.claimheader'
analyze.table_signature("OrdersDW", "dbo", "ClaimHeader")
# 'ordersdw::dbo.claimheader'
```

`tests/test_analyze.py::test_signature_matches_reportlineage_when_it_is_importable`
imports the real `TableRef` and asserts the two agree, so this cannot drift
silently.

## The high-value wiring: resolving stored procedures

`reportlineage.builder` defines:

```python
ProcBodyLookup = Callable[[Optional[str], Optional[str], str], Optional[str]]
```

and `nodes_from_rdl`, `nodes_from_dtsx`, and `build_estate` all accept a
`proc_body_lookup=`. **Nothing in this repository has ever supplied one.** So
lineage stops at the `EXEC` frontier and emits an `ssrs_sql_unresolved`
diagnostic for every stored-procedure-backed dataset, which in a real SSRS
estate is most of them.

`mssql_get_definition` is exactly the missing piece. Wire it in:

```python
import sys
sys.path.insert(0, "skills/claude-skills/sql-server-schema/scripts")
from sqlserver_schema_mcp import mssql_get_definition
from reportlineage.builder import build_estate


def make_proc_body_lookup(default_database=None):
    """Adapt this server to reportlineage's ProcBodyLookup contract."""
    cache = {}

    def lookup(server, database, proc_name):
        key = (database or default_database, proc_name.strip().lower())
        if key not in cache:
            result = mssql_get_definition(proc_name.strip(), database=key[0] or "")
            cache[key] = result.get("definition") if result.get("ok") else None
        return cache[key]

    return lookup


graph = build_estate(
    rdl_paths=[...],
    proc_body_lookup=make_proc_body_lookup(default_database="OrdersDW"),
)
```

With that in place, lineage traces *through* procedures into the base tables
they read, the `ssrs_sql_unresolved` diagnostic count collapses, and the
`report_reads` counts in a digest become accurate rather than a floor.

The lookup caches, because an estate will ask for the same procedure many
times, and it returns `None` rather than raising when a procedure is missing or
the login lacks `VIEW DEFINITION`, which is what the contract expects.

## Feeding usage counts back into a digest

`analyze.build_digest(..., report_reads={...})` takes a mapping of
lower-cased `schema.table` to a report count. It does two things:

1. Adds the strongest term in the importance score (weight 5.0, the largest of
   any signal).
2. Forces any table read by at least one report to tier 2 or better, so
   something the estate actually depends on can never be buried in tier 3 by a
   heuristic that guessed wrong.

The generated pack then shows `Read by N report(s)` on each table block, which
turns the schema pack into a migration scoping document: the tables no report
reads are candidates for exclusion, and the tables reports read that the digest
does not contain are cross-database references or a stale scan, either of which
is a finding worth chasing.
