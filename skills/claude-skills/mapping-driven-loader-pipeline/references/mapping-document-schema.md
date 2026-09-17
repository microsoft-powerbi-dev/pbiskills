# The mapping document: what each column drives

A mapping document is a spreadsheet where **one row is one field of one output
file**. Each row has a *target* half (what the loader file must contain) and a
*source* half (where the value comes from). Read them together; neither half
means anything alone.

This reference is the contract `scripts/mapping_reader.py` implements and the
interpretation rules you apply on top of the digest it produces.

## Layout assumptions, and how the reader survives them being wrong

Real mapping documents are hand-maintained, so the reader does not assume a
clean frame:

- **The header row is located, not assumed.** It scans the first 20 rows and
  picks the row matching the most known column names (minimum 4 matches).
  Title/banner rows above the header are common and are skipped.
- **Merged and blank `file name` cells are forward-filled.** A mapping sheet
  typically names the file once and leaves the cell blank for the following 80
  rows. A blank `file name` means *same as the row above*, never "no file".
  The same forward-fill applies to `source` when it is blank but `source field`
  is populated — one source table serving a run of fields.
- **One sheet may hold many files, or one file may span many sheets.** Group by
  the resolved `file name`, across sheets. Ignore sheets whose header row
  cannot be located (revision logs, code-set tabs, notes) but *list them* —
  a code-set tab is often where the `codes` column's values actually live.
- **Fully blank rows are dropped; visually-blank separator rows are dropped.**
  A row with a `file name` but no `column name` is a section separator, not a
  field.
- **Column names are normalised** — lowercased, whitespace and punctuation
  collapsed — then matched against the alias table below.

## Column C is the target column name

In these documents the third spreadsheet column is the output file's field
name. It is the **primary key of the whole exercise**: mapper table rows,
transform function names, `layout.py` entries, and test names all key off it.

Resolution order, implemented in the reader:

1. A header matching a `column name` alias.
2. Failing that, positional Excel column **C**.

If both resolve and they point at *different* columns, the reader raises rather
than picking one. That disagreement means the document's shape differs from the
expected shape, and everything downstream would be silently misaligned. Ask the
user which column is the target name.

## The 19 columns

`Drives` is what the column actually changes in the generated artifacts. A
column that drives nothing but documentation still belongs in the mapper —
that is where a reviewer checks your reading.

### Target half

| Column | Aliases accepted | Drives |
| --- | --- | --- |
| **file name** | `file`, `filename`, `target file`, `output file`, `loader file name` | Grouping. One distinct value → one `pipelines/<slug>/` directory and one output file set. Forward-filled. |
| **column name** *(Column C)* | `target column`, `field name`, `column`, `target field` | The target field identifier. Transform function name (`snake_case`), `layout.py` entry, mapper row key. |
| **loader file column** | `loader column`, `loader field`, `position`, `seq`, `ordinal`, `column no` | Field **order** in the pipe-delimited line. If numeric, it is the ordinal and it is authoritative — sort by it, and report gaps and duplicates as errors. If it is a name, it is the loader's own field name where that differs from `column name`; carry both. If absent entirely, sheet row order is the ordinal — say so in the mapper. |
| **base type** | `type`, `target type`, `data type`, `datatype` | The target datatype (`CHAR`, `VARCHAR`, `NUMBER`/`NUMERIC`, `DECIMAL`, `DATE`, `INT`). Drives the output cast and the validation check. `CHAR` usually implies right-padding to `size`; `NUMBER` usually implies zero-padding left. Confirm the padding convention once per file — do not infer it per field. |
| **size** | `length`, `len`, `target size`, `width`, `field size` | Output width. Drives truncate/pad and the length assertion. `9(7)V99`-style or `18,2`-style values carry precision *and* scale — parse both. A `size` smaller than `source size` is a **truncation risk**: flag it in the mapper, never silently truncate. |
| **format** | `target format`, `output format`, `mask`, `picture` | Output formatting: date masks (`YYYYMMDD`, `MM/DD/YYYY`, `CCYYMMDD`), implied decimals (`V99` = two implied decimals, no point emitted), sign handling, zero-fill. This is a **rendering** step applied after the transformation and before the width check. |
| **mandatory** | `required`, `req`, `nullable`, `null?`, `m/o` | Null policy. `Y`/`yes`/`required`/`M`/`not null` → mandatory. `nullable`-style headers invert the sense — the reader normalises to a single boolean and records which sense it read. A mandatory field with a nullable source and no stated default is a **stop-and-ask**. |
| **description** | `desc`, `field description`, `business description`, `definition` | The field's business meaning. Goes verbatim into the generated transform function's docstring and the mapper subsection. **Use it to disambiguate an ambiguous transformation rule** — a rule reading "current status" resolves differently for "status at time of claim" than for "status as of extract date". |
| **codes** | `code`, `valid values`, `values`, `domain`, `code values`, `allowed values` | An enumerated domain. If it lists pairs (`A = Active`, `1-Single`), it becomes a code-translation dict in `transforms.py` and a domain check in `validate.py`. If it *names* a code set without listing values (`see MEMBER_STATUS codes`), that is a **stop-and-ask**: request the values, or the lookup table and key, and check the sheet's other tabs first. |
| **target file needed** | `needed`, `required in target`, `include`, `in scope`, `send` | Inclusion filter. `N`/`no`/`not needed` → **exclude the field from the output entirely**, but keep it in the mapper marked excluded so the decision is visible and reversible. Do not silently drop it. Applied at the *file* level when every row for a file is `N`. |
| **additional comment** | `additional comments`, `notes`, `remarks`, `comment 2` | Caveats, defaulting rules ("default to spaces if null", "hardcode for phase 1"), and conditional logic that the rule column did not capture. Read it as **part of the transformation** — real documents routinely put the exception here and the happy path in the rule column. Folded into the docstring. |

### Source half

Read this block as a unit, per row. For a member loader or any other
target file, this is the section that answers "where does this come from".

| Column | Aliases accepted | Drives |
| --- | --- | --- |
| **source** | `source table`, `source system`, `src`, `source object`, `from` | The source table/view/system. Collected per file into the source lineage section of the mapper and cross-checked against the base tables the user confirmed. Forward-filled when blank beside a populated `source field`. A value like `derived`, `n/a`, `hardcoded`, `constant` means there is no source object — the value comes from the rule. |
| **source field** | `source column`, `src field`, `source attribute` | The source column → a `SELECT` item in `extract.sql`. Multiple comma/`+`-separated fields mean the transformation consumes several inputs; **all** of them must reach the query. |
| **source type** | `src type`, `source data type` | The incoming datatype → the read schema override and the parse step. A source `INT` holding `20240115` with target `DATE` is a parse, not a cast. |
| **source size** | `src size`, `source length` | Incoming width. Compared against `size` to detect truncation. |
| **source format** | `src format`, `source mask` | The incoming representation — packed dates, implied decimals, trailing sign, left-padded zeros. The **parse** step, mirror of the target `format`'s render step. |
| **source description** | `src description`, `source definition` | Source semantics. Corroborates the join path and confirms grain — this is where "one row per member per plan year" tends to be written down, which is exactly what you need to know before a join fans the extract out. |
| **transformation rules** | `transformation rule`, `transformation`, `rule`, `logic`, `mapping rule`, `transformation logic`, `business rule` | **The authoritative transformation logic.** Becomes the transform function body. Quoted verbatim in the mapper before interpretation. |
| **comments** | `comment`, `open items`, `questions`, `issues` | Open questions and review chatter. Anything reading as unresolved (`TBD`, `?`, `confirm`, `pending`, `check with`) becomes an entry in the mapper's Open Questions and a clarifying question to the user. Never resolved silently. |

## Interpreting `transformation rules`

Precedence when the columns disagree: **`transformation rules` wins**, with
`additional comment` layered on top as an exception clause, and `description` /
`source description` used only to *disambiguate* — never to override. If the
rule and the description are in genuine conflict (not merely differently
worded), that is a stop-and-ask; record both in the mapper.

Classify each rule and emit the matching shape. These are the patterns that
account for nearly every row in a real document:

| Rule text looks like | Classification | Emitted as |
| --- | --- | --- |
| blank, `direct`, `direct move`, `as is`, `1:1`, `straight move` | **passthrough** | Cast to `base type`, render `format`, width-check. Confirm once per file that blank genuinely means direct move in this document's convention. |
| `hardcode 'X'`, `constant`, `always 'US'`, `default 'N'`, `spaces`, `zeroes` | **constant** | A literal in `layout.py`; no source column needed. |
| `trim`, `upper`, `lower`, `left N`, `substring`, `pad`, `strip non-numeric` | **string transform** | A named registry rule with params. Prefer the existing `extract-engine` rule names (`trim`, `upper`, `pad_left`, …) over new ones. |
| `format as YYYYMMDD`, `convert to CCYYMMDD`, `date only` | **date render** | Parse per `source format`, render per `format`. Never chain string slicing on a date — parse then render. |
| `if X then A else B`, `when … otherwise` | **conditional** | An explicit `if`/`elif`/`else` in the transform function, one branch per stated case, plus an `else` that raises or defaults **per the document** — if no fallthrough is stated, that is a stop-and-ask. |
| `map per codes`, `translate using code table` | **code lookup** | A dict from the `codes` column (or the confirmed lookup table) plus a documented unmatched-value policy. |
| `A + B`, `concatenate`, `first + space + last` | **concatenation** | Explicit about the separator and about null handling — a null in the middle of a concatenation must not shift the rest of the line. |
| `sum`, `count`, `max of`, `latest`, `most recent` | **aggregate / pick-one** | Changes the **grain**. It belongs in `extract.sql` as a window function or grouped subquery, not in `transforms.py`. Flag it: an aggregate rule means the driving query is not a simple row-per-source-row read. |
| `sequence`, `record number`, `incrementing` | **row sequence** | Stateful across the file. Must be generated in the writer, in the deterministic `ORDER BY` order, and must be defined as per-file or per-part — **ask which**. |
| `derive from`, `per business logic`, `as per current process`, `same as legacy` | **underspecified** | **Stop and ask.** Do not infer. This is the single most common source of a wrong loader file. |

Two standing rules:

- **Never `eval` a rule string.** Rules resolve to named functions in a
  registry by exact match, exactly as `extract-engine/scripts/extract_engine/rules.py`
  does. An unrecognised rule is a generation-time error, not a runtime
  surprise.
- **Identical rule text on many rows is one registry entry, reused.** Deduplicate
  into the mapper's rule catalog. A document with 300 rows usually has under 30
  distinct rules, and collapsing them is what makes the generated code
  reviewable.

## What the digest looks like

`mapping_reader.py` emits a stable, sorted JSON document:

```json
{
  "schema_version": "1.0",
  "source_workbook": "sample-mapping-member-loader.xlsx",
  "source_sha256": "…",
  "generated_by": "mapping_reader.py 1.0",
  "sheets_read": {"Member Loader": 12},
  "sheets_skipped": [{"sheet": "Code Sets", "reason": "no header row matched"}],
  "unmatched_columns": ["reviewed by"],
  "column_name_source": "header",
  "mandatory_sense": "mandatory",
  "files": [
    {
      "file_name": "MEMBER_LOADER",
      "slug": "member_loader",
      "field_count": 12,
      "fields": [
        {
          "ordinal": 1,
          "column_name": "MEMBER_ID",
          "loader_file_column": "1",
          "base_type": "CHAR",
          "size": "12",
          "format": null,
          "mandatory": true,
          "description": "Unique member identifier assigned at enrolment",
          "codes": null,
          "target_file_needed": true,
          "additional_comment": null,
          "source": "dbo.Member",
          "source_field": "MemberKey",
          "source_type": "int",
          "source_size": "4",
          "source_format": null,
          "source_description": "Surrogate key, one row per member",
          "transformation_rules": "Left pad with zeroes to 12",
          "comments": null,
          "sheet": "Member Loader",
          "row": 4
        }
      ]
    }
  ],
  "issues": [
    {"severity": "error", "file": "MEMBER_LOADER", "row": 9,
     "column": "transformation rules",
     "message": "underspecified rule: 'as per current process'"}
  ]
}
```

`issues` is the reader's own findings — duplicate ordinals, ordinal gaps,
missing target names, truncation risks, underspecified rules, named-but-unlisted
code sets. Every `error` must be resolved with the user before step 5; every
`warning` must appear in the mapper's Open Questions.
