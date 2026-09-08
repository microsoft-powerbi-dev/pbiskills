# Deployment

Getting an `.rdl` onto a report server, pointed at the right database, visible to
the right people, and running on a schedule.

Three deployment paths exist. Pick by server version and by how many items you
are moving.

| Path | Works on | Best for |
| --- | --- | --- |
| REST API v2.0 (`/api/v2.0`) | SSRS 2017+, Power BI Report Server | Scripted deployment, CI/CD, this repository's own connector |
| SOAP (`ReportService2010.asmx`) | SSRS 2008 R2 through 2022, PBIRS | Older servers, and everything the REST API does not expose (subscriptions, policies) |
| `rs.exe` with an RSS script | Any version with the tools installed | Bulk deploy, environment promotion, anything needing SOAP without writing a client |

Visual Studio / SSDT project deployment sits on top of SOAP.

---

## REST API v2.0

The API base lives under the **web portal** virtual directory, not the web
service directory:

```
https://ssrs01.contoso.com/Reports/api/v2.0
```

That trips people up because SOAP lives under `/ReportServer`. This repository
handles it by deriving candidates from either form and probing
(`backend/app/core/report_server/endpoints.py`): given `/ReportServer`, it also
tries the `/Reports` sibling, and vice versa. Site-level installs rename both
directories (`/ReportServer_SQL2019`), so the sibling swap preserves the rename.

Authentication is Windows: NTLM or Negotiate for on-premises, bearer for
token-protected front ends.

### Probe the server

```
GET {api_base}/System
```

Returns product metadata. This repository's `probe()` in `rest_api.py` treats a
404 as "no REST API here" and moves to the next candidate, and it reads
`ProductVersion` plus `ProductType` (falling back to `ProductName`) from the
response.

```bash
API="https://ssrs01.contoso.com/Reports/api/v2.0"
curl --negotiate -u : "${API}/System"
```

```powershell
$api = "https://ssrs01.contoso.com/Reports/api/v2.0"
Invoke-RestMethod -Uri "$api/System" -UseDefaultCredentials |
    Select-Object ProductName, ProductVersion, ReportServerAbsoluteUrl
```

Over plain HTTP, PowerShell 7 needs `-AllowUnencryptedAuthentication`. That
applies to every PowerShell example below.

### List the catalog, flat

```
GET {api_base}/CatalogItems
```

This repository fetches the **entire** catalog in one call and filters locally.
The reason is documented at the top of `rest_api.py`:

> `$filter` support drifts between SSRS 2017, 2019, 2022 and PBIRS releases,
> whereas the flat collection is stable everywhere, and a report estate is small
> enough (thousands of rows) that one round-trip beats N.

The response is OData-shaped, with rows under `value`. Each row carries `Id`,
`Path`, `Name`, `Type`, `Description`, `ModifiedDate`, `ModifiedBy`, `Size` and
`Hidden`. `list_catalog` guards against a pathological catalog with a 20,000-item
cap and logs when it truncates.

```bash
curl --negotiate -u : "${API}/CatalogItems" |
  jq -r '.value[] | select(.Type=="Report") | "\(.Id)  \(.Path)"'
```

### Create a report

```
POST {api_base}/CatalogItems
```

The RDL travels inline as base64 in the `Content` property. The exact body this
repository sends (`create_report` in `rest_api.py`):

```json
{
  "@odata.type": "#Model.Report",
  "Path": "/Sales/Monthly Sales by Region",
  "Name": "Monthly Sales by Region",
  "Type": "Report",
  "Hidden": false,
  "Content": "PD94bWwgdmVyc2lvbj0iMS4wIiA...",
  "ContentType": null
}
```

`@odata.type` of `#Model.Report` is required; without it the server cannot tell
which entity type you are creating. `ContentType` is explicitly `null` for
reports (it carries a MIME type only for `Resource` items).

A `409 Conflict` means an item with that path already exists. This repository
turns that into an actionable message rather than a stack trace:

> A report named 'Monthly Sales by Region' already exists in '/Sales'. Enable
> overwrite to replace it.

```bash
B64=$(base64 -w0 MonthlySalesByRegion.rdl)
printf '{"@odata.type":"#Model.Report","Path":"/Sales/Monthly Sales by Region",
"Name":"Monthly Sales by Region","Type":"Report","Hidden":false,
"Content":"%s","ContentType":null}' "$B64" > body.json

curl --negotiate -u : -H "Content-Type: application/json" \
     -X POST --data @body.json "${API}/CatalogItems"
```

```powershell
$api    = "https://ssrs01.contoso.com/Reports/api/v2.0"
$rdl    = "C:\build\MonthlySalesByRegion.rdl"
$folder = "/Sales"
$name   = [IO.Path]::GetFileNameWithoutExtension($rdl)

$body = @{
    '@odata.type' = '#Model.Report'
    Path          = "$folder/$name"
    Name          = $name
    Type          = 'Report'
    Hidden        = $false
    Content       = [Convert]::ToBase64String([IO.File]::ReadAllBytes($rdl))
    ContentType   = $null
} | ConvertTo-Json

Invoke-RestMethod -Uri "$api/CatalogItems" -Method Post `
    -Body $body -ContentType 'application/json' -UseDefaultCredentials
```

### Overwrite an existing report

```
PUT {api_base}/CatalogItems({id})
```

Same body. The id comes from the catalog listing. This repository's
`create_report` takes `overwrite` plus `existing_id` and switches from `POST` to
`PUT` when both are present.

```powershell
$existing = (Invoke-RestMethod -Uri "$api/CatalogItems" -UseDefaultCredentials).value |
            Where-Object { $_.Path -eq "$folder/$name" }

if ($existing) {
    Invoke-RestMethod -Uri "$api/CatalogItems($($existing.Id))" -Method Put `
        -Body $body -ContentType 'application/json' -UseDefaultCredentials
} else {
    Invoke-RestMethod -Uri "$api/CatalogItems" -Method Post `
        -Body $body -ContentType 'application/json' -UseDefaultCredentials
}
```

An overwrite preserves the item's id, its subscriptions, its security policy and
its data source bindings. That is why `PUT` is the correct promotion mechanism
and delete-then-create is not.

### Download a definition

Two forms, because older PBIRS builds serve one or the other depending on item
type. This repository tries the streaming form first and falls back
(`download_definition` in `rest_api.py`):

```
GET {api_base}/CatalogItems({id})/Content/$value      -> raw bytes
GET {api_base}/CatalogItems({id})                     -> entity, base64 in "Content"
```

```bash
ID="9d3b4c2e-5f11-4a7c-9c2d-70a3e8b21f04"

curl --negotiate -u : -o downloaded.rdl \
     "${API}/CatalogItems(${ID})/Content/\$value"          # streaming form

curl --negotiate -u : "${API}/CatalogItems(${ID})" |
     jq -r '.Content' | base64 -d > downloaded.rdl          # base64 fallback
```

```powershell
try {
    Invoke-WebRequest -Uri "$api/CatalogItems($id)/Content/`$value" `
        -UseDefaultCredentials -OutFile "downloaded.rdl"
} catch {
    $item = Invoke-RestMethod -Uri "$api/CatalogItems($id)" -UseDefaultCredentials
    [IO.File]::WriteAllBytes("downloaded.rdl", [Convert]::FromBase64String($item.Content))
}
```

Always download the current definition before an overwrite. That is your
rollback.

### Item types and their definition extensions

From `backend/app/core/report_server/models.py`:

| Catalog `Type` | Extension |
| --- | --- |
| `Report`, `LinkedReport` | `.rdl` |
| `DataSet` | `.rsd` |
| `DataSource` | `.rds` |

Mobile reports and `.pbix` items appear in the catalog but have no downloadable
definition through this path.

---

## rs.exe with an RSS script

`rs.exe` runs a VB.NET script against the SOAP endpoint with an implicit `rs`
proxy object already connected. It is the right tool for bulk deployment on any
server version and needs no client code.

```powershell
& "C:\Program Files (x86)\Microsoft SQL Server\150\Tools\Binn\rs.exe" `
    -i .\Deploy.rss `
    -s http://ssrs01:8090/ReportServer `
    -e Mgmt2010 `
    -v folder="/Sales" `
    -v sourceDir="C:\build\rdl" `
    -v dataSourcePath="/Data Sources/WideWorldImportersDW"
```

Flags that matter: `-e Mgmt2010` selects the ReportService2010 endpoint (the
default is the older `Mgmt2005`), `-v name=value` passes a variable into the
script, `-l 0` sets an unlimited timeout, `-b` runs as a batch so a failure rolls
everything back.

### A complete bulk-deploy RSS script

```vb
' Deploy.rss - publish every .rdl in a folder and bind its data sources.
' rs.exe -i Deploy.rss -s http://ssrs01:8090/ReportServer -e Mgmt2010
'        -v folder="/Sales" -v sourceDir="C:\build\rdl"
'        -v dataSourcePath="/Data Sources/WideWorldImportersDW"

Public Sub Main()

    Dim warnings As Warning() = Nothing
    Dim files As String() = System.IO.Directory.GetFiles(sourceDir, "*.rdl")
    Dim deployed As Integer = 0

    EnsureFolder(folder)

    For Each file As String In files

        Dim name As String = System.IO.Path.GetFileNameWithoutExtension(file)
        Dim definition As Byte() = System.IO.File.ReadAllBytes(file)

        Console.WriteLine("Publishing {0} to {1}", name, folder)

        Try
            warnings = rs.CreateCatalogItem( _
                "Report", name, folder, True, definition, Nothing, Nothing)

            If Not (warnings Is Nothing) Then
                For Each w As Warning In warnings
                    Console.WriteLine("  WARN {0}: {1}", w.Code, w.Message)
                Next
            End If

            BindDataSource(folder & "/" & name)
            deployed = deployed + 1

        Catch ex As Exception
            Console.WriteLine("  FAILED {0}: {1}", name, ex.Message)
        End Try

    Next

    Console.WriteLine("Deployed {0} of {1} report(s).", deployed, files.Length)

End Sub


' Create the target folder if it does not exist. Idempotent.
Private Sub EnsureFolder(ByVal path As String)
    Dim parent As String = path.Substring(0, path.LastIndexOf("/"))
    Dim leaf   As String = path.Substring(path.LastIndexOf("/") + 1)
    If parent = "" Then parent = "/"

    Try
        rs.CreateFolder(leaf, parent, Nothing)
        Console.WriteLine("Created folder {0}", path)
    Catch ex As Exception
        ' AlreadyExists is the expected case; anything else re-throws.
        If ex.Message.IndexOf("AlreadyExists") < 0 Then Throw
    End Try
End Sub


' Point every data source reference in the report at the shared data source.
Private Sub BindDataSource(ByVal reportPath As String)
    Dim sources As DataSource() = rs.GetItemDataSources(reportPath)
    If sources Is Nothing OrElse sources.Length = 0 Then Return

    For Each src As DataSource In sources
        Dim reference As New DataSourceReference()
        reference.Reference = dataSourcePath
        src.Item = CType(reference, DataSourceDefinitionOrReference)
    Next

    rs.SetItemDataSources(reportPath, sources)
    Console.WriteLine("  bound {0} data source(s) -> {1}", sources.Length, dataSourcePath)
End Sub
```

`CreateCatalogItem`'s fourth argument is `Overwrite`. Passing `True` is what
makes the script re-runnable.

Common additions to this script:

- `rs.SetPolicies(path, policies)` to apply role assignments.
- `rs.CreateDataSource(...)` to create the shared data source itself as the first
  step of a fresh environment build.
- `rs.SetItemParameters(path, params)` to set report-level parameter defaults per
  environment.

---

## Visual Studio / SSDT project deployment

A Report Server Project has deployment properties per configuration (Debug,
Release, or your own DevServer / TestServer / ProdServer):

| Property | Meaning |
| --- | --- |
| `TargetServerURL` | The **web service** URL, `http://ssrs01:8090/ReportServer` |
| `TargetServerVersion` | Must match the server, or publishing fails on schema |
| `TargetFolder` | Catalog folder for reports |
| `TargetDataSourceFolder` | Usually a shared `/Data Sources` folder |
| `TargetDatasetFolder` | Usually a shared `/Datasets` folder |
| `OverwriteDataSources` | **Set to `False` for test and prod.** `True` overwrites the target environment's connection with the developer's |
| `OverwriteDatasets` | `True` is normally fine; shared datasets are code |

The `OverwriteDataSources` setting is where the classic incident comes from: a
developer deploys to production with it `True`, and every report in the folder
now points at the development database. Create one build configuration per
environment and set it `False` everywhere except a first-time environment build.

---

## Folder structure and naming for an estate

Structure by **audience and subject**, not by technology or by author.

```
/
├── Data Sources/                  shared .rds, one per source system per environment
│   ├── WideWorldImportersDW
│   └── FinanceGL
├── Datasets/                      shared .rsd, mostly parameter value lists
│   ├── RegionList
│   └── FiscalPeriodList
├── Finance/
│   ├── Monthly Sales by Region
│   ├── Monthly Sales by Region - Detail      (drillthrough target)
│   └── _Archive/                             hidden, retired reports
├── Operations/
├── Supply Chain/
└── _Shared/                       reports used by several audiences
```

Conventions that pay off:

- **Report name is a business name**, in title case, no version suffix, no
  author initials, no date. Version lives in source control.
- **Drillthrough targets carry a suffix** (`- Detail`, `- Lines`) and sit next to
  their parent, so a relative `ReportName` in the RDL resolves.
- **Prefix folders that are not user destinations with an underscore** so they
  sort to the top and read as infrastructure.
- **Hide drillthrough-only targets** by setting `Hidden` on the catalog item.
  They are still reachable by path; they just do not clutter the folder.
- **One folder per security boundary.** Item-level permissions are far more work
  to audit than folder-level ones.

Names to avoid because they become duplicates: `Sales Report`, `Sales Report v2`,
`Sales Report (Copy)`, `Sales Report FINAL`. This repository's duplicate detector
exists because that pattern is universal; see
`backend/app/core/complexity/duplicates.py`.

---

## Item-level security and role assignments

Report server security is inherited down the folder tree until an item breaks
inheritance. Built-in item roles:

| Role | Grants |
| --- | --- |
| Browser | View reports and folders, subscribe to reports |
| Report Builder | Browser plus view and edit report definitions |
| My Reports | Manage items in the user's own My Reports folder |
| Content Manager | Everything on the item: publish, delete, secure, manage subscriptions |
| Publisher | Publish reports and data sources, but not manage security |

System-level roles (`System User`, `System Administrator`) control server-wide
actions such as managing schedules and viewing the execution log.

Assign to **AD groups**, never to individuals. A group named for the business
role (`CONTOSO\BI-Finance-Readers`) survives staff turnover.

Via `rs.exe`:

```vb
Public Sub Main()
    Dim policies() As Policy
    Dim inherited As Boolean
    policies = rs.GetPolicies("/Finance", inherited)

    Dim newPolicy As New Policy()
    newPolicy.GroupUserName = "CONTOSO\BI-Finance-Readers"
    Dim role As New Role()
    role.Name = "Browser"
    newPolicy.Roles = New Role() { role }

    Dim updated(policies.Length) As Policy
    Array.Copy(policies, updated, policies.Length)
    updated(policies.Length) = newPolicy

    rs.SetPolicies("/Finance", updated)
    Console.WriteLine("Granted Browser on /Finance to BI-Finance-Readers")
End Sub
```

The first `SetPolicies` on an item breaks inheritance from its parent
permanently. Prefer setting policy on the folder and letting everything inside
inherit.

---

## Re-pointing data sources after deployment

Publishing an RDL does **not** update the data source binding of an existing
item. It also does not create a shared data source. Both are separate steps.

**Check what a report is currently bound to**, via `rs.exe`:

```vb
Dim sources As DataSource() = rs.GetItemDataSources("/Finance/Monthly Sales by Region")
For Each s As DataSource In sources
    Console.WriteLine("{0} -> {1}", s.Name, CType(s.Item, DataSourceReference).Reference)
Next
```

**Re-point** with `SetItemDataSources`, as in the `BindDataSource` helper above.

**Re-point at the RDL level** (before publishing) is what this repository's
paginated transform does. `backend/app/core/paginated/transformer.py` walks every
`DataSource`, finds its `ConnectionProperties/ConnectString`, and replaces the
text with the target:

```python
RepointTarget(server="FabricSQL", database="SalesDW").to_connect_string()
# -> "Data Source=FabricSQL;Initial Catalog=SalesDW"
```

If no embedded data source is found, it records a warning, because a report using
a shared `DataSourceReference` has nothing to re-point:

```
[WARN] no_embedded_datasource: No embedded data source found to re-point
       (shared references must be embedded first).
```

That warning is the whole shared-versus-embedded trade-off in one line: shared
data sources are re-pointed on the server, embedded ones are re-pointed in the
file.

---

## Subscriptions

### Standard subscription

One schedule, one fixed set of parameter values, one delivery target.

Delivery extensions: **Report Server Email**, **Windows File Share**, and (SSRS
2019+ / PBIRS) **Null Delivery Provider**, which renders the report to prime the
cache and discards the output.

Requirements:

- The data source must use **stored credentials** or **no credentials**. There is
  no interactive user at 07:00.
- The subscription owner is whoever created it. That identity is what
  `User!UserID` returns during the scheduled run, which matters for row-level
  security. Create subscriptions under a service account.

Practical settings for the worked example:

```
Description   Monthly Sales by Region - Finance PDF
Schedule      Shared schedule "Month End + 2 working days, 07:00"
Delivery      Report Server Email
  To          fin-leadership@contoso.com
  Render      PDF
  Subject     @ReportName executed at @ExecutionTime
  Include     Report as attachment
Parameters    DateFrom = 2026-07-01, DateTo = 2026-07-31, Region = (all)
```

`@ReportName` and `@ExecutionTime` are the only two substitution tokens available
in the subject line.

Prefer a **shared schedule** over a per-subscription one. Fifty subscriptions on
a shared schedule can be paused with a single action.

### Data-driven subscription

A query returns one row per delivery, and every field of the subscription
(recipient, format, parameter values) can be bound to a column of that query.
This is how you send each regional manager only their own region.

Available on Enterprise / Business Intelligence editions of SQL Server, and on
Power BI Report Server.

**The driving-query contract**: the query runs once, against a data source of
your choosing, and must return one row per delivery with a column for every
value you intend to bind.

```sql
SELECT
    m.EmailAddress            AS Recipient,
    m.DisplayName             AS RecipientName,
    m.Region                  AS RegionValue,        -- report parameter
    'PDF'                     AS RenderFormat,       -- delivery setting
    CONVERT(char(10), DATEADD(month, -1, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)), 23)
                              AS DateFromValue,
    CONVERT(char(10), DATEADD(day, -1, DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)), 23)
                              AS DateToValue,
    'Monthly sales for ' + m.Region AS SubjectLine
FROM Ref.RegionManager AS m
WHERE m.IsActive = 1;
```

Binding rules that catch people out:

- Every value is bound as a **string**. Dates must be formatted in a way the
  report parameter's `DateTime` type will parse; ISO `yyyy-MM-dd` is safest.
- A **multi-value** report parameter takes a comma-separated string in the
  driving column. Values containing commas cannot be delivered this way.
- The data source used by the driving query must have stored credentials, same
  as the report's.
- One failed row does not stop the run; failures land in the subscription's
  status and in `ExecutionLog3` as separate rows.
- The whole subscription is one `RSExecutionLog` job. A 500-recipient
  data-driven subscription runs the report 500 times unless the report is cached
  or snapshotted first. Pair it with a null-delivery subscription that runs a few
  minutes earlier to warm the cache.

---

## Snapshots, history and caching

Three different mechanisms, often confused.

| Mechanism | What it stores | When it is used | Set where |
| --- | --- | --- | --- |
| **Cache** | The rendered intermediate format, keyed by parameter combination | Next request with the same parameters, until expiry | Report properties, Caching |
| **Execution snapshot** | One intermediate format for the whole report, at a point in time | Every request, until refreshed | Report properties, Processing Options |
| **History snapshot** | A retained copy of an execution snapshot | On demand, from the History tab | Report properties, History |

**Cache** suits a report with a small number of common parameter combinations.
Expiry can be "after N minutes" or "on a schedule". It requires stored
credentials. A cache miss simply runs the report, so a cold cache is never an
error.

**Execution snapshot** suits an expensive report over data that changes once per
day. Every user gets the same, already-rendered result, instantly. The
consequence: parameters that filter the query cannot vary, because the snapshot
is one fixed execution. Parameters that filter only within the report layout can
still vary.

**History** suits a reporting obligation: keep the month-end report as it was
produced at month end, even after the data is restated. Retention is set per
item or from the server default.

Cache refresh plans (SSRS 2017+) pre-warm the cache on a schedule, which is the
clean answer to "the first user each morning waits 90 seconds".

---

## Environment promotion

Three environments, one definition, three data source bindings.

```
DEV   /Finance   -> /Data Sources/WWIDW  ->  sqldev01.WideWorldImportersDW
TEST  /Finance   -> /Data Sources/WWIDW  ->  sqltest01.WideWorldImportersDW
PROD  /Finance   -> /Data Sources/WWIDW  ->  sqlprod01.WideWorldImportersDW
```

The rules that make this work:

1. **The shared data source has the same name and path in every environment.**
   Then the RDL's `DataSourceReference` is identical everywhere and nothing in
   the file is environment specific.
2. **The connection string lives on the server, not in the file.** It is set once
   per environment when the environment is built.
3. **Never deploy data sources with the reports.** `OverwriteDataSources` is
   `False` in the SSDT project for test and prod; the `rs.exe` script binds the
   reference but does not create or modify the target.
4. **Promotion is `PUT`, not delete and create.** The item id survives, so
   subscriptions, security and history survive.
5. **The definition promoted to prod is byte-identical to the one tested.** If
   anything in the file must change per environment, that is a design defect;
   move it to a server-side setting or a parameter default.

A minimal promotion pipeline:

```powershell
param(
    [Parameter(Mandatory)][string]$ApiBase,     # https://host/Reports/api/v2.0
    [Parameter(Mandatory)][string]$SourceDir,
    [Parameter(Mandatory)][string]$TargetFolder
)

$catalog = (Invoke-RestMethod -Uri "$ApiBase/CatalogItems" -UseDefaultCredentials).value

Get-ChildItem -Path $SourceDir -Filter *.rdl | ForEach-Object {

    $name = $_.BaseName
    $path = "$TargetFolder/$name"
    $body = @{
        '@odata.type' = '#Model.Report'
        Path          = $path
        Name          = $name
        Type          = 'Report'
        Hidden        = $false
        Content       = [Convert]::ToBase64String([IO.File]::ReadAllBytes($_.FullName))
        ContentType   = $null
    } | ConvertTo-Json

    $existing = $catalog | Where-Object { $_.Path -eq $path }

    if ($existing) {
        # back up first: this is the rollback artifact
        Invoke-WebRequest -Uri "$ApiBase/CatalogItems($($existing.Id))/Content/`$value" `
            -UseDefaultCredentials -OutFile "$SourceDir\rollback\$name.rdl"

        Invoke-RestMethod -Uri "$ApiBase/CatalogItems($($existing.Id))" -Method Put `
            -Body $body -ContentType 'application/json' -UseDefaultCredentials
        Write-Host "Updated  $path"
    }
    else {
        Invoke-RestMethod -Uri "$ApiBase/CatalogItems" -Method Post `
            -Body $body -ContentType 'application/json' -UseDefaultCredentials
        Write-Host "Created  $path"
    }
}
```

### Post-deployment verification

Do all four, every time:

1. `GET /CatalogItems` and confirm `ModifiedDate` moved.
2. `rs.GetItemDataSources` and confirm the binding points at this environment.
3. Render the report **on the server** with production parameters, not in the
   designer. The designer uses your credentials; the server uses the data
   source's.
4. Confirm subscriptions still exist and their next run time is in the future.
