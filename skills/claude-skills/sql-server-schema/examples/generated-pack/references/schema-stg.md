# Schema `stg`

### `stg.stg_OrderImport`  (staging, tier 2, 0 rows)

Classified staging: name or schema marks this as staging or a backup copy.

Primary key: none declared

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `OrderId` | int | no | FK to `dbo.FactOrder.OrderId (inferred)` |
| `Payload` | nvarchar | no |  |

Joins out: `dbo.FactOrder`
Joined from: none
