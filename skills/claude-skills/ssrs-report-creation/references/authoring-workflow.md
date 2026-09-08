# Authoring Workflow

The lifecycle of a paginated report, with a gate at the end of each stage. A gate
is a named artifact plus a named person. If you cannot name both, the stage is
not finished.

The worked example carried through every stage is **Monthly Sales by Region**: a
report the finance team receives as PDF on the second working day of each month,
with a `Region` multi-value parameter and a `DateFrom` / `DateTo` range.

## Stage map

| # | Stage | Produces | Gate signed by |
| --- | --- | --- | --- |
| 1 | Requirements intake | Completed intake form | Requesting business owner |
| 2 | Data contract | Agreed source tables, grain, definitions | Data owner / DBA |
| 3 | Dataset design | Proc or view, tested in SSMS | Developer plus DBA |
| 4 | Parameter design | Parameter table, defaults, cascade order | Business owner |
| 5 | Layout | RDL with regions, groups, page setup | Developer |
| 6 | Formatting | Format strings, styles, locale | Business owner (visual) |
| 7 | Review | Completed review checklist | Peer developer |
| 8 | Deploy | Item in the target folder, datasource re-pointed | Release owner |
| 9 | Operate | Subscription, cache/snapshot policy, monitoring | Support owner |
| 10 | Retire | Decommission record, archived RDL | Business owner |

---

## Stage 1: Requirements intake

**Produce**

- The filled `examples/requirements-template.md`.
- One sentence stating the grain: "one row per region per month".
- The "is paginated the right tool" decision box, answered, not skipped.

**Check**

- Delivery mechanism is named (portal, email subscription, file share, API call).
- Export format is named. PDF and Excel impose different layout limits and you
  cannot satisfy both by accident.
- Worst-case row count is a number, not "a lot".
- Every column has a definition in business language, not a column name.

**Sign-off**: the person who will read the report, in writing, on the form.

**Failure mode if skipped**: you build to an assumed grain. The first review
meeting reveals the reader wanted region by month by product, the dataset has to
change, and the layout, parameters and totals all change with it. Rework here is
total rework, not partial.

### Worked example

```
Report name      Monthly Sales by Region
Audience         Finance leadership (6 named users) plus Regional Managers (22)
Delivery         Email subscription, PDF attachment, 2nd working day 07:00
Also             On-demand from the portal, Excel export allowed
Grain            One detail row per Region per Sales Month
Columns          Region, SalesMonth, OrderCount, GrossSales, NetSales, MarginPct
Parameters       Region (multi-value, default = all the user is entitled to)
                 DateFrom, DateTo (date, default = previous calendar month)
Volume           Typical 12 regions x 1 month = 12 rows.
                 Worst case 12 regions x 36 months = 432 rows.
Latency          Data is loaded by the DW ETL at 03:00; report may run any time after 06:00
Security         Regional Managers see only their own regions
Retention        24 months of run history
Acceptance       GrossSales for the prior month ties to FIN-GL-201 within 0.01
```

Paginated is correct here: fixed layout, PDF destination, scheduled email, a
regulator-adjacent reconciliation requirement.

---

## Stage 2: Data contract

**Produce**

- The list of source objects: schema-qualified tables or views.
- The grain of each source object and how it joins to the others.
- A definition of every measure as SQL, agreed with the data owner.
- The refresh time of each source, so the report's schedule sits after it.

**Check**

- Does a certified source already exist? A warehouse fact table beats querying
  the OLTP system, every time.
- Is the join fan-out understood? If you need `SELECT DISTINCT` to get the right
  row count, the join is wrong. This repository's scanner flags
  `select_distinct` for exactly that reason.
- Are there late-arriving rows? If the fact table is restated for 3 days, a
  report run on day 2 will not match one run on day 5, and someone must decide
  which is correct.

**Sign-off**: the owner of the source data.

**Failure mode if skipped**: the numbers do not tie. You then spend more time
proving the report is right than you spent building it, and the report loses
credibility permanently even after the discrepancy is explained.

### Worked example

```
Source objects
  DW.Fact.Sale            grain: one row per invoice line
  DW.Dimension.City       joins Fact.Sale.[City Key] -> City.[City Key]
  DW.Dimension.Date       joins Fact.Sale.[Invoice Date Key] -> Date.Date
  Ref.RegionEntitlement   grain: one row per (UserPrincipal, Region)

Measures (agreed with Finance data owner)
  OrderCount  COUNT(DISTINCT s.[WWI Invoice ID])
  GrossSales  SUM(s.[Total Excluding Tax])
  NetSales    SUM(s.[Total Excluding Tax] - s.[Total Dry Items] * 0)  -- see note
  MarginPct   SUM(s.Profit) / NULLIF(SUM(s.[Total Excluding Tax]), 0)

Refresh    DW ETL completes 03:10 +/- 20 minutes; restatement window 2 days
```

The restatement window is why the subscription runs on the second working day,
not the first.

---

## Stage 3: Dataset design

**Produce**

- A stored procedure or view in source control, not inline SQL in the RDL, for
  anything beyond a trivial single-table select.
- An execution plan captured for the worst-case parameter set.
- A dataset name that says what it returns: `dsSalesByRegion`, not `DataSet1`.

**Check**

- Only the columns the report renders are selected. No `SELECT *`. The scanner
  emits `select_star` for it, and it breaks silently the day a column is added
  upstream and the field list no longer matches.
- Filtering is in the `WHERE` clause, not in a Tablix filter.
- Aggregation is in SQL where the report does not need the detail rows.
- The proc runs inside the agreed time budget at worst-case volume.
- Field names are stable. Renaming a column later orphans every expression that
  references it.

**Sign-off**: developer plus DBA (the DBA owns the plan and the indexes).

**Failure mode if skipped**: the report works on a developer machine with 500
rows and times out in production with 5 million. Retro-fitting a proc after the
layout is built means re-mapping every field reference.

### Worked example

```sql
CREATE OR ALTER PROCEDURE Reporting.usp_MonthlySalesByRegion
    @DateFrom   date,
    @DateTo     date,
    @RegionList nvarchar(max),   -- comma separated, from =Join(Parameters!Region.Value, ",")
    @UserId     nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        c.[Sales Territory]                              AS Region,
        DATEFROMPARTS(YEAR(d.Date), MONTH(d.Date), 1)    AS SalesMonth,
        COUNT(DISTINCT s.[WWI Invoice ID])               AS OrderCount,
        SUM(s.[Total Excluding Tax])                     AS GrossSales,
        SUM(s.[Total Including Tax])                     AS NetSales,
        SUM(s.Profit)
            / NULLIF(SUM(s.[Total Excluding Tax]), 0)    AS MarginPct
    FROM DW.Fact.Sale                AS s
    JOIN DW.Dimension.City           AS c ON c.[City Key] = s.[City Key]
    JOIN DW.Dimension.Date           AS d ON d.Date       = s.[Invoice Date Key]
    WHERE d.Date >= @DateFrom
      AND d.Date <  DATEADD(day, 1, @DateTo)
      AND c.[Sales Territory] IN (SELECT value FROM STRING_SPLIT(@RegionList, ','))
      AND EXISTS (
            SELECT 1 FROM Ref.RegionEntitlement e
            WHERE e.UserPrincipal = @UserId
              AND e.Region        = c.[Sales Territory]
          )
    GROUP BY c.[Sales Territory], DATEFROMPARTS(YEAR(d.Date), MONTH(d.Date), 1)
    ORDER BY c.[Sales Territory], SalesMonth
    OPTION (RECOMPILE);
END
```

`OPTION (RECOMPILE)` is deliberate: the date range varies by two orders of
magnitude between the monthly run and an ad-hoc 3-year pull, and a single cached
plan serves one badly.

---

## Stage 4: Parameter design

**Produce**

- A parameter table: name, data type, prompt, multi-value, nullable, default,
  available-values source, visibility.
- The cascade order, which is the order of `<ReportParameter>` elements in the
  RDL.
- The available-values datasets, each its own dataset.

**Check**

- Every parameter has a default that renders a useful report on first open.
- Multi-value parameters have a "select all" default only if that is safe at
  worst-case volume.
- Hidden and Internal parameters exist only where a drillthrough or subreport
  supplies them.
- Multi-value parameters against a stored procedure are joined into a string,
  not passed directly. SSRS expands `@P` into a value list only for `Text`
  command type.

**Sign-off**: business owner, because defaults are a business decision.

**Failure mode if skipped**: cascade order is wrong and the child parameter is
already populated before the parent is chosen, so the user must reselect. Or the
default is "all regions, all time" and every open of the report scans the fact
table.

### Worked example

| Name | Type | Prompt | Multi | Default | Values from |
| --- | --- | --- | --- | --- | --- |
| `DateFrom` | DateTime | From date | No | `=DateSerial(Year(Today()), Month(Today())-1, 1)` | none |
| `DateTo` | DateTime | To date | No | `=DateSerial(Year(Today()), Month(Today()), 0)` | none |
| `Region` | String | Region | Yes | all entitled | `dsRegionList` |

`dsRegionList` is itself filtered by entitlement, so the parameter dropdown never
offers a region the user cannot see:

```sql
SELECT DISTINCT e.Region
FROM Ref.RegionEntitlement e
WHERE e.UserPrincipal = @UserId
ORDER BY e.Region;
```

with the RDL query parameter bound to `=User!UserID`:

```xml
<QueryParameters>
  <QueryParameter Name="@UserId">
    <Value>=User!UserID</Value>
  </QueryParameter>
</QueryParameters>
```

The main dataset binds the multi-value parameter through `Join`:

```xml
<QueryParameter Name="@RegionList">
  <Value>=Join(Parameters!Region.Value, ",")</Value>
</QueryParameter>
```

---

## Stage 5: Layout

**Produce**

- The RDL body with regions placed, groups defined, page setup set.
- A page-setup decision recorded: paper size, orientation, margins.

**Check**

- Body width plus left margin plus right margin is less than or equal to the
  paper width. If it is not, every page is followed by a blank one in PDF.
  A4 portrait with 0.5in margins gives a usable body width of 7.27in;
  Letter portrait gives 7.5in.
- Group headers that must repeat have `RepeatOnNewPage=true` and the group is
  a `TablixMember` with `KeepWithGroup`.
- Page breaks are on the group, not on a surrounding rectangle.
- Column headers are inside the Tablix header rows, not floating textboxes above
  the Tablix. Floating textboxes do not repeat and do not export to Excel
  sensibly.

**Sign-off**: developer. This is a craft gate, not a business gate.

**Failure mode if skipped**: a 40-page PDF where 20 pages are blank, headers
appear only on page 1, and the Excel export has merged cells everywhere.

### Worked example

```xml
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition">
  <Page>
    <PageHeight>11.69in</PageHeight>
    <PageWidth>8.27in</PageWidth>
    <LeftMargin>0.5in</LeftMargin>
    <RightMargin>0.5in</RightMargin>
    <TopMargin>0.5in</TopMargin>
    <BottomMargin>0.5in</BottomMargin>
  </Page>
  <ReportSections>
    <ReportSection>
      <Body>
        <Height>2.5in</Height>
        <ReportItems><!-- Tablix width must stay <= 7.27in --></ReportItems>
      </Body>
      <Width>7.27in</Width>
    </ReportSection>
  </ReportSections>
</Report>
```

Region is a Tablix row group with a page break after each instance, so each
regional manager's PDF section is self-contained:

```xml
<TablixMember>
  <Group Name="grpRegion">
    <GroupExpressions>
      <GroupExpression>=Fields!Region.Value</GroupExpression>
    </GroupExpressions>
    <PageBreak><BreakLocation>End</BreakLocation></PageBreak>
  </Group>
  <SortExpressions>
    <SortExpression><Value>=Fields!Region.Value</Value></SortExpression>
  </SortExpressions>
  <KeepWithGroup>After</KeepWithGroup>
  <RepeatOnNewPage>true</RepeatOnNewPage>
</TablixMember>
```

---

## Stage 6: Formatting

**Produce**

- `Format` strings on every numeric and date cell.
- A consistent style: one font family, two or three sizes, one accent colour.
- Alignment rules: numbers right, dates centre or right, text left.

**Check**

- Currency uses `Format` = `C2` or an explicit `"#,##0.00"`, never a
  `FormatCurrency()` expression. The format string survives the Excel export as a
  real numeric format. The expression exports as a text string and cannot be
  summed.
- Percentages use `P1` and the underlying value is a fraction, not already
  multiplied by 100.
- Dates use an explicit pattern such as `yyyy-MM-dd` where the audience is
  international, because `d` follows the renderer's locale.
- Alternating row shading is an expression on `RowNumber`, not manually applied.

**Sign-off**: business owner reviews a rendered PDF, not the designer canvas.

**Failure mode if skipped**: the Excel export is a wall of left-aligned text, and
the finance team retypes the numbers.

### Worked example

```xml
<Style>
  <Format>C2</Format>
  <TextAlign>Right</TextAlign>
  <BackgroundColor>=IIf(RowNumber("grpRegion") Mod 2 = 0, "#F2F2F2", "White")</BackgroundColor>
</Style>
```

Note that this repository's enrichers
(`backend/app/core/paginated/enrichers.py`) will infer `C2` for a field named
`GrossSales`, `P1` for `MarginPct` and `yyyy-MM-dd` for anything ending `Date`
when no `<Format>` is present. Rely on that as a safety net, not as the design.

---

## Stage 7: Review

**Produce**

- The completed `examples/report-review-checklist.md`.
- Evidence: a PDF, an XLSX and a CSV export of the same run, plus a reconciliation
  screenshot or query output.

**Check**

- Numbers tie to the agreed source for at least one full period.
- Zero-row behaviour shows a message, not an empty page.
- Null handling is explicit: `=IIf(IsNothing(Fields!MarginPct.Value), "n/a", ...)`
  or a `NoRowsMessage`.
- Worst-case parameter set was actually executed and timed.

**Sign-off**: a developer who did not build it.

**Failure mode if skipped**: defects are found by the audience. Every one of them
costs more trust than it costs time.

---

## Stage 8: Deploy

**Produce**

- The item in the target folder of the target server.
- A datasource re-point record: which shared datasource the report now uses.
- A rollback note: the previous definition, downloaded before overwrite.

**Check**

- The shared datasource in the target environment points at the target
  environment's database. Overwriting a report does not change its datasource
  binding, but creating a new one binds it to whatever the RDL names.
- Item-level security matches the requirement (Regional Managers get Browser on
  the folder, not Content Manager).
- The report renders on the server, not just in the designer. Designer preview
  uses your credentials; the server uses the datasource's.

**Sign-off**: release owner.

**Failure mode if skipped**: the production report silently reads the test
database. This is the single most common paginated-report incident.

Mechanics, including the exact REST v2.0 calls this repository makes, are in
`deployment.md`.

---

## Stage 9: Operate

**Produce**

- Subscription definitions with an owner and a failure destination.
- A cache or snapshot policy where the report is expensive and the data is
  static between loads.
- A monitoring query against `ExecutionLog3`.

**Check**

- The subscription owner is a service account, not a person who will leave.
- Subscription failures alert someone. A silently failing subscription looks
  identical to a report nobody reads.
- Execution times are baselined now, so a regression is detectable later.

**Sign-off**: support owner.

**Failure mode if skipped**: the report degrades gradually as data volume grows
and nobody notices until it times out.

### Worked example

```
Subscription   Monthly Sales by Region - Finance PDF
Schedule       2nd working day of the month, 07:00
Parameters     DateFrom = first day of prior month (data-driven)
               DateTo   = last day of prior month (data-driven)
               Region   = all
Delivery       Email to fin-leadership@contoso.com, PDF attachment
Owner          CONTOSO\svc_ssrs_subs
On failure     SSRS log alert -> BI support queue
```

---

## Stage 10: Retire

**Produce**

- A decommission record: report path, last run date, last viewer, replacement.
- The archived RDL and its proc DDL, in source control, tagged.

**Check**

- `ExecutionLog3` shows no runs in the retention window (90 days is a common
  threshold).
- Subscriptions are deleted, not just disabled, and their recipients told.
- Nothing drillthroughs into it. A retired drillthrough target produces an error
  in a report that still looks healthy.

**Sign-off**: business owner.

**Failure mode if skipped**: the estate grows monotonically. Duplicate and
near-duplicate detection (`backend/app/core/complexity/duplicates.py`) exists
because retirement is the stage most often skipped.

---

## Gate summary as a single pass

Run this before declaring a report done:

1. Intake form complete, grain in one sentence.
2. Measures defined as SQL and agreed by the data owner.
3. Query in a proc or view, worst-case plan captured.
4. Parameter table complete, defaults render something useful.
5. Body width inside the printable area, headers repeat.
6. `Format` strings everywhere, no `FormatCurrency`.
7. Review checklist signed by someone else.
8. Deployed, datasource verified pointing at the right environment.
9. Subscription owned by a service account, failures alert.
10. Retirement criteria written down on day one.
