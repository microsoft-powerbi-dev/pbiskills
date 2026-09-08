# Playbook: generating RDL files

A vendor-neutral reference for writing SSRS / Power BI Paginated `.rdl` XML
from scratch. Full detail: `skills/claude-skills/rdl-generation/`.

## Scope

Use this when a specification, a SQL query, or a data model needs to become an
`.rdl` file, or when generated RDL is rejected by Report Builder, Visual
Studio, or a report server and the cause is in the XML.

## The five rules that prevent most failures

1. Element order is significant. RDL validates against an XSD using
   `xsd:sequence`, not `xsd:all`. Inside `Query`, `DataSourceName` precedes
   `CommandType`, which precedes `CommandText`. A missing or misordered
   element produces an "unexpected element" error naming the element that
   follows the mistake, not the mistake itself.
2. Read namespace-agnostically, write namespace-explicitly. When parsing,
   navigate by local tag name (strip the `{namespace}` prefix) so RDL 2008,
   2010, and 2016 all parse with the same code. When generating, register the
   default namespace and the `rd` prefix before serialising, or every element
   comes out as `ns0:Something`.
3. Every measurement carries a unit: `<Width>3in</Width>`, never
   `<Width>3</Width>`. Some tools tolerate a bare number, the report server
   does not.
4. Emit expressions in the canonical forms `=Fields!Name.Value` and
   `=Sum(Fields!Amount.Value)`. These are the forms most RDL parsers (and this
   repository's own parser) read back reliably. Equivalent but differently
   written expressions may not round-trip through tooling that expects the
   canonical shape.
5. `PageHeader` and `PageFooter` are children of `ReportSection/Page` in RDL
   2016, not of `Report`. Putting them at the report level is the single most
   common structural error in hand-written 2016 RDL.

## Minimum viable document

```
Report
  DataSources / DataSource / ConnectionProperties (DataProvider, ConnectString)
  DataSets / DataSet / Query (DataSourceName, CommandType, CommandText)
             / Fields / Field (Name, DataField, rd:TypeName)
  ReportParameters / ReportParameter (optional)
  ReportSections / ReportSection / Body / ReportItems / Tablix
                                  / Width
                                  / Page / PageHeight / PageWidth / margins
```

## Workflow

1. Pick the target schema version. Default to 2016
   (`http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition`)
   unless the target server is older.
2. Resolve the data before writing XML. Run the query or read the DDL first.
   A guessed field name produces a report that renders `#Error` in every cell
   that references it, and the error only surfaces when someone opens the
   report.
3. Build DataSources, then DataSets, then ReportParameters, then the body.
4. Validate before handing the file over: well-formed XML, correct namespace,
   every `Fields!X.Value` resolves to a declared `Field`, every `DataSetName`
   resolves to a declared `DataSet`, every measurement has a unit,
   `PageHeader`/`PageFooter` are inside `Page`, `Body/Height` is nonzero.

## Common failures

| Symptom | Cause |
| --- | --- |
| "invalid child element 'CommandText'" | `CommandType` came after `CommandText`, or `DataSourceName` is missing |
| Every cell renders `#Error` | Expression references a field name that has no matching `Field` declaration |
| Report opens blank | `Body/Height` is `0in`, or an item's `Top` exceeds the body height |
| Elements serialise as `ns0:Report` | Namespace not registered before writing |
| Blank page after every real page | `Body/Width` plus left and right margins exceeds `PageWidth` |
| `ParseError: not well-formed` on a server-downloaded file | Trailing null-byte padding was not stripped before parsing |

## Where the detail lives

`skills/claude-skills/rdl-generation/references/`: `rdl-structure.md`
(document skeleton and element order), `datasets-parameters.md` (DataSource,
DataSet, Field, ReportParameter XML), `tablix-and-charts.md` (Tablix and Chart
object models, grouping, totals), `expressions.md` (VB.NET expression syntax
and the round-trip-safe forms), `layout-units.md` (units, coordinates, page
setup), `namespaces-versions.md` (schema version table),
`validation-checklist.md` (pre-flight checklist).

`skills/claude-skills/rdl-generation/scripts/`: `rdl_builder.py` (a
dependency-free Python builder, usable as a library or as a
`spec.json -> output.rdl` CLI) and `validate_rdl.py` (structural validator,
runnable standalone).

`skills/claude-skills/rdl-generation/examples/`: five complete, validating
`.rdl` files covering a flat table, a grouped matrix with totals, a chart plus
table, a parameterised stored procedure with a multi-value parameter, and a
page header/footer with a subreport.
