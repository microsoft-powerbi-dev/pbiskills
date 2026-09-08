# The read-only boundary

This server cannot write to your database. That is arranged four ways, and the
layers are not equally strong: know which one is actually load-bearing.

## Layer 1: no write path exists

There is no `execute_sql` tool, no `mssql_execute_statement`, and no
`SQLSERVER_MCP_ALLOW_WRITES` flag for someone to discover later. The only tool
that takes arbitrary SQL is `mssql_run_query`, and it refuses anything that is
not a single SELECT.

This is the layer that matters most in practice, because it means the MCP
client's tool list never advertises a write capability to the model in the
first place. `tests/test_tools.py::test_no_write_tool_is_exposed` asserts it.

## Layer 2: the transaction never commits

Every connection is opened `autocommit=False`, and `connection.connection`
issues an unconditional `rollback()` in a `finally` block. Nothing in this
skill calls `commit()`.

**This is the actual guarantee.** Statement parsing is a heuristic and can in
principle be fooled; a transaction that never commits cannot be. If something
ever did slip past the classifier, it would still be undone.

`tests/test_connection.py::test_connection_rolls_back_and_never_commits` and
its sibling for the exception path assert this.

## Layer 3: statement classification

`guardrails.classify_statement` decides whether a string is a single read-only
SELECT. Its job is to produce a clear error *before* a bad statement is sent,
not to be the security boundary.

It works in this order:

1. **Strip comments and string literals.** Comments first, so
   `-- harmless\nDROP TABLE x` cannot hide behind one. String literals second,
   so a literal containing the word DELETE does not trip a false positive.
2. **Require exactly one statement.** More than one, by semicolon or by
   sqlglot's parse, is refused. This is what blocks
   `SELECT 1; DROP TABLE Claim`.
3. **Run the keyword veto**, always, regardless of what any parser concluded:
   `INSERT UPDATE DELETE MERGE TRUNCATE DROP CREATE ALTER GRANT REVOKE DENY
   BACKUP RESTORE SHUTDOWN RECONFIGURE WAITFOR EXEC EXECUTE sp_executesql xp_*
   sp_* OPENROWSET OPENDATASOURCE OPENQUERY BULK INSERT USE`, plus
   `SELECT ... INTO`.
4. **Require a SELECT root.** With sqlglot installed the check is structural:
   the parsed root must be a `Select`, or a `With` whose body is one. Without
   sqlglot it falls back to requiring the text to begin with `SELECT` or a
   `WITH` clause feeding one.

Two design choices worth naming:

- **A parser verdict cannot override the keyword veto.** sqlglot saying
  "this is a Select" does not rescue a statement containing `EXEC`.
- **Failing to parse fails closed.** An unparseable fragment is refused, not
  waved through. Without sqlglot the policy gets *stricter*, never looser, and
  `tests/test_guardrails.py` runs the whole corpus twice to prove it.

The sqlglot-first, regex-always structure deliberately matches
`reportlineage/sql_refs.py`, which solves the same problem for lineage
extraction and already carries sqlglot as an optional dependency.

## Layer 4: blast radius

Even a legitimate SELECT should not be able to hurt a production server.

| Control | Value | Why |
| --- | --- | --- |
| `SET LOCK_TIMEOUT` | 5000 ms | Never wait more than five seconds on a lock |
| Isolation level | `READ UNCOMMITTED` | Do not take shared locks on production tables to read metadata |
| Query timeout | 30 s, configurable | A runaway query is cancelled, not left running |
| Row cap | 100 default, 1000 hard maximum | `fetchmany`, so the server stops sending |
| Binary columns | Reported as `{"binary": true, "bytes": N}` | A `varbinary(max)` column cannot flood the response |
| `APP=` | `sqlserver-schema-mcp` | A DBA can identify and kill these sessions |

Row limiting uses `cursor.fetchmany(n)` rather than injecting `TOP (n)` into
your SQL. Rewriting a user's statement is its own bug surface, and the response
reports `truncated: true` so nobody mistakes a capped result for a complete
one.

## What is still your responsibility

- **A SELECT can still be expensive.** The guardrails stop writes and cap
  results; they do not stop you asking for a cartesian product across two large
  tables. Check `mssql_table_stats` before querying something unfamiliar.
- **`READ UNCOMMITTED` means dirty reads.** Fine for schema and approximate
  counts, wrong for anything you intend to reconcile. Payloads built on it are
  marked `approximate: true`.
- **The classifier is a heuristic.** It is well tested, and layer 2 stands
  behind it, but do not treat "the tool accepted it" as proof a statement is
  harmless.

## Testing the boundary

The classifier corpus lives in `tests/test_guardrails.py` and includes
comment-hidden DDL, stacked statements, `SELECT ... INTO`, CTE-wrapped writes,
`sp_executesql`, `xp_cmdshell`, `OPENROWSET`, and `BULK INSERT`, alongside
legitimate multi-CTE analytic SELECTs that must be *allowed*. If you extend the
classifier, add to the corpus first.

To watch the guard fire:

```
mssql_run_query(sql="DROP TABLE dbo.Anything")
-> {"ok": false, "kind": "forbidden",
    "error": "Statement contains a forbidden construct (drop). This server is
              read-only: only a single SELECT is permitted."}
```
