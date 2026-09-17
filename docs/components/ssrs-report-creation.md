# Component: `skills/claude-skills/ssrs-report-creation/`

Pure documentation skill. No scripts, no MCP server. It is a workflow playbook:
a set of markdown references and templates that guide an agent (or a human)
through taking an SSRS or Power BI Paginated report from a business request to
a deployed, operated, and eventually retired catalog item.

## 1. Overview

The skill covers the whole paginated-report lifecycle:

```
requirements -> data contract -> dataset (datasource/query) -> parameters
   -> layout -> formatting -> review -> deploy -> operate -> retire
```

Its `SKILL.md` opens with a judgement call that has to happen before any of
that: whether the request should be a paginated report at all, or an
interactive Power BI report instead. Paginated is right for document-shaped
output (fixed layout, print/PDF destination, regulatory format, per-row
export); Power BI interactive is right for exploration and ad hoc slicing.
Getting this wrong is expensive later, so the skill puts the decision first
and gives a signal table to make it explicit rather than assumed.

**Division of responsibility with `rdl-generation`.** This skill and
`skills/claude-skills/rdl-generation/` split the same problem along a clean
line:

- `ssrs-report-creation` is process and judgement: what to ask before writing
  SQL, shared-vs-embedded tradeoffs, which layout pattern fits a given
  requirement, how to deploy safely, how to tune a slow report, when to move a
  report off SSRS entirely. It contains no code and produces no RDL itself.
- `rdl-generation` is the mechanical builder and validator: it owns the actual
  `.rdl` XML — schema skeletons, `Tablix`/`Chart`/`Subreport` construction,
  `rd:TypeName` mapping, expression syntax that round-trips — and ships a
  dependency-free Python builder, a validator, and an MCP server that exposes
  both as tools.

`ssrs-report-creation`'s own `SKILL.md` states this explicitly: "For the
`.rdl` XML itself, use the `rdl-generation` skill." `rdl-generation`'s
`SKILL.md` returns the favor: "If you are standing up a report end to end
including the datasource, deployment and subscription, start from the
`ssrs-report-creation` skill and come back here for the XML." In practice: use
`ssrs-report-creation` to decide what the report should be and how to get it
live; use `rdl-generation` to actually emit or validate the XML at the layout
stage.

A third related skill, `skills/report-lineage/`, is for understanding an
existing estate you did not build (scanning, duplicate detection, lineage) —
`ssrs-report-creation` points to it for that case rather than duplicating it.

## 2. Reference docs — what each covers and when to consult it

`skills/claude-skills/ssrs-report-creation/references/` has seven files.

- **`authoring-workflow.md`** — The full ten-stage lifecycle (requirements
  intake, data contract, dataset design, parameter design, layout, formatting,
  review, deploy, operate, retire), each stage with what it produces, what to
  check, who signs off, and the failure mode if the stage is skipped, all
  carried through one worked example ("Monthly Sales by Region"). Consult this
  first when starting a new report, or whenever you need to know what
  "finished" looks like for a given stage and who has to say so.

- **`data-sources-and-datasets.md`** — Shared vs. embedded data sources and
  why shared wins at estate scale; the four credential modes and the
  double-hop Kerberos delegation problem; connection-string forms for SQL
  Server, Azure SQL, Fabric Warehouse, Fabric Lakehouse SQL endpoint, Power BI
  semantic models (DAX/MDX), Analysis Services, and ODBC/OLE DB; when to use
  inline SQL vs. a view vs. a stored procedure; query-parameter binding and
  the three ways to pass a multi-value parameter to a stored procedure;
  shared datasets and caching; dataset field metadata and why field names must
  stay stable; filtering in the query vs. the report; and four patterns for
  row-level security with `User!UserID`. Consult this whenever you are wiring
  up a datasource or dataset, choosing a credential mode, or deciding where a
  filter belongs.

- **`report-design-patterns.md`** — Sixteen named layout patterns (fixed
  table, dynamic matrix, list-per-entity with page breaks, master-detail via
  nested groups, subreports and why to avoid them, drilldown, drillthrough,
  chart-plus-table, multi-column, green-bar rows, conditional formatting,
  running totals, percent-of-group, Top-N-plus-Other, sparse-data handling,
  no-data messages), each with the RDL fragment and its specific pitfall.
  Consult this at the layout stage to pick the right region for the shape of
  the answer, or when a familiar layout bug (blank alternating columns,
  a toggle that does nothing, a percentage column that doesn't sum to 100)
  needs a name and a fix.

- **`rendering-and-export.md`** — Renderer-specific behavior: soft-page vs.
  hard-page renderers, the blank-page-from-body-width rule, and per-format
  detail for PDF, Excel, Word, CSV, XML, MHTML, and Image/TIFF (including
  `DataElementOutput`/`DataElementName` for the data-only renderers), plus
  URL access parameters and the REST v2.0 export endpoint. Consult this
  whenever an export format is a requirement (which is almost every report)
  and especially before finalizing layout, since format constraints should
  shape layout rather than be discovered after the fact.

- **`deployment.md`** — The three deployment paths (REST API v2.0, SOAP,
  `rs.exe`/RSS scripts) with exact request bodies and worked PowerShell/curl
  examples for probing the server, listing the catalog, creating/overwriting a
  report, and downloading a definition; folder structure and naming
  conventions; item-level security and role assignment; re-pointing data
  sources after deployment; subscriptions (standard and data-driven); caching,
  snapshots and history; and a full environment-promotion pipeline with
  post-deployment verification. Consult this at the deploy and operate stages,
  or any time you're setting up CI/CD against a report server.

- **`performance.md`** — Tuning in the order that pays: query (indexing,
  sargable predicates, parameter sniffing, no `SELECT *`/cursors/leading
  wildcards), dataset (server-side filtering, aggregate in SQL not in the
  report, cache parameter value lists), report (avoid repeated `Lookup`,
  nested `IIf`, chained `Replace`, too many Tablix/chart regions, interactive
  sorting on large sets), and server (caching, snapshots, scale-out, memory
  configuration). Includes `ExecutionLog3` queries and a full diagnostic
  decision tree keyed on which phase (`TimeDataRetrieval` /`TimeProcessing`
  /`TimeRendering`) dominates. Consult this whenever a report is slow, or
  proactively during dataset design and layout to avoid the anti-patterns
  before they ship.

- **`migration-to-fabric.md`** — What transfers unchanged to PBIRS/Fabric vs.
  what needs rework (subreports as a hard blocker, unsupported data source
  types, custom assemblies, custom report items, shared datasource/dataset
  embedding, expression-based query text); the RDL-to-RDL re-point transform
  this repo's backend implements; licensing/capacity facts; a four-outcome
  decide-per-report matrix (keep paginated / convert to interactive / retire /
  merge with a duplicate) driven by usage, compatibility-scan, and duplicate
  analysis; and a phased migration plan template (inventory, retire,
  consolidate, build target platform, migrate clean reports, rework blocked
  reports, convert to interactive, decommission). Consult this when planning a
  move off SSRS, or when deciding per-report whether Fabric paginated is even
  the right target.

## 3. Example files — templates and how they're used

`skills/claude-skills/ssrs-report-creation/examples/` has two files.

- **`requirements-template.md`** — The intake form for stage 1 of the
  workflow. It starts with the paginated-vs-interactive decision box and a
  reuse-check ("does it already exist?"), then walks through identity,
  audience/delivery, grain (a one-sentence completion exercise), a
  column-and-definition table with format strings, a parameter table, volume,
  refresh/latency, security and row-level rules, retention, layout notes, and
  numeric acceptance criteria, ending in a sign-off table. In practice: copy
  this file, rename it after the report, fill it in with the actual
  stakeholder before writing any SQL, and keep it committed alongside the RDL
  in source control as the evidence that stage 1 happened.

- **`report-review-checklist.md`** — The pre-go-live gate for stage 7. It is
  organized into eight checklist sections (correctness, parameters, layout,
  formatting, export, performance, operations, maintainability), each a set of
  checkboxes tied back to concrete artifacts (an `ExecutionLog3` row, a
  reconciliation diff, opened PDF/XLSX/CSV files), plus an actions-arising
  table with blocker/major/minor severities. In practice: a developer who did
  not author the report fills this in against a rendered, deployed report
  before it goes live, attaching evidence rather than opinions, and a blocker
  finding stops the release.

## 4. End-to-end authoring workflow

Numbered sequence, drawn from `SKILL.md`'s "Workflow" section and expanded
with the ten-stage gate structure in `authoring-workflow.md`. Each stage names
the reference doc that governs it.

1. **Decide paginated vs. interactive.** Use the signal table in `SKILL.md`
   before designing anything. (No dedicated reference file — this judgement
   call lives directly in `SKILL.md`.)
2. **Requirements intake.** Capture audience/delivery, grain, parameters,
   volume, and refresh/latency tolerance; fill in
   `examples/requirements-template.md`; get sign-off from the person who will
   read the report. Governed by `authoring-workflow.md` (stage 1).
3. **Data contract.** Agree source tables, grain, measure definitions, and
   refresh timing with the data owner/DBA. Governed by `authoring-workflow.md`
   (stage 2).
4. **Dataset design.** Prefer a shared data source over embedded, and a
   stored procedure or view over inline SQL; filter in the query, not the
   report. Governed by `data-sources-and-datasets.md` and
   `authoring-workflow.md` (stage 3).
5. **Parameter design.** Give every parameter a prompt, data type, and
   sensible default; set cascade order by `<ReportParameter>` element order;
   handle multi-value parameters against stored procedures via `Join` plus
   `STRING_SPLIT` (or a table-valued parameter). Governed by
   `data-sources-and-datasets.md` and `authoring-workflow.md` (stage 4).
6. **Layout.** Pick the region (table/matrix/list/chart/subreport) by the
   shape of the answer; build header and detail rows together; set
   `RepeatOnNewPage`/`KeepWithGroup`; keep body width inside the printable
   area. Governed by `report-design-patterns.md` and `rendering-and-export.md`
   (for export-format layout constraints), with `authoring-workflow.md` stage
   5 as the gate.
7. **Formatting.** Use `Format` strings, not `FormatCurrency()`-style
   expressions, so numbers survive Excel export as real numbers; alternate row
   color via a `RowNumber` expression. Governed by `authoring-workflow.md`
   (stage 6).
8. **Review.** Complete `examples/report-review-checklist.md` with a peer
   developer; produce PDF/XLSX/CSV evidence and a reconciliation. Governed by
   `authoring-workflow.md` (stage 7).
9. **Deploy.** Use the REST v2.0 API (`POST`/`PUT /CatalogItems`) for SSRS
   2017+/PBIRS, or `rs.exe` with an RSS script for older servers/bulk
   deployment; verify the datasource re-point and folder/permission
   conventions. Governed by `deployment.md` and `authoring-workflow.md`
   (stage 8).
10. **Operate.** Set up subscriptions (standard or data-driven) owned by a
    service account with failure alerting, and a caching/snapshot policy where
    the report is expensive; baseline execution times. Governed by
    `deployment.md` and `performance.md`, with `authoring-workflow.md` stage 9
    as the gate.
11. **Retire.** Record a decommission entry, delete (not just disable)
    subscriptions, verify no surviving drillthrough targets a retired report,
    and archive the RDL and proc DDL. Governed by `authoring-workflow.md`
    (stage 10).

`authoring-workflow.md` closes with a ten-item "gate summary as a single pass"
checklist that mirrors this sequence and can be run before declaring any
report done.

## 5. Things to get right — performance and design-pattern guidance

From **`performance.md`**:

- Tune in the order that actually pays: query first (60-80% of the win
  typically), then dataset shape/volume, then report-layer expressions, then
  server-side caching/scale-out. Measure with `ExecutionLog3` before tuning —
  it tells you which of `TimeDataRetrieval`/`TimeProcessing`/`TimeRendering`
  dominates, and each phase points at a different layer.
- Every dataset query needs a covering index on its filter/join columns; keep
  predicates sargable (don't wrap the filtered column in a function); expect
  parameter sniffing on report procs with widely varying date ranges and
  default to `OPTION (RECOMPILE)` since these run a few hundred times a day,
  not a few hundred times a second.
- Never `SELECT *`, never a leading-wildcard `LIKE '%...'`, never a cursor in
  a report query — all three are specifically detected by this repo's
  compatibility scanner and have concrete rewrites (window functions,
  `STRING_AGG`, trailing wildcards or full-text search).
- Filter and aggregate in SQL, not in the report: a report-level or Tablix
  filter runs only after the full result set has crossed the network and been
  buffered in report-server memory — filtering 8,000,000 rows to 400 still
  costs the full transfer.
- Avoid calling `Lookup` more than once per row for the same dataset (join in
  SQL instead), avoid `IIf` nested three or more levels deep (both branches
  always evaluate — use `Switch` or a SQL `CASE`), avoid three or more chained
  `Replace()` calls, and cap data regions at five Tablix/five charts or the
  report is a dashboard wearing a paginated report's clothes.
- Cache parameter available-values lists as a shared, cached dataset — an
  uncached `SELECT DISTINCT` against a large dimension on every report open is
  pure waste.

From **`report-design-patterns.md`**:

- Column headers must live inside the Tablix, never as floating textboxes
  above it — floating headers don't repeat across pages and don't become
  proper header rows in Excel export.
- `RepeatOnNewPage` has no effect without `KeepWithGroup` set alongside it;
  both are required together.
- Page breaks belong on the `Group`, never on a surrounding Rectangle — a
  break on a rectangle produces one break total, not one per group instance.
- Subreports are execution-cost-per-instance: one in a detail row means one
  database round trip per row (2,000 rows = 2,000 round trips), which is the
  single most common cause of a paginated report that takes minutes to
  render. This repo's compatibility scanner treats any subreport as a
  migration blocker outright. Prefer joining in SQL with nested groups, or
  drillthrough to a separate report.
- Matrix (pivot) width is unbounded by the data — twelve months fits a
  portrait page, thirty-six spills sideways into extra PDF pages; constrain
  the range by parameter, and remember sparse combinations render as blanks
  not zeros unless fixed in SQL.
- Always include a `NoRowsMessage` — an empty grid looks broken, and the
  message doesn't fire if rows were removed by a Tablix filter rather than
  absent from the dataset (one more argument for filtering in the query).
- Divide-by-zero protection belongs in SQL with `NULLIF`, not wrapped in
  `IIf` in the report — `IIf` evaluates both branches regardless, so it
  doesn't actually guard against the error.

## 6. Migration to Fabric — what's covered, and where the depth lives

`migration-to-fabric.md` describes moving a paginated report toward Power BI
Report Server (a like-for-like lift — full RDL support including shared
data sources, subreports, custom assemblies) or toward Microsoft
Fabric/Power BI service (RDL with a defined set of unsupported features,
i.e., "a lift with rework"). It covers, at the level needed to plan a
migration:

- What transfers unchanged (RDL schema itself, all layout item types,
  expressions, parameters, renderers, drillthrough/drilldown).
- What needs rework for Fabric specifically: subreports are a hard blocker
  with no equivalent; shared data sources/datasets must be embedded; custom
  code and custom report items are unsupported; certain data source types
  (arbitrary ODBC/OLE DB, flat files, SharePoint lists) aren't supported and
  must be re-pointed to something Fabric-native (Warehouse, Lakehouse SQL
  endpoint, Power BI semantic model, or a gateway-backed source).
- The mechanical RDL-to-RDL re-point transform this repository's backend
  implements (scan for compatibility findings, re-point embedded connection
  strings, re-serialize preserving the original RDL namespace), plus its
  additive/idempotent enrichment rules.
- Licensing and capacity facts (paginated needs a Premium/Embedded/Fabric
  capacity; on-premises sources need a gateway; PBIRS is the no-cloud,
  no-feature-loss option).
- A four-outcome decide-per-report matrix (keep paginated / convert to
  interactive / retire / merge with a duplicate) driven by usage data,
  compatibility-scan findings, and duplicate-cluster analysis, plus a
  phased, multi-week migration plan template (inventory -> retire ->
  consolidate duplicates -> build target platform -> migrate clean reports ->
  rework blocked reports -> convert-to-interactive -> decommission) with
  reconciliation as the final, most-often-underestimated step.

This file connects to `skills/vendor/microsoft-fabric/`, which exists in this
repository as the deeper reference for actually working in Fabric (workspace
setup, capacities, item types, etc.) — that skill is not documented here in
depth; a separate doc covers it.
