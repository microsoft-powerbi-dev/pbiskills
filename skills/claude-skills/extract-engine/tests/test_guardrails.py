"""Tests for the extract engine's write-statement allow-list.

This is the security boundary of the whole engine, so the corpus below is
adversarial on purpose: bare deletes, truncates, drops, admin DDL, and
statements naming a table that isn't on the allow-list.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import guardrails  # noqa: E402


ALLOWED = [
    ("INSERT INTO meta.run_log (feed_id, execution_mode) VALUES (?, ?)", "run_bookkeeping"),
    ("INSERT INTO meta.run_detail (run_id, dataset_id, status) VALUES (?, ?, ?)", "run_bookkeeping"),
    ("INSERT INTO meta.run_dataset_key (run_id, dataset_id, key_value) VALUES (?, ?, ?)", "run_bookkeeping"),
    ("UPDATE meta.run_log SET status = ?, completed_at = ? WHERE run_id = ?", "run_bookkeeping"),
    ("UPDATE meta.run_detail SET status = ? WHERE run_detail_id = ?", "run_bookkeeping"),
    ("DELETE FROM meta.run_detail WHERE run_id = ?", "run_bookkeeping"),
    ("INSERT INTO meta.feed (feed_name, source_server, source_database, output_root) VALUES (?, ?, ?, ?)", "config_write"),
    ("INSERT INTO meta.field_map (dataset_id, ordinal, target_column) VALUES (?, ?, ?)", "config_write"),
    ("DELETE FROM meta.field_map WHERE dataset_id = ?", "config_write"),
    ("DELETE FROM meta.dataset_lookup WHERE dataset_id = ?", "config_write"),
    ("CREATE OR ALTER VIEW gen.v_Claim AS SELECT 1 AS ClaimNumber", "view_ddl"),
    ("create or alter view gen.v_member as select member_id from src.member", "view_ddl"),
]

REFUSED = [
    "",
    "   ",
    "DROP TABLE meta.run_log",
    "TRUNCATE TABLE meta.run_log",
    "DELETE FROM meta.run_log",  # bare delete, no WHERE at all
    "DELETE FROM meta.field_map",  # bare delete on an allowed table is still refused
    "DELETE FROM meta.field_map WHERE dataset_id = ? AND ordinal > ?",  # not a single equality filter
    "EXEC sp_who",
    "EXEC sp_executesql N'DROP TABLE meta.run_log'",
    "xp_cmdshell 'dir'",
    "BACKUP DATABASE X TO DISK = 'x'",
    "GRANT SELECT ON meta.run_log TO public",
    "ALTER TABLE src.claim ADD Col INT",  # not scoped to meta.*
    "ALTER DATABASE X SET SINGLE_USER",
    "INSERT INTO src.claim (x) VALUES (?)",  # not on the allow-list at all
    "INSERT INTO meta.run_log (a) VALUES (?); DROP TABLE meta.run_log",  # stacked
    "SELECT * FROM meta.run_log",  # not a write at all
    "-- harmless\nDROP TABLE meta.run_log",  # comment-hidden
    "CREATE OR ALTER VIEW src.v_evil AS SELECT 1",  # wrong schema
    "CREATE OR ALTER PROCEDURE gen.p_x AS SELECT 1",  # not a view
]


@pytest.mark.parametrize("sql,expected_kind", ALLOWED)
def test_allowed_write_statements(sql, expected_kind):
    verdict = guardrails.classify_write_statement(sql)
    assert verdict.allowed, "{!r} should be allowed: {}".format(sql, verdict.reason)
    assert verdict.kind == expected_kind


@pytest.mark.parametrize("sql", REFUSED)
def test_refused_write_statements(sql):
    verdict = guardrails.classify_write_statement(sql)
    assert not verdict.allowed, "{!r} must be refused".format(sql)


def test_refusal_names_no_table_it_should_not_confirm_exists():
    # A refused DROP shouldn't need to say anything about the table beyond
    # what the caller already typed - no extra confirmation of shape.
    verdict = guardrails.classify_write_statement("DROP TABLE meta.run_log")
    assert verdict.reason


def test_identity_read_suffix_is_recognized_as_part_of_one_insert():
    """connection.execute_insert_return_identity appends a fixed, code-owned
    suffix (never attacker- or config-influenced text) to read back the row
    it just inserted; this must not trip the stacked-statement refusal."""
    sql = (
        "INSERT INTO meta.run_log (feed_id) VALUES (?); "
        "SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS id"
    )
    verdict = guardrails.classify_write_statement(sql)
    assert verdict.allowed
    assert verdict.kind == "run_bookkeeping"


def test_a_genuinely_stacked_second_statement_is_still_refused():
    """The identity-suffix carve-out must not become a general 'anything
    after a semicolon is fine' loophole."""
    sql = "INSERT INTO meta.run_log (feed_id) VALUES (?); DROP TABLE meta.run_log"
    verdict = guardrails.classify_write_statement(sql)
    assert not verdict.allowed


def test_reused_helpers_are_importable_and_unmodified():
    """Confirm the sql-server-schema reuse actually wired up."""
    assert guardrails.resolve_artifact_path is not None
    assert guardrails.classify_column_sensitivity("MemberSSN", "char")["category"] == "ssn"
    assert guardrails.redact_value("123-45-6789", sensitive=True)["masked"] is True
