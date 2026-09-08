# Report Design Patterns

Named layout patterns for paginated reports. Each entry gives when to use it,
the structure, the key RDL or expression, and the pitfall that bites people.

XML fragments assume the RDL 2016 namespace
(`http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition`).
The `rd:` prefix is
`http://schemas.microsoft.com/SQLServer/reporting/reportdesigner`.

---

## 1. Fixed-column table

**When**: the columns are known at design time. The default, and it covers most
reports.

**Structure**: one Tablix, static column members, a header row member and a
detail row member.

```xml
<TablixRowHierarchy>
  <TablixMembers>
    <TablixMember>                        <!-- header row -->
      <KeepWithGroup>After</KeepWithGroup>
      <RepeatOnNewPage>true</RepeatOnNewPage>
    </TablixMember>
    <TablixMember><Group Name="Details" /></TablixMember>
  </TablixMembers>
</TablixRowHierarchy>
```

**Pitfalls**

- The header must be inside the Tablix. A textbox floating above it does not
  repeat on page 2 and does not become a header row in the Excel export.
- `RepeatOnNewPage` has no effect without `KeepWithGroup`. Both are needed.
- A long value wraps, changing row height and therefore pagination. Set
  `CanGrow` to `false` where truncation is preferable to reflow.

---

## 2. Matrix (pivot) with dynamic columns

**When**: the column set comes from the data: months, categories, anything where
"how many columns" is a query result.

**Structure**: a Tablix with a dynamic column group as well as a dynamic row
group, and an aggregate in the intersection cell.

```xml
<TablixColumnHierarchy>
  <TablixMembers>
    <TablixMember />                       <!-- row-header corner, static -->
    <TablixMember>
      <Group Name="grpMonth">
        <GroupExpressions>
          <GroupExpression>=Fields!SalesMonth.Value</GroupExpression>
        </GroupExpressions>
      </Group>
      <SortExpressions>
        <SortExpression><Value>=Fields!SalesMonth.Value</Value></SortExpression>
      </SortExpressions>
    </TablixMember>
    <TablixMember />                       <!-- static grand-total column -->
  </TablixMembers>
</TablixColumnHierarchy>
```

Intersection cell value: `=Sum(Fields!GrossSales.Value)`

**Pitfalls**

- Width is unbounded. Twelve months fit a portrait page; thirty-six do not, and
  the PDF spills sideways onto extra pages. Constrain the range by parameter.
- A wide matrix exports to Excel with merged header cells, which breaks
  filtering and pivoting downstream.
- Sparse combinations render as blanks, not zeros. Fix in SQL (pattern 15) or
  use `=IIf(IsNothing(Sum(Fields!GrossSales.Value)), 0, Sum(Fields!GrossSales.Value))`.
- A matrix cannot show detail rows. Expanding a cell means drilldown (6) or
  drillthrough (7).

---

## 3. List-based document, page break per instance

**When**: invoices, statements, remittance advices, letters. One free-form block
per entity.

**Structure**: a List (a Tablix with one detail cell containing a Rectangle)
grouped by the entity, with the page break on the group.

```xml
<TablixRowHierarchy>
  <TablixMembers>
    <TablixMember>
      <Group Name="grpInvoice">
        <GroupExpressions>
          <GroupExpression>=Fields!InvoiceId.Value</GroupExpression>
        </GroupExpressions>
        <PageBreak><BreakLocation>End</BreakLocation></PageBreak>
      </Group>
    </TablixMember>
  </TablixMembers>
</TablixRowHierarchy>
```

The cell contents hold the whole document block:

```xml
<CellContents>
  <Rectangle Name="rctInvoiceBlock">
    <KeepTogether>true</KeepTogether>
    <ReportItems><!-- header, address, line-item Tablix, totals --></ReportItems>
  </Rectangle>
</CellContents>
```

**Pitfalls**

- The break belongs on the `Group`, not on the Rectangle. A break on a rectangle
  produces one break, not one per instance.
- `BreakLocation` of `End` on the final group emits a trailing blank page in
  PDF. Use `Between` where the RDL version supports it.
- Page numbering restarts per invoice only with
  `<ResetPageNumber>true</ResetPageNumber>` inside the `PageBreak`. After that,
  `Globals!OverallPageNumber` and `Globals!OverallTotalPages` give the
  document-wide values.

---

## 4. Master-detail via nested groups

**When**: an order header with its lines, a customer with their transactions.

**Structure**: one query at the detail grain, an outer group for the master key,
detail rows inside it. Prefer nested groups in one Tablix over a nested Tablix
in a cell.

```xml
<Group Name="grpOrder">
  <GroupExpressions>
    <GroupExpression>=Fields!OrderId.Value</GroupExpression>
  </GroupExpressions>
</Group>
```

Header aggregates use the group scope:

```
=Sum(Fields!LineTotal.Value, "grpOrder")
=CountDistinct(Fields!ProductId.Value, "grpOrder")
```

**Pitfalls**

- Joining header to detail repeats header columns on every line, so `Sum` of a
  header amount over-counts. Use `=Max(Fields!OrderFreight.Value, "grpOrder")`,
  or emit the value only on the first line in SQL.
- A nested Tablix inside a cell is re-instantiated for every outer row. At 5,000
  orders that is 5,000 instantiations and rendering time dominates.

---

## 5. Subreport, and why to avoid it

**When**: genuinely different grain that cannot be joined, and the content must
appear inline.

```xml
<Subreport Name="subOrderLines">
  <ReportName>/Sales/OrderLinesFragment</ReportName>
  <Parameters>
    <Parameter Name="OrderId"><Value>=Fields!OrderId.Value</Value></Parameter>
  </Parameters>
  <OmitBorderOnPageBreak>true</OmitBorderOnPageBreak>
</Subreport>
```

**Why to avoid it**

- Execution cost is per instance. A subreport in a detail row runs its dataset
  once per row: a 2,000-row parent means 2,000 database round trips. This is the
  most common cause of a paginated report that takes minutes.
- Excel export puts each instance in its own merged block, unusable downstream.
- This repository treats subreports as a migration blocker.
  `backend/app/core/paginated/compatibility.py` emits:

  ```
  [BLOCKER] subreport: Subreports have no Power BI paginated equivalent;
            re-model as drill-through.
  ```

**Instead**: join in SQL and use nested groups (4); use `Lookup` / `LookupSet`
for a handful of values from a second dataset; use drillthrough (7) when the
detail is a separate act of reading.

---

## 6. Drilldown via toggle visibility

**When**: a summary the reader expands in the browser. Interactive only; an
export renders whatever the initial state is.

```xml
<TablixMember>
  <Group Name="grpCity">
    <GroupExpressions>
      <GroupExpression>=Fields!City.Value</GroupExpression>
    </GroupExpressions>
  </Group>
  <Visibility>
    <Hidden>true</Hidden>
    <ToggleItem>txtRegionLabel</ToggleItem>
  </Visibility>
</TablixMember>
```

`txtRegionLabel` is the `Name` of a textbox in the parent group's row; it gains
the plus/minus glyph automatically.

**Pitfalls**

- `ToggleItem` must name a textbox in the parent scope. Naming a sibling gives a
  toggle that appears to do nothing.
- Every row is still retrieved and processed. Drilldown makes a report shorter,
  not faster.
- To expand everything on export, drive visibility from the render format:
  `<Hidden>=Globals!RenderFormat.IsInteractive</Hidden>`

---

## 7. Drillthrough to another report

**When**: the detail is a separate report with its own layout, parameters and
permissions. The right answer in most cases where people reach for a subreport.

```xml
<ActionInfo><Actions><Action>
  <Drillthrough>
    <ReportName>Sales Detail by City</ReportName>
    <Parameters>
      <Parameter Name="Region"><Value>=Fields!Region.Value</Value></Parameter>
      <Parameter Name="DateFrom"><Value>=Parameters!DateFrom.Value</Value></Parameter>
      <Parameter Name="DateTo"><Value>=Parameters!DateTo.Value</Value></Parameter>
    </Parameters>
  </Drillthrough>
</Action></Actions></ActionInfo>
```

Style the source textbox so it reads as a link: `<TextDecoration>Underline`
plus `<Color>#0563C1`.

**Pitfalls**

- `ReportName` is a catalog reference and is environment sensitive. A relative
  name resolves in the same folder and survives folder moves better than
  `/Sales/Sales Detail by City`.
- Every parameter of the target must be satisfiable. Anything not passed needs a
  default, or the target opens with a prompt bar.
- The user needs Browser rights on the target, otherwise they get an access
  error rather than a graceful message.
- Drillthrough links do nothing in PDF or Excel. Never make one the only route
  to required information.

---

## 8. Chart plus table

**When**: a trend or comparison that also needs exact numbers. Almost always
"and", not "or".

```xml
<Chart Name="chtMonthlySales">
  <ChartCategoryHierarchy><ChartMembers><ChartMember>
    <Group Name="chtCat">
      <GroupExpressions>
        <GroupExpression>=Fields!SalesMonth.Value</GroupExpression>
      </GroupExpressions>
    </Group>
    <Label>=Format(Fields!SalesMonth.Value, "MMM yyyy")</Label>
  </ChartMember></ChartMembers></ChartCategoryHierarchy>
  <ChartSeriesHierarchy><ChartMembers><ChartMember>
    <Group Name="chtSer">
      <GroupExpressions>
        <GroupExpression>=Fields!Region.Value</GroupExpression>
      </GroupExpressions>
    </Group>
  </ChartMember></ChartMembers></ChartSeriesHierarchy>
  <ChartData><ChartSeriesCollection><ChartSeries Name="GrossSales">
    <ChartDataPoints><ChartDataPoint><ChartDataPointValues>
      <Y>=Sum(Fields!GrossSales.Value)</Y>
    </ChartDataPointValues></ChartDataPoint></ChartDataPoints>
    <Type>Column</Type>
  </ChartSeries></ChartSeriesCollection></ChartData>
  <DataSetName>dsSalesByRegion</DataSetName>
</Chart>
```

**Pitfalls**

- A chart and a table on separate datasets drift apart the moment one query is
  edited. Share the dataset.
- Charts render at a fixed pixel size and look soft at 300 dpi. Size the chart
  to its final print size rather than scaling it.
- More than about five charts is a dashboard in disguise. This repository's
  scanner emits `many_chart` above five charts and `many_tablix` above five
  Tablix regions.

---

## 9. Multi-column layout

**When**: labels, directories, phone lists. Flow down then across.

```xml
<ReportSection>
  <Body><Height>9in</Height><ReportItems>...</ReportItems></Body>
  <Width>3.4in</Width>
  <Page>
    <Columns>2</Columns>
    <ColumnSpacing>0.25in</ColumnSpacing>
    <PageWidth>8.27in</PageWidth>
    <PageHeight>11.69in</PageHeight>
    <LeftMargin>0.5in</LeftMargin>
    <RightMargin>0.5in</RightMargin>
  </Page>
</ReportSection>
```

**Pitfalls**

- The body width is the **column** width, not the page width. Here
  `(8.27 - 0.5 - 0.5 - 0.25) / 2 = 3.51in`, so 3.4in is safe. Getting this wrong
  is the classic cause of blank alternating columns.
- Multi-column applies only to hard-page renderers (PDF, image, print). The HTML
  preview shows one column, so verify in PDF.
- Excel and CSV ignore columns entirely.

---

## 10. Green-bar alternating rows

**When**: dense tables read on paper.

Cell `BackgroundColor`:

```
=IIf(RowNumber(Nothing) Mod 2 = 0, "#F2F2F2", "White")
```

Restarting per group:

```
=IIf(RowNumber("grpRegion") Mod 2 = 0, "#F2F2F2", "White")
```

**Pitfalls**

- `RowNumber(Nothing)` counts across the whole dataset including other groups.
  That is why banding often looks wrong at group boundaries.
- RDL has no row-level style. Apply the expression to every cell in the row, or
  only one column is banded.
- Very light greys print white on some office printers. `#F2F2F2` is about the
  lightest that survives.

---

## 11. Conditional formatting

Two-state colour:

```
=IIf(Fields!MarginPct.Value < 0, "#C00000", "Black")
```

Three states without nesting. The scanner emits `nested_iif` at three or more
levels, so use `Switch`:

```
=Switch(
    Fields!MarginPct.Value < 0,    "#C00000",
    Fields!MarginPct.Value < 0.05, "#ED7D31",
    True,                          "Black"
 )
```

A glyph as a Unicode character rather than an image, so it survives every
renderer:

```
=Switch(
    Fields!Variance.Value >  0.05, ChrW(9650),
    Fields!Variance.Value < -0.05, ChrW(9660),
    True,                          ChrW(9679)
 )
```

**Pitfalls**

- Colour alone is not accessible. Pair it with a glyph or a label.
- `Switch` does not short-circuit; every condition is evaluated. Guard against
  division by zero inside each branch rather than relying on order.
- Conditional `Hidden` on a column changes layout width and can push the Tablix
  past the printable area. Prefer conditional colour for print reports.

---

## 12. Running totals

```
=RunningValue(Fields!GrossSales.Value, Sum, Nothing)       ' whole dataset
=RunningValue(Fields!GrossSales.Value, Sum, "grpRegion")   ' restarts per group
=RunningValue(Fields!OrderId.Value, CountDistinct, "grpRegion")
```

**Pitfalls**

- `RunningValue` follows the rendered sort order, not the dataset order. An
  interactive re-sort recomputes it.
- It has no "carried forward" concept across pages. For a true carried-forward
  balance use a window function, which is also faster:

  ```sql
  SUM(s.[Total Excluding Tax]) OVER (
      PARTITION BY c.[Sales Territory]
      ORDER BY d.Date
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ) AS RunningSales
  ```

---

## 13. Percent of group total

Detail cell with `Format` set to `P1`:

```
=Sum(Fields!GrossSales.Value) / Sum(Fields!GrossSales.Value, "grpRegion")
```

Percent of the whole dataset (the scope name for a dataset is its name):

```
=Sum(Fields!GrossSales.Value) / Sum(Fields!GrossSales.Value, "dsSalesByRegion")
```

**Pitfalls**

- Divide by zero yields `#Error` in the cell. `IIf` evaluates both branches, so
  wrapping it is not a reliable guard. Compute the ratio in SQL:

  ```sql
  SUM(s.Profit) / NULLIF(SUM(s.[Total Excluding Tax]), 0) AS MarginPct
  ```

- Against a filtered Tablix, the inner aggregate uses the filtered scope while a
  dataset-scoped aggregate uses the unfiltered one, so the column will not add
  to 100 percent.

---

## 14. Top-N plus "Other"

Do it in SQL. The report-side approach hides the remainder rather than
aggregating it.

```sql
WITH Ranked AS (
    SELECT
        c.[Sales Territory] AS Region,
        SUM(s.[Total Excluding Tax]) AS GrossSales,
        ROW_NUMBER() OVER (ORDER BY SUM(s.[Total Excluding Tax]) DESC) AS rn
    FROM DW.Fact.Sale s
    JOIN DW.Dimension.City c ON c.[City Key] = s.[City Key]
    WHERE s.[Invoice Date Key] >= @DateFrom
      AND s.[Invoice Date Key] <  DATEADD(day, 1, @DateTo)
    GROUP BY c.[Sales Territory]
)
SELECT
    CASE WHEN rn <= @TopN THEN Region ELSE 'Other' END AS Region,
    SUM(GrossSales)                                    AS GrossSales,
    MIN(CASE WHEN rn <= @TopN THEN rn ELSE 999999 END) AS SortKey
FROM Ranked
GROUP BY CASE WHEN rn <= @TopN THEN Region ELSE 'Other' END
ORDER BY SortKey;
```

Sort the Tablix group on `SortKey` so "Other" lands last.

Report-side alternative, only when the query cannot change:

```xml
<Filters><Filter>
  <FilterExpression>=Sum(Fields!GrossSales.Value)</FilterExpression>
  <Operator>TopN</Operator>
  <FilterValues><FilterValue>=Parameters!TopN.Value</FilterValue></FilterValues>
</Filter></Filters>
```

The grand total then does not equal the sum of visible rows. Label it "Top N
total" explicitly or it will be raised as a defect.

---

## 15. Sparse-data handling

Fix it in SQL so every combination exists:

```sql
SELECT r.Region, d.MonthStart, ISNULL(SUM(s.[Total Excluding Tax]), 0) AS GrossSales
FROM (SELECT DISTINCT [Sales Territory] AS Region FROM DW.Dimension.City) r
CROSS JOIN (
    SELECT DISTINCT DATEFROMPARTS(YEAR(Date), MONTH(Date), 1) AS MonthStart
    FROM DW.Dimension.Date
    WHERE Date >= @DateFrom AND Date < DATEADD(day, 1, @DateTo)
) d
LEFT JOIN DW.Dimension.City c ON c.[Sales Territory] = r.Region
LEFT JOIN DW.Fact.Sale s
       ON s.[City Key] = c.[City Key]
      AND DATEFROMPARTS(YEAR(s.[Invoice Date Key]), MONTH(s.[Invoice Date Key]), 1) = d.MonthStart
GROUP BY r.Region, d.MonthStart;
```

The scanner flags the deliberate cross join:

```
[WARN] cross_join: CROSS JOIN produces a Cartesian product; confirm the row
       multiplication is intentional.
```

That is correct behaviour. Confirm it during review and move on.

Report-side fallback, if the query cannot change:

```
=IIf(IsNothing(Sum(Fields!GrossSales.Value)), 0, Sum(Fields!GrossSales.Value))
```

This fixes the display but not the missing column: a month with no rows anywhere
still produces no column in a matrix.

---

## 16. No-data message

Always include one. An empty grid looks broken.

```xml
<Tablix Name="tblSales">
  <NoRowsMessage>No sales were recorded for the selected regions and date range.</NoRowsMessage>
  <Style>
    <TextAlign>Center</TextAlign>
    <FontStyle>Italic</FontStyle>
    <Color>#595959</Color>
  </Style>
</Tablix>
```

To hide the headings and chart as well, swap two rectangles:

```xml
<Rectangle Name="rctContent">
  <Visibility><Hidden>=IIf(CountRows("dsSalesByRegion") = 0, True, False)</Hidden></Visibility>
</Rectangle>
<Rectangle Name="rctNoData">
  <Visibility><Hidden>=IIf(CountRows("dsSalesByRegion") = 0, False, True)</Hidden></Visibility>
</Rectangle>
```

**Pitfalls**

- `NoRowsMessage` does not fire when the dataset returns rows that a Tablix
  filter then removes. The Tablix renders empty with no message. One more reason
  to filter in the query.
- A hidden Rectangle can still occupy its designed height. Set
  `<rd:ConsumeContainerWhitespace>true</rd:ConsumeContainerWhitespace>` on the
  report root.
- State the parameters in the message so the reader can self-diagnose:

  ```
  ="No sales between " & Format(Parameters!DateFrom.Value, "yyyy-MM-dd") &
   " and " & Format(Parameters!DateTo.Value, "yyyy-MM-dd") &
   " for: " & Join(Parameters!Region.Value, ", ")
  ```

---

## Pattern selection summary

| The answer is | Use |
| --- | --- |
| A grid with known columns | Fixed-column table (1) |
| A grid whose columns come from data | Matrix (2) |
| One page per entity | List with page break per group (3) |
| Header plus its lines | Nested groups in one Tablix (4) |
| Different grain, must be inline | Subreport (5), reluctantly |
| Summary that expands on screen | Drilldown (6) |
| Summary that opens a detail report | Drillthrough (7) |
| Trend plus exact numbers | Chart plus table (8) |
| Labels or a directory | Multi-column (9) |
| Dense printed table | Green-bar (10) |
| Exceptions must stand out | Conditional formatting (11) |
| Cumulative column | Running total (12), preferably in SQL |
| Contribution percentage | Percent of group total (13) |
| Biggest N and the rest | Top-N plus Other (14), in SQL |
| Gaps in a time series | Sparse-data cross join (15) |
| Zero rows returned | No-data message (16) |
