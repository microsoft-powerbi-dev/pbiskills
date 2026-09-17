# Devin knowledge: mapping-driven loader pipelines

## Trigger

Apply this knowledge when a mapping document — a spreadsheet specifying a
target loader or interface file field by field — has to become a working
extract: reading the mapping, writing a transformation specification from it,
or generating pipeline code that pulls from SQL Server, applies the mapped
transformations, writes a pipe-delimited flat file, and splits the output by
record count or file size. Also apply it whenever a loader file, an
eligibility/member feed, a vendor interface file, or a fixed-layout flat file
is being built or changed. The full skill, with code templates and the column
reference, is at
`skills/claude-skills/mapping-driven-loader-pipeline/SKILL.md`; setup and usage
notes are in that folder's `DEVIN-USAGE.md`.

## Content

Work in a fixed order and do not collapse it: parse the document into a
digest, scan the generator code that already exists in the repository, ask the
clarifying questions, write a transformation-mapper Markdown file and have it
reviewed, then generate code. The Markdown mapper is not a formality — it is
reviewable by an analyst who will never read Python, it diffs cleanly when the
spreadsheet is revised, and it is the only cheap place to catch a misread
rule. Code written before that review is code written against a guess.

Parse the spreadsheet deterministically, with
`scripts/mapping_reader.py read --workbook <path>`, and read the resulting
`mapping_digest.json` rather than the spreadsheet. Hand-maintained mapping
documents defeat every assumption a naive reader makes: the header row sits
below a title banner and must be located by scoring rows against known column
names, not assumed to be row 1; `file name` is populated once and left blank
for the eighty rows beneath it, so a blank cell means *same as above* and must
be forward-filled; one sheet holds several target files separated by blank
rows; and a tab whose header cannot be matched is usually where a missing code
set is hiding, so report it rather than discarding it silently.

One row is one field of one output file, and it has a target half and a source
half that mean nothing apart. The target half is `file name`, `column name`,
`loader file column`, `base type`, `size`, `format`, `mandatory`,
`description`, `codes`, `target file needed`, `additional comment`. The source
half is `source`, `source field`, `source type`, `source size`,
`source format`, `source description`, `transformation rules`, `comments`.
Read the source block as a unit per row; for a member loader or any comparable
target file it is the only thing that says where the data comes from.

**Column C is the target column name.** The third spreadsheet column holds the
output field name, and it is the key everything downstream is built on —
mapper rows, transform function names, layout ordinals, test names. Resolve it
by header alias first and fall back to positional column C. If both resolve
and disagree, stop and ask: that disagreement means the document's shape
differs from the assumed shape, and every ordinal and transform after it would
be built on the wrong column, surfacing days later as bad data in a loader
rather than as an error now.

`transformation rules` is the authoritative logic and becomes the transform
function body. `additional comment` layers an exception clause on top of it and
routinely carries the null branch, the default, or the phase-1 override that
the rule column omitted — read it as part of the transformation, not as a note.
`description` and `source description` only *disambiguate*; they never
override. A genuine conflict in substance between rule and description is a
stop-and-ask. `loader file column`, when numeric, is the authoritative field
order, and a gap or duplicate in it is an error rather than a note because it
shifts every following field in the delimited line. `target file needed = N`
excludes a field from the output but must keep it visible in the mapper and in
the generated layout's excluded list, so the decision stays reversible.

Classify each rule and emit the matching shape: passthrough, constant, string
transform, date render, numeric render, conditional, code lookup,
concatenation, aggregate or pick-one, row sequence. Classification by keyword
is precedence-sensitive and the order is load-bearing — "hardcode the extract
run date" is a constant despite the word "date", "left pad with zeroes to 12"
is a pad despite the word "zeroes", and "format as YYYYMMDD" is a date render
despite the word "format". Bare literal tokens like `spaces` or `zeroes` mean a
constant only as the whole rule value, never as a substring. Treat the
classification as a hint for the catalog, never as comprehension.

Two rule shapes deserve specific suspicion. An aggregate or pick-one ("latest
billed premium", "most recent", "sum of") **changes the row grain** and belongs
in the source query as a window function or grouped subquery, never in the
transform module — and mapping documents almost never label these, so a plain
join against that table will silently fan the extract out. A sequence or record
number is **stateful**, must be generated in the writer in deterministic order,
and must be defined as per-file or per-part; getting that wrong makes every
record number after the first part wrong.

Never `eval` a rule string. Rules resolve to named functions in a registry by
exact match, the way
`skills/claude-skills/extract-engine/scripts/extract_engine/rules.py` does, so
an unrecognised rule name is a generation-time error rather than a runtime
surprise. Identical rule text across many rows is one registry entry reused; a
300-row document usually has fewer than 30 distinct rules, and collapsing them
is what makes the generated code reviewable at all.

Four inputs have **no safe default** and block code generation. The split rule
— rows per file, bytes per file, or both, and which wins — must be asked for,
because a loader with a hard vendor limit and a pipeline that assumed 100,000
produces a rejected transmission rather than an answer that is slightly off.
The source SQL query must be asked for, and if one exists it is used verbatim:
never rewrite a query someone else is accountable for, and never invent a join
path. The base tables and the driving table must be confirmed against the
`source` column. And the identity or grain column — the member ID or
equivalent — is required, because it sets the deterministic `ORDER BY` without
which part boundaries move between runs and a re-transmission does not
reconcile against the original.

Also ask for the violation policies, because the document is almost always
silent and each choice changes the output file: what happens when a mandatory
field is null, when a value exceeds its declared size, when a value contains
the delimiter, and when a code value is outside the code set. Record every
answer in the mapper with who gave it and when. Stop and ask, rather than
inferring, whenever a rule is prose you cannot render deterministically,
`codes` names a set whose values are nowhere in the document, `source` is blank
for a non-constant field, `size` is narrower than `source size` with no stated
truncation rule, or `mandatory` is Y on a nullable source with no default.
Anything in `comments` or `additional comment` reading as unresolved — TBD, a
question mark, "confirm with" — becomes a question and an Open Questions entry,
never a silent resolution.

Reuse rather than reinvent. This repository already contains a tested engine
that reads SQL Server and writes split pipe-delimited files, and its
`PartWriter` in
`skills/claude-skills/extract-engine/scripts/extract_engine/execution_polars.py`
should be copied and extended with a byte-size trigger rather than rewritten:
it opens handles in binary mode with an explicit line ending, because text mode
on Windows silently translates `\n` to `\r\n` and corrupts a file whose vendor
spec says LF, and it slices at the exact boundary so no row is split, lost, or
duplicated at a roll. Take the rule-registry shape from `rules.py`, delimiter
collision handling from `sanitizer.py`, the connection contract and its
injectable `conn=` test seam from `connection.py`, and the Markdown emitter
idiom — build a list of lines, append one per line, join with newlines, no
template engine — from `sql-server-schema/scripts/skillgen.py`. State
explicitly whether each component was copied or imported, and why, in the
generated README; copying is the right default because a generated pipeline
gets handed to another team, and the cost is drift that only a recorded
provenance note makes diffable later.

Before generating a parallel codebase at all, consider that `extract-engine`
already turns an Excel-authored feed into split pipe-delimited files with the
mapping held as data in `meta.*` tables. If the mapping is a straightforward
field list whose rules are already in that catalog, translating it into an
extract-engine workbook makes a specification change an Excel edit and a rerun
instead of a code edit and a deploy. Offer that route before writing code, then
write code if the user prefers it or if the rules, the deployment target, or
grain-changing logic genuinely do not fit.

Structure generated code as one directory per output file, with shared
machinery in a thin `common/`, because a mapping document usually specifies
several loader files that share nothing but a source database, and a change to
one must not be able to touch another. Keep the layout as data — an ordered
tuple of field specs is the single source of column order and width, and the
pipeline iterates it. Compose the stages as Python generator functions so a
multi-million-row extract never materialises; never add a `fetchall`. Pin the
read schema from the document's `source type` column rather than inferring it,
so a batch in which a nullable column happens to be entirely null cannot
change a column's type midway through a file. Give every transformation a named
function carrying the verbatim rule in its docstring. Surface mandatory and
size violations rather than truncating silently. Keep the extract path
`SELECT`-only — a loader pipeline needs no write path, and the strongest form
of that guarantee is that none exists.

Run `dry-run` before connecting to anything. Printing the resolved query, the
field layout, and the split rule is the cheapest available check that the
mapping was read correctly, and an off-by-one in the field order is otherwise
discovered when the vendor reports that column 9 is wrong a week later.

A real mapping document, and everything generated from it, names internal
source systems, tables and columns. Write those artifacts outside version
control, never into a public repository, route the destination through
`sql-server-schema/scripts/guardrails.py`'s `resolve_artifact_path`, and state
plainly what a file contains before sharing it. Mapping documents for member
and eligibility feeds routinely specify identifier fields; read field
definitions only, and if the document carries a sample-data tab, strip it
rather than parsing it. The only mapping artifacts safe to commit are the
fictional sample workbook and sample mapper in the skill's `examples/`.
