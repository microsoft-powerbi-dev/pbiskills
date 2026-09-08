"""
guardrails.py - the read-only boundary, sensitivity classification, and the
refusal to write generated artifacts anywhere git would pick them up.

Three independent concerns live here because they are the three ways this skill
can do damage: run a statement that changes data, surface a value that is
somebody's protected health information, or commit an internal database's shape
to a public repository.

On the read-only side, note the division of labour. ``classify_statement`` is a
heuristic and is treated as one: its job is to give a clear error before a bad
statement is sent. The actual guarantee is the unconditional ``rollback()`` in
``connection.connection``. Parsing can be fooled; a transaction that never
commits cannot.

The sqlglot-first, regex-always structure deliberately mirrors
``reportlineage/sql_refs.py``, which solves the same problem for lineage
extraction and already carries sqlglot as an optional dependency.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Statement classification
# ---------------------------------------------------------------------------

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")

# Any of these appearing as a bare word vetoes the statement, whatever sqlglot
# concluded. Ordering is irrelevant; the first match wins and is reported.
_FORBIDDEN = (
    ("insert", r"\bINSERT\b"),
    ("update", r"\bUPDATE\b"),
    ("delete", r"\bDELETE\b"),
    ("merge", r"\bMERGE\b"),
    ("truncate", r"\bTRUNCATE\b"),
    ("drop", r"\bDROP\b"),
    ("create", r"\bCREATE\b"),
    ("alter", r"\bALTER\b"),
    ("grant", r"\bGRANT\b"),
    ("revoke", r"\bREVOKE\b"),
    ("deny", r"\bDENY\b"),
    ("backup", r"\bBACKUP\b"),
    ("restore", r"\bRESTORE\b"),
    ("shutdown", r"\bSHUTDOWN\b"),
    ("reconfigure", r"\bRECONFIGURE\b"),
    ("waitfor", r"\bWAITFOR\b"),
    ("exec", r"\bEXEC(?:UTE)?\b"),
    ("dynamic_sql", r"\bsp_executesql\b"),
    ("extended_proc", r"\bxp_\w+"),
    ("system_proc", r"\bsp_(?!helptext\b)\w+"),
    ("select_into", r"\bSELECT\b[\s\S]*?\bINTO\b\s+(?!\s*\()"),
    ("openrowset", r"\bOPENROWSET\b"),
    ("opendatasource", r"\bOPENDATASOURCE\b"),
    ("openquery", r"\bOPENQUERY\b"),
    ("bulk_insert", r"\bBULK\s+INSERT\b"),
    ("use_database", r"\bUSE\s+\w"),
)

_FORBIDDEN_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _FORBIDDEN
)


@dataclass(frozen=True)
class StatementVerdict:
    """The result of deciding whether one SQL string is a safe read."""

    allowed: bool
    kind: str  # select | cte_select | multi | forbidden | empty | unparsed
    reason: Optional[str] = None
    engine: str = "regex"  # which analyser produced the verdict
    statement_count: int = 1


def strip_sql_noise(sql: str) -> str:
    """Remove comments and string literals so keywords cannot hide inside them.

    Comments first: ``-- harmless\\nDROP TABLE x`` must not read as a comment.
    String literals second, so a literal containing the word DELETE does not
    trip the classifier.
    """
    without_block = _BLOCK_COMMENT.sub(" ", sql or "")
    without_line = _LINE_COMMENT.sub(" ", without_block)
    return _STRING_LITERAL.sub("''", without_line)


def _count_statements(cleaned: str) -> int:
    """Count statements by semicolon, ignoring a single trailing one."""
    parts = [part for part in cleaned.split(";") if part.strip()]
    return len(parts)


def classify_statement(sql: str) -> StatementVerdict:
    """Decide whether ``sql`` is a single, read-only SELECT.

    Returns a verdict rather than raising, so the caller can put the reason in
    a tool response. ``allowed=True`` means: one statement, rooted at SELECT or
    a WITH whose body is a SELECT, containing none of the forbidden keywords.

    When sqlglot is unavailable or cannot parse the input, the policy gets
    *stricter*, not looser: an unparseable fragment is refused.
    """
    cleaned = strip_sql_noise(sql or "").strip()
    if not cleaned:
        return StatementVerdict(False, "empty", "No SQL was supplied.")

    count = _count_statements(cleaned)
    if count > 1:
        return StatementVerdict(
            False,
            "multi",
            "Only one statement may be run at a time; found {}.".format(count),
            statement_count=count,
        )

    # The regex veto runs regardless of what any parser concludes.
    for name, pattern in _FORBIDDEN_COMPILED:
        if pattern.search(cleaned):
            return StatementVerdict(
                False,
                "forbidden",
                "Statement contains a forbidden construct ({}). This server is "
                "read-only: only a single SELECT is permitted.".format(name),
            )

    engine = "regex"
    kind = "select"
    try:
        import sqlglot
        from sqlglot import expressions as exp

        engine = "sqlglot"
        parsed = sqlglot.parse(cleaned, read="tsql")
        statements = [item for item in parsed if item is not None]
        if len(statements) > 1:
            return StatementVerdict(
                False,
                "multi",
                "Only one statement may be run at a time; found {}.".format(
                    len(statements)
                ),
                engine=engine,
                statement_count=len(statements),
            )
        if not statements:
            return StatementVerdict(
                False, "unparsed", "Statement could not be parsed.", engine=engine
            )
        root = statements[0]
        if isinstance(root, exp.Select):
            kind = "select"
        elif isinstance(root, exp.With) or root.args.get("with"):
            # A CTE is fine as long as what it feeds is a SELECT.
            if isinstance(root, exp.Select) or isinstance(
                root.this if hasattr(root, "this") else None, exp.Select
            ):
                kind = "cte_select"
            else:
                return StatementVerdict(
                    False,
                    "forbidden",
                    "A WITH clause is only allowed when its body is a SELECT.",
                    engine=engine,
                )
        else:
            return StatementVerdict(
                False,
                "forbidden",
                "Root of the statement is {}, not SELECT.".format(
                    type(root).__name__.upper()
                ),
                engine=engine,
            )
    except ImportError:
        engine = "regex"
    except Exception:
        # A parse failure is not a licence to proceed.
        return StatementVerdict(
            False,
            "unparsed",
            "Statement could not be parsed as T-SQL; refusing to run it.",
            engine="sqlglot",
        )

    if engine == "regex":
        # Without a parser, demand the statement visibly start with SELECT or WITH.
        if not re.match(r"^\s*(?:WITH\b[\s\S]*?\bSELECT\b|SELECT\b)", cleaned, re.IGNORECASE):
            return StatementVerdict(
                False,
                "forbidden",
                "Statement must begin with SELECT (or a WITH clause feeding a "
                "SELECT). Install sqlglot for more precise classification.",
                engine=engine,
            )
        kind = "cte_select" if re.match(r"^\s*WITH\b", cleaned, re.IGNORECASE) else "select"

    return StatementVerdict(True, kind, None, engine=engine)


# ---------------------------------------------------------------------------
# Column sensitivity
# ---------------------------------------------------------------------------

# Categories in HARD_DENY are never sampled and never profiled by value, and
# asking for them explicitly is refused rather than silently skipped. The
# organisation policy on SSN, payment cards, passports, licence numbers and
# patient records is absolute, so there is no "just skip it" mode for these.
HARD_DENY: FrozenSet[str] = frozenset(
    {
        "ssn",
        "national_id",
        "payment_card",
        "passport",
        "drivers_license",
        "patient_record",
        "credential",
    }
)

# Identifiers are normalized to underscore-separated lower case before matching,
# and `_` is a word character, so \b does not fire at a separator: \bemail\b
# does not match "email_address". These lookarounds treat any non-alphanumeric
# character (including the separator) as a boundary, which is what an
# identifier-aware word match needs.
_B = r"(?<![a-z0-9])"  # start of an identifier word
_E = r"(?![a-z0-9])"  # end of an identifier word


def _word(*words: str) -> str:
    """A pattern matching any of ``words`` as a whole identifier word."""
    return "|".join("{}{}{}".format(_B, word, _E) for word in words)


_SENSITIVE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("ssn", _word("ssn", "socsec") + r"|social.?sec|ss_?num"),
    ("national_id", _word("nric", "nin", "tin") + r"|aadhaar|aadhar|pan_?(?:no|num|card)|tax_?id"),
    ("payment_card", _word("ccnum", "cvv", "iban") + r"|credit.?card|card.?(?:num|no)|routing.?(?:num|no)|bank.?acc|account.?(?:num|no)"),
    ("passport", r"passport"),
    ("drivers_license", _word("dl") + r"|driver.?s?.?lic|dl_?(?:no|num)|licen[cs]e.?(?:no|num)"),
    ("patient_record", _word("mrn", "cpt", "npi", "rx") + r"|patient|medical.?rec|icd\d*|diagnos|prescri"),
    ("credential", _word("pwd", "secret", "token", "salt") + r"|password|passwd|api.?key|pass.?hash"),
    ("dob", _word("dob", "birthday") + r"|date.?of.?birth|birth.?d(?:ate|t)"),
    ("person_name", r"first.?name|last.?name|sur.?name|middle.?name|full.?name|maiden"),
    ("contact", _word("email", "phone", "mobile", "fax") + r"|cell.?(?:num|no|phone)|e_?mail"),
    ("address", _word("street", "zip", "city") + r"|address|postal"),
    ("member_id", r"member.?(?:id|no|num)|subscriber.?(?:id|no)|policy.?(?:no|num)|beneficiar"),
    ("demographic", _word("gender", "sex", "race") + r"|ethnic|marital|religio|citizen|veteran|disab"),
    ("financial", _word("wage", "income") + r"|salary|compensat|net.?pay"),
)

_SENSITIVE_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _SENSITIVE_PATTERNS
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalize_identifier(name: str) -> str:
    """Lower-case an identifier and turn camelCase into underscore boundaries.

    ``MemberSSN``, ``member_ssn`` and ``MEMBER SSN`` must all classify the same.
    """
    spaced = _CAMEL_BOUNDARY.sub("_", name or "")
    return re.sub(r"[\s\-]+", "_", spaced).lower()


def classify_column_sensitivity(
    column_name: str, data_type: str = ""
) -> Optional[Dict[str, str]]:
    """Flag a column as a likely PII or PHI carrier, from its name.

    This is a *candidate* classifier and a safety net, not a compliance
    control. It reduces accidental exposure; it does not certify a column as
    safe. A column it does not flag can still hold sensitive data.
    """
    normalized = normalize_identifier(column_name)
    for category, pattern in _SENSITIVE_COMPILED:
        if pattern.search(normalized):
            return {
                "category": category,
                "tier": "hard_deny" if category in HARD_DENY else "high",
            }
    return None


# Value-shape detectors. These catch a sensitive value in a column whose name
# gave nothing away, which is the case the name-based rules cannot see.
_VALUE_SHAPES: Tuple[Tuple[str, str], ...] = (
    ("ssn", r"^\d{3}-\d{2}-\d{4}$"),
    ("email", r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    ("phone", r"^\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$"),
    ("payment_card", r"^\d{13,19}$"),
)

_VALUE_SHAPES_COMPILED = tuple(
    (name, re.compile(pattern)) for name, pattern in _VALUE_SHAPES
)


def _luhn_valid(digits: str) -> bool:
    total, alternate = 0, False
    for char in reversed(digits):
        if not char.isdigit():
            return False
        value = int(char)
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def value_looks_sensitive(value: Any) -> Optional[str]:
    """Return a category when a *value* looks sensitive regardless of its column."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 64:
        return "free_text"
    for category, pattern in _VALUE_SHAPES_COMPILED:
        if pattern.match(text):
            if category == "payment_card":
                # A long digit string is only interesting if it checksums.
                return "payment_card" if _luhn_valid(text) else None
            return category
    return None


def redact_value(value: Any, sensitive: bool) -> Any:
    """Return the value, or a shape descriptor when the column is sensitive.

    A shape descriptor carries the *pattern* (length, digit layout) and never
    the value, which is enough for an agent to understand a column's format
    without the data ever leaving the database.
    """
    if not sensitive:
        return value
    if value is None:
        return None
    text = str(value)
    return {
        "masked": True,
        "len": len(text),
        "shape": re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "a", text))[:32],
    }


# ---------------------------------------------------------------------------
# Where artifacts may be written
# ---------------------------------------------------------------------------

ENV_ARTIFACT_DIR = "SQLSERVER_MCP_ARTIFACT_DIR"
ENV_ALLOW_REPO_WRITES = "SQLSERVER_MCP_ALLOW_REPO_WRITES"

# Writing a generated pack into any of these is refused unconditionally: they
# are the directories that actually get committed.
_NEVER_WRITE = ("skills", "mcp", "docs")


def default_artifact_root() -> Path:
    """Where generated digests and skill packs go when no path is given.

    Outside the repository by default, because a generated pack names internal
    servers, databases, tables and columns, and this repository is public.
    """
    explicit = (os.environ.get(ENV_ARTIFACT_DIR) or "").strip()
    if explicit:
        return Path(explicit)
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or str(Path.home() / ".local" / "share")
    )
    return Path(base) / "sqlserver-schema-mcp"


def find_repo_root(start: Path) -> Optional[Path]:
    """Walk up from ``start`` looking for a .git directory."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_ignores(repo_root: Path, path: Path) -> bool:
    """Ask git whether it would ignore this path. A real check, not a convention."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=str(repo_root),
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_artifact_path(
    out_path: Optional[str], default_name: str, *, allow_in_repo: bool = False
) -> Path:
    """Resolve where an artifact may be written, refusing unsafe destinations.

    Refuses, in order: a path inside ``skills/``, ``mcp/`` or ``docs/`` of a
    git repository (unconditionally, since those are what get committed), and a
    path anywhere else inside a git work tree that git would not ignore
    (unless ``allow_in_repo`` and the path is genuinely ignored).
    """
    target = (
        Path(out_path).expanduser()
        if out_path
        else default_artifact_root() / default_name
    )
    target = target.resolve()

    repo_root = find_repo_root(target.parent if target.suffix else target)
    if repo_root is None:
        return target

    try:
        relative = target.relative_to(repo_root)
    except ValueError:
        return target

    top = relative.parts[0] if relative.parts else ""
    if top in _NEVER_WRITE:
        raise PermissionError(
            "Refusing to write to {}: {}/ is committed to this repository and a "
            "generated pack contains internal database identifiers. Write it "
            "outside the repository (the default is {}).".format(
                target, top, default_artifact_root()
            )
        )

    if not _git_ignores(repo_root, target):
        raise PermissionError(
            "Refusing to write to {}: it is inside a git work tree and is not "
            "gitignored. Add it to .gitignore, or write outside the repository "
            "(the default is {}).".format(target, default_artifact_root())
        )

    if not allow_in_repo:
        raise PermissionError(
            "{} is gitignored but still inside the repository. Pass "
            "allow_in_repo=True to write there anyway.".format(target)
        )
    return target


def sample_allowlist_ok(column_name: str, data_type: str, max_length: int) -> bool:
    """Whether a column is eligible for value sampling at all.

    An allowlist, not a denylist: a column must positively look like a low
    cardinality code or status field to qualify. Anything unrecognised is
    excluded, which is the correct default when the cost of a false negative is
    a leaked identifier.
    """
    if classify_column_sensitivity(column_name, data_type):
        return False
    allowed_types = {
        "int", "bigint", "smallint", "tinyint", "bit", "date",
        "char", "varchar", "nchar", "nvarchar",
    }
    if (data_type or "").lower() not in allowed_types:
        return False
    if max_length and max_length > 64:
        return False
    return bool(
        re.search(
            r"(status|state|type|category|class|code|group|kind|level|flag"
            r"|^is_|^has_|region|country|currency|unit|frequency|method"
            r"|source|reason|priority|severity|tier|segment)",
            normalize_identifier(column_name),
        )
    )
