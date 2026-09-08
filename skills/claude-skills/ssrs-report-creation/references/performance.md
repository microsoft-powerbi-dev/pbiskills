# Performance

Tune in the order that pays. Almost all paginated-report slowness is query
slowness, and almost all of the rest is returning too many rows. Layout tuning
matters only after those two are fixed.

```
1. Query      typically 60-80% of the win
2. Dataset    shape and volume
3. Report     expressions, regions, interactivity
4. Server     caching, snapshots, memory, scale-out
```

Measure before you tune. The `ExecutionLog3` query at the end of this document
tells you which of the three execution phases dominates, and each phase points at
a different layer.

---

## 1. Query

### Index the predicates you actually use

Every dataset query should have a supporting index on its filter and join
columns. For the monthly-sales example:

```sql
CREATE NONCLUSTERED INDEX IX_Sale_InvoiceDateKey_CityKey
    ON DW.Fact.Sale ([Invoice Date Key], [City Key])
    INCLUDE ([Total Excluding Tax], [Total Including Tax], Profit, [WWI Invoice ID]);
```

The `INCLUDE` list is what makes the index covering: the query never touches the
base table. Check with:

```sql
SET STATISTICS IO ON;
EXEC Reporting.usp_MonthlySalesByRegion
     @DateFrom = '2026-07-01', @DateTo = '2026-07-31',
     @RegionList = 'Southeast,Great Lakes', @UserId = 'CONTOSO\alice';
```

A logical-read count in the millions for a 12-row result means no useful index.

### Keep predicates sargable

A predicate is sargable when the optimiser can seek on it. Wrapping the column in
a function destroys that.

| Not sargable | Sargable rewrite |
| --- | --- |
| `WHERE YEAR(d.Date) = 2026` | `WHERE d.Date >= '2026-01-01' AND d.Date < '2027-01-01'` |
| `WHERE CONVERT(date, s.CreatedAt) = @D` | `WHERE s.CreatedAt >= @D AND s.CreatedAt < DATEADD(day,1,@D)` |
| `WHERE ISNULL(c.Region,'') = @R` | `WHERE c.Region = @R` plus handle NULL explicitly |
| `WHERE LEFT(p.Code, 3) = 'ABC'` | `WHERE p.Code LIKE 'ABC%'` |
| `WHERE CAST(s.OrderId AS varchar) = @Id` | `WHERE s.OrderId = CAST(@Id AS int)` |

Also watch implicit conversion: comparing an `nvarchar` parameter to a `varchar`
column converts the **column**, killing the seek. Match parameter types to column
types exactly.

### Do not `SELECT *`

Detected by this repository as `select_star`:

> Query uses SELECT * - projects unused columns and blocks query optimisation.
> List only the columns the report actually consumes.

Three concrete costs: wider rows across the network, a covering index that no
longer covers, and a report that breaks silently when a column is added or
renamed upstream.

### Avoid leading wildcards

Detected as `leading_wildcard`:

> LIKE '%...' forces a table scan; consider a full-text index or a
> trailing-wildcard match.

`LIKE '%widget%'` cannot use a B-tree index. Options, in order of preference:

1. Change the requirement to "starts with" and use `LIKE 'widget%'`.
2. Add a full-text index and use `CONTAINS(p.Name, 'widget')`.
3. Add a computed, persisted, reversed column and index it, if the requirement is
   genuinely "ends with".

### Understand parameter sniffing

SQL Server compiles a plan for the parameter values present on first execution
and reuses it. A report whose date range varies from one day to three years gets
one plan that is right for one of those cases.

Symptoms: the report is fast for one user and slow for another, with identical
SQL; or it is fast until a statistics update, then permanently slow.

Fixes, in order of preference:

```sql
-- 1. Recompile per execution. Costs a compile, buys a correct plan.
--    Right when the compile cost is small relative to execution.
... OPTION (RECOMPILE);

-- 2. Optimise for a representative value.
... OPTION (OPTIMIZE FOR (@DateFrom = '2026-07-01'));

-- 3. Optimise for unknown: use the density vector instead of the histogram.
... OPTION (OPTIMIZE FOR UNKNOWN);

-- 4. Local variable copies (same effect as OPTIMIZE FOR UNKNOWN, less obvious).
DECLARE @DF date = @DateFrom;   -- then filter on @DF
```

For report procs, `OPTION (RECOMPILE)` is usually correct: a report runs a few
hundred times a day, not a few hundred times a second, and plan quality matters
far more than compile cost.

### No cursors in a report query

Detected as `cursor_query`:

> T-SQL cursor detected - rewrite as a set-based query for Power BI paginated
> performance.

A cursor in a report proc is almost always a running total, a gap-fill, or a
row-by-row lookup, and all three have set-based forms:

| Cursor was doing | Set-based replacement |
| --- | --- |
| Running total | `SUM(x) OVER (PARTITION BY ... ORDER BY ... ROWS UNBOUNDED PRECEDING)` |
| Previous / next row | `LAG(x) OVER (...)` / `LEAD(x) OVER (...)` |
| Ranking, top per group | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` |
| Gap fill | `CROSS JOIN` against a calendar dimension plus `LEFT JOIN` |
| Row-by-row lookup | A join |
| Building a delimited string | `STRING_AGG(x, ', ') WITHIN GROUP (ORDER BY x)` |

### Confirm `DISTINCT` is not hiding a join defect

Detected as `select_distinct`:

> SELECT DISTINCT masks duplicates that may indicate a join defect; verify the
> join keys before shipping.

Remove the `DISTINCT`, count the rows, and confirm the number matches the grain
you documented in stage 2 of the authoring workflow. If it does not, the join is
wrong and the `DISTINCT` was hiding an over-count that will resurface the moment
someone adds a `SUM`.

### Confirm every `CROSS JOIN` is intentional

Detected as `cross_join`:

> CROSS JOIN produces a Cartesian product; confirm the row multiplication is
> intentional.

Intentional cases exist (gap-filling a matrix, generating a number series).
Unintentional ones come from a missing join predicate in old-style comma-joined
`FROM` clauses. Rewrite every query in ANSI `JOIN ... ON` syntax so a missing
predicate is a syntax error rather than a Cartesian product.

---

## 2. Dataset

### Filter server-side, always

A Tablix or dataset filter runs after every row has crossed the network and been
buffered. Eight million rows filtered to four hundred means eight million rows
were transferred, parsed and held in memory.

Move it to `WHERE`. See `data-sources-and-datasets.md` for the three narrow cases
where a report filter is legitimate.

### Return only the columns the report renders

Each unused column is bytes across the wire and bytes in the report server's
intermediate format. On a 500,000-row dataset an unused `nvarchar(500)` column
costs real memory.

Audit it: list every `Fields!X.Value` in the RDL and compare against the
`<Fields>` collection. Anything declared and unreferenced should be dropped from
the `SELECT`.

### Aggregate in SQL, not in the report

If the report shows twelve rows, return twelve rows.

```sql
-- Report shows region x month. Return region x month.
SELECT c.[Sales Territory], DATEFROMPARTS(YEAR(d.Date), MONTH(d.Date), 1), SUM(...)
GROUP BY c.[Sales Territory], DATEFROMPARTS(YEAR(d.Date), MONTH(d.Date), 1);
```

Returning 4,000,000 invoice lines and letting `=Sum(Fields!Amount.Value)` in a
group footer do the work is the single most common cause of a report that takes
four minutes to render twelve rows.

The exception is a drilldown report where the detail must be available on
expansion. Even then, prefer a drillthrough to a detail report so the detail
query runs only when someone asks for it.

### One dataset per grain, not one per region

Two Tablix regions over the same grain should share one dataset. Two datasets
mean two round trips and two chances to drift apart.

### Cache the parameter value lists

An available-values dataset runs on every report open. `SELECT DISTINCT Region
FROM DW.Dimension.City` against a large dimension, on every open, by every user,
is pure waste. Publish it as a shared dataset with caching enabled. See
`data-sources-and-datasets.md`.

---

## 3. Report

### Avoid report-level filters

Covered above. Worth repeating because it is the highest-impact report-layer
mistake.

### Avoid repeated `Lookup` calls

`Lookup(sourceExpr, destExpr, resultExpr, "dsOther")` builds a hash of the target
dataset on first use, which is fine. The problem is calling it several times per
row for several fields:

```
=Lookup(Fields!ProductId.Value, Fields!Id.Value, Fields!Name.Value,     "dsProduct")
=Lookup(Fields!ProductId.Value, Fields!Id.Value, Fields!Category.Value, "dsProduct")
=Lookup(Fields!ProductId.Value, Fields!Id.Value, Fields!Brand.Value,    "dsProduct")
```

Three lookups per row, 200,000 rows, 600,000 hash probes plus expression
evaluations. Join in SQL instead. If the second dataset genuinely cannot be
joined (different source system), do the join in the ETL layer, not in the
report.

`LookupSet` and `MultiLookup` are worse: they return collections, and the
`Join(LookupSet(...), ", ")` idiom allocates per row.

### Limit the number of data regions

This repository emits `many_tablix` above five Tablix regions and `many_chart`
above five charts:

> N Tablix report items; consider splitting into multiple reports for
> maintainability.

Each region is a separate processing and rendering pass. A report with nine
Tablix regions renders nine times. If the requirement really is nine regions, it
is a dashboard, and a Power BI interactive report will serve it better. See
`migration-to-fabric.md`.

### Avoid nested `IIf`

Detected as `nested_iif` at three or more levels:

> Three-or-more nested IIF(...) - rewrite as SWITCH() for readability and perf.

`IIf` is a function, not a language construct: **both** branches are evaluated
every time, so nesting three deep evaluates up to seven expressions per cell.
Rewrite:

```
' Before
=IIf(F!A.Value>100, "X", IIf(F!A.Value>50, "Y", IIf(F!A.Value>10, "Z", "W")))

' After
=Switch(Fields!A.Value > 100, "X",
        Fields!A.Value >  50, "Y",
        Fields!A.Value >  10, "Z",
        True,                 "W")
```

Better still, compute the band in SQL with a `CASE` expression and return it as a
column. It is computed once per row by the database rather than once per cell by
the report engine.

### Avoid chained `Replace`

Detected as `chained_replace` at three or more calls in one expression:

> Chained Replace() calls (3+); prefer a single regex or a normalisation column
> upstream.

```
' Before, evaluated per cell
=Replace(Replace(Replace(Fields!Code.Value, "-", ""), "/", ""), " ", "")
```

Normalise upstream. In SQL:

```sql
TRANSLATE(p.Code, '-/ ', '   ') AS CodeNormalised   -- then REPLACE the spaces once
```

or add a persisted computed column and index it if the normalised form is also a
filter predicate.

### Minimise custom code

`<Code>` blocks and `<CodeModules>` are compiled once but invoked per evaluation
through a late-bound path. They are also a Fabric-paginated warning
(`custom_code`) and a maintenance burden, because the code is invisible in the
designer's expression editor.

If custom code is doing string formatting or classification, move it to SQL. If
it is doing genuinely report-specific layout logic, keep it small and pure.

### Be careful with interactive sorting on large sets

`<UserSort>` on a column header re-processes the entire data region on every
click, from the cached intermediate format. On a 200,000-row Tablix each click
costs a full re-sort and re-render.

Provide a sort **parameter** instead, so the sort happens in `ORDER BY`:

```xml
<QueryParameter Name="@SortBy"><Value>=Parameters!SortBy.Value</Value></QueryParameter>
```

```sql
ORDER BY
    CASE WHEN @SortBy = 'Region'     THEN c.[Sales Territory] END,
    CASE WHEN @SortBy = 'SalesDesc'  THEN SUM(s.[Total Excluding Tax]) END DESC;
```

### Other report-layer costs, in rough order

- **Subreports in a detail row.** One query execution per row. See
  `report-design-patterns.md` pattern 5.
- **`CanGrow` on many columns.** Every growable cell forces a measurement pass.
- **Images embedded per row.** Use `Source=External` with a shared URL so the
  renderer caches, or `Source=Embedded` for a single logo.
- **Complex conditional visibility.** Each expression is evaluated per instance
  and can prevent the renderer from taking a fast path.
- **Very tall page headers.** Rendered once per page; a heavy header on a
  400-page PDF is 400 renders.

---

## 4. Server

### Caching

Cache the rendered intermediate format keyed by parameter combination. Suits a
report with a handful of common parameter sets. Requires stored credentials. Add
a cache refresh plan so the first user of the day does not pay the cost.

### Snapshots

One execution, stored, served to everyone. Suits an expensive report over data
that changes once per load cycle. Query-filtering parameters cannot vary.

### Scale-out

A scale-out deployment adds report server nodes sharing one catalog database.
It multiplies rendering and processing capacity. It does **not** help if the
bottleneck is the source database, which is the usual case, so confirm with
`ExecutionLog3` first.

### Memory configuration

`rsreportserver.config` controls the memory envelope:

```xml
<Configuration>
  <WorkingSetMaximum>8388608</WorkingSetMaximum>   <!-- KB; 8 GB -->
  <WorkingSetMinimum>4194304</WorkingSetMinimum>   <!-- KB; 4 GB -->
  <MemorySafetyMargin>80</MemorySafetyMargin>      <!-- percent -->
  <MemoryThreshold>90</MemoryThreshold>            <!-- percent -->
</Configuration>
```

Above `MemorySafetyMargin` the server starts pressuring low-priority requests
(interactive renders yield to subscriptions). Above `MemoryThreshold` it starts
refusing new requests. Symptoms of a bad envelope are `rsProcessingAborted`
errors under concurrency, not slowness.

Also relevant: `<MaxQueueThreads>` and the `<Timeout>` values for report
execution; a report that must run for eleven minutes needs the server-level
execution timeout raised above its default.

### ExecutionLog3

`ReportServer.dbo.ExecutionLog3` is the single most useful diagnostic on the
server. It records, per execution, the three phases in milliseconds:

| Column | Phase | Points at |
| --- | --- | --- |
| `TimeDataRetrieval` | Running the dataset queries | The database and the query |
| `TimeProcessing` | Grouping, sorting, filtering, aggregating in the report engine | Row volume, report filters, expressions |
| `TimeRendering` | Producing the output format | Layout complexity, page count, renderer |

Ready-to-run query:

```sql
USE ReportServer;

SELECT TOP (200)
    e.ItemPath,
    e.UserName,
    e.Format,
    e.TimeStart,
    e.RowCount,
    e.TimeDataRetrieval,
    e.TimeProcessing,
    e.TimeRendering,
    e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering AS TotalMs,
    CAST(100.0 * e.TimeDataRetrieval
         / NULLIF(e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering, 0) AS decimal(5,1)) AS PctData,
    CAST(100.0 * e.TimeProcessing
         / NULLIF(e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering, 0) AS decimal(5,1)) AS PctProc,
    CAST(100.0 * e.TimeRendering
         / NULLIF(e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering, 0) AS decimal(5,1)) AS PctRender,
    e.Source,          -- Live | Cache | Snapshot | History | AdHoc
    e.Status
FROM dbo.ExecutionLog3 AS e
WHERE e.TimeStart >= DATEADD(day, -7, SYSDATETIME())
ORDER BY TotalMs DESC;
```

Aggregate view, to find the worst reports rather than the worst executions:

```sql
SELECT
    e.ItemPath,
    COUNT(*)                                       AS Executions,
    AVG(e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering) AS AvgTotalMs,
    MAX(e.TimeDataRetrieval + e.TimeProcessing + e.TimeRendering) AS MaxTotalMs,
    AVG(e.TimeDataRetrieval)                       AS AvgDataMs,
    AVG(e.TimeProcessing)                          AS AvgProcMs,
    AVG(e.TimeRendering)                           AS AvgRenderMs,
    AVG(CAST(e.RowCount AS bigint))                AS AvgRows,
    SUM(CASE WHEN e.Status <> 'rsSuccess' THEN 1 ELSE 0 END) AS Failures
FROM dbo.ExecutionLog3 AS e
WHERE e.TimeStart >= DATEADD(day, -30, SYSDATETIME())
GROUP BY e.ItemPath
HAVING COUNT(*) >= 5
ORDER BY AvgTotalMs DESC;
```

Unused reports, for the retirement stage:

```sql
SELECT c.Path, MAX(e.TimeStart) AS LastRun, COUNT(e.ItemPath) AS Runs90d
FROM dbo.Catalog AS c
LEFT JOIN dbo.ExecutionLog3 AS e
       ON e.ItemPath = c.Path
      AND e.TimeStart >= DATEADD(day, -90, SYSDATETIME())
WHERE c.Type = 2                       -- 2 = Report
GROUP BY c.Path
HAVING COUNT(e.ItemPath) = 0
ORDER BY c.Path;
```

`ExecutionLog3` retention is controlled by the `ExecutionLogDaysKept` server
property, default 60 days. Raise it to 180 if you want a seasonal baseline.

---

## Diagnostic decision tree

```
Report is slow
│
├─ Query ExecutionLog3. Which phase is the largest share of TotalMs?
│
├─ TimeDataRetrieval dominates  (usually > 60%)
│   ├─ Run the dataset query in SSMS with the same parameters.
│   │   ├─ Also slow in SSMS  -> it is a database problem
│   │   │     ├─ Check the actual plan for scans on the fact table
│   │   │     │     -> add or fix the covering index
│   │   │     ├─ Check for non-sargable predicates -> rewrite
│   │   │     ├─ Compare estimated vs actual rows
│   │   │     │     -> parameter sniffing: OPTION (RECOMPILE)
│   │   │     ├─ Cursor or row-by-row logic -> rewrite set-based
│   │   │     └─ Blocking / waits -> check sys.dm_exec_requests during a run
│   │   └─ Fast in SSMS, slow from SSRS
│   │         ├─ Different plan due to SET options -> add OPTION (RECOMPILE)
│   │         ├─ Multi-value parameter expanding to a huge IN list
│   │         │     -> STRING_SPLIT plus an all-values shortcut
│   │         └─ Several datasets running serially -> reduce dataset count
│   └─ Consider a snapshot if the data changes once per day.
│
├─ TimeProcessing dominates
│   ├─ Is RowCount much larger than the rows displayed?
│   │     -> aggregate in SQL; remove report and Tablix filters
│   ├─ Report or Tablix filters present -> move to WHERE
│   ├─ Many Lookup / LookupSet calls -> join in SQL
│   ├─ Nested IIf or chained Replace -> Switch, or compute in SQL
│   └─ Sorting in the report over a large set -> ORDER BY in the query
│
├─ TimeRendering dominates
│   ├─ Which format? PDF and Image are inherently the most expensive.
│   ├─ Very high page count -> reduce rows, or paginate by group deliberately
│   ├─ Many data regions (see many_tablix / many_chart) -> split the report
│   ├─ Subreport inside a detail row -> re-model as nested groups or drillthrough
│   ├─ CanGrow on many columns, tall page header -> simplify layout
│   └─ Body wider than the printable area -> blank pages doubling the page count
│
└─ All three are small but users still wait
    ├─ Check Source column: is every run 'Live'? -> enable caching
    ├─ Check the first run of the day specifically -> add a cache refresh plan
    ├─ Check MemorySafetyMargin breaches in the server log
    └─ Check concurrency: many subscriptions on the same schedule
          -> stagger them, or use null delivery to warm the cache first
```

---

## Anti-patterns this repository detects

From `backend/app/core/paginated/compatibility.py`. Codes are exact.

| Code | Severity | Detects | Fix |
| --- | --- | --- | --- |
| `select_star` | warn | `SELECT *` in any `CommandText` | List only the columns the report consumes |
| `select_distinct` | info | `SELECT DISTINCT` | Verify the join keys; remove the `DISTINCT` and confirm the row count matches the documented grain |
| `leading_wildcard` | warn | `LIKE '%...` | Change to a trailing wildcard, or add a full-text index and use `CONTAINS` |
| `cross_join` | warn | `CROSS JOIN` | Confirm the multiplication is intentional; otherwise supply the missing join predicate |
| `cursor_query` | warn | `DECLARE ... CURSOR` | Rewrite set-based: window functions, `STRING_AGG`, joins |
| `nested_iif` | warn | Three or more nested `IIf(` in one expression | Use `Switch`, or compute the band in SQL with `CASE` |
| `chained_replace` | info | Three or more `Replace(` calls in one expression | Normalise upstream with `TRANSLATE`, a computed column, or in the ETL |
| `integrated_security` | info | `Integrated Security=true/SSPI` in a connect string, or `<IntegratedSecurity>true` | Fabric paginated needs an on-premises data gateway with a service account that has read access; on SSRS, prefer stored credentials plus a `@UserId` parameter |
| `many_tablix` | info | More than five `Tablix` items | Split into multiple reports, or reconsider whether this is a dashboard |
| `many_chart` | info | More than five `Chart` items | Consolidate series or split the report |
| `subreport` | blocker | Any `Subreport` element | Re-model as nested groups or a drillthrough report |
| `shared_datasource` | warn | `DataSourceReference` present | Embed the connection for Fabric paginated |
| `shared_dataset` | warn | `SharedDataSet` present | Embed the query for Fabric paginated |
| `custom_code` | warn | `Code` or `CodeModules` present | Move the logic to SQL; custom assemblies are unsupported in Fabric paginated |
| `custom_report_item` | warn | `CustomReportItem` present | Validate after conversion; it may not render |
| `expression_query` | warn | `CommandText` beginning with `=` | Re-validate the generated SQL against the new data source |
| `map` | info | `Map` report item | Validate spatial data sources after re-pointing |

Run the scan over an estate rather than one report at a time: the codes are
designed to be counted and prioritised, and the same scan feeds the
complexity and duplicate analysis described in `docs/report-server.md`.
