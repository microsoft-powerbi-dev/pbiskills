# Clarifying questions: the gate

Work this list before writing the mapping document. Ask in grouped batches
where you can, not one question at a time, and never let an unanswered item
become a silent default.

As in `mapping-driven-loader-pipeline`, some of these have a **sane default**
you may apply while stating it, and some have **no default** and block the
document. They are separated below on purpose.

## No default — the document is blocked until answered

### 1. Which endpoints/collections, and internal or external

The scope: which API operation(s) and which Mongo collection(s) this mapping
covers, and for each API — internal (in this repository) or external. This
decides which of `api-discovery.md`'s two paths to use, and an external API
with no spec and no sample payloads is a hard stop (see that reference's
"nothing at all yet" case).

### 2. What actually gets persisted

For every endpoint: does its *response* get written to Mongo, does its
*request* payload get written as sent, or both? This is never safe to assume
from field-name similarity — an update endpoint's request and response
commonly differ in exactly the fields that matter (server-generated
timestamps, computed statuses). Ask the person who owns the ingestion code if
one exists; if none exists yet, this document is specifying it, so ask what
is intended.

### 3. The identity / key field

What uniquely identifies a record on each side, and how they correspond: the
API's own identifier field, and whichever Mongo field is used to look up or
upsert against it (`_id` itself, or another field like `sourceSystemId` in
the worked example). This determines whether an ingestion is an insert, an
upsert, or an insert-only feed that fails on a duplicate — and, same as the
loader-pipeline skill's identity-column question, it has no safe default.

### 4. Embed, flatten, or reference — per nested structure

For every nested object or array on the API side, which of the three applies
on the Mongo side (`mapping-document-format.md` §3c defines them), and why.
Do not default to "embed everything" just because it is Mongo's native shape
— an array with no realistic bound, or a nested object with its own
independent lifecycle, is a reference in waiting, and choosing wrong here is
expensive to unwind once documents exist.

### 5. Null vs. missing, per optional field

For every field that can be absent from the API side: does that map to a
Mongo field that is omitted, or one explicitly set to `null`? Ask this
per-field rather than once for the whole mapping — a codebase is rarely
consistent about it, and if it should be consistent, that is itself a
decision worth recording.

### 6. Every polymorphic or unresolved field

Every field the readers flagged: an unresolved `$ref`, a `oneOf`/`anyOf` not
collapsed to one shape, a Mongo path observed with more than one non-null
type across samples. Ask what the actual shape is (or which variant is
current); do not pick the first one and move on.

## Sane default available — apply it, but say so

| Question | Default if unstated | Say this |
| --- | --- | --- |
| API version to map | the version already deployed / the spec's `info.version` | State the exact version string in the provenance block — it is what makes drift detectable later. |
| Identifier storage | keep the API's string identifier as a Mongo string, not an `ObjectId` | Simpler and avoids a validity constraint the API doesn't itself enforce; convert only if the collection is queried by `ObjectId` elsewhere. |
| Date storage | parse to BSON `Date` | Loses the original string's timezone offset unless also stored; flag if the API sends non-UTC offsets and the offset matters downstream. |
| Enum enforcement | mirror the API's enum values into a Mongo-side `enum` if a validator exists, otherwise store as an unconstrained string | An unenforced enum means a future API value silently gets stored even if this document doesn't yet account for it. |
| Sample count for inference | 5 minimum before treating a sample-inferred field as anything but a hypothesis | Matches `json_sample_schema_reader.py`'s `MIN_CONFIDENT_SAMPLES`; ask for more samples rather than proceeding on fewer. |
| Extra API fields with no obvious Mongo use | list them under "API fields not persisted" rather than omitting them | Keeps the decision visible and reversible, same reasoning as the loader-pipeline skill's excluded-fields list. |

## Also ask, when relevant

- **Is this a one-time backfill, an ongoing sync, or both?** Changes whether
  the mapping needs to account for values the API only returns on the very
  first call (a "created" timestamp) versus every call.
- **Does an existing implementation already do this mapping** — in this
  repository or elsewhere — that the document should describe rather than
  design fresh? A working ingestion path is the most reliable source for any
  question this list raises, the same way an existing COBOL/SSIS job is for
  the loader-pipeline skill.
- **Multiple writers to the same collection?** If more than one code path or
  service writes to the target collection, the schema-defining code found in
  this repository may not be the whole story — see `mongo-discovery.md`'s
  caution on this.
- **Rate limits, pagination, or partial responses on the API side** that
  would mean a single logical record arrives across more than one call —
  affects whether the mapping is really one-endpoint-to-one-document.

## Stop conditions

Do not proceed on a guess. Stop and ask when:

- An external API has no spec, no Postman collection, and no sample
  payloads offered.
- A `$ref` does not resolve, or a `oneOf`/`anyOf` was not collapsed to one
  shape.
- A Mongo path was observed as more than one non-null type across samples.
- Fewer than 5 sample documents are available for an inferred schema and the
  document is about to state anything as more than a hypothesis.
- The embed/flatten/reference choice for a nested structure has not been
  confirmed.
- Two sources for the same side disagree (a committed OpenAPI file and the
  handler code, or a `$jsonSchema` validator and the sample documents pulled
  from the collection).
- A field's null-vs-missing treatment has not been confirmed and the field is
  optional on the API side.

## How to ask

Use `AskUserQuestion` with grouped, concrete options where the choice is
genuinely bounded (embed/flatten/reference, insert vs. upsert, null vs.
missing). For the unbounded ones — what an "as designed" endpoint is actually
supposed to persist, the real shape behind an unresolved `$ref` — ask plainly
and wait.

Record every answer in the mapping document with **who** answered and
**when**, the same discipline `mapping-driven-loader-pipeline` applies. Six
months later, "the embed choice was confirmed with the API's owner on
2026-09-16" is traceable; an unattributed decision is folklore.
