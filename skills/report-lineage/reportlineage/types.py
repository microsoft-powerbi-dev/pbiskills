"""
A small, local data-type vocabulary for report/dataset fields.

This intentionally mirrors the shape of a typical IR ``DataType`` enum without
importing one from a host application: the values are just strings, closed
over the handful of buckets lineage parsing needs (a field's precise SQL type
does not matter for lineage, only whether it is numeric, textual, temporal,
boolean, or unknown).
"""
from __future__ import annotations

from enum import Enum


class DataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    REAL = "real"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    DATE = "date"
    UNKNOWN = "unknown"
