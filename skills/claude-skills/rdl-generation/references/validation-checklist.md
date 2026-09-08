# RDL Pre-flight Validation Checklist

Work through this in order. Each stage assumes the one before it passed, because
a failure early on makes the later checks meaningless. Stages 1 to 5 are things
you can verify with an XML parser and arithmetic. Stage 6 is the static-analysis
rule set this repository actually implements.

## Stage 1: structural

- [ ] **The file is well-formed XML.** `ET.parse` succeeds. If it fails at
      "line 1, column 0" on a file downloaded from a report server, strip
      trailing `\x00` padding first (see `sanitize_rdl_bytes`).
- [ ] **There is exactly one root and it is `Report`.** Compare by *local* name,
      because the tag is namespaced. Anything else is the blocker
      `not_a_report`.
- [ ] **A recognised RDL namespace is declared as the default namespace on
      `Report`.** One of the three in `_KNOWN_RDL_NAMESPACES`. A missing or
      unrecognised namespace is `unknown_namespace`, severity `warn`, and in
      practice means the server will refuse the upload.
- [ ] **No RDL element carries a prefix.** If every tag serialised as `ns0:`,
      the default namespace was not registered before writing.
- [ ] **The body exists.** `Report/ReportSections/ReportSection/Body` for RDL
      2010 and 2016, or `Report/Body` for 2008. Neither present is the blocker
      `no_body`.
- [ ] **`ReportSection/Width` (2010/2016) or `Report/Width` (2008) is present and
      carries a unit.**
- [ ] **`Body/Height` is present, carries a unit, and is not `0in`.** A zero
      height renders a blank report.
- [ ] **`Body/ReportItems` contains at least one report item.** Zero renderable
      items is the blocker `no_report_items`. The item tags that count are
      `Tablix`, `Chart`, `Textbox`, `Image`, `Rectangle`, `Gauge`, `Map`,
      `Subreport`, `List`.
- [ ] **`PageHeader` and `PageFooter`, if present, are children of `Page`.** Not
      of `ReportSection`, not of `Report` (in 2008 and later).
- [ ] **Page header and footer contain no data regions.** Only `Textbox`,
      `Image`, `Line` and `Rectangle` are legal there.

## Stage 2: reference integrity

- [ ] **Every `Query/DataSourceName` resolves to a `DataSource/@Name`.** Exact
      match, case sensitive.
- [ ] **Every data region's `DataSetName` resolves to a `DataSet/@Name`.** Check
      `Tablix`, `Chart`, `Gauge`, `Map` and `List`.
- [ ] **Every `DataSet` has a `Query` with non-empty `CommandText`.** Empty is
      `dataset_no_query`, severity `warn`. A shared dataset is the legitimate
      exception.
- [ ] **Every `DataSet` declares at least one `Field`.** Zero is
      `dataset_no_fields`, severity `warn`.
- [ ] **Every `Field/@Name` is a CLS-compliant identifier.** The pattern is
      `^[A-Za-z_]\w*$`. A space, hyphen or leading digit fails the upload with
      "Field names must be CLS-compliant identifiers" (SSRS error 1027). This is
      the blocker `field_name_not_cls_compliant`. Put the real column name in
      `DataField` instead.
- [ ] **Every `Fields!X.Value` in any expression resolves to a declared
      `Field/@Name`.** Both `Fields!Name.Value` and `Fields![Name].Value` count.
      Unresolved names are the blocker `unknown_field_reference` and are the
      cause of a report where every cell shows `#Error`.
- [ ] **Every `Parameters!P.Value` and `Parameters!P.Label` resolves to a
      `ReportParameter/@Name`.**
- [ ] **Every `QueryParameter/Value` that references a parameter names a
      declared one.** This repository's parser reports the mismatch as a
      warning: "query parameter '@X' references unknown ReportParameter 'Y'".
- [ ] **Every `@Name` token in a `CommandText` has a matching `QueryParameter`.**
      A missing binding renders as "Must declare the scalar variable '@X'".
- [ ] **Every parameter used in a cascading `ValidValues/DataSetReference` is
      declared *before* the parameter that consumes it.** Forward dependencies
      are rejected at publish time.
- [ ] **Every aggregate scope argument names a real dataset, group or data
      region.** Scope names are string literals and are case sensitive.
- [ ] **Every `ReportItems!X.Value` names a real `Textbox/@Name`.**
- [ ] **Every `Subreport/ReportName` is a path that exists on the target
      server.** It is a catalogue path, not a file path. A missing child
      publishes fine and fails on first render.
- [ ] **Every `ToggleItem` names a textbox that is a peer or ancestor of the
      hidden item.**
- [ ] **Every `Code.` call has a matching member in `Report/Code`.**

## Stage 3: element order

- [ ] `Query`: `DataSourceName`, `CommandType`, `CommandText`, `QueryParameters`,
      `Timeout`. Out of order gives "The element 'Query' has invalid child
      element 'CommandText'", which names the wrong element.
- [ ] `ReportParameter`: `DataType`, `Nullable`, `DefaultValue`, `AllowBlank`,
      `Prompt`, `PromptUser`, `Hidden`, `MultiValue`, `ValidValues`,
      `UsedInQuery`.
- [ ] `ReportSection`: `Body`, `Width`, `Page`.
- [ ] `Body`: `ReportItems`, `Height`, `Style`.
- [ ] `TablixBody`: `TablixColumns`, then `TablixRows`.
- [ ] `TablixMember`: `Group`, `SortExpressions`, `TablixHeader`,
      `TablixMembers`, then the flags (`RepeatOnNewPage`, `KeepWithGroup`, ...).
- [ ] `ChartMember`: `Group`, `SortExpressions`, `ChartMembers`, `Label`. The
      label goes last, after the group.
- [ ] `ConnectionProperties`: `DataProvider`, `ConnectString`,
      `IntegratedSecurity`, `Prompt`.
- [ ] `Field`: `DataField` or `Value`, then `rd:TypeName`.
- [ ] `Report`: `Description`, `Author`, `AutoRefresh`, `DataSources`,
      `DataSets`, `ReportSections`, `ReportParameters`, `Code`,
      `EmbeddedImages`, `Language`, `CodeModules`, `Classes`, ...

## Stage 4: units

- [ ] **Every `Width`, `Height`, `Top`, `Left`, `Size`, `PageWidth`,
      `PageHeight`, `*Margin`, `ColumnSpacing`, `FontSize`, `Padding*`,
      `LineHeight` and `Border/Width` carries a unit.** A regular expression that
      finds violations: match the text of those elements against
      `^\s*[0-9]*\.?[0-9]+\s*$`. Any hit is unit-less.
- [ ] **The unit is one of `in`, `cm`, `mm`, `pt`, `pc`.** `px` is not valid RDL.
- [ ] **`pc` is avoided** if the document will pass through this repository:
      `rdl_xml._SIZE_RE` does not recognise picas and returns the fallback
      default instead.
- [ ] **`FontSize` uses `pt`.** Other units are legal but render inconsistently.

## Stage 5: layout

- [ ] **Blank-page arithmetic.** `Width + LeftMargin + RightMargin <= PageWidth`.
      Exceeding it inserts an empty page after every content page.
- [ ] **Nothing overflows the body horizontally.**
      `max(Left + Width)` over `Body/ReportItems` is less than or equal to
      `ReportSection/Width`.
- [ ] **`Body/Height` covers the content.** `max(Top + Height)` over
      `Body/ReportItems` is less than or equal to `Body/Height`.
- [ ] **`Body/Height` is not zero.**
- [ ] **Vertical page budget.**
      `PageHeight - TopMargin - BottomMargin - PageHeader/Height - PageFooter/Height`
      is positive and large enough for at least one Tablix row.
- [ ] **Tablix arity, columns.** `count(TablixColumn)` equals
      `count(TablixCell)` in every `TablixRow`, and equals the number of *leaf*
      `TablixMember` elements in `TablixColumnHierarchy`.
- [ ] **Tablix arity, rows.** `count(TablixRow)` equals the number of leaf
      `TablixMember` elements in `TablixRowHierarchy`.
- [ ] **Chart required children.** Every `Chart` has `ChartCategoryHierarchy`,
      `ChartSeriesHierarchy` and `ChartData`. Missing any is the blocker
      `chart_missing_required_element`.
- [ ] **Chart series arity.** A `ChartSeriesHierarchy` with no grouped member
      needs one `ChartSeries` per `ChartMember`. One with a grouped member needs
      exactly one `ChartSeries`. A mismatch is the blocker `chart_series_arity`.
- [ ] **Every leaf `ChartMember` has a `Label`.** Missing is the blocker
      `chart_member_missing_label`.
- [ ] **Every `ReportParameter` has a `DefaultValue` or is `Nullable`.**
      Otherwise unattended rendering fails with `rsReportParameterValueNotSet`.
      This is `parameter_without_default`, severity `warn`.
- [ ] **A `String` parameter whose default may be blank has
      `<AllowBlank>true</AllowBlank>`.**
- [ ] **Every multi-value `DefaultValue/Values/Value` appears in `ValidValues`.**
      A mismatch makes Report Designer throw a `NullReferenceException` on open.

## Stage 6: the compatibility rules this repository implements

These come from `backend/app/core/paginated/compatibility.py`, function `detect`
and its helper `_detect_review_findings`. Each is emitted as a `Finding` with a
severity, a code, a message and a locator.

### Power BI paginated feature support (`detect`)

| Code | Severity | Locator | What it means |
| --- | --- | --- | --- |
| `subreport` | `blocker` | the `Subreport/@Name` | "Subreports have no Power BI paginated equivalent; re-model as drill-through." Triggered by any `Subreport` element at any depth. |
| `shared_datasource` | `warn` | the `DataSource/@Name` | "Shared data source must be embedded into the report for Power BI." Triggered by a `DataSource` with a direct `DataSourceReference` child. |
| `shared_dataset` | `warn` | the `DataSet/@Name` | "Shared dataset must be embedded into the report for Power BI." Triggered by a `DataSet` with a direct `SharedDataSet` child. |
| `custom_code` | `warn` | `Report/Code` | "Custom code / code modules are not supported in Power BI paginated reports." Triggered by any `CodeModules` or `Code` element. Emitted once for the whole report. |
| `custom_report_item` | `warn` | the `CustomReportItem/@Type` | "Custom report item may not render in Power BI; validate after conversion." |
| `map` | `info` | the `Map/@Name` | "Map report item: validate spatial data sources after re-pointing." |
| `expression_query` | `warn` | `Query/CommandText` | "Expression-based query text must be re-validated against the new data source." Triggered when a `CommandText` begins with `=` after left-stripping. |

`subreport` is the only blocker in this group. A report that trips it will not
run as a Power BI paginated report at all.

### Query-quality review rules (`_detect_review_findings`)

These scan every `Query/CommandText`. They are quality and performance smells,
not blockers.

| Code | Severity | Pattern | What it means |
| --- | --- | --- | --- |
| `select_star` | `warn` | `SELECT\s+\*` | "Query uses SELECT * , projects unused columns and blocks query optimisation. List only the columns the report actually consumes." |
| `select_distinct` | `info` | `SELECT\s+DISTINCT\b` | "SELECT DISTINCT masks duplicates that may indicate a join defect; verify the join keys before shipping." |
| `leading_wildcard` | `warn` | `LIKE\s+'\s*%` | "LIKE '%...' forces a table scan; consider a full-text index or a trailing-wildcard match." |
| `cross_join` | `warn` | `\bCROSS\s+JOIN\b` | "CROSS JOIN produces a Cartesian product; confirm the row multiplication is intentional." |
| `cursor_query` | `warn` | `\bDECLARE\b[^;]*\bCURSOR\b` | "T-SQL cursor detected, rewrite as a set-based query for Power BI paginated performance." |

All five are case-insensitive. `cursor_query` also matches across newlines.

### Expression-complexity review rules

These scan the text of every element whose local name is `Value`.

| Code | Severity | Trigger | What it means |
| --- | --- | --- | --- |
| `nested_iif` | `warn` | `IIF\s*\([^)]*IIF\s*\([^)]*IIF\s*\(` | "Three-or-more nested IIF(...), rewrite as SWITCH() for readability and perf." |
| `chained_replace` | `info` | three or more matches of `\bReplace\s*\(` in one expression | "Chained Replace() calls (3+); prefer a single regex or a normalisation column upstream." |

### Data-source and structural rules

| Code | Severity | Trigger | What it means |
| --- | --- | --- | --- |
| `integrated_security` | `info` | `ConnectString` matches `Integrated\s*Security\s*=\s*(true\|sspi)`, or `IntegratedSecurity` is `true` | "Data source uses Integrated Security, Fabric paginated requires an on-premises data gateway with a service account that has read access." |
| `many_tablix` | `info` | more than 5 `Tablix` elements | "N Tablix report items; consider splitting into multiple reports for maintainability." |
| `many_chart` | `info` | more than 5 `Chart` elements | "N Chart report items; consider consolidating series or splitting the report." |

Note that `integrated_security` fires per `DataSource` and per
`ConnectionProperties`, so a report with one data source can produce one finding
while a report with four produces four.

### Severity semantics

| Severity | Constant | Meaning |
| --- | --- | --- |
| `blocker` | `SEV_BLOCKER` | the target will not accept or will not run the report |
| `warn` | `SEV_WARN` | the report runs but needs manual attention |
| `info` | `SEV_INFO` | advisory, no action required to ship |

`PaginatedReport.has_blockers` is true when any finding has severity `blocker`,
and `TargetResult.supported` is false in the same condition. That is the gate
between "converted" and "converted with warnings" in this repository's pipeline.

## Stage 7: the generated-RDL validator

`backend/app/core/paginated/validate.py` answers a different question: will SSRS
*and* Power BI paginated both accept a document this tool just generated. It
emits the codes below in addition to everything in stage 6. Run it with
`validate_rdl(path)`; it never raises.

| Code | Severity | Meaning |
| --- | --- | --- |
| `parse_error` | `blocker` | the file is not well-formed XML; both targets fail |
| `not_a_report` | `blocker` | the root element is not `Report` |
| `unknown_namespace` | `warn` | the namespace is not one of the three recognised RDL namespaces |
| `no_body` | `blocker` | neither `Body` nor `ReportSections` under `Report` |
| `no_datasets` | `warn` | the report defines no datasets and will render empty |
| `dataset_no_fields` | `warn` | a named dataset declares no fields |
| `dataset_no_query` | `warn` | a named dataset has no query text |
| `no_report_items` | `blocker` | the body contains no renderable report item |
| `unknown_field_reference` | `blocker` | expressions reference fields no dataset declares (first 10 listed) |
| `field_name_not_cls_compliant` | `blocker` | a `Field/@Name` is not `^[A-Za-z_]\w*$` |
| `parameter_without_default` | `warn` | a parameter has neither a default nor `Nullable` |
| `chart_missing_required_element` | `blocker` | a chart lacks one of the three mandatory children |
| `chart_series_arity` | `blocker` | series count does not match the series hierarchy |
| `chart_member_missing_label` | `blocker` | one or more leaf `ChartMember` elements have no `Label` |
| `roundtrip_failed` | `warn` | the repository's own parser could not re-read the file |
| `roundtrip_no_datasources` | `warn` | re-parsing found no datasets |
| `roundtrip_no_visuals` | `info` | re-parsing found no recognised visuals |

The round-trip codes are the practical test of the "round-trip safe forms" rule
in `expressions.md`. `roundtrip_no_visuals` on a report that clearly has a Tablix
means the expressions inside it are not in the canonical
`=Fields!X.Value` / `=Sum(Fields!X.Value)` shape.

## Error message to root cause

| Message | Root cause | Fix |
| --- | --- | --- |
| `not well-formed (invalid token): line 1, column 0` | trailing `\x00` padding on a server download, or a BOM read as text | read bytes and `rstrip(b"\x00")`, as `sanitize_rdl_bytes` does |
| `The element 'Query' has invalid child element 'CommandText'` | `CommandType` after `CommandText`, or `DataSourceName` missing | reorder to `DataSourceName`, `CommandType`, `CommandText` |
| `The report definition has an invalid target namespace '...' which cannot be upgraded` | the namespace is newer than the target server | downgrade to 2010/01 or 2008/01, or upgrade the server |
| `A field in the dataset 'X' has the name 'Y'. Field names must be CLS-compliant identifiers` | a space, hyphen or leading digit in `Field/@Name` | sanitise `@Name`, keep the real spelling in `DataField` |
| `The expression used for the calculated field 'X' includes an aggregate` | an aggregate inside `Field/Value` | move the aggregate to the cell that uses it |
| `'Chart' is empty ... missing a mandatory child element of type 'ChartSeriesHierarchy'` | the chart omits one of the three mandatory children | emit all three, even for a single static series |
| `'ChartMember' is empty ... missing a mandatory child element of type 'Label'` | a leaf `ChartMember` with a `Group` but no `Label` | add `<Label>` after `<Group>` |
| `contains a different number of ChartSeries elements than the number of StaticSeries elements` | series arity mismatch | one `ChartSeries` per static `ChartMember`, or exactly one when the series hierarchy is grouped |
| `The 'AllowBlank' property of report parameter 'X' is false. However, the 'DefaultValue' property contains a value that violates...` | a `String` parameter with a blank default and no `AllowBlank` | add `<AllowBlank>true</AllowBlank>` |
| `rsReportParameterValueNotSet` | a parameter with no default and not `Nullable`, rendered unattended | add a `DefaultValue`, or set `Nullable` and give a null default |
| `The report parameter 'X' has a DefaultValue or ValidValues that depends on the report parameter 'Y'. Forward dependencies are not valid` | the child parameter is declared before its parent | reorder `ReportParameters` so parents come first |
| `NullReferenceException` when opening a parameterised report in Report Designer | a multi-value default that is not in `ValidValues`, or a list stringified into one `<Value>` | emit one `<Value>` per entry and make each appear in `ValidValues` |
| `Must declare the scalar variable '@X'` | a `@X` in `CommandText` with no matching `QueryParameter` | add the `QueryParameter` binding |
| Every cell renders `#Error` | the field name in the expression does not match a `Field/@Name` (case sensitive) | fix the name, or declare the field |
| One cell renders `#Error` | divide by zero or a null in arithmetic | guard with `IIf` and a safe denominator |
| Report opens blank | `Body/Height` is `0in`, or every item's `Top` exceeds `Body/Height` | set a real body height and check the item offsets |
| Blank page between every page | `Width + LeftMargin + RightMargin` exceeds `PageWidth` | shrink the body or the margins |
| Elements serialise as `ns0:Report` | the default namespace was not registered before writing | `ET.register_namespace("", _NS)` before `tree.write` |
| Header row does not repeat on page 2 | `RepeatOnNewPage` is set but `KeepWithGroup` is `None` | set `KeepWithGroup` to `After` on the header member |
| Cell borders do not appear | `Style` applied to `TablixCell` instead of the `Textbox` inside `CellContents` | move the `Style` onto the textbox |
| Excel export has spurious merged cells | overlapping or misaligned report items | align items to a shared grid, remove overlaps |
| Server accepts the file, Report Builder rejects it | a 2010-or-later element in a document declaring the 2008 namespace | upgrade the namespace, or remove the element |
| `The data extension X is either not registered or not supported` | a `DataProvider` value the target server does not have | check `rsreportserver.config` under `<Data><Extensions>` |
| `Login failed for user 'NT AUTHORITY\ANONYMOUS LOGON'` | integrated security plus a remote data source without Kerberos delegation | fix the SPNs, or switch to stored credentials |

## Running the checks

```powershell
# from the repo root
$env:PYTHONPATH = (Get-Location).Path ; $env:ENVIRONMENT = "development"
.\.venv\Scripts\python.exe -c "from app.core.paginated.validate import validate_rdl; import json; print(json.dumps(validate_rdl(r'path\to\report.rdl').to_dict(), indent=2))"
```

`RdlValidation.ok` is true only when every target is supported.
`RdlValidation.markdown()` renders the same result as a report, and
`summary_lines()` produces one line per target for a job log.

The skill also ships `scripts/validate_rdl.py`, which is dependency-free and
covers stages 1 through 5 without importing this repository.
