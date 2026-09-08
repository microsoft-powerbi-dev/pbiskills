# Data Sources, Data Sets, Fields and Parameters

Everything data-bound in an RDL hangs off three blocks: `DataSources` (where the
data lives), `DataSets` (what to ask for and what comes back), and
`ReportParameters` (what the user supplies). Get these right and the layout is
straightforward. Get them wrong and every cell renders `#Error`.

## DataSources

### Embedded data source

An embedded data source carries its own connection string and lives entirely
inside the `.rdl`. This is the only form Power BI paginated reports accept, and
the form this repository's generator always emits.

```xml
<DataSources>
  <DataSource Name="SalesDB">
    <ConnectionProperties>
      <DataProvider>SQL</DataProvider>
      <ConnectString>Data Source=SQL01;Initial Catalog=Sales</ConnectString>
      <IntegratedSecurity>true</IntegratedSecurity>
    </ConnectionProperties>
    <rd:SecurityType>Integrated</rd:SecurityType>
    <rd:DataSourceID>0a4f6f18-1f2a-4e6f-9a41-6f2b8ad1a6b1</rd:DataSourceID>
  </DataSource>
</DataSources>
```

`DataSource/@Name` must be a CLS-compliant identifier: letters, digits and
underscore, not starting with a digit. `Query/DataSourceName` in every dataset
must match it exactly, including case.

### Shared data source reference

A shared data source points at a `.rds` item already published on the server.

```xml
<DataSource Name="SharedSales">
  <DataSourceReference>/Data Sources/SalesDB</DataSourceReference>
  <rd:SecurityType>None</rd:SecurityType>
  <rd:DataSourceID>0a4f6f18-1f2a-4e6f-9a41-6f2b8ad1a6b1</rd:DataSourceID>
</DataSource>
```

`DataSourceReference` and `ConnectionProperties` are mutually exclusive. The
reference is a server folder path, not a file path, and it is resolved at render
time, so a report that references a missing path publishes fine and fails on
first execution.

Power BI paginated does not support shared data sources. This repository flags
them: `compatibility.detect` emits `shared_datasource` at severity `warn` with
the message "Shared data source must be embedded into the report for Power BI."

### DataProvider values

`DataProvider` names a data extension registered on the target server. Check
`rsreportserver.config` under `<Data><Extensions>` for the exact names a given
server accepts.

| DataProvider | Source |
| --- | --- |
| `SQL` | SQL Server, Azure SQL Database, Fabric Warehouse and Lakehouse SQL endpoints |
| `SQLAZURE` | Azure SQL Database via the dedicated extension |
| `SQLPDW` | Analytics Platform System / Parallel Data Warehouse |
| `OLEDB` | any OLE DB provider |
| `OLEDB-MD` | Analysis Services and other multidimensional OLE DB providers |
| `ODBC` | any ODBC driver |
| `ORACLE` | Oracle |
| `TERADATA` | Teradata |
| `XML` | an XML document or web service |
| `SHAREPOINTLIST` | a SharePoint list |
| `SAPBW`, `ESSBASE` | SAP BW, Hyperion Essbase |
| `PBIDATASET` | a Power BI semantic model (paginated reports) |

Some servers register the Analysis Services extension as `SQLAS` rather than
`OLEDB-MD`. If a published report fails with "The data extension X is either not
registered or not supported", the name is the thing to change.

### ConnectString forms

All of these go inside `<ConnectionProperties>` with the `DataProvider` shown.

```xml
<!-- SQL Server, default instance. DataProvider SQL -->
<ConnectString>Data Source=SQL01;Initial Catalog=Sales</ConnectString>

<!-- SQL Server, named instance and explicit port -->
<ConnectString>Data Source=SQL01\INST01,1433;Initial Catalog=Sales</ConnectString>

<!-- Azure SQL Database -->
<ConnectString>Data Source=tcp:myserver.database.windows.net,1433;Initial Catalog=Sales;Encrypt=True;TrustServerCertificate=False</ConnectString>

<!-- Fabric Warehouse via its SQL endpoint. DataProvider SQL, rd:SecurityType DataBase -->
<ConnectString>Data Source=xxxxxxxxxxxx.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesWarehouse</ConnectString>

<!-- Fabric Lakehouse via its SQL analytics endpoint: same host, catalog is the lakehouse, read-only -->
<ConnectString>Data Source=xxxxxxxxxxxx.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesLakehouse</ConnectString>

<!-- Analysis Services, multidimensional or tabular. DataProvider OLEDB-MD -->
<ConnectString>Data Source=ASSERVER;Initial Catalog=SalesModel</ConnectString>

<!-- Power BI semantic model, paginated in the service or Fabric. DataProvider PBIDATASET -->
<ConnectString>Data Source=powerbi://api.powerbi.com/v1.0/myorg/Finance;Initial Catalog=Sales Semantic Model</ConnectString>
```

In full, the Fabric Warehouse case:

```xml
<DataSource Name="FabricWarehouse">
  <ConnectionProperties>
    <DataProvider>SQL</DataProvider>
    <ConnectString>Data Source=xxxxxxxxxxxx.datawarehouse.fabric.microsoft.com;Initial Catalog=SalesWarehouse</ConnectString>
    <IntegratedSecurity>false</IntegratedSecurity>
  </ConnectionProperties>
  <rd:SecurityType>DataBase</rd:SecurityType>
</DataSource>
```

### IntegratedSecurity

`<IntegratedSecurity>true</IntegratedSecurity>` tells the server to connect as
the caller (or as the configured unattended execution account) rather than with
stored credentials. It pairs with `rd:SecurityType`, the designer's record of the
same choice: `Integrated`, `DataBase`, `Windows` or `None`.

Two operational consequences: integrated security plus a remote data source means
Kerberos delegation, and wrong SPNs give "Login failed for user 'NT
AUTHORITY\ANONYMOUS LOGON'"; and in Power BI paginated and Fabric it requires an
on-premises data gateway with a service account that has read access. This
repository flags the latter as `integrated_security` at severity `info`.

## DataSets

### The Query block

```xml
<DataSet Name="SalesData">
  <Query>
    <DataSourceName>SalesDB</DataSourceName>
    <CommandType>Text</CommandType>
    <CommandText>SELECT Region, Product, OrderDate, Amount FROM dbo.Sales WHERE YEAR(OrderDate) = @Year</CommandText>
    <QueryParameters>
      <QueryParameter Name="@Year"><Value>=Parameters!Year.Value</Value></QueryParameter>
    </QueryParameters>
    <Timeout>120</Timeout>
    <rd:UseGenericDesigner>true</rd:UseGenericDesigner>
  </Query>
  <Fields>
    <Field Name="Region"><DataField>Region</DataField><rd:TypeName>System.String</rd:TypeName></Field>
    <Field Name="Product"><DataField>Product</DataField><rd:TypeName>System.String</rd:TypeName></Field>
    <Field Name="OrderDate"><DataField>OrderDate</DataField><rd:TypeName>System.DateTime</rd:TypeName></Field>
    <Field Name="Amount"><DataField>Amount</DataField><rd:TypeName>System.Decimal</rd:TypeName></Field>
  </Fields>
</DataSet>
```

The `Query` child order is `DataSourceName`, `CommandType`, `CommandText`,
`QueryParameters`, `Timeout`. This repository's generator states the rule
inline: "RDL schema child order: DataSourceName, CommandType, CommandText,
QueryParameters."

### CommandType values

| Value | `CommandText` holds | Notes |
| --- | --- | --- |
| `Text` | a SQL statement or batch | the default when `CommandType` is omitted |
| `StoredProcedure` | the bare procedure name, e.g. `dbo.usp_SalesByRegion` | no `EXEC`, no arguments, no brackets around the schema |
| `TableDirect` | a single table name | the provider issues `SELECT * FROM <name>`; not supported by all extensions |

Because `Text` is the default, `<CommandType>Text</CommandType>` is optional.
Omitting it is what Report Builder does. Emitting it is harmless and more
explicit. When switching a dataset from a procedure back to text, remove the
element rather than setting it to `Text`, which is what
`paginated/transformer._override_datasets` does.

Stored procedure form:

```xml
<Query>
  <DataSourceName>SalesDB</DataSourceName>
  <CommandType>StoredProcedure</CommandType>
  <CommandText>dbo.usp_SalesByRegion</CommandText>
  <QueryParameters>
    <QueryParameter Name="@Year"><Value>=Parameters!Year.Value</Value></QueryParameter>
    <QueryParameter Name="@Region"><Value>=Join(Parameters!Region.Value, ",")</Value></QueryParameter>
  </QueryParameters>
</Query>
```

A stored-procedure dataset must declare a `QueryParameter` for every procedure
parameter that has no default. `QueryParameter/@Name` includes the `@`. The
`Value` is an RDL expression, so it may be a literal, a `Parameters!` reference,
or a computed expression such as `=Today()`.

### Fields: DataField versus Value

`DataField` names a column in the result set. `Value` makes the field a
calculated field evaluated per row.

```xml
<Fields>
  <!-- physical column -->
  <Field Name="Amount"><DataField>Amount</DataField><rd:TypeName>System.Decimal</rd:TypeName></Field>
  <!-- calculated field: expression, no DataField -->
  <Field Name="AmountWithTax"><Value>=Fields!Amount.Value * 1.2</Value></Field>
  <!-- name differs from the column: Name is the identifier, DataField is the column -->
  <Field Name="Total_Sales"><DataField>Total Sales</DataField><rd:TypeName>System.Decimal</rd:TypeName></Field>
</Fields>
```

Three hard rules.

- `Field/@Name` must be a CLS-compliant identifier. SSRS rejects the whole
  report with "A field in the dataset 'ds' has the name 'Total Sales'. Field
  names must be CLS-compliant identifiers." `DataField` is not bound by that
  rule, so a column with a space keeps its spelling there and the `Name` is
  sanitised. This repository implements exactly that in
  `rdl_generator._rdl_field_name`.
- A calculated field cannot contain an aggregate. SSRS rejects "The expression
  used for the calculated field 'Total_Sales' includes an aggregate ...
  Aggregate functions cannot be used in calculated field expressions." Put the
  aggregate at the point of use in a cell instead. This repository resolves
  measures into inline aggregates for that reason (`_resolve_measures`).
- `rd:TypeName` is a designer hint, not a runtime contract. Omitting it works;
  getting it wrong causes formatting and sorting surprises, not errors.

### The rd:TypeName table

| `rd:TypeName` | .NET type | Typical T-SQL column | RDL parameter `DataType` | This repo's IR `DataType` |
| --- | --- | --- | --- | --- |
| `System.String` | string | `varchar`, `nvarchar`, `char`, `text` | `String` | `STRING` |
| `System.Int16` | short | `smallint` | `Integer` | `INTEGER` |
| `System.Int32` | int | `int` | `Integer` | `INTEGER` |
| `System.Int64` | long | `bigint` | `Integer` | `INTEGER` |
| `System.Byte` | byte | `tinyint` | `Integer` | `INTEGER` |
| `System.Decimal` | decimal | `decimal`, `numeric`, `money`, `smallmoney` | `Float` | `REAL` |
| `System.Double` | double | `float` | `Float` | `REAL` |
| `System.Single` | float | `real` | `Float` | `REAL` |
| `System.Boolean` | bool | `bit` | `Boolean` | `BOOLEAN` |
| `System.DateTime` | DateTime | `datetime`, `datetime2`, `date`, `smalldatetime` | `DateTime` | `DATETIME` |
| `System.DateTimeOffset` | DateTimeOffset | `datetimeoffset` | `DateTime` | `DATETIME` |
| `System.Guid` | Guid | `uniqueidentifier` | `String` | `STRING` |
| `System.TimeSpan` | TimeSpan | `time` | no direct match, use `String` | `UNKNOWN` |
| `System.Byte[]` | byte array | `varbinary`, `image` | not usable as a parameter | `UNKNOWN` |
| `System.Object` | object | `sql_variant` | not usable as a parameter | `UNKNOWN` |

The last column is `_TYPENAME_MAP` in `backend/app/core/parser/rdl_xml.py`;
anything not in the map falls through to `DataType.UNKNOWN`. The same map accepts
the five RDL parameter type names directly (`string`, `integer`, `float`,
`boolean`, `datetime`), which is how `_parse_parameters` types a
`ReportParameter`.

The RDL `ReportParameter/DataType` enumeration has exactly five members:
`Boolean`, `DateTime`, `Integer`, `Float`, `String`. There is no decimal, guid or
binary, so money and decimal columns become `Float` parameters and lose exact
decimal semantics at the prompt.

### Dataset filters

`DataSet/Filters` filters rows *after* they are fetched. It never reduces what
the database returns, so it is a formatting tool, not a performance tool.

```xml
<Filters>
  <Filter>
    <FilterExpression>=Fields!Amount.Value</FilterExpression>
    <Operator>GreaterThan</Operator>
    <FilterValues><FilterValue>0</FilterValue></FilterValues>
  </Filter>
</Filters>
```

Operators: `Equal`, `NotEqual`, `GreaterThan`, `GreaterThanOrEqual`, `LessThan`,
`LessThanOrEqual`, `TopN`, `BottomN`, `TopPercent`, `BottomPercent`, `In`,
`Between`, `Like`.

To filter at the source, push the predicate into `CommandText` and bind it with a
`QueryParameter`. This repository does that in
`rdl_generator._apply_filter_to_sql`, which splices
`([Field] = @P OR @P IS NULL)` into the `WHERE` clause so a null parameter means
"no filter".

### Shared datasets

```xml
<DataSet Name="CustomerList">
  <Query>
    <DataSourceName>SharedSales</DataSourceName>
    <CommandText />
    <rd:UseGenericDesigner>true</rd:UseGenericDesigner>
  </Query>
  <Fields>
    <Field Name="CustomerId"><DataField>CustomerId</DataField><rd:TypeName>System.Int32</rd:TypeName></Field>
    <Field Name="CustomerName"><DataField>CustomerName</DataField><rd:TypeName>System.String</rd:TypeName></Field>
  </Fields>
  <SharedDataSet><SharedDataSetReference>/Datasets/CustomerList</SharedDataSetReference></SharedDataSet>
</DataSet>
```

The `Fields` block is still required: it is the local contract the report binds
against, and it must match the shared dataset's field names. Power BI paginated
does not support shared datasets; this repository flags them as
`shared_dataset` at severity `warn`.

## ReportParameters

### The full shape

```xml
<ReportParameters>
  <ReportParameter Name="Year">
    <DataType>Integer</DataType>
    <Nullable>false</Nullable>
    <DefaultValue><Values><Value>=Year(Today())</Value></Values></DefaultValue>
    <Prompt>Fiscal year</Prompt>
    <ValidValues><ParameterValues>
      <ParameterValue><Value>2024</Value><Label>FY2024</Label></ParameterValue>
      <ParameterValue><Value>2025</Value><Label>FY2025</Label></ParameterValue>
      <ParameterValue><Value>2026</Value><Label>FY2026</Label></ParameterValue>
    </ParameterValues></ValidValues>
  </ReportParameter>
</ReportParameters>
```

Child order is fixed: `DataType`, `Nullable`, `DefaultValue`, `AllowBlank`,
`Prompt`, `PromptUser`, `Hidden`, `MultiValue`, `ValidValues`, `UsedInQuery`.
The generator's `_emit_report_parameter` documents this: "The RDL schema fixes
the child order as DataType, Nullable, DefaultValue, AllowBlank, Prompt, ...,
ValidValues; emitting them out of order makes SSRS reject the whole definition
at publish time."

### DataType and Nullable

`DataType` is one of `Boolean`, `DateTime`, `Integer`, `Float`, `String`.

`Nullable` decides whether the user may tick "NULL" in the parameter pane. Set
`Nullable` to `true` for an optional prompt, and give it a null default so the
report renders unattended:

```xml
<ReportParameter Name="Region">
  <DataType>String</DataType>
  <Nullable>true</Nullable>
  <DefaultValue><Values><Value /></Values></DefaultValue>
  <AllowBlank>true</AllowBlank>
  <Prompt>Region</Prompt>
</ReportParameter>
```

Without a default and without `Nullable`, a subscription or an unattended render
fails with `rsReportParameterValueNotSet`. This repository's validator flags that
as `parameter_without_default` at severity `warn`.

### AllowBlank

`AllowBlank` applies only to `String` parameters. It permits the empty string as
a value. A blank default with the implicit `AllowBlank=false` trips the
publish-time check: "The 'AllowBlank' property of report parameter 'X' is false.
However, the 'DefaultValue' property contains a value that violates the
'AllowBlank' property condition." So: if the default may be blank, emit
`<AllowBlank>true</AllowBlank>`.

### DefaultValue: Values versus DataSetReference

```xml
<!-- literal or expression values -->
<DefaultValue><Values><Value>=DateAdd("d", -30, Today())</Value></Values></DefaultValue>

<!-- values drawn from a dataset -->
<DefaultValue><DataSetReference>
  <DataSetName>RegionList</DataSetName>
  <ValueField>RegionCode</ValueField>
</DataSetReference></DefaultValue>
```

`DataSetReference` under `DefaultValue` has `DataSetName` and `ValueField` only.
There is no `LabelField` there, because a default is a value, not a label.

### ValidValues: ParameterValues versus DataSetReference

```xml
<!-- static list, value and display label separate -->
<ValidValues><ParameterValues>
  <ParameterValue><Value>N</Value><Label>North</Label></ParameterValue>
  <ParameterValue><Value>S</Value><Label>South</Label></ParameterValue>
</ParameterValues></ValidValues>

<!-- query-driven list -->
<ValidValues><DataSetReference>
  <DataSetName>RegionList</DataSetName>
  <ValueField>RegionCode</ValueField>
  <LabelField>RegionName</LabelField>
</DataSetReference></ValidValues>
```

The dataset named here must be defined in the same report. It runs before the
parameter is rendered, so it must not itself depend on that parameter.

### MultiValue

```xml
<ReportParameter Name="Region">
  <DataType>String</DataType>
  <Nullable>false</Nullable>
  <DefaultValue><Values><Value>N</Value><Value>S</Value></Values></DefaultValue>
  <AllowBlank>true</AllowBlank>
  <Prompt>Region</Prompt>
  <MultiValue>true</MultiValue>
  <ValidValues><ParameterValues>
    <ParameterValue><Value>N</Value><Label>North</Label></ParameterValue>
    <ParameterValue><Value>S</Value><Label>South</Label></ParameterValue>
    <ParameterValue><Value>E</Value><Label>East</Label></ParameterValue>
  </ParameterValues></ValidValues>
</ReportParameter>
```

Rules for multi-value parameters.

- A multi-value parameter cannot be `Nullable`. Use "Select All" or an explicit
  "(all)" sentinel value instead.
- Every `DefaultValue/Values/Value` entry must appear in `ValidValues`, or Report
  Designer throws a `NullReferenceException` on open. The generator's
  `_emit_report_parameter` emits one `<Value>` per list entry for this reason,
  rather than stringifying the list.
- `MultiValue` goes after `Prompt` and before `ValidValues`.

### How a multi-value parameter binds into IN (@P)

With `CommandType` of `Text`, SSRS expands the parameter into a comma-separated
literal list before sending the query. Write the SQL as if `@Region` were a
single value inside `IN`:

```xml
<Query>
  <DataSourceName>SalesDB</DataSourceName>
  <CommandText>SELECT Region, Amount FROM dbo.Sales WHERE Region IN (@Region)</CommandText>
  <QueryParameters>
    <QueryParameter Name="@Region">
      <Value>=Parameters!Region.Value</Value>
    </QueryParameter>
  </QueryParameters>
</Query>
```

The server sends `WHERE Region IN ('N','S')`. Three consequences.

- This only works with `CommandType` of `Text`. A stored procedure receives a
  single scalar, so you must flatten the list yourself:
  `<Value>=Join(Parameters!Region.Value, ",")</Value>` and split it inside the
  procedure (`STRING_SPLIT` on SQL Server 2016 and later).
- The expansion is textual. A very large selection produces a very large SQL
  statement and can exceed the 2100-parameter or statement-size limits.
- The bare `Parameters!Region.Value` in a report expression is an *array*, not a
  scalar. `=Parameters!Region.Value` in a textbox renders `#Error`; use
  `=Join(Parameters!Region.Value, ", ")` instead.

### Hidden versus Internal

| Visibility | RDL 2016 | Shown in the parameter pane | Settable via URL or subscription |
| --- | --- | --- | --- |
| Visible | no `Hidden` element, or `<Hidden>false</Hidden>` | yes | yes |
| Hidden | `<Hidden>true</Hidden>` with a `Prompt` | no | yes |
| Internal | `<Hidden>true</Hidden>` with no `Prompt` | no | no |

RDL 2005 had a separate `<Internal>` element. RDL 2008 and later dropped it and
express both states through `Hidden`. Use Hidden for a value a drill-through
parent passes in, and Internal for a constant the report author wants to keep out
of the URL.

```xml
<ReportParameter Name="TenantId">
  <DataType>Integer</DataType>
  <Nullable>false</Nullable>
  <DefaultValue><Values><Value>42</Value></Values></DefaultValue>
  <Hidden>true</Hidden>
</ReportParameter>
```

### Cascading parameters

Cascading means the valid values of a child parameter are produced by a dataset
that itself references the parent parameter. The mechanics are entirely
positional and referential.

1. Declare the parent first in `ReportParameters`. Order in that block is the
   evaluation order, and a child that appears before its parent fails with "The
   report parameter 'X' has a DefaultValue or ValidValues that depends on the
   report parameter 'Y'. Forward dependencies are not valid."
2. Give the child's `ValidValues` a `DataSetReference` naming a dataset whose
   query filters on the parent.
3. That dataset declares a `QueryParameter` bound to the parent.

```xml
<DataSets>
  <DataSet Name="CountryList">
    <Query>
      <DataSourceName>SalesDB</DataSourceName>
      <CommandText>SELECT DISTINCT CountryCode, CountryName FROM dbo.DimGeography ORDER BY CountryName</CommandText>
    </Query>
    <Fields>
      <Field Name="CountryCode"><DataField>CountryCode</DataField><rd:TypeName>System.String</rd:TypeName></Field>
      <Field Name="CountryName"><DataField>CountryName</DataField><rd:TypeName>System.String</rd:TypeName></Field>
    </Fields>
  </DataSet>
  <DataSet Name="CityList">
    <Query>
      <DataSourceName>SalesDB</DataSourceName>
      <CommandText>SELECT DISTINCT City FROM dbo.DimGeography WHERE CountryCode = @Country ORDER BY City</CommandText>
      <QueryParameters>
        <QueryParameter Name="@Country"><Value>=Parameters!Country.Value</Value></QueryParameter>
      </QueryParameters>
    </Query>
    <Fields><Field Name="City"><DataField>City</DataField><rd:TypeName>System.String</rd:TypeName></Field></Fields>
  </DataSet>
</DataSets>

<ReportParameters>
  <ReportParameter Name="Country">
    <DataType>String</DataType>
    <Nullable>false</Nullable>
    <AllowBlank>false</AllowBlank>
    <Prompt>Country</Prompt>
    <ValidValues><DataSetReference>
      <DataSetName>CountryList</DataSetName>
      <ValueField>CountryCode</ValueField>
      <LabelField>CountryName</LabelField>
    </DataSetReference></ValidValues>
  </ReportParameter>
  <ReportParameter Name="City">
    <DataType>String</DataType>
    <Nullable>false</Nullable>
    <AllowBlank>false</AllowBlank>
    <Prompt>City</Prompt>
    <MultiValue>true</MultiValue>
    <ValidValues><DataSetReference>
      <DataSetName>CityList</DataSetName>
      <ValueField>City</ValueField>
      <LabelField>City</LabelField>
    </DataSetReference></ValidValues>
  </ReportParameter>
</ReportParameters>
```

Behaviour worth knowing.

- Changing `Country` re-runs `CityList` and clears the current `City` selection.
- A cascading child cannot have a static `DefaultValue` that might not exist in
  the refreshed list. Use a `DataSetReference` default, which picks the first row.
- Deep cascades are slow: each level is a round trip before the pane is usable.

## How this repository reads it back

`rdl_parser._parse_datasets` reads `Query/DataSourceName`, `Query/CommandType`,
`Query/CommandText` and every `QueryParameters/QueryParameter` (`Name` to
`Value`), then each `Fields/Field`: `@Name` becomes the IR column name,
`rd:TypeName` becomes the type, and the presence of a `<Value>` child marks the
field calculated.

`rdl_parser._parse_parameters` reads `@Name`, `DataType`, the first
`DefaultValue//Value` as the current value, and every `ValidValues//Value` as the
allowable list. It reads `Value` elements at any depth, so both the
`ParameterValues` and `DataSetReference` shapes are tolerated, the latter simply
yielding nothing.
