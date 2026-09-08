"""
Turn a parsed RDL report into a comparable fingerprint.

The fingerprint is what duplicate detection and reuse search actually compare.
It deliberately uses several independent dimensions, because two SSRS reports
can overlap in different ways: same query against the same tables, same field
list with a different query, or same layout over a renamed dataset. Any single
dimension misses at least one of those.

This module works directly off :class:`reportlineage.parsers.rdl.RdlReport`,
the narrow lineage-only parse result, rather than a full IR workbook, so it
has no dependency on any host application's models.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, FrozenSet, Iterable, List, Optional, Set

from reportlineage.sql_refs import extract_table_refs

if TYPE_CHECKING:
    from reportlineage.parsers.rdl import RdlReport

_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_WHITESPACE = re.compile(r"\s+")

#: Tokens too generic to say anything about what a report is about.
STOPWORDS = frozenset(
    {
        "report", "reports", "the", "and", "for", "with", "by", "of", "a", "an",
        "id", "key", "code", "name", "date", "time", "value", "total", "count",
        "new", "copy", "final", "v1", "v2", "v3", "old", "test", "temp", "draft",
    }
)

#: Bounded so one pathological report cannot dominate memory or comparison cost.
_MAX_PER_DIMENSION = 500


def normalize_name(value: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    ``Sales Amount``, ``sales_amount`` and ``SalesAmount `` all collapse
    together, which is what makes cross-report column matching work at all.
    """
    lowered = (value or "").strip().lower()
    lowered = lowered.replace("_", " ")
    lowered = _NON_WORD.sub(" ", lowered)
    return _WHITESPACE.sub(" ", lowered).strip()


def normalize_sql(sql: str) -> str:
    """Collapse whitespace and case so cosmetically different SQL compares equal."""
    return _WHITESPACE.sub(" ", (sql or "").strip().lower())


def tokenize(values: Iterable[str]) -> Set[str]:
    """Split a batch of strings into lower-cased content words.

    Shared by fingerprinting (report/dataset/field names) and reuse search
    (a free-text request), so both sides of a comparison tokenize identically.
    """
    tokens: Set[str] = set()
    for value in values:
        for token in normalize_name(value).split():
            if len(token) > 2 and token not in STOPWORDS:
                tokens.add(token)
    return tokens


def _cap(values: Set[str]) -> FrozenSet[str]:
    if len(values) <= _MAX_PER_DIMENSION:
        return frozenset(values)
    return frozenset(sorted(values)[:_MAX_PER_DIMENSION])


@dataclass(frozen=True)
class ReportFingerprint:
    """Everything duplicate detection and reuse search need about one report."""

    key: str
    """Stable identity, typically the source file path or catalog path."""
    name: str
    folder: str = ""
    tables: FrozenSet[str] = frozenset()
    """``database::schema.table`` signatures, the cross-tool join key."""
    columns: FrozenSet[str] = frozenset()
    parameters: FrozenSet[str] = frozenset()
    visuals: FrozenSet[str] = frozenset()
    sql: FrozenSet[str] = frozenset()
    terms: FrozenSet[str] = frozenset()
    """Content words from the name, dataset names and field names, powers reuse search."""
    datasource_names: FrozenSet[str] = frozenset()
    visual_count: int = 0
    kind: str = "report"

    @property
    def is_empty(self) -> bool:
        return not (self.tables or self.columns or self.sql or self.parameters)


def build_fingerprint_from_rdl_report(
    report: "RdlReport", *, key: Optional[str] = None, folder: str = ""
) -> ReportFingerprint:
    """Fingerprint an already-parsed :class:`~reportlineage.parsers.rdl.RdlReport`.

    Never raises: any internal failure yields the best-effort fingerprint built
    so far, so one malformed report cannot abort a sweep over many files.
    """
    fp_key = key or getattr(report, "path", None) or getattr(report, "name", "report")
    tables: Set[str] = set()
    columns: Set[str] = set()
    sql_texts: Set[str] = set()
    term_sources: List[str] = []

    try:
        term_sources.append(report.name)
    except Exception:
        pass

    try:
        for dset in report.datasets:
            try:
                term_sources.append(dset.name)
                for fname in dset.fields:
                    columns.add(normalize_name(fname))
                    term_sources.append(fname)
                if dset.command_text:
                    sql_texts.add(normalize_sql(dset.command_text))
                    server, db = report.connections.get(dset.datasource_name or "", (None, None))
                    try:
                        refs = extract_table_refs(dset.command_text, database=db)
                        for ref in refs.all:
                            tables.add(ref.signature())
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass

    try:
        parameters = {normalize_name(p) for p in report.parameters}
    except Exception:
        parameters = set()

    try:
        visuals = {v.lower() for v in report.visual_kinds}
    except Exception:
        visuals = set()

    try:
        datasource_names = {normalize_name(n) for n in report.connections.keys()}
    except Exception:
        datasource_names = set()

    try:
        visual_count = int(report.visual_count)
    except Exception:
        visual_count = 0

    try:
        display_name = report.name
    except Exception:
        display_name = str(fp_key)

    return ReportFingerprint(
        key=str(fp_key),
        name=display_name,
        folder=folder,
        tables=_cap(tables),
        columns=_cap(columns),
        parameters=_cap(parameters),
        visuals=frozenset(visuals),
        sql=_cap(sql_texts),
        terms=_cap(tokenize(term_sources)),
        datasource_names=_cap(datasource_names),
        visual_count=visual_count,
    )


def fingerprint_rdl(path: str, *, key: Optional[str] = None, folder: str = "") -> ReportFingerprint:
    """Parse one ``.rdl`` file from disk and fingerprint it.

    Never raises: a file that cannot be parsed at all still yields a usable
    (if empty) fingerprint, keyed and named from the file path, so a whole-estate
    fingerprinting sweep never aborts on one bad file.
    """
    try:
        from reportlineage.parsers.rdl import parse_rdl_lineage

        report = parse_rdl_lineage(path)
        return build_fingerprint_from_rdl_report(report, key=key or path, folder=folder)
    except Exception:
        name = Path(path).stem if path else "report"
        return ReportFingerprint(key=str(key or path), name=name, folder=folder)
