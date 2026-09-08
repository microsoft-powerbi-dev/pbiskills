# Rendering and Export

The renderer decides what your layout actually means. A design that is correct in
the browser preview can produce blank pages in PDF, merged cells in Excel and an
empty file in CSV. Choose the target formats during requirements intake, not
after the layout is built.

## Soft pages versus hard pages

SSRS paginates twice, with different rules.

**Soft page break renderers**: HTML, MHTML, Excel, Word.

- Page height comes from `InteractiveHeight` on the `ReportSection`'s `Page`
  element, not from the physical paper size.
- Content that exceeds the width simply keeps going; there is no horizontal
  pagination.
- Explicit `PageBreak` elements are honoured.

**Hard page break renderers**: PDF, Image/TIFF, Print.

- Page size comes from `PageHeight` and `PageWidth` minus the four margins.
- Content wider than the printable area is split onto additional physical pages
  to the right.
- Everything is positioned absolutely at the declared size.

```xml
<Page>
  <PageHeight>11.69in</PageHeight>
  <PageWidth>8.27in</PageWidth>
  <LeftMargin>0.5in</LeftMargin>
  <RightMargin>0.5in</RightMargin>
  <TopMargin>0.5in</TopMargin>
  <BottomMargin>0.5in</BottomMargin>
  <InteractiveHeight>0in</InteractiveHeight>
  <InteractiveWidth>8.27in</InteractiveWidth>
</Page>
```

`InteractiveHeight` set to `0in` means "one continuous page" in HTML: the whole
report scrolls as a single page. That is usually what browser users want. Setting
it to `11.69in` makes the HTML view paginate like the PDF, which is what you want
when users are checking a layout they will later print.

### The blank-page rule

This is the single most reported paginated-report defect.

```
usable width = PageWidth - LeftMargin - RightMargin
```

If `Body.Width` (or any report item's `Left + Width`) exceeds the usable width by
even 0.01in, every content page is followed by a blank page in PDF.

| Paper and orientation | Page width | Margins 0.5in each | Usable body width |
| --- | --- | --- | --- |
| A4 portrait | 8.27in | 1.0in | 7.27in |
| A4 landscape | 11.69in | 1.0in | 10.69in |
| Letter portrait | 8.5in | 1.0in | 7.5in |
| Letter landscape | 11in | 1.0in | 10in |
| Legal portrait | 8.5in | 1.0in | 7.5in |

Check the `ReportSection`'s own `Width` element, not just the visual canvas. The
canvas grows when you drag an item and does not shrink when you drag it back.

---

## PDF

The reference renderer. If it looks right in PDF, it will print right.

**Behaviour**

- Hard pagination against the physical page.
- Page headers and footers render on every page, subject to `PrintOnFirstPage`
  and `PrintOnLastPage`.
- `Globals!PageNumber` and `Globals!TotalPages` are meaningful.
- Fonts are embedded if licensing allows; if not, they are substituted, and
  substituted metrics change line wrapping.
- Interactive features (toggle, sort, drillthrough, tooltips, document map links
  to external reports) do nothing.

**Design rules**

- Keep body width inside the usable width. See the blank-page rule above.
- Use fonts that exist on the report server, not just on your workstation. Arial,
  Segoe UI, Calibri, Times New Roman are safe. A missing font is silently
  substituted at render time on the **server**.
- Set `<PrintOnFirstPage>` explicitly on page headers and footers; the default
  differs between designers.
- Put a page footer on every report:

  ```xml
  <TextRun><Value>="Page " &amp; Globals!PageNumber &amp; " of " &amp; Globals!TotalPages</Value></TextRun>
  ```

  This repository injects exactly that footer, plus `=Globals!ExecutionTime`,
  when a migrated report has none (`backend/app/core/paginated/enrichers.py`,
  rule A1).

**Pitfalls**

- `KeepTogether` on a Rectangle taller than one page silently has no effect.
- A Tablix with `RepeatOnNewPage` headers and a `KeepWithGroup` of `None` will
  orphan headers at page ends.
- Background images tile by default. Set `<BackgroundRepeat>Clip</BackgroundRepeat>`.

---

## Excel (XLSX)

The most-used export and the most fragile.

**Behaviour**

- The renderer maps the rendered layout onto a grid. Any two report items whose
  horizontal or vertical edges do not align produce **merged cells**, because the
  renderer has to introduce splitter rows and columns.
- Each explicit page break becomes a **new worksheet**. A report with a page
  break per region produces one tab per region.
- The worksheet name comes from the `PageName` property on the report, the
  `ReportSection`, or the group:

  ```xml
  <Group Name="grpRegion">
    <GroupExpressions><GroupExpression>=Fields!Region.Value</GroupExpression></GroupExpressions>
    <PageBreak><BreakLocation>End</BreakLocation></PageBreak>
    <PageName>=Fields!Region.Value</PageName>
  </Group>
  ```

- Page headers and footers become Excel **print** headers and footers, and Excel
  supports only simple text there. A header containing an image, a textbox with
  an expression, or more than one line often renders only its first textbox.
- A cell's value arrives as a real number **only if** the report cell has a
  `Format` string and the underlying value is numeric. A value produced by a
  string expression such as `=FormatCurrency(Fields!GrossSales.Value)` arrives as
  text and cannot be summed.
- Row height and column width are approximated from the RDL sizes.
- XLSX limits: 1,048,576 rows and 16,384 columns per sheet. The older XLS
  renderer caps at 65,536 rows and truncates silently.

**Design rules**

- Align every item on a grid. Snap tops, lefts, widths and heights of anything
  that shares a horizontal or vertical band. This is the single highest-value
  action for a clean Excel export.
- Never place a floating textbox beside a Tablix. Put it above or below, spanning
  the same width.
- Set `Format` on every numeric and date cell: `C2`, `N0`, `P1`, `yyyy-MM-dd`,
  `#,##0.00`. Never use `FormatCurrency`, `FormatNumber`, `FormatPercent` or
  `CStr` in the value expression.
- Keep the page header to one line of plain text, or accept that it will not
  survive.
- To exclude an item from Excel entirely, use render-format visibility:

  ```
  =IIf(Globals!RenderFormat.Name = "EXCELOPENXML", True, False)
  ```

- Give groups a `PageName` when you break per group, otherwise the tabs are
  `Sheet1`, `Sheet2` and so on.

---

## Word (DOCX)

**Behaviour**

- Soft pagination, but the document carries the physical page setup, so printing
  from Word matches the PDF closely.
- Report items become a nested table structure. Deeply nested rectangles produce
  deeply nested tables that are painful to edit.
- Page headers and footers become real Word headers and footers, with more
  fidelity than Excel.
- `Globals!TotalPages` is not available in the Word header/footer; Word
  recalculates page count itself.

**Design rules**

- Use Word export when the reader will edit the output (a letter, a draft
  report). If they will only read it, use PDF.
- Minimise nesting. Every Rectangle becomes a table.
- Avoid absolute-positioned overlapping items; they become text boxes anchored in
  odd places.

---

## CSV

CSV is a **data-only renderer**. It does not render layout at all; it walks the
report's data regions and emits their data.

**Behaviour**

- Only data regions are emitted: Tablix, Chart, Gauge, Map. Standalone textboxes
  in the body are emitted once, at the top, as a header block (in default mode).
- Two modes, selected by a device setting:

  | Mode | Device setting | Output |
  | --- | --- | --- |
  | Default | `Mode=Default` | Formatted, laid out with headers, one record per row, blank separator lines between peer regions |
  | Compliant | `Mode=Compliant` | Strict RFC 4180, one region only, no blank lines, suitable for machine consumption |

- Nested data regions produce a denormalised, repeated result: the parent columns
  repeat on each child row.
- Multiple peer data regions produce multiple blocks in Default mode. In
  Compliant mode you must ensure exactly one region is emitted.

**The `DataElementOutput` property**

Every report item has `DataElementOutput`, which controls whether the data
renderers (CSV and XML) emit it:

| Value | Meaning |
| --- | --- |
| `Auto` | Default. Textboxes with a constant value are omitted; everything else is emitted |
| `Output` | Always emit |
| `NoOutput` | Never emit |
| `ContentsOnly` | Emit the children but not this element (rectangles only) |

Use it to make a clean CSV without touching the visual layout:

```xml
<Tablix Name="tblSummaryChart">
  <DataElementOutput>NoOutput</DataElementOutput>
</Tablix>

<Tablix Name="tblDetail">
  <DataElementName>SalesDetail</DataElementName>
  <DataElementOutput>Output</DataElementOutput>
</Tablix>

<Textbox Name="txtReportTitle">
  <DataElementOutput>NoOutput</DataElementOutput>
</Textbox>
```

`DataElementName` sets the column/element name in the output, independent of the
textbox name.

**Design rules**

- If CSV is a delivery format, mark every decorative item `NoOutput` and every
  non-primary data region `NoOutput`. Then the CSV has exactly one clean block.
- Set `Format` strings carefully: CSV emits the **formatted** value in Default
  mode. A `C2` format emits `$1,234.56`, which downstream parsers hate. For
  machine consumption use Compliant mode, which emits the underlying value.
- Never rely on column order matching the visual order after a layout edit.
  Pin it with `DataElementName` on every emitted textbox.

---

## XML

Also a data-only renderer, driven by the same `DataElementOutput` and
`DataElementName` properties, plus `DataElementStyle` (`Attribute` or `Element`)
which controls whether values become XML attributes or child elements.

**Behaviour**

- The report root element name comes from the report's `DataElementName`, or the
  report file name.
- Hierarchy follows the report item nesting, so nested groups become nested XML,
  which CSV cannot express. This is the main reason to prefer XML over CSV for
  master-detail extracts.
- An XSLT can be applied at render time via the `XSLT` device setting, so the
  report can emit an arbitrary schema.

```xml
<Report ...>
  <DataElementName>MonthlySales</DataElementName>
  <DataElementStyle>ElementNormal</DataElementStyle>
</Report>
```

---

## MHTML

A single-file web archive: HTML plus embedded images, base64-encoded.

**Behaviour**

- Soft pagination, so `InteractiveHeight` applies.
- Everything is in one file, which makes it the right choice for **email body**
  delivery in a subscription (as opposed to an attachment).
- Interactive features do not work; it is a snapshot of the current toggle state.
- Rendering is heavier than HTML because images are inlined.

**Design rule**: keep it narrow. An MHTML email body wider than about 800px is
horizontally scrolled in most mail clients, and Outlook's rendering engine is
Word's, so complex CSS positioning degrades.

---

## Image and TIFF

**Behaviour**

- Hard pagination, identical page geometry to PDF.
- Multi-page TIFF is the default output; single-page formats (PNG, JPEG, BMP,
  GIF, EMF) are selected by the `OutputFormat` device setting and emit only the
  page selected by `StartPage` / `EndPage`.
- Fonts must be present on the server, and are rasterised, so anti-aliasing
  quality depends on `DpiX` / `DpiY` device settings (default 96; use 200 or 300
  for archival).

**Use it for**: archival systems and fax gateways that require TIFF. Nothing
else.

---

## Format, constraints, design rules

| Format | Pagination | Key constraints | Design rules |
| --- | --- | --- | --- |
| PDF | Hard, physical page | Body width must fit usable width; fonts must exist on the server; no interactivity | Verify width arithmetic; embed-safe fonts; page footer with `Globals!PageNumber` |
| Excel XLSX | Soft; page break becomes a worksheet | Misaligned items produce merged cells; headers/footers are print-only and near-plain-text; numbers stay numeric only with a `Format` string; 1,048,576 row cap | Align everything on a grid; `Format` strings everywhere; no `FormatCurrency`; set `PageName` per group |
| Excel XLS (legacy) | Soft | 65,536 row cap, truncates silently | Do not use; migrate to XLSX |
| Word DOCX | Soft, carries page setup | Nested rectangles become nested tables; `Globals!TotalPages` unavailable in header | Flatten nesting; use only when the reader edits the output |
| CSV | None (data only) | Only data regions emitted; Default mode emits formatted strings; multiple regions produce multiple blocks | Mark decorative items `NoOutput`; one data region; use Compliant mode for machines |
| XML | None (data only) | Same `DataElementOutput` rules; nesting preserved | Set `DataElementName` on everything emitted; consider an XSLT |
| MHTML | Soft | Single file, no interactivity, mail-client CSS limits | Keep under about 800px wide for email bodies |
| Image / TIFF | Hard, physical page | Fonts rasterised at `DpiX`/`DpiY`; single-page formats need `OutputFormat` plus `StartPage` | Use 200 to 300 dpi for archival; only for systems that demand TIFF |
| HTML | Soft, `InteractiveHeight` | The only format with working toggle, sort and drillthrough | Set `InteractiveHeight` to `0in` for continuous scrolling |

---

## Designing for more than one format

Most reports are viewed in HTML, emailed as PDF and exported to Excel. To satisfy
all three:

1. **Align on a grid.** Every item's `Left`, `Top`, `Width` and `Height` should
   share edges with its neighbours. Serves Excel, harms nothing.
2. **Body width inside the usable width.** Serves PDF, harms nothing.
3. **`Format` strings, never format functions.** Serves Excel and CSV, harms
   nothing.
4. **Headers inside the Tablix.** Serves Excel and PDF page repetition.
5. **`InteractiveHeight` set to `0in`.** Serves HTML.
6. **Page breaks on groups, with `PageName`.** Serves Excel tabs and PDF
   sections simultaneously.
7. **Render-format-aware visibility for the parts that only make sense in one
   format.** For example, expand drilldown for non-interactive output and hide a
   chart from CSV.

Test all three every time. A report is not reviewed until a PDF, an XLSX and a
CSV of the same execution have been opened.

### Render-format expressions

```
=Globals!RenderFormat.Name                 ' "PDF", "EXCELOPENXML", "CSV", "HTML4.0", "WORDOPENXML"
=Globals!RenderFormat.IsInteractive        ' True for HTML only
=Globals!RenderFormat.DeviceInfo!Toolbar   ' device settings, when supplied
```

Common uses:

```xml
<!-- expand drilldown on export -->
<Hidden>=Globals!RenderFormat.IsInteractive</Hidden>

<!-- hide a chart from data renderers and Excel -->
<Hidden>=InStr("CSV|XML|EXCELOPENXML", Globals!RenderFormat.Name) &gt; 0</Hidden>
```

---

## URL access parameters

The report server accepts render instructions on the query string. Prefix
meanings:

| Prefix | Scope | Examples |
| --- | --- | --- |
| `rs:` | Report server command | `rs:Command`, `rs:Format`, `rs:ParameterLanguage`, `rs:Snapshot` |
| `rc:` | Renderer device settings | `rc:Toolbar`, `rc:Zoom`, `rc:Parameters`, `rc:OutputFormat`, `rc:Mode` |
| none | Report parameters | `Region=Southeast&DateFrom=2026-01-01` |

Render a report straight to PDF, no toolbar, with parameters:

```
http://ssrs01:8090/ReportServer?/Sales/Monthly+Sales+by+Region
  &rs:Command=Render
  &rs:Format=PDF
  &rc:Toolbar=False
  &DateFrom=2026-07-01
  &DateTo=2026-07-31
  &Region=Southeast
  &Region=Great+Lakes
```

A multi-value parameter is expressed by repeating the name, as shown for
`Region`.

Excel with a specific worksheet name:

```
&rs:Format=EXCELOPENXML
```

CSV in compliant mode, comma delimited, with a header row:

```
&rs:Format=CSV
&rc:Mode=Compliant
&rc:FieldDelimiter=,
&rc:NoHeader=False
&rc:Qualifier="
```

Image at 300 dpi, page 2 only, as PNG:

```
&rs:Format=IMAGE
&rc:OutputFormat=PNG
&rc:DpiX=300&rc:DpiY=300
&rc:StartPage=2&rc:EndPage=2
```

Useful `rs:Command` values: `Render` (default for reports), `ListChildren`,
`GetDataSourceContents`, `GetResourceContents`.

`rs:ClearSession=true` forces a fresh execution rather than reusing the cached
session, which matters when scripting a loop of renders with different
parameters.

PowerShell, saving a PDF with Windows credentials:

```powershell
$url = "http://ssrs01:8090/ReportServer?/Sales/Monthly+Sales+by+Region" +
       "&rs:Command=Render&rs:Format=PDF&rc:Toolbar=False" +
       "&DateFrom=2026-07-01&DateTo=2026-07-31&Region=Southeast"

Invoke-WebRequest -Uri $url -UseDefaultCredentials `
    -AllowUnencryptedAuthentication `
    -OutFile "C:\temp\MonthlySales.pdf"
```

`-AllowUnencryptedAuthentication` is required by PowerShell 7 when sending
Windows credentials over plain HTTP, as noted in `docs/ssrs-lab-setup.md`.

---

## REST v2.0 export endpoint

SSRS 2017+ and Power BI Report Server expose export through the same
`/api/v2.0` surface this repository uses for the catalog
(`backend/app/core/report_server/rest_api.py`).

The catalog item id comes from the `Id` property of a `GET /CatalogItems` row.
Export is then:

```
GET {api_base}/Reports({id})/Export(Format='PDF')
```

Supported `Format` values match the renderer names: `PDF`, `EXCELOPENXML`,
`WORDOPENXML`, `CSV`, `XML`, `MHTML`, `IMAGE`.

curl with Negotiate auth:

```bash
API="https://ssrs01.contoso.com/Reports/api/v2.0"
ID="9d3b4c2e-5f11-4a7c-9c2d-70a3e8b21f04"

curl --negotiate -u : \
     -H "Accept: application/octet-stream" \
     -o MonthlySales.pdf \
     "${API}/Reports(${ID})/Export(Format='PDF')"
```

PowerShell with NTLM:

```powershell
$api  = "https://ssrs01.contoso.com/Reports/api/v2.0"
$id   = "9d3b4c2e-5f11-4a7c-9c2d-70a3e8b21f04"
$cred = Get-Credential                      # CONTOSO\svc_pbi

Invoke-WebRequest -Uri "$api/Reports($id)/Export(Format='PDF')" `
    -Credential $cred `
    -OutFile "MonthlySales.pdf"
```

Two limits to know:

- The export endpoint runs the report with its **default** parameter values. To
  supply parameters, use URL access (`rs:Format`) against the web service
  directory instead, or create a linked report with the parameters baked in.
- Large exports stream; set a generous client timeout. This repository's
  connector uses `report_server_timeout` (default 30 seconds) per request, which
  is sized for catalog operations rather than for rendering a 10,000-page PDF.
