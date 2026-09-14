# Using this skill in Devin

A human setup-and-run guide for `mapping-driven-loader-pipeline` on Devin
Chat, Devin Desktop, and Devin CLI. `SKILL.md` is written for the agent; this
file is written for you.

**Tag legend** (same convention as `docs/devin-windsurf-microsoft-skills-integration.md`):
`TESTED` = run on this machine, `FILE` = read from files in this repository,
`DOCS` = read in Devin's published documentation, `VERIFY` = plausible but must
be confirmed on your actual Devin seat before you rely on it.

---

## What you get out of it

You give Devin a mapping document. You get back:

1. **`mapping_digest.json`** — the spreadsheet parsed deterministically, with
   every problem it found listed.
2. **A transformation-mapper Markdown file** — the reviewable spec, one section
   per target file, every transformation rule quoted verbatim beside its
   interpretation, with an Open Questions list.
3. **A generated Python pipeline** — one directory per output file, that
   extracts from SQL Server, transforms per the mapping, writes pipe-delimited
   flat files, and splits them by record count and/or byte size.

And a batch of questions in between. That is the skill working, not the skill
failing — see [Expect to be asked things](#expect-to-be-asked-things).

---

## Install

### Option A — repo-vendored skill (recommended, works for Chat, Desktop and CLI)

Devin has no skill marketplace, so skills are vendored into the repository at
the path Devin scans. `DOCS` This repo already carries the copy:

```
.agents/skills/mapping-driven-loader-pipeline/SKILL.md
```

Commit that path to the branch Devin is working on and it is discovered
automatically. Nothing else to configure — this skill needs **no MCP server**
and **no secrets** to produce the mapper and the code. It only needs a database
when you actually run the generated pipeline, which is your call, not Devin's.

The canonical copy lives at
`skills/claude-skills/mapping-driven-loader-pipeline/` with all of its
`references/`, `scripts/`, and `examples/`. The `.agents/skills/` copy is the
discovery stub.

**The constraint that matters:** Devin's skill loader reads **`SKILL.md` only**.
`DOCS`/`VERIFY` It does not automatically pull in `references/*.md`. That is
fine and intended — `SKILL.md` names each reference file by path, and Devin
reads them with its normal file tools when it needs them. But it means **the
repository must be in Devin's workspace**, not just the one `SKILL.md`. If you
vendor only the stub into an unrelated repo, Devin gets the procedure and
loses the column-interpretation rules, the code templates, and the question
checklist.

### Option B — Devin Desktop beta skill

`VERIFY` — confirm the exact menu path against your Desktop build; the beta
skills UI has been moving.

1. Open Devin Desktop → Settings → Skills (beta).
2. Add a skill, paste the contents of
   `skills/claude-skills/mapping-driven-loader-pipeline/SKILL.md`.
3. Keep the YAML front matter intact. `name`, `description` and `triggers` are
   what make Devin auto-select the skill instead of you having to invoke it.

Because a pasted skill has no `references/` beside it, **also point Desktop at
this repository** as a working directory, or paste the reference files in as
additional skills/knowledge. A pasted `SKILL.md` alone will produce a
plausible-looking pipeline that skips the interpretation rules.

### Option C — Devin CLI

`.agents/skills/` is on the CLI's discovery path, as is `.windsurf/skills/`.
`DOCS` Option A covers it; no extra step.

---

## Prerequisites

| Requirement | Why | Notes |
| --- | --- | --- |
| Python 3.10+ | the reader and the generated pipeline | `TESTED` on 3.13 |
| `openpyxl` | reading the mapping workbook | `pip install openpyxl`. The only hard dependency of the reader. |
| `pyodbc` + ODBC Driver 18 | **only** to *run* the generated pipeline | Not needed to generate anything. Skip it if Devin is only producing artifacts. |
| This repository in Devin's workspace | the references and the existing generator patterns the skill reuses | See the constraint in Option A |

Devin runs on Linux. **Windows Integrated Auth is not available there**, which
is what `extract-engine`'s `connection.py` assumes. `FILE` So Devin can
generate the pipeline perfectly well, but a run against an on-premises SQL
Server with Windows auth has to happen on a Windows host with a Kerberos
ticket — yours, not Devin's. Decide this before you ask Devin to "run it", or
you will get a connection failure that looks like a code defect.

---

## Getting the mapping document to Devin

Pick one, in this order of preference:

1. **Commit it to the repo on a branch** — best, because the digest's
   `source_sha256` then pins a reviewable revision. Only do this if the
   document is not customer-sensitive; most are (see
   [Customer data](#customer-data-read-this-before-you-upload)).
2. **Attach it to the Devin session.** Devin puts attachments on its machine;
   ask it for the absolute path, then hand that path to the skill.
3. **Point at a share or artifact store** Devin's machine can reach.

Then say, plainly:

> Use the mapping-driven-loader-pipeline skill on `<path>/MemberLoaderMapping_v4.xlsx`.
> Produce the transformation mapper first; do not generate code until I have
> reviewed it.

That last clause is worth including every time. It makes step 4 a checkpoint
instead of something that flies past on the way to code.

---

## Invoking it

| Surface | How |
| --- | --- |
| Devin Chat / Desktop | `@skills:mapping-driven-loader-pipeline`, or just describe the task — the `description` and `triggers` in the front matter are written for auto-selection |
| Devin CLI | `/mapping-driven-loader-pipeline` |
| Any | Mention a mapping document, a loader file, a pipe-delimited extract, or file splitting; the triggers cover those phrasings |

---

## Expect to be asked things

The skill is deliberately built to stop rather than guess. Four inputs have
**no default at all** and it will not generate code without them:

1. **The split rule** — rows per file, max bytes per file, or both, and which
   wins. A guessed split is a rejected transmission.
2. **The source SQL query** — or, if there isn't one, the base tables so it can
   draft a query for your approval.
3. **The base tables** and which one drives the row grain.
4. **The identity / grain column** (member ID or equivalent). This sets the
   deterministic `ORDER BY`; without it, part boundaries move between runs and
   a re-send doesn't reconcile.

Plus one question per underspecified transformation rule, and one per violation
policy (mandatory null, size overflow, delimiter collision, unmapped code).

**To cut the question count, put this in your opening message:**

```
Mapping doc:      <path>
Target file(s):   MEMBER_LOADER
Source SQL:       <paste it, or "none - base tables are dbo.Member, dbo.Enrollment, dbo.Grp">
Driving table:    dbo.Member
Identity column:  MemberKey (one row per member per plan year)
Split rule:       50,000 records per file; no byte limit
Delimiter:        pipe
Line ending:      CRLF
Header / trailer: no header; trailer "TRAILER|<rows in part>"
Nulls:            empty between delimiters
On mandatory null / size overflow / delimiter collision: fail the run
Output:           /tmp/extract/member_loader
```

That answers most of the gate in one go. Anything you leave out, it asks for.

If Devin ever produces a pipeline **without** asking about the split rule,
treat that as a red flag: check whether it actually loaded the skill, or
whether it reconstructed something from the repository instead.

---

## A session, end to end

What a correct run looks like. Steps 1 and 2 are `TESTED` on this machine; the
rest is the documented procedure.

**1. Parse the document.**

```bash
python skills/claude-skills/mapping-driven-loader-pipeline/scripts/mapping_reader.py \
    report --workbook <path-to-mapping>.xlsx
```

On the committed fictional sample this prints — `TESTED`:

```
DEPENDENT_LOADER  (4 fields, 4 included, 0 excluded)
MEMBER_LOADER     (16 fields, 15 included, 1 excluded)
  rules:   code_lookup=2, constant=1, date_render=3, numeric_render=1,
           passthrough=3, row_sequence=1, string_transform=4, underspecified=1
Sheets skipped (check these for code-set values):
  Code Sets - no header row matched
Issues: 2 error, 9 warning, 2 info
```

Note the exit code: **1 when there are errors**, so it fails a pipeline step
rather than passing quietly. The two errors on the sample are intentional — a
code set named but not listed, and a rule reading "as per current process".
Both are things a human has to answer.

**2. Get the machine-readable digest.**

```bash
python .../scripts/mapping_reader.py read --workbook <path>.xlsx --out ./mapping_digest.json
```

**3. Devin asks its questions.** Answer them. Every answer gets recorded in the
mapper with who said it and when — six months later that is the difference
between a traceable decision and folklore.

**4. Review the transformation mapper.** This is your real checkpoint. Read the
per-field subsections and check the **verbatim rule quote** against your
reading of it. Then read Open Questions and the rule catalog's "Unstated
behaviour resolved as" column — that column is where every decision the
document didn't make got made.

Compare against `examples/sample-transformation-mapper.md` for the expected
shape.

**5. Devin generates the pipeline.** One directory per output file under
`pipelines/`, shared machinery in `common/`, `run.py` at the top.

**6. Verify before connecting to anything.**

```bash
python -m pytest <out_root>/tests -q
python <out_root>/run.py validate-layout --pipeline member_loader
python <out_root>/run.py dry-run        --pipeline member_loader
```

`dry-run` prints the resolved SQL, the field layout, and the split rule without
opening a connection. It is the cheapest possible check that the mapping was
read correctly. **Run it before anyone touches a database.**

---

## Customer data: read this before you upload

A real mapping document names internal source systems, tables, and columns. So
does everything generated from it — the digest, the mapper, and the pipeline's
`extract.sql`.

- **Do not commit a real mapping document, digest, or mapper into this
  repository.** The skill routes artifact paths through
  `sql-server-schema/scripts/guardrails.py`'s `resolve_artifact_path()`, which
  refuses `skills/`, `mcp/`, `docs/`, and any in-repo path `git check-ignore`
  does not confirm. `FILE` That is a backstop, not a policy — the residual risk
  is a human copying one in by hand.
- The only committed mapping artifacts here are
  `examples/sample-mapping-member-loader.xlsx` and
  `examples/sample-transformation-mapper.md`, both **entirely fictional**, with
  no data values in them at all. `TESTED` Use them to trial the skill before
  you upload anything real.
- Uploading a document to a Devin session puts it on Devin's machine and in
  that session's history. Clear that with whoever owns the document first.
- Mapping documents for member/eligibility feeds routinely specify identifier
  fields. This skill only ever reads **field definitions**, never data values —
  but if your document has a sample-data tab, strip it before uploading.

---

## Reviewing what Devin produced

The acceptance gate, in order of how much time each item saves you:

1. **`dry-run` output.** Does the field order match the mapping document's
   `loader file column`? Does the split rule match what the vendor asked for?
2. **The golden line test** in `tests/`. One fake source row rendered to its
   expected pipe-delimited line. This is what catches an off-by-one in the
   layout — the defect class that otherwise surfaces as "the vendor says
   column 9 is wrong" a week later.
3. **`EXCLUDED` in `layout.py`.** Every field the document marked
   `target file needed = N` should be listed there, not silently absent.
4. **The `ORDER BY` in `extract.sql`.** Present, and on the identity column.
5. **Open Questions in the mapper.** Empty is a claim, not an absence. If the
   document had `TBD`s and the mapper has no open questions, something was
   resolved silently.
6. **The copy-vs-import note in the generated `README.md`.** It should say
   which components were copied from `extract-engine` and why.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no header row matched (best score N)` | The sheet's headers don't match enough known aliases, or it's a notes/code-set tab | Check `sheets_skipped` in the digest. If a real mapping sheet was skipped, add its header spellings to `ALIASES` in `mapping_reader.py` |
| `ambiguous target column name: header 'X' is at column 5 but column C holds 'Y'` | The document's shape differs from the expected shape | Deliberate hard stop — tell Devin which column is the target column name. Do not let it pick |
| `no column matched a 'column name' alias and Excel column C is empty` | Wrong sheet, or a heavily reshaped document | Confirm which sheet holds the field list |
| `ModuleNotFoundError: openpyxl` | Missing dependency on Devin's machine | `pip install openpyxl` |
| Every field comes back `mandatory: false` | The column is headed "Nullable"/"Optional" and the sense was inverted | Check `mandatory_sense` in the digest — it records which way it read the column |
| Ordinals reported as non-contiguous | Gaps or duplicates in `loader file column` | A real spec defect. A gap shifts every following field in the line — fix the document, don't work around it |
| `a split rule is required` at pipeline startup | `config.yml` has neither limit | Intentional. Supply the rule |
| Devin generated code without asking anything | The skill probably wasn't loaded | Confirm `.agents/skills/mapping-driven-loader-pipeline/SKILL.md` is on the branch Devin is on |
| Connection failure when running the pipeline | Devin is on Linux; Windows Integrated Auth is unavailable there | Run the pipeline on a Windows host, or switch to a SQL login |

---

## Known limits

- **Rule classification is a reading of prose.** `classify_rule()` in
  `mapping_reader.py` is keyword precedence, not comprehension. It is ordered
  deliberately — "Hardcode the extract run date" is a constant despite the word
  "date", and "Left pad with zeroes to 12" is a pad despite the word "zeroes"
  `TESTED` — but it is a hint for the mapper's catalog, never a substitute for
  reading the rule. `unclassified` and `underspecified` both mean *ask*.
- **The reader does not validate the source.** It never checks that a
  `source field` exists, that a `source type` is real, or that the join path is
  correct. Use `skills/claude-skills/sql-server-schema/` for that.
- **The generated code is a first draft.** It is meant to be refined. The
  templates in `references/generated-pipeline-layout.md` are stdlib + `pyodbc`
  only, which is the right default for a pipeline handed to another team and
  the wrong one if your transformations are genuinely vectorisable at volume —
  ask for Polars in that case.
- **`.csv`/`.xls` inputs.** `openpyxl` reads `.xlsx` only. Convert first.
- **Devin's skill loader reads `SKILL.md` only.** `DOCS`/`VERIFY` The
  references are read as files, which is why the repo has to be in the
  workspace.
- **Consider not generating code at all.** `extract-engine` already turns an
  Excel-authored feed into split pipe-delimited files with the mapping held as
  data in `meta.*` tables. If your mapping is a straightforward field list
  whose rules are already in that catalog, that route makes a spec change an
  Excel edit and a rerun rather than a code edit and a deploy. The skill is
  instructed to raise this before generating a parallel codebase — take it
  seriously when it does.
