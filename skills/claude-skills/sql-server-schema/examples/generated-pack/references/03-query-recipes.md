# Query recipes

Generated from the join graph, not hand-written and not executed. Treat each as a starting point: check the grain before you trust a total.

## dbo.FactOrder

```sql
SELECT
    COUNT_BIG(*) AS RowCount,
    SUM(f.PaidAmount) AS PaidAmount,
    SUM(f.Quantity) AS Quantity
FROM dbo.FactOrder AS f
INNER JOIN dbo.DimCustomer AS j0 ON j0.CustomerId = f.CustomerId
INNER JOIN dbo.DimProduct AS j1 ON j1.ProductId = f.ProductId
WHERE f.IsDeleted = 0  -- soft delete convention detected
  AND f.OrderDate >= @FromDate
  AND f.OrderDate <  @ToDate   -- half-open range
;
```
