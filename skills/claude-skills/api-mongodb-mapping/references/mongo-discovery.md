# Discovering the MongoDB side

MongoDB enforces no schema unless a collection has been given a validator, so
"read the schema" means different things depending on what this repository
actually has. Prefer the most authoritative source available, in this order,
and be explicit in the mapping document about which one was used — that
choice is the single biggest driver of how much to trust the result.

## 1. A `$jsonSchema` validator (most authoritative)

If the collection was created or altered with a validator
(`db.createCollection(name, {validator: {$jsonSchema: {...}}})` or
`collMod`), Mongo itself enforces it on every write — this is the closest
thing to a real schema Mongo has. Find it by searching the repository for
`$jsonSchema`, `createCollection`, or `collMod`, or by asking whoever owns the
database to export the current validator with
`db.getCollectionInfos({name: "<collection>"})`.

These documents are usually small enough to read directly rather than run
through a script: quote the relevant `bsonType`, `required`, and `enum`
entries straight into the mapping document's field table. If one is large and
deeply nested, the same flattening logic `scripts/openapi_reader.py` applies
to OpenAPI schemas carries over almost directly — `bsonType` stands in for
`type`, `properties`/`items`/`required` mean the same thing, and `$ref` is
rare in Mongo validators, so a manual walk is usually faster than adapting the
script. Say explicitly which is done.

A validator only constrains what can be *written* going forward — it does not
guarantee every existing document already conforms, especially if it was
added after the collection already held data, or if `validationLevel` is
`"moderate"` (only re-validates on update, not on existing documents) or
`validationAction` is `"warn"` (logs rather than rejects). State the
`validationLevel`/`validationAction` in the document if you can find them;
they change how much to trust that every document matches.

## 2. Schema-defining application code

Absent a validator, look for the object-modeling layer the application code
itself uses — these classes are what actually shapes what gets written, even
though Mongo won't enforce them:

| Stack | Search for |
| --- | --- |
| Mongoose (Node) | `new Schema(\{`, `mongoose.model(` |
| Node + Zod/Joi in front of a raw driver write | the validation schema passed to `.parse(`/`.validate(` right before an `insertOne`/`updateOne` call |
| Python + Beanie | `class \w+\(Document\)` |
| Python + raw PyMongo + Pydantic | a Pydantic model passed to `.dict()`/`.model_dump()` immediately before a `.insert_one(`/`.insert_many(` call |
| Java/Kotlin + Spring Data MongoDB | `@Document(collection = `, `@Field(`, `@Id` |
| C# + MongoDB.Driver | POCO classes with `[BsonId]`/`[BsonElement]`, or plain POCOs relied on by convention (check the `IMongoCollection<T>`'s `T`) |

Read the class definition the same way `openapi_reader.py` reads a schema:
every property, its declared type, whether it is optional (a nullable type, a
`?`, an `Optional[...]`, or simply not required by the ORM's own validation),
and any default. State which class was read and where, the same way the API
side states which spec file or route was read.

**Caution specific to this path:** application-level typing does not stop a
document with an extra field, a missing field, or a wrong type from existing
in the collection already — it only constrains *new* writes made through this
code path. If more than one service writes to the collection, a class found
in this repository may not be the only thing shaping it. Ask whether this is
the only writer before treating the class as complete.

## 3. Sample documents (least authoritative — the explicit fallback)

When neither of the above exists, the only honest source is a set of real or
representative documents. Get several — `json_sample_schema_reader.py` marks
the whole result `confidence: low` below 5 documents, and a single example is
a hypothesis, not a schema. Acceptable sources:

- A `mongoexport` dump of a handful of representative documents (strip
  anything sensitive before this leaves a production system, per
  [Handling real data](#handling-real-data) below).
- A `.find().limit(n)` result the user pastes in or attaches.
- Documents already committed in this repository as fixtures or seed data —
  check for these first; they may already exist for tests.

Run:

```bash
python skills/claude-skills/api-mongodb-mapping/scripts/json_sample_schema_reader.py \
    read --samples <path1.json> [<path2.json> ...] --out mongo_digest.json
```

Read the digest, not the raw files. It records, per field path: every JSON
type observed, the fraction of samples the field was present in, the fraction
of *present* occurrences that were `null`, and a best-effort flag for
`ObjectId`/`Date` shapes rendered as plain strings or MongoDB Extended JSON
(`{"$oid": ...}`, `{"$date": ...}`). None of this is asserted as fact in the
mapping document without saying it came from N sample documents at this
confidence level.

**Missing vs. `null` is the recurring trap here.** A field absent from one
sample and `null` in another look similar at a glance but mean different
things to a query (`{field: {$exists: false}}` versus `{field: null}` — the
latter actually matches both `null` and missing, which is its own frequent
source of confusion). The reader reports both ratios separately for exactly
this reason; do not collapse them into one "sometimes absent" note.

## Either way

- **State the collection name and, if relevant, the database.** A mapping
  document with no named target is not reviewable.
- **Identify the key field(s)** — usually `_id`, but confirm whether the
  natural key used to look up or upsert a document is `_id` itself or another
  field the API's identifier maps onto (`sourceSystemId` in the worked
  example). This decides whether an ingestion is an insert, an upsert keyed
  on that field, or something else — ask if it is not obvious.
- **A collection with no schema-defining code and no validator is not a
  failure of discovery** — it is information. Say so plainly in the
  provenance block rather than presenting an inferred shape with the same
  confidence as a validator would carry.

## Handling real data

Sample documents pulled from a real collection are, by definition, real data.
Apply the same discipline `mapping-driven-loader-pipeline` applies to a real
mapping spreadsheet:

- Route any artifact through `sql-server-schema/scripts/guardrails.py`'s
  `resolve_artifact_path()` and write outside version control by default.
  Never commit a real sample document, digest, or mapping document into this
  repository.
- If a sample document carries values that are PII or otherwise sensitive
  (member/patient identifiers, contact details, anything covered by this
  organization's data-handling policy), read field *definitions* from it —
  never carry a live value into the mapping document. Replace any example
  value shown in the document with an obviously fictional placeholder.
- The only sample documents safe to commit are the fictional fixtures in this
  skill's own `examples/` directory.
