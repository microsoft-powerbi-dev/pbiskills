# Devin knowledge: SSRS report creation

## Trigger

Apply this knowledge when the task is building, designing, deploying, or
tuning an SSRS or Power BI Paginated report end to end, not only writing its
XML.

## Content

Decide the report type before designing anything. Paginated fits a fixed
document destined for print, PDF, or a per-row export. Interactive Power BI
fits open-ended analysis. When a request wants both a dashboard and a
printable form, that is usually a Power BI report plus a paginated
drillthrough, not a single artifact.

Requirements to capture before writing SQL: audience and delivery mechanism,
export format, grain (one row per what), which parameters are required versus
cascading versus multi-value and their defaults, expected and worst-case row
volume, and refresh or latency tolerance.

Data source and dataset defaults: prefer a shared data source over one
embedded per report, since an embedded connection string multiplies
credentials across an estate. Prefer a stored procedure or a view over inline
SQL once a query is non-trivial. Filter inside the query, not with a
report-level filter, which still transfers every row before discarding most
of them.

Parameter defaults: give every parameter a prompt, a data type, and a
sensible default so the report can render on first open. Multi-value
parameters cannot bind directly to a stored procedure parameter; join the
values into a delimited string and split server-side, or use a table-valued
parameter.

Layout defaults: a table for fixed known columns, a matrix when columns come
from the data, a list for a repeated free-form block such as an invoice, a
chart alongside a table rather than instead of it, a subreport only when the
grain genuinely cannot be joined into the main dataset (many estate
migration tools flag subreports as a compatibility blocker).

Formatting: use a `Format` string property (`C2`, `N0`, `P1`, `d`) rather
than a formatting function inside an expression, because the format string
survives export to Excel as a real number, while the function's return value
exports as text.

Deployment: for SSRS 2017 or later and for Power BI Report Server, use the
REST v2.0 API at `/reports/api/v2.0`. `POST /CatalogItems` creates a report
and `PUT /CatalogItems({id})` overwrites one, with the RDL as base64 in a
`Content` field. For older servers, use `rs.exe` with an RSS script.

Anti-patterns to flag or avoid: `SELECT *` in a dataset query, `SELECT
DISTINCT` masking a join fan-out, leading-wildcard `LIKE` predicates, cross
joins, cursors, `IIf` nested more than two levels deep, chained `Replace()`
calls, embedded connection strings using integrated security on a service
account, and more than roughly five Tablix or Chart regions on one report.

## Reference material in this repository

`skills/claude-skills/ssrs-report-creation/` contains the full lifecycle
guide (`references/authoring-workflow.md`), data source and dataset detail,
sixteen named design patterns, per-renderer export constraints, the full
deployment guide with REST v2.0 calls and an RSS bulk-deploy script,
performance tuning in query-then-dataset-then-report-then-server order, and a
Fabric migration guide. `examples/` has an intake form and a pre-go-live
checklist. Read `SKILL.md` there first.
