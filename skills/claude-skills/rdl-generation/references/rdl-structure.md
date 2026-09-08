# RDL Document Structure

Report Definition Language is a single XML document with one `Report` root. Every
element is namespaced, every child sequence is fixed by an XSD, and every
measurement carries a unit. This file describes the document top to bottom for
RDL 2016 (`http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition`).

## The whole tree at a glance

```text
Report                                   (root, 1)
├─ Description?                          plain text, shown in the catalogue
├─ Author?
├─ AutoRefresh?                          seconds, HTML viewer only
├─ DataSources?                          connections
│   └─ DataSource  @Name                 1..n
│       ├─ Transaction?
│       ├─ ConnectionProperties | DataSourceReference
│       ├─ rd:SecurityType?              designer hint
│       └─ rd:DataSourceID?              designer GUID
├─ DataSets?                             queries + field lists
│   └─ DataSet  @Name                    1..n
│       ├─ Query
│       │   ├─ DataSourceName            must match a DataSource/@Name
│       │   ├─ CommandType?              Text | StoredProcedure | TableDirect
│       │   ├─ CommandText?
│       │   ├─ QueryParameters?
│       │   ├─ Timeout?
│       │   └─ rd:UseGenericDesigner?
│       ├─ Fields?
│       │   └─ Field  @Name              DataField | Value, plus rd:TypeName
│       ├─ Filters?                      post-fetch row filter
│       └─ SharedDataSet?                shared-dataset reference
├─ ReportSections                        REQUIRED in 2010/2016
│   └─ ReportSection                     1..n
│       ├─ Body
│       │   ├─ ReportItems?              Tablix, Chart, Textbox, Image, ...
│       │   ├─ Height                    e.g. 6in
│       │   └─ Style?
│       ├─ Width                         e.g. 8.5in
│       └─ Page
│           ├─ PageHeader?
│           ├─ PageFooter?
│           ├─ PageHeight?  PageWidth?
│           ├─ InteractiveHeight?  InteractiveWidth?
│           ├─ LeftMargin? RightMargin? TopMargin? BottomMargin?
│           ├─ Columns?  ColumnSpacing?
│           └─ Style?
├─ ReportParameters?
│   └─ ReportParameter  @Name            1..n
├─ ReportParametersLayout?               parameter-pane grid
├─ Code?                                 VB.NET custom code block
├─ EmbeddedImages?
├─ Language?                             e.g. en-US, or an expression
├─ CodeModules?                          custom assembly references
├─ Classes?                              instance classes from those assemblies
├─ CustomProperties?
├─ DataElementName? DataElementStyle?    data rendering (XML/CSV)
├─ ConsumeContainerWhitespace?
├─ InitialPageName?
└─ Variables?                            report-scoped variables
```

## Required versus optional

Only three things are structurally required for a document the server will
accept.

- `Report` with a recognised RDL namespace declared as the default namespace.
- `ReportSections/ReportSection/Body` (RDL 2010/2016) or `Body` (RDL 2008).
- `ReportSection/Width` (RDL 2010/2016) or `Report/Width` (RDL 2008), with a unit.

Everything else is optional to the schema but not to a useful report.

- Without `DataSources` and `DataSets` the report renders, but every data-bound
  expression yields `#Error`.
- Without `Body/ReportItems` the report renders a blank page. The repository's
  own validator treats this as a blocker (`no_report_items` in
  `backend/app/core/paginated/validate.py`).
- Without `Body/Height` the body collapses. Set it explicitly.
- `Page` is optional, but omitting it means the server applies its defaults
  (8.5in by 11in, 1in margins) which rarely match your `Body/Width`.

## Element-order tables

RDL is validated with `xsd:sequence`, so the order of children matters. When the
order is wrong the parser reports the *next* element it did not expect, not the
one you misplaced, which is why the message often names an innocent element.

The orders below are the orders Report Designer and Report Builder emit. Emit in
these orders and the document validates on every target. Some readers tolerate
deviation, but do not rely on that.

### Report

| # | Element | Occurs |
| --- | --- | --- |
| 1 | `Description` | 0..1 |
| 2 | `Author` | 0..1 |
| 3 | `AutoRefresh` | 0..1 |
| 4 | `DataSources` | 0..1 |
| 5 | `DataSets` | 0..1 |
| 6 | `ReportSections` | 1 |
| 7 | `ReportParameters` | 0..1 |
| 8 | `ReportParametersLayout` | 0..1 |
| 9 | `Code` | 0..1 |
| 10 | `EmbeddedImages` | 0..1 |
| 11 | `Language` | 0..1 |
| 12 | `CodeModules` | 0..1 |
| 13 | `Classes` | 0..1 |
| 14 | `CustomProperties` | 0..1 |
| 15 | `DataTransform`, `DataSchema`, `DataElementName`, `DataElementStyle` | 0..1 each |
| 16 | `ConsumeContainerWhitespace` | 0..1 |
| 17 | `InitialPageName` | 0..1 |
| 18 | `Variables` | 0..1 |

Note on this repository: `backend/app/core/generator/rdl_generator.py` emits
`DataSources`, `DataSets`, `ReportParameters`, `ReportSections`, which puts
`ReportParameters` one slot early. That output opens in Report Builder and
Power BI Desktop, but if you validate against the published XSD, use the order
in the table above.

### DataSource

| # | Element | Notes |
| --- | --- | --- |
| 1 | `Transaction` | 0..1, `true` to run all datasets in one transaction |
| 2 | `DataSourceReference` **or** `ConnectionProperties` | exactly one of the two |
| - | `rd:SecurityType`, `rd:DataSourceID` | designer extension elements, emitted last |

### ConnectionProperties

| # | Element | Notes |
| --- | --- | --- |
| 1 | `DataProvider` | required, e.g. `SQL` |
| 2 | `ConnectString` | required in practice |
| 3 | `IntegratedSecurity` | 0..1, `true` or `false` |
| 4 | `Prompt` | 0..1, credential prompt text |

### DataSet

| # | Element | Notes |
| --- | --- | --- |
| 1 | `Query` | required unless the dataset is purely a shared reference |
| 2 | `Fields` | 0..1, but always emit it |
| 3 | `CaseSensitivity`, `Collation`, `AccentSensitivity`, `KanatypeSensitivity`, `WidthSensitivity` | 0..1 each |
| 4 | `Filters` | 0..1, applied after the rows are fetched |
| 5 | `InterpretSubtotalsAsDetails` | 0..1, OLAP sources |
| 6 | `SharedDataSet` | 0..1, `SharedDataSetReference` inside |

### Query

| # | Element | Notes |
| --- | --- | --- |
| 1 | `DataSourceName` | required, matches `DataSource/@Name` |
| 2 | `CommandType` | 0..1, defaults to `Text` when omitted |
| 3 | `CommandText` | the SQL, the procedure name, or the table name |
| 4 | `QueryParameters` | 0..1 |
| 5 | `Timeout` | 0..1, seconds |
| - | `rd:UseGenericDesigner` | designer extension, emitted last |

This is the sequence the skill's rule 1 is about. `CommandType` after
`CommandText` produces "The element 'Query' has invalid child element
'CommandText'".

### Field

| # | Element | Notes |
| --- | --- | --- |
| 1 | `DataField` **or** `Value` | `DataField` names a result-set column, `Value` is a calculated-field expression |
| - | `rd:TypeName` | designer extension carrying the .NET type |

### ReportParameter

| # | Element | Notes |
| --- | --- | --- |
| 1 | `DataType` | required: `Boolean`, `DateTime`, `Integer`, `Float`, `String` |
| 2 | `Nullable` | 0..1 |
| 3 | `DefaultValue` | 0..1 |
| 4 | `AllowBlank` | 0..1, `String` parameters only |
| 5 | `Prompt` | 0..1, the label in the parameter pane |
| 6 | `PromptUser` | 0..1 |
| 7 | `Hidden` | 0..1 |
| 8 | `MultiValue` | 0..1 |
| 9 | `ValidValues` | 0..1 |
| 10 | `UsedInQuery` | 0..1, `True`, `False` or `Auto` |

### ReportSection

| # | Element | Notes |
| --- | --- | --- |
| 1 | `Body` | required |
| 2 | `Width` | required, the body width |
| 3 | `Page` | page setup for this section |

### Body

| # | Element | Notes |
| --- | --- | --- |
| 1 | `ReportItems` | 0..1 |
| 2 | `Height` | 0..1, but always emit it |
| 3 | `Style` | 0..1 |

### Page

| # | Element |
| --- | --- |
| 1 | `PageHeader` |
| 2 | `PageFooter` |
| 3 | `PageHeight` |
| 4 | `PageWidth` |
| 5 | `InteractiveHeight` |
| 6 | `InteractiveWidth` |
| 7 | `LeftMargin` |
| 8 | `RightMargin` |
| 9 | `TopMargin` |
| 10 | `BottomMargin` |
| 11 | `Columns` |
| 12 | `ColumnSpacing` |
| 13 | `Style` |

### Tablix

| # | Element | Notes |
| --- | --- | --- |
| 1 | `TablixCorner` | 0..1, the top-left block of a matrix |
| 2 | `TablixBody` | required |
| 3 | `TablixColumnHierarchy` | required |
| 4 | `TablixRowHierarchy` | required |
| 5 | `LayoutDirection`, `GroupsBeforeRowHeaders`, `RepeatColumnHeaders`, `RepeatRowHeaders`, `FixedColumnHeaders`, `FixedRowHeaders` | 0..1 each |
| 6 | `KeepTogether`, `NoRowsMessage` | 0..1 each |
| 7 | `DataSetName` | 0..1, required in practice |
| 8 | `PageBreak`, `PageName`, `Filters`, `SortExpressions` | 0..1 each |
| 9 | `Top`, `Left`, `Height`, `Width`, `ZIndex` | 0..1 each |
| 10 | `Visibility`, `ToolTip`, `Bookmark`, `RepeatWith`, `DataElementName`, `DataElementOutput` | 0..1 each |
| 11 | `Style` | 0..1 |

### Textbox

| # | Element | Notes |
| --- | --- | --- |
| 1 | `CanGrow`, `CanShrink` | 0..1 each |
| 2 | `KeepTogether`, `HideDuplicates`, `ToggleImage`, `UserSort` | 0..1 each |
| 3 | `Paragraphs` | required |
| 4 | `ActionInfo` | 0..1, hyperlink / drill-through |
| 5 | `Top`, `Left`, `Height`, `Width`, `ZIndex` | 0..1 each |
| 6 | `Visibility`, `ToolTip`, `Bookmark`, `RepeatWith` | 0..1 each |
| 7 | `DataElementName`, `DataElementOutput`, `DataElementStyle` | 0..1 each |
| 8 | `Style` | 0..1 |

`Paragraphs/Paragraph` holds `TextRuns` first, then paragraph-level
`Style`. `TextRun` holds `Value`, then `MarkupType`, then `Style`.

### Chart

| # | Element | Notes |
| --- | --- | --- |
| 1 | `ChartCategoryHierarchy` | required, may hold zero members |
| 2 | `ChartSeriesHierarchy` | required |
| 3 | `ChartData` | required |
| 4 | `ChartAreas` | 0..1 |
| 5 | `ChartLegends`, `ChartTitles`, `ChartCustomPaletteColors` | 0..1 each |
| 6 | `Palette`, `ChartBorderSkin`, `ChartNoDataMessage`, `ChartElementPosition` | 0..1 each |
| 7 | `DataSetName` | 0..1, required in practice |
| 8 | `PageBreak`, `Filters`, `SortExpressions` | 0..1 each |
| 9 | `Top`, `Left`, `Height`, `Width`, `ZIndex` | 0..1 each |
| 10 | `Style` | 0..1 |

The repository's own validator (`_ssrs_findings`) raises a blocker when any of
`ChartCategoryHierarchy`, `ChartSeriesHierarchy` or `ChartData` is missing, so all
three must be present even when the chart has a single static series.

## The 2008 versus 2016 structural split

The single largest difference between RDL 2008 and RDL 2010/2016 is where the
body lives.

RDL 2008 (`.../2008/01/reportdefinition`):

```xml
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition">
  <DataSources>...</DataSources>
  <DataSets>...</DataSets>
  <Body>
    <ReportItems>...</ReportItems>
    <Height>6in</Height>
  </Body>
  <ReportParameters>...</ReportParameters>
  <Width>8.5in</Width>
  <Page>
    <PageHeader>...</PageHeader>
    <PageFooter>...</PageFooter>
    <PageHeight>11in</PageHeight>
    <PageWidth>8.5in</PageWidth>
  </Page>
</Report>
```

RDL 2010 and 2016 wrap `Body`, `Width` and `Page` in a repeatable section:

```xml
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition">
  <DataSources>...</DataSources>
  <DataSets>...</DataSets>
  <ReportSections>
    <ReportSection>
      <Body>
        <ReportItems>...</ReportItems>
        <Height>6in</Height>
      </Body>
      <Width>8.5in</Width>
      <Page>
        <PageHeader>...</PageHeader>
        <PageFooter>...</PageFooter>
        <PageHeight>11in</PageHeight>
        <PageWidth>8.5in</PageWidth>
      </Page>
    </ReportSection>
  </ReportSections>
  <ReportParameters>...</ReportParameters>
</Report>
```

`ReportSections` may contain more than one `ReportSection`. Each section gets its
own body, width and page setup, and sections render one after another. Report
Builder only ever authors one, but the schema allows several and the server
renders all of them.

The reader side of this repository handles both shapes in one helper,
`rdl_parser._report_section`:

```python
def _report_section(report: Element) -> Element:
    sections = child(report, "ReportSections")
    if sections is not None:
        rs = child(sections, "ReportSection")
        if rs is not None:
            return rs
    return report
```

Everything below the section is identical between the two versions, which is why
upgrading a 2008 document is mostly a matter of moving three elements. See
`namespaces-versions.md`.

## PageHeader and PageFooter live inside Page

This is the most common structural error in hand-written RDL 2016.

```xml
<!-- WRONG: PageHeader as a sibling of Body -->
<ReportSection>
  <Body>...</Body>
  <PageHeader>...</PageHeader>
  <Width>8.5in</Width>
  <Page>...</Page>
</ReportSection>
```

```xml
<!-- RIGHT -->
<ReportSection>
  <Body>...</Body>
  <Width>8.5in</Width>
  <Page>
    <PageHeader>
      <Height>0.4in</Height>
      <PrintOnFirstPage>true</PrintOnFirstPage>
      <PrintOnLastPage>true</PrintOnLastPage>
      <ReportItems>
        <Textbox Name="HeaderTitle">
          <Paragraphs>
            <Paragraph>
              <TextRuns>
                <TextRun>
                  <Value>Sales by Region</Value>
                  <Style><FontWeight>Bold</FontWeight><FontSize>14pt</FontSize></Style>
                </TextRun>
              </TextRuns>
            </Paragraph>
          </Paragraphs>
          <Top>0in</Top>
          <Left>0in</Left>
          <Height>0.3in</Height>
          <Width>6in</Width>
        </Textbox>
      </ReportItems>
    </PageHeader>
    <PageHeight>11in</PageHeight>
    <PageWidth>8.5in</PageWidth>
  </Page>
</ReportSection>
```

`PageHeader` and `PageFooter` children are, in order: `Height`,
`PrintOnFirstPage`, `PrintOnLastPage`, `ReportItems`, `Style`. Report Builder
emits `ReportItems` before the print flags; both orders are accepted, but the
order above is the schema order.

Two behavioural rules for headers and footers.

- They cannot contain data regions. Only `Textbox`, `Image`, `Line` and
  `Rectangle` are allowed.
- Expressions inside them can only reference `Globals!`, `User!`,
  `Parameters!` and `ReportItems!`. A bare `Fields!X.Value` in a page header is
  a validation error because there is no data scope there.

## A complete minimal valid document

This is the smallest RDL 2016 file that opens in Report Builder, publishes to a
report server, and renders one row of data. Every element in it is load-bearing.

```xml
<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
        xmlns:rd="http://schemas.microsoft.com/SQLServer/reporting/reportdesigner">
  <DataSources>
    <DataSource Name="SalesDB">
      <ConnectionProperties>
        <DataProvider>SQL</DataProvider>
        <ConnectString>Data Source=SQL01;Initial Catalog=Sales</ConnectString>
        <IntegratedSecurity>true</IntegratedSecurity>
      </ConnectionProperties>
      <rd:SecurityType>Integrated</rd:SecurityType>
    </DataSource>
  </DataSources>

  <DataSets>
    <DataSet Name="SalesData">
      <Query>
        <DataSourceName>SalesDB</DataSourceName>
        <CommandType>Text</CommandType>
        <CommandText>SELECT Region, Amount FROM dbo.Sales</CommandText>
      </Query>
      <Fields>
        <Field Name="Region">
          <DataField>Region</DataField>
          <rd:TypeName>System.String</rd:TypeName>
        </Field>
        <Field Name="Amount">
          <DataField>Amount</DataField>
          <rd:TypeName>System.Decimal</rd:TypeName>
        </Field>
      </Fields>
    </DataSet>
  </DataSets>

  <ReportSections>
    <ReportSection>
      <Body>
        <ReportItems>
          <Tablix Name="SalesTablix">
            <TablixBody>
              <TablixColumns>
                <TablixColumn><Width>2in</Width></TablixColumn>
                <TablixColumn><Width>1.5in</Width></TablixColumn>
              </TablixColumns>
              <TablixRows>
                <TablixRow>
                  <Height>0.25in</Height>
                  <TablixCells>
                    <TablixCell>
                      <CellContents>
                        <Textbox Name="RegionCell">
                          <Paragraphs><Paragraph><TextRuns><TextRun>
                            <Value>=Fields!Region.Value</Value>
                          </TextRun></TextRuns></Paragraph></Paragraphs>
                        </Textbox>
                      </CellContents>
                    </TablixCell>
                    <TablixCell>
                      <CellContents>
                        <Textbox Name="AmountCell">
                          <Paragraphs><Paragraph><TextRuns><TextRun>
                            <Value>=Sum(Fields!Amount.Value)</Value>
                            <Style><Format>N2</Format></Style>
                          </TextRun></TextRuns></Paragraph></Paragraphs>
                        </Textbox>
                      </CellContents>
                    </TablixCell>
                  </TablixCells>
                </TablixRow>
              </TablixRows>
            </TablixBody>
            <TablixColumnHierarchy>
              <TablixMembers>
                <TablixMember />
                <TablixMember />
              </TablixMembers>
            </TablixColumnHierarchy>
            <TablixRowHierarchy>
              <TablixMembers>
                <TablixMember>
                  <Group Name="RegionGroup">
                    <GroupExpressions>
                      <GroupExpression>=Fields!Region.Value</GroupExpression>
                    </GroupExpressions>
                  </Group>
                </TablixMember>
              </TablixMembers>
            </TablixRowHierarchy>
            <DataSetName>SalesData</DataSetName>
            <Top>0.25in</Top>
            <Left>0.25in</Left>
            <Height>0.25in</Height>
            <Width>3.5in</Width>
          </Tablix>
        </ReportItems>
        <Height>1in</Height>
      </Body>
      <Width>6.5in</Width>
      <Page>
        <PageHeight>11in</PageHeight>
        <PageWidth>8.5in</PageWidth>
        <LeftMargin>1in</LeftMargin>
        <RightMargin>1in</RightMargin>
        <TopMargin>1in</TopMargin>
        <BottomMargin>1in</BottomMargin>
      </Page>
    </ReportSection>
  </ReportSections>
</Report>
```

Checks worth noting in that document.

- The number of `TablixColumn` elements equals the number of `TablixCell`
  elements in each row, and equals the number of `TablixMember` elements in
  `TablixColumnHierarchy`. A mismatch is the most common Tablix error.
- `Body/Width` is 6.5in and the margins are 1in each, so
  `6.5 + 1 + 1 = 8.5 = PageWidth`. Exceed that and the renderer emits a blank
  page after every page.
- Every measurement carries a unit.
- The aggregate `Sum(Fields!Amount.Value)` is inside a row group scoped on
  `Region`, so it produces one total per region.

## Where the repository does this

- Writer: `backend/app/core/generator/rdl_generator.py`, function `generate_rdl`.
- Reader: `backend/app/core/parser/rdl_parser.py`, function `parse_rdl`.
- Known-good fixture: `backend/tests/fixtures/sales_by_region.rdl`.
