# SalesDW: index

6 tables across 2 schemas. Detail lives in the per-schema files below. Find the table here first, then open only the file it points to.

## Where to look

| Schema | Tables | Tier 1 | File(s) |
| --- | --- | --- | --- |
| `dbo` | 5 | 5 | `schema-dbo.md` |
| `stg` | 1 | 0 | `schema-stg.md` |

## Tier 1: the tables you will actually query

| Table | Role | Rows | In file |
| --- | --- | --- | --- |
| `dbo.FactOrder` | fact | 4.00M | `references/schema-dbo.md` |
| `dbo.DimCustomer` | dimension | 52.0K | `references/schema-dbo.md` |
| `dbo.DimProduct` | dimension | 1.4K | `references/schema-dbo.md` |
| `dbo.OrderAudit` | audit | 900.0K | `references/schema-dbo.md` |
| `dbo.StatusLookup` | lookup | 8 | `references/schema-dbo.md` |

## Tier 2: supporting tables

| Table | Role | Rows | In file |
| --- | --- | --- | --- |
| `stg.stg_OrderImport` | staging | 0 | `references/schema-stg.md` |

## Alphabetical locator

`dbo.DimCustomer` -> `references/schema-dbo.md` · `dbo.DimProduct` -> `references/schema-dbo.md` · `dbo.FactOrder` -> `references/schema-dbo.md` · `dbo.OrderAudit` -> `references/schema-dbo.md` · `dbo.StatusLookup` -> `references/schema-dbo.md` · `stg.stg_OrderImport` -> `references/schema-stg.md`
