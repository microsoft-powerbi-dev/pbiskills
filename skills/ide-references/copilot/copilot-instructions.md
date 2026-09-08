# Copilot instructions: SSRS / RDL work in this repository

When suggesting or completing code that creates, edits, or reviews an `.rdl`
file, or that relates to SSRS / Power BI Paginated report design, deployment,
or performance, follow the rules below. Full detail lives in
`skills/claude-skills/rdl-generation/` and
`skills/claude-skills/ssrs-report-creation/`.

## When generating RDL XML

Target schema version 2016
(`http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition`)
by default; match an existing file's declared version instead of upgrading
it. Respect element order: inside `Query`, `DataSourceName` comes before
`CommandType`, which comes before `CommandText`. Place `PageHeader` and
`PageFooter` inside `ReportSection/Page`, never directly inside `Report`.
Register the default namespace and the `rd` prefix before writing generated
XML, so elements do not serialise with an auto-generated `ns0:` prefix. Give
every measurement a unit suffix (`in`, `cm`, `mm`, `pt`, `px`); never emit a
bare numeric size.

Write field references as `=Fields!Name.Value` and aggregates as
`=Sum(Fields!Name.Value)` (or `Avg` / `Min` / `Max` / `Count` /
`CountDistinct`). Before suggesting a `Fields!X.Value` reference, check that
`X` is declared as a `Field` in the dataset the containing Tablix or Chart
points to; an unresolved reference renders `#Error` at runtime with no
build-time signal.

## When designing or reviewing a report

Prefer a shared data source over an embedded connection string. Prefer a
stored procedure or a view over inline SQL for anything beyond a trivial
query. Filter in the query, not with a report-level filter. Give every
parameter a prompt, a data type, and a sensible default. Remember that
multi-value parameters cannot bind directly to a stored procedure parameter;
they need a join-and-split or a table-valued parameter.

Flag these patterns in review: `SELECT *`, `SELECT DISTINCT` used to mask a
join fan-out, a leading-wildcard `LIKE '%...'`, cross joins, cursors, `IIf`
nested more than two levels deep, chained `Replace()` calls, an embedded
connection string with integrated security on a service account, and more
than roughly five Tablix or Chart regions on a single report.

## When deploying

For SSRS 2017+ or Power BI Report Server, use the REST v2.0 API
(`/reports/api/v2.0`): `POST /CatalogItems` to create, `PUT
/CatalogItems({id})` to overwrite, RDL content as base64 in the `Content`
field. For older servers, use `rs.exe` with an RSS script.

## Reference paths

- `skills/claude-skills/rdl-generation/SKILL.md` and its `references/`,
  `scripts/`, `examples/` subfolders for anything about the XML itself.
- `skills/claude-skills/ssrs-report-creation/SKILL.md` and its `references/`,
  `examples/` subfolders for the end-to-end workflow, design patterns,
  export, deployment, and performance.
