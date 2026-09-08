"""
Estate acquisition scanners: local filesystem, a cloned git repository, and a
live SSRS / Power BI Report Server catalog. Each scanner produces (or feeds)
a :class:`~reportlineage.scanners.filesystem.ScanResult` that
:func:`reportlineage.builder.build_estate` can consume directly.

Submodules are imported lazily by callers (the CLI, tests) rather than
re-exported here, so importing this package never requires optional
dependencies like ``requests`` or the ``git`` binary.
"""
from __future__ import annotations
