# Conventions

Detected by counting patterns across the whole database. Each statement carries its hit rate: treat a rule at 70% as a strong hint, not a law.

- Table names are PascalCase (83% of tables).
- Primary keys are named `other` (83%).
- 1 tables (17%) have no primary key at all. Do not assume a unique row identifier exists.
- Soft-delete columns are present (1 found, for example `dbo.FactOrder.IsDeleted`). A query that omits the soft-delete filter is silently wrong.
- Type-2 slowly-changing dimension columns are present (for example `dbo.DimCustomer.EffectiveFrom`). A naive join to these dimensions fans out; filter to the current row.
- String columns are predominantly `nvarchar`.

## Soft-delete columns

Every query against these tables needs the soft-delete predicate, or it silently returns retired rows.

- `dbo.FactOrder.IsDeleted`

## Slowly-changing dimension columns

These dimensions keep history. Joining without a currency filter multiplies fact rows by the number of versions.

- `dbo.DimCustomer.EffectiveFrom`
- `dbo.DimCustomer.IsCurrent`
