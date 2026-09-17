"""
catalog.py - read a SQL Server database's structure out of the sys.* catalog views.

Every function here takes an already-open connection as its first argument and
never opens one itself. That is the seam that makes the whole catalog layer
testable against a fake cursor, with no driver, no server, and no network. It is
the same move ``reportlineage/scanners/report_server.py`` makes with its
injectable ``session=``.

Two rules run through all of it.

**sys.* rather than INFORMATION_SCHEMA.** INFORMATION_SCHEMA is an ANSI
compatibility layer that cannot express identity columns, computed columns,
included or filtered index columns, or an untrusted foreign key, and it
truncates a routine definition at 4000 characters. sys.* is the superset.

**Resolve an object to its object_id first, then filter on the integer.** No
caller-supplied identifier is ever interpolated into SQL. The one place a name
must be materialized (sampling rows) uses the name the *server* returned from
the resolve step, wrapped in QUOTENAME, so an injected identifier cannot
survive the round trip.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Query text
# ---------------------------------------------------------------------------

SQL_SERVER_INFO = """
SELECT
    SERVERPROPERTY('ProductVersion')      AS product_version,
    SERVERPROPERTY('Edition')             AS edition,
    SERVERPROPERTY('ProductLevel')        AS product_level,
    SERVERPROPERTY('Collation')           AS server_collation,
    SERVERPROPERTY('MachineName')         AS machine_name,
    DB_NAME()                             AS current_database,
    SUSER_SNAME()                         AS login_name,
    @@SPID                                AS spid
"""

# auth_scheme is the single most useful diagnostic this server has: KERBEROS or
# NTLM proves Windows Integrated Auth actually happened, rather than being
# assumed. Needs VIEW SERVER STATE, so it is queried separately and allowed to
# fail without taking the whole connection test with it.
SQL_AUTH_SCHEME = """
SELECT auth_scheme, net_transport, encrypt_option
FROM sys.dm_exec_connections
WHERE session_id = @@SPID
"""

SQL_DATABASES = """
SELECT
    d.name                                       AS database_name,
    d.database_id,
    d.state_desc,
    d.recovery_model_desc,
    d.collation_name,
    d.compatibility_level,
    d.is_read_only,
    CAST(SUM(mf.size) * 8.0 / 1024 AS DECIMAL(18,2)) AS size_mb
FROM sys.databases AS d
LEFT JOIN sys.master_files AS mf ON mf.database_id = d.database_id
WHERE d.state = 0
  AND HAS_DBACCESS(d.name) = 1
  AND (? = 1 OR d.database_id > 4)
GROUP BY d.name, d.database_id, d.state_desc, d.recovery_model_desc,
         d.collation_name, d.compatibility_level, d.is_read_only
ORDER BY d.name
"""

SQL_SCHEMAS = """
SELECT
    s.name AS schema_name,
    COUNT(o.object_id) AS object_count,
    SUM(CASE WHEN o.type = 'U' THEN 1 ELSE 0 END) AS table_count,
    SUM(CASE WHEN o.type = 'V' THEN 1 ELSE 0 END) AS view_count
FROM sys.schemas AS s
LEFT JOIN sys.objects AS o
       ON o.schema_id = s.schema_id AND o.type IN ('U','V','P','FN','IF','TF')
WHERE s.name NOT IN ('sys','INFORMATION_SCHEMA','guest','db_owner',
                     'db_accessadmin','db_securityadmin','db_ddladmin',
                     'db_backupoperator','db_datareader','db_datawriter',
                     'db_denydatareader','db_denydatawriter')
GROUP BY s.name
ORDER BY s.name
"""

# Row counts come from partition stats, never COUNT(*): a COUNT(*) on a
# multi-million-row production fact table is a full scan.
SQL_TABLES = """
SELECT
    s.name  AS schema_name,
    o.name  AS object_name,
    o.type_desc,
    o.object_id,
    o.create_date,
    o.modify_date,
    ISNULL(ps.row_count, 0)                                    AS row_count,
    CAST(ISNULL(ps.reserved_mb, 0) AS DECIMAL(18,2))           AS size_mb,
    -- sys.extended_properties.value is sql_variant; pyodbc cannot decode it
    -- at all, so cast to text before it crosses the wire.
    CAST(ep.value AS NVARCHAR(MAX))                            AS description
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
LEFT JOIN (
    SELECT object_id,
           SUM(CASE WHEN index_id IN (0,1) THEN row_count ELSE 0 END) AS row_count,
           SUM(reserved_page_count) * 8.0 / 1024                      AS reserved_mb
    FROM sys.dm_db_partition_stats
    GROUP BY object_id
) AS ps ON ps.object_id = o.object_id
LEFT JOIN sys.extended_properties AS ep
       ON ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
WHERE o.type IN ('U','V')
  AND (? = 1 OR o.type = 'U')
  AND (? IS NULL OR s.name = ?)
  AND (? IS NULL OR o.name LIKE ?)
ORDER BY s.name, o.name
"""

SQL_RESOLVE_OBJECT = """
SELECT o.object_id, s.name AS schema_name, o.name AS object_name, o.type_desc
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.object_id = OBJECT_ID(?) AND o.type IN ('U','V')
"""

SQL_COLUMNS = """
SELECT
    c.column_id                                   AS ordinal,
    c.name                                        AS column_name,
    t.name                                        AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.is_identity,
    c.is_computed,
    c.collation_name,
    dc.definition                                 AS default_definition,
    cc.definition                                 AS computed_definition,
    -- sys.identity_columns.seed_value/increment_value are typed sql_variant,
    -- which pyodbc cannot decode at all (fails even under an explicit CAST AS
    -- sql_variant back to the client). Cast to bigint in T-SQL so the value
    -- never crosses the wire as a variant. An identity seed/increment outside
    -- bigint range is not a case this skill needs to support.
    CAST(ic.seed_value AS BIGINT)                 AS seed_value,
    CAST(ic.increment_value AS BIGINT)            AS increment_value,
    -- sys.extended_properties.value is sql_variant for the same reason.
    CAST(ep.value AS NVARCHAR(MAX))               AS description
FROM sys.columns AS c
JOIN sys.types AS t ON t.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints AS dc ON dc.object_id = c.default_object_id
LEFT JOIN sys.computed_columns AS cc
       ON cc.object_id = c.object_id AND cc.column_id = c.column_id
LEFT JOIN sys.identity_columns AS ic
       ON ic.object_id = c.object_id AND ic.column_id = c.column_id
LEFT JOIN sys.extended_properties AS ep
       ON ep.major_id = c.object_id AND ep.minor_id = c.column_id
      AND ep.name = 'MS_Description'
WHERE c.object_id = ?
ORDER BY c.column_id
"""

SQL_PRIMARY_KEY = """
SELECT kc.name AS constraint_name, c.name AS column_name, ic.key_ordinal
FROM sys.key_constraints AS kc
JOIN sys.index_columns AS ic
       ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
JOIN sys.columns AS c
       ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE kc.parent_object_id = ? AND kc.type = 'PK'
ORDER BY ic.key_ordinal
"""

SQL_FOREIGN_KEYS = """
SELECT
    fk.name                    AS constraint_name,
    ps.name                    AS parent_schema,
    po.name                    AS parent_table,
    pc.name                    AS parent_column,
    rs.name                    AS referenced_schema,
    ro.name                    AS referenced_table,
    rc.name                    AS referenced_column,
    fkc.constraint_column_id,
    fk.delete_referential_action_desc,
    fk.update_referential_action_desc,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.objects AS po ON po.object_id = fk.parent_object_id
JOIN sys.schemas AS ps ON ps.schema_id = po.schema_id
JOIN sys.columns AS pc
       ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.objects AS ro ON ro.object_id = fk.referenced_object_id
JOIN sys.schemas AS rs ON rs.schema_id = ro.schema_id
JOIN sys.columns AS rc
       ON rc.object_id = fkc.referenced_object_id
      AND rc.column_id = fkc.referenced_column_id
WHERE (? IS NULL OR fk.parent_object_id = ? OR fk.referenced_object_id = ?)
ORDER BY ps.name, po.name, fk.name, fkc.constraint_column_id
"""

# sp_helpindex is deprecated and reports neither included columns nor a filter
# predicate, so this reads the index catalog directly.
SQL_INDEXES = """
SELECT
    i.name          AS index_name,
    i.type_desc,
    i.is_unique,
    i.is_primary_key,
    i.has_filter,
    i.filter_definition,
    c.name          AS column_name,
    ic.key_ordinal,
    ic.is_included_column,
    ic.is_descending_key
FROM sys.indexes AS i
JOIN sys.index_columns AS ic
       ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns AS c
       ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.object_id = ? AND i.type > 0
ORDER BY i.name, ic.is_included_column, ic.key_ordinal
"""

SQL_PROGRAMMABILITY = """
SELECT
    s.name       AS schema_name,
    o.name       AS object_name,
    o.type_desc,
    o.type,
    o.create_date,
    o.modify_date,
    CASE WHEN sm.definition IS NULL THEN 0 ELSE 1 END AS definition_available
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules AS sm ON sm.object_id = o.object_id
WHERE o.type IN ('P','FN','IF','TF','V')
  AND (? IS NULL OR o.type = ?)
  AND (? IS NULL OR s.name = ?)
  AND (? IS NULL OR o.name LIKE ?)
ORDER BY s.name, o.name
"""

SQL_MODULE_DEFINITION = """
SELECT sm.definition
FROM sys.sql_modules AS sm
WHERE sm.object_id = OBJECT_ID(?)
"""

SQL_PARAMETERS = """
SELECT p.name AS parameter_name, t.name AS data_type, p.max_length,
       p.precision, p.scale, p.is_output, p.parameter_id
FROM sys.parameters AS p
JOIN sys.types AS t ON t.user_type_id = p.user_type_id
WHERE p.object_id = OBJECT_ID(?)
ORDER BY p.parameter_id
"""

SQL_TABLE_STATS = """
SELECT TOP (?)
    s.name AS schema_name,
    o.name AS object_name,
    SUM(CASE WHEN ps.index_id IN (0,1) THEN ps.row_count ELSE 0 END) AS row_count,
    CAST(SUM(ps.reserved_page_count) * 8.0 / 1024 AS DECIMAL(18,2))  AS reserved_mb,
    CAST(SUM(ps.used_page_count) * 8.0 / 1024 AS DECIMAL(18,2))      AS used_mb
FROM sys.dm_db_partition_stats AS ps
JOIN sys.objects AS o ON o.object_id = ps.object_id
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.type = 'U'
  AND (? IS NULL OR s.name = ?)
GROUP BY s.name, o.name
ORDER BY row_count DESC
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rows_as_dicts(cursor: Any) -> List[Dict[str, Any]]:
    """Turn a cursor's pending result set into a list of plain dicts."""
    if cursor.description is None:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query(conn: Any, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run a parameterized query and return dicts. Never interpolates."""
    cursor = conn.cursor()
    try:
        cursor.execute(sql, *params) if params else cursor.execute(sql)
        return rows_as_dicts(cursor)
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def format_type(
    data_type: str,
    max_length: Optional[int],
    precision: Optional[int],
    scale: Optional[int],
) -> str:
    """Render a column type the way a human writes it in DDL.

    Three traps handled here: an ``n``-prefixed type reports ``max_length`` in
    *bytes*, so it halves; ``-1`` means MAX; and decimal-family types carry
    precision and scale rather than a length.
    """
    name = (data_type or "").lower()
    if name in ("decimal", "numeric"):
        return "{}({},{})".format(name, precision or 0, scale or 0)
    if name in ("datetime2", "time", "datetimeoffset"):
        return "{}({})".format(name, scale or 0) if scale is not None else name
    if name in ("char", "varchar", "binary", "varbinary", "nchar", "nvarchar"):
        if max_length is None:
            return name
        if max_length == -1:
            return "{}(MAX)".format(name)
        length = max_length // 2 if name.startswith("n") else max_length
        return "{}({})".format(name, length)
    if name == "float" and precision:
        return "float({})".format(precision)
    return name


def _one_or_none(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Server and database level
# ---------------------------------------------------------------------------


def get_server_info(conn: Any) -> Dict[str, Any]:
    """Version, edition, collation, and the login this session authenticated as."""
    info = _one_or_none(query(conn, SQL_SERVER_INFO)) or {}
    result = {key: _plain(value) for key, value in info.items()}
    # Optional: needs VIEW SERVER STATE. Its absence is reported, not fatal.
    try:
        auth = _one_or_none(query(conn, SQL_AUTH_SCHEME)) or {}
        result["auth_scheme"] = auth.get("auth_scheme")
        result["net_transport"] = auth.get("net_transport")
        result["encrypt_option"] = auth.get("encrypt_option")
        result["missing_permissions"] = []
    except Exception:
        result["auth_scheme"] = None
        result["missing_permissions"] = ["VIEW SERVER STATE"]
        result["hint"] = (
            "auth_scheme is unavailable without VIEW SERVER STATE. Windows auth "
            "may still be working; this only means it cannot be confirmed here."
        )
    return result


def list_databases(conn: Any, include_system: bool = False) -> List[Dict[str, Any]]:
    """Databases this Windows identity can actually open."""
    rows = query(conn, SQL_DATABASES, (1 if include_system else 0,))
    return [{key: _plain(value) for key, value in row.items()} for row in rows]


def list_schemas(conn: Any) -> List[Dict[str, Any]]:
    """Non-system schemas in the current database, with object counts."""
    return [
        {key: _plain(value) for key, value in row.items()}
        for row in query(conn, SQL_SCHEMAS)
    ]


def list_tables(
    conn: Any,
    schema: Optional[str] = None,
    name_like: Optional[str] = None,
    include_views: bool = True,
) -> List[Dict[str, Any]]:
    """Tables and optionally views, with row counts and sizes."""
    params = (
        1 if include_views else 0,
        schema,
        schema,
        name_like,
        name_like,
    )
    rows = query(conn, SQL_TABLES, params)
    return [{key: _plain(value) for key, value in row.items()} for row in rows]


# ---------------------------------------------------------------------------
# Object level
# ---------------------------------------------------------------------------


def resolve_object(conn: Any, name: str) -> Optional[Dict[str, Any]]:
    """Resolve a table or view name to its object_id and canonical names.

    Everything downstream filters on the integer object_id, so a caller-supplied
    string is never interpolated into SQL. This is the injection boundary.
    """
    row = _one_or_none(query(conn, SQL_RESOLVE_OBJECT, (name,)))
    return {key: _plain(value) for key, value in row.items()} if row else None


def get_columns(conn: Any, object_id: int) -> List[Dict[str, Any]]:
    """Full column detail for one object, including identity and computed columns."""
    columns = []
    for row in query(conn, SQL_COLUMNS, (object_id,)):
        columns.append(
            {
                "ordinal": _plain(row.get("ordinal")),
                "name": _plain(row.get("column_name")),
                "data_type": _plain(row.get("data_type")),
                "type": format_type(
                    row.get("data_type"),
                    row.get("max_length"),
                    row.get("precision"),
                    row.get("scale"),
                ),
                "max_length": _plain(row.get("max_length")),
                "precision": _plain(row.get("precision")),
                "scale": _plain(row.get("scale")),
                "nullable": bool(row.get("is_nullable")),
                "is_identity": bool(row.get("is_identity")),
                "identity_seed": _plain(row.get("seed_value")),
                "identity_increment": _plain(row.get("increment_value")),
                "is_computed": bool(row.get("is_computed")),
                "computed_definition": _plain(row.get("computed_definition")),
                "default": _plain(row.get("default_definition")),
                "collation": _plain(row.get("collation_name")),
                "description": _plain(row.get("description")),
            }
        )
    return columns


def get_primary_key(conn: Any, object_id: int) -> Optional[Dict[str, Any]]:
    """The primary key constraint, or None for a heap or a view."""
    rows = query(conn, SQL_PRIMARY_KEY, (object_id,))
    if not rows:
        return None
    return {
        "name": _plain(rows[0].get("constraint_name")),
        "columns": [_plain(row.get("column_name")) for row in rows],
    }


def get_foreign_keys(conn: Any, object_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Foreign keys, grouped into one entry per constraint.

    With ``object_id`` given, returns the keys in both directions for that
    object. With None, returns every foreign key in the database, which is what
    builds the join graph.
    """
    rows = query(conn, SQL_FOREIGN_KEYS, (object_id, object_id, object_id))
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = "{}.{}.{}".format(
            _plain(row.get("parent_schema")),
            _plain(row.get("parent_table")),
            _plain(row.get("constraint_name")),
        )
        entry = grouped.setdefault(
            key,
            {
                "name": _plain(row.get("constraint_name")),
                "parent_schema": _plain(row.get("parent_schema")),
                "parent_table": _plain(row.get("parent_table")),
                "parent_columns": [],
                "referenced_schema": _plain(row.get("referenced_schema")),
                "referenced_table": _plain(row.get("referenced_table")),
                "referenced_columns": [],
                "on_delete": _plain(row.get("delete_referential_action_desc")),
                "on_update": _plain(row.get("update_referential_action_desc")),
                "is_disabled": bool(row.get("is_disabled")),
                "is_not_trusted": bool(row.get("is_not_trusted")),
            },
        )
        entry["parent_columns"].append(_plain(row.get("parent_column")))
        entry["referenced_columns"].append(_plain(row.get("referenced_column")))
    return list(grouped.values())


def get_indexes(conn: Any, object_id: int) -> List[Dict[str, Any]]:
    """Indexes with their key columns, included columns, and filter predicate."""
    rows = query(conn, SQL_INDEXES, (object_id,))
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = _plain(row.get("index_name"))
        entry = grouped.setdefault(
            name,
            {
                "name": name,
                "type": _plain(row.get("type_desc")),
                "is_unique": bool(row.get("is_unique")),
                "is_primary_key": bool(row.get("is_primary_key")),
                "filter": _plain(row.get("filter_definition"))
                if row.get("has_filter")
                else None,
                "key_columns": [],
                "included_columns": [],
            },
        )
        column = _plain(row.get("column_name"))
        if row.get("is_included_column"):
            entry["included_columns"].append(column)
        else:
            entry["key_columns"].append(column)
    return list(grouped.values())


def describe_table(conn: Any, name: str) -> Dict[str, Any]:
    """Everything about one table or view, in a single payload."""
    resolved = resolve_object(conn, name)
    if resolved is None:
        return {
            "found": False,
            "error": "No table or view named {!r} is visible to this login. "
            "Note that SQL Server hides objects the login has no permission "
            "on, so 'not found' can mean 'not permitted'.".format(name),
        }
    object_id = resolved["object_id"]
    foreign_keys = get_foreign_keys(conn, object_id)
    schema_name = resolved["schema_name"]
    table_name = resolved["object_name"]
    return {
        "found": True,
        "schema": schema_name,
        "name": table_name,
        "qualified_name": "{}.{}".format(schema_name, table_name),
        "object_type": resolved["type_desc"],
        "columns": get_columns(conn, object_id),
        "primary_key": get_primary_key(conn, object_id),
        "foreign_keys_out": [
            fk
            for fk in foreign_keys
            if fk["parent_schema"] == schema_name and fk["parent_table"] == table_name
        ],
        "referenced_by": [
            fk
            for fk in foreign_keys
            if not (
                fk["parent_schema"] == schema_name and fk["parent_table"] == table_name
            )
        ],
        "indexes": get_indexes(conn, object_id),
    }


def list_programmability(
    conn: Any,
    kind: Optional[str] = None,
    schema: Optional[str] = None,
    name_like: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Stored procedures, functions, and views.

    ``kind`` is one of procedure, scalar_function, inline_function,
    table_function, view, or None for all of them.
    """
    type_codes = {
        "procedure": "P",
        "scalar_function": "FN",
        "inline_function": "IF",
        "table_function": "TF",
        "view": "V",
    }
    code = type_codes.get((kind or "").lower()) if kind else None
    rows = query(
        conn, SQL_PROGRAMMABILITY, (code, code, schema, schema, name_like, name_like)
    )
    return [{key: _plain(value) for key, value in row.items()} for row in rows]


def get_definition(conn: Any, name: str, max_chars: int = 40000) -> Dict[str, Any]:
    """The T-SQL body of one module, truncated at ``max_chars``."""
    rows = query(conn, SQL_MODULE_DEFINITION, (name,))
    if not rows or rows[0].get("definition") is None:
        return {
            "found": False,
            "error": "No definition available for {!r}. Either the object does "
            "not exist, it is encrypted, or the login lacks VIEW "
            "DEFINITION.".format(name),
        }
    body = str(rows[0]["definition"])
    parameters = [
        {key: _plain(value) for key, value in row.items()}
        for row in query(conn, SQL_PARAMETERS, (name,))
    ]
    return {
        "found": True,
        "name": name,
        "parameters": parameters,
        "definition": body[:max_chars],
        "truncated": len(body) > max_chars,
        "length": len(body),
    }


def get_table_stats(
    conn: Any, schema: Optional[str] = None, top: int = 200
) -> List[Dict[str, Any]]:
    """Row counts and sizes, largest first, from partition stats.

    These are metadata reads, not a COUNT(*): approximate under concurrent
    writes, and free.
    """
    rows = query(conn, SQL_TABLE_STATS, (int(top), schema, schema))
    return [{key: _plain(value) for key, value in row.items()} for row in rows]


def sample_rows(
    conn: Any, name: str, columns: Sequence[str], limit: int = 5
) -> List[Dict[str, Any]]:
    """Read a few rows, using only server-returned identifiers.

    ``name`` is resolved through ``resolve_object`` first, so the schema and
    table names interpolated below came from the server's catalog, not from the
    caller. ``columns`` are matched against the object's real column list and
    anything unrecognised is dropped. QUOTENAME wraps every identifier.
    """
    resolved = resolve_object(conn, name)
    if resolved is None:
        raise ValueError("No table or view named {!r} is visible.".format(name))
    real_columns = {column["name"] for column in get_columns(conn, resolved["object_id"])}
    chosen = [column for column in columns if column in real_columns]
    if not chosen:
        raise ValueError(
            "No sampleable columns remain for {!r} after the allowlist and "
            "sensitivity filters.".format(name)
        )
    # Bracket-quote every identifier, doubling any embedded closing bracket.
    # All three inputs came from the catalog, not the caller.
    select_list = ", ".join("[{}]".format(c.replace("]", "]]")) for c in chosen)
    target = "[{}].[{}]".format(
        resolved["schema_name"].replace("]", "]]"),
        resolved["object_name"].replace("]", "]]"),
    )
    sql = "SELECT TOP ({}) {} FROM {}".format(int(limit), select_list, target)
    return query(conn, sql)


def _plain(value: Any) -> Any:
    """Coerce driver-specific scalars into something json.dumps can handle."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        import datetime
        import decimal

        if isinstance(value, decimal.Decimal):
            return float(value)
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        if isinstance(value, (bytes, bytearray)):
            return {"binary": True, "bytes": len(value)}
    except Exception:
        pass
    return str(value)
