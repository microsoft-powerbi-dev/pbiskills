"""A fake pyodbc connection, so the catalog layer is testable with no database.

The catalog functions take a connection as their first argument and never build
one, which is exactly what lets this stand in. Scripts are matched by regex
against the SQL text, so a test declares "when a query mentioning
sys.foreign_keys arrives, return these rows" without caring about whitespace.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


class FakeCursor:
    """The subset of the pyodbc cursor API that ``catalog.py`` actually uses."""

    def __init__(self, script: Sequence[Tuple[Any, List[str], List[tuple]]], log: List):
        self._script = script
        self._log = log
        self.description: Optional[List[Tuple[str, ...]]] = None
        self._rows: List[tuple] = []
        self.closed = False

    def execute(self, sql: str, *params: Any) -> "FakeCursor":
        self._log.append((sql, params))
        for pattern, columns, rows in self._script:
            if pattern.search(sql):
                self.description = [(name,) for name in columns]
                self._rows = list(rows)
                return self
        # An unscripted query returns an empty result set rather than blowing
        # up, so a test only has to script what it cares about.
        self.description = None
        self._rows = []
        return self

    def fetchall(self) -> List[tuple]:
        return list(self._rows)

    def fetchmany(self, size: int) -> List[tuple]:
        taken, self._rows = self._rows[:size], self._rows[size:]
        return taken

    def fetchone(self) -> Optional[tuple]:
        return self._rows.pop(0) if self._rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Dispatches every cursor to the same script and records what was asked.

    ``executed`` holds every (sql, params) pair, which is what lets a test
    assert that identifiers were passed as parameters rather than interpolated.
    """

    def __init__(self, script: Optional[Dict[str, Tuple[List[str], List[tuple]]]] = None):
        self._script = [
            (re.compile(pattern, re.IGNORECASE | re.DOTALL), columns, rows)
            for pattern, (columns, rows) in (script or {}).items()
        ]
        self.executed: List[Tuple[str, tuple]] = []
        self.rolled_back = False
        self.committed = False
        self.closed = False
        self.timeout: Optional[int] = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._script, self.executed)

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:  # pragma: no cover - a test failing here is the point
        self.committed = True

    def close(self) -> None:
        self.closed = True

    # Convenience for assertions
    def sql_texts(self) -> List[str]:
        return [sql for sql, _ in self.executed]
