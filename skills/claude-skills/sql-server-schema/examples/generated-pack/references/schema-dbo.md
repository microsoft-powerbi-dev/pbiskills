# Schema `dbo`

### `dbo.DimCustomer`  (dimension, tier 1, 52.0K rows)

Classified dimension: name starts with an explicit dim prefix.

Primary key: `CustomerId`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `CustomerId` | int | no | PK; identity |
| `CustomerName` | varchar | no |  |
| `City` | varchar | no | **sensitive (address)** |
| `IsCurrent` | bit | no |  |
| `EffectiveFrom` | date | no |  |

Joins out: none
Joined from: `dbo.FactOrder`

### `dbo.DimProduct`  (dimension, tier 1, 1.4K rows)

Classified dimension: name starts with an explicit dim prefix.

Primary key: `ProductId`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `ProductId` | int | no | PK; identity |
| `ProductName` | varchar | no |  |
| `CategoryCode` | varchar | no |  |

Joins out: none
Joined from: `dbo.FactOrder`

### `dbo.FactOrder`  (fact, tier 1, 4.00M rows)

Classified fact: name starts with an explicit fact prefix.

Primary key: `OrderId`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `OrderId` | int | no | PK; identity |
| `CustomerId` | int | no | FK to `dbo.DimCustomer.CustomerId` |
| `ProductId` | int | no | FK to `dbo.DimProduct.ProductId` |
| `OrderDate` | date | no |  |
| `PaidAmount` | decimal | no |  |
| `Quantity` | int | no |  |
| `IsDeleted` | bit | no |  |

Joins out: `dbo.DimCustomer`, `dbo.DimProduct`
Joined from: `stg.stg_OrderImport`

### `dbo.OrderAudit`  (audit, tier 1, 900.0K rows)

Classified audit: audit or history naming with nothing referencing it.

Primary key: `AuditId`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `AuditId` | int | no | PK; identity |
| `ChangedOn` | datetime | no |  |

Joins out: none
Joined from: none

### `dbo.StatusLookup`  (lookup, tier 1, 8 rows)

Classified lookup: small, narrow, and named as a lookup or reference table.

Primary key: `StatusCode`

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `StatusCode` | varchar | no | PK |
| `Description` | varchar | no |  |

Joins out: none
Joined from: none
