---
name: mapping-driven-loader-pipeline
description: >
  Use this skill when a mapping document (a spreadsheet specifying a target
  loader/extract file field by field) must be turned into a working extract
  pipeline. It reads the mapping workbook, interprets the target-column,
  description and transformation-rule columns into named transformation logic,
  writes a reviewable **transformation-mapper Markdown artifact**, and then
  generates **generator-style Python pipeline code** — one directory per output
  file — that extracts from SQL Server, applies the mapped transformations,
  writes a pipe-delimited flat text file, and splits the output into parts by
  record count and/or byte size. Before generating any code it scans the
  existing generator platform already in this repository
  (`skills/claude-skills/extract-engine/`, `sql-server-schema/`,
  `rdl-generation/`) and reuses those patterns rather than inventing new ones,
  then offers the generated tree for refinement. It never guesses a
  transformation, a split rule, a grain, or a source query: unstated inputs are
  asked for. Example requests: "here is the member loader mapping sheet, build
  the extract", "generate the transformation mapper for this mapping document",
  "turn tab 3 of this spec into a pipe-delimited extract with 50k rows per
  file", "the mapping doc changed, regenerate the pipeline".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
triggers:
  - mapping document
  - mapping doc
  - mapping sheet
  - mapping spreadsheet
  - transformation mapper
  - transformation mapping
  - transformation rules
  - transformation rule column
  - loader file
  - loader file column
  - member loader
  - member extract
  - target file needed
  - base type
  - source field
  - pipe delimited
  - pipe-delimited flat file
  - flat file extract
  - fixed layout extract
  - file split
  - split output files
  - records per file
  - rows per file
  - max file size
  - trailer row
  - generate pipeline code
  - generator code
  - extract pipeline
  - sql extract to flat file
  - loader pipeline
---

# Mapping-Driven Loader Pipeline

Turn a **mapping document** into two artifacts: a reviewable
**transformation-mapper Markdown file**, and a **generated Python pipeline**
that reads SQL Server, applies the mapped transformations, and writes split
pipe-delimited flat files.

The mapping document is the specification. This skill's entire job is to stop
that specification from being re-interpreted by hand — differently — every time
someone builds or changes a loader feed.

## The order of work — do not skip or reorder

1. **Read the mapping document.** Deterministically, with
   `scripts/mapping_reader.py`, into a `mapping_digest.json`. Never eyeball a
   spreadsheet and start writing code from memory of it.
2. **Scan the existing generator platform in this repository.** See step 2
   below and `references/reuse-existing-generators.md`. You are extending an
   established codebase, not starting one.
3. **Ask the clarifying questions.** The gate list is
   `references/clarifying-questions.md`. Several of them — source SQL, base
   tables, identity column, split rule — have **no safe default** and must be
   answered before step 5.
4. **Write the transformation-mapper Markdown** and have it reviewed. This is
   the cheap place to catch a misread rule. Format:
   `references/transformation-mapper-format.md`.
5. **Generate the pipeline code**, one directory per output file. Layout and
   code templates: `references/generated-pipeline-layout.md`.
6. **Offer the tree for refinement**, then run the generated tests.

Steps 4 and 5 are separate deliverables on purpose. The Markdown mapper is the
seam: it is reviewable by a business analyst who will never read Python, it
diffs cleanly when the mapping document is revised, and it makes the generated
code auditable against the spec without re-opening the spreadsheet.

## Step 1: read the mapping document

Input expected: **one mapping workbook** (`.xlsx`, or `.csv`/`.xls` — see the
reference), typically one sheet per target file or one sheet holding many files
distinguished by the `file name` column.

```bash
python skills/claude-skills/mapping-driven-loader-pipeline/scripts/mapping_reader.py \
    read --workbook <path> --out <scratch>/mapping_digest.json
```

The reader is deterministic, openpyxl-only, and does **no** interpretation: it
locates the header row, normalises the 19 known column names against their
aliases, forward-fills merged/blank `file name` cells, and reports every column
it could not match. Read the digest, not the spreadsheet.

**Column C is the target column name.** In these documents the third column is
the field name for the output file, and it is the reference used by every
downstream artifact — mapper table rows, transform function names, layout
ordinals. The reader resolves it by header name first and falls back to
positional column C; if those two disagree it stops and asks rather than
guessing. Full column-by-column interpretation rules, including what to do when
`transformation rules` is blank or contradicts `description`:
`references/mapping-document-schema.md`.

For a target file such as a member loader, the **source section** of the sheet
(`source`, `source field`, `source type`, `source size`, `source format`,
`source description`) is what tells you where the data comes from — read it as a
block, per row, and treat the target section and source section of the same row
as the two halves of one field mapping.

## Step 2: reuse before you write

Before generating anything, scan and read what already exists here. The
pipeline you are about to emit is a near-sibling of a system that is already
built, tested, and carries hard-won fixes you will otherwise re-discover:

| Scan this | For |
| --- | --- |
| `skills/claude-skills/extract-engine/scripts/extract_engine/execution_polars.py` | `PartWriter` — the pipe-delimited part writer, boundary-exact row slicing, per-part trailer, running SHA-256. **Copy this, adapted; do not write a new one.** |
| `.../extract_engine/rules.py` | The rule-registry shape: a `@dataclass(frozen=True) Rule`, a `_REGISTRY` dict, a `rule(...)` decorator. Rule names are **looked up, never `eval`'d**. |
| `.../extract_engine/sanitizer.py` | Delimiter/CR/LF/TAB collision handling and the `fail` mode that refuses rather than silently mangling. |
| `.../extract_engine/connection.py` | Windows-auth `read_connection()`/`write_connection()` context managers, driver enumeration, the `conn=` injection seam that makes tests offline. |
| `.../extract_engine/query_builder.py` | Bracket-quoting identifiers from metadata, `?` bind params for every value, returning a frozen plan object carrying `sql` + `params` together. |
| `skills/claude-skills/sql-server-schema/scripts/skillgen.py` | The Markdown emitter idiom — `lines: List[str]`, one `.append()` per line, `"\n".join(lines)`. No template engine. Use this for the transformation mapper. |
| `.../sql-server-schema/scripts/guardrails.py` | `resolve_artifact_path()` — refuses to write customer-naming artifacts into `skills/`, `mcp/`, `docs/`, or any non-gitignored repo path. |
| `skills/claude-skills/extract-engine/SKILL.md` + `DEVELOPER.md` | What that engine already does, and its documented gaps. |

Then make an explicit, stated choice per component: **copy-and-adapt** (default
— the generated pipeline must stand alone and be handed to another team) or
**import** (only when the user has said the pipeline lives inside this repo
permanently). Say which you chose and why in the generated `README.md`.

Also check whether the mapping document is better served by extract-engine's
existing metadata path — an Excel-authored feed loaded into `meta.*` tables —
rather than fresh code. If the mapping is a straightforward field list with
rules already in the rule catalog, say so and offer that route as the
lower-maintenance alternative before generating a parallel codebase.

Repository conventions the generated code must follow (they are uniform across
all three existing skills): `from __future__ import annotations` first,
`@dataclass(frozen=True)` for value objects, explicit `typing` imports, prose
module docstrings that explain *why*, keyword-only config args, domain error
classes subclassing stdlib errors, and comments that record empirically
discovered traps. Details and rationale: `references/reuse-existing-generators.md`.

## Step 3: ask, do not assume

Work through `references/clarifying-questions.md`. Ask in one batch where you
can, grouped, rather than one question at a time.

You are explicitly authorised — and expected — to ask for:

- **The source SQL query.** If the user has one, use it verbatim as the extract
  and do not rewrite it. If they do not, ask for the base tables instead and
  draft the query for approval; never invent a join path.
- **The base tables** behind the query, and which one is the driving table.
- **The identity / grain columns** for those tables — the member ID or
  equivalent. This is not optional: it determines the row grain, the
  deterministic `ORDER BY` (without which split boundaries move between runs),
  duplicate detection, and any restart key.
- **The split rule.** There is deliberately **no default**. Ask for rows per
  file, maximum bytes per file, or both, and which wins when both are set. A
  loader with a hard vendor limit and a pipeline that guessed 100,000 is a
  rejected transmission, not a rounding error.

Anything in the mapping document's `comments` or `additional comment` column
that reads as an open question (`TBD`, `confirm with`, `?`) becomes a question
here. Carry it into the mapper's Open Questions section; never resolve it
silently.

Stop and ask — do not proceed on a guess — whenever: a transformation rule is
prose you cannot render deterministically, `codes` names a code set whose
values are not in the document, `source` is blank for a field that is not a
constant, two rows collide on the same target column or ordinal, or `mandatory`
is `Y` on a field whose source is nullable with no stated default.

## Step 4: the transformation-mapper Markdown artifact

One Markdown file per mapping document (multi-file mappings get one section per
target file, or one file per target file for large mappings). It contains: the
provenance header, a per-file field table in loader ordinal order, a
per-field subsection quoting the **verbatim** transformation rule beside its
interpreted logic, the derived rule catalog, source lineage with the identity
column and join keys, the record-count and split contract, and Open Questions.

Two non-negotiables:

- **Quote the rule verbatim before interpreting it.** The reviewer's job is to
  check your reading against the original text, and they cannot do that if you
  only show the interpretation.
- **It is customer data.** A real mapping document names internal systems,
  tables, and columns. Route the output path through
  `resolve_artifact_path()`, write it outside version control by default, and
  emit the do-not-commit header. Never commit a mapper generated from a real
  document into this repository.

Full structure, the required sections, and a worked template:
`references/transformation-mapper-format.md`.

## Step 5: generate the pipeline code

Generator-style, streaming, one directory per output file. Two senses of
"generator" apply and both are required: the skill *generates* the code, and
the emitted code uses Python **generator functions** (`yield`) end to end so a
multi-million-row extract never materialises in memory.

```
<out_root>/
  README.md                   # what was generated, from which mapping doc sha256, copy-vs-import choices
  requirements.txt
  common/                     # shared, adapted from extract-engine
    connection.py             # read_connection(), driver enumeration
    part_writer.py            # PartWriter: pipe-delimited, rolls on rows AND/OR bytes
    rules.py                  # the rule registry; names looked up, never eval'd
    sanitizer.py              # delimiter/CR/LF/TAB collisions
    validate.py               # mandatory / size / domain / base-type checks
    run_log.py                # per-run manifest: parts, counts, checksums
  pipelines/
    <file_name>/              # one directory per target file, e.g. member_loader/
      __init__.py
      extract.sql             # the approved source query, verbatim
      layout.py               # ordered FieldSpec tuple: ordinal, name, base type, size, format, mandatory
      extract.py              # read_batches(conn, ...) -> Iterator[batch]
      transforms.py           # one named, documented function per target column
      pipeline.py             # the orchestration generator: extract -> transform -> validate -> sanitize -> write
      config.yml              # split rule, delimiter, line ending, null sentinel, output paths
  run.py                      # argparse CLI: list / dry-run / validate-layout / run
  tests/
    test_<file_name>_transforms.py     # one case per rule, from the mapping doc's own examples
    test_part_writer_split.py          # boundary-exact splitting
```

Requirements on the emitted code:

- **Pipe-delimited output**, delimiter configurable, with the collision policy
  the user chose. A value containing the delimiter must never silently corrupt
  the line.
- **Splitting on record count and/or byte size**, whichever trips first, with
  the boundary hit exactly — a row is never split across parts, and no row is
  lost or duplicated at a roll. Part naming follows the vendor convention the
  user gave (default `<stem>_part{NNN}.txt`).
- **A deterministic `ORDER BY`** on the identity column in `extract.sql`.
  Without it, part boundaries shift between runs and a re-send does not match.
- **Every transformation is a named function** with the verbatim mapping-doc
  rule in its docstring, registered by name. No inline lambdas, no `eval`, no
  `map_elements`-style per-row Python where a vectorised or SQL expression will
  do.
- **The layout is data, not control flow.** `layout.py` is the single source of
  column order and width; `pipeline.py` iterates it.
- **Mandatory and size violations are surfaced, never truncated silently** —
  the policy (reject row / fail run / pad / warn) is the user's choice, asked
  for in step 3 and recorded in `config.yml`.
- **Read-only against the source.** The extract path issues `SELECT` only.

Code templates for each emitted file: `references/generated-pipeline-layout.md`.

## Step 6: refine

Present the generated tree as a first draft and invite changes. Then:

```bash
python -m pytest <out_root>/tests -q          # offline, against fakes
python <out_root>/run.py dry-run --pipeline <file_name>   # prints SQL + layout, no connection
```

A dry run that prints the resolved query and layout is the cheapest check that
the mapping was read correctly. Run it before anyone connects to a database.

When the mapping document is later revised: re-run step 1, diff the new
`mapping_digest.json` and the regenerated mapper Markdown against the previous
ones, and let that diff drive the code change. The mapper artifact exists
precisely so that a spec revision produces a reviewable diff instead of an
archaeology exercise.

## Bundled assets

### `scripts/`

| Script | Role |
| --- | --- |
| `mapping_reader.py` | Deterministic mapping-workbook → `mapping_digest.json`. Header-row location, column-alias normalisation, forward-fill, unmatched-column reporting. Also `--report` for a quick human summary. No interpretation, no DB, no network. |
| `build_sample_mapping.py` | Regenerates `examples/sample-mapping-member-loader.xlsx` — **entirely fictional** member-loader mapping, safe to commit, used to exercise the skill without customer data. |

### `references/`

| File | Covers |
| --- | --- |
| `mapping-document-schema.md` | All 19 columns, what each one drives, the aliases the reader accepts, Column C resolution, and the precedence rules when `transformation rules` / `description` / `codes` disagree |
| `transformation-mapper-format.md` | The Markdown artifact's required sections, the emitter idiom, and a full worked template |
| `generated-pipeline-layout.md` | The emitted directory tree and a code template for every file in it, including the rows-and-bytes `PartWriter` |
| `clarifying-questions.md` | The gate checklist: what must be asked, what has no default, and the stop conditions |
| `reuse-existing-generators.md` | What to scan in this repository, the copy-vs-import decision, and the code conventions the generated tree must match |

### `examples/`

| Path | Contents |
| --- | --- |
| `sample-mapping-member-loader.xlsx` | The fictional mapping workbook (regenerate with `build_sample_mapping.py`) |
| `sample-transformation-mapper.md` | The mapper artifact this skill produces from that workbook — the golden reference for step 4's output shape |

## Related material in this repository

- `skills/claude-skills/extract-engine/` — the metadata-driven engine this skill
  borrows `PartWriter`, the rule-registry shape, sanitization, and the
  connection contract from. Consider it as an alternative to generating code at
  all (step 2).
- `skills/claude-skills/sql-server-schema/` — use it to establish the source
  schema when the mapping document's `source` column names tables you have not
  seen. Its `skillgen.py` is the Markdown-emitter reference.
- `skills/ide-references/devin/mapping-driven-loader-pipeline.knowledge.md` —
  the condensed Devin knowledge entry for this skill.
