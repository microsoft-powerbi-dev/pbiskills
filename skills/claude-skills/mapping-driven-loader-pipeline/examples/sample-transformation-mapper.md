---
generated_by: mapping-driven-loader-pipeline
generator_version: "1.0"
source_workbook: sample-mapping-member-loader.xlsx
source_sha256: df2d1f67f3deb84d0000000000000000000000000000000000000000000000000
digest_schema_version: "1.0"
generated: 2026-09-11
do_not_commit: false
---

> **This example is committed deliberately.** It is generated from
> `sample-mapping-member-loader.xlsx`, which is **entirely fictional** — no
> real member, plan, or source system is named, and the workbook contains no
> data values of any kind. A mapper generated from a *real* mapping document
> carries `do_not_commit: true` and the warning block below, and is written
> outside version control.
>
> Template of that block, for real runs:
> *Do not commit. This file names internal source systems, tables and columns
> transcribed from a customer mapping document.*

# Transformation mapper — Member Loader interface

## Summary

| | |
| --- | --- |
| Target files | 2 — `MEMBER_LOADER` (16 fields), `DEPENDENT_LOADER` (4 fields) |
| Fields included / excluded | 19 included, 1 excluded |
| Distinct rules | 8 |
| Open questions | **4 — two of them blocking** |
| Target name resolved by | header match (Excel column C, agrees with positional fallback) |
| `mandatory` column read as | `mandatory` sense (not inverted) |
| Sheets read | `Member Loader` (20 field rows) |
| Sheets skipped | `Code Sets` — no header row matched. **Checked for the missing RELATIONSHIP values; it only says "pending vendor confirmation".** |
| Unmatched columns | none |
| Reader findings | 2 error, 9 warning, 2 info |

The skipped sheet matters: a named-but-unlisted code set is usually hiding on
one, and in this document it is not. That turns Open Question 1 from "look
harder" into "ask the vendor".

---

## File: `MEMBER_LOADER`

### File contract

| Setting | Value | Provenance |
| --- | --- | --- |
| Output file stem | `MEMBER_LOADER` | stated in mapping document (`file name`) |
| Part naming | `MEMBER_LOADER_part{NNN}.txt` | confirmed by the interface team lead, 2026-09-11 |
| Delimiter | `\|` | confirmed — pipe-delimited per the interface request |
| Line ending | `CRLF` | confirmed by the interface team lead, 2026-09-11 |
| Header row | none | confirmed |
| Trailer row | `TRAILER\|<rows in that part>`, **per part** | confirmed |
| Null representation | spaces for `CHAR`, zeroes for `NUMBER` | confirmed — see `TERM_DATE`'s additional comment, which forced the question |
| Padding convention | `CHAR` right-pads with spaces; `NUMBER` left-pads with zeroes | confirmed once for the whole file |
| **Split rule** | **50,000 records per file; no byte limit** | **confirmed by the interface team lead, 2026-09-11.** The document states none, and this skill has no default. |
| Encoding | UTF-8 | confirmed — the vendor accepts UTF-8, so byte counts equal character counts for this layout |

### Field table

| # | Target column | Base type | Size | Format | Mand. | Source | Source field | Rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `MEMBER_ID` | CHAR | 12 | — | Y | `dbo.Member` | `MemberKey` | [`pad_left_zero`](#pad_left_zero) |
| 2 | `GROUP_ID` | CHAR | 10 | — | Y | `dbo.Grp` | `GroupCode` | [`passthrough`](#passthrough) |
| 3 | `LAST_NAME` | CHAR | 30 | — | Y | `dbo.Member` | `LastName` | [`trim_upper`](#trim_upper) |
| 4 | `FIRST_NAME` | CHAR | 20 | — | Y | `dbo.Member` | `FirstName` | [`trim_upper`](#trim_upper) |
| 5 | `MIDDLE_INIT` | CHAR | 1 | — | N | `dbo.Member` | `MiddleName` | [`first_char_upper`](#first_char_upper) |
| 6 | `BIRTH_DATE` | DATE | 8 | YYYYMMDD | Y | `dbo.Member` | `BirthDate` | [`date_yyyymmdd`](#date_yyyymmdd) |
| 7 | `GENDER_CODE` | CHAR | 1 | — | Y | `dbo.Member` | `GenderCode` | [`gender_code`](#gender_code) |
| 8 | `RELATIONSHIP` | CHAR | 2 | — | Y | `dbo.Enrollment` | `RelationCode` | **blocked** — see OQ 1 |
| 9 | `EFF_DATE` | DATE | 8 | YYYYMMDD | Y | `dbo.Enrollment` | `EffectiveDate` | [`date_yyyymmdd`](#date_yyyymmdd) |
| 10 | `TERM_DATE` | DATE | 8 | YYYYMMDD | N | `dbo.Enrollment` | `TerminationDate` | [`date_yyyymmdd_or_spaces`](#date_yyyymmdd_or_spaces) |
| 11 | `PLAN_CODE` | CHAR | 8 | — | Y | `dbo.Enrollment` | `PlanCode` | [`constant`](#constant) — see OQ 3 |
| 12 | `PREMIUM_AMT` | NUMBER | 9 (7 int + 2 implied) | implied 2dp, zero filled | N | `dbo.Billing` | `PremiumAmount` | [`implied_decimal_2`](#implied_decimal_2) |
| 13 | `STATUS_FLAG` | CHAR | 1 | — | Y | `dbo.Enrollment` | `StatusCode` | **blocked** — see OQ 2 |
| 14 | `RECORD_SEQ` | NUMBER | 7 | — | Y | *derived* | — | [`row_sequence`](#row_sequence) — see OQ 4 |
| 15 | `LOAD_DATE` | DATE | 8 | YYYYMMDD | Y | *derived* | — | [`constant`](#constant) |

### Excluded fields

| Target column | Size | Source | Stated reason |
| --- | --- | --- | --- |
| `LEGACY_KEY` | CHAR(15) | `dbo.Member.LegacyId` | `target file needed = N`. "Not needed in the target file; retained here for reconciliation against the legacy extract." |

Excluded, not deleted. It stays in `layout.py`'s `EXCLUDED` tuple so the
decision is visible and reversible, and it is never written to the output.

### Per-field detail

#### 1. `MEMBER_ID`

- **Description** (verbatim): Unique member identifier assigned at enrolment
- **Source**: `dbo.Member.MemberKey` — `int(4)`, "Surrogate key, one row per member"
- **Target**: `CHAR(12)`, mandatory
- **Transformation rule** (verbatim):
  > Left pad with zeroes to 12
- **Interpreted as**: cast the integer key to text, right-justify in 12
  characters padding with `0`. Classification: **string transform**.
- **Notes**: source width 4 → target width 12, so no truncation risk. A value
  wider than 12 raises rather than truncating — the document promised the
  vendor a 12-wide key, so an over-width value is a data defect, not a case to
  absorb. Confirmed.

#### 2. `GROUP_ID`

- **Description** (verbatim): Employer group the member is enrolled under
- **Source**: `dbo.Grp.GroupCode` — `varchar(10)`, "Natural group code, unique per employer group"
- **Target**: `CHAR(10)`, mandatory
- **Transformation rule** (verbatim):
  > Direct move
- **Interpreted as**: **passthrough**, then right-pad to 10 with spaces per the
  file's `CHAR` convention.

#### 3. `LAST_NAME`

- **Description** (verbatim): Member surname as held on the enrolment record
- **Source**: `dbo.Member.LastName` — `varchar(40)`, "Free text, not validated on entry"
- **Target**: `CHAR(30)`, mandatory
- **Transformation rule** (verbatim):
  > Trim and convert to upper case
- **Interpreted as**: strip leading/trailing whitespace, upper-case, right-pad
  to 30. Classification: **string transform**.
- **⚠ Truncation risk**: source width **40** exceeds target width **30**, and
  the document states no truncation rule. Resolved as: **truncate to 30 and
  count the occurrence in the run log** — confirmed by the interface team lead,
  2026-09-11, on the grounds that the vendor layout is fixed and a 31-character
  surname cannot be transmitted. This is a decision the document did not make.

#### 4. `FIRST_NAME`

- **Description** (verbatim): Member given name
- **Source**: `dbo.Member.FirstName` — `varchar(25)`, "Free text, not validated on entry"
- **Target**: `CHAR(20)`, mandatory
- **Transformation rule** (verbatim):
  > Trim and convert to upper case
- **Interpreted as**: as `LAST_NAME`. Same rule, same registry entry.
- **⚠ Truncation risk**: source 25 → target 20. Same resolution as `LAST_NAME`.

#### 5. `MIDDLE_INIT`

- **Description** (verbatim): Middle initial
- **Source**: `dbo.Member.MiddleName` — `varchar(25)`, "Full middle name where captured"
- **Target**: `CHAR(1)`, optional
- **Transformation rule** (verbatim):
  > First character of middle name, upper case
- **Additional comment** (verbatim): Space if the member has no middle name
- **Interpreted as**: trim, take character 1, upper-case; emit a single space
  when the source is null or blank. The additional comment supplies the null
  branch the rule column omitted — which is where these documents normally put
  it.
- **Notes**: the reader flags source 25 → target 1 as a truncation risk. It is
  not one here; extracting one character is the stated intent. Recorded so the
  warning is visibly dismissed rather than silently ignored.

#### 6. `BIRTH_DATE`

- **Description** (verbatim): Member date of birth, used by the vendor for matching
- **Source**: `dbo.Member.BirthDate` — `date`, source format `YYYY-MM-DD`, "Captured at enrolment"
- **Target**: `DATE(8)`, format `YYYYMMDD`, mandatory
- **Transformation rule** (verbatim):
  > Format as YYYYMMDD
- **Interpreted as**: **parse** to a date per `source format`, then **render**
  `%Y%m%d`. Classification: **date render**. Not string slicing — a source that
  drifts between `2024-01-15` and `20240115` breaks slicing silently and breaks
  a parse loudly.
- **Notes**: mandatory with a nullable-looking source. Confirmed non-nullable at
  source, so no default is needed; a null fails the run.

#### 7. `GENDER_CODE`

- **Description** (verbatim): Gender code as required by the vendor layout
- **Source**: `dbo.Member.GenderCode` — `char(1)`, "Single character, nullable"
- **Target**: `CHAR(1)`, mandatory
- **Codes** (verbatim): `M = Male, F = Female, X = Non-binary, U = Unknown`
- **Transformation rule** (verbatim):
  > Map per codes
- **Additional comment** (verbatim): Default to U when the source value is null
- **Interpreted as**: **code lookup** against the four pairs above, with `U`
  for null. Unmatched non-null value → raise, confirmed: a code outside the
  vendor's domain must not be transmitted, and must not be silently coerced to
  `U`, which would misreport it as "unknown" rather than "wrong".

#### 8. `RELATIONSHIP` — **BLOCKED**

- **Description** (verbatim): Relationship of the member to the subscriber
- **Source**: `dbo.Enrollment.RelationCode` — `char(2)`, "Internal relationship code"
- **Target**: `CHAR(2)`, mandatory
- **Codes** (verbatim): `see RELATIONSHIP codes`
- **Transformation rule** (verbatim):
  > Translate using code table
- **Comments** (verbatim): TBD - confirm the full vendor code list before build
- **Interpreted as**: nothing yet. The document names a code set and does not
  list it, and the `Code Sets` tab says only "pending vendor confirmation". See
  **Open Question 1**. No transform is generated for this field; the pipeline
  will not build until it is answered.

#### 9. `EFF_DATE`

- **Description** (verbatim): Date the member's current coverage began
- **Source**: `dbo.Enrollment.EffectiveDate` — `date`, `YYYY-MM-DD`, **"One row per member per plan year"**
- **Target**: `DATE(8)`, `YYYYMMDD`, mandatory
- **Transformation rule** (verbatim):
  > Format as YYYYMMDD
- **Interpreted as**: same as `BIRTH_DATE`; same registry entry.
- **⚠ Grain**: this field's `source description` is where the file's real grain
  is written down — one row per member **per plan year**, not one row per
  member. It drives the `ORDER BY` and the expected volume. See Source lineage.

#### 10. `TERM_DATE`

- **Description** (verbatim): Date coverage ended; blank while coverage is active
- **Source**: `dbo.Enrollment.TerminationDate` — `date`, `YYYY-MM-DD`, "Null while the enrolment is open"
- **Target**: `DATE(8)`, `YYYYMMDD`, optional
- **Transformation rule** (verbatim):
  > Format as YYYYMMDD
- **Additional comment** (verbatim): Spaces, not zeroes, when coverage is active
- **Interpreted as**: as `BIRTH_DATE`, but null renders as **8 spaces**, not
  `00000000` and not an empty field. A distinct registry entry from
  `date_yyyymmdd` because the null branch differs, and because this is the
  field that established the whole file's null convention.

#### 11. `PLAN_CODE` — contradiction, resolved

- **Description** (verbatim): Benefit plan the member is enrolled in
- **Source**: `dbo.Enrollment.PlanCode` — `varchar(8)`, "Benefit plan code"
- **Target**: `CHAR(8)`, mandatory
- **Transformation rule** (verbatim):
  > Direct move
- **Additional comment** (verbatim): Hardcode 'STANDARD' for phase 1; source
  the real value from phase 2
- **Interpreted as**: **constant `STANDARD`** for this phase. The additional
  comment is an exception clause layered over the rule, and it wins for phase
  1 — but only because it was confirmed. See **Open Question 3**: this is a
  temporary state with a scheduled reversal, and `PlanCode` must still be
  carried in the query so phase 2 is a one-line change.

#### 12. `PREMIUM_AMT`

- **Description** (verbatim): Monthly premium attributable to this member
- **Source**: `dbo.Billing.PremiumAmount` — `decimal(18,2)`, "Latest billed premium for the member"
- **Target**: `NUMBER`, size `9(7)V99` → **9 characters**, 7 integer digits + 2 implied decimals
- **Transformation rule** (verbatim):
  > Implied two decimals, no decimal point, zero fill left
- **Interpreted as**: multiply by 100, truncate to an integer, zero-pad left to
  9. Classification: **numeric render**. `1234.50` → `000123450`.
- **Notes**: the emitted width is **9, not 7** — `V` is an implied point that
  occupies no character, and sizing this field at 7 would under-size it by the
  scale. A value ≥ 10,000,000.00 does not fit and raises.
- **⚠ Precision**: source `decimal(18,2)` far exceeds 7 integer digits. Values
  above the target range are a data defect; confirmed to fail the run.
- **Open**: the rule says nothing about negatives (a credit/adjustment). No
  sign position is specified in an unsigned 9-character field. Raised as a
  note to the interface team; current data has no negatives, and a negative
  will raise rather than silently drop the sign.

#### 13. `STATUS_FLAG` — **BLOCKED**

- **Description** (verbatim): Active or terminated indicator
- **Source**: `dbo.Enrollment.StatusCode` — `char(1)`, "Internal status code"
- **Target**: `CHAR(1)`, mandatory
- **Codes** (verbatim): `A = Active, T = Terminated`
- **Transformation rule** (verbatim):
  > Derive from termination date as per current process
- **Comments** (verbatim): Confirm with the enrolment team
- **Interpreted as**: nothing. "As per current process" states no derivation.
  The obvious reading — `T` when `TerminationDate` is non-null and in the past,
  else `A` — is a **guess**, and the boundary cases (a future-dated
  termination, a termination equal to today, a reinstatement) are exactly where
  it would be wrong. Note also that `source field` is `StatusCode` while the
  rule says to derive from the termination date, so the two disagree about
  where the value even comes from. See **Open Question 2**.

#### 14. `RECORD_SEQ`

- **Description** (verbatim): Sequential record number within the transmission
- **Source**: *derived* — "Generated during extract"
- **Target**: `NUMBER(7)`, mandatory
- **Transformation rule** (verbatim):
  > Sequence number, incrementing by 1
- **Comments** (verbatim): Confirm whether the sequence restarts per file part
- **Interpreted as**: **row sequence** — stateful across the file, generated in
  the writer in deterministic `ORDER BY` order, zero-padded to 7. Scope
  (per-file vs per-part) is **Open Question 4**; it is not blocking because
  "the document says *within the transmission*" makes per-file the stated
  reading, but it must be confirmed because getting it wrong makes every
  record number after part 1 wrong.
- **Notes**: this is why the `ORDER BY` is mandatory. A non-deterministic order
  makes the sequence non-reproducible, and a re-send will not reconcile.

#### 15. `LOAD_DATE`

- **Description** (verbatim): Date the extract was produced
- **Source**: *derived* — "Extract run date"
- **Target**: `DATE(8)`, `YYYYMMDD`, mandatory
- **Transformation rule** (verbatim):
  > Hardcode the extract run date
- **Interpreted as**: **constant** — one value resolved once at run start and
  used for every row, not `date.today()` evaluated per row. A run that crosses
  midnight must not emit two different load dates in one file.

---

## Rule catalog

Eight distinct rules across 19 included fields. The last column is the one to
read: it records every place the document was silent and someone decided.

| Rule | Classification | Params | Used by | Unstated behaviour resolved as |
| --- | --- | --- | --- | --- |
| `passthrough` | passthrough | — | `GROUP_ID`, `DEP_*` (see below) | pad per the file's `CHAR`/`NUMBER` convention |
| `pad_left_zero` | string transform | width from `size`, char `0` | `MEMBER_ID`, `DEP_SEQ` | over-width value **raises** (confirmed) |
| `trim_upper` | string transform | width from `size` | `LAST_NAME`, `FIRST_NAME`, `DEP_LAST_NAME` | over-width value **truncates**, counted in the run log (confirmed) |
| `first_char_upper` | string transform | — | `MIDDLE_INIT` | null → one space (from `additional comment`) |
| `date_yyyymmdd` | date render | — | `BIRTH_DATE`, `EFF_DATE`, `DEP_BIRTH_DATE` | unparseable value **raises** (confirmed) |
| `date_yyyymmdd_or_spaces` | date render | — | `TERM_DATE` | null → 8 spaces, not zeroes (from `additional comment`) |
| `gender_code` | code lookup | 4 pairs from `codes` | `GENDER_CODE` | null → `U`; unmatched non-null **raises** (confirmed) |
| `implied_decimal_2` | numeric render | width 9, scale 2 | `PREMIUM_AMT` | out-of-range **raises**; negatives **raise** (no sign position specified) |
| `constant` | constant | literal in `layout.py` | `PLAN_CODE` (phase 1), `LOAD_DATE` | run date resolved once per run, not per row |
| `row_sequence` | row sequence | width 7 | `RECORD_SEQ` | **scope unconfirmed** — see OQ 4 |
| *(none)* | blocked | — | `RELATIONSHIP`, `STATUS_FLAG` | **nothing resolved** — OQ 1 and 2 |

---

## Source lineage

| | |
| --- | --- |
| Driving table | `dbo.Enrollment` — **not** `dbo.Member` |
| Grain | **one row per member per plan year**, per `EFF_DATE`'s source description |
| Identity column | `MemberKey`; full ordering key `(MemberKey, EffectiveDate)` |
| Joined tables | `dbo.Member` on `MemberKey`; `dbo.Grp` on `GroupCode`; `dbo.Billing` on `MemberKey` |
| Derived fields | `RECORD_SEQ`, `LOAD_DATE` — no source object |
| Tables the document names | `dbo.Member`, `dbo.Grp`, `dbo.Enrollment`, `dbo.Billing` |
| Tables confirmed by the requester | the same four — **no mismatch** |

Two things to carry into `extract.sql`:

- **The driving table is `dbo.Enrollment`**, because the grain is per plan year
  and enrolment is what makes it so. Driving from `dbo.Member` and joining
  enrolment would produce the same rows here, but reading the grain off the
  member table is the mistake that makes the next mapping revision wrong.
- **`dbo.Billing` can fan the extract out.** "Latest billed premium" is a
  **pick-one**, not a join — one member has many billing rows. It must be a
  windowed subquery (`ROW_NUMBER() OVER (PARTITION BY MemberKey ORDER BY
  BilledDate DESC) = 1`) in the query, not a plain join and not logic in
  `transforms.py`. This is the only grain-changing rule in the document and it
  is not labelled as one anywhere in the spreadsheet.

`ORDER BY MemberKey, EffectiveDate` — deterministic, and the basis of both the
part boundaries and `RECORD_SEQ`.

---

## Split and record-count contract

| | |
| --- | --- |
| Split rule | 50,000 records per file; no byte limit |
| Provenance | **confirmed by the interface team lead, 2026-09-11.** The mapping document states no split rule, and this skill has no default. |
| Whichever-first | n/a — only the record limit is set |
| Trailer | `TRAILER\|<rows in that part>`, per part; excluded from the record count |
| Rejected rows | none expected; a rejected row fails the run rather than being dropped, so counts always reconcile |
| Expected volume | ~1.2M rows → ~25 parts. Supplied by the requester, not derivable from the document. |
| Reproducibility | `ORDER BY MemberKey, EffectiveDate` makes part boundaries stable across runs, so a re-send matches the original |

---

## File: `DEPENDENT_LOADER`

Four fields, same source database, grain **one row per dependent per
subscriber** (`dbo.Dependent.DependentSeq`), identity `(MemberKey,
DependentSeq)`. Every rule is one already in the catalog above — no new
transformations.

| # | Target column | Base type | Size | Mand. | Source field | Rule |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `MEMBER_ID` | CHAR | 12 | Y | `dbo.Dependent.MemberKey` | `pad_left_zero` |
| 2 | `DEP_SEQ` | NUMBER | 2 | Y | `dbo.Dependent.DependentSeq` | `pad_left_zero` |
| 3 | `DEP_LAST_NAME` | CHAR | 30 | Y | `dbo.Dependent.LastName` | `trim_upper` (source 40 → truncation, same resolution) |
| 4 | `DEP_BIRTH_DATE` | DATE | 8 | Y | `dbo.Dependent.BirthDate` | `date_yyyymmdd` |

**Unanswered**: must the two files' record counts reconcile against each other,
and do they transmit as one run? Raised as part of Open Question 4's batch.

---

## Open questions

| # | Field | Row | Question | Raised by | Blocks |
| --- | --- | --- | --- | --- | --- |
| 1 | `RELATIONSHIP` | 10 | `codes` says "see RELATIONSHIP codes" but no values are listed, and the `Code Sets` tab says only "pending vendor confirmation". Need the full value list, or the lookup table and its key. | `codes` column + reader error | **the field's transform, the domain check, and the build** |
| 2 | `STATUS_FLAG` | 15 | Rule reads "Derive from termination date as per current process" — the actual derivation is not stated, and `source field` says `StatusCode` while the rule says termination date. Which is the source, and what are the boundary rules for a future-dated termination, a termination equal to the run date, and a reinstatement? | `transformation rules` column + reader error | **the field's transform and the build** |
| 3 | `PLAN_CODE` | 13 | `additional comment` overrides the rule with `Hardcode 'STANDARD'` for phase 1. Confirm the phase-2 date and that `PlanCode` should still be carried in the query meanwhile. | `additional comment` vs `transformation rules` | phase-2 change effort only — not the build |
| 4 | `RECORD_SEQ` | 16 | Does the sequence restart per file part, or run continuously across the whole transmission? And must `MEMBER_LOADER` and `DEPENDENT_LOADER` counts reconcile in one run? | `comments` column | correct record numbering in parts 2+ |

Questions 1 and 2 are **blocking**: no transform is generated for those fields
and the pipeline will not build. Questions 3 and 4 do not block generation, but
4 will silently corrupt `RECORD_SEQ` in every part after the first if the
assumed per-file reading is wrong.

---

## What this mapper does not tell you

- It reflects `sample-mapping-member-loader.xlsx` at the recorded sha256 and
  nothing else. It does **not** verify that any source column exists, that a
  declared `source type` is real, or that the join path is correct. Use
  `skills/claude-skills/sql-server-schema/` for that.
- **Rule classification is a reading of prose.** Every classification in the
  catalog is a judgment. The two the reader could not classify are in Open
  Questions; the ones it classified confidently can still be wrong.
- Truncation risks and widths are derived from **declared** sizes, not from
  profiled data. Nobody has checked how many surnames in `dbo.Member` actually
  exceed 30 characters — and that number is what decides whether the
  `LAST_NAME` truncation decision is acceptable or a problem.
- The expected volume (~1.2M rows, ~25 parts) came from the requester, not from
  the document or the database.
