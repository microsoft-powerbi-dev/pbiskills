"""
Cross-tool table signature: the join key that links one source artifact to
another. An SSIS data-flow destination table and an RDL dataset source table
that share a signature are the same physical table, which is what turns
"SSIS package writes X" plus "report reads X" into a package to report
dependency.

The canonical implementation lives on ``TableRef.signature``; this module
re-exports it as a plain function so lineage code has a single, named entry
point that does not require constructing a ``TableRef``.
"""
from __future__ import annotations

from typing import Optional

from reportlineage.sql_refs import TableRef


def table_signature(database: Optional[str], schema: Optional[str], table: str) -> str:
    """``database::schema.table``, case-folded, schema defaulting to ``dbo``."""
    return TableRef(table=table, schema=schema, database=database).signature()
