# Clarifying questions: the gate

Work this list before generating code. Ask in grouped batches, not one question
at a time — and never let an unanswered item become a silent default.

The distinction that matters: some of these have a **sane default** you may
apply while stating it, and some have **no default** and block generation. They
are separated below on purpose.

## No default — generation is blocked until answered

### 1. The split rule

Ask for: rows per file, maximum bytes per file, or both — and which wins when
both are set.

There is deliberately no default. A loader with a hard vendor limit and a
pipeline that assumed 100,000 rows is a rejected transmission, not a rounding
error. `PartWriter` raises at construction if neither limit is set, so a
forgotten answer fails at startup rather than after writing the wrong files.

Follow-ups: does the vendor count the trailer row toward the limit? Is the byte
limit pre- or post-compression? Is a part-count cap imposed as well?

### 2. The source SQL query

Ask whether one already exists.

- **If yes**: use it **verbatim**. Do not rewrite, reformat, or "improve" a
  query someone else is accountable for. The only permitted change is appending
  a deterministic `ORDER BY`, and that change is flagged to them explicitly.
- **If no**: ask for the base tables instead (next question) and draft the
  query for approval. Never invent a join path.

### 3. Base tables and the driving table

Which tables feed this file, and which one drives the row grain. Needed to
cross-check the mapping document's `source` column, to establish whether any
join can fan the extract out, and to spot a mapped source that no table
provides.

### 4. Identity / grain columns

The member ID or equivalent, per table. Not optional, and not merely
informational — it determines:

- The **deterministic `ORDER BY`**. Without it, part boundaries move between
  runs and a re-send does not reconcile against the original.
- The declared **grain** — "one row per member" versus "one row per member per
  plan year" changes both the query and the expected volume.
- **Duplicate detection**, and any restart or resume key.

### 5. Every underspecified transformation rule

Each rule classified as *underspecified* in `mapping-document-schema.md`
(`derive from`, `per business logic`, `as per current process`, `same as
legacy`), each named-but-unlisted code set, and each conditional with no stated
fallthrough. Ask for the actual logic. This is the largest single cause of a
wrong loader file, and inferring it is never cheaper than asking.

### 6. Violation policies

For each of these the document is usually silent, and each choice changes the
output file:

- A **mandatory field is null** → fail the run, reject the row, or substitute
  a stated default?
- A **value exceeds `size`** → fail, reject the row, or truncate?
- A value **contains the delimiter** → fail, replace, or strip?
- A code value is **not in the code set** → fail, reject the row, or pass
  through?

"Reject the row" always needs a follow-up: where do rejected rows go, and does
the trailer count include them?

## Sane default available — apply it, but say so

| Question | Default if unstated | Say this |
| --- | --- | --- |
| Delimiter | `\|` | The request said pipe-delimited. |
| Line ending | `CRLF` | Most mainframe/vendor loaders expect it; flag it as an assumption because a vendor expecting LF fails obscurely. |
| Header row | none | Loader files usually have no header; state it and move on. |
| Trailer row | `TRAILER\|<rows in that part>` | Matches `extract-engine`. Confirm per-part versus whole-file, and the exact literal. |
| Null representation | empty string between delimiters | Distinguish from a literal `NULL` or a space-filled field — `CHAR` fields with a padding convention often want spaces. |
| Part naming | `<stem>_part{NNN}.txt` | Vendors often mandate a date stamp or a sequence; ask if the file is transmitted anywhere. |
| Batch size | 50,000 rows | Tune later; it affects memory, not output. |
| Encoding | UTF-8 | Ask if the target is a mainframe — EBCDIC or windows-1252 changes byte counts, and therefore the byte-based split. |
| Padding convention | `CHAR` right-pads with spaces, `NUMBER` left-pads with zeroes | Confirm **once per file**, not per field. |

## Also ask, when relevant

- **Full or incremental?** If incremental: the window column, the anchor date,
  and whether late-arriving rows are re-sent.
- **Environment and connection.** Server, database, and whether Windows
  Integrated Auth applies (it is what `extract-engine`'s `connection.py`
  assumes). If the pipeline must run headless on Linux, integrated auth is not
  available — surface that early, not at deployment.
- **Where do the files land?** A local directory, a share, or an SFTP drop.
  This skill generates the file; it does not transmit it unless asked.
- **Run cadence and volume.** Drives batch size and whether checkpoint/resume
  is worth generating at all.
- **Does an existing implementation exist** that this replaces? A legacy
  COBOL/SSIS/SQL job is the most reliable answer to every "as per current
  process" rule in the document, and diffing against its output is the best
  acceptance test available.
- **Multi-file mappings**: do the files share a run, and must their record
  counts reconcile against each other?

## Stop conditions

Do not proceed on a guess. Stop and ask when:

- A transformation rule is prose you cannot render deterministically.
- `codes` names a code set whose values are not in the document *and* not on
  another tab.
- `source` is blank for a field that is not a constant.
- Two rows collide on the same target column or the same ordinal, or the
  ordinals have gaps.
- `mandatory` is `Y` on a field whose source is nullable with no stated
  default.
- `size` is smaller than `source size` and no truncation rule is stated.
- The header-name and positional-column-C resolutions disagree.
- A rule implies an aggregate or pick-one, which means the grain is not what
  the field table suggests.
- The mapping document contradicts itself between `transformation rules` and
  `description` in substance rather than wording.

## How to ask

Use `AskUserQuestion` with grouped, concrete options rather than open prose
where the choice is genuinely bounded (split rule, violation policies, line
ending). For the unbounded ones — the source query, the actual business logic
behind an underspecified rule — ask plainly and wait.

Record every answer in the transformation mapper with **who** answered and
**when**. Six months later, the value in `config.yml` is either traceable to a
person or it is folklore.
