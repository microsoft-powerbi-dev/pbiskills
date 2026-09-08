# Redaction report

Policy: no sample data. Digest schema version 1.0.

## Flagged: 1 columns across 1 tables

| Table | Column | Category | Tier |
| --- | --- | --- | --- |
| `dbo.DimCustomer` | `City` | address | high |

Column *names* are shown deliberately. An agent that does not know a sensitive column exists will write `SELECT *` and pull it; naming the column and marking it do-not-select is the safer failure mode. No values from these columns were read or written.

Categories: address 1

## Not withheld, review before sharing

- The server alias `demo-sql` and database name `SalesDW` appear throughout this pack.
- 6 internal table names and 21 column names are in the clear.
- This pack is not safe for a public repository.
