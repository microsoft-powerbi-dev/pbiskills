# The catalog views, and which question each one answers

## Use `sys.*`, not `INFORMATION_SCHEMA.*`

`INFORMATION_SCHEMA` is an ANSI compatibility layer. It cannot express:

- identity columns (seed, increment)
- computed columns and their definitions
- included columns on an index, or a filtered index's predicate
- whether a foreign key is disabled or untrusted
- partitioning, temporal tables, or change tracking

and `INFORMATION_SCHEMA.ROUTINES.ROUTINE_DEFINITION` **truncates at 4000
characters**, which quietly corrupts any stored procedure long enough to
matter. `sys.*` is the superset. This skill uses `INFORMATION_SCHEMA` nowhere.

## Which view answers which question

| Question | Source |
| --- | --- |
| What databases can I actually open? | `sys.databases` filtered by `HAS_DBACCESS(name) = 1` and `state = 0` |
| How big is each database? | `sys.master_files`, summing `size * 8 / 1024` for MB |
| What schemas exist? | `sys.schemas`, excluding `sys`, `INFORMATION_SCHEMA`, and the fixed database-role schemas |
| What tables and views exist? | `sys.objects` where `type IN ('U','V')`, joined to `sys.schemas` |
| How many rows, and how big? | `sys.dm_db_partition_stats`, `index_id IN (0,1)` |
| What columns, with what types? | `sys.columns` joined to `sys.types` on `user_type_id` |
| What is the default on this column? | `sys.default_constraints` on `c.default_object_id` |
| Is it an identity, and seeded how? | `sys.identity_columns` |
| Is it computed, and from what? | `sys.computed_columns` |
| Is there a documented description? | `sys.extended_properties` where `name = 'MS_Description'` |
| What is the primary key? | `sys.key_constraints` joined to `sys.index_columns` on `unique_index_id` |
| What foreign keys, in both directions? | `sys.foreign_keys` and `sys.foreign_key_columns` |
| What indexes, with which columns? | `sys.indexes` and `sys.index_columns` |
| What is the body of this procedure? | `sys.sql_modules.definition` |
| What parameters does it take? | `sys.parameters` joined to `sys.types` |

## Details that are easy to get wrong

**Row counts.** Use `sys.dm_db_partition_stats` with `index_id IN (0,1)` (the
heap or the clustered index; other index ids would multiply-count the same
rows). Never `COUNT(*)`, which is a full scan on a production table. Never
`sys.sysindexes.rows`, which is deprecated and stale.

```sql
SELECT SUM(CASE WHEN index_id IN (0,1) THEN row_count ELSE 0 END) AS row_count,
       SUM(reserved_page_count) * 8.0 / 1024 AS reserved_mb
FROM sys.dm_db_partition_stats
GROUP BY object_id
```

**`max_length` is in bytes.** For `nvarchar`, `nchar`, and other `n`-prefixed
types it is twice the declared character length, so `nvarchar(50)` reports
`max_length = 100`. A value of `-1` means `MAX`. `catalog.format_type` handles
both, along with `decimal(p,s)` and `datetime2(n)`.

**Types resolve through `user_type_id`, not `system_type_id`.** Joining on
`system_type_id` collapses a user-defined alias type back to its base type and
loses the name the schema actually uses.

**Indexes: not `sp_helpindex`.** It is deprecated and reports neither included
columns nor a filter predicate. `sys.index_columns.is_included_column`
separates key columns from included ones; `sys.indexes.filter_definition`
carries the predicate of a filtered index.

**Foreign keys come back one row per column.** A composite key produces
multiple rows that must be grouped by constraint and ordered by
`constraint_column_id`, or you get the columns in an arbitrary order and the
join clause is wrong. `catalog.get_foreign_keys` does that grouping.

**`is_not_trusted` matters.** A foreign key created `WITH NOCHECK` is not
enforced for existing rows, so the relationship may not actually hold in the
data. The digest carries the flag.

**Extended properties are keyed by `minor_id`.** `minor_id = 0` is the object's
own description; `minor_id = column_id` is a column's.

## Identifier safety: resolve to `object_id` first

No caller-supplied identifier is interpolated into SQL anywhere in this skill.
The pattern is always:

```sql
-- Step 1: resolve, fully parameterized.
SELECT o.object_id, s.name AS schema_name, o.name AS object_name, o.type_desc
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.object_id = OBJECT_ID(?) AND o.type IN ('U','V');

-- Step 2: every subsequent query filters on the integer object_id.
SELECT ... FROM sys.columns WHERE object_id = ?;
```

The one place a name must become text is reading sample rows, where a table
name cannot be a parameter. That path uses the schema and object names the
*server* returned from step 1, bracket-quoted with embedded `]` doubled, so an
injected identifier cannot survive the round trip.

## Cross-database access

`sys.*` views are per-database: `sys.tables` in `master` describes `master`.
To read another database, this skill opens a **new connection** with
`Database=` set, rather than issuing a dynamic `USE`. That keeps dynamic SQL
out of the codebase entirely, and gives a clean "no such database or no access"
error from the connection attempt rather than a confusing empty result.

## Permission degradation

A read-only account frequently lacks one of these, and the failure mode is
usually silence rather than an error:

| Missing permission | Effect | Grant |
| --- | --- | --- |
| `VIEW DEFINITION` | `sys.sql_modules.definition` comes back NULL | `GRANT VIEW DEFINITION TO [DOMAIN\user]` |
| `VIEW DATABASE STATE` | `sys.dm_db_partition_stats` returns nothing, so every row count is 0 | `GRANT VIEW DATABASE STATE TO [DOMAIN\user]` |
| `VIEW SERVER STATE` | `auth_scheme` is unavailable, so integrated auth cannot be confirmed | `GRANT VIEW SERVER STATE TO [login]` |
| Object-level rights | **The object is invisible**, not forbidden | Grant `SELECT` on the schema |

That last one is the trap. SQL Server trims catalog metadata by permission, so
a login with no rights on a table sees a database that appears not to contain
it. An empty table list means "nothing you can see", not "nothing there".
`mssql_describe_table` says so in its not-found message rather than letting you
conclude the table does not exist.
