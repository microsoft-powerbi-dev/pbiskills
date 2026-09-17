# Devin knowledge: API-to-MongoDB mapping documents

## Trigger

Apply this knowledge whenever an API's fields need to be documented against a
MongoDB target, field by field: producing a reviewable mapping document that
says where each API value lands in Mongo, what changes shape on the way, and
what nobody has confirmed yet. This covers both an API defined in this
repository (a route/controller and its request/response models) and an
external API whose contract has to be handed over as a spec or sample
payloads. It produces **one artifact only** — a Markdown document — never
integration code. The full skill, with both readers and the reference
material, is at `skills/claude-skills/api-mongodb-mapping/SKILL.md`; setup and
usage notes are in that folder's `DEVIN-USAGE.md`.

## Content

Work in a fixed order: establish scope (which operation(s), which
collection(s), and whether each API is internal or external), discover the
API side, discover the MongoDB side, ask the clarifying questions, then write
the document. Do not write anything before both sides have been discovered
and the gate questions answered — the document exists specifically to stop a
guessed translation from becoming the record.

**The API side has two very different discovery paths, and picking the wrong
one produces a document with a false sense of authority.** For an API defined
in this repository, scan for it: a committed, generated OpenAPI/Swagger file
beats hand-scanning code, and if one exists, parse it with
`scripts/openapi_reader.py` exactly as an external spec would be. Failing
that, typed request/response models — Pydantic in FastAPI, DTOs with
`class-validator` in NestJS, `@RequestBody` classes in Spring, ASP.NET Core
action signatures — are themselves the contract the framework enforces, and
reading them is as authoritative as reading a spec. Untyped handler code
(`req.body.foo`, `request.json["foo"]`) is the weakest signal available and
every field found this way is flagged as inferred, not asserted. For an
external API, never invent a field for a source you cannot read: ask for an
OpenAPI file, a Postman collection exported to OpenAPI, or sample payloads
plus whatever documentation exists, in that order of preference, and stop
outright if none of these can be produced.

Parse a spec deterministically:
`scripts/openapi_reader.py read --spec <path>`. It resolves same-document
`$ref` pointers only — it makes no network call and fetches no external file
— and breaks a `$ref` cycle rather than recursing forever, recording both as
issues. It merges `allOf` (first-writer-wins on a conflicting property,
flagged). It **never** collapses a `oneOf`/`anyOf` to one branch: every
variant is recorded and the field is marked polymorphic, because picking one
silently is exactly the guess this exists to prevent. Read the digest it
produces, not the spec file, the same discipline
`mapping-driven-loader-pipeline`'s reader established for spreadsheets.

The MongoDB side has three discovery tiers, most to least authoritative, and
the tier used has to be stated in the document because it changes how much a
reader should trust the result. A `$jsonSchema` validator (found by searching
for `$jsonSchema`, `createCollection`, or `collMod`) is enforced by Mongo
itself on every write and is the closest thing to a real schema Mongo has —
these are usually short enough to read and quote directly rather than script.
Schema-defining application code (a Mongoose `Schema`, a Beanie `Document`
subclass, a Spring Data `@Document` class, a MongoDB C# driver POCO) shapes
writes but Mongo does not enforce it, and it may not be the only writer to
the collection — ask before treating it as complete. Failing both, infer from
representative sample documents with
`scripts/json_sample_schema_reader.py read --samples <files>`: it reports,
per JSON path, every type observed, the fraction of samples the field was
present in, the fraction of *present* occurrences that were null (missing and
null are different states in Mongo and routinely conflated), and a
best-effort flag for `ObjectId`/`Date` values rendered as 24-hex strings or as
MongoDB Extended JSON (`{"$oid": ...}`, `{"$date": ...}`). Below 5 pooled
sample documents the whole result is marked `confidence: low`, and every
claim built on it must say so in the document rather than presenting an
inference with the same certainty as a validator.

Four things have no safe default and must be asked rather than assumed. What
actually gets persisted for each API operation — its response, its request,
or both — is never inferred from field-name similarity; an update endpoint's
request and response commonly differ in exactly the fields that matter
(server-generated timestamps, computed statuses). The identity/key field on
each side and how they correspond decides insert-versus-upsert semantics. The
embed/flatten/reference choice for every nested object or array must be
stated and justified — do not default to embedding everything just because it
is Mongo's native shape; an unbounded array or a nested structure with its
own independent lifecycle is a reference candidate, and choosing wrong is
expensive to unwind once real documents exist. And null-versus-missing must
be decided per optional field, not once for the whole document, because a
codebase is rarely consistent about it.

Stop and ask, rather than resolving silently, whenever: a `$ref` does not
resolve or a `oneOf`/`anyOf` was not collapsed; a Mongo path was observed with
more than one non-null type across samples; fewer than 5 sample documents
back a field about to be stated as more than a hypothesis; or two sources for
the same side disagree — a committed spec against the handler code that
implements it, or a validator against what the pooled samples actually
contain.

The mapping document itself follows the same emitter idiom as
`sql-server-schema/scripts/skillgen.py`: a list of lines, one `.append()`
each, joined with newlines, no template engine, iterated in a stable field
order so two runs against the same inputs produce byte-identical Markdown.
Required sections: a provenance block naming, per side, whether it is
internal or external / authoritative or sample-inferred and the exact
file(s) and sha256(s) read; a summary including any low-confidence flag from
the sample-inference tier; one section per API-operation-to-collection
pairing covering direction (which of request/response is persisted), the
full field table with type and rule on both sides for every field —
including every "passthrough," since BSON's `Date`, `ObjectId`, and
`Decimal128` types have no exact JSON equivalent and a bare passthrough note
is often wrong in exactly the row it looks safest — nested/array handling
decisions, fields with no counterpart on either side (an API field
deliberately dropped, or a Mongo field that is generated internally rather
than sourced from the API, each with its reason), and explicit type-coercion
notes (string-vs-ObjectId, string-vs-Date, enum enforcement, null-vs-missing);
and an Open Questions section that is always emitted, even empty, because an
empty section is a claim the discovery was complete, not an absence of
anything to say.

This is a document-only skill, not a code generator — that is the deliberate
difference from its sibling `mapping-driven-loader-pipeline`, which also
generates a Python extract pipeline from its mapping document. If the actual
ingestion code implementing this mapping is also wanted, that is a separate,
explicit ask; do not drift into writing it unasked.

An internal API's routes and an internal MongoDB collection's field names
describe how this organization's systems actually work, the same sensitivity
class `mapping-driven-loader-pipeline` treats real mapping spreadsheets with.
Route any generated artifact through
`sql-server-schema/scripts/guardrails.py`'s `resolve_artifact_path()`, write
outside version control by default, and never commit a mapping document built
from a real internal API or a real collection into this repository. The only
committed mapping artifacts are the fictional fixtures and the mapping
document built from them in the skill's own `examples/` directory.
