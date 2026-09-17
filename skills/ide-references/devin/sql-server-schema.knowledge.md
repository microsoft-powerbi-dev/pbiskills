# Devin knowledge: SQL Server schema analysis

## Trigger

Apply this knowledge when the task involves connecting to an on-premises
Microsoft SQL Server to learn what is in it: enumerating databases, schemas,
tables, columns, keys, indexes, or stored procedures; working out how two
tables join; deciding which tables matter in a migration; or tracing an SSRS
dataset back to the source tables it reads. Also apply it whenever a query is
being written against a database whose schema has not been established yet.

## Content

Connect with Windows Integrated Auth over ODBC. The connection string is
`DRIVER={<driver>};SERVER=<host>;DATABASE=<db>;Trusted_Connection=yes`. Note
that `Integrated Security=SSPI` is the ADO.NET spelling and is silently ignored
by ODBC, producing an anonymous login attempt. Never put a username or password
in the string: this access model is the user's own Windows credentials and
nothing else.

Do not hardcode a driver version. Enumerate what is installed with
`pyodbc.drivers()` and take the highest `ODBC Driver NN for SQL Server`,
falling back to the legacy `SQL Server` driver that ships with Windows. ODBC
Driver 18 changed the default to `Encrypt=yes` *with* certificate chain
validation, which fails against the self-signed certificates most on-premises
servers present (`SSL Provider: the certificate chain was issued by an
authority that is not trusted`). On driver 18 and above, emit
`Encrypt=yes;TrustServerCertificate=yes` so the wire stays encrypted while the
chain check is relaxed. Driver 17 and older default to no encryption and do not
hit this.

A named instance is `SERVER=HOST\INSTANCE` and needs the SQL Browser service on
UDP 1434 to resolve to a port. If that is firewalled, use `SERVER=HOST,PORT`
with the instance's static TCP port instead: a comma, not a colon. Never supply
both a named instance and a port.

Confirm authentication actually happened rather than assuming it. Query
`sys.dm_exec_connections` for the current session and read `auth_scheme`:
`KERBEROS` or `NTLM` proves integrated auth; `NTLM` alone usually means the SQL
service account has no correct SPN. This needs `VIEW SERVER STATE`; without it
the value is unavailable, which means "cannot confirm", not "failed". Before
blaming any code, prove the connection at the driver level with
`sqlcmd -S <server> -d <db> -E -Q "SELECT SUSER_SNAME()"`.

Read metadata from the `sys.*` catalog views, never `INFORMATION_SCHEMA`. The
ANSI views cannot express identity columns, computed columns, an index's
included columns or filter predicate, or an untrusted foreign key, and they
truncate a routine definition at 4000 characters. The mapping is:
`sys.databases` for databases (filter `HAS_DBACCESS(name) = 1`), `sys.schemas`,
`sys.objects` where `type IN ('U','V')` for tables and views, `sys.columns`
joined to `sys.types` on `user_type_id`, `sys.default_constraints` on
`default_object_id`, `sys.identity_columns`, `sys.computed_columns`,
`sys.key_constraints` with `sys.index_columns` for primary keys,
`sys.foreign_keys` with `sys.foreign_key_columns`, `sys.indexes` with
`sys.index_columns`, `sys.sql_modules` for procedure bodies, and
`sys.extended_properties` where `name = 'MS_Description'` for documentation.

Get row counts from `sys.dm_db_partition_stats` filtered to
`index_id IN (0,1)`, summing `row_count`. Never run `COUNT(*)` on a table whose
size is unknown: that is a full scan on production. Never use
`sys.sysindexes.rows`, which is deprecated and stale. The partition-stats
numbers are approximate under concurrent writes; say so when reporting them.

Interrogate breadth-first. Go databases, then schemas, then a table list with
row counts, and only then pull full column detail for the tables that matter. A
production OLTP database can exceed three thousand tables, and fetching every
column of every one of them is both slow and useless.

Three details cause most wrong answers when reading the catalog. `max_length`
is in **bytes**, so an `n`-prefixed type reports twice its declared character
length and `nvarchar(50)` comes back as 100; a value of `-1` means `MAX`. A
foreign key returns **one row per column**, so a composite key must be grouped
by constraint and ordered by `constraint_column_id` or the join clause is
wrong. And `is_not_trusted` on a foreign key means it was created `WITH
NOCHECK` and is not enforced for existing rows, so the relationship may not
hold in the data.

Never interpolate a caller-supplied identifier into SQL. Resolve the name to an
`object_id` first with `WHERE o.object_id = OBJECT_ID(?)`, then filter every
subsequent query on that integer. Where a name genuinely must become text, use
the schema and object names the *server* returned from that resolve step,
bracket-quoted with any embedded `]` doubled.

Treat a missing foreign key as a missing constraint, not a missing
relationship. Older estates routinely drop constraints for load performance and
keep the join graph only as a naming convention, so infer joins from a
non-key column matching another table's single-column primary key by name and
compatible type. Label every inferred join as inferred and verify it before
trusting a total built on it.

Two conventions change query correctness rather than style, so detect them
before writing any WHERE clause. A soft-delete column (`IsDeleted`,
`IsActive`, `DeletedDate`, `RecordStatus`) means a query omitting the filter
silently returns retired rows. Type-2 slowly-changing dimension columns
(`EffectiveFrom`, `EffectiveTo`, `IsCurrent`, `RowStartDate`) mean a naive
dimension join fans facts out across every historical version.

Permission trimming is the most misleading failure mode. SQL Server hides
objects a login has no rights on rather than refusing them, so an empty table
list means "nothing you can see", not "nothing there". Similarly, missing
`VIEW DATABASE STATE` makes every row count come back zero, and missing `VIEW
DEFINITION` makes every procedure body come back empty. Report these as partial
results with the permission named, never as facts about the database.

Stay read-only. Open connections with autocommit off and roll back
unconditionally, so nothing can be written even if a statement is
misclassified. Before running any ad-hoc SQL, strip comments and string
literals, require exactly one statement, require the root to be a SELECT or a
WITH clause feeding one, and reject anything containing INSERT, UPDATE, DELETE,
MERGE, TRUNCATE, DROP, CREATE, ALTER, GRANT, BACKUP, RESTORE, EXEC,
sp_executesql, an xp_ procedure, OPENROWSET, BULK INSERT, USE, or `SELECT ...
INTO`. Cap returned rows with `fetchmany` rather than rewriting the user's SQL
to add a TOP clause. Use `SET LOCK_TIMEOUT 5000` and `SET TRANSACTION ISOLATION
LEVEL READ UNCOMMITTED` so metadata reads cannot block production work, and set
an application name in the connection string so a DBA can identify the session.

Never emit data values from a column whose name suggests an identifier or a
health attribute: ssn, social security, tax id, passport, driver's licence,
credit card, cvv, iban, account number, date of birth, patient, medical record,
mrn, npi, diagnosis, icd, prescription, password, token. Show the column *name*
and mark it do-not-select, because an agent unaware the column exists will
write `SELECT *` and pull it, but never read its contents. Treat this as a
safety net rather than a compliance control: a column it does not flag can
still hold sensitive data.

Anything generated from a real schema, whether a digest or a written summary,
names internal servers, databases, tables and columns. Write it outside version
control, never into a public repository, and state plainly what it contains
before sharing it with anyone.
