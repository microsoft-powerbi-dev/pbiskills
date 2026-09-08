# Devin knowledge: RDL generation

## Trigger

Apply this knowledge when the task involves creating, writing, or emitting an
`.rdl` file (SSRS or Power BI Paginated Report Definition Language), or when a
generated or hand-edited `.rdl` file fails to open in Report Builder, Visual
Studio, or a report server.

## Content

RDL is XML validated against a version-specific XSD (`2005/01`, `2008/01`,
`2010/01`, or `2016/01` under
`http://schemas.microsoft.com/sqlserver/reporting/`). Default to `2016/01`
unless the target server is known to be older. The schema uses
`xsd:sequence`, so child element order inside every complex type is fixed,
not incidental.

Non-negotiable rules, in priority order:

1. Inside `DataSet/Query`, emit `DataSourceName`, then `CommandType` (one of
   `Text`, `StoredProcedure`, `TableDirect`), then `CommandText`, in that
   order. Reversing or omitting one produces a validation error that names
   the element after the mistake.
2. `PageHeader` and `PageFooter` are children of `ReportSection/Page`, not of
   `Report`, in the 2010+ schema shape. RDL 2008 and earlier used
   `Report/Body` directly with no `ReportSections` wrapper; do not mix the
   two shapes in one document.
3. Register the default XML namespace (the schema URL) and the `rd` prefix
   (`http://schemas.microsoft.com/SQLServer/reporting/reportdesigner`) before
   serialising the tree, or every element is emitted with an auto-generated
   `ns0:` prefix that most tools reject.
4. Every size value carries a unit suffix: `in`, `cm`, `mm`, `pt`, or `px`.
   `<Width>3in</Width>` is valid; `<Width>3</Width>` is not, even though some
   renderers tolerate it silently.
5. Write expressions as `=Fields!ColumnName.Value` for a plain field
   reference and `=Sum(Fields!ColumnName.Value)` for an aggregate. These are
   the forms most RDL tooling expects to read back; do not invent equivalent
   but differently-shaped expressions.

Before emitting a `Fields!X.Value` expression anywhere in the body or an
aggregate, confirm `X` is declared as a `Field` inside the `DataSet` that the
enclosing `Tablix` or `Chart`'s `DataSetName` points to. A field reference to
an undeclared name renders `#Error` in every affected cell at runtime, with no
compile-time warning.

Minimum structural skeleton, in required order:
`Report > DataSources > DataSource > ConnectionProperties`, then
`DataSets > DataSet > Query, Fields`, then optionally `ReportParameters`, then
`ReportSections > ReportSection > Body > ReportItems, Width, Page`.

## Reference material in this repository

`skills/claude-skills/rdl-generation/` contains seven reference documents
(structure, datasets and parameters, Tablix and Chart anatomy, expressions,
layout units, schema versions, a validation checklist), a dependency-free
Python builder and validator in `scripts/`, and five complete working `.rdl`
examples in `examples/`. Read `SKILL.md` there first; it indexes everything
else.
