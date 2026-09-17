---
name: api-mongodb-mapping
description: >
  Use this skill when an API's fields need to be documented against a
  MongoDB target field by field — producing a reviewable **API-to-MongoDB
  mapping document**, and nothing else (no code is generated). The API can be
  **internal**, discovered by scanning this repository's own route/controller
  and request/response-model code (or a committed OpenAPI/Swagger file), or
  **external**, where the contract has to be handed over as a spec, a Postman
  collection, or sample payloads because the source isn't available here. The
  MongoDB side is read from whatever is most authoritative and available: a
  `$jsonSchema` validator, schema-defining application code (Mongoose,
  Beanie, Spring Data MongoDB, the MongoDB C# driver), or, failing both,
  inferred from representative sample documents with the inference's
  confidence stated plainly. It never invents a field for a source it cannot
  see, never resolves a naming or type mismatch silently, and always states
  whether the API is internal or external and whether the Mongo side is
  authoritative or inferred. Example requests: "generate a mapping document
  for our provider API and the providers collection", "document how this
  endpoint's fields map onto Mongo", "we're integrating with this external
  API — map its response onto our collection schema", "the mapping doc is
  stale, rescan and update it".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
triggers:
  - api mapping document
  - api to mongodb mapping
  - api to mongo mapping
  - mongodb field mapping
  - mongo field mapping
  - field mapping document
  - endpoint mapping
  - openapi mapping
  - swagger mapping
  - external api mapping
  - internal api mapping
  - api contract to mongo
  - map api fields
  - map api response to mongo
  - mongo schema discovery
  - jsonschema validator
  - sample document schema
  - mongodb collection mapping
  - api integration mapping
  - request response field mapping
---

# API-to-MongoDB Mapping Document

> **Devin discovery copy.** The canonical skill, with its `references/`,
> `scripts/`, and `examples/`, lives at
> `skills/claude-skills/api-mongodb-mapping/` in this
> repository; every path below is repo-root-relative because Devin's
> loader reads this file alone. Setup and usage:
> `skills/claude-skills/api-mongodb-mapping/DEVIN-USAGE.md`.
> Edit the canonical copy, not this one.

Turn an API contract and a MongoDB target into one artifact: a reviewable
**mapping document** that says, field by field, where each API value goes in
Mongo, what changes shape on the way, and what nobody has confirmed yet.

This is a **document-only** skill. Unlike its sibling
`mapping-driven-loader-pipeline`, it does not generate integration code. The
document is the deliverable, and its whole purpose is to stop that
translation from being redone — differently, by whoever touches the
ingestion code next — every time the API or the collection changes.

## The order of work — do not skip or reorder

1. **Establish scope.** Which API operation(s), which Mongo collection(s),
   and — for every API involved — internal (in this repository) or external.
   This gate has no default; see `skills/claude-skills/api-mongodb-mapping/references/clarifying-questions.md` item 1.
2. **Discover the API side.** Internal: scan this repository per
   `skills/claude-skills/api-mongodb-mapping/references/api-discovery.md` — prefer a committed OpenAPI/Swagger file,
   then typed request/response models, then raw handler code (flagged
   lowest-confidence). External: get a spec, a Postman-exported OpenAPI file,
   or sample payloads from the user. Never invent a field for a source you
   cannot read.
3. **Discover the MongoDB side.** Per `skills/claude-skills/api-mongodb-mapping/references/mongo-discovery.md`: prefer
   a `$jsonSchema` validator, then schema-defining application code, then —
   only as a last resort — infer from sample documents and say so plainly.
4. **Ask the clarifying questions.** The gate list is
   `skills/claude-skills/api-mongodb-mapping/references/clarifying-questions.md`. What actually gets persisted, the
   identity/key field, the embed/flatten/reference choice per nested
   structure, and null-vs-missing per optional field all have **no safe
   default**.
5. **Write the mapping document** and have it reviewed. Format and required
   sections: `skills/claude-skills/api-mongodb-mapping/references/mapping-document-format.md`.
6. **Offer it for review**, then re-run discovery and diff whenever either
   side changes.

## Step 1: establish scope

Get, at minimum: which operation(s) on the API side, which collection(s) on
the Mongo side, and whether each API is internal or external. If the user's
request already states this plainly (a route, a collection name), confirm it
rather than re-asking; if it doesn't, ask before scanning anything; a scan
with no target just produces noise.

## Step 2: discover the API side

Read `skills/claude-skills/api-mongodb-mapping/references/api-discovery.md` in full before doing this. In short:

- **Internal API** — scan this repository. A committed, generated
  OpenAPI/Swagger file beats hand-scanning code; if none exists, typed
  request/response models (Pydantic, DTOs with `class-validator`, Spring
  `@RequestBody` classes, ASP.NET Core action signatures) are themselves the
  contract and are read directly; untyped handler code is the weakest
  signal and every field found this way is flagged
  `discovery: inferred from handler code`.
- **External API** — ask for an OpenAPI/Swagger file, a Postman collection
  exported to OpenAPI, or sample payloads plus whatever documentation exists.
  A hard stop, not a guess, if none of these can be provided.

Parse a spec (from either path) deterministically:

```bash
python skills/claude-skills/api-mongodb-mapping/scripts/openapi_reader.py \
    read --spec <path-to-openapi-or-swagger-file> --out api_digest.json
```

The reader resolves same-document `$ref`s, merges `allOf`, and **never**
collapses a `oneOf`/`anyOf` to one branch — it records every variant and
raises an issue instead. Read the digest, not the spec file, once it exists;
`report` prints a quick per-endpoint field-count summary and every issue.

When only sample payloads are available (no formal spec, on either the
internal or the external path), use the same reader as the Mongo
sample-document fallback:

```bash
python skills/claude-skills/api-mongodb-mapping/scripts/json_sample_schema_reader.py \
    read --samples <payload1.json> [<payload2.json> ...] --out api_digest.json
```

## Step 3: discover the MongoDB side

Read `skills/claude-skills/api-mongodb-mapping/references/mongo-discovery.md` in full before doing this. In short,
most-to-least authoritative:

1. A `$jsonSchema` validator — Mongo enforces it on writes; find it by
   searching for `$jsonSchema`, `createCollection`, or `collMod`, or by
   asking for `db.getCollectionInfos()`'s output. Usually small enough to
   read and quote directly rather than script.
2. Schema-defining application code — Mongoose schemas, Beanie `Document`
   subclasses, Spring Data `@Document` classes, MongoDB C# driver POCOs. This
   shapes writes but Mongo itself does not enforce it, and it may not be the
   only writer to the collection — ask.
3. Sample documents, only when neither of the above exists:

   ```bash
   python skills/claude-skills/api-mongodb-mapping/scripts/json_sample_schema_reader.py \
       read --samples <doc1.json> [<doc2.json> ...] --out mongo_digest.json
   ```

   This is the same script as step 2's payload fallback — it pools whatever
   JSON documents it is given and reports, per path, every type observed,
   presence ratio, null ratio, and a best-effort `ObjectId`/`Date` hint. Below
   5 pooled documents the whole result is marked `confidence: low`, and every
   claim built on it in the mapping document must say so.

## Step 4: ask, do not assume

Work through `skills/claude-skills/api-mongodb-mapping/references/clarifying-questions.md`. Ask in one grouped batch
where you can. You are explicitly authorised — and expected — to ask for:

- **What actually gets persisted** for each API operation: its response, its
  request, or both. Never inferred from field-name similarity alone.
- **The identity/key field** on each side, and how they correspond — this
  decides insert vs. upsert semantics and has no safe default.
- **The embed/flatten/reference choice**, per nested object or array. Do not
  default to "embed everything."
- **Null vs. missing**, per optional field, since Mongo distinguishes them
  and a codebase is rarely consistent about which one an absent API value
  should become.

Stop and ask — do not proceed on a guess — whenever: a `$ref` does not
resolve, a `oneOf`/`anyOf` was not collapsed, a Mongo path was observed with
more than one non-null type, fewer than 5 sample documents back an inferred
field, or two sources for the same side disagree (a spec versus the handler
code that implements it, or a validator versus what the samples actually
contain).

## Step 5: the mapping document

One Markdown file per API-to-collection pairing (or one section per pairing,
for a mapping that covers several together). Contents, in order: provenance
for both sides (spec/model/validator path plus its sha256, or the sample-set
and its confidence), a summary, per-pairing sections covering direction, the
field table, nested/array handling, fields with no counterpart on either
side, type-coercion notes, and Open Questions.

Two non-negotiables, same discipline as the loader-pipeline skill's mapper:

- **State the discovery method for every claim** — a field read from a
  formal spec or validator is not the same kind of fact as one inferred from
  three samples, and the reviewer needs to be able to tell them apart at a
  glance, not by re-deriving it.
- **This is very often internal-system data.** An internal API's routes and
  an internal collection's field names describe how this organization's
  systems work. Route the destination through
  `skills/claude-skills/sql-server-schema/scripts/guardrails.py`'s `resolve_artifact_path()`,
  write outside version control by default, and emit the do-not-commit
  header unless every field involved is already public. Never commit a real
  mapping document into this repository.

Full structure and a worked template: `skills/claude-skills/api-mongodb-mapping/references/mapping-document-format.md`.

## Step 6: review, then keep it current

Present the document as a first draft and invite corrections — especially on
anything flagged low-confidence or an Open Question. When either side
changes (a new API version, a validator edit, a Mongoose schema change),
re-run steps 2–3, diff the new digests against the previous ones, and let
that diff drive the document's revision rather than rewriting it from
scratch.

## Bundled assets

### `skills/claude-skills/api-mongodb-mapping/scripts/`

| Script | Role |
| --- | --- |
| `openapi_reader.py` | Deterministic OpenAPI 3.x / Swagger 2.0 reader. Resolves same-document `$ref`s, merges `allOf`, flags every `oneOf`/`anyOf` as polymorphic rather than picking a branch. `read`/`report` subcommands, same shape as `mapping_reader.py`. No network calls; no `$ref` is ever fetched remotely. |
| `json_sample_schema_reader.py` | Infers a per-path field digest from sample JSON documents — used for both an external/internal API with no formal spec and a MongoDB collection with no validator or model code. Reports type(s) seen, presence ratio, null ratio, a low-confidence flag below 5 pooled samples, and a best-effort `ObjectId`/`Date` hint for MongoDB Extended JSON and 24-hex strings. |

### `skills/claude-skills/api-mongodb-mapping/references/`

| File | Covers |
| --- | --- |
| `api-discovery.md` | Internal repo-scan signals per framework, external-API acquisition order of preference, and when to stop and ask |
| `mongo-discovery.md` | `$jsonSchema` validators, schema-defining code per stack, the sample-document fallback, and the missing-vs-null trap |
| `mapping-document-format.md` | The Markdown artifact's required sections, the emitter idiom, and a full worked template |
| `clarifying-questions.md` | The gate checklist: what must be asked, what has no default, and the stop conditions |

### `skills/claude-skills/api-mongodb-mapping/examples/`

| Path | Contents |
| --- | --- |
| `sample-external-provider-api.openapi.yaml` | A fictional external API spec — the worked example's API side |
| `sample-providers-collection.jsonschema.json` | A fictional `$jsonSchema` validator — the worked example's authoritative Mongo side |
| `sample-provider-notes-documents.json` | Fictional sample documents with no validator behind them — the worked example for the sample-inference fallback (`confidence: low`, a polymorphic field, a null-vs-missing case, and Extended JSON `ObjectId`/`Date` detection) |
| `sample-api-mongodb-mapping.md` | The mapping document this skill produces from the first two fixtures — the golden reference for step 5's output shape |

## Related material in this repository

- `skills/claude-skills/mapping-driven-loader-pipeline/` — the sibling skill
  for a spreadsheet-specified loader/extract feed. Shares this skill's
  discipline (read deterministically, ask rather than guess, quote the
  source before interpreting it) but targets a flat-file loader and also
  generates pipeline code; this skill stops at the document.
- `skills/claude-skills/sql-server-schema/` — the source of
  `guardrails.py`'s `resolve_artifact_path()`, reused here for the same
  reason: a generated mapping document routinely names internal systems.
- `skills/ide-references/devin/api-mongodb-mapping.knowledge.md` — the
  condensed Devin knowledge entry for this skill.
