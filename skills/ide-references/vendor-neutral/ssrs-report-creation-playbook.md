# Playbook: creating an SSRS report end to end

A vendor-neutral reference for taking a reporting requirement to a deployed
report. Full detail: `skills/claude-skills/ssrs-report-creation/`. For the RDL
XML itself, see `rdl-generation-playbook.md`.

## Decide first: is paginated the right tool?

Paginated fits an output that is a document: fixed layout, print or PDF
destination, a regulatory format, a per-row export. It does not fit an output
that is an analysis: exploration, cross-filtering, drill paths the author
could not anticipate. A dashboard with a printable detail sheet usually wants
a Power BI report plus a paginated drillthrough, not one or the other.

## Workflow

### 1. Requirements

Capture before writing any SQL: audience and delivery mechanism (portal,
subscription, API) and export format; grain (one row per what); which
parameters are required, cascading, or multi-value, and their defaults;
expected and worst-case row volume; refresh and latency tolerance (live,
snapshot, cached).

### 2. Data

Prefer a shared data source over an embedded one; an embedded connection
string per report multiplies credentials across an estate and turns a server
migration into a hundred manual edits. Prefer a stored procedure or a view
over inline SQL once the query is non-trivial: it is version-controllable,
testable, and tunable independently of the report. Filter in the query, not
in the report; a report-level filter still pulls every row across the wire
before discarding most of them.

### 3. Parameters

Give every parameter a prompt, a data type, and a sensible default. Order in
the parameter list defines the cascade order for dependent parameters.
Multi-value parameters cannot bind directly to a stored procedure parameter;
join them (`Join(Parameters!P.Value, ",")`) and split server-side (for
example `STRING_SPLIT`), or use a table-valued parameter.

### 4. Layout

Match the region to the shape of the answer: a table for fixed known columns,
a matrix when columns are generated from the data (a pivot), a list for a
free-form block repeated per group (invoices, statements), a chart alongside
a table rather than instead of it, a subreport only when the grain genuinely
cannot be joined (subreports are commonly flagged as a migration blocker).
Keep the body width and height inside the printable page area, or every page
is followed by a blank one.

### 5. Formatting

Use `Format` strings (`C2`, `N0`, `P1`, `d`) rather than functions such as
`FormatCurrency()` inside an expression. A format string survives export to
Excel as a real number format; the function's output exports as text.

### 6. Deploy

Modern path (SSRS 2017+, Power BI Report Server): the REST v2.0 API,
`/reports/api/v2.0`. `POST /CatalogItems` creates a report, `PUT
/CatalogItems({id})` overwrites one, with the RDL travelling as base64 in a
`Content` property. Older servers: `rs.exe` with an RSS script.

### 7. Operate

Standard and data-driven subscriptions, snapshots and caching for expensive
queries, and periodic review of the execution log for reports that have
slowed down.

## Design patterns worth naming

Fixed table, matrix/pivot, list document with page-break-per-instance,
master-detail via nested regions, drilldown (toggle visibility), drillthrough
to a second report with parameters passed through, chart-plus-table,
alternating row colour keyed on row number, running totals, percent of group
total, top-N-plus-other, and an explicit no-data message region.

## Anti-patterns to avoid at authoring time

`SELECT *` in a dataset query; `SELECT DISTINCT` used to paper over a join
fan-out; a leading-wildcard `LIKE '%...'` that no index can serve; cross
joins and cursors in report queries; `IIf` nested more than two deep (use
`Switch`, or move the logic into SQL); chained `Replace()` calls; embedded
`Integrated Security` connection strings on a server running as a service
account; more than about five Tablix regions or five charts on a single
report, which usually means the report wants to be a dashboard instead.

## Where the detail lives

`skills/claude-skills/ssrs-report-creation/references/`: `authoring-workflow.md`
(full lifecycle with sign-off gates), `data-sources-and-datasets.md` (shared
vs embedded, credentials, query parameters), `report-design-patterns.md` (all
patterns above with XML), `rendering-and-export.md` (per-format constraints),
`deployment.md` (REST v2.0 and rs.exe, folders, subscriptions),
`performance.md` (tuning order and diagnostics), `migration-to-fabric.md`
(moving a paginated estate to Fabric or Power BI Report Server).

`skills/claude-skills/ssrs-report-creation/examples/`: `requirements-template.md`
(intake form) and `report-review-checklist.md` (pre-go-live checklist).
