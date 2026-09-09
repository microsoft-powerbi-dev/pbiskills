# Write guardrails and safety posture

Unlike `sql-server-schema` (no write tool, no flag that adds one), this
engine genuinely writes to the database: `meta.run_log`/`run_detail`/
`run_dataset_key` bookkeeping, `CREATE OR ALTER VIEW gen.*`, and the config
loader's upsert into `meta.feed`/`dataset`/`field_map`/`dataset_lookup`. The
safety posture here is therefore an **allow-list for what may be written**,
not a blanket refusal — the mechanical backstop behind the project rule
"never drop or delete without asking."

## `classify_write_statement(sql) -> WriteVerdict`

`guardrails.py`'s central function. `WriteVerdict` is
`(allowed: bool, kind: str, reason: Optional[str])`, where `kind` is one of
`config_write | run_bookkeeping | view_ddl | forbidden | multi | empty`.

Classification order, exactly as implemented:

1. Empty or whitespace-only SQL → refused (`kind="empty"`).
2. The fixed identity-read suffix
   (`; SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS id`, always exactly this
   text — never attacker- or config-influenced) is stripped **before**
   anything else runs, so it is never counted as a second statement.
3. More than one `;`-separated non-empty statement remaining → refused
   (`kind="multi"`) — this is what stops
   `"INSERT ...; DROP TABLE ..."` from riding in behind a legitimate insert.
4. `CREATE OR ALTER VIEW gen.\w+ AS SELECT ...` → **allowed**
   (`kind="view_ddl"`), checked *before* the general forbidden-construct scan
   below, because it is the one legitimate DDL statement this engine ever
   sends and carving an exception into the forbidden regex itself would be
   fragile.
5. A hard-forbidden construct anywhere in the statement → refused
   (`kind="forbidden"`), regardless of what table it names:
   `TRUNCATE`, `DROP`, `EXEC`/`EXECUTE`, any `xp_...` or `sp_...` procedure
   call (note: `sp_executesql` is *not* exempted — it is still caught by the
   `EXEC` pattern separately, since `EXEC sp_executesql ...` matches
   `\bEXEC(?:UTE)?\b` first), `BACKUP`, `RESTORE`, `SHUTDOWN`, `RECONFIGURE`,
   `GRANT`, `REVOKE`, `DENY`, and any `ALTER` not already permitted by the
   `CREATE OR ALTER VIEW` exception above (so `ALTER TABLE ...` on any
   schema, including `meta.*` itself, is refused).
6. A bare `DELETE FROM <table>` with no `WHERE` at all, or a `WHERE` that is
   not a single `<col> = ?` equality (e.g. a compound `WHERE ... AND ...`),
   or any `TRUNCATE TABLE` → refused unconditionally
   (`_bare_delete_or_truncate`), independent of which table it names.
7. Otherwise, the statement must match `INSERT INTO`, `UPDATE ... SET`, or
   the single-equality `DELETE FROM ... WHERE <col> = ?` shape. If it
   doesn't match any of the three, it's refused (not one of
   INSERT/UPDATE/DELETE/CREATE OR ALTER VIEW).
8. The matched table name (brackets stripped, lower-cased) is checked
   against two fixed tuples:
   - `_RUN_BOOKKEEPING_TABLES`: `meta.run_log`, `meta.run_detail`,
     `meta.run_dataset_key` → allowed, `kind="run_bookkeeping"`.
   - `_CONFIG_WRITE_TABLES`: `meta.feed`, `meta.dataset`, `meta.field_map`,
     `meta.dataset_lookup`, `meta.feed_config_version` → allowed,
     `kind="config_write"`.
   - Any other table → refused, naming the allow-list in the reason.

`tests/test_guardrails.py` is written as an adversarial corpus specifically:
bare deletes, truncates, drops, `xp_cmdshell`, comment-hidden DDL
(`-- harmless\nDROP TABLE ...` — comments are stripped by `strip_sql_noise`
before matching, so this is still caught), a view targeting the wrong schema
(`src.v_evil`), and a `CREATE OR ALTER PROCEDURE` (not a view, so not exempt)
are all in the refused corpus.

## Two connection contexts, deliberately named apart

`connection.py` (this skill's own, not `sql-server-schema`'s) exposes:

- **`read_connection()`** — same rollback-always shape as
  `sql-server-schema`'s `connection()`: unconditionally rolled back on exit,
  regardless of whether an exception occurred. Used for every `meta.*` read
  and for the config loader's lookup-table-existence check.
- **`write_connection()`** — the opposite default: commits on a clean exit,
  rolls back on an exception. Used only for the narrow write surface
  `classify_write_statement` actually permits — nothing here sends
  arbitrary SQL through `write_connection`; every call site's SQL text is
  one of the shapes described above by construction, not by a runtime check
  on every statement (the run/checkpoint/config-loader code paths simply
  never build anything else). `deploy_views` is the one place that *also*
  calls `classify_write_statement` explicitly before sending, as a second,
  independent check on generated DDL text.

The two functions are named differently specifically so neither is ever
mistaken for the other at a call site — there is no shared `connection()`
that takes a "read-only" flag.

`connection.execute_insert_return_identity` deserves a specific note: a
parameterized `INSERT` runs via `sp_prepare`/`sp_execute` (an RPC call), and
SQL Server treats each RPC as a new scope — so a *separate*
`SELECT SCOPE_IDENTITY()` call afterward reliably returns `NULL` even though
the insert succeeded (confirmed empirically against LocalDB). The fix is to
keep the INSERT and the identity read in the same batch, then advance with
`cursor.nextset()`. `@@IDENTITY` was considered and rejected: it ignores
scope and can return the wrong value if a trigger fired an identity insert
of its own.

## Reused, unmodified, from `sql-server-schema`

`guardrails.py` re-exports `sql-server-schema`'s `resolve_artifact_path`,
`classify_column_sensitivity`, `redact_value`, `value_looks_sensitive`,
`normalize_identifier`, `strip_sql_noise`, `sample_allowlist_ok`,
`default_artifact_root`, `find_repo_root`, and `HARD_DENY` verbatim — this
engine writes artifacts that name real customer schema too (generated view
DDL from `extract explain --out`, benchmark/findings numbers were the design
doc's intent even though the `benchmark` command itself does not exist), and
they have exactly the same public-repo-safety problem `sql-server-schema`
already solved. `resolve_artifact_path` in particular refuses to write inside
the repository — the same refusal `sql-server-schema` relies on to stop a
schema pack naming an internal database from ever landing in this public
repo.

## What this does *not* cover

- No statement-level guardrail exists for the source database read side —
  `read_connection()` has no `classify_statement`/SELECT-only refusal the
  way `sql-server-schema`'s `connection()` does, because every read query
  this engine issues is built by `query_builder`/`execution_sql` from
  validated metadata, not accepted as free text from a caller. There is no
  `run_query`-style free-SQL tool anywhere in this skill.
- `classify_write_statement` is a text-shape classifier, not a SQL parser.
  It relies on the fact that every call site constructs its SQL from a small,
  fixed set of code paths (never string-concatenating caller-supplied SQL
  text as a *statement*); it is a backstop against a construction mistake or
  a stacked-statement injection landing in one of those fixed shapes, not a
  general-purpose SQL sandbox.
