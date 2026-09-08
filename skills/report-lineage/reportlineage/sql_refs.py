"""
Lightweight T-SQL table-reference extraction.

Shared by the SSIS ``.dtsx`` parser (OLE DB SQL commands, Execute SQL tasks)
and the RDL lineage builder (dataset ``CommandText``). This is deliberately a
heuristic regex pass, not a full T-SQL grammar: it recognizes the statement
forms that appear in real source estates (``SELECT ... FROM/JOIN``,
``INSERT INTO ... SELECT``, ``MERGE INTO ... USING``, ``UPDATE``,
``DELETE FROM``, ``CREATE VIEW/TABLE``, ``EXEC proc``) and classifies each
referenced object as a read, a write, or a procedure call.

Anything it cannot resolve statically (dynamic ``EXEC(@sql)`` /
``sp_executesql``) is flagged via ``SqlRefs.dynamic`` plus ``problems`` so the
caller can emit a diagnostic instead of silently dropping lineage.

An optional :mod:`sqlglot` parse (dialect ``tsql``) runs first for the
DML/DDL read/write surface when the package is installed; the regex path is
the fallback when sqlglot is unavailable or the fragment does not parse, and
always drives ``EXEC`` and dynamic-SQL detection. Nothing here requires
sqlglot: the regex path works completely standalone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# A 1-, 2-, or 3-part identifier: bare word or [bracketed name], dot-separated.
_PART = r"(?:\[[^\]\r\n]+\]|[A-Za-z_@#][\w$]*)"
_NAME = rf"({_PART}(?:\s*\.\s*{_PART}){{0,2}})"

# Statement-shape patterns. Order does not imply precedence; each is scanned
# independently and the operation is fixed per pattern.
_RE_FROM = re.compile(rf"\bFROM\s+{_NAME}", re.IGNORECASE)
_RE_JOIN = re.compile(rf"\bJOIN\s+{_NAME}", re.IGNORECASE)
_RE_USING = re.compile(rf"\bUSING\s+{_NAME}", re.IGNORECASE)        # MERGE ... USING
_RE_INSERT = re.compile(rf"\bINSERT\s+INTO\s+{_NAME}", re.IGNORECASE)
_RE_SELECT_INTO = re.compile(rf"\bSELECT\b.*?\bINTO\s+{_NAME}", re.IGNORECASE | re.DOTALL)
_RE_UPDATE = re.compile(rf"\bUPDATE\s+{_NAME}", re.IGNORECASE)
_RE_DELETE = re.compile(rf"\bDELETE\s+FROM\s+{_NAME}", re.IGNORECASE)
_RE_MERGE = re.compile(rf"\bMERGE\s+(?:INTO\s+)?{_NAME}", re.IGNORECASE)
_RE_CREATE = re.compile(rf"\bCREATE\s+(?:OR\s+ALTER\s+)?(?:TABLE|VIEW)\s+{_NAME}", re.IGNORECASE)
_RE_EXEC = re.compile(rf"\bEXEC(?:UTE)?\s+{_NAME}", re.IGNORECASE)

# Dynamic / un-resolvable constructs.
_RE_DYNAMIC = re.compile(r"\b(?:EXEC(?:UTE)?\s*\(|sp_executesql|EXEC(?:UTE)?\s+@)", re.IGNORECASE)

# Comment strippers so commented-out SQL does not produce phantom references.
_RE_LINE_COMMENT = re.compile(r"--[^\r\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Tokens that look like names but are SQL noise after FROM/JOIN.
_KEYWORD_STOP = {"select", "where", "set", "values", "with", "as", "on"}


@dataclass(frozen=True)
class TableRef:
    """A reference to a database object, normalized to ``schema.table``."""

    table: str
    schema: Optional[str] = None
    database: Optional[str] = None
    kind: str = "table"          # "table" | "view" | "stored_procedure"
    operation: str = "read"      # "read" | "write" | "exec"

    def signature(self) -> str:
        """Cross-tool join key ``database::schema.table`` (case-folded).

        Schema defaults to ``dbo`` (SQL Server default) so an unqualified name
        on one side still matches a ``dbo``-qualified name on the other.
        """
        db = (self.database or "").strip().lower()
        sch = (self.schema or "dbo").strip().lower()
        return f"{db}::{sch}.{self.table.strip().lower()}"

    @property
    def qualified_name(self) -> str:
        sch = self.schema or "dbo"
        return f"{sch}.{self.table}"


@dataclass
class SqlRefs:
    reads: List[TableRef] = field(default_factory=list)
    writes: List[TableRef] = field(default_factory=list)
    execs: List[TableRef] = field(default_factory=list)
    dynamic: bool = False
    problems: List[str] = field(default_factory=list)

    @property
    def all(self) -> List[TableRef]:
        return [*self.reads, *self.writes, *self.execs]


def _split_dotted(raw: str) -> List[str]:
    """Split a dotted identifier on top-level dots, preserving bracketed parts."""
    out: List[str] = []
    cur: List[str] = []
    depth = 0
    for ch in raw:
        if ch == "[":
            depth += 1
            cur.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "." and depth == 0:
            out.append("".join(cur))
            cur = []
        elif ch.isspace() and depth == 0:
            continue
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p for p in out if p]


def _strip_brackets(part: str) -> str:
    p = part.strip()
    if p.startswith("[") and p.endswith("]"):
        return p[1:-1]
    return p


def split_object_name(raw: str) -> Tuple[Optional[str], Optional[str], str]:
    """``raw`` (1-, 2- or 3-part) becomes ``(database, schema, table)``."""
    parts = [_strip_brackets(p) for p in _split_dotted(raw)]
    parts = [p for p in parts if p]
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    if len(parts) == 1:
        return None, None, parts[0]
    return None, None, raw.strip()


def make_table_ref(
    raw: str,
    database: Optional[str] = None,
    operation: str = "read",
    kind: str = "table",
) -> Optional[TableRef]:
    """Build a :class:`TableRef` from a raw (possibly bracketed) object name."""
    db, schema, table = split_object_name(raw)
    if not table or table.lower() in _KEYWORD_STOP:
        return None
    return TableRef(
        table=table,
        schema=schema,
        database=db or database,
        kind=kind,
        operation=operation,
    )


def _collect(pattern: "re.Pattern", sql: str, database: Optional[str], operation: str, kind: str) -> List[TableRef]:
    refs: List[TableRef] = []
    for m in pattern.finditer(sql):
        ref = make_table_ref(m.group(1), database=database, operation=operation, kind=kind)
        if ref is not None:
            refs.append(ref)
    return refs


def _dedupe(refs: List[TableRef]) -> List[TableRef]:
    seen = set()
    out: List[TableRef] = []
    for r in refs:
        if r.signature() not in seen:
            seen.add(r.signature())
            out.append(r)
    return out


def _regex_reads_writes(
    clean: str, database: Optional[str]
) -> Tuple[List[TableRef], List[TableRef]]:
    """The regex-heuristic read/write extraction over comment-free SQL."""
    writes: List[TableRef] = []
    writes += _collect(_RE_INSERT, clean, database, "write", "table")
    writes += _collect(_RE_SELECT_INTO, clean, database, "write", "table")
    writes += _collect(_RE_UPDATE, clean, database, "write", "table")
    writes += _collect(_RE_DELETE, clean, database, "write", "table")
    writes += _collect(_RE_MERGE, clean, database, "write", "table")
    writes += _collect(_RE_CREATE, clean, database, "write", "view")

    reads: List[TableRef] = []
    reads += _collect(_RE_FROM, clean, database, "read", "table")
    reads += _collect(_RE_JOIN, clean, database, "read", "table")
    reads += _collect(_RE_USING, clean, database, "read", "table")
    return reads, writes


def _table_raw_name(table) -> str:
    """Reassemble a sqlglot ``Table`` node into a dotted ``[db.]schema.table``."""
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    return ".".join(parts)


def _real_tables(statement, exp) -> List:
    """Real ``Table`` nodes under ``statement``, dropping phantoms.

    Drops nameless function-valued sources, tables whose schema-less name is a
    table alias reference, and CTE-name references (``WITH c AS (...) ... FROM c``
    parses ``c`` as a Table with no schema; it is a local name, not a base
    table).
    """
    tables = list(statement.find_all(exp.Table))
    aliases = {t.alias for t in tables if t.alias}
    cte_names = {cte.alias for cte in statement.find_all(exp.CTE) if cte.alias}
    skip = aliases | cte_names
    real: List = []
    for table in tables:
        if not table.name:
            continue
        if not table.db and table.name in skip:
            continue
        real.append(table)
    return real


def _write_target(statement, exp):
    """The write target of an Insert/Update/Merge/Delete statement, if any."""
    this = statement.this
    if isinstance(statement, exp.Update):
        target_ref = this.name if isinstance(this, exp.Table) else None
        for table in _real_tables(statement, exp):
            if table.alias and table.alias == target_ref:
                return table
        if isinstance(this, exp.Table) and this.db:
            return this
        reals = _real_tables(statement, exp)
        return reals[0] if reals else None
    found = list(this.find_all(exp.Table)) if this is not None else []
    return found[0] if found else None


def _statement_reads_writes(
    statement, database: Optional[str], exp
) -> Tuple[List[TableRef], List[TableRef]]:
    """Classify one parsed statement's tables into reads and writes."""
    reads: List[TableRef] = []
    writes: List[TableRef] = []

    if isinstance(statement, exp.Create):
        kind = "view" if (statement.kind or "").upper() == "VIEW" else "table"
        target = statement.this
        created = list(target.find_all(exp.Table)) if target is not None else []
        write_names = set()
        for tbl in created[:1]:
            ref = make_table_ref(_table_raw_name(tbl), database=database, operation="write", kind=kind)
            if ref is not None:
                writes.append(ref)
                write_names.add(id(tbl))
        for tbl in _real_tables(statement, exp):
            if id(tbl) in write_names:
                continue
            ref = make_table_ref(_table_raw_name(tbl), database=database, operation="read", kind="table")
            if ref is not None:
                reads.append(ref)
        return reads, writes

    if isinstance(statement, (exp.Insert, exp.Update, exp.Merge, exp.Delete)):
        target = _write_target(statement, exp)
        target_id = id(target) if target is not None else None
        if target is not None:
            ref = make_table_ref(_table_raw_name(target), database=database, operation="write", kind="table")
            if ref is not None:
                writes.append(ref)
        for tbl in _real_tables(statement, exp):
            if target_id is not None and id(tbl) == target_id:
                continue
            ref = make_table_ref(_table_raw_name(tbl), database=database, operation="read", kind="table")
            if ref is not None:
                reads.append(ref)
        return reads, writes

    # SELECT ... INTO target FROM src: the INTO target is a write, not a read.
    into_id: Optional[int] = None
    if isinstance(statement, exp.Select):
        into = statement.args.get("into")
        into_table = into.this if into is not None else None
        if isinstance(into_table, exp.Table):
            into_id = id(into_table)
            ref = make_table_ref(_table_raw_name(into_table), database=database, operation="write", kind="table")
            if ref is not None:
                writes.append(ref)

    # SELECT and everything else: every real table is a read.
    for tbl in _real_tables(statement, exp):
        if into_id is not None and id(tbl) == into_id:
            continue
        ref = make_table_ref(_table_raw_name(tbl), database=database, operation="read", kind="table")
        if ref is not None:
            reads.append(ref)
    return reads, writes


def _sqlglot_reads_writes(
    sql: str, database: Optional[str]
) -> Optional[Tuple[List[TableRef], List[TableRef]]]:
    """sqlglot-based read/write extraction; ``None`` on import or parse failure."""
    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return None

    try:
        statements = sqlglot.parse(sql, dialect="tsql")
    except Exception:
        return None

    reads: List[TableRef] = []
    writes: List[TableRef] = []
    for statement in statements:
        if not isinstance(statement, exp.Expression):
            continue
        s_reads, s_writes = _statement_reads_writes(statement, database, exp)
        reads += s_reads
        writes += s_writes
    return reads, writes


def extract_table_refs(sql: Optional[str], database: Optional[str] = None) -> SqlRefs:
    """Extract read/write/exec table references from a T-SQL fragment.

    A sqlglot parse (dialect ``tsql``) resolves the DML/DDL read/write surface
    when sqlglot is installed and the parse succeeds; on import failure or a
    parse error the regex heuristics take over unchanged. Stored-procedure
    calls and dynamic SQL are always classified by the regex detectors
    (orthogonal, and more reliable textually), so ``execs`` and ``dynamic``
    behave identically either way.
    """
    result = SqlRefs()
    if not sql or not sql.strip():
        return result

    clean = _RE_BLOCK_COMMENT.sub(" ", sql)
    clean = _RE_LINE_COMMENT.sub(" ", clean)

    if _RE_DYNAMIC.search(clean):
        result.dynamic = True
        result.problems.append("dynamic or expression-built SQL could not be fully resolved")

    execs = _collect(_RE_EXEC, clean, database, "exec", "stored_procedure")

    parsed = _sqlglot_reads_writes(sql, database)
    if parsed is not None:
        reads, writes = parsed
    else:
        reads, writes = _regex_reads_writes(clean, database)

    # A name written via DELETE/SELECT-INTO would also match a read; keep it a
    # write. A stored-procedure name must never leak into reads.
    write_sigs = {w.signature() for w in writes}
    exec_sigs = {e.signature() for e in execs}
    reads = [r for r in reads if r.signature() not in write_sigs and r.signature() not in exec_sigs]

    result.writes = _dedupe(writes)
    result.execs = _dedupe(execs)
    result.reads = _dedupe(reads)
    return result
