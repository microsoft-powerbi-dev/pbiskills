# The API-to-MongoDB mapping document

The reviewable deliverable this skill produces. There is no second deliverable
— unlike `mapping-driven-loader-pipeline`, this skill does not generate
integration code. The document is the whole job, and it has to stand on its
own for a reviewer who will never read the API's source code or run a Mongo
query.

It exists because the two sides almost never describe themselves the same
way: an API's contract says `acceptingNewPatients: boolean, nullable`, and the
same concept in Mongo is `isAcceptingPatients` with no way to tell "missing"
from "explicitly false" apart from reading the write path. That translation
is exactly what disappears from institutional memory first, and this document
is where it gets written down before it does.

## Emitter idiom

Same as `sql-server-schema/scripts/skillgen.py` and
`mapping-driven-loader-pipeline`: build `lines: List[str]`, one `.append()`
per Markdown line, `"\n".join(lines)`, no template engine. Iterate endpoints
and fields in a stable order (route, then method, then field path) so the same
two digests always produce byte-identical Markdown — that is what makes a
later re-scan diff cleanly against this version.

## It is very often internal-system data

An internal API's field names, an internal MongoDB collection's field names,
and the endpoints and hosts involved describe how this organization's systems
actually work. That is different from `mapping-driven-loader-pipeline`'s
concern (which is about *customer* data) but the handling is the same:

- Route the destination through
  `sql-server-schema/scripts/guardrails.py`'s `resolve_artifact_path()`.
- Write outside version control by default when either side is an internal,
  non-public API or an internal Mongo deployment.
- Emit the do-not-commit header (below) unless every field in the document —
  API side and Mongo side — is provably public (a published, external vendor
  API mapped only to fictional or already-public sample shapes).
- Never commit a document built from a real internal API or a real
  collection into this repository. The only committed example is
  `examples/sample-api-mongodb-mapping.md`, built entirely from the fictional
  fixtures in this skill's `examples/` directory.

## Required sections, in order

### 1. Front matter and provenance

```markdown
---
generated_by: api-mongodb-mapping
generator_version: "1.0"
generated: 2026-09-16
do_not_commit: true
api_side:
  kind: internal            # internal | external
  discovery: repo-scan       # repo-scan | committed-openapi-spec | handed-spec | sample-payloads
  source: backend/src/routes/providers.ts   # or a spec file path, or "n/a - see samples"
  source_sha256: 8a1c4e...   # of the spec file, or of the scanned route file(s) — list all if more than one
mongo_side:
  kind: internal
  discovery: schema-validator   # schema-validator | model-code | sample-documents
  source: db/validators/providers.jsonschema.json
  source_sha256: 2f90ab...
digest_schema_version: "1.0"
---

> **Do not commit.** This file names an internal API route, an internal
> MongoDB collection, or both. It is written outside version control by
> design.
```

Two provenance blocks, one per side, because the two sides are frequently
discovered by completely different means (a scanned controller file versus a
`$jsonSchema` validator versus three sample documents pasted into a session),
and the reviewer needs to know which claims in this document are read from an
authoritative source and which are inferred from examples.

### 2. Summary

- How many endpoints (or, for the Mongo side, collections) are covered.
- Field counts per side, and how many fields on each side have **no
  counterpart** on the other (see §3d).
- The confidence level carried up from `json_sample_schema_reader.py` when
  either side was sample-inferred — **`low` confidence must appear here**,
  not just buried in a per-field note.
- Issues surfaced by whichever reader(s) ran: unresolved `$ref`s, `oneOf`/
  `anyOf` fields that were not collapsed, polymorphic Mongo paths, fields
  present in only some samples.
- Whether the API side was discovered by scanning this repository's source or
  handed over as a spec/sample for an external API — a reviewer treats a
  repo-scanned mapping and an external-API mapping with different scrutiny,
  and should not have to infer which this is from context.

### 3. One section per endpoint-to-collection pairing

Most mappings pair one API operation (`GET /providers/{id}` or
`POST /providers`) with one Mongo collection. A mapping that fans one
operation out to several collections, or folds several operations into one
collection, still gets one section per **pairing**, not per operation — the
field table is what a reader actually needs aligned side by side.

**3a. Direction and what actually gets persisted**

State plainly, because it is never obvious from the API spec alone: does
calling this operation produce data that gets written to Mongo (its
*response* fields are the source), does the call's own payload get persisted
as sent (its *request* fields are the source), or both (e.g., an upsert
endpoint whose request is stored and whose response, containing generated
identifiers, is stored back onto the same document)? Get this from the person
who owns the ingestion code or ask for it — do not infer it from field-name
similarity alone.

**3b. Field mapping table**

```markdown
| API field | Type | Req. | Source | Mongo field | BSON type | Req. | Rule / note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `externalProviderId` | string | Y | response | `sourceSystemId` | string | Y | passthrough |
| `npi` | string | Y | response | `npi` | string | Y | passthrough |
| `acceptingNewPatients` | boolean, nullable | N | response | `isAcceptingPatients` | bool/null | N | passthrough; API `null` and API-field-absent are **both** written as Mongo `null`, never as field-absent — confirm this matches the read path's expectations |
| `specialties[].code` | string (enum) | N | response | `specialties[].code` | string | N | passthrough; enum values not in the API's declared set are rejected at write time (see Open Questions) |
| `lastVerifiedAt` | string, `date-time` | N | response | `lastSyncedAt` | date | N | parse ISO 8601 → BSON `Date`; **name differs** — verify this is the same event, not two different timestamps |
```

Every row needs the type on both sides and the transformation, even when it
is "passthrough" — a silent passthrough is still a claim that no conversion
is needed, and BSON's `Date`, `ObjectId`, and `Decimal128` types have no exact
JSON equivalent, so "passthrough" is frequently wrong for exactly the fields
where it looks safest.

**3c. Nested and array handling**

For every object or array path, say which of these applies and why:
**embed** (the nested shape is stored inline, as here), **flatten** (nested
fields are hoisted to top-level Mongo fields, e.g. `primaryAddress.city` →
`city`), or **reference** (the nested shape becomes a separate collection with
this document holding an id). Mongo's native fit is embedding; reach for
reference only when the nested data has its own lifecycle (created, updated,
or deleted independently of the parent) or is large/unbounded (an array with
no realistic upper bound is a reference, not an embed — an unbounded embedded
array is the most common cause of a document exceeding Mongo's 16 MB limit).

**3d. Fields with no counterpart**

Two tables, both required, both allowed to be empty (state so explicitly):

- **API fields not persisted** — present in the contract but deliberately
  dropped (with the stated reason: not needed, sensitive, redundant with
  another field).
- **Mongo fields with no API source** — `_id`, `createdAt`, `lastSyncedAt`,
  and any field this system computes or generates itself rather than reading
  from the API. State what populates each one (a driver default, application
  code, a separate process) so a reader never mistakes a generated field for
  a missed mapping.

**3e. Type coercion notes**

Called out explicitly, one entry each, whenever they apply to this pairing:

- **String ⇄ `ObjectId`.** Whether the API's identifier string is stored as a
  Mongo `ObjectId` (requires it to be a valid 24-hex value, or a stated
  translation) or kept as a plain string (simpler, but loses `ObjectId`
  query/index behavior).
- **String ⇄ `Date`.** The exact format parsed (ISO 8601 offset vs UTC vs
  date-only) and what happens on a value that doesn't parse.
- **Enum ⇄ string.** Whether Mongo enforces the enum (a `$jsonSchema`
  `enum`) or stores any string a future API version might send.
- **Number precision.** Whether a monetary or high-precision field is stored
  as BSON `double` (binary floating point — a known source of drift for
  money) or `Decimal128`.
- **Null vs missing.** For every optional field: does "the API didn't return
  this" map to a Mongo field that is absent, or one that is explicitly `null`?
  These are different states in Mongo (`{$exists: false}` vs
  `{$eq: null}` both match a query for `null` unless queried carefully) and
  routinely mismatched — state the choice per field, not once for the whole
  document.

### 4. Open questions

Every unresolved item, numbered: which field it's about, what raised it
(an unresolved `$ref`, an `oneOf` not collapsed, a polymorphic sample-inferred
path, a name that doesn't obviously correspond across the two sides, a
missing confirmation of the embed/flatten/reference choice), and who it is
with.

```markdown
| # | Field | Raised by | Question | Blocks |
| --- | --- | --- | --- | --- |
| 1 | `Provider.specialties` (`oneOf`) | reader: unresolved oneOf | The spec allows a `Specialty` object or a bare code string here. Which does this API version actually send? | the field mapping row for `specialties[]` |
| 2 | `priority` (sample-inferred) | reader: polymorphic type | Seen as both integer and string across 3 samples. Is this a data-quality issue in the samples or a genuine union type? | the BSON type choice for `priority` |
```

An empty Open Questions section is a claim that both sides were fully and
unambiguously discovered. Emit the section either way.

### 5. What this document does not tell you

Always emitted:

- It reflects the API contract and Mongo schema at the recorded provenance
  (spec file + sha256, or scanned repo file + sha256, or sample-document set +
  sha256s) and nothing later. An API version bump or a schema change on
  either side makes this document stale — re-run discovery and diff.
- Where either side was sample-inferred, the field list is a hypothesis about
  the shape, not a guarantee — see the confidence level in §2 and
  `json_sample_schema_reader.py`'s own caveats.
- It does not verify that the mapping is actually implemented anywhere in
  code, only that the two contracts are compatible on paper. Use it as the
  spec for that code, not as evidence the code exists.
- It does not cover authentication, rate limits, pagination, or error-response
  shapes unless a field mapping specifically depends on one of those (a
  paginated list endpoint's cursor field, for instance).
