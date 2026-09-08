---
name: SSRS Report Creation
description: >
  Use this skill when creating a SQL Server Reporting Services or Power BI
  Paginated report end to end: from requirements through query design, dataset
  and parameter modelling, layout, formatting, deployment and subscriptions.
  Covers shared vs embedded data sources, stored-procedure-backed datasets,
  cascading and multi-value parameters, table / matrix / chart / list design
  patterns, drillthrough and drilldown, export-format constraints, deployment via
  the REST v2.0 API and rs.exe, folder and permission conventions, performance
  tuning, and when to move a report to Power BI instead. Example requests:
  "build a monthly sales report with a region parameter", "design a paginated
  invoice report", "deploy these RDLs to the report server", "make this SSRS
  report faster", "should this report be paginated or a Power BI report".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
triggers:
  - ssrs report
  - create ssrs report
  - build a report
  - reporting services
  - sql server reporting services
  - paginated report
  - report builder
  - report design
  - shared datasource
  - shared dataset
  - cascading parameter
  - multi value parameter
  - drillthrough report
  - subreport
  - deploy report
  - rs.exe
  - report server rest api
  - report subscription
  - report snapshot
  - report performance
  - ssrs to power bi
---

# SSRS Report Creation

Take a reporting requirement to a deployed, maintainable SSRS or Power BI
Paginated report.

This skill covers the whole lifecycle and the judgement calls. For the `.rdl`
XML itself, use the `rdl-generation` skill. For understanding an estate you did
not build, use `skills/report-lineage/`.

## Decide first: should this be a paginated report at all?

Paginated is the right answer when the output is **a document**: something with
page fidelity, a fixed layout, a print or PDF destination, a regulatory format,
or a per-row export. It is the wrong answer when the output is **an analysis**:
exploration, cross-filtering, drill paths the author did not anticipate.

| Signal | Points to |
| --- | --- |
| Must fit an official form, invoice, statement, or remittance | Paginated |
| Emailed as PDF or Excel on a schedule | Paginated |
| Thousands of rows exported for downstream processing | Paginated |
| "Can I slice this by anything?" | Power BI interactive |
| Users want to build their own views | Power BI interactive |
| Both: a dashboard with a printable detail sheet | Power BI report plus a paginated drillthrough |

Getting this wrong is expensive later, so ask before designing. If the estate is
being migrated, `backend/app/core/paginated/compatibility.py` already classifies
which reports transfer as paginated and which need rework.

## Workflow

### 1. Requirements: before any SQL

Capture, explicitly:

- **Audience and delivery.** Who opens it, how (portal / subscription / API),
  and in what format. Export format determines layout constraints more than
  anything else; see `references/rendering-and-export.md`.
- **Grain.** One row per what? This drives the dataset and the grouping.
- **Parameters.** Which are required, which cascade, which are multi-value,
  which have defaults. Cascading parameters are a query design decision, not a
  UI decision.
- **Volume.** Expected and worst-case row count. A report that is fine at 5,000
  rows and unusable at 500,000 needs to know that now.
- **Refresh and latency tolerance.** Live query, snapshot, or cached.

### 2. Data: shared before embedded

Prefer a **shared data source** and, where the query is reused, a **shared
dataset**. Embedded datasources multiply credentials across the estate and are
the main reason a server migration turns into a hundred manual edits.

Datasets: prefer a stored procedure or a view over inline SQL when the query is
non-trivial. It moves the logic where it can be version-controlled, tested and
tuned, and it keeps the RDL readable. Filter in the query, not in the report:
report-level filters pull every row across the wire and then discard it.

See `references/data-sources-and-datasets.md`.

### 3. Parameters

- Give every parameter a `Prompt`, a `DataType`, and a default where one is
  sensible. A report that cannot render until the user fills five blanks gets
  abandoned.
- Cascading: the child parameter's available-values dataset takes the parent as
  a query parameter. Order in `<ReportParameters>` defines the cascade order.
- Multi-value: pass with `IN (@Param)` and let SSRS expand it. Multi-value
  parameters cannot be used with stored procedures directly: either join them
  (`=Join(Parameters!P.Value, ",")`) and split server-side, or use a
  table-valued parameter.
- Hidden and internal parameters are for drillthrough targets and subreports.

### 4. Layout

Pick the region by the shape of the answer:

| Region | Use for |
| --- | --- |
| **Table** | Fixed, known columns; the default |
| **Matrix** | Columns generated from data (months, categories): a pivot |
| **List** | One free-form block per group: invoices, statements, letters |
| **Chart** | Trend or comparison, usually alongside a table not instead of it |
| **Subreport** | Genuinely different grain that cannot be joined; use sparingly |

Design rules that pay off: build the header row and the detail row together;
set `RepeatOnNewPage` on group headers; put page breaks on the group, not on a
rectangle; keep the body width inside the printable area or every page is
followed by a blank one.

See `references/report-design-patterns.md`.

### 5. Formatting

Use `Format` strings (`C2`, `N0`, `P1`, `d`, `yyyy-MM-dd`) rather than
`FormatCurrency()` in an expression: the format string survives export to Excel
as a real number format, the expression exports as text. Use alternating row
colour via a `BackgroundColor` expression on `RowNumber`, not by hand.

### 6. Deploy

The repository targets the **REST v2.0 API** (`/reports/api/v2.0`) for SSRS
2017+ and Power BI Report Server: `POST /CatalogItems` to create,
`PUT /CatalogItems({id})` to overwrite, with the RDL travelling as base64 in
`Content`. `rs.exe` with an RSS script remains the option for older servers and
for scripted bulk deployment.

Folder and naming conventions, permissions, and the datasource re-point step are
in `references/deployment.md`.

### 7. Operate

Subscriptions (standard and data-driven), snapshots, caching, and execution-log
monitoring are in `references/deployment.md` and
`references/performance.md`.

## Bundled assets

### `references/`

| File | Covers |
| --- | --- |
| `authoring-workflow.md` | The full lifecycle checklist with sign-off gates |
| `data-sources-and-datasets.md` | Shared vs embedded, credentials, stored procs, query parameters, dataset fields |
| `report-design-patterns.md` | Table / matrix / list / chart / subreport patterns, drillthrough, drilldown, grouping and totals |
| `rendering-and-export.md` | Renderer-specific layout constraints: PDF, Excel, Word, CSV, XML, image |
| `deployment.md` | REST v2.0 and rs.exe deployment, folders, permissions, datasource re-point, subscriptions |
| `performance.md` | Query, dataset, layout and server-side tuning; the anti-patterns this repo's analyser flags |
| `migration-to-fabric.md` | Moving a paginated estate to Power BI Report Server / Fabric, and when to convert to interactive instead |

### `examples/`

| File | Contents |
| --- | --- |
| `requirements-template.md` | The intake form that makes step 1 concrete |
| `report-review-checklist.md` | What to check before a report goes live |

## Anti-patterns the estate analyser flags

These come from `backend/app/core/paginated/compatibility.py`, which runs them
against real reports. Avoid them at authoring time:

- `SELECT *` in a dataset query: breaks the moment a column is added upstream.
- `SELECT DISTINCT` used to paper over a join fan-out.
- Leading-wildcard `LIKE '%...'`: no index can serve it.
- Cross joins, and cursors in a report query.
- Nested `IIf` more than two deep: use `Switch` or move it into SQL.
- Chained `Replace()` calls: move to the query or to custom code.
- `Integrated Security` in an embedded connection string on a server that runs
  as a service account.
- More than five Tablix regions or five charts on one report: it is a
  dashboard wearing a paginated report as a costume.

## Related material in this repository

- `docs/report-server.md`, `docs/report-server-setup.md`: connecting to a
  report server from this platform.
- `docs/ssrs-lab-setup.md`: a reproducible local SSRS lab on
  WideWorldImporters, useful for testing generated reports.
- `backend/app/core/report_server/`: REST v2.0 and SOAP clients.
- `.claude/agents/paginated-performance-advisor.md`: the performance review
  agent, loaded at runtime by the platform.
- `.claude/skills/paginated-reports/`: deeper Power BI Paginated reference
  material, including the REST API and troubleshooting guides.
