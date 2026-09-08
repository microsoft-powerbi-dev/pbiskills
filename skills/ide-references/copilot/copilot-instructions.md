# Copilot instructions: SSRS / RDL work in this repository

When suggesting or completing code that creates, edits, or reviews an `.rdl`
file, that relates to SSRS / Power BI Paginated report design, deployment, or
performance, or that connects to a SQL Server to read its schema, follow the
rules below. Full detail lives in `skills/claude-skills/rdl-generation/`,
`skills/claude-skills/ssrs-report-creation/`, and
`skills/claude-skills/sql-server-schema/`.

## When generating RDL XML

Target schema version 2016
(`http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition`)
by default; match an existing file's declared version instead of upgrading
it. Respect element order: inside `Query`, `DataSourceName` comes before
`CommandType`, which comes before `CommandText`. Place `PageHeader` and
`PageFooter` inside `ReportSection/Page`, never directly inside `Report`.
Register the default namespace and the `rd` prefix before writing generated
XML, so elements do not serialise with an auto-generated `ns0:` prefix. Give
every measurement a unit suffix (`in`, `cm`, `mm`, `pt`, `px`); never emit a
bare numeric size.

Write field references as `=Fields!Name.Value` and aggregates as
`=Sum(Fields!Name.Value)` (or `Avg` / `Min` / `Max` / `Count` /
`CountDistinct`). Before suggesting a `Fields!X.Value` reference, check that
`X` is declared as a `Field` in the dataset the containing Tablix or Chart
points to; an unresolved reference renders `#Error` at runtime with no
build-time signal.

## When designing or reviewing a report

Prefer a shared data source over an embedded connection string. Prefer a
stored procedure or a view over inline SQL for anything beyond a trivial
query. Filter in the query, not with a report-level filter. Give every
parameter a prompt, a data type, and a sensible default. Remember that
multi-value parameters cannot bind directly to a stored procedure parameter;
they need a join-and-split or a table-valued parameter.

Flag these patterns in review: `SELECT *`, `SELECT DISTINCT` used to mask a
join fan-out, a leading-wildcard `LIKE '%...'`, cross joins, cursors, `IIf`
nested more than two levels deep, chained `Replace()` calls, an embedded
connection string with integrated security on a service account, and more
than roughly five Tablix or Chart regions on a single report.

## When deploying

For SSRS 2017+ or Power BI Report Server, use the REST v2.0 API
(`/reports/api/v2.0`): `POST /CatalogItems` to create, `PUT
/CatalogItems({id})` to overwrite, RDL content as base64 in the `Content`
field. For older servers, use `rs.exe` with an RSS script.

## When exploring a SQL Server database

Connect with Windows Integrated Auth (`Trusted_Connection=yes`) and never
suggest a connection string containing a username or password. Do not hardcode
an ODBC driver version: enumerate what is installed and take the highest
`ODBC Driver NN for SQL Server`. On driver 18 and above add
`Encrypt=yes;TrustServerCertificate=yes`, because that driver validates the
certificate chain by default and most on-premises servers present a
self-signed certificate.

Read structure from the `sys.*` catalog views rather than `INFORMATION_SCHEMA`,
which cannot express identity or computed columns, an index's included columns
or filter predicate, or an untrusted foreign key, and which truncates routine
definitions at 4000 characters. Take row counts from
`sys.dm_db_partition_stats` filtered to `index_id IN (0,1)`, never from
`COUNT(*)` on a table of unknown size.

Three details cause most wrong answers: `max_length` is in bytes, so
`nvarchar(50)` reports 100 and `-1` means MAX; foreign keys come back one row
per column and must be grouped by constraint and ordered by
`constraint_column_id`; and an empty result frequently means permission
trimming rather than an empty database, because SQL Server hides objects a
login has no rights on.

Resolve object names to an `object_id` through `OBJECT_ID(?)` and filter on the
integer rather than interpolating an identifier into SQL. Keep everything
read-only: autocommit off, unconditional rollback, and refuse any statement
that is not a single SELECT.

Detect soft-delete columns (`IsDeleted`, `IsActive`, `DeletedDate`) and type-2
dimension columns (`EffectiveFrom`, `IsCurrent`) before writing a WHERE clause:
omitting the first silently returns retired rows, and ignoring the second fans
facts out across every historical version.

Never suggest returning values from a column named for an identifier or a
health attribute (ssn, tax id, passport, licence, card number, account number,
date of birth, patient, mrn, npi, diagnosis, icd, password, token). Name the
column so it is visibly there and mark it do-not-select, but do not read it.
Anything generated from a real schema names internal systems and must not be
committed to a public repository.

## Reference paths

- `skills/claude-skills/rdl-generation/SKILL.md` and its `references/`,
  `scripts/`, `examples/` subfolders for anything about the XML itself.
- `skills/claude-skills/ssrs-report-creation/SKILL.md` and its `references/`,
  `examples/` subfolders for the end-to-end workflow, design patterns,
  export, deployment, and performance.
- `skills/claude-skills/sql-server-schema/SKILL.md` and its `references/`,
  `scripts/`, `examples/` subfolders for connecting to an on-premises SQL
  Server, reading its catalog, and generating a per-database schema pack.
