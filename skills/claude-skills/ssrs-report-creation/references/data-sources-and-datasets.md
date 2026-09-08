# Data Sources and Datasets

Everything a paginated report knows about the world arrives through a data
source and a dataset. Get these two wrong and no amount of layout work rescues
the report.

## Shared versus embedded data sources

A **shared data source** is a `.rds` catalog item on the report server. Reports
reference it by path:

```xml
<DataSources>
  <DataSource Name="dsWWIDW">
    <DataSourceReference>/Data Sources/WideWorldImportersDW</DataSourceReference>
    <rd:SecurityType>None</rd:SecurityType>
  </DataSource>
</DataSources>
```

An **embedded data source** carries the connection inside the RDL:

```xml
<DataSources>
  <DataSource Name="dsWWIDW">
    <ConnectionProperties>
      <DataProvider>SQL</DataProvider>
      <ConnectString>Data Source=sqldw01;Initial Catalog=WideWorldImportersDW</ConnectString>
      <IntegratedSecurity>true</IntegratedSecurity>
    </ConnectionProperties>
    <rd:SecurityType>Integrated</rd:SecurityType>
  </DataSource>
</DataSources>
```

### Why shared wins at estate scale

| Concern | Shared | Embedded |
| --- | --- | --- |
| Change the server name | 1 edit | N edits, one per report |
| Rotate the password | 1 edit | N edits |
| Promote dev to prod | Datasource per environment, reports unchanged | Every RDL differs per environment |
| Audit "what reads this database" | Query the catalog | Parse every RDL |
| Credential exposure | Encrypted once on the server | Encrypted N times |

With 300 reports, an embedded estate turns a server rename into a three-week
project. That is the whole argument.

### The exception

Power BI paginated reports in the Fabric service do **not** support shared data
sources. The definition must carry its own connection. This repository's
compatibility scanner reports that as a warning, not a blocker:

```
[WARN] shared_datasource: Shared data source must be embedded into the report for Power BI.
```

So the target rule is: shared on SSRS and Power BI Report Server, embedded on
Fabric, with the embedding done by the migration transform rather than by hand.
See `migration-to-fabric.md`.

## Credential options

A data source stores one of four credential modes. The choice determines whether
the report can run unattended and whether row-level security by user is possible.

| Mode | RDL / catalog setting | Runs unattended | `User!UserID` is | Use when |
| --- | --- | --- | --- | --- |
| Stored credentials | `rd:SecurityType=DataBase` plus stored user/password | Yes | The viewer's login (still populated) | Subscriptions, snapshots, caching |
| Stored, "use as Windows credentials" | Stored plus impersonate flag | Yes | The viewer's login | Windows auth to SQL with a fixed service account |
| Windows integrated | `<IntegratedSecurity>true</IntegratedSecurity>` | No | The viewer's login | Interactive reports where the database enforces per-user security |
| Prompt | `rd:SecurityType=None`, prompt string set | No | The viewer's login | Rare, mostly ad-hoc admin reports |
| None | `rd:SecurityType=None`, no credentials | Yes | The viewer's login | Anonymous/read-only sources, or when the unattended execution account is used |

Two hard rules:

- **Subscriptions, cached reports and snapshots require stored credentials or
  None.** There is no interactive user at 07:00 to impersonate, so a Windows
  integrated data source simply fails with "credentials have not been supplied".
- **Stored credentials do not break `User!UserID`.** The report server always
  knows who requested the render. So row-level security via a `@UserId` query
  parameter works perfectly well with stored credentials, and is the standard way
  to keep subscriptions working while still filtering per user.

### The double-hop problem

With Windows integrated security, the chain is:

```
User browser  --(hop 1: NTLM/Kerberos)-->  Report Server  --(hop 2)-->  SQL Server
```

NTLM cannot forward a delegated identity across hop 2. The report server
receives the user's token but cannot use it to authenticate onward, so SQL sees
an anonymous logon and rejects it. The symptom is:

```
Login failed for user 'NT AUTHORITY\ANONYMOUS LOGON'.
```

Fixing it requires Kerberos constrained delegation, which is five separate
configuration steps across three teams:

```
1. SPN for the report server service account:
     setspn -S HTTP/ssrs01.contoso.com CONTOSO\svc_ssrs
     setspn -S HTTP/ssrs01              CONTOSO\svc_ssrs
2. SPN for SQL Server:
     setspn -S MSSQLSvc/sqldw01.contoso.com:1433 CONTOSO\svc_sql
3. Trust svc_ssrs for constrained delegation to the MSSQLSvc SPN
   ("use any authentication protocol" if the front end falls back to NTLM).
4. rsreportserver.config: <AuthenticationTypes> must list <RSWindowsNegotiate/>
   before <RSWindowsNTLM/>.
5. Browsers must treat the report server URL as an intranet site, so they send
   Kerberos rather than NTLM.
```

Every one of those five is a separate failure point. This
is the practical reason most estates use **stored credentials plus a `@UserId`
query parameter** instead: one moving part rather than five, and it survives
subscriptions.

Note that this repository's own connector does not implement delegation either:
`docs/report-server.md` states plainly that Kerberos delegation is not
implemented and `negotiate` uses SSPI with either explicit credentials or the
service account identity.

## Connection string forms

`<DataProvider>` and `<ConnectString>` together define the source. The provider
values below are the RDL `DataProvider` element values.

### SQL Server

```xml
<DataProvider>SQL</DataProvider>
<ConnectString>Data Source=sqldw01;Initial Catalog=WideWorldImportersDW</ConnectString>
```

Named instance and port:

```
Data Source=sqldw01\BI,1433;Initial Catalog=WideWorldImportersDW
```

Add `Encrypt=True;TrustServerCertificate=False` where TLS is enforced. Add
`Application Name=SSRS-MonthlySales` so DBAs can attribute load in
`sys.dm_exec_sessions`.

### Azure SQL Database

```xml
<DataProvider>SQL</DataProvider>
<ConnectString>Data Source=tcp:contoso-sql.database.windows.net,1433;Initial Catalog=SalesDW;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30</ConnectString>
```

Credentials must be stored (SQL authentication) or a managed identity where the
platform supports it. Integrated Security does not apply.

### Fabric Warehouse

The Fabric Warehouse exposes a TDS endpoint, so it is a SQL Server connection:

```xml
<DataProvider>SQL</DataProvider>
<ConnectString>Data Source=abcd1234.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesWarehouse</ConnectString>
```

Authentication is Entra ID (OAuth), so the data source credential type is
"OAuth" in the Fabric paginated data source dialog rather than stored SQL
credentials.

### Fabric Lakehouse SQL analytics endpoint

Identical shape, different host suffix, and the catalog is the lakehouse name:

```xml
<DataProvider>SQL</DataProvider>
<ConnectString>Data Source=abcd1234.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesLakehouse</ConnectString>
```

The endpoint is read-only. Any report that expects to call a stored procedure
that writes will fail; the endpoint supports views and read-only procs only.

### Power BI semantic model (DAX or MDX)

```xml
<DataProvider>PBIDATASET</DataProvider>
<ConnectString>Data Source=powerbi://api.powerbi.com/v1.0/myorg/Finance;Initial Catalog=Sales Model</ConnectString>
```

Query text is DAX (or MDX). A minimal DAX dataset query:

```
EVALUATE
SUMMARIZECOLUMNS(
    'Region'[Region],
    'Date'[Month],
    TREATAS({@RegionList}, 'Region'[Region]),
    "GrossSales", [Total Sales],
    "OrderCount", [Order Count]
)
```

Field names arrive bracketed, for example `[Region]` and `[GrossSales]`, and the
RDL field references must match exactly.

### Analysis Services (multidimensional or tabular)

```xml
<DataProvider>OLEDB-MD</DataProvider>
<ConnectString>Data Source=ssas01;Initial Catalog=SalesCube;Cube=Sales</ConnectString>
```

MDX query text. Multi-value parameters map naturally because MDX takes sets:
`STRTOSET(@Region)`.

### ODBC and OLE DB

```xml
<DataProvider>ODBC</DataProvider>
<ConnectString>Dsn=SalesDW</ConnectString>
<!-- DSN-less form -->
<ConnectString>Driver={ODBC Driver 18 for SQL Server};Server=sqldw01;Database=SalesDW;Encrypt=yes</ConnectString>
```

```xml
<DataProvider>OLEDB</DataProvider>
<ConnectString>Provider=MSOLEDBSQL;Data Source=sqldw01;Initial Catalog=SalesDW</ConnectString>
```

Both use **positional** `?` parameters, not named ones. The query becomes
`WHERE d.Date >= ? AND d.Date &lt; ?` and the `<QueryParameters>` order is the
binding, which is a common source of silent wrong-parameter bugs. Prefer the
native `SQL` provider over OLE DB for SQL Server: there is no benefit and the
parameter semantics are worse.

## Dataset design: inline SQL, view, or stored procedure

| Approach | Right when | Wrong when |
| --- | --- | --- |
| Inline SQL | Single table, under about 15 lines, no reuse | Anything with joins plus conditional logic |
| View | The same row set is needed by several reports and by ad-hoc users; you want it queryable outside SSRS | You need parameters (a view cannot take them; the report filters the view, which may not fold well) |
| Stored procedure | Non-trivial logic, parameters, temp tables, hints, anything you want to unit test | The source is a semantic model, a lakehouse read-only endpoint, or a provider that cannot execute procs |

The stored procedure argument in practice:

- The query is in source control with the rest of the database.
- A DBA can tune it without opening Report Builder.
- It can be executed and diffed in SSMS with the exact production parameters.
- `OPTION (RECOMPILE)` and query hints live somewhere reviewable.
- The RDL stays small and readable.

Declaring it in RDL:

```xml
<DataSet Name="dsSalesByRegion">
  <Query>
    <DataSourceName>dsWWIDW</DataSourceName>
    <CommandType>StoredProcedure</CommandType>
    <CommandText>Reporting.usp_MonthlySalesByRegion</CommandText>
    <QueryParameters>
      <QueryParameter Name="@DateFrom">
        <Value>=Parameters!DateFrom.Value</Value>
      </QueryParameter>
      <QueryParameter Name="@DateTo">
        <Value>=Parameters!DateTo.Value</Value>
      </QueryParameter>
      <QueryParameter Name="@RegionList">
        <Value>=Join(Parameters!Region.Value, ",")</Value>
      </QueryParameter>
      <QueryParameter Name="@UserId">
        <Value>=User!UserID</Value>
      </QueryParameter>
    </QueryParameters>
    <rd:UseGenericDesigner>true</rd:UseGenericDesigner>
  </Query>
  <Fields>
    <Field Name="Region">    <DataField>Region</DataField>    </Field>
    <Field Name="SalesMonth"><DataField>SalesMonth</DataField></Field>
    <Field Name="OrderCount"><DataField>OrderCount</DataField></Field>
    <Field Name="GrossSales"><DataField>GrossSales</DataField></Field>
    <Field Name="NetSales">  <DataField>NetSales</DataField>  </Field>
    <Field Name="MarginPct"> <DataField>MarginPct</DataField> </Field>
  </Fields>
</DataSet>
```

Note the ordering constraint inside `<Query>`: `DataSourceName`, then
`CommandType`, then `CommandText`. This repository's paginated transformer
enforces exactly that order when it injects a `CommandType` during a dataset
override (`backend/app/core/paginated/transformer.py`, `_override_datasets`).

## Query parameters and how they bind

Two different things share the word "parameter":

- **Report parameter** (`<ReportParameters>/<ReportParameter>`): what the user
  sees and fills in.
- **Query parameter** (`<Query>/<QueryParameters>/<QueryParameter>`): what is
  sent to the database.

They are bound by the `<Value>` expression in the query parameter. The binding
is one-way and arbitrary. You can send a constant, a transformed value, or a
built-in:

```xml
<QueryParameter Name="@AsOf">
  <Value>=Today()</Value>
</QueryParameter>
<QueryParameter Name="@UserId">
  <Value>=User!UserID</Value>
</QueryParameter>
<QueryParameter Name="@DateToExclusive">
  <Value>=DateAdd("d", 1, Parameters!DateTo.Value)</Value>
</QueryParameter>
```

For `CommandType=Text`, SSRS auto-creates a report parameter for every `@Name`
it finds in the SQL unless you have already declared one. This convenience is
also a trap: rename the SQL variable and you silently get a second, unfilled
report parameter.

## Multi-value parameters

### With `CommandType=Text`

SSRS expands the parameter into a comma-separated literal list at execution
time. Write it as a bare `IN`:

```sql
SELECT ...
FROM DW.Fact.Sale s
JOIN DW.Dimension.City c ON c.[City Key] = s.[City Key]
WHERE c.[Sales Territory] IN (@Region)
```

Do not add quotes around `@Region`. SSRS produces `IN (N'Southeast', N'Great
Lakes')` itself. Adding quotes yields `IN ('''Southeast'',''Great Lakes''')`,
which matches nothing and does not error.

### With `CommandType=StoredProcedure`: the limitation

A stored procedure parameter is a scalar. SSRS does not expand multi-value
parameters into a list for stored procedures. Passing `Parameters!Region.Value`
directly gives you only the **first** selected value, silently.

There are three correct workarounds.

**1. Join and `STRING_SPLIT`** (SQL Server 2016+, compatibility level 130+):

RDL side:

```xml
<QueryParameter Name="@RegionList">
  <Value>=Join(Parameters!Region.Value, ",")</Value>
</QueryParameter>
```

Proc side:

```sql
CREATE OR ALTER PROCEDURE Reporting.usp_SalesByRegion
    @RegionList nvarchar(max)
AS
BEGIN
    SELECT ...
    FROM DW.Fact.Sale s
    JOIN DW.Dimension.City c ON c.[City Key] = s.[City Key]
    WHERE c.[Sales Territory] IN (SELECT LTRIM(RTRIM(value)) FROM STRING_SPLIT(@RegionList, ','));
END
```

Pick a delimiter that cannot appear in the data. Comma is fine for region codes
and wrong for company names. Use `|` or a pipe-plus-tilde sequence when in
doubt.

**2. Table-valued parameter.** Cleaner typing, better cardinality estimates,
but SSRS cannot construct a TVP from the report. It works only when the proc is
called by a wrapper, so in practice this is used from an intermediate layer, not
directly from RDL. Declared as:

```sql
CREATE TYPE Reporting.RegionList AS TABLE (Region nvarchar(50) PRIMARY KEY);

CREATE OR ALTER PROCEDURE Reporting.usp_SalesByRegionTvp
    @Regions Reporting.RegionList READONLY
AS
BEGIN
    SELECT ...
    FROM DW.Fact.Sale s
    JOIN DW.Dimension.City c ON c.[City Key] = s.[City Key]
    JOIN @Regions r ON r.Region = c.[Sales Territory];
END
```

**3. Switch the dataset to `CommandType=Text` and call the proc with an
expanded list.** Works, but you have moved logic back into the RDL. Acceptable
only as a stop-gap.

### Handling "select all" safely

`=Join(Parameters!Region.Value, ",")` on a 5,000-item parameter produces a
string that can exceed practical limits and destroys cardinality estimates. Add
an all-values shortcut:

```sql
WHERE (@AllRegions = 1
       OR c.[Sales Territory] IN (SELECT value FROM STRING_SPLIT(@RegionList, ',')))
```

with

```xml
<QueryParameter Name="@AllRegions">
  <Value>=IIf(Parameters!Region.Count = Parameters!Region.Label.Length, 1, 0)</Value>
</QueryParameter>
```

or, more robustly, compare `Parameters!Region.Count` to a count returned by the
available-values dataset.

### The MultiValue element

Fabric paginated reports want an explicit `<MultiValue>true</MultiValue>` next to
`<AllowMultipleValues>`. This repository injects it automatically during the
paginated transform (`enrichers.py`, rule A2), inserting it after `Prompt` and
before `ValidValues`/`DefaultValue` because sibling order matters:

```xml
<ReportParameter Name="Region">
  <DataType>String</DataType>
  <AllowBlank>false</AllowBlank>
  <AllowMultipleValues>true</AllowMultipleValues>
  <Prompt>Region</Prompt>
  <MultiValue>true</MultiValue>
  <ValidValues>
    <DataSetReference>
      <DataSetName>dsRegionList</DataSetName>
      <ValueField>Region</ValueField>
      <LabelField>Region</LabelField>
    </DataSetReference>
  </ValidValues>
</ReportParameter>
```

## Shared datasets and caching

A **shared dataset** (`.rsd`) is a query published to the catalog and referenced
by many reports:

```xml
<DataSet Name="dsRegionList">
  <SharedDataSet>
    <SharedDataSetReference>/Datasets/RegionList</SharedDataSetReference>
  </SharedDataSet>
  <Fields>
    <Field Name="Region"><DataField>Region</DataField></Field>
  </Fields>
</DataSet>
```

Use them for:

- Parameter available-values lists. A `RegionList` shared dataset used by 40
  reports means one place to change when regions are restructured.
- Reference data that changes slowly and is queried constantly.

The payoff is caching. A shared dataset can be configured with a cache refresh
plan, so the region dropdown is served from cache rather than hitting the
database on every report open. Configure it in the portal under the dataset's
**Caching** properties, either "cache expires after N minutes" or "cache expires
on schedule", with an optional cache refresh plan that pre-warms it.

Caching a shared dataset requires stored credentials on its data source, for
the same reason subscriptions do.

Fabric paginated does not support shared datasets. The scanner reports:

```
[WARN] shared_dataset: Shared dataset must be embedded into the report for Power BI.
```

## Dataset field metadata

The `<Fields>` collection is the contract between the query and the layout. Two
kinds of field exist.

**Query field**, mapped by `DataField` to a result-set column name:

```xml
<Field Name="GrossSales">
  <DataField>GrossSales</DataField>
  <rd:TypeName>System.Decimal</rd:TypeName>
</Field>
```

**Calculated field**, evaluated by the report engine per row:

```xml
<Field Name="SalesPerOrder">
  <Value>=IIf(Fields!OrderCount.Value = 0, Nothing,
              Fields!GrossSales.Value / Fields!OrderCount.Value)</Value>
</Field>
```

Calculated fields are evaluated once per row and are visible to aggregates, so
`Sum(Fields!SalesPerOrder.Value)` works (though summing a ratio is usually a
mistake, and you want `Sum(GrossSales)/Sum(OrderCount)` instead).

### Why field names must be stable

Every expression in the report references a field by name:
`=Fields!GrossSales.Value`. Rename the field and every reference silently
resolves to nothing at runtime, rendering blank cells rather than an error.

This repository's RDL validator makes that failure loud instead: it checks that
**every `Fields!X.Value` reference resolves to a declared dataset field** and
fails validation if it does not (`docs/report-server.md`, section 8, describes a
real bug caught this way where the generator declared only physical columns while
visuals referenced calculated measures).

Practical rules:

- Alias in SQL so the result-set column name equals the field name you want.
  Never rely on SSRS's auto-generated `Column1`.
- Never rename a field to fix a label. Change the header textbox instead.
- If a source column must be renamed, add the new name as an additional alias
  first, migrate references, then drop the old one.

## Filtering in the query versus in the report

SSRS lets you attach a `<Filters>` collection to a dataset, a Tablix, or a
group:

```xml
<Filters>
  <Filter>
    <FilterExpression>=Fields!Region.Value</FilterExpression>
    <Operator>In</Operator>
    <FilterValues>
      <FilterValue>=Parameters!Region.Value</FilterValue>
    </FilterValues>
  </Filter>
</Filters>
```

This is almost always the wrong place. The data-volume argument:

- A report filter executes **after** the full result set has crossed the network
  and been materialised in report server memory.
- Filtering 8,000,000 rows down to 400 in the report means 8,000,000 rows were
  transferred, parsed and buffered. `TimeDataRetrieval` and `TimeProcessing` both
  balloon and memory pressure on the report server rises.
- The same predicate in `WHERE` lets the optimiser use an index and return 400
  rows.

Report filters are legitimate in exactly three cases:

1. The dataset is shared and cached, and this report needs a subset of the cached
   rows. You are trading a filter for a database round trip on purpose.
2. Two regions of the same report need different subsets of one dataset (a
   Tablix filter avoids a second query).
3. The filter value depends on something computed in the report and not
   available to the query.

## Row-level security with `User!UserID`

`User!UserID` returns the requesting user's identity: `CONTOSO\alice` for
Windows auth, the UPN for Entra ID. It is populated regardless of the data
source's credential mode, which is what makes this pattern compatible with
subscriptions and stored credentials.

### Pattern 1: entitlement table join

Most common and most flexible.

```sql
CREATE TABLE Ref.RegionEntitlement (
    UserPrincipal nvarchar(128) NOT NULL,
    Region        nvarchar(50)  NOT NULL,
    CONSTRAINT PK_RegionEntitlement PRIMARY KEY (UserPrincipal, Region)
);

-- in the report proc
WHERE EXISTS (
    SELECT 1 FROM Ref.RegionEntitlement e
    WHERE e.UserPrincipal = @UserId
      AND e.Region        = c.[Sales Territory]
)
```

Bind `@UserId` to `=User!UserID` in the query parameter. Also filter the
available-values dataset with the same predicate so the dropdown never offers a
region the user cannot see.

### Pattern 2: AD group expansion

Where entitlement is by group rather than by user, resolve groups in the
database against a cache refreshed by a scheduled job (expanding AD groups at
query time is slow and fragile):

```sql
WHERE c.[Sales Territory] IN (
    SELECT m.Region
    FROM Ref.GroupRegionMap  AS m
    JOIN Ref.UserGroupCache  AS g ON g.GroupName = m.GroupName
    WHERE g.UserPrincipal = @UserId
);
```

### Pattern 3: native SQL Server row-level security

Define a security predicate on the fact table and let the database enforce it.
Rather than connecting as the user (which reintroduces the double-hop problem),
have the proc set a session context that the predicate function reads:

```sql
EXEC sys.sp_set_session_context @key = N'UserPrincipal', @value = @UserId, @read_only = 1;
-- the RLS predicate function reads SESSION_CONTEXT(N'UserPrincipal')
```

Strongest option, because a report author cannot bypass it, and the most work to
set up.

### Pattern 4: subscription-safe checks

A subscription runs under its owner's identity, so `User!UserID` is the
subscription owner, not the recipient. For per-recipient filtering, use a
**data-driven subscription** whose driving query returns one row per recipient
with their parameter values. See `deployment.md`.

### What not to do

Do not filter with a report-level filter on `User!UserID`. Every row still leaves
the database, so a determined user who exports the underlying dataset (or who
changes a URL parameter) can see everything. Security belongs in the `WHERE`
clause or in the database's own RLS.
