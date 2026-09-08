# Paginated Report Requirements Intake

Fill this in before writing any SQL. Every blank left as `TBD` is a defect that
surfaces later at ten times the cost.

Copy this file, rename it after the report, and keep it with the RDL in source
control.

---

## 0. Is paginated the right tool?

Answer this first. Do not skip it because someone asked for "a report".

| Signal | Present? | Points to |
| --- | --- | --- |
| Must fit an official form, invoice, statement or remittance | [ ] | Paginated |
| Emailed as PDF or Excel on a schedule | [ ] | Paginated |
| Thousands of rows exported for downstream processing | [ ] | Paginated |
| Print fidelity or a fixed page layout is required | [ ] | Paginated |
| A regulator or auditor specifies the format | [ ] | Paginated |
| Users ask "can I slice this by anything else?" | [ ] | Power BI interactive |
| Users want to build their own views | [ ] | Power BI interactive |
| The request is really a dashboard with more than five regions | [ ] | Power BI interactive |
| Both: a dashboard plus a printable detail sheet | [ ] | Interactive report with a paginated drillthrough |

**Decision**: `paginated` / `interactive` / `both` / `reuse an existing report`

**Rationale** (one or two sentences):

**Does it already exist?** Run the reuse check against the estate before
building. Record the verdict here.

- Closest existing report:
- Similarity verdict: `reuse_as_is` / `extend` / `reference` / `none`
- Decision: build new / extend the existing one / clone

---

## 1. Identity

| Field | Value |
| --- | --- |
| Report name (business name, title case, no version suffix) | |
| Catalog folder | |
| Business owner (name, role) | |
| Technical owner (team) | |
| Requested by | |
| Date requested | |
| Target go-live date | |
| Related reports (drillthrough parent or child) | |

---

## 2. Audience and delivery

| Field | Value |
| --- | --- |
| Primary audience (roles, approximate headcount) | |
| Secondary audience | |
| Delivery mechanism | portal / email subscription / file share / URL access / API |
| Delivery format(s) | PDF / XLSX / CSV / Word / MHTML / XML |
| If subscription: schedule | |
| If subscription: recipients or driving query | |
| If subscription: standard or data-driven | |
| Subscription owner (must be a service account) | |
| On failure, who is alerted | |

**Export formats drive layout constraints more than anything else.** If both PDF
and Excel are listed, note it in the layout section, because the two impose
different rules.

---

## 3. Grain

Complete this sentence. Do not proceed until it is unambiguous.

> One row of the detail section represents one ____________ per ____________.

| Field | Value |
| --- | --- |
| Detail grain | |
| Grouping levels (outer to inner) | |
| Subtotal levels required | |
| Grand total required | yes / no |

---

## 4. Columns and definitions

One row per column. The definition must be in business language, agreed by the
data owner, and precise enough to write SQL from.

| # | Column heading | Business definition | Source object and column | Type | Format string | Aggregation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |

Format strings to use: `C2` currency, `N0` integer, `N2` decimal, `P1` percent,
`yyyy-MM-dd` date, `#,##0.00` explicit numeric. Never a `FormatCurrency()`
expression.

**Calculated measures**, written as SQL, agreed by the data owner:

```sql
-- Measure name:
-- Definition:

-- Measure name:
-- Definition:
```

---

## 5. Parameters

| # | Name | Prompt | Type | Multi-value | Cascades from | Default value | Available values from | Visibility |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | | | | [ ] | | | | visible / hidden / internal |
| 2 | | | | [ ] | | | | |
| 3 | | | | [ ] | | | | |
| 4 | | | | [ ] | | | | |

Checks:

- [ ] Every parameter has a default that renders a useful report on first open.
- [ ] Cascade order is listed parent first; it becomes the `<ReportParameters>`
      order in the RDL.
- [ ] Any multi-value parameter used against a stored procedure has a documented
      binding strategy (`Join` plus `STRING_SPLIT`, or a table-valued parameter).
- [ ] A "select all" default has been checked against worst-case volume.
- [ ] Available-values datasets are themselves filtered by entitlement where
      row-level security applies.

---

## 6. Volume

| Field | Value |
| --- | --- |
| Typical detail row count | |
| Worst-case detail row count (and which parameters produce it) | |
| Expected page count in PDF (typical / worst case) | |
| Expected concurrent users at peak | |
| Peak period (month end, quarter end, daily 09:00) | |
| Acceptable execution time (typical / worst case) | |

"A lot" is not an answer. If the number is unknown, run a `COUNT(*)` against the
source with the worst-case predicate and record it here.

---

## 7. Refresh and latency

| Field | Value |
| --- | --- |
| Source system | |
| Source refresh schedule (ETL completion time) | |
| Restatement window (how many days can the data change after load) | |
| Acceptable data latency | |
| Execution mode | live query / cached / execution snapshot |
| If cached: expiry policy | |
| If snapshot: refresh schedule | |
| Report run time must be after | |

---

## 8. Security and row-level rules

| Field | Value |
| --- | --- |
| Who may view the report (AD groups, not individuals) | |
| Who may edit or publish | |
| Is the data restricted per user or per group? | yes / no |
| If yes, the rule in business language | |
| Entitlement source (table, AD group mapping, native RLS) | |
| Pattern | entitlement join / group expansion / SQL Server RLS / data-driven subscription |
| Credential mode required | stored / integrated / prompt / none |
| Does a subscription need to run this? | yes / no |

If a subscription is required, the credential mode must be **stored** or
**none**. Windows integrated cannot run unattended.

If per-recipient filtering is required in a subscription, note that
`User!UserID` returns the subscription **owner**, not the recipient, and a
data-driven subscription is needed.

Data classification:

- [ ] Contains personal data
- [ ] Contains financial data subject to audit
- [ ] Contains data restricted by contract or regulation
- [ ] None of the above

---

## 9. Retention and history

| Field | Value |
| --- | --- |
| Must past executions be retained? | yes / no |
| If yes, how long | |
| History snapshot schedule | |
| Delivered file retention (file share deliveries) | |
| Report retirement criteria (for example, zero runs in 90 days) | |
| Review date | |

Fill in the retirement criteria on day one. It is the stage most often skipped,
and skipping it is why estates grow without limit.

---

## 10. Layout notes

| Field | Value |
| --- | --- |
| Paper size and orientation | A4 / Letter / Legal, portrait / landscape |
| Margins | |
| Usable body width (page width minus both margins) | |
| Page break requirement (per group, per N rows, none) | |
| Headers repeat on each page | yes / no |
| Logo or branding required | |
| Region type | table / matrix / list / chart plus table / multi-column |
| Multi-format export (if both PDF and Excel, note the constraints) | |

Usable widths for reference: A4 portrait with 0.5in margins is 7.27in; Letter
portrait with 0.5in margins is 7.5in; A4 landscape is 10.69in.

---

## 11. Acceptance criteria

Numeric and checkable. "Looks right" is not acceptance.

| # | Criterion | How it will be verified | Verified by |
| --- | --- | --- | --- |
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |

Include at least one reconciliation criterion, of the form:

> `<measure>` for `<period>` ties to `<named source or existing report>` within
> `<tolerance>`.

And at least one performance criterion:

> The report completes in under `<n>` seconds at worst-case volume
> (`<parameter set>`), measured from `ExecutionLog3`.

---

## 12. Sign-off

| Stage | Name | Role | Date |
| --- | --- | --- | --- |
| Requirements agreed | | Business owner | |
| Data contract agreed | | Data owner / DBA | |
| Query reviewed | | DBA | |
| Report reviewed | | Peer developer | |
| Accepted | | Business owner | |
| Deployed | | Release owner | |

---

## Open questions

| # | Question | Owner | Needed by | Answer |
| --- | --- | --- | --- | --- |
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
