"""A fake pyodbc connection for the extract engine's tests.

Same shape as sql-server-schema's tests/fakes.py (regex-matched scripted
reads), extended with write recording: every INSERT/UPDATE/DELETE/CREATE OR
ALTER VIEW that runs through a FakeConnection is captured in ``.writes`` as
(sql, params), which is what lets a test assert "this statement was sent"
without a real database - the seam checkpoint.py and config_loader.py's tests
both need.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


class FakeCursor:
    def __init__(self, parent: "FakeConnection"):
        self._parent = parent
        self.description: Optional[List[Tuple[str, ...]]] = None
        self._rows: List[tuple] = []
        self.closed = False

    def execute(self, sql: str, *params: Any) -> "FakeCursor":
        flat_params = params[0] if len(params) == 1 and isinstance(params[0], (tuple, list)) else params
        self._parent.executed.append((sql, tuple(flat_params)))

        stripped = sql.strip().upper()
        # A combined "INSERT ...; SELECT SCOPE_IDENTITY()" batch (see
        # connection.execute_insert_return_identity) still starts with the
        # write keyword, so this also covers that shape correctly.
        if stripped.startswith(("INSERT", "UPDATE", "DELETE", "CREATE")):
            self._parent.writes.append((sql, tuple(flat_params)))

        for pattern, columns, rows in self._parent.script:
            if pattern.search(sql):
                self.description = [(name,) for name in columns]
                self._rows = list(rows)
                return self
        self.description = None
        self._rows = []
        return self

    def nextset(self) -> bool:
        # No-op: FakeCursor.execute already positions on whichever scripted
        # result set matched, covering the combined INSERT+SELECT batch shape
        # without needing to model multiple real result sets.
        return True

    def fetchall(self) -> List[tuple]:
        return list(self._rows)

    def fetchone(self) -> Optional[tuple]:
        return self._rows.pop(0) if self._rows else None

    def fetchmany(self, size: int) -> List[tuple]:
        taken, self._rows = self._rows[:size], self._rows[size:]
        return taken

    def executemany(self, sql: str, params_seq) -> "FakeCursor":
        for params in params_seq:
            self.execute(sql, *params)
        return self

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, script: Optional[Dict[str, Tuple[List[str], List[tuple]]]] = None):
        self.script = [
            (re.compile(pattern, re.IGNORECASE | re.DOTALL), columns, rows)
            for pattern, (columns, rows) in (script or {}).items()
        ]
        self.executed: List[Tuple[str, tuple]] = []
        self.writes: List[Tuple[str, tuple]] = []
        self.rolled_back = False
        self.committed = False
        self.closed = False
        self.timeout: Optional[int] = None

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True

    def sql_texts(self) -> List[str]:
        return [sql for sql, _ in self.executed]

    def write_texts(self) -> List[str]:
        return [sql for sql, _ in self.writes]
