# Layout, Units and Page Setup

RDL is an absolute-positioning format. There is no flow layout, no percentage
sizing and no automatic reflow. Every report item sits at a fixed offset inside
its container, and the renderer paginates whatever that produces.

## Measurement units

Every RDL size is a number followed by a unit, with no space required between
them.

| Unit | Meaning | Inches | Pixels at 96 dpi |
| --- | --- | --- | --- |
| `in` | inch | 1 | 96 |
| `cm` | centimetre | 0.393701 | 37.795 |
| `mm` | millimetre | 0.0393701 | 3.7795 |
| `pt` | point (1/72 inch) | 0.013889 | 1.3333 |
| `pc` | pica (12 points, 1/6 inch) | 0.166667 | 16 |

**The unit is mandatory.** `<Width>3</Width>` is not valid RDL. Some readers
tolerate it; the report server does not, and a unit-less value is one of the
first things to check when a document is rejected without a useful message.

`px` is **not** a valid RDL unit. Do not emit it.

### This repository's conversion

`backend/app/core/parser/rdl_xml.py` converts sizes to pixels at 96 dpi:

```python
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(in|cm|mm|pt|px)?\s*$", re.IGNORECASE)
_UNIT_TO_PX = {
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "pt": 96.0 / 72.0,
    "px": 1.0,
}
```

Two consequences worth knowing.

- A unit-less value is treated as **pixels**, not inches. The docstring says why:
  "A unit-less value is treated as pixels (factor 1.0) rather than inches so
  malformed input cannot silently inflate the layout 96x." So `<Width>3</Width>`
  parses to 3 pixels, and the resulting report looks empty rather than enormous.
- `pc` is not in the regex alternation, so `2pc` fails to match at all and
  `size_to_px` returns its `default` (usually 0). RDL allows picas; this
  repository does not read them. Prefer `in`, `cm`, `mm` or `pt`.

The generator converts the other way in `rdl_generator._px_to_in`:

```python
def _px_to_in(px: float) -> str:
    return f"{round(px / 96.0, 4)}in"
```

Four decimal places at 96 dpi means the smallest expressible step is 0.0001in,
about 0.01 pixels. That is plenty.

## Absolute positioning

Every report item may carry `Top`, `Left`, `Height`, `Width` and `ZIndex`.

```xml
<Tablix Name="SalesTablix">
  ...
  <Top>0.5in</Top>
  <Left>0.25in</Left>
  <Height>2in</Height>
  <Width>6in</Width>
  <ZIndex>2</ZIndex>
</Tablix>
```

Rules.

- `Top` and `Left` are measured from the top-left corner of the **containing
  region**, not the page. Inside `Body/ReportItems` they are relative to the body.
  Inside a `Rectangle`, they are relative to that rectangle. Inside a
  `CellContents`, they are relative to the cell, and are normally omitted.
- `Height` and `Width` are the item's own extent. They are not affected by
  `Top` and `Left`.
- Omitting `Top` and `Left` means zero, so items pile up at the origin.
- Negative offsets are not valid.
- Report items may overlap. They are allowed to in HTML, and mostly work in PDF,
  but Excel and CSV renderers behave badly with overlaps: an item that overlaps
  another by even 0.01in forces the Excel renderer to introduce extra merged
  cells. Keep items on a clean grid.

This repository's parser flattens rectangles when it walks the tree, and warns
about it (`rdl_parser._walk_report_items`): "Rectangle container flattened; child
item positions are approximate." The offsets are accumulated
(`_item_box(elem, off_x, off_y)`) so nested positions come out in body
coordinates.

## Body Width and Height

`Body/Height` and `ReportSection/Width` are the size of the design canvas, not
the size of the output.

```xml
<ReportSection>
  <Body>
    <ReportItems>...</ReportItems>
    <Height>6in</Height>
  </Body>
  <Width>6.5in</Width>
  <Page>...</Page>
</ReportSection>
```

- `Width` is a sibling of `Body`, not a child of it. In RDL 2008 it is a sibling
  under `Report`; in 2010/2016 it is a sibling under `ReportSection`.
- `Body/Height` is the height of the design surface. It does **not** control how
  long the rendered report is. A Tablix that returns 10,000 rows grows well past
  `Body/Height` and paginates normally. `Body/Height` matters for two things: it
  sets where the body ends when items are absolutely positioned below the data,
  and it sets the minimum height of the body on a page with few rows.
- `Body/Height` of `0in` renders a blank report. That is the usual cause of "the
  report opens but nothing is there".
- `Width` **does** control the output width, and it is the term in the
  blank-page arithmetic below.

Set `Body/Height` to the bottom edge of the lowest item (`Top + Height`), and
set `Width` to the right edge of the widest item, then round up slightly.

## The Page element

```xml
<Page>
  <PageHeader>...</PageHeader>
  <PageFooter>...</PageFooter>
  <PageHeight>11in</PageHeight>
  <PageWidth>8.5in</PageWidth>
  <InteractiveHeight>0in</InteractiveHeight>
  <InteractiveWidth>8.5in</InteractiveWidth>
  <LeftMargin>1in</LeftMargin>
  <RightMargin>1in</RightMargin>
  <TopMargin>0.75in</TopMargin>
  <BottomMargin>0.75in</BottomMargin>
  <Columns>1</Columns>
  <ColumnSpacing>0.5in</ColumnSpacing>
  <Style>
    <BackgroundColor>White</BackgroundColor>
  </Style>
</Page>
```

| Element | Meaning | Default |
| --- | --- | --- |
| `PageHeight` | physical page height for paginated renderers (PDF, image, print) | `11in` |
| `PageWidth` | physical page width | `8.5in` |
| `InteractiveHeight` | soft-page height in the HTML viewer; `0in` means "one long page, no soft breaks" | same as `PageHeight` |
| `InteractiveWidth` | soft-page width in the HTML viewer | same as `PageWidth` |
| `LeftMargin` / `RightMargin` | horizontal margins, subtracted from the printable width | `1in` |
| `TopMargin` / `BottomMargin` | vertical margins | `1in` |
| `Columns` | newspaper-style column count for the body | `1` |
| `ColumnSpacing` | gutter between those columns | `0.5in` |

Two practical notes.

- Set `InteractiveHeight` to `0in` for a report meant to be scrolled in a
  browser and exported to Excel. It removes the soft page breaks that otherwise
  chop a long table into arbitrary chunks.
- `PageHeader` and `PageFooter` heights are *outside* the body but *inside* the
  margins. The vertical space available to the body per page is
  `PageHeight - TopMargin - BottomMargin - PageHeader/Height - PageFooter/Height`.

## The blank-page arithmetic

The single most reported RDL layout defect is a blank page between every page of
content. It is always the same cause.

```text
Body Width + LeftMargin + RightMargin  <=  PageWidth
```

If the left side exceeds `PageWidth`, the renderer splits the body horizontally
and emits the overflow strip as its own page. The overflow strip is usually
empty, so you get content, blank, content, blank.

Worked example for US Letter portrait with 1in margins:

```text
PageWidth  = 8.5in
LeftMargin = 1in
RightMargin= 1in
=> maximum Body Width = 8.5 - 1 - 1 = 6.5in
```

So `<Width>6.5in</Width>` is the largest safe body. A Tablix at `Left` of 0.25in
that is 6.4in wide has a right edge at 6.65in, which already overflows.

The same arithmetic applies vertically for a page footer that pushes the body
past the bottom margin, though the visible symptom there is a truncated footer
rather than a blank page.

Checklist when you see blank pages.

1. Compute `Width + LeftMargin + RightMargin` and compare to `PageWidth`.
2. Compute `max(Left + Width)` over every item in `Body/ReportItems` and compare
   to `Width`. An item wider than the body silently widens the body at render
   time.
3. Check for an item with a `Left` that is nearly the body width and a tiny
   `Width`, which is usually a stray textbox left over from editing.
4. Check `Columns`. A value greater than 1 divides the body width and changes
   the arithmetic.

## Standard page sizes

| Size | Portrait `PageWidth` x `PageHeight` | Landscape |
| --- | --- | --- |
| Letter | `8.5in` x `11in` | `11in` x `8.5in` |
| Legal | `8.5in` x `14in` | `14in` x `8.5in` |
| Tabloid / Ledger | `11in` x `17in` | `17in` x `11in` |
| Executive | `7.25in` x `10.5in` | `10.5in` x `7.25in` |
| A3 | `29.7cm` x `42cm` | `42cm` x `29.7cm` |
| A4 | `21cm` x `29.7cm` | `29.7cm` x `21cm` |
| A5 | `14.8cm` x `21cm` | `21cm` x `14.8cm` |
| B4 (JIS) | `25.7cm` x `36.4cm` | `36.4cm` x `25.7cm` |

Maximum body width at 1in margins (or 2cm for the metric sizes):

| Size | Portrait max body width | Landscape max body width |
| --- | --- | --- |
| Letter | `6.5in` | `9in` |
| Legal | `6.5in` | `12in` |
| Tabloid | `9in` | `15in` |
| A4 (2cm margins) | `17cm` | `25.7cm` |
| A3 (2cm margins) | `25.7cm` | `38cm` |

Landscape is expressed by swapping `PageWidth` and `PageHeight`. There is no
orientation element in RDL.

A4 landscape example:

```xml
<Page>
  <PageHeight>21cm</PageHeight>
  <PageWidth>29.7cm</PageWidth>
  <LeftMargin>2cm</LeftMargin>
  <RightMargin>2cm</RightMargin>
  <TopMargin>1.5cm</TopMargin>
  <BottomMargin>1.5cm</BottomMargin>
</Page>
```

with `<Width>25.7cm</Width>` on the `ReportSection`.

## The Style element

`Style` appears on almost every report item, on `TextRun`, on `Paragraph`, on
`Page` and on `Body`. Its children are all optional and all may be expressions.

```xml
<Style>
  <Border>
    <Color>LightGrey</Color>
    <Style>Solid</Style>
    <Width>1pt</Width>
  </Border>
  <TopBorder><Style>None</Style></TopBorder>
  <BottomBorder><Style>Solid</Style><Color>Black</Color><Width>2pt</Width></BottomBorder>
  <BackgroundColor>#F5F5F5</BackgroundColor>
  <FontFamily>Segoe UI</FontFamily>
  <FontSize>9pt</FontSize>
  <FontWeight>Bold</FontWeight>
  <FontStyle>Normal</FontStyle>
  <TextDecoration>None</TextDecoration>
  <Color>#333333</Color>
  <Format>#,##0.00</Format>
  <TextAlign>Right</TextAlign>
  <VerticalAlign>Middle</VerticalAlign>
  <PaddingLeft>2pt</PaddingLeft>
  <PaddingRight>2pt</PaddingRight>
  <PaddingTop>1pt</PaddingTop>
  <PaddingBottom>1pt</PaddingBottom>
  <WritingMode>Horizontal</WritingMode>
</Style>
```

### Values

| Child | Values |
| --- | --- |
| `Border/Style` and the four sided variants | `None`, `Dotted`, `Dashed`, `Solid`, `Double`, `Groove`, `Ridge`, `Inset`, `WindowInset`, `Outset` |
| `Border/Width` | a size, e.g. `1pt`; only `1pt` renders reliably in Excel |
| `BackgroundColor`, `Color`, `Border/Color` | a named .NET colour (`Black`, `Firebrick`, `WhiteSmoke`) or `#RRGGBB` |
| `BackgroundImage` | an `ImageSource` of `External`, `Embedded` or `Database`, plus `Value` and `BackgroundRepeat` |
| `FontFamily` | a font name; it must be installed on the report server, not the client |
| `FontSize` | a size in `pt`; between `1pt` and `200pt` |
| `FontWeight` | `Lighter`, `Normal`, `Bold`, `Bolder`, or `100` through `900` |
| `FontStyle` | `Normal`, `Italic` |
| `TextDecoration` | `None`, `Underline`, `Overline`, `LineThrough` |
| `TextAlign` | `General`, `Left`, `Center`, `Right`, `Justify` |
| `VerticalAlign` | `Top`, `Middle`, `Bottom` |
| `Padding*` | a size; padding is inside the border |
| `WritingMode` | `Horizontal`, `Vertical`, `Rotate270` |
| `Direction` | `LTR`, `RTL` |
| `Language` | overrides `Report/Language` for this item's formatting |
| `Format` | a .NET format string, see `expressions.md` |
| `LineHeight` | a size |

The four sided border elements (`TopBorder`, `BottomBorder`, `LeftBorder`,
`RightBorder`) override `Border` for that edge. Their child order is `Color`,
`Style`, `Width`, and each accepts an expression.

Every `Style` child can be an expression, which is how conditional formatting
works:

```xml
<BackgroundColor>=IIf(RowNumber(Nothing) Mod 2 = 0, "WhiteSmoke", "White")</BackgroundColor>
```

### Style on a cell

Cell styling is applied to the `Textbox` inside `CellContents`, not to the
`TablixCell`. The `TablixCell` has no `Style`. This is the reason a border
applied "to the cell" does not appear: it has to go on the textbox.

## Growth behaviour

| Element | On | Effect |
| --- | --- | --- |
| `CanGrow` | `Textbox` | the textbox grows taller to fit wrapped text |
| `CanShrink` | `Textbox` | the textbox shrinks when the text is shorter than `Height` |
| `KeepTogether` | `Tablix`, `Rectangle`, `TablixMember` | try to render the whole thing on one page |
| `HideDuplicates` | `Textbox` | blank out repeated values within the named scope |
| `RepeatWith` | `Textbox`, `Image`, `Chart` | render this item next to the named data region on every page |

```xml
<Textbox Name="Description">
  <CanGrow>true</CanGrow>
  <CanShrink>false</CanShrink>
  <Paragraphs>...</Paragraphs>
  <Height>0.25in</Height>
  <Width>3in</Width>
</Textbox>
```

Behaviour notes.

- `CanGrow` grows the row containing the textbox, which grows the Tablix, which
  may push items below it. Items below a growing region are **not** pushed down:
  RDL has no reflow, so a growing Tablix overlaps whatever is beneath it.
  Leave vertical slack, or put the items below in a second `ReportSection`.
- `CanGrow` only grows vertically. There is no `CanGrowHorizontally`.
- `CanShrink` on a textbox with a fixed border produces uneven row heights.
  Prefer leaving it `false` for table cells.
- `KeepTogether` on a group member is a hint, not a guarantee. If the group is
  taller than a page it is broken anyway.
- `HideDuplicates` takes a scope name, not a boolean:
  `<HideDuplicates>RegionGroup</HideDuplicates>`.

## Z-order

`ZIndex` is an integer. Higher values render on top. The default is the document
order of the items within their container, so a later sibling is on top of an
earlier one.

```xml
<Rectangle Name="Backdrop">
  <ZIndex>0</ZIndex>
  ...
</Rectangle>
<Textbox Name="Overlay">
  <ZIndex>1</ZIndex>
  ...
</Textbox>
```

Two constraints.

- `ZIndex` only orders items within the same container. An item inside a
  `Rectangle` can never render above a sibling of that rectangle.
- Only the HTML and image and PDF renderers honour overlap correctly. Excel and
  CSV flatten it, which is another reason to avoid overlapping layouts.

## A worked layout

Letter portrait, 1in margins, a page header, a title, a Tablix and a chart.

```xml
<ReportSection>
  <Body>
    <ReportItems>
      <Textbox Name="Title">
        <Paragraphs><Paragraph><TextRuns><TextRun>
          <Value>Sales by region</Value>
          <Style><FontSize>16pt</FontSize><FontWeight>Bold</FontWeight></Style>
        </TextRun></TextRuns></Paragraph></Paragraphs>
        <Top>0in</Top><Left>0in</Left>
        <Height>0.35in</Height><Width>6.5in</Width>
      </Textbox>

      <Tablix Name="SalesTablix">
        <!-- body and hierarchies omitted -->
        <DataSetName>SalesData</DataSetName>
        <Top>0.5in</Top><Left>0in</Left>
        <Height>0.75in</Height><Width>4.5in</Width>
      </Tablix>

      <Chart Name="SalesChart">
        <!-- hierarchies and data omitted -->
        <DataSetName>SalesData</DataSetName>
        <Top>1.5in</Top><Left>0in</Left>
        <Height>3in</Height><Width>6.5in</Width>
      </Chart>
    </ReportItems>
    <Height>4.6in</Height>
  </Body>
  <Width>6.5in</Width>
  <Page>
    <PageHeader>
      <Height>0.4in</Height>
      <PrintOnFirstPage>true</PrintOnFirstPage>
      <PrintOnLastPage>true</PrintOnLastPage>
      <ReportItems>
        <Textbox Name="HeaderDate">
          <Paragraphs><Paragraph><TextRuns><TextRun>
            <Value>=Globals!ExecutionTime</Value>
            <Style><Format>yyyy-MM-dd HH:mm</Format></Style>
          </TextRun></TextRuns></Paragraph></Paragraphs>
          <Top>0.05in</Top><Left>4.5in</Left>
          <Height>0.25in</Height><Width>2in</Width>
          <Style><TextAlign>Right</TextAlign></Style>
        </Textbox>
      </ReportItems>
    </PageHeader>
    <PageFooter>
      <Height>0.35in</Height>
      <PrintOnFirstPage>true</PrintOnFirstPage>
      <PrintOnLastPage>true</PrintOnLastPage>
      <ReportItems>
        <Textbox Name="Pager">
          <Paragraphs><Paragraph><TextRuns><TextRun>
            <Value>="Page " &amp; Globals!PageNumber &amp; " of " &amp; Globals!TotalPages</Value>
          </TextRun></TextRuns></Paragraph></Paragraphs>
          <Top>0.05in</Top><Left>0in</Left>
          <Height>0.25in</Height><Width>6.5in</Width>
          <Style><TextAlign>Center</TextAlign></Style>
        </Textbox>
      </ReportItems>
    </PageFooter>
    <PageHeight>11in</PageHeight>
    <PageWidth>8.5in</PageWidth>
    <InteractiveHeight>0in</InteractiveHeight>
    <InteractiveWidth>8.5in</InteractiveWidth>
    <LeftMargin>1in</LeftMargin>
    <RightMargin>1in</RightMargin>
    <TopMargin>0.75in</TopMargin>
    <BottomMargin>0.75in</BottomMargin>
    <Columns>1</Columns>
    <ColumnSpacing>0.13in</ColumnSpacing>
  </Page>
</ReportSection>
```

Verification of that layout.

- `6.5 + 1 + 1 = 8.5` equals `PageWidth`. No blank pages.
- Widest item right edge: chart at `0 + 6.5 = 6.5`, equal to `Width`. Nothing
  overflows.
- Lowest item bottom edge: chart at `1.5 + 3 = 4.5`, and `Body/Height` is 4.6in.
  The body contains everything.
- Body space per page: `11 - 0.75 - 0.75 - 0.4 - 0.35 = 8.75in`.
