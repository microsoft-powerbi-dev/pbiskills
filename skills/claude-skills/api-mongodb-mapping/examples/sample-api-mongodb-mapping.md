---
generated_by: api-mongodb-mapping
generator_version: "1.0"
generated: 2026-09-16
do_not_commit: false
api_side:
  kind: external
  discovery: handed-spec
  source: examples/sample-external-provider-api.openapi.yaml
  source_sha256: (recompute with `openapi_reader.py read` — regenerated fixture, not pinned here)
mongo_side:
  kind: internal
  discovery: schema-validator
  source: examples/sample-providers-collection.jsonschema.json
  source_sha256: (recompute with the validator's own file hash)
digest_schema_version: "1.0"
---

> This is the fictional worked example for the `api-mongodb-mapping` skill.
> No real vendor, endpoint, or collection is described. `do_not_commit` is
> `false` here only because every value below is invented for this example —
> a real mapping built from an internal API or a real collection must set it
> `true` and be written outside version control, per
> `references/mapping-document-format.md`.

# Sample External Provider Directory API → `providers` collection

## Summary

- 2 operations covered: `POST /providers` (create) and
  `GET /providers/{providerId}` (fetch), both mapped to one Mongo collection,
  `providers`.
- API side: 13 request fields, 12 response fields (`requestedByUserId` is
  request-only). Discovered from a handed-over OpenAPI 3.0.3 spec — see
  `examples/sample-external-provider-api.openapi.yaml`; parsed with
  `scripts/openapi_reader.py`, 0 errors, 0 warnings.
- Mongo side: 10 declared fields plus `_id`. Discovered from a committed
  `$jsonSchema` validator — see
  `examples/sample-providers-collection.jsonschema.json`. Authoritative: Mongo
  enforces this validator on every write (assume `validationLevel: strict`
  unless told otherwise — the fixture does not state one).
- Confidence: **high** on both sides — a formal spec and a formal validator,
  not a sample-document inference.
- Open questions: 1 (see §4).

## 1. Direction and what actually gets persisted

`POST /providers`: the **response** is persisted, not the request. The
request supplies the data to register a provider; the response is the
system's canonical record of what was actually stored, including any
server-assigned fields — treat the response as the source of truth for this
mapping. (This is stated for the example, not derived from the spec — a real
mapping should confirm this with whoever owns the ingestion code, per
`clarifying-questions.md` item 2.)

`GET /providers/{providerId}`: the response is read into the mapping the same
way, for the fields that are refreshed on each fetch (`status`,
`acceptingNewPatients`, `lastVerifiedAt`).

## 2. Field mapping table

| API field | Type | Req. | Source | Mongo field | BSON type | Req. | Rule / note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `externalProviderId` | string | Y | response | `sourceSystemId` | string | Y | passthrough |
| `npi` | string | Y | response | `npi` | string | Y | passthrough |
| `name` | string | Y | response | `displayName` | string | Y | passthrough; **name differs** — confirmed with API owner this is the same concept |
| `status` | string (enum: `ACTIVE`/`INACTIVE`/`PENDING`) | Y | response | `status` | string (enum: adds `UNKNOWN`) | Y | passthrough; Mongo's enum is a superset — `UNKNOWN` is set internally when a sync fails, never sent by the API (see §3, Mongo fields with no API source) |
| `acceptingNewPatients` | boolean, nullable | N | response | `isAcceptingPatients` | bool/null | N | passthrough; API `null` **and** API-field-absent both write Mongo `null` — confirmed, not inferred (§3e) |
| `specialties[].code` | string (enum: `PCP`/`CARD`/`ORTHO`/`DERM`) | N | response | `specialties[].code` | string | N | passthrough; Mongo does not itself enforce this enum (validator has no `enum` on this sub-field) — see Open Questions |
| `specialties[].displayName` | string | N | response | `specialties[].label` | string | N | passthrough; **name differs** (`displayName` → `label`) |
| `primaryAddress.line1` | string | Y (within object) | response | `address.line1` | string | N | passthrough; **required on the API side, not required by the Mongo validator** — flagged, not silently resolved |
| `primaryAddress.city` | string | Y (within object) | response | `address.city` | string | N | passthrough; same requiredness mismatch as above |
| `primaryAddress.state` | string | N | response | `address.region` | string | N | passthrough; **name differs** (`state` → `region`, chosen to also fit non-US addresses) |
| `primaryAddress.postalCode` | string (`format: zip`) | N | response | `address.postalCode` | string | N | passthrough |
| `lastVerifiedAt` | string (`format: date-time`) | N | response | `lastSyncedAt` | date | N | parse ISO 8601 → BSON `Date`; **name differs** — confirmed this is the verification timestamp, not a separate sync event |

## 3. Nested and array handling

- **`specialties[]`** — **embed**. Bounded in practice (a handful of
  specialty codes per provider), no independent lifecycle apart from the
  provider record, and the validator embeds it the same way. Not a
  reference candidate.
- **`primaryAddress` / `address`** — **embed**. Single nested object, no
  independent lifecycle.

## Fields with no counterpart

**API fields not persisted:**

| Field | Reason |
| --- | --- |
| `requestedByUserId` (request-only) | Audit-only at the point of the create call; not part of the provider's own record. If an audit trail is needed, it belongs in a separate log collection, not on the provider document. |

**Mongo fields with no API source:**

| Field | Populated by |
| --- | --- |
| `_id` | Mongo driver default `ObjectId` on insert. |
| `status: "UNKNOWN"` | Set by the sync process itself when a refresh call to the API fails, never sent by the API — this is why the Mongo enum is a superset of the API's. |
| `createdAt` | Application code, set once at insert time; distinct from `lastSyncedAt`, which is refreshed on every successful sync. |

## Type coercion notes

- **String ⇄ `ObjectId`**: `sourceSystemId` is kept as a plain string, not
  converted to Mongo `ObjectId` — it is the *external* system's identifier
  format, which is not guaranteed to be a 24-hex value.
- **String ⇄ `Date`**: `lastVerifiedAt` (ISO 8601, `format: date-time`) parses
  to BSON `Date`. Confirmed with the API owner that the API always sends a
  UTC value (`Z` suffix); no offset-preservation is needed.
- **Enum ⇄ string**: `status` is enforced both sides, with Mongo's superset
  noted above. `specialties[].code` is enforced on the API side only — see
  Open Questions.
- **Null vs. missing**: `acceptingNewPatients` absent on the API response and
  `acceptingNewPatients: null` both write Mongo `null` (not a missing field).
  Confirmed with the API owner: the API only omits this field for providers
  registered before the field was added, which is operationally identical to
  "unknown," the same meaning as an explicit `null`.

## 4. Open questions

| # | Field | Raised by | Question | Blocks |
| --- | --- | --- | --- | --- |
| 1 | `specialties[].code` | field mapping row | The API enforces this as one of 4 codes; the Mongo validator does not constrain it at all. Should the validator be tightened to match, or is the extra laxity intentional (e.g. to tolerate a future API value before the validator is updated)? | whether this row ships as-is or the validator changes first |

## 5. What this document does not tell you

- It reflects the spec at `examples/sample-external-provider-api.openapi.yaml`
  and the validator at `examples/sample-providers-collection.jsonschema.json`
  as committed in this skill's `examples/` directory — both fixtures, not a
  live system. Re-run discovery against the real spec and the real validator
  before relying on this shape for anything but learning the document format.
  A real mapping would record each file's `source_sha256` here so drift is
  detectable later.
- It does not verify that any ingestion code implementing this mapping
  actually exists or matches it.
- It does not cover authentication, pagination, or the `404`/error response
  shapes for either operation.
