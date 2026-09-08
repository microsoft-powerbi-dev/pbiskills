"""Tests for the catalog layer, driven entirely by a fake connection.

Fixture data is fictional on purpose: this repository is public, so no real
schema, table, or column name appears anywhere in the test suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import catalog  # noqa: E402
from fakes import FakeConnection  # noqa: E402


# ---------------------------------------------------------------------------
# Type formatting: the three traps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args,expected",
    [
        (("int", 4, 10, 0), "int"),
        (("varchar", 50, 0, 0), "varchar(50)"),
        # n-prefixed types report max_length in BYTES, so 100 is nvarchar(50).
        (("nvarchar", 100, 0, 0), "nvarchar(50)"),
        (("nvarchar", -1, 0, 0), "nvarchar(MAX)"),
        (("varbinary", -1, 0, 0), "varbinary(MAX)"),
        (("decimal", 9, 18, 2), "decimal(18,2)"),
        (("numeric", 9, 10, 4), "numeric(10,4)"),
        (("datetime2", 8, 0, 7), "datetime2(7)"),
        (("bit", 1, 1, 0), "bit"),
    ],
)
def test_format_type(args, expected):
    assert catalog.format_type(*args) == expected


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

COLUMN_FIELDS = [
    "ordinal", "column_name", "data_type", "max_length", "precision", "scale",
    "is_nullable", "is_identity", "is_computed", "collation_name",
    "default_definition", "computed_definition", "seed_value",
    "increment_value", "description",
]

COLUMN_ROWS = [
    (1, "OrderId", "int", 4, 10, 0, False, True, False, None, None, None, 1, 1, "Surrogate key"),
    (2, "CustomerId", "int", 4, 10, 0, False, False, False, None, None, None, None, None, None),
    (3, "OrderDate", "date", 3, 10, 0, False, False, False, None, "(getdate())", None, None, None, None),
    (4, "Notes", "nvarchar", -1, 0, 0, True, False, False, "SQL_Latin1_General_CP1_CI_AS", None, None, None, None, None),
    (5, "LineTotal", "decimal", 9, 18, 2, True, False, True, None, None, "([Qty]*[Price])", None, None, None),
]


def _columns_conn():
    return FakeConnection({r"sys\.columns": (COLUMN_FIELDS, COLUMN_ROWS)})


def test_get_columns_shapes_every_field():
    columns = catalog.get_columns(_columns_conn(), 42)
    assert len(columns) == 5
    first = columns[0]
    assert first["name"] == "OrderId"
    assert first["type"] == "int"
    assert first["is_identity"] is True
    assert first["identity_seed"] == 1
    assert first["nullable"] is False
    assert first["description"] == "Surrogate key"


def test_get_columns_reports_defaults_and_computed_definitions():
    columns = {c["name"]: c for c in catalog.get_columns(_columns_conn(), 42)}
    assert columns["OrderDate"]["default"] == "(getdate())"
    assert columns["LineTotal"]["is_computed"] is True
    assert columns["LineTotal"]["computed_definition"] == "([Qty]*[Price])"
    assert columns["Notes"]["type"] == "nvarchar(MAX)"


def test_get_columns_filters_by_object_id_as_a_parameter():
    conn = _columns_conn()
    catalog.get_columns(conn, 42)
    sql, params = conn.executed[0]
    assert params == (42,)
    # The id is a bound parameter, never interpolated into the text.
    assert "42" not in sql


# ---------------------------------------------------------------------------
# Foreign keys
# ---------------------------------------------------------------------------

FK_FIELDS = [
    "constraint_name", "parent_schema", "parent_table", "parent_column",
    "referenced_schema", "referenced_table", "referenced_column",
    "constraint_column_id", "delete_referential_action_desc",
    "update_referential_action_desc", "is_disabled", "is_not_trusted",
]

# A two-column composite FK, deliberately, since single-column grouping is the
# easy case and composite is where naive implementations break.
FK_ROWS = [
    ("FK_OrderLine_Order", "dbo", "OrderLine", "OrderId", "dbo", "Order", "OrderId", 1, "NO_ACTION", "NO_ACTION", False, False),
    ("FK_OrderLine_Order", "dbo", "OrderLine", "TenantId", "dbo", "Order", "TenantId", 2, "NO_ACTION", "NO_ACTION", False, False),
    ("FK_Order_Customer", "dbo", "Order", "CustomerId", "dbo", "Customer", "CustomerId", 1, "CASCADE", "NO_ACTION", False, True),
]


def test_foreign_keys_group_composite_columns_in_order():
    conn = FakeConnection({r"sys\.foreign_keys": (FK_FIELDS, FK_ROWS)})
    keys = catalog.get_foreign_keys(conn)
    assert len(keys) == 2
    composite = next(k for k in keys if k["name"] == "FK_OrderLine_Order")
    assert composite["parent_columns"] == ["OrderId", "TenantId"]
    assert composite["referenced_columns"] == ["OrderId", "TenantId"]
    assert composite["parent_table"] == "OrderLine"
    assert composite["referenced_table"] == "Order"


def test_foreign_keys_carry_trust_and_action_metadata():
    conn = FakeConnection({r"sys\.foreign_keys": (FK_FIELDS, FK_ROWS)})
    keys = {k["name"]: k for k in catalog.get_foreign_keys(conn)}
    assert keys["FK_Order_Customer"]["on_delete"] == "CASCADE"
    # An untrusted FK is not enforced for the existing rows, which matters when
    # deciding whether to believe the relationship.
    assert keys["FK_Order_Customer"]["is_not_trusted"] is True


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

INDEX_FIELDS = [
    "index_name", "type_desc", "is_unique", "is_primary_key", "has_filter",
    "filter_definition", "column_name", "key_ordinal", "is_included_column",
    "is_descending_key",
]

INDEX_ROWS = [
    ("PK_Order", "CLUSTERED", True, True, False, None, "OrderId", 1, False, False),
    ("IX_Order_Customer", "NONCLUSTERED", False, False, True, "([IsDeleted]=(0))", "CustomerId", 1, False, False),
    ("IX_Order_Customer", "NONCLUSTERED", False, False, True, "([IsDeleted]=(0))", "OrderDate", 2, False, False),
    ("IX_Order_Customer", "NONCLUSTERED", False, False, True, "([IsDeleted]=(0))", "Total", 0, True, False),
]


def test_indexes_split_key_and_included_columns():
    conn = FakeConnection({r"sys\.indexes": (INDEX_FIELDS, INDEX_ROWS)})
    indexes = {i["name"]: i for i in catalog.get_indexes(conn, 42)}
    covering = indexes["IX_Order_Customer"]
    assert covering["key_columns"] == ["CustomerId", "OrderDate"]
    assert covering["included_columns"] == ["Total"]
    # A filtered index records its predicate: sp_helpindex would not show this.
    assert covering["filter"] == "([IsDeleted]=(0))"
    assert indexes["PK_Order"]["is_primary_key"] is True
    assert indexes["PK_Order"]["filter"] is None


# ---------------------------------------------------------------------------
# Object resolution and describe_table
# ---------------------------------------------------------------------------

RESOLVE_FIELDS = ["object_id", "schema_name", "object_name", "type_desc"]


def _describe_conn():
    # Patterns anchor on FROM, because several catalog queries *join* to
    # sys.columns and would otherwise match the columns script first.
    return FakeConnection(
        {
            r"OBJECT_ID\(\?\)": (RESOLVE_FIELDS, [(42, "dbo", "Order", "USER_TABLE")]),
            r"FROM sys\.columns": (COLUMN_FIELDS, COLUMN_ROWS),
            r"FROM sys\.key_constraints": (
                ["constraint_name", "column_name", "key_ordinal"],
                [("PK_Order", "OrderId", 1)],
            ),
            r"FROM sys\.foreign_keys": (FK_FIELDS, FK_ROWS),
            r"FROM sys\.indexes": (INDEX_FIELDS, INDEX_ROWS),
        }
    )


def test_describe_table_separates_outbound_from_inbound_keys():
    result = catalog.describe_table(_describe_conn(), "dbo.Order")
    assert result["found"] is True
    assert result["qualified_name"] == "dbo.Order"
    # Order -> Customer is outbound; OrderLine -> Order is inbound.
    assert [fk["name"] for fk in result["foreign_keys_out"]] == ["FK_Order_Customer"]
    assert [fk["name"] for fk in result["referenced_by"]] == ["FK_OrderLine_Order"]
    assert result["primary_key"]["columns"] == ["OrderId"]


def test_describe_table_resolves_the_name_as_a_parameter_first():
    conn = _describe_conn()
    catalog.describe_table(conn, "dbo.Order")
    first_sql, first_params = conn.executed[0]
    assert "OBJECT_ID" in first_sql
    assert first_params == ("dbo.Order",)
    # After resolution, every subsequent query filters on the integer id.
    for sql, params in conn.executed[1:]:
        assert "dbo.Order" not in sql


def test_describe_table_explains_that_not_found_may_mean_not_permitted():
    conn = FakeConnection({r"OBJECT_ID\(\?\)": (RESOLVE_FIELDS, [])})
    result = catalog.describe_table(conn, "dbo.Nope")
    assert result["found"] is False
    assert "permission" in result["error"].lower()


# ---------------------------------------------------------------------------
# Databases, stats, and value coercion
# ---------------------------------------------------------------------------


def test_list_databases_excludes_system_databases_by_default():
    conn = FakeConnection(
        {
            r"sys\.databases": (
                ["database_name", "database_id", "state_desc", "recovery_model_desc",
                 "collation_name", "compatibility_level", "is_read_only", "size_mb"],
                [("SalesDW", 7, "ONLINE", "SIMPLE", "SQL_Latin1_General_CP1_CI_AS", 150, False, 4096.0)],
            )
        }
    )
    databases = catalog.list_databases(conn)
    assert databases[0]["database_name"] == "SalesDW"
    assert conn.executed[0][1] == (0,)
    catalog.list_databases(conn, include_system=True)
    assert conn.executed[1][1] == (1,)


def test_table_stats_uses_partition_stats_not_count_star():
    conn = FakeConnection(
        {
            r"dm_db_partition_stats": (
                ["schema_name", "object_name", "row_count", "reserved_mb", "used_mb"],
                [("dbo", "Order", 4120000, 812.5, 790.0)],
            )
        }
    )
    stats = catalog.get_table_stats(conn, top=10)
    assert stats[0]["row_count"] == 4120000
    sql = conn.executed[0][0]
    assert "COUNT(*)" not in sql.upper()
    assert "index_id IN (0,1)" in sql


def test_plain_coerces_driver_scalars_for_json():
    import datetime
    import decimal

    assert catalog._plain(decimal.Decimal("12.50")) == 12.5
    assert catalog._plain(datetime.date(2026, 9, 8)) == "2026-09-08"
    # Binary is reported by size, never by content.
    assert catalog._plain(b"\x00\x01\x02") == {"binary": True, "bytes": 3}
    assert catalog._plain(None) is None
    assert catalog._plain("text") == "text"


def test_sample_rows_drops_columns_that_are_not_real():
    conn = _describe_conn()
    with pytest.raises(ValueError):
        catalog.sample_rows(conn, "dbo.Order", ["NotAColumn"], limit=5)


def test_sample_rows_quotes_catalog_sourced_identifiers():
    conn = _describe_conn()
    catalog.sample_rows(conn, "dbo.Order", ["OrderId", "CustomerId"], limit=3)
    sql = conn.executed[-1][0]
    assert sql.startswith("SELECT TOP (3)")
    assert "[dbo].[Order]" in sql
    assert "[OrderId]" in sql
