# Connecting to an on-premises SQL Server with Windows credentials

## The authentication model

This skill authenticates one way: Windows Integrated Auth, as whoever launched
the process. There is no password parameter, no `SQLSERVER_MCP_PASSWORD`
variable, no `.env` loading, and no secrets file. `connection.assert_no_credentials`
runs on every connection string built here and raises if `PWD`, `Password`,
`UID`, or `User Id` appears, so a future edit that adds SQL authentication
fails a test rather than shipping quietly.

Two consequences worth being explicit about:

- **The MCP server must run in your own session.** It inherits your Kerberos
  ticket. A Windows service under a different account would authenticate as
  that account, not as you. Claude Desktop, VS Code, and Claude Code all launch
  it as the logged-in user, which is exactly what makes this work.
- **There is no double hop.** The connection is laptop to SQL Server directly,
  so Kerberos delegation and SPN-for-delegation problems do not arise. A
  missing SPN can still downgrade the connection to NTLM, which
  `mssql_test_connection` will show you.

In the connection string this is `Trusted_Connection=yes`. Note that
`Integrated Security=SSPI` is the ADO.NET spelling and is ignored by ODBC:
using it silently produces an anonymous login attempt.

## Choosing a driver

Nothing here hardcodes a driver version. `connection.pick_driver` walks a
preference list from newest to oldest and takes the first one actually
installed:

```
ODBC Driver 18 for SQL Server
ODBC Driver 17 for SQL Server
ODBC Driver 13.1 / 13 / 11 for SQL Server
SQL Server Native Client 11.0
SQL Server                      (legacy, present on every Windows install)
```

Check what a machine has before assuming:

```bash
python skills/claude-skills/sql-server-schema/scripts/cli.py drivers
```

Set `SQLSERVER_MCP_DRIVER` to pin one explicitly. If the named driver is not
installed, that is an error rather than a silent fallback, so a pin cannot
quietly stop meaning what it said.

## Encryption, and the driver 18 trap

This is the single most common cause of a connection that worked in SSMS and
fails here.

**ODBC Driver 18 changed the default** to `Encrypt=yes` *with* certificate
chain validation. Most on-premises SQL Servers present a self-signed
certificate, which fails that validation:

```
SSL Provider: The certificate chain was issued by an authority that is not trusted
```

Driver 17 and older default to `Encrypt=no` and never hit it. So the same code
that works on a machine with driver 17 fails on a machine with driver 18, for
reasons that have nothing to do with the code.

`SQLSERVER_MCP_ENCRYPT` controls the policy:

| Value | Driver 18+ emits | Driver 17 and older emit | When to use |
| --- | --- | --- | --- |
| `auto` (default) | `Encrypt=yes;TrustServerCertificate=yes` | nothing | On-premises with a self-signed certificate. The wire is encrypted; the chain is not validated |
| `strict` | `Encrypt=yes;TrustServerCertificate=no` | `Encrypt=yes;TrustServerCertificate=no` | Once a CA-issued certificate is installed on the SQL host. This is the target state |
| `off` | `Encrypt=no;TrustServerCertificate=yes` | same | Legacy servers that cannot negotiate TLS at all |

`auto` is a deliberate trade, not an oversight: it keeps traffic encrypted
while accepting an unverified certificate, which is strictly better than the
`Encrypt=no` that driver 17 defaults to today. Move to `strict` when the
certificate story on the server is fixed.

`mssql_test_connection` reports the driver and encryption mode it actually
used, so the setting is never invisible.

## Naming the server

`normalize_target` accepts, and round-trips:

| Form | Meaning |
| --- | --- |
| `SQLPROD01` | Default instance, port 1433 |
| `SQLPROD01\SQL2019` | Named instance, resolved through the SQL Browser service |
| `SQLPROD01,1433` | Explicit TCP port. Note the **comma**, not a colon |
| `tcp:SQLPROD01,1433` | Same, with an explicit protocol prefix |

A named instance and an explicit port together is rejected, because a named
instance is *resolved to* a port: supplying both is a contradiction rather than
extra safety.

**If a named instance will not connect**, the usual cause is that SQL Browser
(UDP 1434) is blocked by a firewall. The fix is to use `HOST,PORT` with the
instance's static TCP port instead. `mssql_test_connection` says this in its
error hint for SQLSTATE 08001.

## Where the target comes from

Environment variables supply the default; every tool takes an optional
`server=` and `database=` that override it for one call.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SQLSERVER_MCP_SERVER` | none, required | Default host |
| `SQLSERVER_MCP_DATABASE` | `master` | Default catalog |
| `SQLSERVER_MCP_DRIVER` | auto | Pin an exact ODBC driver name |
| `SQLSERVER_MCP_ENCRYPT` | `auto` | See the table above |
| `SQLSERVER_MCP_LOGIN_TIMEOUT` | `10` | Seconds to wait for a login |
| `SQLSERVER_MCP_QUERY_TIMEOUT` | `30` | Seconds to wait for a query |
| `SQLSERVER_MCP_MAX_ROWS` | `100` | Row ceiling, hard-capped at 1000 |
| `SQLSERVER_MCP_ISOLATION` | `READ UNCOMMITTED` | Session isolation level |
| `SQLSERVER_MCP_READONLY_INTENT` | unset | Add `ApplicationIntent=ReadOnly` |
| `SQLSERVER_MCP_ALLOWED_SERVERS` | unset | Comma-separated allowlist of hosts |
| `SQLSERVER_MCP_ARTIFACT_DIR` | `%LOCALAPPDATA%\sqlserver-schema-mcp` | Where digests and packs are written |

The reason the default lives in the environment rather than in a committed
config file: this repository is public, and a server name in `.mcp.json` would
be committed. Keeping it in the environment also keeps it out of tool-call
transcripts. `SQLSERVER_MCP_ALLOWED_SERVERS` exists so an agent cannot point
the tool at an arbitrary host; the refusal message reports a hash, not the
hostname it rejected.

`ApplicationIntent=ReadOnly` is opt-in rather than default. Against an
Availability Group listener it routes to a readable secondary and takes load
off the primary, which is what you want. Against a standalone instance it is
meaningless, and on some AG configurations it hard-fails, so defaulting it on
would break more setups than it helps.

## Session settings

Every connection runs these immediately after connecting:

```sql
SET LOCK_TIMEOUT 5000;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
```

`READ UNCOMMITTED` is deliberate. These are metadata reads against a live
production database, and taking shared locks on a large table to learn its
shape is not a reasonable trade. Dirty reads do not matter for catalog
metadata, and every payload built on approximate data reports
`"approximate": true`. Override with `SQLSERVER_MCP_ISOLATION` if your
environment has read-committed snapshot isolation on and you would rather use
it.

The connection string also sets `APP=sqlserver-schema-mcp`, so a DBA can find
and, if necessary, kill these sessions:

```sql
SELECT session_id, login_name, program_name, status
FROM sys.dm_exec_sessions
WHERE program_name = 'sqlserver-schema-mcp';
```

## Diagnosing a failure

Work outward from the driver. **Before blaming Python**, confirm the same thing
with the tool that ships with the driver:

```bash
sqlcmd -S <server> -d <database> -E -Q "SELECT @@VERSION, SUSER_SNAME()"
```

`-E` is Windows Integrated Auth. If that fails, the problem is Kerberos, a
firewall, SQL Browser, or a missing SQL login, and nothing in this skill will
fix it.

Then:

```bash
python skills/claude-skills/sql-server-schema/scripts/cli.py test-connection --server <server>
```

The field to read is `auth_scheme`:

| Value | Meaning |
| --- | --- |
| `KERBEROS` | Integrated auth, working properly |
| `NTLM` | Integrated auth, but Kerberos was unavailable. Usually a missing or wrong SPN on the SQL service account. Works, but worth fixing |
| `SQL` | A SQL login was used. This skill cannot produce this, so it means something else made the connection |
| `null` with `missing_permissions` | Your login lacks `VIEW SERVER STATE`, so this cannot be confirmed. It does not mean auth failed |

## Common errors

| SQLSTATE | Symptom | Cause and fix |
| --- | --- | --- |
| `IM002` | Data source name not found and no default driver specified | The named driver is not installed. Run the `drivers` command |
| `28000` | Login failed for user 'DOMAIN\user' | The Windows account has no SQL login, or has no permission on the database |
| `28000` | Login failed for user 'NT AUTHORITY\ANONYMOUS LOGON' | Kerberos could not be used and the connection fell back to an anonymous NTLM attempt. Usually a missing SPN |
| `08001` | Could not open a connection | Host unreachable, or a named instance whose SQL Browser is firewalled. Try `HOST,PORT` |
| `01000` / TLS error | Certificate chain not trusted | Driver 18 with a self-signed certificate. Use `SQLSERVER_MCP_ENCRYPT=auto` (the default) |
| `HYT00` | Timeout expired | Raise `SQLSERVER_MCP_LOGIN_TIMEOUT` or `SQLSERVER_MCP_QUERY_TIMEOUT` |
| Empty table list, no error | The database looks empty | SQL Server trims metadata by permission: objects you have no rights on are invisible rather than forbidden. "Not found" can mean "not permitted" |
