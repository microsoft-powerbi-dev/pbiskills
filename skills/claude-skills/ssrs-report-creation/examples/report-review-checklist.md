# Pre Go-Live Review Checklist

Run this before a paginated report is released. The reviewer must be someone
other than the author.

Record evidence, not opinions: attach the PDF, the XLSX, the CSV, the
reconciliation output and the `ExecutionLog3` row.

```
Report:        ________________________________________
Catalog path:  ________________________________________
Author:        ____________________  Reviewer: _________
Date:          ____________________  Verdict:  pass / pass with actions / fail
```

---

## 1. Correctness

- [ ] The detail grain matches the one sentence agreed in the intake form.
- [ ] Every measure matches its agreed SQL definition, checked expression by
      expression.
- [ ] The report's numbers tie to the named source for at least one full period,
      with the diff attached as evidence.
- [ ] Group subtotals sum to the grand total.
- [ ] The grand total equals an independent `SELECT SUM(...)` against the source
      with the same predicates.
- [ ] Row counts match: the report's row count equals the query's row count with
      no report-level filter silently removing rows.
- [ ] Percentages that should sum to 100 do so, and any that do not are labelled
      to explain why (for example, a Top-N table).
- [ ] Zero-row behaviour verified: a parameter set returning nothing shows a
      no-data message, not an empty grid.
- [ ] Null handling verified: null measures render as a deliberate value
      (`n/a`, `-`, `0`), not blank-by-accident, and nulls do not silently drop
      rows through an inner join.
- [ ] Division by zero produces no `#Error` cell anywhere. Ratios are computed
      in SQL with `NULLIF` rather than guarded with `IIf`.
- [ ] Date boundaries are half-open (`>= @From AND < DATEADD(day,1,@To)`), so
      rows timestamped late on the final day are included.
- [ ] `SELECT DISTINCT`, if present, has been justified: removing it produces the
      documented grain's row count.

---

## 2. Parameters

- [ ] Every parameter has a `Prompt` in business language, not the column name.
- [ ] Every parameter has a `DataType` that matches its query parameter.
- [ ] Defaults produce a useful report on first open, with no manual selection.
- [ ] Date defaults are relative expressions, not hard-coded dates.
- [ ] Cascading parameters refresh in the right order; selecting the parent
      repopulates the child.
- [ ] Multi-value parameters return the correct rows for one value, several
      values, and all values.
- [ ] A multi-value parameter used against a stored procedure is bound via
      `=Join(Parameters!X.Value, ",")` and split server-side, not passed
      directly (which would silently use only the first value).
- [ ] `AllowBlank` and `Nullable` are set deliberately, not left at the default.
- [ ] Hidden and internal parameters exist only where a drillthrough or
      subreport supplies them, and each has a default so the report can still
      render standalone.
- [ ] Available-values datasets are filtered by entitlement where row-level
      security applies, so the dropdown offers nothing the user cannot see.
- [ ] Invalid input is handled: an out-of-range date, a reversed date range, an
      empty multi-value selection.

---

## 3. Layout

- [ ] Body width plus left margin plus right margin is less than or equal to the
      page width. Arithmetic shown: ______ + ______ + ______ <= ______
- [ ] The PDF has no blank pages, checked at worst-case volume.
- [ ] No unintended trailing blank page from a `BreakLocation` of `End` on the
      final group.
- [ ] Column headers are inside the Tablix, not floating textboxes above it.
- [ ] Group headers repeat on each page: both `RepeatOnNewPage` and
      `KeepWithGroup` are set.
- [ ] Page breaks are on the group, not on a surrounding rectangle, and produce
      exactly one break per instance.
- [ ] Page header and footer render on the intended pages, with
      `PrintOnFirstPage` and `PrintOnLastPage` set explicitly.
- [ ] A page footer shows `Page X of Y` and the execution time.
- [ ] Long text values behave as intended: either they grow (`CanGrow`) or they
      truncate, and the choice is deliberate.
- [ ] Conditional visibility does not push the Tablix past the printable area
      when the hidden branch is shown.
- [ ] Multi-column layouts use the column width as the body width, not the page
      width.

---

## 4. Formatting

- [ ] Every numeric cell has a `Format` string (`C2`, `N0`, `N2`, `P1`,
      `#,##0.00`).
- [ ] Every date cell has an explicit pattern (`yyyy-MM-dd` or the agreed
      locale-appropriate one).
- [ ] No `FormatCurrency`, `FormatNumber`, `FormatPercent` or `CStr` in any value
      expression. They export to Excel as text.
- [ ] Percentages use `P1` and the underlying value is a fraction, not already
      multiplied by 100.
- [ ] Numbers are right-aligned, dates right or centre, text left.
- [ ] Alternating row colour comes from a `RowNumber` expression, not manual
      shading, and is scoped so it restarts sensibly at group boundaries.
- [ ] One font family, at most three sizes, one accent colour.
- [ ] Fonts used exist on the report server, not only on the author's machine.
- [ ] Conditional formatting is paired with a glyph or label, not colour alone.
- [ ] Negative numbers are visually distinct and use the agreed convention
      (minus sign or parentheses), consistently.
- [ ] Locale: currency symbol, thousand separator and date order match the
      audience, and `Language` on the report is set deliberately.

---

## 5. Export

Verify by opening the file, not by trusting the preview.

- [ ] **PDF** rendered at worst-case volume. Page count recorded: ______
- [ ] PDF: no blank pages, headers repeat, footer correct, fonts render.
- [ ] **XLSX** rendered. Opened in Excel.
- [ ] XLSX: numeric cells are right-aligned numbers that `SUM` correctly, not
      text.
- [ ] XLSX: no unexpected merged cells; report items are aligned on a grid.
- [ ] XLSX: worksheet tabs are named (via `PageName`) where page breaks split
      the report, not `Sheet1`, `Sheet2`.
- [ ] XLSX: row count is within the 1,048,576 limit at worst-case volume.
- [ ] **CSV** rendered. Opened in a text editor.
- [ ] CSV: exactly one clean data block; decorative items and secondary regions
      are marked `DataElementOutput` of `NoOutput`.
- [ ] CSV: values are parseable downstream (compliant mode, or format strings
      that do not emit currency symbols).
- [ ] Any format-specific visibility expression behaves correctly in every
      listed format.
- [ ] Interactive-only features (toggle, sort, drillthrough) are not the only
      route to required information, since they do nothing in exports.

---

## 6. Performance

- [ ] Worst-case parameter set has actually been executed, not estimated.
      Parameter set used: ____________________________
- [ ] `ExecutionLog3` row captured. Times recorded:
      `TimeDataRetrieval` ______ ms, `TimeProcessing` ______ ms,
      `TimeRendering` ______ ms, `RowCount` ______
- [ ] Total time is within the agreed acceptance criterion.
- [ ] `TimeProcessing` is not dominant, which would indicate report-level
      filtering or aggregation that belongs in SQL.
- [ ] The query returns only the rows the report displays; it does not return
      millions of detail rows to render twelve.
- [ ] The query returns only the columns the report references.
- [ ] No `SELECT *`, no leading-wildcard `LIKE '%...`, no cursor, no
      unintentional `CROSS JOIN`.
- [ ] No nested `IIf` three or more deep; `Switch` or a SQL `CASE` used instead.
- [ ] No chained `Replace` three or more deep; normalised upstream instead.
- [ ] No `Lookup` called more than once per row for the same dataset; joined in
      SQL instead.
- [ ] No subreport inside a detail row.
- [ ] At most five Tablix regions and five charts, or a documented reason.
- [ ] Parameter available-values datasets are shared and cached where the
      dimension is large.
- [ ] An execution plan for the worst-case parameter set has been reviewed by a
      DBA and shows no scan on the fact table.

---

## 7. Operations

- [ ] The data source points at the **target environment's** database, verified
      on the server with `GetItemDataSources`, not assumed from the RDL.
- [ ] `OverwriteDataSources` was `False` for this deployment, so no other
      report's binding was changed.
- [ ] Credential mode is correct for how the report runs: stored or none if a
      subscription, cache or snapshot is involved.
- [ ] The report renders **on the server** with production credentials, not just
      in the designer.
- [ ] Item or folder permissions grant the intended AD groups the intended role,
      and grant nothing to individuals.
- [ ] Permission inheritance is intact unless breaking it was a deliberate,
      recorded decision.
- [ ] Subscriptions are created, owned by a service account, and their next run
      time is in the future.
- [ ] Subscription failures alert a monitored destination.
- [ ] Caching or snapshot policy matches the intake form.
- [ ] The previous definition was downloaded before overwrite and stored as the
      rollback artifact. Location: ____________________________
- [ ] Drillthrough targets exist in this environment, and the audience has
      Browser rights on them.

---

## 8. Maintainability

- [ ] The query lives in a stored procedure or a view, in source control, not
      inline in the RDL (unless it is a trivial single-table select).
- [ ] The proc or view DDL is committed alongside the RDL.
- [ ] Dataset names describe what they return (`dsSalesByRegion`), not
      `DataSet1`.
- [ ] Report item names are meaningful (`tblSalesDetail`, `txtRegionLabel`), not
      `Textbox47`.
- [ ] Field names are stable and aliased in SQL; nothing relies on
      auto-generated `Column1`.
- [ ] Every `Fields!X.Value` reference resolves to a declared dataset field.
- [ ] No custom code or custom assemblies, or a documented justification for
      each.
- [ ] No embedded connection string where a shared data source exists.
- [ ] The report name is a business name with no version suffix, author initials
      or date.
- [ ] The catalog description field is populated with a one-line purpose
      statement.
- [ ] The intake form is committed with the report, with all sign-offs recorded.
- [ ] Retirement criteria and a review date are recorded.
- [ ] If this report overlaps an existing one, the duplicate analysis verdict is
      recorded and the decision (keep both, merge, retire) is signed off.

---

## Actions arising

| # | Finding | Severity | Owner | Due | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | | blocker / major / minor | | | |
| 2 | | | | | |
| 3 | | | | | |

A blocker prevents release. A major is fixed within one sprint. A minor is
logged and batched.
