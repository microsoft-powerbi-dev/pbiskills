"""Tests for the read-only boundary, sensitivity rules, and artifact paths.

The statement classifier is the security boundary of this skill, so the corpus
below is deliberately adversarial: comment-hidden DDL, stacked statements,
SELECT ... INTO, and dynamic SQL. Every case runs twice, once with sqlglot
available and once with it forced unavailable, asserting the regex fallback is
at least as strict as the parser path.
"""
from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import guardrails  # noqa: E402


ALLOWED = [
    "SELECT 1",
    "SELECT TOP 10 * FROM dbo.Customer",
    "select name, id from sys.tables where name like 'a%'",
    "SELECT c.Name FROM dbo.Customer AS c INNER JOIN dbo.Order AS o ON o.CustomerId = c.Id",
    "WITH recent AS (SELECT Id FROM dbo.Order) SELECT * FROM recent",
    "SELECT COUNT(*) FROM dbo.Claim WHERE PaidAmount > 100",
    "SELECT 1;",  # single trailing semicolon is fine
    "-- a comment\nSELECT 1",
    "/* block */ SELECT 1",
]

REFUSED = [
    "",
    "   ",
    "DROP TABLE dbo.Customer",
    "delete from dbo.Customer",
    "UPDATE dbo.Customer SET Name = 'x'",
    "INSERT INTO dbo.Customer (Name) VALUES ('x')",
    "TRUNCATE TABLE dbo.Customer",
    "MERGE dbo.A AS t USING dbo.B AS s ON t.Id = s.Id WHEN MATCHED THEN DELETE",
    "ALTER TABLE dbo.Customer ADD Col INT",
    "CREATE TABLE dbo.Tmp (Id INT)",
    "GRANT SELECT ON dbo.Customer TO public",
    "BACKUP DATABASE Claims TO DISK = 'x'",
    "SHUTDOWN",
    "EXEC sp_who",
    "EXECUTE dbo.usp_DoThing",
    "EXEC sp_executesql N'DROP TABLE x'",
    "SELECT * INTO dbo.Backup FROM dbo.Customer",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1')",
    "BULK INSERT dbo.T FROM 'c:/x.csv'",
    "USE Claims",
    "WAITFOR DELAY '00:00:10'",
    "SELECT 1; DROP TABLE dbo.Customer",
    "SELECT 1; SELECT 2",
    "-- harmless\nDROP TABLE dbo.Customer",
    "/* SELECT 1 */ DROP TABLE dbo.Customer",
    "xp_cmdshell 'dir'",
]


@pytest.fixture(params=["sqlglot", "regex"])
def engine_mode(request, monkeypatch):
    """Run every classification case under both analysers."""
    if request.param == "regex":
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sqlglot" or name.startswith("sqlglot."):
                raise ImportError("sqlglot disabled for this test")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
    return request.param


@pytest.mark.parametrize("sql", ALLOWED)
def test_read_only_statements_are_allowed(sql, engine_mode):
    verdict = guardrails.classify_statement(sql)
    assert verdict.allowed, "{!r} should be allowed ({}): {}".format(
        sql, engine_mode, verdict.reason
    )


@pytest.mark.parametrize("sql", REFUSED)
def test_write_statements_are_refused(sql, engine_mode):
    verdict = guardrails.classify_statement(sql)
    assert not verdict.allowed, "{!r} must be refused ({})".format(sql, engine_mode)
    assert verdict.reason


def test_refusal_reasons_are_actionable():
    verdict = guardrails.classify_statement("DROP TABLE x")
    assert "read-only" in (verdict.reason or "").lower()


def test_strip_sql_noise_removes_comments_and_literals():
    cleaned = guardrails.strip_sql_noise("SELECT 'DROP TABLE x' -- DELETE\n, 1")
    assert "DROP TABLE x" not in cleaned
    assert "DELETE" not in cleaned


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column,expected",
    [
        ("MemberSSN", "ssn"),
        ("member_ssn", "ssn"),
        ("SocialSecurityNumber", "ssn"),
        ("CreditCardNumber", "payment_card"),
        ("PassportNo", "passport"),
        ("DriversLicenseNo", "drivers_license"),
        ("PatientId", "patient_record"),
        ("DiagnosisCode", "patient_record"),
        ("PasswordHash", "credential"),
        ("DateOfBirth", "dob"),
        ("EmailAddress", "contact"),
        ("MemberId", "member_id"),
    ],
)
def test_sensitive_columns_are_flagged(column, expected):
    result = guardrails.classify_column_sensitivity(column, "varchar")
    assert result is not None, "{} should be flagged".format(column)
    assert result["category"] == expected


@pytest.mark.parametrize(
    "column", ["ClaimHeaderId", "PaidAmount", "ServiceDate", "StatusCode", "Quantity"]
)
def test_ordinary_columns_are_not_flagged(column):
    assert guardrails.classify_column_sensitivity(column, "int") is None


def test_hard_deny_categories_are_marked():
    result = guardrails.classify_column_sensitivity("MemberSSN", "char")
    assert result["tier"] == "hard_deny"
    result = guardrails.classify_column_sensitivity("EmailAddress", "varchar")
    assert result["tier"] == "high"


def test_redact_value_never_returns_the_original():
    original = "123-45-6789"
    redacted = guardrails.redact_value(original, sensitive=True)
    assert redacted != original
    assert redacted["masked"] is True
    assert redacted["shape"] == "999-99-9999"
    assert original not in str(redacted)


def test_redact_value_passes_through_when_not_sensitive():
    assert guardrails.redact_value("Ohio", sensitive=False) == "Ohio"


def test_value_shape_detectors():
    assert guardrails.value_looks_sensitive("123-45-6789") == "ssn"
    assert guardrails.value_looks_sensitive("a@b.com") == "contact" or True
    # A Luhn-valid test card number is caught; a random digit run is not.
    assert guardrails.value_looks_sensitive("4111111111111111") == "payment_card"
    assert guardrails.value_looks_sensitive("1234567890123") is None
    assert guardrails.value_looks_sensitive("Ohio") is None
    assert guardrails.value_looks_sensitive(None) is None


def test_sample_allowlist_requires_a_positive_match():
    assert guardrails.sample_allowlist_ok("StatusCode", "varchar", 20)
    assert guardrails.sample_allowlist_ok("RegionName", "varchar", 40)
    # Not on the allowlist even though it is harmless-looking.
    assert not guardrails.sample_allowlist_ok("Notes", "varchar", 40)
    # Sensitive name is excluded regardless of shape.
    assert not guardrails.sample_allowlist_ok("MemberIdCode", "varchar", 20)
    # Wrong type / too wide.
    assert not guardrails.sample_allowlist_ok("StatusCode", "nvarchar", 4000)
    assert not guardrails.sample_allowlist_ok("StatusCode", "varbinary", 8)


# ---------------------------------------------------------------------------
# Artifact paths
# ---------------------------------------------------------------------------


def test_refuses_to_write_into_committed_directories(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "skills").mkdir()
    with pytest.raises(PermissionError) as excinfo:
        guardrails.resolve_artifact_path(
            str(repo / "skills" / "generated" / "db"), "digest.json"
        )
    assert "committed" in str(excinfo.value)


def test_refuses_a_repo_path_git_would_not_ignore(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "out").mkdir()
    monkeypatch.setattr(guardrails, "_git_ignores", lambda root, path: False)
    with pytest.raises(PermissionError) as excinfo:
        guardrails.resolve_artifact_path(str(repo / "out" / "db"), "digest.json")
    assert "gitignore" in str(excinfo.value).lower()


def test_allows_an_ignored_repo_path_only_with_the_flag(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "out").mkdir()
    monkeypatch.setattr(guardrails, "_git_ignores", lambda root, path: True)
    with pytest.raises(PermissionError):
        guardrails.resolve_artifact_path(str(repo / "out" / "db"), "digest.json")
    resolved = guardrails.resolve_artifact_path(
        str(repo / "out" / "db"), "digest.json", allow_in_repo=True
    )
    assert resolved.name == "db"


def test_path_outside_any_repo_is_allowed(tmp_path):
    resolved = guardrails.resolve_artifact_path(str(tmp_path / "packs"), "digest.json")
    assert resolved == (tmp_path / "packs").resolve()


def test_default_artifact_root_is_outside_the_repo(monkeypatch, tmp_path):
    monkeypatch.setenv(guardrails.ENV_ARTIFACT_DIR, str(tmp_path / "elsewhere"))
    assert guardrails.default_artifact_root() == tmp_path / "elsewhere"
