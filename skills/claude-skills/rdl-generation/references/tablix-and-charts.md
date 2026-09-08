# Tablix and Chart

A Tablix is the only tabular data region in RDL 2008 and later. Table, matrix and
list are not separate elements: they are the same `Tablix` with different
hierarchies. Understanding that one fact removes most of the difficulty.

## The Tablix data model

A Tablix has two halves that must agree with each other.

- `TablixBody` is the physical grid: a list of column widths, a list of rows, and
  a cell for every intersection. It is a spreadsheet, and it knows nothing about
  data.
- `TablixColumnHierarchy` and `TablixRowHierarchy` are the logical structure:
  which of those columns and rows are fixed ("static") and which repeat once per
  group value ("dynamic").

The renderer walks the hierarchies to decide how many times to emit each body row
and column, and pulls the contents from the body grid.

### The arity rule

This is the rule that breaks hand-written Tablix XML.

- The number of **leaf** `TablixMember` elements in `TablixColumnHierarchy`
  equals the number of `TablixColumn` elements.
- The number of leaf `TablixMember` elements in `TablixRowHierarchy` equals the
  number of `TablixRow` elements.
- Every `TablixRow` has exactly as many `TablixCell` elements as there are
  `TablixColumn` elements.

"Leaf" means a member with no nested `TablixMembers`. A member that has children
contributes its children's leaves, not itself.

### The cell idiom

Every cell holds exactly one report item, almost always a `Textbox`. The full
form is verbose, so the examples below compress it onto one line:

```xml
<TablixCell><CellContents>
  <Textbox Name="Cell_Region">
    <CanGrow>true</CanGrow>
    <Paragraphs><Paragraph><TextRuns><TextRun>
      <Value>=Fields!Region.Value</Value><Style><Format>N2</Format></Style>
    </TextRun></TextRuns></Paragraph></Paragraphs>
    <Style>
      <Border><Style>Solid</Style><Color>LightGrey</Color></Border>
      <PaddingLeft>2pt</PaddingLeft><PaddingRight>2pt</PaddingRight><TextAlign>Right</TextAlign>
    </Style>
  </Textbox>
</CellContents></TablixCell>
```

Notes.

- `CellContents` holds one item. For two things in a cell, put a `Rectangle` in
  the cell and two items inside it.
- Cell styling goes on the `Textbox`. `TablixCell` has no `Style`, which is why a
  border "applied to the cell" does not appear.
- `CellContents` also accepts `ColSpan` and `RowSpan` as its first children. A
  cell that is spanned over is still declared, as `<TablixCell />`.
- `TablixBody` child order is `TablixColumns` then `TablixRows`. A `TablixRow`
  holds `TablixCells` and `Height`; both orders of those two are accepted.

### Static versus dynamic members

A `TablixMember` with no `Group` child is **static**. It renders once. Header
rows, total rows and fixed columns are static members.

```xml
<TablixMember />
```

A member with a `Group` child is **dynamic**. It renders once per distinct value
of its `GroupExpressions`.

```xml
<TablixMember>
  <Group Name="RegionGroup">
    <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
  </Group>
</TablixMember>
```

A `Group` with a `Name` but no `GroupExpressions` is the **detail** group: once
per dataset row.

```xml
<TablixMember><Group Name="Details" /></TablixMember>
```

`TablixMember` child order: `Group`, `SortExpressions`, `TablixHeader`,
`TablixMembers`, `FixedData`, `Visibility`, `HideIfNoRows`, `RepeatOnNewPage`,
`KeepWithGroup`, `KeepTogether`, `DataElementName`, `DataElementOutput`.

`Group` child order: `GroupExpressions`, `PageBreak`, `Filters`, `Parent`,
`DomainScope`, `Variables`, `DataElementName`, `DataElementOutput`.

### Table, matrix and list are the same element

| Shape | Row hierarchy | Column hierarchy |
| --- | --- | --- |
| Flat table | static header member plus a detail member | one static member per column |
| Grouped table | static header, then a dynamic member wrapping a detail member | one static member per column |
| Matrix | one or more dynamic members | one or more dynamic members |
| List | one detail member, and the single body cell holds a `Rectangle` | one static member |

RDL 2005 had a real `<List>` element; RDL 2008 and later express it as the Tablix
above.

### Header cells: TablixHeader

`TablixHeader` attaches a cell to a *member* rather than to a body row. It is how
a matrix labels its groups, and what makes labels repeat when the group repeats.

```xml
<TablixMember>
  <Group Name="RegionGroup">
    <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
  </Group>
  <TablixHeader><Size>1.25in</Size><CellContents>
    <Textbox Name="RegionHeader"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Region.Value</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
  </CellContents></TablixHeader>
  <TablixMembers><TablixMember /></TablixMembers>
</TablixMember>
```

`Size` is the header's width for a row-hierarchy member and its height for a
column-hierarchy member. Headers add to the Tablix extent on top of the body grid.

### KeepWithGroup and RepeatOnNewPage

```xml
<TablixRowHierarchy>
  <TablixMembers>
    <TablixMember>
      <KeepWithGroup>After</KeepWithGroup>
      <RepeatOnNewPage>true</RepeatOnNewPage>
    </TablixMember>
    <TablixMember><Group Name="Details" /></TablixMember>
  </TablixMembers>
</TablixRowHierarchy>
```

- `KeepWithGroup` is `None`, `Before` or `After`. `After` means "stay with the
  group that follows", which is what a header wants. `Before` is what a total row
  wants.
- `RepeatOnNewPage` only has an effect when `KeepWithGroup` is not `None`. That
  pairing is the single most common reason header rows fail to repeat.
- `FixedData` set to `true` freezes the row or column in the HTML viewer.

### Sorting

```xml
<TablixMember>
  <Group Name="RegionGroup">
    <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
  </Group>
  <SortExpressions>
    <SortExpression>
      <Value>=Sum(Fields!Amount.Value)</Value>
      <Direction>Descending</Direction>
    </SortExpression>
  </SortExpressions>
</TablixMember>
```

`Direction` is `Ascending` (default, may be omitted) or `Descending`. Multiple
`SortExpression` elements apply in order. Sorting the detail member sorts rows
inside each group; sorting the group member reorders the groups. RDL discards
query order once a group hierarchy exists, so a grouped report needs
`SortExpressions` even when the query has an `ORDER BY`.

### Page breaks on groups

```xml
<Group Name="RegionGroup">
  <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
  <PageBreak>
    <BreakLocation>Between</BreakLocation>
    <ResetPageNumber>true</ResetPageNumber>
  </PageBreak>
  <PageName>=Fields!Region.Value</PageName>
</Group>
```

`BreakLocation` is `Start`, `End`, `StartAndEnd` or `Between`. Use `Between` for
"one group per page" without a blank leading page. `PageName` sets the Excel
worksheet tab name on export, which is the standard one-tab-per-group trick.

## Complete Tablix fragments

### 1. Flat table

Two columns, a repeating header row, one row per dataset row.

```xml
<Tablix Name="FlatTable">
  <TablixBody>
    <TablixColumns>
      <TablixColumn><Width>2in</Width></TablixColumn>
      <TablixColumn><Width>1.25in</Width></TablixColumn>
    </TablixColumns>
    <TablixRows>
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="H_Product"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Product</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="H_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Amount</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs><Style><TextAlign>Right</TextAlign></Style></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
      <TablixRow><Height>0.22in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="D_Product"><CanGrow>true</CanGrow><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Product.Value</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="D_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Amount.Value</Value><Style><Format>N2</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs><Style><TextAlign>Right</TextAlign></Style></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
    </TablixRows>
  </TablixBody>
  <TablixColumnHierarchy>
    <TablixMembers><TablixMember /><TablixMember /></TablixMembers>
  </TablixColumnHierarchy>
  <TablixRowHierarchy>
    <TablixMembers>
      <TablixMember><KeepWithGroup>After</KeepWithGroup><RepeatOnNewPage>true</RepeatOnNewPage></TablixMember>
      <TablixMember><Group Name="Details" /></TablixMember>
    </TablixMembers>
  </TablixRowHierarchy>
  <DataSetName>SalesData</DataSetName>
  <Top>0.3in</Top><Left>0.3in</Left><Height>0.47in</Height><Width>3.25in</Width>
</Tablix>
```

Two body rows, two row members. Two columns, two column members, two cells per
row. The arity rule holds.

### 2. Grouped table with subtotal and grand total

Four body rows: header, detail, group subtotal, grand total. The grand total is a
static member that is a **sibling** of the dynamic group member, which is what
makes it render once at the end rather than once per group.

```xml
<Tablix Name="GroupedTable">
  <TablixBody>
    <TablixColumns>
      <TablixColumn><Width>2in</Width></TablixColumn>
      <TablixColumn><Width>1.25in</Width></TablixColumn>
    </TablixColumns>
    <TablixRows>
      <!-- row 0: column titles -->
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="H_Product"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Product</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="H_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Amount</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
      <!-- row 1: detail -->
      <TablixRow><Height>0.22in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="D_Product"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Product.Value</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="D_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Amount.Value</Value><Style><Format>N2</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
      <!-- row 2: subtotal per region -->
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="S_Label"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Region.Value &amp; " total"</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="S_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value)</Value><Style><FontWeight>Bold</FontWeight><Format>N2</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
      <!-- row 3: grand total -->
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="G_Label"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Grand total</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="G_Amount"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value, "SalesData")</Value><Style><FontWeight>Bold</FontWeight><Format>N2</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
    </TablixRows>
  </TablixBody>
  <TablixColumnHierarchy>
    <TablixMembers><TablixMember /><TablixMember /></TablixMembers>
  </TablixColumnHierarchy>
  <TablixRowHierarchy>
    <TablixMembers>
      <!-- leaf 0: column titles -->
      <TablixMember><KeepWithGroup>After</KeepWithGroup><RepeatOnNewPage>true</RepeatOnNewPage></TablixMember>
      <!-- region group: contributes leaf 1 (detail) and leaf 2 (subtotal) -->
      <TablixMember>
        <Group Name="RegionGroup">
          <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
          <PageBreak><BreakLocation>Between</BreakLocation></PageBreak>
        </Group>
        <SortExpressions><SortExpression><Value>=Fields!Region.Value</Value></SortExpression></SortExpressions>
        <TablixMembers>
          <TablixMember><Group Name="Details" /></TablixMember>
          <TablixMember><KeepWithGroup>Before</KeepWithGroup></TablixMember>
        </TablixMembers>
      </TablixMember>
      <!-- leaf 3: grand total, sibling of the group -->
      <TablixMember><KeepWithGroup>Before</KeepWithGroup></TablixMember>
    </TablixMembers>
  </TablixRowHierarchy>
  <DataSetName>SalesData</DataSetName>
  <Top>0.3in</Top><Left>0.3in</Left><Height>0.97in</Height><Width>3.25in</Width>
</Tablix>
```

Leaves in the row hierarchy: title (1), detail (2), subtotal (3), grand total (4).
Four body rows. The explicit scope on the grand total,
`Sum(Fields!Amount.Value, "SalesData")`, aggregates over the whole dataset
regardless of where the cell sits.

### 3. Matrix with row and column groups

Regions down the side, years across the top, a total column and a total row. The
static total members are siblings of the dynamic group members.

```xml
<Tablix Name="SalesMatrix">
  <TablixCorner><TablixCornerRows><TablixCornerRow><TablixCornerCell><CellContents>
    <Textbox Name="CornerLabel"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Region</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
  </CellContents></TablixCornerCell></TablixCornerRow></TablixCornerRows></TablixCorner>
  <TablixBody>
    <TablixColumns>
      <TablixColumn><Width>1.25in</Width></TablixColumn>
      <TablixColumn><Width>1.25in</Width></TablixColumn>
    </TablixColumns>
    <TablixRows>
      <!-- row 0: intersection, repeated per region and per year -->
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="M_Value"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value)</Value><Style><Format>N0</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="M_RowTotal"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value)</Value><Style><FontWeight>Bold</FontWeight><Format>N0</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
      <!-- row 1: column totals -->
      <TablixRow><Height>0.25in</Height><TablixCells>
        <TablixCell><CellContents><Textbox Name="M_ColTotal"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value)</Value><Style><FontWeight>Bold</FontWeight><Format>N0</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
        <TablixCell><CellContents><Textbox Name="M_Grand"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Sum(Fields!Amount.Value, "SalesData")</Value><Style><FontWeight>Bold</FontWeight><Format>N0</Format></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox></CellContents></TablixCell>
      </TablixCells></TablixRow>
    </TablixRows>
  </TablixBody>
  <TablixColumnHierarchy>
    <TablixMembers>
      <TablixMember>
        <Group Name="YearGroup">
          <GroupExpressions><GroupExpression>=Year(Fields!OrderDate.Value)</GroupExpression></GroupExpressions>
        </Group>
        <SortExpressions><SortExpression><Value>=Year(Fields!OrderDate.Value)</Value></SortExpression></SortExpressions>
        <TablixHeader><Size>0.25in</Size><CellContents>
          <Textbox Name="YearHeader"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Year(Fields!OrderDate.Value)</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
        </CellContents></TablixHeader>
      </TablixMember>
      <TablixMember>
        <TablixHeader><Size>0.25in</Size><CellContents>
          <Textbox Name="TotalColHeader"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Total</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
        </CellContents></TablixHeader>
      </TablixMember>
    </TablixMembers>
  </TablixColumnHierarchy>
  <TablixRowHierarchy>
    <TablixMembers>
      <TablixMember>
        <Group Name="RegionGroup">
          <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
        </Group>
        <TablixHeader><Size>1.25in</Size><CellContents>
          <Textbox Name="RegionHeader"><Paragraphs><Paragraph><TextRuns><TextRun><Value>=Fields!Region.Value</Value></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
        </CellContents></TablixHeader>
      </TablixMember>
      <TablixMember>
        <TablixHeader><Size>1.25in</Size><CellContents>
          <Textbox Name="TotalRowHeader"><Paragraphs><Paragraph><TextRuns><TextRun><Value>Total</Value><Style><FontWeight>Bold</FontWeight></Style></TextRun></TextRuns></Paragraph></Paragraphs></Textbox>
        </CellContents></TablixHeader>
        <KeepWithGroup>Before</KeepWithGroup>
      </TablixMember>
    </TablixMembers>
  </TablixRowHierarchy>
  <DataSetName>SalesData</DataSetName>
  <Top>0.3in</Top><Left>0.3in</Left><Height>0.5in</Height><Width>3.75in</Width>
</Tablix>
```

The matrix is the same element as the flat table. The differences are the dynamic
column member, the `TablixHeader` labels, and `TablixCorner` filling the top left.
`TablixCorner` is required as soon as both hierarchies have headers; its grid is
`TablixCornerRows/TablixCornerRow/TablixCornerCell`, sized to the number of header
levels on each side.

## Chart

A chart has three mandatory children, checked at publish time:
`ChartCategoryHierarchy`, `ChartSeriesHierarchy` and `ChartData`. This
repository's validator raises the blocker `chart_missing_required_element` if any
is absent.

- `ChartCategoryHierarchy` is the x axis (or the pie slices).
- `ChartSeriesHierarchy` is the legend. Static members mean one fixed series per
  `ChartSeries`; a dynamic member (one with a `Group`) means series expand at run
  time from the data.
- `ChartData/ChartSeriesCollection/ChartSeries` holds the value expressions and
  the chart type.

### The series arity rule

Enforced in this repository as `chart_series_arity`.

- No grouped member in `ChartSeriesHierarchy`: the number of `ChartSeries`
  elements must equal the number of `ChartMember` elements.
- A grouped member: there must be exactly **one** `ChartSeries`.

Violating it produces "contains a different number of ChartSeries elements than
the number of StaticSeries elements".

### ChartMember must have a Label

Every leaf `ChartMember` needs a `<Label>`, or SSRS rejects the report with
"'ChartMember' is empty ... missing a mandatory child element of type 'Label'".
Child order is `Group`, `SortExpressions`, `ChartMembers`, `Label`, so the label
goes after the group.

### Chart Type values

| `Type` | Common `Subtype` values |
| --- | --- |
| `Column` | `Plain`, `Stacked`, `PercentStacked` |
| `Bar` | `Plain`, `Stacked`, `PercentStacked` |
| `Line` | `Plain`, `Smooth`, `Stepped` |
| `Area` | `Plain`, `Stacked`, `PercentStacked` |
| `Scatter` | `Plain`, `Line`, `SmoothLine`, `Bubble` |
| `Pie` | `Plain`, `Exploded` |
| `Doughnut` | `Plain`, `Exploded` |
| `Shape` | funnel and pyramid variants |
| `Range` | `Plain`, `Stacked`, candlestick and stock variants |
| `Polar` | `Plain`, `Radar` |

`Column` is a vertical bar chart and `Bar` is horizontal. This repository maps its
IR types accordingly in `rdl_generator._VTYPE_TO_CHART`: `BAR` becomes `Column`,
`HORIZONTAL_BAR` becomes `Bar`, and `DONUT` becomes `Doughnut`.

### Complete column chart

One dynamic category (region), one static series, one value.

```xml
<Chart Name="SalesByRegion">
  <ChartCategoryHierarchy>
    <ChartMembers>
      <ChartMember>
        <Group Name="RegionCat">
          <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
        </Group>
        <SortExpressions><SortExpression><Value>=Fields!Region.Value</Value></SortExpression></SortExpressions>
        <Label>=Fields!Region.Value</Label>
      </ChartMember>
    </ChartMembers>
  </ChartCategoryHierarchy>

  <ChartSeriesHierarchy>
    <ChartMembers><ChartMember><Label>Amount</Label></ChartMember></ChartMembers>
  </ChartSeriesHierarchy>

  <ChartData>
    <ChartSeriesCollection>
      <ChartSeries Name="Amount">
        <ChartDataPoints><ChartDataPoint>
          <ChartDataPointValues><Y>=Sum(Fields!Amount.Value)</Y></ChartDataPointValues>
          <ChartDataLabel><Style><Format>N0</Format></Style><Visible>true</Visible></ChartDataLabel>
        </ChartDataPoint></ChartDataPoints>
        <Type>Column</Type><Subtype>Plain</Subtype>
        <ChartAreaName>Default</ChartAreaName>
      </ChartSeries>
    </ChartSeriesCollection>
  </ChartData>

  <ChartAreas><ChartArea Name="Default">
    <ChartCategoryAxes><ChartAxis Name="Primary">
      <Visible>true</Visible><Title><Caption>Region</Caption></Title>
      <MajorGridLines><Enabled>false</Enabled></MajorGridLines>
    </ChartAxis></ChartCategoryAxes>
    <ChartValueAxes><ChartAxis Name="Primary">
      <Visible>true</Visible><Title><Caption>Amount</Caption></Title><Minimum>0</Minimum>
      <MajorGridLines><Enabled>true</Enabled><Style><Color>Gainsboro</Color></Style></MajorGridLines>
    </ChartAxis></ChartValueAxes>
  </ChartArea></ChartAreas>

  <ChartLegends><ChartLegend Name="Default"><Position>RightTop</Position><Visible>true</Visible></ChartLegend></ChartLegends>
  <ChartTitles><ChartTitle Name="Title"><Caption>Sales by region</Caption><Style><FontSize>12pt</FontSize><FontWeight>Bold</FontWeight></Style></ChartTitle></ChartTitles>

  <DataSetName>SalesData</DataSetName>
  <Top>1.5in</Top><Left>0.3in</Left><Height>3in</Height><Width>6in</Width>
</Chart>
```

One `ChartMember` in the series hierarchy with no group, and one `ChartSeries`.
That satisfies the static arity rule.

`ChartCategoryAxes` and `ChartValueAxes` each hold up to two `ChartAxis` elements
named `Primary` and `Secondary`. Attach a series to the second axis with
`<ValueAxisName>Secondary</ValueAxisName>` inside `ChartSeries`.

### Multi-series by grouping

To let the data produce the series (one line per product), give the series
hierarchy a dynamic member and keep exactly one `ChartSeries`:

```xml
<ChartSeriesHierarchy>
  <ChartMembers>
    <ChartMember>
      <Group Name="ProductSeries">
        <GroupExpressions><GroupExpression>=Fields!Product.Value</GroupExpression></GroupExpressions>
      </Group>
      <Label>=Fields!Product.Value</Label>
    </ChartMember>
  </ChartMembers>
</ChartSeriesHierarchy>
<ChartData>
  <ChartSeriesCollection>
    <ChartSeries Name="Amount">
      <ChartDataPoints><ChartDataPoint>
        <ChartDataPointValues><Y>=Sum(Fields!Amount.Value)</Y></ChartDataPointValues>
      </ChartDataPoint></ChartDataPoints>
      <Type>Line</Type><Subtype>Plain</Subtype>
    </ChartSeries>
  </ChartSeriesCollection>
</ChartData>
```

### Scatter and bubble

Scatter uses both `X` and `Y`; bubble adds `Size`:

```xml
<ChartDataPointValues>
  <X>=Fields!Units.Value</X>
  <Y>=Fields!Amount.Value</Y>
  <Size>=Fields!Margin.Value</Size>
</ChartDataPointValues>
```

## How this repository reads Tablix and Chart back

`rdl_parser._parse_tablix` collects group field names from both hierarchies by
matching `=Fields!X.Value` inside every `GroupExpression`, and collects measures
by matching `Sum|Avg|Min|Max|CountDistinct|Count(Fields!X.Value)` inside any
element whose local tag name is `Value`, `Y` or `X`.

`rdl_parser._parse_chart` reads the chart type from the **first**
`ChartData/ChartSeriesCollection/ChartSeries/Type`, the categories from
`ChartCategoryHierarchy`, the legend field from the first `ChartSeriesHierarchy`
group, and the values from `ChartData`.

So put the aggregate directly in the cell's `<Value>` or the data point's `<Y>`,
in the canonical form. An aggregate hidden behind a `ReportItems!` reference or a
custom-code call parses as nothing. See "Round-trip safe forms" in
`expressions.md`.
