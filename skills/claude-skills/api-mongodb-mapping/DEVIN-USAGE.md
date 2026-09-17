# Using this skill in Devin

A human setup-and-run guide for `api-mongodb-mapping` on Devin Chat, Devin
Desktop, and Devin CLI. `SKILL.md` is written for the agent; this file is
written for you.

**Tag legend** (same convention as
`docs/devin-windsurf-microsoft-skills-integration.md`): `TESTED` = run on this
machine, `FILE` = read from files in this repository, `DOCS` = read in Devin's
published documentation, `VERIFY` = plausible but must be confirmed on your
actual Devin seat before you rely on it.

---

## What you get out of it

You point Devin at an API (in this repository, or an external one you hand
over) and a MongoDB target. You get back **one Markdown mapping document** —
nothing else. This skill does not generate integration code; compare with
`mapping-driven-loader-pipeline`, which does both a document and code.

The document states, per field: where it comes from, where it lands in
Mongo, what changes shape on the way (type coercion, renaming, embed vs.
flatten vs. reference), and — critically — whether each claim came from a
formal contract (an OpenAPI spec, a `$jsonSchema` validator) or was inferred
from a handful of samples, with that confidence stated plainly rather than
hidden.

And a batch of questions in between, on purpose — see
[Expect to be asked things](#expect-to-be-asked-things).

---

## Install

### Option A — repo-vendored skill (recommended, works for Chat, Desktop and CLI)

Devin has no skill marketplace, so skills are vendored into the repository at
the path Devin scans. `DOCS` This repo carries the copy at:

```
.agents/skills/api-mongodb-mapping/SKILL.md
```

Commit that path to the branch Devin is working on and it is discovered
automatically. No MCP server and no secrets are needed to produce the
document — this skill only reads files (the repository's own source, an
OpenAPI spec, sample payloads or documents) and never calls a network
endpoint or a live database.

The canonical copy, with all of its `references/`, `scripts/`, and
`examples/`, lives at `skills/claude-skills/api-mongodb-mapping/`. The
`.agents/skills/` copy is the discovery stub.

**The constraint that matters:** Devin's skill loader reads **`SKILL.md`
only`**. `DOCS`/`VERIFY` It does not automatically pull in `references/*.md`.
`SKILL.md` names each reference file by path, and Devin reads them with its
normal file tools when it needs them — but that means **the repository whose
API or MongoDB collection is being mapped must be in Devin's workspace**, not
just the skill's own files. If the API being mapped lives in a different
repository than this skill, vendor the skill folder into *that* repository so
the scan in `references/api-discovery.md` has something to scan.

### Option B — Devin Desktop beta skill

`VERIFY` — confirm the exact menu path against your Desktop build.

1. Open Devin Desktop → Settings → Skills (beta).
2. Add a skill, paste the contents of
   `skills/claude-skills/api-mongodb-mapping/SKILL.md`.
3. Keep the YAML front matter intact — `name`, `description`, and `triggers`
   are what make Devin auto-select the skill.

Because a pasted skill has no `references/` beside it, also point Desktop at
this repository (for the scripts and reference docs) **and** at the
repository containing the API/collection being mapped, if different. A
pasted `SKILL.md` alone will produce a plausible-looking mapping that skips
the discovery-preference rules and the stop conditions.

### Option C — Devin CLI

`.agents/skills/` is on the CLI's discovery path, as is `.windsurf/skills/`.
`DOCS` Option A covers it.

---

## Prerequisites

| Requirement | Why | Notes |
| --- | --- | --- |
| Python 3.9+ | both readers | `TESTED` on 3.13 |
| `PyYAML` | only if the OpenAPI/Swagger spec is `.yaml`/`.yml` | `pip install pyyaml`. A `.json` spec needs nothing beyond the standard library. `TESTED` |
| The repository whose API is being mapped, in Devin's workspace | internal-API repo scanning | Not needed if every API in scope is external and handed over as a spec/samples |
| Nothing else | this skill makes no database connection and no HTTP call | Neither reader accepts a URL; both read local files only |

---

## Getting the inputs to Devin

**For an internal API**, nothing to hand over — the repository is the
source. Just name the route(s) or say "scan the repo for the provider API."

**For an external API**, in order of preference:

1. An OpenAPI/Swagger file, or a Postman collection exported as OpenAPI.
2. Sample request/response payloads, plus whatever documentation exists (a
   vendor reference page, an email, a Confluence export).
3. If neither exists yet, say so — Devin should stop and ask rather than
   invent the contract.

**For the MongoDB side**, in order of preference:

1. Nothing to hand over if a `$jsonSchema` validator or a schema-defining
   model already exists in the repository — Devin finds it.
2. A handful of representative sample documents (a `mongoexport`, or a
   `.find().limit(n)` result) if neither exists. **5 or more**, or the result
   is marked low-confidence.

Then say, plainly:

> Use the api-mongodb-mapping skill on `POST /providers` and
> `GET /providers/{id}` against the `providers` collection. The API spec is
> at `<path>`. Produce the mapping document; ask me anything you can't
> confirm from the spec or the validator.

---

## Invoking it

| Surface | How |
| --- | --- |
| Devin Chat / Desktop | `@skills:api-mongodb-mapping`, or describe the task — the `description` and `triggers` are written for auto-selection |
| Devin CLI | `/api-mongodb-mapping` |
| Any | Mention a mapping document, an API contract, an OpenAPI/Swagger spec, a MongoDB collection, or field mapping; the triggers cover those phrasings |

---

## Expect to be asked things

Four inputs have **no default at all**:

1. **What actually gets persisted** — an operation's request, its response,
   or both. Never inferred from field-name similarity.
2. **The identity/key field** on each side and how they correspond — decides
   insert vs. upsert semantics.
3. **Embed, flatten, or reference**, per nested object or array.
4. **Null vs. missing**, per optional field — Mongo distinguishes them and a
   codebase is rarely consistent about which one applies.

Plus one question per unresolved `$ref`, per `oneOf`/`anyOf` not collapsed to
one shape, per Mongo path observed with more than one non-null type, and per
disagreement between two sources for the same side.

**To cut the question count, put this in your opening message:**

```
API:              POST /providers, GET /providers/{id}  (internal | external)
Spec/source:      <path to OpenAPI file, or "scan the repo">
What persists:    the response (includes server-assigned fields)
Mongo target:     providers collection
Mongo schema:     db/validators/providers.jsonschema.json  (or "no validator, infer from samples: <paths>")
Identity/key:     API externalProviderId -> Mongo sourceSystemId; upsert on that field
Nested handling:  specialties[] and address embed; nothing referenced
Null vs missing:  absent API field -> Mongo null (not omitted), for all optional fields
Output:           <path> or "print it, don't write a file yet"
```

If Devin produces a mapping document **without** asking about the
identity/key field or the null-vs-missing treatment, treat that as a red
flag — check whether the skill actually loaded.

---

## A session, end to end

Steps 1–3 are `TESTED` against the fixtures committed in this skill's
`examples/`; the rest is the documented procedure.

**1. Parse the API side.**

```bash
python skills/claude-skills/api-mongodb-mapping/scripts/openapi_reader.py \
    report --spec skills/claude-skills/api-mongodb-mapping/examples/sample-external-provider-api.openapi.yaml
```

`TESTED` — prints:

```
Sample External Provider Directory API 1.2.0 [openapi3]
2 endpoint(s)
  POST   /providers                               req= 13 resp= 12
  GET    /providers/{providerId}                  req=  0 resp= 12
```

No issues on this fixture — the spec has no unresolved `$ref`s and no
`oneOf`/`anyOf`. A real spec with either will list them, one per line, and
those become Open Questions in the document rather than something resolved
silently.

**2. Parse (or infer) the Mongo side.** With a validator, read it directly —
it is usually short enough to quote from. Without one:

```bash
python skills/claude-skills/api-mongodb-mapping/scripts/json_sample_schema_reader.py \
    report --samples skills/claude-skills/api-mongodb-mapping/examples/sample-provider-notes-documents.json
```

`TESTED` — prints, among other things:

```
3 sample document(s) pooled, confidence=low
6 distinct path(s)
  ...
  priority                                 integer,string        [polymorphic, present=67%]
  tag                                      null,string           [null=33%]
Issues: 2 info, 2 warning
```

That output is the fallback path working as intended: 3 samples is below the
5-document confidence floor, `priority` is flagged polymorphic rather than
silently typed as one thing, and `tag`'s null-vs-absent behavior is surfaced
rather than collapsed.

**3. Devin asks its questions.** Answer them; every answer is recorded in the
document with who said it and when.

**4. Review the mapping document.** Check every "passthrough" row's type on
both sides — BSON's `Date`, `ObjectId`, and `Decimal128` have no exact JSON
equivalent, so a "passthrough" is often wrong in exactly the row it looks
safe. Then read Open Questions.

Compare against `examples/sample-api-mongodb-mapping.md` for the expected
shape.

---

## Internal-system and customer data: read this before you upload

An internal API's routes and an internal MongoDB collection's field names
describe how this organization's systems work, and a real mapping document
built from either is not something to commit here.

- **Do not commit a mapping document, a digest, or sample documents built
  from a real internal API or a real collection into this repository.** The
  skill routes artifact paths through `sql-server-schema/scripts/
  guardrails.py`'s `resolve_artifact_path()`, which refuses `skills/`,
  `mcp/`, `docs/`, and any in-repo path `git check-ignore` does not confirm.
  `FILE` That is a backstop, not a policy.
- The only committed mapping artifacts here are the fictional fixtures in
  `examples/` and the mapping document built from them.
- Sample MongoDB documents pulled from a real collection are real data by
  definition. Read field *definitions* from them, never carry a live value
  into the document, and strip anything sensitive before it leaves the
  source system.
- Uploading a spec or sample documents to a Devin session puts them on
  Devin's machine and in that session's history. Clear that with whoever
  owns the API or the collection first, same as you would before uploading a
  mapping spreadsheet.

---

## Reviewing what Devin produced

In order of how much time each item saves you:

1. **Every "passthrough" row's type on both sides.** The most common silent
   defect: an API `date-time` string stored without a stated parse rule, or
   a numeric field stored as `double` when the value is money.
2. **The null-vs-missing note on every optional field.** Absent by default
   from most people's mental model of "just map the fields."
3. **The embed/flatten/reference choice**, especially for any array with no
   stated bound.
4. **Fields with no counterpart, both directions.** An API field silently
   dropped, or a Mongo field silently assumed to come from "somewhere."
5. **Open Questions** — empty is a claim, not an absence.
6. **The provenance block.** Does it correctly say internal vs. external,
   and validator/model/spec vs. sample-inferred? A document that names its
   own confidence wrongly is worse than one that names it honestly as low.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `could not detect dialect: no top-level 'openapi: 3.x' or 'swagger: 2.0' key` | The file isn't an OpenAPI/Swagger document, or is a Postman collection | Export the Postman collection as OpenAPI first, or point at sample payloads instead |
| `external or non-pointer $ref left unresolved` | The spec references another file or a URL | Expected — this reader never fetches anything. Merge the referenced file into the main spec first, or resolve it by hand and note it in Open Questions |
| `$ref cycle detected` | A self-referential schema (e.g. a tree/recursive structure) | Expected and stopped safely; describe the recursive shape by hand in the mapping document rather than flattening it infinitely |
| A field shows up as `polymorphic` with variant type names listed | A `oneOf`/`anyOf` in the spec, or more than one non-null type across Mongo samples | Ask which variant/type actually applies; do not pick one for Devin |
| `only N sample document(s) pooled ... confidence low` | Fewer than 5 sample documents given | Provide more, or accept the document will say "hypothesis, not confirmed schema" for that side |
| `ModuleNotFoundError: yaml` | Missing PyYAML, and the spec is `.yaml`/`.yml` | `pip install pyyaml`, or convert the spec to JSON first |
| Devin produced a mapping document without asking about identity/key or null-vs-missing | The skill probably wasn't loaded | Confirm `.agents/skills/api-mongodb-mapping/SKILL.md` is on the branch Devin is on |
| Devin tried to call the external API or connect to MongoDB to "double check" | Out of scope for this skill — it reads files only | Redirect it to the spec file, the validator/model code, or sample documents instead |

---

## Known limits

- **Postman collections are not a directly supported input.** Export as
  OpenAPI first (Postman's own "Export" → "OpenAPI 3.0" does this), or fall
  back to sample payloads.
- **`allOf` merging is first-writer-wins on a conflicting property.** Flagged
  as a warning when it happens; check the flagged property by hand.
- **`oneOf`/`anyOf` are never resolved automatically**, by design — every
  occurrence becomes a question.
- **Repo scanning for an internal API is pattern-based, not a real parser**
  for most of the listed frameworks (FastAPI/Pydantic and NestJS DTOs are the
  exception, since those are themselves typed schemas read directly). Treat
  a field found by grepping handler code as a hint to confirm, not a fact.
- **Sample-document inference can never prove a field is absent** — it can
  only say a field was not observed in the samples given. A larger sample set
  raises confidence; it never reaches certainty the way a validator or a spec
  does.
- **This skill produces no code.** If the actual ingestion pipeline also
  needs to be generated, that is out of scope here — write it by hand against
  this document, or see whether `mapping-driven-loader-pipeline`'s patterns
  (the connection contract, the generator-style structure) are worth adapting
  even though that skill targets a flat-file loader, not MongoDB.
