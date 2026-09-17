"""Tests for the join graph, classification, conventions, and digest assembly.

The schema modelled here is invented (a small orders star schema). Nothing in
this suite reflects a real database, because this repository is public.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyze  # noqa: E402


def _column(name, data_type="int", nullable=False, **extra):
    column = {
        "name": name,
        "data_type": data_type,
        "type": data_type,
        "nullable": nullable,
        "is_identity": extra.get("is_identity", False),
        "is_computed": False,
        "default": extra.get("default"),
        "description": extra.get("description"),
    }
    return column


def _table(schema, name, columns, pk=None, rows=0, **extra):
    return {
        "schema": schema,
        "name": name,
        "columns": columns,
        "primary_key": {"name": "PK_" + name, "columns": pk} if pk else None,
        "row_count": rows,
        "object_type": "USER_TABLE",
        **extra,
    }


def _star():
    tables = [
        _table(
            "dbo", "FactOrder",
            [
                _column("OrderId", is_identity=True),
                _column("CustomerId"),
                _column("ProductId"),
                _column("OrderDate", "date"),
                _column("PaidAmount", "decimal"),
                _column("Quantity"),
                _column("IsDeleted", "bit"),
            ],
            pk=["OrderId"], rows=4_000_000,
        ),
        _table(
            "dbo", "DimCustomer",
            [
                _column("CustomerId", is_identity=True),
                _column("CustomerName", "varchar"),
                _column("City", "varchar"),
                _column("IsCurrent", "bit"),
                _column("EffectiveFrom", "date"),
            ],
            pk=["CustomerId"], rows=52_000,
        ),
        _table(
            "dbo", "DimProduct",
            [
                _column("ProductId", is_identity=True),
                _column("ProductName", "varchar"),
                _column("CategoryCode", "varchar"),
            ],
            pk=["ProductId"], rows=1_400,
        ),
        _table(
            "dbo", "StatusLookup",
            [_column("StatusCode", "varchar"), _column("Description", "varchar")],
            pk=["StatusCode"], rows=8,
        ),
        _table(
            "stg", "stg_OrderImport",
            [_column("OrderId"), _column("Payload", "nvarchar")],
            rows=0,
        ),
        _table(
            "dbo", "OrderAudit",
            [_column("AuditId", is_identity=True), _column("ChangedOn", "datetime")],
            pk=["AuditId"], rows=900_000,
        ),
    ]
    foreign_keys = [
        {
            "name": "FK_FactOrder_DimCustomer",
            "parent_schema": "dbo", "parent_table": "FactOrder",
            "parent_columns": ["CustomerId"],
            "referenced_schema": "dbo", "referenced_table": "DimCustomer",
            "referenced_columns": ["CustomerId"],
            "is_not_trusted": False,
        },
        {
            "name": "FK_FactOrder_DimProduct",
            "parent_schema": "dbo", "parent_table": "FactOrder",
            "parent_columns": ["ProductId"],
            "referenced_schema": "dbo", "referenced_table": "DimProduct",
            "referenced_columns": ["ProductId"],
            "is_not_trusted": False,
        },
    ]
    return tables, foreign_keys


# ---------------------------------------------------------------------------
# Signature: the join key shared with reportlineage
# ---------------------------------------------------------------------------


def test_signature_matches_the_reportlineage_format():
    assert analyze.table_signature("SalesDW", "dbo", "FactOrder") == "salesdw::dbo.factorder"
    # Schema defaults to dbo, exactly as TableRef.signature() does.
    assert analyze.table_signature("SalesDW", None, "FactOrder") == "salesdw::dbo.factorder"
    assert analyze.table_signature("SALESDW", "DBO", "FACTORDER") == "salesdw::dbo.factorder"


def test_signature_matches_reportlineage_when_it_is_importable():
    """If reportlineage is on the path, the two must agree byte for byte."""
    lineage = Path(__file__).resolve().parents[3] / "report-lineage"
    if not lineage.exists():
        pytest.skip("report-lineage not present")
    sys.path.insert(0, str(lineage))
    try:
        from reportlineage.sql_refs import TableRef
    except Exception:
        pytest.skip("reportlineage not importable")
    ref = TableRef(table="FactOrder", schema="dbo", database="SalesDW")
    assert ref.signature() == analyze.table_signature("SalesDW", "dbo", "FactOrder")
    # And the dbo-defaulting rule agrees too.
    assert TableRef(table="FactOrder", database="SalesDW").signature() == (
        analyze.table_signature("SalesDW", None, "FactOrder")
    )


# ---------------------------------------------------------------------------
# Join graph
# ---------------------------------------------------------------------------


def test_declared_edges_come_from_foreign_keys():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    assert len(graph.edges) == 2
    assert all(edge["confidence"] == "declared" for edge in graph.edges)
    assert graph.in_degree("dbo.DimCustomer") == 1
    assert graph.out_degree("dbo.FactOrder") == 2


def test_inference_finds_undeclared_joins():
    tables, _ = _star()
    # No declared foreign keys at all: the estate dropped them for load speed.
    graph = analyze.build_join_graph(tables, [], infer=True)
    inferred = {(e["from_table"], e["to_table"]) for e in graph.edges}
    assert ("dbo.FactOrder", "dbo.DimCustomer") in inferred
    assert ("dbo.FactOrder", "dbo.DimProduct") in inferred
    assert all(edge["confidence"] == "inferred" for edge in graph.edges)


def test_inference_does_not_duplicate_a_declared_edge():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=True)
    pairs = [(e["from_table"], e["to_table"]) for e in graph.edges]
    assert pairs.count(("dbo.FactOrder", "dbo.DimCustomer")) == 1


def test_shortest_join_path():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    path = analyze.shortest_join_path(graph, "dbo.DimCustomer", "dbo.DimProduct")
    assert path is not None
    assert len(path) == 2  # via FactOrder
    assert analyze.shortest_join_path(graph, "dbo.DimCustomer", "dbo.Nope") is None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_roles_are_assigned_as_expected():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    facts = analyze.classify_tables(tables, graph)
    assert facts["dbo.FactOrder"]["role"] == "fact"
    assert facts["dbo.DimCustomer"]["role"] == "dimension"
    assert facts["dbo.StatusLookup"]["role"] == "lookup"
    assert facts["stg.stg_OrderImport"]["role"] == "staging"
    assert facts["dbo.OrderAudit"]["role"] == "audit"


def test_every_classification_carries_its_reasons():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    facts = analyze.classify_tables(tables, graph)
    for entry in facts.values():
        assert entry["reasons"], "{} has no stated reason".format(entry["key"])


def test_measure_and_date_columns_are_detected():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    facts = analyze.classify_tables(tables, graph)
    fact = facts["dbo.FactOrder"]
    assert "PaidAmount" in fact["measure_columns"]
    assert "Quantity" in fact["measure_columns"]
    # A foreign key is numeric but is not a measure.
    assert "CustomerId" not in fact["measure_columns"]
    assert fact["date_columns"] == ["OrderDate"]


def test_report_usage_lifts_a_table_out_of_tier_three():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    without = analyze.classify_tables(tables, graph, max_tier1=1, max_tier2=1)
    with_reads = analyze.classify_tables(
        tables, graph, report_reads={"stg.stg_orderimport": 12}, max_tier1=1, max_tier2=1
    )
    assert with_reads["stg.stg_OrderImport"]["tier"] <= 2
    assert with_reads["stg.stg_OrderImport"]["tier"] <= without["stg.stg_OrderImport"]["tier"]


def test_empty_tables_are_penalised():
    tables, foreign_keys = _star()
    graph = analyze.build_join_graph(tables, foreign_keys, infer=False)
    facts = analyze.classify_tables(tables, graph)
    assert facts["stg.stg_OrderImport"]["score"] < facts["dbo.FactOrder"]["score"]


# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------


def test_conventions_detect_keys_soft_deletes_and_scd2():
    tables, _ = _star()
    conventions = analyze.detect_conventions(tables)
    assert conventions["primary_key_style"]["value"] in ("<Table>Id", "other", "Id")
    assert any("IsDeleted" in c for c in conventions["soft_delete_columns"])
    assert any("IsCurrent" in c for c in conventions["scd2_columns"])
    assert any("EffectiveFrom" in c for c in conventions["scd2_columns"])
    assert conventions["tables_without_primary_key"]["count"] == 1


def test_conventions_on_an_empty_database_do_not_explode():
    assert analyze.detect_conventions([]) == {}


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def test_digest_is_valid_and_complete():
    tables, foreign_keys = _star()
    digest = analyze.build_digest(
        database="SalesDW", tables=tables, foreign_keys=foreign_keys
    )
    assert analyze.validate_digest(digest) == []
    assert digest["coverage"]["table_count"] == 6
    assert digest["source"]["database"] == "SalesDW"
    signatures = {t["signature"] for t in digest["tables"]}
    assert "salesdw::dbo.factorder" in signatures


def test_digest_is_deterministic():
    """Two builds of an unchanged database must be byte-identical."""
    import json

    tables, foreign_keys = _star()
    first = analyze.build_digest(
        database="SalesDW", tables=tables, foreign_keys=foreign_keys, generated_at="fixed"
    )
    second = analyze.build_digest(
        database="SalesDW",
        tables=list(reversed(tables)),  # input order must not matter
        foreign_keys=list(reversed(foreign_keys)),
        generated_at="fixed",
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_digest_does_not_record_the_real_server_by_default():
    tables, foreign_keys = _star()
    digest = analyze.build_digest(
        database="SalesDW", tables=tables, foreign_keys=foreign_keys
    )
    assert digest["source"]["server_alias"] == "sqlserver"
    assert "server" not in digest["source"]


def test_validate_digest_reports_problems():
    assert "Digest is not a JSON object." in analyze.validate_digest([])
    problems = analyze.validate_digest({"digest_schema_version": "9.0", "tables": []})
    assert any("major version" in p for p in problems)
    assert any("source.database" in p for p in problems)
