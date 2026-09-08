"""
A simple, dependency-free inverted-index search over report fingerprints.

Answers two questions: "which existing reports look like this request?"
(:meth:`SearchIndex.search`, which delegates to the same scoring
:func:`reportlineage.duplicates.find_reuse` uses) and "what reports would be
affected if I changed this table?" (:meth:`SearchIndex.by_table`, an exact
lookup).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from reportlineage.duplicates import find_reuse
from reportlineage.fingerprint import ReportFingerprint


class SearchIndex:
    """An inverted index over a list of :class:`ReportFingerprint` values."""

    def __init__(self, fingerprints: List[ReportFingerprint]) -> None:
        self.fingerprints: List[ReportFingerprint] = list(fingerprints)
        self._by_key: Dict[str, ReportFingerprint] = {fp.key: fp for fp in self.fingerprints}
        self._table_index: Dict[str, List[ReportFingerprint]] = {}
        for fp in self.fingerprints:
            for table in fp.tables:
                self._table_index.setdefault(table, []).append(fp)

    def search(self, query: str, limit: int = 10) -> List[Tuple[ReportFingerprint, float]]:
        """Rank fingerprints against a free-text query, best match first."""
        matches = find_reuse(self.fingerprints, query)
        out: List[Tuple[ReportFingerprint, float]] = []
        for match in matches:
            fp = self._by_key.get(match.key)
            if fp is not None:
                out.append((fp, match.score))
        return out[:limit]

    def by_table(self, signature: str) -> List[ReportFingerprint]:
        """Every fingerprint that references ``signature`` exactly."""
        return list(self._table_index.get(signature, []))
