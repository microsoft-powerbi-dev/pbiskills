# Playbook: analysing an on-premises SQL Server database

A condensed view of `skills/claude-skills/sql-server-schema/`. The full detail,
including the runnable MCP server and the nine reference documents, lives
there; this is the version you can hand to any tool that reads markdown.

## Scope

Connecting to a SQL Server you did not design, using the Windows credentials
you already have, and working out what is in it: tables, columns, keys,
indexes, relationships, and which of it actually matters. Read-only throughout.

Out of scope: moving or modifying data, and anything Fabric-hosted (that has
its own control plane, its own CLI, and Entra authentication rather than
Windows).

## The five rules

1. **Read-only, structurally.** No write path at all, rather than a write path
   that is switched off. Open connections with autocommit off and roll back
   unconditionally: statement parsing is a heuristic, a transaction that never
   commits is not.
2. **Breadth before depth.** Databases, then schemas, then a table list with
   row counts, then columns only for the tables that scored. A production
   database can exceed three thousand tables.
3. **`sys.*`, never `INFORMATION_SCHEMA`.** The ANSI views cannot express
   identity or computed columns, included or filtered index columns, or an
   untrusted foreign key, and they truncate routine definitions at 4000
   characters.
4. **Row counts from `sys.dm_db_partition_stats`**, filtered to
   `index_id IN (0,1)`. Never `COUNT(*)` on a table of unknown size. The result
   is approximate; report it as such.
5. **A missing foreign key is not a missing relationship.** Infer joins from
   naming, label every inference, and verify before trusting a number built on
   one.

## Which catalog view answers which question

| Question | Source |
| --- | --- |
| Which databases can I open? | `sys.databases`, `HAS_DBACCESS(name) = 1` |
| What tables, and how big? | `sys.objects` + `sys.dm_db_partition_stats` |
| What columns and types? | `sys.columns` + `sys.types` on `user_type_id` |
| Defaults, identity, computed? | `sys.default_constraints`, `sys.identity_columns`, `sys.computed_columns` |
| Primary key? | `sys.key_constraints` + `sys.index_columns` |
| How do these join? | `sys.foreign_keys` + `sys.foreign_key_columns` |
| What access path was intended? | `sys.indexes` + `sys.index_columns` |
| What does this procedure read? | `sys.sql_modules.definition` |
| Documented anywhere? | `sys.extended_properties`, `MS_Description` |

## Connecting

`DRIVER={<highest installed>};SERVER=<host>;DATABASE=<db>;Trusted_Connection=yes`

- Enumerate drivers rather than hardcoding one. `Integrated Security=SSPI` is
  ADO.NET syntax and is ignored by ODBC.
- **Driver 18 trap**: it defaults to `Encrypt=yes` with certificate validation,
  which fails against a self-signed certificate. Emit
  `Encrypt=yes;TrustServerCertificate=yes` on 18+; 17 and older need nothing.
- Named instance is `HOST\INSTANCE` and needs SQL Browser on UDP 1434; if
  blocked, use `HOST,PORT` (comma, not colon). Never both.
- Confirm auth actually happened: `auth_scheme` from `sys.dm_exec_connections`
  should read `KERBEROS` or `NTLM`. Prove it at the driver level first with
  `sqlcmd -S <server> -E -Q "SELECT SUSER_SNAME()"`.

## Reading the catalog without getting it wrong

- `max_length` is in **bytes**: `nvarchar(50)` reports 100, and `-1` means MAX.
- Foreign keys come back **one row per column**; group by constraint and order
  by `constraint_column_id` or a composite join clause is wrong.
- `is_not_trusted` means the constraint was created `WITH NOCHECK` and does not
  hold for existing rows.
- Resolve a name to `object_id` with `OBJECT_ID(?)` first, then filter on the
  integer. Never interpolate a caller-supplied identifier.
- **Permission trimming**: SQL Server hides objects rather than refusing them.
  An empty result means "nothing you can see". Missing `VIEW DATABASE STATE`
  zeroes every row count; missing `VIEW DEFINITION` empties every procedure
  body. Report these as partial, with the permission named.

## Classification in one page

Roles, first match wins: **staging** (stg/tmp/etl schema or prefix, or a
backup or date-stamped suffix), **audit** (audit/log/history naming with
nothing referencing it), **fact** (explicit prefix, or two or more outgoing
foreign keys plus numeric measure columns plus a date column or a large row
count), **dimension** (explicit prefix, or referenced by several tables while
referencing few), **bridge** (composite key made entirely of foreign keys),
**lookup** (small, narrow, referenced or named as one), **operational**,
**unknown**.

A "measure" is a numeric column whose name reads like an amount, quantity or
total, and which is neither the primary key nor a foreign key. `CustomerId` is
numeric and is not a measure.

Rank by report usage first, then inbound and outbound degree, then row count,
then role. Anything an existing report reads outranks the heuristics: the
reports are ground truth about what matters.

## Two conventions that change correctness

- **Soft delete** (`IsDeleted`, `IsActive`, `DeletedDate`, `RecordStatus`): a
  query omitting the filter silently returns retired rows.
- **Type-2 dimensions** (`EffectiveFrom`, `EffectiveTo`, `IsCurrent`): a naive
  join fans facts out across every historical version.

Detect both before writing a WHERE clause, and include them in any query you
hand over.

## Safety: what never leaves the machine

Never return values from a column named for an identifier or a health
attribute: ssn, tax id, passport, driver's licence, card number, cvv, iban,
account number, date of birth, patient, mrn, npi, diagnosis, icd, prescription,
password, token. Show the column name, marked do-not-select, because an agent
unaware of it will write `SELECT *`; never read its contents. This is a safety
net, not a compliance control.

Anything generated from a real schema names internal systems. Keep it out of
version control, and say what it contains before sharing it.

## Full detail

`skills/claude-skills/sql-server-schema/SKILL.md`, and its `references/`
folder: connection and auth, catalog views, read-only guardrails, the digest
format, the pack format, PII and public-repo safety, the RDL-to-tables bridge,
existing tools and alternatives, and MCP client setup.
