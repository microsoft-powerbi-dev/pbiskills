"""
analyze.py - turn raw catalog reads into a schema digest: the join graph, table
roles, naming conventions, and an importance ranking.

The digest is the seam of this whole skill. The live server writes one; the
generator reads one and never touches a database. That split is what lets a
schema pack be reviewed before it is generated, regenerated on a machine with
no ODBC driver, and unit-tested against a fixture.

Every heuristic here is deliberately transparent. ``classify_tables`` returns
the signals that fired alongside the label, so a wrong call is visible and
arguable rather than silent. None of this is semantic understanding: it is
name, key, and cardinality pattern-matching, and the reference doc says so.

The table signature is ``database::schema.table``, lower-cased, which is
byte-for-byte the string ``reportlineage.sql_refs.TableRef.signature()``
produces. Reusing it is what makes "this RDL report reads these tables" join to
"here is what those tables contain" without a mapping layer.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DIGEST_SCHEMA_VERSION = "1.0"

_MEASURE_TYPES = {
    "decimal", "numeric", "money", "smallmoney", "float", "real", "bigint", "int",
}
_MEASURE_NAME = re.compile(
    r"(amount|amt|qty|quantity|count|total|sum|balance|cost|price|units|paid"
    r"|billed|allowed|charge|fee|rate|premium)",
    re.IGNORECASE,
)
_DATE_TYPES = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"}

_STAGING_SCHEMAS = {"stg", "staging", "tmp", "temp", "etl", "load", "work"}
_STAGING_NAME = re.compile(r"^(stg|tmp|temp|wrk|load|bak)_|_(bak|old|backup|copy)(_?\d*)?$|_(19|20)\d{6}$", re.IGNORECASE)
_AUDIT_NAME = re.compile(r"(audit|_log$|history|_hist$|archive|changetracking)", re.IGNORECASE)
# Match Fact_Order, FactOrder, fact_order and the dim equivalents, without
# matching Factory or Dimension: the prefix must be followed by a separator or
# a new capitalised word, so the case-sensitivity here is deliberate.
_FACT_NAME = re.compile(r"^(?:fact|Fact|FACT)(?:_|(?=[A-Z]))")
_DIM_NAME = re.compile(r"^(?:dim|Dim|DIM)(?:_|(?=[A-Z]))")

_LOOKUP_NAME = re.compile(r"(lookup|_?ref$|reference|_type$|_status$|_code$|codes?$)", re.IGNORECASE)

_SOFT_DELETE = re.compile(r"^(is_?deleted|is_?active|deleted_?date|record_?status|row_?status)$", re.IGNORECASE)
_SCD2 = re.compile(r"^(effective_?(from|to)|valid_?(from|to)|is_?current|row_?(start|end)_?date)$", re.IGNORECASE)
_AUDIT_COLUMN = re.compile(r"^(created|modified|updated|inserted)_?(by|date|on|at|utc)?$|^(rowversion|row_?version|timestamp)$", re.IGNORECASE)


def table_signature(database: Optional[str], schema: Optional[str], table: str) -> str:
    """Build the ``database::schema.table`` key, matching reportlineage exactly.

    Schema defaults to ``dbo`` and everything is case-folded, which is the same
    rule ``reportlineage.sql_refs.TableRef.signature()`` applies, so signatures
    from a lineage scan and from a schema digest compare directly.
    """
    db = (database or "").strip().lower()
    sch = (schema or "dbo").strip().lower() or "dbo"
    tbl = (table or "").strip().lower()
    return "{}::{}.{}".format(db, sch, tbl)


# ---------------------------------------------------------------------------
# Join graph
# ---------------------------------------------------------------------------


@dataclass
class JoinGraph:
    """Nodes keyed by ``schema.table``, plus declared and inferred edges."""

    nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    edges: List[Dict[str, Any]] = field(default_factory=list)

    def in_degree(self, key: str) -> int:
        return sum(1 for edge in self.edges if edge["to_table"] == key)

    def out_degree(self, key: str) -> int:
        return sum(1 for edge in self.edges if edge["from_table"] == key)

    def neighbours(self, key: str) -> List[str]:
        found = []
        for edge in self.edges:
            if edge["from_table"] == key:
                found.append(edge["to_table"])
            elif edge["to_table"] == key:
                found.append(edge["from_table"])
        return sorted(set(found))


def build_join_graph(
    tables: Sequence[Dict[str, Any]],
    foreign_keys: Sequence[Dict[str, Any]],
    *,
    infer: bool = True,
) -> JoinGraph:
    """Build the join graph from declared foreign keys, optionally inferring more.

    Inference matters more than it sounds: large legacy estates routinely drop
    foreign key constraints for load performance, so the join graph exists only
    as a naming convention. Inferred edges are marked as such and never mixed
    into the declared set.
    """
    graph = JoinGraph()
    for table in tables:
        key = "{}.{}".format(table["schema"], table["name"])
        graph.nodes[key] = {
            "schema": table["schema"],
            "name": table["name"],
            "row_count": table.get("row_count", 0),
            "column_count": len(table.get("columns", [])),
        }

    for fk in foreign_keys:
        from_key = "{}.{}".format(fk["parent_schema"], fk["parent_table"])
        to_key = "{}.{}".format(fk["referenced_schema"], fk["referenced_table"])
        graph.edges.append(
            {
                "from_table": from_key,
                "to_table": to_key,
                "from_columns": list(fk.get("parent_columns", [])),
                "to_columns": list(fk.get("referenced_columns", [])),
                "constraint": fk.get("name"),
                "confidence": "declared",
                "trusted": not fk.get("is_not_trusted", False),
            }
        )

    if infer:
        graph.edges.extend(infer_relationships(tables, graph))
    return graph


def infer_relationships(
    tables: Sequence[Dict[str, Any]], graph: JoinGraph
) -> List[Dict[str, Any]]:
    """Guess joins from column naming where no foreign key was declared.

    Matches a non-key column named ``<Table>Id`` / ``<Table>_ID`` / ``<Table>Key``
    against a table whose primary key column has that same name. Emitted with
    ``confidence: "inferred"`` so a consumer can weigh it differently.
    """
    # Index primary keys by their single-column name.
    pk_owner: Dict[str, str] = {}
    for table in tables:
        pk = table.get("primary_key") or {}
        columns = pk.get("columns") or []
        if len(columns) == 1:
            pk_owner.setdefault(columns[0].lower(), "{}.{}".format(table["schema"], table["name"]))

    declared = {
        (edge["from_table"], edge["to_table"])
        for edge in graph.edges
        if edge["confidence"] == "declared"
    }

    inferred: List[Dict[str, Any]] = []
    for table in tables:
        from_key = "{}.{}".format(table["schema"], table["name"])
        own_pk = {c.lower() for c in ((table.get("primary_key") or {}).get("columns") or [])}
        for column in table.get("columns", []):
            name = column["name"].lower()
            if name in own_pk:
                continue
            if not re.search(r"(id|key|code|no)$", name):
                continue
            owner = pk_owner.get(name)
            if not owner or owner == from_key:
                continue
            if (from_key, owner) in declared:
                continue
            inferred.append(
                {
                    "from_table": from_key,
                    "to_table": owner,
                    "from_columns": [column["name"]],
                    "to_columns": [column["name"]],
                    "constraint": None,
                    "confidence": "inferred",
                    "trusted": False,
                }
            )
            declared.add((from_key, owner))
    return inferred


def shortest_join_path(
    graph: JoinGraph, source: str, target: str, max_hops: int = 3
) -> Optional[List[Dict[str, Any]]]:
    """Breadth-first shortest join path between two tables, or None."""
    if source not in graph.nodes or target not in graph.nodes:
        return None
    queue: List[Tuple[str, List[Dict[str, Any]]]] = [(source, [])]
    seen = {source}
    while queue:
        current, path = queue.pop(0)
        if current == target:
            return path
        if len(path) >= max_hops:
            continue
        for edge in graph.edges:
            if edge["from_table"] == current:
                nxt = edge["to_table"]
            elif edge["to_table"] == current:
                nxt = edge["from_table"]
            else:
                continue
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append((nxt, path + [edge]))
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _measure_columns(table: Dict[str, Any]) -> List[str]:
    pk = {c.lower() for c in ((table.get("primary_key") or {}).get("columns") or [])}
    found = []
    for column in table.get("columns", []):
        if column["name"].lower() in pk:
            continue
        if column.get("data_type", "").lower() in _MEASURE_TYPES and _MEASURE_NAME.search(
            column["name"]
        ):
            found.append(column["name"])
    return found


def _has_code_and_description(table: Dict[str, Any]) -> bool:
    """A short code column plus a longer descriptive one: the lookup-table shape."""
    names = [column["name"].lower() for column in table.get("columns", [])]
    has_code = any(re.search(r"(code|id|key|abbr)$", name) for name in names)
    has_description = any(
        re.search(r"(name|description|desc|label|text|title)$", name) for name in names
    )
    return has_code and has_description


def _date_columns(table: Dict[str, Any]) -> List[str]:
    return [
        column["name"]
        for column in table.get("columns", [])
        if column.get("data_type", "").lower() in _DATE_TYPES
    ]


def classify_table(
    table: Dict[str, Any], graph: JoinGraph, median_rows: float
) -> Tuple[str, List[str]]:
    """Assign a modelling role, returning the label and the reasons that fired.

    First match wins, so the ordering below is the specification.
    """
    key = "{}.{}".format(table["schema"], table["name"])
    schema = table["schema"].lower()
    name = table["name"]
    reasons: List[str] = []

    in_degree = graph.in_degree(key)
    out_degree = graph.out_degree(key)
    rows = table.get("row_count") or 0
    measures = _measure_columns(table)
    dates = _date_columns(table)
    column_count = len(table.get("columns", []))
    pk_columns = (table.get("primary_key") or {}).get("columns") or []

    if schema in _STAGING_SCHEMAS or _STAGING_NAME.search(name):
        return "staging", ["name or schema marks this as staging or a backup copy"]

    if _AUDIT_NAME.search(name) and in_degree == 0:
        return "audit", ["audit or history naming with nothing referencing it"]

    if _FACT_NAME.match(name):
        return "fact", ["name starts with an explicit fact prefix"]
    if _DIM_NAME.match(name):
        return "dimension", ["name starts with an explicit dim prefix"]

    fk_columns = set()
    for edge in graph.edges:
        if edge["from_table"] == key:
            fk_columns.update(c.lower() for c in edge["from_columns"])

    if (
        len(pk_columns) >= 2
        and all(c.lower() in fk_columns for c in pk_columns)
        and column_count - len(pk_columns) <= 3
    ):
        return "bridge", ["composite primary key made entirely of foreign keys"]

    if rows < 1000 and column_count <= 8:
        # Being referenced is the strongest signal, but a lookup table with no
        # declared foreign key pointing at it is common in estates that dropped
        # their constraints, so name shape counts too.
        if in_degree >= 1:
            return "lookup", ["small, narrow, and referenced by other tables"]
        if _LOOKUP_NAME.search(name):
            return "lookup", ["small, narrow, and named as a lookup or reference table"]
        if _has_code_and_description(table):
            return "lookup", ["small, narrow, with a code and description column pair"]

    if out_degree >= 2 and measures and (rows >= median_rows * 10 or dates):
        reasons.append("{} outgoing foreign keys".format(out_degree))
        reasons.append("numeric measure columns: {}".format(", ".join(measures[:3])))
        if dates:
            reasons.append("has a date column")
        return "fact", reasons

    if in_degree >= 2 and out_degree <= 1:
        return "dimension", ["referenced by {} tables, references few".format(in_degree)]

    # A table referenced by something, referencing nothing itself, and carrying
    # descriptive text is a dimension even in a small model where nothing else
    # has got round to referencing it yet.
    descriptive = [
        column
        for column in table.get("columns", [])
        if (column.get("data_type") or "").lower() in ("varchar", "nvarchar", "char", "nchar")
    ]
    if in_degree >= 1 and out_degree == 0 and len(descriptive) >= 2:
        reasons.append("referenced by {} table(s) and references none".format(in_degree))
        reasons.append("{} descriptive text columns".format(len(descriptive)))
        return "dimension", reasons

    if in_degree or out_degree:
        return "operational", ["participates in the join graph without a clear role"]

    return "unknown", ["no keys and no name signal"]


def classify_tables(
    tables: Sequence[Dict[str, Any]],
    graph: JoinGraph,
    *,
    report_reads: Optional[Dict[str, int]] = None,
    max_tier1: int = 40,
    max_tier2: int = 150,
) -> Dict[str, Dict[str, Any]]:
    """Classify and rank every table. Returns a map keyed by ``schema.table``."""
    reads = report_reads or {}
    row_counts = sorted((table.get("row_count") or 0) for table in tables)
    median_rows = float(row_counts[len(row_counts) // 2]) if row_counts else 0.0

    role_weight = {
        "fact": 1.0, "dimension": 0.9, "bridge": 0.5, "lookup": 0.4,
        "operational": 0.3, "audit": 0.1, "staging": 0.0, "unknown": 0.2,
    }

    facts: Dict[str, Dict[str, Any]] = {}
    for table in tables:
        key = "{}.{}".format(table["schema"], table["name"])
        role, reasons = classify_table(table, graph, median_rows)
        facts[key] = {
            "key": key,
            "schema": table["schema"],
            "name": table["name"],
            "role": role,
            "reasons": reasons,
            "row_count": table.get("row_count") or 0,
            "column_count": len(table.get("columns", [])),
            "in_degree": graph.in_degree(key),
            "out_degree": graph.out_degree(key),
            "measure_columns": _measure_columns(table),
            "date_columns": _date_columns(table),
            "report_reads": reads.get(key.lower(), 0),
        }

    max_reads = max((f["report_reads"] for f in facts.values()), default=0) or 1
    max_in = max((f["in_degree"] for f in facts.values()), default=0) or 1
    max_out = max((f["out_degree"] for f in facts.values()), default=0) or 1

    for entry in facts.values():
        entry["score"] = round(
            5.0 * (entry["report_reads"] / max_reads)
            + 3.0 * (entry["in_degree"] / max_in)
            + 2.0 * (entry["out_degree"] / max_out)
            + 1.5 * (math.log10(max(entry["row_count"], 1)) / 9.0)
            + 1.0 * role_weight.get(entry["role"], 0.2)
            + (-2.0 if entry["row_count"] == 0 else 0.0),
            4,
        )

    # Stable ordering: score first, then name, so two runs agree exactly.
    ranked = sorted(facts.values(), key=lambda e: (-e["score"], e["key"]))
    for position, entry in enumerate(ranked):
        if position < max_tier1 and entry["score"] >= 0.5:
            entry["tier"] = 1
        elif position < max_tier1 + max_tier2 or entry["score"] >= 0.25:
            entry["tier"] = 2
        else:
            entry["tier"] = 3
        # A table an existing report actually reads is never buried in tier 3.
        # The reports are ground truth about what matters; the score is a guess.
        if entry["report_reads"] > 0:
            entry["tier"] = min(entry["tier"], 2)
    return facts


# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------


def _case_style(name: str) -> str:
    if "_" in name:
        return "UPPER_SNAKE" if name.isupper() else "snake_case"
    if name[:1].isupper():
        return "PascalCase"
    return "camelCase"


def detect_conventions(tables: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect the naming and modelling conventions this database follows.

    A rule is only reported when it holds for a clear majority, and it always
    carries its hit rate so a reader can calibrate how much to trust it. These
    are what let an agent guess correctly about a table it has not read.
    """
    if not tables:
        return {}

    total = len(tables)
    case_counts: Dict[str, int] = {}
    pk_styles: Dict[str, int] = {}
    no_pk = 0
    soft_delete: List[str] = []
    scd2: List[str] = []
    audit_columns: Dict[str, int] = {}
    string_types: Dict[str, int] = {}
    plural = 0

    for table in tables:
        style = _case_style(table["name"])
        case_counts[style] = case_counts.get(style, 0) + 1
        if re.search(r"(?<!s)s$", table["name"]):
            plural += 1

        pk = (table.get("primary_key") or {}).get("columns") or []
        if not pk:
            no_pk += 1
        elif len(pk) > 1:
            pk_styles["composite"] = pk_styles.get("composite", 0) + 1
        else:
            column = pk[0]
            lowered = column.lower()
            if lowered == "id":
                pk_styles["Id"] = pk_styles.get("Id", 0) + 1
            elif lowered == "{}id".format(table["name"].lower()):
                pk_styles["<Table>Id"] = pk_styles.get("<Table>Id", 0) + 1
            elif lowered == "{}key".format(table["name"].lower()):
                pk_styles["<Table>Key"] = pk_styles.get("<Table>Key", 0) + 1
            else:
                pk_styles["other"] = pk_styles.get("other", 0) + 1

        for column in table.get("columns", []):
            name = column["name"]
            if _SOFT_DELETE.match(name):
                soft_delete.append("{}.{}.{}".format(table["schema"], table["name"], name))
            if _SCD2.match(name):
                scd2.append("{}.{}.{}".format(table["schema"], table["name"], name))
            if _AUDIT_COLUMN.match(name):
                audit_columns[name] = audit_columns.get(name, 0) + 1
            data_type = (column.get("data_type") or "").lower()
            if data_type in ("varchar", "nvarchar", "char", "nchar"):
                string_types[data_type] = string_types.get(data_type, 0) + 1

    def _dominant(counts: Dict[str, int]) -> Optional[Dict[str, Any]]:
        if not counts:
            return None
        name, count = max(sorted(counts.items()), key=lambda item: item[1])
        return {"value": name, "count": count, "hit_rate": round(count / total, 3)}

    return {
        "table_case": _dominant(case_counts),
        "table_names_plural": round(plural / total, 3),
        "primary_key_style": _dominant(pk_styles),
        "tables_without_primary_key": {
            "count": no_pk,
            "hit_rate": round(no_pk / total, 3),
        },
        "soft_delete_columns": sorted(set(soft_delete))[:50],
        "scd2_columns": sorted(set(scd2))[:50],
        "audit_columns": dict(sorted(audit_columns.items(), key=lambda i: -i[1])[:10]),
        "string_types": dict(sorted(string_types.items(), key=lambda i: -i[1])),
    }


# ---------------------------------------------------------------------------
# The digest
# ---------------------------------------------------------------------------


def build_digest(
    *,
    database: str,
    tables: Sequence[Dict[str, Any]],
    foreign_keys: Sequence[Dict[str, Any]],
    schemas: Optional[Sequence[Dict[str, Any]]] = None,
    server_info: Optional[Dict[str, Any]] = None,
    routines: Optional[Sequence[Dict[str, Any]]] = None,
    report_reads: Optional[Dict[str, int]] = None,
    generated_at: Optional[str] = None,
    server_alias: Optional[str] = None,
    infer: bool = True,
    warnings: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Compose a complete, portable schema digest.

    Deterministic apart from ``generated_at``: every collection is sorted, so
    two runs against an unchanged database produce identical output and the
    digest doubles as a schema-drift detector.
    """
    graph = build_join_graph(tables, foreign_keys, infer=infer)
    facts = classify_tables(tables, graph, report_reads=report_reads)
    conventions = detect_conventions(tables)

    digest_tables = []
    for table in sorted(tables, key=lambda t: (t["schema"].lower(), t["name"].lower())):
        key = "{}.{}".format(table["schema"], table["name"])
        entry = facts.get(key, {})
        digest_tables.append(
            {
                "signature": table_signature(database, table["schema"], table["name"]),
                "schema": table["schema"],
                "name": table["name"],
                "object_type": table.get("object_type", "USER_TABLE"),
                "row_count": table.get("row_count") or 0,
                "size_mb": table.get("size_mb"),
                "description": table.get("description"),
                "role": entry.get("role", "unknown"),
                "role_reasons": entry.get("reasons", []),
                "tier": entry.get("tier", 3),
                "score": entry.get("score", 0.0),
                "report_reads": entry.get("report_reads", 0),
                "measure_columns": entry.get("measure_columns", []),
                "date_columns": entry.get("date_columns", []),
                "primary_key": table.get("primary_key"),
                "columns": table.get("columns", []),
                "indexes": table.get("indexes", []),
            }
        )

    edges = sorted(
        graph.edges,
        key=lambda e: (e["from_table"].lower(), e["to_table"].lower(), e["confidence"]),
    )

    return {
        "digest_schema_version": DIGEST_SCHEMA_VERSION,
        "generated_at": generated_at or "",
        "generator": "sqlserver-schema-mcp",
        "source": {
            "server_alias": server_alias or "sqlserver",
            "database": database,
            "product_version": (server_info or {}).get("product_version"),
            "edition": (server_info or {}).get("edition"),
            "collation": (server_info or {}).get("server_collation"),
        },
        "coverage": {
            "schemas": sorted({table["schema"] for table in tables}),
            "table_count": len(tables),
            "relationship_count": len(edges),
            "declared_relationships": sum(
                1 for e in edges if e["confidence"] == "declared"
            ),
            "inferred_relationships": sum(
                1 for e in edges if e["confidence"] == "inferred"
            ),
        },
        "schemas": list(schemas or []),
        "tables": digest_tables,
        "relationships": edges,
        "routines": sorted(
            list(routines or []),
            key=lambda r: (str(r.get("schema_name", "")).lower(), str(r.get("object_name", "")).lower()),
        ),
        "conventions": conventions,
        "warnings": sorted(set(warnings or [])),
    }


def validate_digest(digest: Dict[str, Any]) -> List[str]:
    """Return a list of problems with a digest; empty means it is usable."""
    problems: List[str] = []
    if not isinstance(digest, dict):
        return ["Digest is not a JSON object."]
    version = str(digest.get("digest_schema_version") or "")
    if not version:
        problems.append("Missing digest_schema_version.")
    elif version.split(".")[0] != DIGEST_SCHEMA_VERSION.split(".")[0]:
        problems.append(
            "Digest schema major version {} does not match this generator's "
            "{}.".format(version, DIGEST_SCHEMA_VERSION)
        )
    if not (digest.get("source") or {}).get("database"):
        problems.append("Missing source.database.")
    if not isinstance(digest.get("tables"), list):
        problems.append("Missing or malformed tables array.")
    else:
        for index, table in enumerate(digest["tables"]):
            if not table.get("name") or not table.get("schema"):
                problems.append("Table at index {} has no schema or name.".format(index))
            if not isinstance(table.get("columns"), list):
                problems.append(
                    "Table {} has no columns array.".format(table.get("name", index))
                )
    if not isinstance(digest.get("relationships"), list):
        problems.append("Missing or malformed relationships array.")
    return problems
