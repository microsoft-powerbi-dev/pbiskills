"""
Classify SSRS/SSIS artifact files under a local directory tree by extension.

Deliberately shallow: no XML sniffing, just an extension map, because
:func:`reportlineage.builder.build_estate` and the source-specific parsers
already tolerate a misclassified or malformed file without raising.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

#: Extension -> ScanResult attribute name.
_EXTENSION_MAP = {
    ".rdl": "rdl_paths",
    ".dtsx": "dtsx_paths",
    ".conmgr": "conmgr_paths",
    ".sql": "sql_paths",
    ".rds": "rsd_paths",
    ".rsd": "rsd_paths",
}


@dataclass
class ScanResult:
    """Files discovered under one scan root, grouped by artifact type."""

    rdl_paths: List[str] = field(default_factory=list)
    dtsx_paths: List[str] = field(default_factory=list)
    conmgr_paths: List[str] = field(default_factory=list)
    sql_paths: List[str] = field(default_factory=list)
    rsd_paths: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    """Human-readable notes about paths that could not be scanned."""


def scan_filesystem(root: str, *, follow_symlinks: bool = False) -> ScanResult:
    """Recursively classify every SSRS/SSIS artifact file under ``root``.

    Never raises: a nonexistent or unreadable root yields an empty
    :class:`ScanResult` with a note in ``skipped``, not an exception.
    """
    result = ScanResult()

    if not root or not os.path.isdir(root):
        result.skipped.append(f"scan root does not exist or is not a directory: {root}")
        return result

    try:
        walker = os.walk(root, followlinks=follow_symlinks)
        for dirpath, _dirnames, filenames in walker:
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1].lower()
                attr = _EXTENSION_MAP.get(ext)
                if attr is not None:
                    getattr(result, attr).append(full_path)
    except OSError as exc:
        result.skipped.append(f"error walking '{root}': {exc}")

    return result
