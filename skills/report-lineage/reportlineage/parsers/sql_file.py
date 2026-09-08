"""
Classify a standalone ``.sql`` file's primary object for lineage purposes.

This is deliberately shallow: it looks for a single leading
``CREATE [OR ALTER] TABLE|VIEW|PROCEDURE|PROC`` statement to name the object
and pick its kind, then defers to :func:`reportlineage.sql_refs.extract_table_refs`
for the read/write table surface of the whole file. When no CREATE statement
names the object, the filename (minus extension) is used instead, so the
caller always gets a usable name.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from reportlineage.sql_refs import extract_table_refs, make_table_ref

_RE_LINE_COMMENT = re.compile(r"--[^\r\n]*")
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

_RE_CREATE_TABLE = re.compile(
    r"\bCREATE\s+TABLE\s+([^\s(]+)", re.IGNORECASE
)
_RE_CREATE_VIEW = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+([^\s(]+)", re.IGNORECASE
)
_RE_CREATE_PROC = re.compile(
    r"\bCREATE\s+(?:OR\s+ALTER\s+)?(?:PROCEDURE|PROC)\s+([^\s(]+)", re.IGNORECASE
)


def _clean(sql: str) -> str:
    clean = _RE_BLOCK_COMMENT.sub(" ", sql)
    clean = _RE_LINE_COMMENT.sub(" ", clean)
    return clean


def classify_sql_object(sql_text: str, filename: str = "") -> dict:
    """Classify the primary object defined by ``sql_text``.

    Returns a dict with keys ``kind`` (``"table"``, ``"view"``,
    ``"stored_procedure"``, or ``"unknown"``), ``name`` (the object's
    unqualified/dotted name as written, or ``None``), ``reads``
    (``List[TableRef]``) and ``writes`` (``List[TableRef]``).
    """
    text = sql_text or ""
    clean = _clean(text)

    kind = "unknown"
    raw_name: Optional[str] = None

    m = _RE_CREATE_TABLE.search(clean)
    if m:
        kind = "table"
        raw_name = m.group(1)
    if raw_name is None:
        m = _RE_CREATE_VIEW.search(clean)
        if m:
            kind = "view"
            raw_name = m.group(1)
    if raw_name is None:
        m = _RE_CREATE_PROC.search(clean)
        if m:
            kind = "stored_procedure"
            raw_name = m.group(1)

    name: Optional[str] = None
    if raw_name:
        ref = make_table_ref(raw_name)
        name = ref.qualified_name if ref else raw_name
    elif filename:
        name = Path(filename).stem

    refs = extract_table_refs(text)
    reads = list(refs.reads)
    writes = list(refs.writes)

    # A CREATE TABLE/VIEW/PROC statement names the object being defined, not a
    # table it reads from or writes into; drop it from both surfaces if the
    # underlying regex scan of the whole file picked it up as a write.
    if raw_name:
        self_ref = make_table_ref(raw_name)
        if self_ref is not None:
            self_sig = self_ref.signature()
            reads = [r for r in reads if r.signature() != self_sig]
            writes = [w for w in writes if w.signature() != self_sig]

    return {
        "kind": kind,
        "name": name,
        "reads": reads,
        "writes": writes,
    }
