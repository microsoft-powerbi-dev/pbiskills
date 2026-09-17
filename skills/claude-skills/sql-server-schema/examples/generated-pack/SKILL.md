---
name: "Schema: SalesDW"
description: >
  Use this skill when writing T-SQL, designing an SSRS dataset, or planning
  a Power BI model against the SalesDW database. Covers 6 tables across
  2 schemas, 1 classified fact tables and 2 dimensions, the declared
  and inferred foreign-key join graph, and the naming conventions this
  database follows. Read references/00-index.md first: it names which
  per-schema file answers which question. Example requests: "what joins
  X to Y", "which table holds the amounts", "write a dataset query for
  this database".
allowed-tools:
  - Read
  - Glob
  - Grep
triggers:
  - dbo
  - dimcustomer
  - dimproduct
  - factorder
  - orderaudit
  - salesdw
  - statuslookup
  - stg
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

# Schema: SalesDW

> **Generated artifact, do not commit.** This pack describes an internal
> database: it names real schemas, tables, and columns. It is produced by
> `skills/claude-skills/sql-server-schema/scripts/skillgen.py` and lives
> outside version control by default. Regenerate it rather than editing it by
> hand, and never copy it into a public repository. `REDACTIONS.md` lists what
> was withheld.

## What this database is

6 tables across 2 schemas (`dbo`, `stg`). 2 declared foreign keys and 1 inferred relationships.

Role mix: 1 audit, 2 dimension, 1 fact, 1 lookup, 1 staging.

## Read this in order

1. `references/00-index.md`, to locate the table you need.
2. The per-schema file that index points you at.
3. `references/01-join-graph.md` when you need to join across tables.
4. `references/02-conventions.md` before writing any WHERE clause.

## The tables that matter most

| Table | Role | Rows | Joins to | Read by reports |
| --- | --- | --- | --- | --- |
| `dbo.DimCustomer` | dimension | 52.0K | none | 0 |
| `dbo.DimProduct` | dimension | 1.4K | none | 0 |
| `dbo.FactOrder` | fact | 4.00M | `dbo.DimCustomer`, `dbo.DimProduct` | 0 |
| `dbo.OrderAudit` | audit | 900.0K | none | 0 |
| `dbo.StatusLookup` | lookup | 8 | none | 0 |

## Conventions this database follows

- Table names are PascalCase (83% of tables).
- Primary keys are named `other` (83%).
- 1 tables (17%) have no primary key at all. Do not assume a unique row identifier exists.
- Soft-delete columns are present (1 found, for example `dbo.FactOrder.IsDeleted`). A query that omits the soft-delete filter is silently wrong.
- Type-2 slowly-changing dimension columns are present (for example `dbo.DimCustomer.EffectiveFrom`). A naive join to these dimensions fans out; filter to the current row.
- String columns are predominantly `nvarchar`.

## Bundled assets

| File | Covers |
| --- | --- |
| `REDACTIONS.md` | Which columns were flagged sensitive, and what was withheld |
| `references/00-index.md` | Locate any table, and which file describes it |
| `references/01-join-graph.md` | Declared and inferred joins, with clauses |
| `references/02-conventions.md` | Naming, keys, soft deletes, and SCD columns |
| `references/03-query-recipes.md` | A starting query per tier 1 fact table |
| `references/schema-dbo.md` | Table catalog for schema `dbo` |
| `references/schema-stg.md` | Table catalog for schema `stg` |
| `schema_digest.json` | The machine-readable digest this pack was built from |

## What this pack does not tell you

- No data values. No sample rows were collected.
- No row-level security, permissions, or query plans.
- Nothing about data quality: a column existing does not mean it is populated.
- Roles and tiers are name, key, and cardinality heuristics, not semantics. Each table's `role_reasons` in `schema_digest.json` says which rule fired.
- The schema as of generation. Regenerate if the database has changed.

## Related material

- `skills/claude-skills/sql-server-schema/SKILL.md` in the pbiskills repository: how this pack was produced and how to query the database live.
- `skills/report-lineage/`: which reports read these tables.
- `skills/claude-skills/rdl-generation/`: turning one of these queries into an SSRS report definition.
