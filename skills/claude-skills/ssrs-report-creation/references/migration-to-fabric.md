# Migration to Power BI Report Server and Fabric

Moving a paginated estate off SQL Server Reporting Services. Two destinations,
with different rules.

| Destination | What it is | RDL support |
| --- | --- | --- |
| **Power BI Report Server (PBIRS)** | On-premises, a superset of SSRS that also hosts `.pbix` | Full RDL, including shared data sources, shared datasets, subreports and custom assemblies |
| **Microsoft Fabric / Power BI service** | Cloud, paginated reports on a Fabric capacity | RDL with a defined set of unsupported features |

PBIRS is a lift. Fabric is a lift with rework. Decide which per report, not per
estate.

---

## What transfers unchanged

To both destinations:

- The RDL schema itself. Power BI paginated reports **are** RDL, which is why the
  conversion is a re-point plus validate rather than a rebuild.
- Tablix, Chart, Gauge, Rectangle, Image, Line, Textbox, and all their layout
  and grouping semantics.
- Expressions, aggregate functions, scope names, `Globals`, `User!UserID`.
- Report parameters, including cascading and multi-value.
- Page setup, page breaks, headers and footers.
- Renderers: PDF, Excel, Word, CSV, XML, MHTML, Image.
- Drillthrough between paginated reports, and drilldown by toggle visibility.

---

## What needs rework for Fabric

This repository's compatibility scanner
(`backend/app/core/paginated/compatibility.py`) encodes exactly this list. Its
module docstring states the mapping:

```
Subreports                      -> blocker (no Power BI paginated equivalent)
Shared data source / dataset    -> warn   (must be embedded into the .rdl)
Custom code / CodeModules       -> warn   (custom assemblies not supported)
Maps / CustomReportItem         -> warn   (validate / may need rework)
Expression-based query text     -> warn   (re-validate against the new source)
```

### Subreports (blocker)

There is no Fabric paginated equivalent. Every subreport must be re-modelled
before the report can move.

- **Subreport at header or footer level** (a single instance): merge its dataset
  into the parent query and place the items inline.
- **Subreport in a detail row** (one instance per row): this was always a
  performance disaster. Re-model as nested groups over one joined query
  (`report-design-patterns.md` pattern 4), or as a drillthrough report
  (pattern 7).

### Data source types

Fabric paginated supports a specific list of data sources. Anything outside it
must be re-pointed.

| SSRS source | Fabric paginated |
| --- | --- |
| SQL Server (on-premises) | Supported via an on-premises data gateway |
| Azure SQL Database / Managed Instance | Supported directly |
| Azure Synapse Analytics | Supported directly |
| Fabric Warehouse | Supported directly |
| Fabric Lakehouse SQL analytics endpoint | Supported directly |
| Power BI semantic model | Supported directly, DAX or MDX |
| Analysis Services (tabular and multidimensional) | Supported via a gateway |
| Oracle, Teradata | Supported via a gateway |
| ODBC / OLE DB to arbitrary providers | Not supported; re-point to a supported source |
| Flat file, XML, SharePoint list | Not supported; land the data in a warehouse first |

`Integrated Security` in a connect string produces the `integrated_security`
finding:

> Data source uses Integrated Security - Fabric paginated requires an
> on-premises data gateway with a service account that has read access.

That is the practical consequence: user identity is not delegated to an
on-premises SQL Server from the cloud. The gateway connects as a single service
account, so per-user filtering must move into the query via `User!UserID` (see
`data-sources-and-datasets.md`).

### Custom assemblies and custom code

`<CodeModules>` referencing a `.dll` cannot be deployed to Fabric; there is no
GAC to install into. `<Code>` blocks embedded in the RDL are also flagged
(`custom_code`) and should be assumed unsupported.

Migration path: move the logic into SQL (a `CASE` expression, a scalar function,
a computed column) or into a report expression using built-in functions.

### Custom report items

Third-party controls (barcode generators, specialised charts) render only where
their assembly is installed. Fabric flags them `custom_report_item` and they will
not render. Replace with a built-in equivalent, or generate the artifact upstream
(for example, render a barcode to an image in the database and use an
`Image` report item with `Source=Database`).

### Shared data sources and shared datasets

Both must be embedded into the `.rdl`. This is a mechanical transform: take the
`.rds` connection string and inline it as `<ConnectionProperties>`; take the
`.rsd` query and inline it as `<Query>`.

The consequence is that the estate loses its single point of connection control.
Plan for it: keep the shared data sources as the source of truth in source
control and generate the embedded RDLs from them at build time, rather than
hand-editing 300 files.

### Expression-based query text

A `CommandText` that begins with `=` is built at runtime. It gets
`expression_query`:

> Expression-based query text must be re-validated against the new data source.

The generated SQL may be valid on SQL Server and invalid on a Lakehouse endpoint.
Extract a representative sample of generated queries and execute them against the
target before migrating.

### Maps

Flagged `map` (info). Map report items depend on spatial data sources and on the
bundled map gallery. Validate after re-pointing; expect rework if the spatial
data came from an unsupported source.

---

## The RDL-to-RDL re-point this repository implements

`backend/app/core/paginated/` implements the transform. The design decision, from
`transformer.py`:

> Because Power BI paginated reports use the RDL schema, conversion preserves the
> original document and mutates it in place via ElementTree rather than
> round-tripping through an IR (which would be lossy).

Three steps:

1. **Scan** with `compatibility.detect`, producing `Finding` records at
   `blocker` / `warn` / `info` severity.
2. **Re-point** every embedded `ConnectString` to the target.
3. **Re-serialise** with the original RDL namespace preserved (2008, 2010 or
   2016), so the output is still valid against the schema the report was authored
   against.

Namespace preservation matters: `_detect_namespaces` collects every
`xmlns` prefix from the source and re-registers it before writing, so a
2008-schema report stays a 2008-schema report.

### The re-point target

```python
@dataclass(frozen=True)
class RepointTarget:
    server: Optional[str] = None
    database: Optional[str] = None
    connect_string: Optional[str] = None  # full override, e.g. a Fabric SQL endpoint
```

`to_connect_string()` returns `connect_string` when supplied, otherwise
`Data Source={server};Initial Catalog={database}`. For Fabric, supply the full
override:

```
Data Source=abcd1234.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesWarehouse
```

If the report has no embedded data source (because it uses a shared reference),
the transform records:

```
[WARN] no_embedded_datasource: No embedded data source found to re-point
       (shared references must be embedded first).
```

That is the ordering constraint: embed first, then re-point.

### Dataset overrides

`transform_paginated(..., dataset_overrides={...})` rewrites a named dataset's
`CommandText` and `CommandType` during the migration. This is how a report that
called `dbo.usp_LegacySales` gets moved onto `Reporting.usp_MonthlySalesByRegion`
in the same pass. Matching is by dataset name, case-insensitively, and the
transform maintains the RDL schema's required child order
(`DataSourceName`, `CommandType`, `CommandText`).

### Enrichment

`enrichers.py` runs after the transform and before writing. Every rule is
**additive and idempotent**: it never overwrites an author's element. Three rules
ship today:

| Rule | Adds | Code logged |
| --- | --- | --- |
| A1 | A `<PageFooter>` with `"Page " & Globals!PageNumber & " of " & Globals!TotalPages` and `=Globals!ExecutionTime` on any `<Page>` that has none | `enricher_page_footer` |
| A2 | `<MultiValue>true</MultiValue>` on any `ReportParameter` with `AllowMultipleValues=true` that lacks it, inserted after `Prompt` and before `ValidValues`, because sibling order matters in Fabric | `enricher_multi_value` |
| A3 | A canonical `<Format>` on unformatted Tablix cells: `C2` for amount/price/total/revenue/sales/value/balance/salary/fee, `P1` for pct/percent/rate/ratio/share, `yyyy-MM-dd` for date/time/timestamp/created/modified/updated | `enricher_format_string` |

Pass `enrich=False` when you need a byte-for-byte migration diff.

### Output

A `compatibility_report.md` per report, with a summary count of blockers,
warnings and info findings, and each finding rendered as
`**SEVERITY** - code: message (locator)`.

---

## Fabric-specific data source options

### Warehouse

A Fabric Warehouse is a full T-SQL surface: tables, views, stored procedures
(read and write), full DML. It behaves like SQL Server for reporting purposes.
Connect as SQL Server against the TDS endpoint with Entra ID authentication.

This is the right target when the paginated estate already runs stored
procedures, because the procs port with minimal change.

### Lakehouse SQL analytics endpoint

The SQL analytics endpoint over a lakehouse is **read-only**. Views are
supported; stored procedures that write are not. A report backed by a proc that
creates a temp table and populates it will fail.

Migration path for those reports: either move the proc's logic into a view or a
CTE inside the dataset query, or move the workload to a Warehouse.

### Power BI semantic model

Query with DAX or MDX. The right target when an interactive Power BI report
already exists over the same data and the paginated report should agree with it
to the penny. One model, two report types, no reconciliation problem.

```
EVALUATE
SUMMARIZECOLUMNS(
    'Region'[Region],
    'Date'[Month],
    TREATAS({@RegionList}, 'Region'[Region]),
    "GrossSales", [Total Sales]
)
```

Trade-off: DAX is not SQL, so every dataset query has to be rewritten, and
report authors need DAX skills. Reserve this for reports where the "same numbers
as the dashboard" requirement is real.

---

## Licensing and capacity, factually

- Paginated reports in the Power BI service require a **capacity**: Premium
  (P SKU), Embedded (A SKU), Fabric (F SKU), or Premium Per User for PPU
  workspaces. They do not run in a Pro-only shared-capacity workspace.
- Viewers of a paginated report in a capacity-backed workspace need at least a
  **Free** licence for F64 and above; below that threshold, viewers need Pro or
  PPU. Check the current thresholds against Microsoft's documentation, as SKU
  rules change.
- On-premises sources need an **on-premises data gateway** (standard mode, not
  personal). The gateway is licensed with the capacity, but it is a machine you
  must run, patch and monitor.
- **Power BI Report Server** is licensed through SQL Server Enterprise with
  Software Assurance, or through Power BI Premium. It is the no-cloud option and
  is a superset of SSRS, so an SSRS estate moves to it with no feature loss.
- Fabric capacity is consumption-metered. A large paginated workload (hundreds of
  scheduled subscriptions rendering PDFs) consumes capacity units that the
  interactive workload also needs. Size for the peak, which for paginated is
  usually month end.

---

## The decide-per-report matrix

Four outcomes. Assign one to every report before any work starts.

| Outcome | Signals | Effort |
| --- | --- | --- |
| **Keep paginated** | Fixed layout, print or PDF destination, regulatory format, per-row export, subscription-delivered, pixel fidelity required | Low: re-point and validate |
| **Convert to interactive** | Users ask to slice it, more than five data regions, no print destination, exploratory questions, a Power BI report already covers most of it | High: rebuild in Power BI, but the payoff is a reusable model |
| **Retire** | No executions in 90 days, owner cannot be found, replaced by another report | Trivial, and it is the highest-value outcome |
| **Merge with a duplicate** | A near-identical report exists; one is a strict superset of the other | Medium: pick a keeper, add missing columns, redirect subscriptions |

### Signals, concretely

Run these three checks over the estate before the matrix meeting.

**Usage.** From `ExecutionLog3` (query in `performance.md`): reports with zero
runs in 90 days are retirement candidates by default. Make the owner argue for
keeping them, not the other way round.

**Complexity and compatibility.** Run the compatibility scan. A report with a
`subreport` blocker plus `custom_code` plus `custom_report_item` is not a
"re-point and validate" report; it is a rebuild, and that is the moment to ask
whether it should be interactive instead.

**Duplication.** This repository ships duplicate and reuse analysis at
`skills/report-lineage/` and in `backend/app/core/complexity/duplicates.py`. It
fingerprints each report across seven dimensions (normalised SQL, table
signatures, columns, measures, parameters, visual types, and content terms),
scores pairs by weighted Jaccard similarity, and clusters the results.

The verdicts and what to do with them:

| Verdict | Similarity | Action |
| --- | --- | --- |
| `identical` | at or above 0.95 | Retire all but the keeper |
| `near_duplicate` | at or above 0.82 | Merge: add the missing columns to the keeper, then retire |
| `overlapping` | at or above 0.62 | Consider one semantic model with multiple pages |
| `related` | at or above 0.45 | Review; probably keep both |

The keeper is the report with the broadest surface (most columns plus tables),
because it is the natural survivor of a merge. Cluster-level actions are
`retire_duplicates`, `merge_into_one`, `single_model_multi_page` and `review`.

Redundancy is quoted as `(member_count - 1) * 100`, so three equivalent reports
are 200% redundant. At portfolio level,
`portfolio_redundancy_pct = reports_saved / total_reports * 100`.

The verified lab result in `docs/ssrs-lab-setup.md` shows what this looks like in
practice: 7 reports, 1 cluster, 2 removable, 28.6% portfolio redundancy.

Before building anything new, run the reuse search
(`POST /api/report-studio/reuse-check` or
`POST /api/estate/{id}/reuse-search`). It ranks the existing estate against a
natural-language request and returns `reuse_as_is`, `extend` or `reference`. The
cheapest migration is the one where the report already exists.

---

## Phased migration plan template

Adapt the numbers; keep the phase boundaries.

### Phase 0: Inventory and baseline (2 to 3 weeks)

- Connect to the report server and pull the full catalog. This repository does it
  with one `GET /CatalogItems` call and local filtering
  (`backend/app/core/report_server/rest_api.py`).
- Download every `.rdl`, `.rsd` and `.rds` definition.
- Run `ExecutionLog3` usage aggregation over the last 90 days.
- Run the compatibility scan over every report.
- Run duplicate and consolidation analysis.

**Exit criteria**: a spreadsheet with one row per report carrying path, owner,
last run, run count, blocker count, warning count, duplicate cluster id, and a
proposed outcome from the four-way matrix.

### Phase 1: Retire (2 weeks)

Start here. It is the cheapest phase and it shrinks every subsequent one.

- Notify owners of zero-usage reports with a 10 working day objection window.
- Delete subscriptions first, then hide the items, then delete after 30 days.
- Verify no drillthrough or subreport in a surviving report targets a retired
  one. A retired drillthrough target produces an error inside a healthy-looking
  report.

**Exit criteria**: estate size reduced, with the reduction quantified.

### Phase 2: Consolidate duplicates (3 to 4 weeks)

- For each cluster, confirm the algorithmic keeper with the business owner.
- Add the columns and parameters the other members carry (the duplicate report
  lists them per member).
- Repoint subscriptions and drillthrough references to the keeper.
- Retire the rest.

**Exit criteria**: portfolio redundancy percentage reduced to the agreed target.

### Phase 3: Build the target platform (parallel with 1 and 2)

- Stand up the target: PBIRS instance, or Fabric workspace on a capacity.
- Install and configure the on-premises data gateway if any source stays
  on-premises.
- Create the folder structure and the security groups (see `deployment.md`).
- Create the target data sources. For Fabric, decide Warehouse versus Lakehouse
  endpoint versus semantic model per source system, not per report.
- Build and test the deployment pipeline against three throwaway reports.

**Exit criteria**: a report deployed by pipeline, rendering from the target data
source, delivered by subscription, with permissions verified.

### Phase 4: Migrate the clean reports (bulk, 4 to 8 weeks)

Reports with zero blockers and zero or few warnings.

- Run the RDL-to-RDL transform with the appropriate `RepointTarget`.
- Review each `compatibility_report.md`.
- Deploy with `PUT` or `POST` per `deployment.md`.
- Reconcile: run old and new side by side for one full period and diff the
  numbers. Automate the diff; do not eyeball it.
- Migrate subscriptions.

**Exit criteria**: each wave signed off by its business owner after a full-period
reconciliation.

### Phase 5: Rework the blocked reports (6 to 12 weeks)

Reports with subreports, custom code, custom report items or unsupported data
sources.

- Re-model subreports as nested groups or drillthrough.
- Move custom code into SQL.
- Replace custom report items.
- Re-point unsupported data sources, which usually means landing the data in the
  warehouse first.

Treat each of these as a small development project with its own review gate, not
as a migration task.

**Exit criteria**: zero blockers remaining in the estate.

### Phase 6: Convert to interactive (ongoing)

Reports flagged "convert to interactive" in the matrix. This is not a migration;
it is a rebuild against a semantic model, and it should be sequenced by business
value rather than by migration convenience.

### Phase 7: Decommission (2 weeks)

- Confirm zero traffic to the old server for 30 days.
- Archive the catalog database backup and the definition export.
- Remove the SSRS URL reservations and shut the service down.
- Keep the archive for the retention period the business agreed in stage 1 of the
  authoring workflow.

---

## Reconciliation, the part that is always underestimated

Every migrated report needs a numeric proof. The cheapest reliable method:

1. Render the old report to CSV with a fixed parameter set.
2. Render the new report to CSV with the same parameter set.
3. Diff them with a script that sorts both by the key columns and compares
   numerically with a tolerance.

```powershell
$old = Import-Csv .\old.csv | Sort-Object Region, SalesMonth
$new = Import-Csv .\new.csv | Sort-Object Region, SalesMonth

Compare-Object $old $new -Property Region, SalesMonth, GrossSales |
    Format-Table -AutoSize
```

Do this for at least three parameter sets: a typical one, an empty one, and the
worst-case volume. Store the evidence with the sign-off.
