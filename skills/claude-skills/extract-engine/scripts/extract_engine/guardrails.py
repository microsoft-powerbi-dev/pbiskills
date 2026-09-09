"""
guardrails.py - the extract engine's write boundary and public-repo safety.

Reuses ``sql-server-schema``'s ``resolve_artifact_path`` (and the sensitivity/
redaction helpers) unmodified: this engine writes artifacts that name real
customer schema and data shape too (generated view DDL, Devin context packs,
benchmark/findings numbers), and they have exactly the same public-repo-safety
problem sql-server-schema already solved.

What is new here is ``classify_write_statement``. This engine, unlike
sql-server-schema, genuinely writes to the database - so it needs an allow-list
for what it may write, not a SELECT-only refusal. The allow-list is narrow and
enumerated, not a general "anything but DROP" rule: this is the mechanical
backstop behind the project's "never drop or delete without asking" rule.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_SQL_SERVER_SCHEMA_SCRIPTS = (
    Path(__file__).resolve().parents[3] / "sql-server-schema" / "scripts"
)
if str(_SQL_SERVER_SCHEMA_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SQL_SERVER_SCHEMA_SCRIPTS))

from guardrails import (  # noqa: E402  (re-exported, unmodified)
    HARD_DENY,
    classify_column_sensitivity,
    default_artifact_root,
    find_repo_root,
    normalize_identifier,
    redact_value,
    resolve_artifact_path,
    sample_allowlist_ok,
    strip_sql_noise,
    value_looks_sensitive,
)

__all__ = [
    "HARD_DENY",
    "classify_column_sensitivity",
    "default_artifact_root",
    "find_repo_root",
    "normalize_identifier",
    "redact_value",
    "resolve_artifact_path",
    "sample_allowlist_ok",
    "strip_sql_noise",
    "value_looks_sensitive",
    "classify_write_statement",
    "WriteVerdict",
]


@dataclass(frozen=True)
class WriteVerdict:
    """The result of deciding whether one SQL statement may be sent."""

    allowed: bool
    kind: str  # config_write | run_bookkeeping | view_ddl | forbidden | multi | empty
    reason: Optional[str] = None


# Every table this engine is ever permitted to write to, and how.
_CONFIG_WRITE_TABLES = (
    "meta.feed",
    "meta.dataset",
    "meta.field_map",
    "meta.dataset_lookup",
    "meta.feed_config_version",
)
_RUN_BOOKKEEPING_TABLES = (
    "meta.run_log",
    "meta.run_detail",
    "meta.run_dataset_key",
)

# The exact, fixed suffix connection.execute_insert_return_identity appends
# to every INSERT it runs (never attacker- or config-influenced text, just a
# hardcoded literal), so it is recognized here as part of one logical write
# rather than tripping the "only one statement at a time" rule that exists to
# catch genuine stacked-statement injection.
_IDENTITY_SUFFIX = re.compile(
    r";\s*SELECT\s+CAST\(SCOPE_IDENTITY\(\)\s+AS\s+BIGINT\)\s+AS\s+id\s*$", re.IGNORECASE
)

_INSERT_INTO = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][\w\.\[\]]*)", re.IGNORECASE)
_UPDATE = re.compile(r"^\s*UPDATE\s+([A-Za-z_][\w\.\[\]]*)\s+SET\s", re.IGNORECASE)
_DELETE = re.compile(
    # Anchored to end-of-string (aside from trailing whitespace): exactly one
    # equality filter and nothing else. "WHERE dataset_id = ? AND x > ?" must
    # NOT match this - a compound WHERE is not "a single scoped delete".
    r"^\s*DELETE\s+FROM\s+([A-Za-z_][\w\.\[\]]*)\s+WHERE\s+\w+\s*=\s*\?\s*$",
    re.IGNORECASE,
)
_CREATE_OR_ALTER_VIEW = re.compile(
    r"^\s*CREATE\s+OR\s+ALTER\s+VIEW\s+gen\.\w+\s+AS\s+SELECT\b", re.IGNORECASE | re.DOTALL
)

# Absolute refusals, checked after the CREATE OR ALTER VIEW exception (see
# classify_write_statement) so that legitimate ALTER usage isn't caught by the
# same net that blocks arbitrary ALTER TABLE/DATABASE/anything-else DDL.
_ALWAYS_FORBIDDEN = re.compile(
    r"\bTRUNCATE\b|\bDROP\b|\bEXEC(?:UTE)?\b|\bxp_\w+|\bsp_(?!executesql\b)\w+"
    r"|\bBACKUP\b|\bRESTORE\b|\bSHUTDOWN\b|\bRECONFIGURE\b|\bGRANT\b|\bREVOKE\b|\bDENY\b"
    r"|\bALTER\b",  # any ALTER not already permitted by the CREATE OR ALTER VIEW exception
    re.IGNORECASE,
)


def _strip_brackets(name: str) -> str:
    return name.replace("[", "").replace("]", "")


def _bare_delete_or_truncate(cleaned: str) -> bool:
    """A DELETE with no WHERE at all, or a WHERE that isn't a single ?-bound
    equality filter, is a bare delete: refused unconditionally regardless of
    which table it names."""
    if re.match(r"^\s*DELETE\s+FROM\b", cleaned, re.IGNORECASE) and not _DELETE.match(cleaned):
        return True
    return bool(re.search(r"\bTRUNCATE\s+TABLE\b", cleaned, re.IGNORECASE))


def classify_write_statement(sql: str) -> WriteVerdict:
    """Decide whether one write statement is on the engine's narrow allow-list.

    Permits exactly: parameterized INSERT into the run-bookkeeping tables or
    the config tables; a status/timestamp UPDATE on the bookkeeping tables
    filtered by a single ``WHERE col = ?``; a scoped ``DELETE ... WHERE
    dataset_id = ?`` against the config tables (the loader's reinsert path);
    and ``CREATE OR ALTER VIEW gen.*``. A bare DELETE, TRUNCATE, DROP, or any
    other DDL/admin statement is refused unconditionally, regardless of which
    table it names - this is the code-level backstop behind "never drop or
    delete without asking".
    """
    cleaned = strip_sql_noise(sql or "").strip()
    if not cleaned:
        return WriteVerdict(False, "empty", "No SQL was supplied.")

    # Strip the identity-read suffix, if present, before counting statements
    # or matching the INSERT pattern - it is not a second, independent
    # statement for classification purposes, it is part of how one INSERT
    # reports back the row it just created.
    cleaned = _IDENTITY_SUFFIX.sub("", cleaned).rstrip()

    statements = [part for part in cleaned.split(";") if part.strip()]
    if len(statements) > 1:
        return WriteVerdict(
            False, "multi", "Only one statement may be sent at a time; found {}.".format(len(statements))
        )

    # CREATE OR ALTER VIEW gen.* is the one legitimate DDL statement this
    # engine sends, so it's recognized before the general ALTER/DDL refusal
    # net rather than trying to carve an exception into that net's regex.
    if _CREATE_OR_ALTER_VIEW.match(cleaned):
        return WriteVerdict(True, "view_ddl")

    if _ALWAYS_FORBIDDEN.search(cleaned):
        return WriteVerdict(
            False,
            "forbidden",
            "Statement contains a construct this engine never sends "
            "(DROP/TRUNCATE/EXEC/xp_/sp_/BACKUP/RESTORE/ALTER/admin DDL).",
        )

    if _bare_delete_or_truncate(cleaned):
        return WriteVerdict(
            False,
            "forbidden",
            "A DELETE must be a single, parameterized 'WHERE <col> = ?' scoped "
            "to one table; a bare DELETE or TRUNCATE is refused unconditionally.",
        )

    match = _INSERT_INTO.match(cleaned) or _UPDATE.match(cleaned) or _DELETE.match(cleaned)
    if not match:
        return WriteVerdict(
            False, "forbidden", "Statement is not one of INSERT/UPDATE/DELETE/CREATE OR ALTER VIEW."
        )

    table = _strip_brackets(match.group(1)).lower()
    if table in _RUN_BOOKKEEPING_TABLES:
        return WriteVerdict(True, "run_bookkeeping")
    if table in _CONFIG_WRITE_TABLES:
        return WriteVerdict(True, "config_write")

    return WriteVerdict(
        False,
        "forbidden",
        "Table {!r} is not on the write allow-list ({}).".format(
            table, ", ".join(_CONFIG_WRITE_TABLES + _RUN_BOOKKEEPING_TABLES)
        ),
    )
