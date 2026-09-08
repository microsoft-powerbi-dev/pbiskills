"""
Namespace-agnostic XML helpers, plus RDL type/size conversions.

RDL and DTSX files are namespaced against version-specific schema URLs
(for example ``http://schemas.microsoft.com/sqlserver/reporting/<year>/...``).
To stay version-tolerant, this module navigates by *local* tag name and
ignores namespaces.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import List, Optional, Union
from xml.etree.ElementTree import Element

from reportlineage.types import DataType


def local_name(tag: str) -> str:
    """Return the local part of a possibly-namespaced ElementTree tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def sanitize_rdl_bytes(source: Union[str, Path]) -> io.BytesIO:
    """Read an RDL file and strip trailing null bytes that SSRS Report Server pads."""
    raw = Path(source).read_bytes()
    return io.BytesIO(raw.rstrip(b"\x00"))


def child(elem: Optional[Element], name: str) -> Optional[Element]:
    """First direct child with the given local name (case-insensitive)."""
    if elem is None:
        return None
    lname = name.lower()
    for c in list(elem):
        if local_name(c.tag).lower() == lname:
            return c
    return None


def children(elem: Optional[Element], name: str) -> List[Element]:
    """All direct children with the given local name."""
    if elem is None:
        return []
    lname = name.lower()
    return [c for c in list(elem) if local_name(c.tag).lower() == lname]


def descendants(elem: Optional[Element], name: str) -> List[Element]:
    """All descendants (any depth) with the given local name."""
    if elem is None:
        return []
    lname = name.lower()
    return [d for d in elem.iter() if local_name(d.tag).lower() == lname]


def text_of(elem: Optional[Element], name: str) -> Optional[str]:
    """Text of the first direct child with the given local name."""
    c = child(elem, name)
    if c is not None and c.text is not None:
        return c.text.strip()
    return None


# --- RDL size strings ("2in", "1.5cm", "10pt", "100mm", "5px") to pixels @96dpi ---

_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(in|cm|mm|pt|px)?\s*$", re.IGNORECASE)
_UNIT_TO_PX = {
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "pt": 96.0 / 72.0,
    "px": 1.0,
}


def size_to_px(value: Optional[str], default: float = 0.0) -> float:
    """Convert an RDL measurement string to pixels at 96 dpi.

    Conformant RDL always carries an explicit unit. A unit-less value is
    treated as pixels (factor 1.0) rather than inches, so malformed input
    cannot silently inflate the layout 96x.
    """
    if not value:
        return default
    m = _SIZE_RE.match(value)
    if not m:
        return default
    num = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    return round(num * _UNIT_TO_PX.get(unit, 1.0), 2)


# --- RDL field / parameter type names -> local DataType ---

_TYPENAME_MAP = {
    "system.string": DataType.STRING,
    "system.int16": DataType.INTEGER,
    "system.int32": DataType.INTEGER,
    "system.int64": DataType.INTEGER,
    "system.byte": DataType.INTEGER,
    "system.decimal": DataType.REAL,
    "system.double": DataType.REAL,
    "system.single": DataType.REAL,
    "system.boolean": DataType.BOOLEAN,
    "system.datetime": DataType.DATETIME,
    "system.datetimeoffset": DataType.DATETIME,
    "system.guid": DataType.STRING,
    # RDL ReportParameter DataType values
    "string": DataType.STRING,
    "integer": DataType.INTEGER,
    "float": DataType.REAL,
    "boolean": DataType.BOOLEAN,
    "datetime": DataType.DATETIME,
    "date": DataType.DATE,
}


def map_data_type(type_name: Optional[str]) -> DataType:
    if not type_name:
        return DataType.UNKNOWN
    return _TYPENAME_MAP.get(type_name.strip().lower(), DataType.UNKNOWN)
