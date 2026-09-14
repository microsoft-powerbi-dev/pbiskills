# The transformation-mapper Markdown artifact

The reviewable deliverable of step 4, and the seam between the mapping document
and the generated code.

It exists because a business analyst who will never read `transforms.py` still
has to confirm that "left pad with zeroes to 12" was understood as left-padding
a 12-wide key and not as something else. If that confirmation happens after the
code is written, it happens after the code is wrong.

## Emitter idiom

Follow `sql-server-schema/scripts/skillgen.py`: build `lines: List[str]`,
one `.append()` per Markdown line, `return "\n".join(lines)`. No template
engine. A blank `lines.append("")` after every heading and every block — never
rely on implicit spacing. Iterate sorted/ordinal-ordered collections so the
same digest always produces byte-identical Markdown, which is what makes a
spec revision diff cleanly.

## It is customer data

A mapper generated from a real mapping document names internal systems,
tables, and columns.

- Route the destination through `sql-server-schema/scripts/guardrails.py`'s
  `resolve_artifact_path()`, which refuses `skills/`, `mcp/`, `docs/`, and any
  in-repo path `git check-ignore` does not confirm.
- Write outside version control by default.
- Emit the do-not-commit header (below) as the first block after the front
  matter.
- Never commit a mapper built from a real document into this repository. The
  only committed example is `examples/sample-transformation-mapper.md`, built
  from the fictional sample workbook.

## Required sections, in order

### 1. Front matter and provenance

```markdown
---
generated_by: mapping-driven-loader-pipeline
generator_version: "1.0"
source_workbook: MemberLoaderMapping_v4.xlsx
source_sha256: 3f9c1e…
digest_schema_version: "1.0"
generated: 2026-09-11
do_not_commit: true
---

> **Do not commit.** This file names internal source systems, tables and
> columns transcribed from a customer mapping document. It is written outside
> version control by design.
```

The `source_sha256` is load-bearing: it is how you later prove which revision
of the spreadsheet the generated pipeline actually implements.

### 2. Summary

Target files, field counts, how many fields are excluded, how many distinct
rules, how many open questions. Plus the reader's own findings: sheets skipped
and why, unmatched columns, whether `column name` resolved by header or by
position, and which sense the `mandatory` column was read in. A reviewer needs
to know what the reader could *not* see.

### 3. One section per target file

For each file, in this order:

**3a. File contract** — output file name and part-naming template, delimiter,
line ending, header/trailer, null sentinel, and the split rule. Mark each value
as `stated in mapping document` or `confirmed by <who> on <when>`. A value with
neither provenance has no business being here.

**3b. Field table**, in loader ordinal order:

```markdown
| # | Target column | Base type | Size | Format | Mand. | Source | Source field | Rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `MEMBER_ID` | CHAR | 12 | — | Y | `dbo.Member` | `MemberKey` | [`pad_left_zero`](#pad_left_zero) |
| 2 | `LAST_NAME` | CHAR | 30 | — | Y | `dbo.Member` | `LastName` | [`trim_upper`](#trim_upper) |
| 3 | `EFF_DATE` | DATE | 8 | YYYYMMDD | Y | `dbo.Enrollment` | `EffectiveDate` | [`date_yyyymmdd`](#date_yyyymmdd) |
```

**3c. Excluded fields** — every row the document marked
`target file needed = N`, with its stated reason. Excluded, not deleted: the
decision must stay visible so it can be reversed when the spec changes.

**3d. Per-field detail**, one subsection per field. This is the section a
reviewer actually reads:

```markdown
#### 1. `MEMBER_ID`

- **Description** (verbatim): Unique member identifier assigned at enrolment
- **Source**: `dbo.Member.MemberKey` — `int(4)`, one row per member
- **Target**: `CHAR(12)`, mandatory
- **Transformation rule** (verbatim):
  > Left pad with zeroes to 12
- **Interpreted as**: cast the integer key to text, right-justify in 12
  characters padding with `0`. Classification: **string transform**
  (`pad_left_zero`).
- **Additional comment** (verbatim): —
- **Notes**: source width 4 → target width 12, so no truncation risk. A key
  wider than 12 digits raises rather than truncating; confirmed with the
  requester that a 13-digit key is a data error, not a case to handle.
```

The **verbatim quote before the interpretation** is not optional. The
reviewer's whole job is to check your reading against the original text, and
they cannot do it if you only show your reading.

### 4. Rule catalog

One entry per **distinct** rule text, with the fields that use it. A 300-row
document typically collapses to under 30 rules, and this table is what makes
the generated code reviewable at all.

```markdown
| Rule | Classification | Params | Used by | Unstated behaviour resolved as |
| --- | --- | --- | --- | --- |
| `pad_left_zero` | string transform | width from `size`, char `0` | `MEMBER_ID`, `GROUP_ID` | over-width value raises (confirmed) |
| `date_yyyymmdd` | date render | — | `EFF_DATE`, `TERM_DATE` | unparseable value raises (confirmed) |
| `status_code` | code lookup | 4 pairs from `codes` | `MEMBER_STATUS` | unmapped code raises (confirmed) |
```

The last column matters more than the rest. It records every place the document
was silent and someone made a decision — which is where loader defects live.

### 5. Source lineage

Base tables, the driving table, the join path with keys, the **identity /
grain column**, and the stated grain ("one row per member per plan year"). Flag
any rule classified as aggregate or pick-one, because those change the grain
and belong in the query rather than in `transforms.py`.

Cross-check the `source` values from the document against the base tables the
user confirmed, and list any table the document names that the user did not
confirm — and vice versa. A mismatch here is a wrong extract.

### 6. Split and record-count contract

The split rule, its provenance, and the reconciliation story: trailer format,
whether the trailer count is per-part or whole-file, expected row volume, and
the deterministic `ORDER BY` that makes part boundaries reproducible.

State plainly that there is **no default split rule** and record who supplied
the one in use.

### 7. Open questions

Every unresolved item, numbered, each with: the field and mapping-document row
it came from, the verbatim text that raised it, what is blocked by it, and who
it is with. Sources are the `comments` and `additional comment` columns, the
reader's `issues` list, and every stop-condition you hit.

```markdown
| # | Field | Row | Question | Raised by | Blocks |
| --- | --- | --- | --- | --- | --- |
| 1 | `PLAN_CODE` | 14 | `codes` says "see PLAN codes" but no values are listed and no tab holds them. Need the value list or the lookup table and key. | `codes` column | code-lookup rule, `validate.py` domain check |
| 2 | `RELATIONSHIP` | 22 | Rule reads "as per current process". Need the actual derivation. | `transformation rules` column | the field's transform |
```

An empty Open Questions section is a claim that the document was fully
specified. Emit the section either way — an explicit "none" is information;
a missing section is ambiguity.

### 8. What this mapper does not tell you

The limitations block, always emitted:

- The mapper reflects the mapping document at the recorded sha256 and nothing
  else. It does not verify that the source columns exist, that the declared
  source types are real, or that the join path is correct — use
  `skills/claude-skills/sql-server-schema/` for that.
- Rule classification is a reading of prose. Every classification in §4 is a
  judgment, and §7 lists the ones known to be uncertain.
- Row volumes and truncation risks are derived from declared sizes, not from
  profiled data.
