# What the generator emits

A **schema skill pack** is a per-database, generated skill: a `SKILL.md` an
agent can load, plus reference files it reads on demand. It is generated from a
digest by `skillgen.generate_skill_pack`, deterministically, with no model call
and no network.

A complete worked example, built from a fictional six-table schema, is
committed at `examples/generated-pack/`.

## Layout

```
<out_dir>/
  .gitignore                    body is "*", so the pack ignores itself
  SKILL.md                      the entry point, deliberately small
  REDACTIONS.md                 what was flagged sensitive, always written
  schema_digest.json            the digest this pack was built from
  references/
    00-index.md                 the locator: find any table in one hop
    01-join-graph.md            edges, join clauses, and a Mermaid diagram
    02-conventions.md           naming, keys, soft deletes, SCD columns
    03-query-recipes.md         a starting query per tier 1 fact table
    schema-<name>.md            per-schema table catalog, chunked
    schema-<name>-2.md          ... continuing, when a schema is large
```

The committed example omits the pack's own `.gitignore`, because its `*` would
hide the example itself. Real packs always get it.

## The context-budget contract

This is the design constraint the whole layout serves: **a 3000-table database
has to stay usable inside an agent's context window.**

| File | Ceiling | What happens when it is exceeded |
| --- | --- | --- |
| `SKILL.md` | ~8 KB | Fixed. Only the top-ten table is variable-length |
| `references/00-index.md` | ~25 KB | Tier 3 collapses to a comma-joined name list per schema |
| `references/schema-<x>.md` | 40 tables per file by default | Split on a table boundary into `-2`, `-3`, each listed in the index |
| `references/01-join-graph.md` | tier 1 and 2 only | Tier 3 edges are omitted; the Mermaid diagram caps at 120 edges |
| Per-table column list | 60 columns | The remainder collapses to "N further columns, see `schema_digest.json`" |

`SKILL.md` **never inlines the table catalog.** It names the ten tables that
matter and points at `00-index.md` for everything else. That is what keeps the
entry point loadable no matter how large the database is.

`00-index.md` ends with an alphabetical locator mapping every table to the file
that describes it, so a `Grep` for a table name lands in one hop.

## `SKILL.md` frontmatter

```yaml
---
name: "Schema: SalesDW"
description: >
  Use this skill when writing T-SQL, designing an SSRS dataset, or planning a
  Power BI model against the SalesDW database. Covers 6 tables across 2
  schemas ... Read references/00-index.md first.
allowed-tools:
  - Read
  - Glob
  - Grep
triggers:
  - salesdw
  - dbo
  - factorder
  - dimcustomer
metadata:
  generated_by: skills/claude-skills/sql-server-schema/scripts/skillgen.py
  generator_version: "1.0"
  digest_schema_version: "1.0"
  source_database: SalesDW
  source_server_alias: demo-sql
  table_count: 6
  samples_included: false
  contains_internal_identifiers: true
  do_not_commit: true
---
```

Two mechanical points the generator has to honour:

- **`name` must be quoted.** `Schema: SalesDW` unquoted parses as a YAML
  mapping, not a string. `tests/test_skillgen.py` asserts the frontmatter
  parses and that `name` comes back as a string.
- **`allowed-tools` is read-only**: `Read`, `Glob`, `Grep`. No `Write`, no
  `Edit`, no `Bash`. A schema pack is reference material and should not be able
  to modify anything.

`triggers` are the tier-1 table names, the schema names, and the database name,
lower-cased, capped at 40.

Immediately under the H1 comes a do-not-commit banner, so the warning is
visible to anyone who opens the file rather than buried in metadata.

## Per-table block

```markdown
### `dbo.FactOrder`  (fact, tier 1, 4.00M rows)

Classified fact: name starts with an explicit fact prefix.

Primary key: `OrderId`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `OrderId` | int | no | PK; identity |
| `CustomerId` | int | no | FK to `dbo.DimCustomer.CustomerId` |
| `MemberSSN` | char(11) | yes | **withheld: sensitive (ssn)** |

Joins out: `dbo.DimCustomer`, `dbo.DimProduct`
Joined from: `stg.stg_OrderImport`
Indexes: IX_FactOrder_Customer on (CustomerId)
Read by 34 report(s).
```

The Notes cell carries, in order: PK, identity, foreign key target (marked
`(inferred)` when it is a name match rather than a declared constraint),
computed, default, sensitivity flag, and the `MS_Description` if the database
has one.

`Read by N report(s)` only appears when a lineage scan supplied usage counts,
see `rdl-to-tables.md`.

## Query recipes

One per tier-1 fact table, built from the join graph rather than written by
hand:

```sql
SELECT
    COUNT_BIG(*) AS RowCount,
    SUM(f.PaidAmount) AS PaidAmount
FROM dbo.FactOrder AS f
INNER JOIN dbo.DimCustomer AS j0 ON j0.CustomerId = f.CustomerId
INNER JOIN dbo.DimProduct AS j1 ON j1.ProductId = f.ProductId
WHERE f.IsDeleted = 0  -- soft delete convention detected
  AND f.OrderDate >= @FromDate
  AND f.OrderDate <  @ToDate   -- half-open range
;
```

The rules the generator follows:

- Join paths come from the graph, declared edges preferred; an inferred join
  is annotated `-- inferred, verify`.
- The soft-delete predicate is injected automatically when the conventions pass
  detected one. This is the single most common cause of a silently wrong
  report, so it is not left to the reader to remember.
- Date filters are parameterised and half-open (`>= @From`, `< @To`), which
  avoids the boundary bug an inclusive `BETWEEN` on a datetime column creates.
- `SELECT *` never appears: the `ssrs-report-creation` skill lists it as an
  anti-pattern and the generator honours that.

These are starting points, not verified queries. Nothing is executed during
generation.

## Regenerating

```bash
python scripts/cli.py generate <digest.json> --dry-run
```

`--dry-run` renders and measures everything and writes nothing, which is the
right first move. Then drop the flag.

Because generation is deterministic, re-running against a fresh digest and
diffing the pack shows exactly what changed in the database. Do not hand-edit a
pack: the next regeneration overwrites it, and a hand edit is invisible in that
diff.
