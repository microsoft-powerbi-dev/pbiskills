"""
skillgen.py - render a schema digest into a loadable skill pack.

Deterministic templating and nothing else: f-strings over sorted collections,
no Jinja, no network, no model call. Given the same digest and options the
output is byte-identical, which is what makes the pack diffable as a
schema-drift detector and what makes the golden-file test in
``tests/test_skillgen.py`` meaningful.

A generated pack describes an internal database. It names servers, databases,
tables, and columns, so it is written outside version control by default and
``guardrails.resolve_artifact_path`` refuses to write it anywhere git would
pick it up. Every pack carries its own ``.gitignore`` and a do-not-commit
header as second and third lines of defence.

This module imports no database driver. It reads a JSON file, which is the
point of the digest seam: a pack can be regenerated and reviewed on a machine
that has no ODBC driver and no access to the server.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import analyze  # noqa: E402
import guardrails  # noqa: E402

GENERATOR_VERSION = "1.0"

_PACK_GITIGNORE = """# Generated schema skill pack: contains internal database identifiers.
# Never commit. See the header of SKILL.md.
*
"""

_DO_NOT_COMMIT = """> **Generated artifact, do not commit.** This pack describes an internal
> database: it names real schemas, tables, and columns. It is produced by
> `skills/claude-skills/sql-server-schema/scripts/skillgen.py` and lives
> outside version control by default. Regenerate it rather than editing it by
> hand, and never copy it into a public repository. `REDACTIONS.md` lists what
> was withheld.
"""


def slugify(value: str) -> str:
    """Lower-case, hyphen-separated, filesystem-safe."""
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "database"


def _fmt_rows(count: Optional[int]) -> str:
    """Row counts to three significant figures: precision buys nothing here."""
    number = int(count or 0)
    if number >= 1_000_000_000:
        return "{:.2f}B".format(number / 1_000_000_000)
    if number >= 1_000_000:
        return "{:.2f}M".format(number / 1_000_000)
    if number >= 1_000:
        return "{:.1f}K".format(number / 1_000)
    return str(number)


def _sensitive_note(column: Dict[str, Any]) -> Optional[Dict[str, str]]:
    return guardrails.classify_column_sensitivity(
        column.get("name", ""), column.get("data_type", "")
    )


def _column_note(
    column: Dict[str, Any],
    primary_key: Sequence[str],
    outbound: Dict[str, str],
) -> str:
    """The Notes cell for one column row."""
    notes: List[str] = []
    if column["name"] in primary_key:
        notes.append("PK")
    if column.get("is_identity"):
        notes.append("identity")
    if column["name"] in outbound:
        notes.append("FK to `{}`".format(outbound[column["name"]]))
    if column.get("is_computed"):
        notes.append("computed")
    if column.get("default"):
        notes.append("default {}".format(column["default"]))
    sensitivity = _sensitive_note(column)
    if sensitivity:
        notes.append(
            "**withheld: sensitive ({})**".format(sensitivity["category"])
            if sensitivity["tier"] == "hard_deny"
            else "**sensitive ({})**".format(sensitivity["category"])
        )
    if column.get("description"):
        notes.append(str(column["description"]))
    return "; ".join(notes)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_skill_md(digest: Dict[str, Any], context: Dict[str, Any]) -> str:
    """The pack entry point: small on purpose, and it never inlines the catalog."""
    database = digest["source"]["database"]
    tables = digest["tables"]
    tier1 = [t for t in tables if t.get("tier") == 1]
    conventions = digest.get("conventions") or {}

    trigger_names = sorted(
        {slugify(t["name"]).replace("-", " ") for t in tier1[:20]}
        | {database.lower()}
        | {s.lower() for s in digest["coverage"]["schemas"]}
    )

    lines: List[str] = []
    lines.append("---")
    lines.append('name: "Schema: {}"'.format(database))
    lines.append("description: >")
    lines.append(
        "  Use this skill when writing T-SQL, designing an SSRS dataset, or planning"
    )
    lines.append(
        "  a Power BI model against the {} database. Covers {} tables across".format(
            database, len(tables)
        )
    )
    lines.append(
        "  {} schemas, {} classified fact tables and {} dimensions, the declared".format(
            len(digest["coverage"]["schemas"]),
            sum(1 for t in tables if t["role"] == "fact"),
            sum(1 for t in tables if t["role"] == "dimension"),
        )
    )
    lines.append(
        "  and inferred foreign-key join graph, and the naming conventions this"
    )
    lines.append(
        "  database follows. Read references/00-index.md first: it names which"
    )
    lines.append(
        "  per-schema file answers which question. Example requests: \"what joins"
    )
    lines.append(
        "  X to Y\", \"which table holds the amounts\", \"write a dataset query for"
    )
    lines.append("  this database\".")
    lines.append("allowed-tools:")
    # Read-only: a schema pack is reference material and must not be able to
    # modify anything.
    for tool in ("Read", "Glob", "Grep"):
        lines.append("  - {}".format(tool))
    lines.append("triggers:")
    for trigger in trigger_names[:40]:
        lines.append("  - {}".format(trigger))
    lines.append("metadata:")
    lines.append("  generated_by: skills/claude-skills/sql-server-schema/scripts/skillgen.py")
    lines.append("  generator_version: \"{}\"".format(GENERATOR_VERSION))
    lines.append("  digest_schema_version: \"{}\"".format(digest["digest_schema_version"]))
    lines.append("  source_database: {}".format(database))
    lines.append("  source_server_alias: {}".format(digest["source"].get("server_alias")))
    lines.append("  table_count: {}".format(len(tables)))
    lines.append("  samples_included: {}".format(str(context["include_samples"]).lower()))
    lines.append("  contains_internal_identifiers: true")
    lines.append("  do_not_commit: true")
    lines.append("---")
    lines.append("")
    lines.append("# Schema: {}".format(database))
    lines.append("")
    lines.append(_DO_NOT_COMMIT)
    lines.append("## What this database is")
    lines.append("")
    lines.append(
        "{} tables across {} schemas ({}). {} declared foreign keys and {} "
        "inferred relationships.".format(
            len(tables),
            len(digest["coverage"]["schemas"]),
            ", ".join("`{}`".format(s) for s in digest["coverage"]["schemas"]),
            digest["coverage"]["declared_relationships"],
            digest["coverage"]["inferred_relationships"],
        )
    )
    lines.append("")
    roles: Dict[str, int] = {}
    for table in tables:
        roles[table["role"]] = roles.get(table["role"], 0) + 1
    lines.append(
        "Role mix: "
        + ", ".join("{} {}".format(count, role) for role, count in sorted(roles.items()))
        + "."
    )
    lines.append("")
    lines.append("## Read this in order")
    lines.append("")
    lines.append("1. `references/00-index.md`, to locate the table you need.")
    lines.append("2. The per-schema file that index points you at.")
    lines.append("3. `references/01-join-graph.md` when you need to join across tables.")
    lines.append("4. `references/02-conventions.md` before writing any WHERE clause.")
    lines.append("")
    lines.append("## The tables that matter most")
    lines.append("")
    lines.append("| Table | Role | Rows | Joins to | Read by reports |")
    lines.append("| --- | --- | --- | --- | --- |")
    for table in tier1[:10]:
        key = "{}.{}".format(table["schema"], table["name"])
        joins = sorted(
            {
                edge["to_table"]
                for edge in digest["relationships"]
                if edge["from_table"] == key
            }
        )
        lines.append(
            "| `{}` | {} | {} | {} | {} |".format(
                key,
                table["role"],
                _fmt_rows(table["row_count"]),
                ", ".join("`{}`".format(j) for j in joins[:3]) or "none",
                table.get("report_reads", 0),
            )
        )
    lines.append("")
    lines.append("## Conventions this database follows")
    lines.append("")
    for line in _convention_lines(conventions):
        lines.append("- {}".format(line))
    lines.append("")
    lines.append("## Bundled assets")
    lines.append("")
    lines.append("| File | Covers |")
    lines.append("| --- | --- |")
    for name, purpose in context["asset_index"]:
        lines.append("| `{}` | {} |".format(name, purpose))
    lines.append("")
    lines.append("## What this pack does not tell you")
    lines.append("")
    lines.append("- No data values." + ("" if context["include_samples"] else " No sample rows were collected."))
    lines.append("- No row-level security, permissions, or query plans.")
    lines.append("- Nothing about data quality: a column existing does not mean it is populated.")
    lines.append(
        "- Roles and tiers are name, key, and cardinality heuristics, not semantics. "
        "Each table's `role_reasons` in `schema_digest.json` says which rule fired."
    )
    lines.append(
        "- The schema as of generation. Regenerate if the database has changed."
    )
    lines.append("")
    lines.append("## Related material")
    lines.append("")
    lines.append(
        "- `skills/claude-skills/sql-server-schema/SKILL.md` in the pbiskills "
        "repository: how this pack was produced and how to query the database live."
    )
    lines.append(
        "- `skills/report-lineage/`: which reports read these tables."
    )
    lines.append(
        "- `skills/claude-skills/rdl-generation/`: turning one of these queries "
        "into an SSRS report definition."
    )
    lines.append("")
    return "\n".join(lines)


def _convention_lines(conventions: Dict[str, Any]) -> List[str]:
    """Human-readable convention statements, each carrying its hit rate."""
    lines: List[str] = []
    case = conventions.get("table_case")
    if case:
        lines.append(
            "Table names are {} ({:.0%} of tables).".format(case["value"], case["hit_rate"])
        )
    pk = conventions.get("primary_key_style")
    if pk:
        lines.append(
            "Primary keys are named `{}` ({:.0%}).".format(pk["value"], pk["hit_rate"])
        )
    no_pk = conventions.get("tables_without_primary_key") or {}
    if no_pk.get("count"):
        lines.append(
            "{} tables ({:.0%}) have no primary key at all. Do not assume a "
            "unique row identifier exists.".format(no_pk["count"], no_pk["hit_rate"])
        )
    if conventions.get("soft_delete_columns"):
        lines.append(
            "Soft-delete columns are present ({} found, for example `{}`). A query "
            "that omits the soft-delete filter is silently wrong.".format(
                len(conventions["soft_delete_columns"]),
                conventions["soft_delete_columns"][0],
            )
        )
    if conventions.get("scd2_columns"):
        lines.append(
            "Type-2 slowly-changing dimension columns are present (for example "
            "`{}`). A naive join to these dimensions fans out; filter to the "
            "current row.".format(conventions["scd2_columns"][0])
        )
    if conventions.get("audit_columns"):
        names = ", ".join("`{}`".format(n) for n in list(conventions["audit_columns"])[:4])
        lines.append("Recurring audit columns you can usually ignore: {}.".format(names))
    string_types = conventions.get("string_types") or {}
    if string_types:
        dominant = next(iter(string_types))
        lines.append("String columns are predominantly `{}`.".format(dominant))
    return lines or ["No strong conventions were detected."]


def render_index(digest: Dict[str, Any], context: Dict[str, Any]) -> str:
    """The locator. A Grep for a table name must land in one hop."""
    tables = digest["tables"]
    by_schema: Dict[str, List[Dict[str, Any]]] = {}
    for table in tables:
        by_schema.setdefault(table["schema"], []).append(table)

    lines = ["# {}: index".format(digest["source"]["database"]), ""]
    lines.append(
        "{} tables across {} schemas. Detail lives in the per-schema files below. "
        "Find the table here first, then open only the file it points to.".format(
            len(tables), len(by_schema)
        )
    )
    lines.append("")
    lines.append("## Where to look")
    lines.append("")
    lines.append("| Schema | Tables | Tier 1 | File(s) |")
    lines.append("| --- | --- | --- | --- |")
    for schema in sorted(by_schema):
        entries = by_schema[schema]
        files = context["schema_files"].get(schema, [])
        lines.append(
            "| `{}` | {} | {} | {} |".format(
                schema,
                len(entries),
                sum(1 for e in entries if e.get("tier") == 1),
                ", ".join("`{}`".format(f) for f in files),
            )
        )
    lines.append("")

    for tier, heading, note in (
        (1, "Tier 1: the tables you will actually query", True),
        (2, "Tier 2: supporting tables", False),
    ):
        entries = sorted(
            [t for t in tables if t.get("tier") == tier],
            key=lambda t: (-t.get("score", 0), t["schema"], t["name"]),
        )
        if not entries:
            continue
        lines.append("## {}".format(heading))
        lines.append("")
        lines.append("| Table | Role | Rows | In file |")
        lines.append("| --- | --- | --- | --- |")
        for entry in entries:
            key = "{}.{}".format(entry["schema"], entry["name"])
            lines.append(
                "| `{}` | {} | {} | `{}` |".format(
                    key,
                    entry["role"],
                    _fmt_rows(entry["row_count"]),
                    context["table_file"].get(key, ""),
                )
            )
        lines.append("")

    tier3 = sorted(
        [t for t in tables if t.get("tier") == 3],
        key=lambda t: (t["schema"], t["name"]),
    )
    if tier3:
        lines.append("## Tier 3: everything else")
        lines.append("")
        by_schema3: Dict[str, List[str]] = {}
        for entry in tier3:
            by_schema3.setdefault(entry["schema"], []).append(entry["name"])
        for schema in sorted(by_schema3):
            lines.append(
                "`{}`: {}".format(schema, ", ".join(sorted(by_schema3[schema])))
            )
            lines.append("")

    lines.append("## Alphabetical locator")
    lines.append("")
    locator = sorted(
        ("{}.{}".format(t["schema"], t["name"]), context["table_file"].get("{}.{}".format(t["schema"], t["name"]), ""))
        for t in tables
    )
    lines.append(
        " · ".join("`{}` -> `{}`".format(key, path) for key, path in locator)
    )
    lines.append("")
    return "\n".join(lines)


def render_schema_reference(
    digest: Dict[str, Any],
    schema: str,
    part: int,
    total_parts: int,
    tables: Sequence[Dict[str, Any]],
    context: Dict[str, Any],
) -> str:
    """One schema's table catalog, chunked so each file stays readable."""
    lines = ["# Schema `{}`".format(schema)]
    if total_parts > 1:
        lines.append("")
        lines.append("Part {} of {}.".format(part, total_parts))
    lines.append("")

    relationships = digest["relationships"]
    for table in tables:
        key = "{}.{}".format(table["schema"], table["name"])
        primary_key = (table.get("primary_key") or {}).get("columns") or []
        outbound: Dict[str, str] = {}
        for edge in relationships:
            if edge["from_table"] == key:
                for index, column in enumerate(edge["from_columns"]):
                    target_column = (
                        edge["to_columns"][index]
                        if index < len(edge["to_columns"])
                        else ""
                    )
                    suffix = "" if edge["confidence"] == "declared" else " (inferred)"
                    outbound[column] = "{}.{}{}".format(
                        edge["to_table"], target_column, suffix
                    )

        lines.append(
            "### `{}`  ({}, tier {}, {} rows)".format(
                key, table["role"], table.get("tier", 3), _fmt_rows(table["row_count"])
            )
        )
        lines.append("")
        if table.get("description"):
            lines.append(str(table["description"]))
            lines.append("")
        if table.get("role_reasons"):
            lines.append("Classified {}: {}.".format(table["role"], "; ".join(table["role_reasons"])))
            lines.append("")
        lines.append(
            "Primary key: {}".format(
                ", ".join("`{}`".format(c) for c in primary_key) or "none declared"
            )
        )
        lines.append("")
        lines.append("| Column | Type | Null | Notes |")
        lines.append("| --- | --- | --- | --- |")
        columns = table.get("columns", [])
        shown = columns[: context["max_columns_per_table"]]
        for column in shown:
            lines.append(
                "| `{}` | {} | {} | {} |".format(
                    column["name"],
                    column.get("type", column.get("data_type", "")),
                    "yes" if column.get("nullable") else "no",
                    _column_note(column, primary_key, outbound),
                )
            )
        if len(columns) > len(shown):
            lines.append(
                "| ... | | | {} further columns, see `schema_digest.json` |".format(
                    len(columns) - len(shown)
                )
            )
        lines.append("")
        inbound = sorted(
            {e["from_table"] for e in relationships if e["to_table"] == key}
        )
        outbound_tables = sorted(
            {e["to_table"] for e in relationships if e["from_table"] == key}
        )
        lines.append(
            "Joins out: {}".format(
                ", ".join("`{}`".format(t) for t in outbound_tables) or "none"
            )
        )
        lines.append(
            "Joined from: {}".format(
                ", ".join("`{}`".format(t) for t in inbound) or "none"
            )
        )
        if table.get("indexes"):
            index_text = "; ".join(
                "{} on ({})".format(
                    index["name"], ", ".join(index.get("key_columns", []))
                )
                for index in table["indexes"][:5]
            )
            lines.append("Indexes: {}".format(index_text))
        if table.get("report_reads"):
            lines.append("Read by {} report(s).".format(table["report_reads"]))
        lines.append("")
    return "\n".join(lines)


def render_join_graph(digest: Dict[str, Any], context: Dict[str, Any]) -> str:
    """The join graph, as a table of edges plus a Mermaid diagram."""
    relationships = digest["relationships"]
    keep = {
        "{}.{}".format(t["schema"], t["name"])
        for t in digest["tables"]
        if t.get("tier", 3) <= 2
    }
    edges = [
        edge
        for edge in relationships
        if edge["from_table"] in keep and edge["to_table"] in keep
    ]

    lines = ["# Join graph", ""]
    lines.append(
        "{} relationships in total, {} shown here (tier 1 and tier 2 tables). "
        "Declared foreign keys are enforced by the database; inferred edges are "
        "name matches and should be verified before you rely on them.".format(
            len(relationships), len(edges)
        )
    )
    lines.append("")
    lines.append("| From | To | On | Confidence |")
    lines.append("| --- | --- | --- | --- |")
    for edge in edges:
        pairs = ", ".join(
            "{} = {}".format(a, b)
            for a, b in zip(edge["from_columns"], edge["to_columns"])
        )
        lines.append(
            "| `{}` | `{}` | {} | {} |".format(
                edge["from_table"], edge["to_table"], pairs, edge["confidence"]
            )
        )
    lines.append("")
    lines.append("## Join clauses")
    lines.append("")
    lines.append("```sql")
    for edge in edges:
        conditions = " AND ".join(
            "t.{} = r.{}".format(a, b)
            for a, b in zip(edge["from_columns"], edge["to_columns"])
        )
        marker = "" if edge["confidence"] == "declared" else "  -- inferred, verify"
        lines.append(
            "-- {} -> {}{}".format(edge["from_table"], edge["to_table"], marker)
        )
        lines.append(
            "INNER JOIN {} AS r ON {}".format(edge["to_table"], conditions)
        )
    lines.append("```")
    lines.append("")
    lines.append("## Diagram")
    lines.append("")
    lines.append("```mermaid")
    lines.append("graph LR")
    for edge in edges[:120]:
        style = "-->" if edge["confidence"] == "declared" else "-.->"
        lines.append(
            "  {}{}{}".format(
                _mermaid_id(edge["from_table"]), style, _mermaid_id(edge["to_table"])
            )
        )
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _mermaid_id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def render_conventions(digest: Dict[str, Any]) -> str:
    conventions = digest.get("conventions") or {}
    lines = ["# Conventions", ""]
    lines.append(
        "Detected by counting patterns across the whole database. Each statement "
        "carries its hit rate: treat a rule at 70% as a strong hint, not a law."
    )
    lines.append("")
    for line in _convention_lines(conventions):
        lines.append("- {}".format(line))
    lines.append("")
    if conventions.get("soft_delete_columns"):
        lines.append("## Soft-delete columns")
        lines.append("")
        lines.append(
            "Every query against these tables needs the soft-delete predicate, or "
            "it silently returns retired rows."
        )
        lines.append("")
        for column in conventions["soft_delete_columns"]:
            lines.append("- `{}`".format(column))
        lines.append("")
    if conventions.get("scd2_columns"):
        lines.append("## Slowly-changing dimension columns")
        lines.append("")
        lines.append(
            "These dimensions keep history. Joining without a currency filter "
            "multiplies fact rows by the number of versions."
        )
        lines.append("")
        for column in conventions["scd2_columns"]:
            lines.append("- `{}`".format(column))
        lines.append("")
    return "\n".join(lines)


def render_query_recipes(digest: Dict[str, Any]) -> str:
    """A starting query per tier-1 fact table, built from the join graph."""
    conventions = digest.get("conventions") or {}
    soft_delete_columns = {
        entry.split(".")[-1] for entry in conventions.get("soft_delete_columns", [])
    }
    lines = ["# Query recipes", ""]
    lines.append(
        "Generated from the join graph, not hand-written and not executed. Treat "
        "each as a starting point: check the grain before you trust a total."
    )
    lines.append("")

    facts = [
        t for t in digest["tables"] if t["role"] == "fact" and t.get("tier") == 1
    ]
    if not facts:
        lines.append("No tier 1 fact tables were classified in this database.")
        lines.append("")
        return "\n".join(lines)

    for fact in facts[:10]:
        key = "{}.{}".format(fact["schema"], fact["name"])
        joins = [
            edge for edge in digest["relationships"] if edge["from_table"] == key
        ][:3]
        measures = fact.get("measure_columns", [])[:2]
        dates = fact.get("date_columns", [])
        fact_soft_delete = [
            column["name"]
            for column in fact.get("columns", [])
            if column["name"] in soft_delete_columns
        ]

        lines.append("## {}".format(key))
        lines.append("")
        lines.append("```sql")
        lines.append("SELECT")
        lines.append("    COUNT_BIG(*) AS RowCount" + ("," if measures else ""))
        for position, measure in enumerate(measures):
            suffix = "," if position < len(measures) - 1 else ""
            lines.append("    SUM(f.{}) AS {}{}".format(measure, measure, suffix))
        lines.append("FROM {} AS f".format(key))
        for index, edge in enumerate(joins):
            alias = "j{}".format(index)
            conditions = " AND ".join(
                "{}.{} = f.{}".format(alias, b, a)
                for a, b in zip(edge["from_columns"], edge["to_columns"])
            )
            comment = "" if edge["confidence"] == "declared" else "  -- inferred, verify"
            lines.append(
                "INNER JOIN {} AS {} ON {}{}".format(
                    edge["to_table"], alias, conditions, comment
                )
            )
        where: List[str] = []
        for column in fact_soft_delete:
            where.append("f.{} = 0  -- soft delete convention detected".format(column))
        if dates:
            where.append("f.{} >= @FromDate".format(dates[0]))
            where.append("f.{} <  @ToDate   -- half-open range".format(dates[0]))
        if where:
            lines.append("WHERE " + "\n  AND ".join(where))
        lines.append(";")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def render_redactions(digest: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Always written. What was withheld, and what still needs a human eye."""
    findings: List[Dict[str, str]] = []
    categories: Dict[str, int] = {}
    for table in digest["tables"]:
        for column in table.get("columns", []):
            sensitivity = _sensitive_note(column)
            if not sensitivity:
                continue
            categories[sensitivity["category"]] = (
                categories.get(sensitivity["category"], 0) + 1
            )
            findings.append(
                {
                    "table": "{}.{}".format(table["schema"], table["name"]),
                    "column": column["name"],
                    "category": sensitivity["category"],
                    "tier": sensitivity["tier"],
                }
            )

    lines = ["# Redaction report", ""]
    lines.append(
        "Policy: {}. Digest schema version {}.".format(
            "samples included" if context["include_samples"] else "no sample data",
            digest["digest_schema_version"],
        )
    )
    lines.append("")
    lines.append(
        "## Flagged: {} columns across {} tables".format(
            len(findings), len({f["table"] for f in findings})
        )
    )
    lines.append("")
    if findings:
        lines.append("| Table | Column | Category | Tier |")
        lines.append("| --- | --- | --- | --- |")
        for finding in sorted(
            findings, key=lambda f: (f["tier"], f["table"], f["column"])
        ):
            lines.append(
                "| `{}` | `{}` | {} | {} |".format(
                    finding["table"], finding["column"], finding["category"], finding["tier"]
                )
            )
        lines.append("")
        lines.append(
            "Column *names* are shown deliberately. An agent that does not know a "
            "sensitive column exists will write `SELECT *` and pull it; naming the "
            "column and marking it do-not-select is the safer failure mode. No "
            "values from these columns were read or written."
        )
        lines.append("")
        lines.append(
            "Categories: "
            + " · ".join(
                "{} {}".format(name, count)
                for name, count in sorted(categories.items())
            )
        )
        lines.append("")
    else:
        lines.append("No columns matched the sensitivity heuristics.")
        lines.append("")
        lines.append(
            "This is a name-based heuristic, not a compliance control. A column it "
            "did not flag can still hold sensitive data."
        )
        lines.append("")

    lines.append("## Not withheld, review before sharing")
    lines.append("")
    lines.append(
        "- The server alias `{}` and database name `{}` appear throughout this "
        "pack.".format(digest["source"].get("server_alias"), digest["source"]["database"])
    )
    total_columns = sum(len(t.get("columns", [])) for t in digest["tables"])
    lines.append(
        "- {} internal table names and {} column names are in the clear.".format(
            len(digest["tables"]), total_columns
        )
    )
    lines.append("- This pack is not safe for a public repository.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_skill_pack(
    digest: Dict[str, Any],
    out_dir: Optional[str] = None,
    *,
    include_samples: bool = False,
    max_columns_per_table: int = 60,
    max_tables_per_file: int = 40,
    allow_in_repo: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Render a digest into a skill pack on disk.

    Returns the manifest, the file list, and any warnings. With ``dry_run`` the
    files are rendered and measured but nothing is written, which is the safe
    first move against a real database.
    """
    problems = analyze.validate_digest(digest)
    if problems:
        return {"ok": False, "errors": problems, "files": []}

    database = digest["source"]["database"]
    default_name = Path("schema-skills") / slugify(database)
    target = guardrails.resolve_artifact_path(
        out_dir, str(default_name), allow_in_repo=allow_in_repo
    )

    # Plan the per-schema file layout first: both the index and the per-table
    # cross-references need to know which file a table ended up in.
    by_schema: Dict[str, List[Dict[str, Any]]] = {}
    for table in digest["tables"]:
        by_schema.setdefault(table["schema"], []).append(table)

    schema_files: Dict[str, List[str]] = {}
    table_file: Dict[str, str] = {}
    planned: List[tuple] = []
    for schema in sorted(by_schema):
        entries = sorted(
            by_schema[schema], key=lambda t: (t.get("tier", 3), t["name"].lower())
        )
        chunks = [
            entries[index : index + max_tables_per_file]
            for index in range(0, len(entries), max_tables_per_file)
        ] or [[]]
        for part, chunk in enumerate(chunks, start=1):
            filename = "schema-{}{}.md".format(
                slugify(schema), "" if len(chunks) == 1 else "-{}".format(part)
            )
            schema_files.setdefault(schema, []).append(filename)
            for entry in chunk:
                table_file["{}.{}".format(entry["schema"], entry["name"])] = (
                    "references/" + filename
                )
            planned.append((schema, part, len(chunks), chunk, filename))

    asset_index = [
        ("references/00-index.md", "Locate any table, and which file describes it"),
        ("references/01-join-graph.md", "Declared and inferred joins, with clauses"),
        ("references/02-conventions.md", "Naming, keys, soft deletes, and SCD columns"),
        ("references/03-query-recipes.md", "A starting query per tier 1 fact table"),
        ("schema_digest.json", "The machine-readable digest this pack was built from"),
        ("REDACTIONS.md", "Which columns were flagged sensitive, and what was withheld"),
    ]
    for schema in sorted(schema_files):
        for filename in schema_files[schema]:
            asset_index.append(
                ("references/" + filename, "Table catalog for schema `{}`".format(schema))
            )

    context = {
        "include_samples": include_samples,
        "max_columns_per_table": max_columns_per_table,
        "schema_files": schema_files,
        "table_file": table_file,
        "asset_index": sorted(asset_index),
    }

    files: Dict[str, str] = {
        ".gitignore": _PACK_GITIGNORE,
        "SKILL.md": render_skill_md(digest, context),
        "REDACTIONS.md": render_redactions(digest, context),
        "references/00-index.md": render_index(digest, context),
        "references/01-join-graph.md": render_join_graph(digest, context),
        "references/02-conventions.md": render_conventions(digest),
        "references/03-query-recipes.md": render_query_recipes(digest),
        "schema_digest.json": json.dumps(digest, indent=2, sort_keys=True) + "\n",
    }
    for schema, part, total_parts, chunk, filename in planned:
        files["references/" + filename] = render_schema_reference(
            digest, schema, part, total_parts, chunk, context
        )

    written: List[Dict[str, Any]] = []
    for relative in sorted(files):
        content = files[relative]
        path = target / relative
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"path": str(path), "bytes": len(content.encode("utf-8"))})

    return {
        "ok": True,
        "out_dir": str(target),
        "dry_run": dry_run,
        "files": written,
        "manifest": {
            "generator_version": GENERATOR_VERSION,
            "digest_schema_version": digest["digest_schema_version"],
            "database": database,
            "server_alias": digest["source"].get("server_alias"),
            "table_count": len(digest["tables"]),
            "samples_included": include_samples,
            "contains_internal_identifiers": True,
            "do_not_commit": True,
        },
    }


def load_digest(path: str) -> Dict[str, Any]:
    """Read and validate a digest from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = analyze.validate_digest(data)
    if problems:
        raise ValueError("Digest at {} is not usable: {}".format(path, "; ".join(problems)))
    return data
