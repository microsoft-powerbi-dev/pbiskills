# The schema digest

The digest is the seam of this skill. The live server writes one; the generator
reads one and never touches a database. Everything follows from that split:

- A pack can be **reviewed before it is generated**. The digest is the artifact
  a human reads to decide whether the schema is safe to turn into documentation.
- A pack can be regenerated on a machine with **no ODBC driver and no access to
  the server**.
- The generator is **unit-testable against a fixture** rather than a database.
- Two digests of the same database **diff cleanly**, which makes the digest a
  schema-drift detector for free.

A worked example, entirely fictional, lives at
`examples/schema-digest.example.json`. Regenerate it with
`python scripts/cli.py example`.

## Determinism

Every collection is sorted before serialisation, and the JSON is written with
`sort_keys=True`. Two runs against an unchanged database produce byte-identical
output. The only field that varies is `generated_at`, which the CLI lets you
pin with `--generated-at` when you want an exact comparison.

Input order does not matter either:
`tests/test_analyze.py::test_digest_is_deterministic` builds the same digest
from a reversed table list and asserts the results are identical.

## Shape

```jsonc
{
  "digest_schema_version": "1.0",
  "generated_at": "2026-01-01T00:00:00Z",
  "generator": "sqlserver-schema-mcp",

  "source": {
    "server_alias": "demo-sql",          // never the real hostname by default
    "database": "SalesDW",
    "product_version": "15.0.4345.5",
    "edition": "Developer Edition (64-bit)",
    "collation": "SQL_Latin1_General_CP1_CI_AS"
  },

  "coverage": {
    "schemas": ["dbo", "stg"],
    "table_count": 6,
    "relationship_count": 4,
    "declared_relationships": 2,
    "inferred_relationships": 2
  },

  "schemas": [ /* raw sys.schemas rows with object counts */ ],

  "tables": [{
    "signature": "salesdw::dbo.factorder",   // the cross-tool join key
    "schema": "dbo",
    "name": "FactOrder",
    "object_type": "USER_TABLE",
    "row_count": 4000000,
    "size_mb": null,
    "description": null,                      // MS_Description if present
    "role": "fact",                           // see classification below
    "role_reasons": ["name starts with an explicit fact prefix"],
    "tier": 1,
    "score": 4.9312,
    "report_reads": 0,
    "measure_columns": ["PaidAmount", "Quantity"],
    "date_columns": ["OrderDate"],
    "primary_key": {"name": "PK_FactOrder", "columns": ["OrderId"]},
    "columns": [{
      "ordinal": 1, "name": "OrderId",
      "data_type": "int", "type": "int",
      "max_length": 4, "precision": 10, "scale": 0,
      "nullable": false, "is_identity": true,
      "identity_seed": 1, "identity_increment": 1,
      "is_computed": false, "computed_definition": null,
      "default": null, "collation": null, "description": null
    }],
    "indexes": [{
      "name": "IX_FactOrder_Customer", "type": "NONCLUSTERED",
      "is_unique": false, "is_primary_key": false,
      "filter": "([IsDeleted]=(0))",
      "key_columns": ["CustomerId"], "included_columns": ["PaidAmount"]
    }]
  }],

  "relationships": [{
    "from_table": "dbo.FactOrder",
    "to_table": "dbo.DimCustomer",
    "from_columns": ["CustomerId"],
    "to_columns": ["CustomerId"],
    "constraint": "FK_FactOrder_DimCustomer",
    "confidence": "declared",                 // "declared" | "inferred"
    "trusted": true                           // false if created WITH NOCHECK
  }],

  "routines": [ /* procedures, functions and views, names and types only */ ],
  "conventions": { /* see below */ },
  "warnings": []
}
```

## `signature`: the cross-tool join key

`database::schema.table`, lower-cased, schema defaulting to `dbo`. This is
byte-for-byte the string `reportlineage.sql_refs.TableRef.signature()`
produces, which is what lets a lineage scan ("this RDL reads these tables")
join to a digest ("here is what those tables contain") with no mapping layer.
`tests/test_analyze.py` asserts the two agree by importing the real
`TableRef` when `skills/report-lineage/` is present.

## `role` and `role_reasons`

Roles are assigned by first-match rules over name, keys, cardinality, and
column shape. Every table carries the reasons that fired, so a wrong call is
visible and arguable rather than silent.

| Role | Rule |
| --- | --- |
| `staging` | Schema is one of stg/staging/tmp/temp/etl/load/work, or the name has a staging or backup prefix or a date-stamp suffix |
| `audit` | Audit, log, history, or archive naming, with nothing referencing it |
| `fact` | Explicit `fact` prefix, or two or more outgoing foreign keys plus numeric measure columns plus either a date column or a row count well above the median |
| `dimension` | Explicit `dim` prefix, or referenced by two or more tables while referencing at most one, or referenced by something, referencing nothing, and carrying descriptive text columns |
| `bridge` | Composite primary key made entirely of foreign key columns, with at most three other columns |
| `lookup` | Small and narrow, and either referenced by something, named as a lookup or reference table, or carrying a code plus description column pair |
| `operational` | In the join graph but with no clearer role |
| `unknown` | No keys and no name signal |

"Numeric measure column" means a numeric type whose name matches
amount/qty/total/paid/balance/price and which is neither the primary key nor a
foreign key column. That last exclusion matters: a `CustomerId` is numeric but
is not a measure.

**None of this is semantic understanding.** It is name, key, and cardinality
pattern-matching, and it will be wrong on databases that do not follow common
conventions.

## `score` and `tier`

The score is scale-free, so it does not need retuning per database:

```
score = 5.0 * (report_reads   / max_report_reads)
      + 3.0 * (in_degree      / max_in_degree)
      + 2.0 * (out_degree     / max_out_degree)
      + 1.5 * (log10(row_count) / 9)
      + 1.0 * role_weight
      - 2.0 if the table is empty
```

Tier 1 is the top 40 by score (with a floor, so a tiny database does not
promote everything), tier 2 the next 150, tier 3 the rest. Tiering is what
keeps a 3000-table database usable: the pack's `SKILL.md` carries tier 1, the
index carries tiers 1 and 2 in full and tier 3 by name only.

**Report usage overrides the score.** A table that any existing RDL report
reads is forced to at least tier 2. The reports are ground truth about what
matters; the heuristics are a guess.

## `conventions`

```jsonc
{
  "table_case": {"value": "PascalCase", "count": 6, "hit_rate": 1.0},
  "table_names_plural": 0.0,
  "primary_key_style": {"value": "<Table>Id", "count": 4, "hit_rate": 0.667},
  "tables_without_primary_key": {"count": 1, "hit_rate": 0.167},
  "soft_delete_columns": ["dbo.FactOrder.IsDeleted"],
  "scd2_columns": ["dbo.DimCustomer.IsCurrent", "dbo.DimCustomer.EffectiveFrom"],
  "audit_columns": {"ChangedOn": 1},
  "string_types": {"varchar": 6}
}
```

Every statement carries its hit rate, so a reader can calibrate. The two that
change query correctness rather than style are `soft_delete_columns` (a query
omitting the filter silently returns retired rows) and `scd2_columns` (a naive
dimension join fans out across history), and the generator injects both into
its query recipes automatically.

## Validation

`analyze.validate_digest(digest)` returns a list of problems; empty means
usable. It checks the major version matches, `source.database` is present, and
every table has a schema, a name, and a columns array. `skillgen.load_digest`
runs it and refuses to generate from a digest that fails.

A digest whose **major** version differs from the generator's is rejected
rather than best-guessed.

## What a digest deliberately does not contain

- **No data values.** `sample_rows` is not populated.
- **No stored-procedure bodies.** Routines carry names, types, and whether a
  definition is readable. A procedure body is arbitrary text that routinely
  contains hardcoded values, connection strings, and comments naming people, so
  it is fetched on demand through `mssql_get_definition` rather than embedded.
- **No real server hostname.** `source.server_alias` is what travels.
