"""
metadata.py - typed accessors over the meta.* catalog.

This is the only module that knows the exact DDL shape from
seed/seed_schema.sql. Everything else (query_builder, execution_polars,
execution_sql, config_loader) works with the dataclasses defined here, not
raw rows, so a future schema change is a one-file change.

Every read function takes an open connection and returns plain dataclasses -
no ORM, matching the rest of this repo's style (see sql-server-schema's
catalog.py, which does the same for a different schema).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Feed:
    feed_id: int
    feed_name: str
    source_server: str
    source_database: str
    delimiter: str
    collision_action: str  # 'sanitize' | 'fail'
    collision_char: str
    line_ending: str  # 'CRLF' | 'LF'
    null_sentinel: str
    max_rows_per_file: int
    emit_header_row: bool
    emit_trailer_row: bool
    emit_concat_ws_line: bool
    default_execution_mode: str  # 'polars' | 'sql'
    output_root: str
    anchor_date_default: Optional[str]
    window_years_default: int
    is_active: bool


@dataclass(frozen=True)
class Dataset:
    dataset_id: int
    feed_id: int
    dataset_name: str
    source_schema: str
    source_table: str
    dataset_mode: str  # 'primary' | 'dependent' | 'reference'
    primary_key_columns: List[str]
    window_date_column: Optional[str]
    depends_on_dataset_id: Optional[int]
    dependency_source_column: Optional[str]
    dependency_target_column: Optional[str]
    include_own_window: bool
    output_file_stem: str
    run_ordinal: int
    is_active: bool

    @property
    def qualified_table(self) -> str:
        return "[{}].[{}]".format(self.source_schema, self.source_table)


@dataclass(frozen=True)
class DatasetLookup:
    dataset_lookup_id: int
    dataset_id: int
    lookup_alias: str
    lookup_schema: str
    lookup_table: str
    join_source_column: str
    join_lookup_column: str
    join_type: str  # 'left' | 'inner'

    @property
    def qualified_table(self) -> str:
        return "[{}].[{}]".format(self.lookup_schema, self.lookup_table)


@dataclass(frozen=True)
class FieldMap:
    field_map_id: int
    dataset_id: int
    ordinal: int
    target_column: str
    source_expression: str  # bare column, or 'lookup:<alias>.<column>'
    data_type: str
    rule_name: str
    rule_params: Dict[str, Any] = field(default_factory=dict)
    nullable: bool = True
    default_value: Optional[str] = None
    is_active: bool = True

    @property
    def lookup_alias(self) -> Optional[str]:
        """The alias part of 'lookup:<alias>.<column>', or None for a bare column."""
        if self.source_expression.startswith("lookup:"):
            return self.source_expression[len("lookup:"):].split(".", 1)[0]
        return None

    @property
    def source_column(self) -> str:
        """The bare column name, stripped of any 'lookup:<alias>.' prefix."""
        if self.source_expression.startswith("lookup:"):
            return self.source_expression.split(".", 1)[1]
        return self.source_expression


def _rows(cursor) -> List[Dict[str, Any]]:
    if cursor.description is None:
        return []
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _split_columns(value: str) -> List[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _parse_rule_params(raw: Optional[str]) -> Dict[str, Any]:
    import json

    if not raw:
        return {}
    return json.loads(raw)


def get_feed(conn, feed_name: str) -> Optional[Feed]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT feed_id, feed_name, source_server, source_database, delimiter, "
            "collision_action, collision_char, line_ending, null_sentinel, "
            "max_rows_per_file, emit_header_row, emit_trailer_row, emit_concat_ws_line, "
            "default_execution_mode, output_root, anchor_date_default, "
            "window_years_default, is_active "
            "FROM meta.feed WHERE feed_name = ?",
            feed_name,
        )
        rows = _rows(cursor)
    finally:
        cursor.close()
    if not rows:
        return None
    row = rows[0]
    return Feed(
        feed_id=row["feed_id"],
        feed_name=row["feed_name"],
        source_server=row["source_server"],
        source_database=row["source_database"],
        delimiter=row["delimiter"],
        collision_action=row["collision_action"],
        collision_char=row["collision_char"],
        line_ending=row["line_ending"],
        null_sentinel=row["null_sentinel"],
        max_rows_per_file=row["max_rows_per_file"],
        emit_header_row=bool(row["emit_header_row"]),
        emit_trailer_row=bool(row["emit_trailer_row"]),
        emit_concat_ws_line=bool(row["emit_concat_ws_line"]),
        default_execution_mode=row["default_execution_mode"],
        output_root=row["output_root"],
        anchor_date_default=str(row["anchor_date_default"]) if row["anchor_date_default"] else None,
        window_years_default=row["window_years_default"],
        is_active=bool(row["is_active"]),
    )


def list_datasets(conn, feed_id: int, *, active_only: bool = True) -> List[Dataset]:
    cursor = conn.cursor()
    try:
        sql = (
            "SELECT dataset_id, feed_id, dataset_name, source_schema, source_table, "
            "dataset_mode, primary_key_columns, window_date_column, "
            "depends_on_dataset_id, dependency_source_column, dependency_target_column, "
            "include_own_window, output_file_stem, run_ordinal, is_active "
            "FROM meta.dataset WHERE feed_id = ?"
        )
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY run_ordinal"
        cursor.execute(sql, feed_id)
        rows = _rows(cursor)
    finally:
        cursor.close()
    return [
        Dataset(
            dataset_id=row["dataset_id"],
            feed_id=row["feed_id"],
            dataset_name=row["dataset_name"],
            source_schema=row["source_schema"],
            source_table=row["source_table"],
            dataset_mode=row["dataset_mode"],
            primary_key_columns=_split_columns(row["primary_key_columns"]),
            window_date_column=row["window_date_column"],
            depends_on_dataset_id=row["depends_on_dataset_id"],
            dependency_source_column=row["dependency_source_column"],
            dependency_target_column=row["dependency_target_column"],
            include_own_window=bool(row["include_own_window"]),
            output_file_stem=row["output_file_stem"],
            run_ordinal=row["run_ordinal"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def list_lookups(conn, dataset_id: int) -> List[DatasetLookup]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT dataset_lookup_id, dataset_id, lookup_alias, lookup_schema, "
            "lookup_table, join_source_column, join_lookup_column, join_type "
            "FROM meta.dataset_lookup WHERE dataset_id = ? ORDER BY lookup_alias",
            dataset_id,
        )
        rows = _rows(cursor)
    finally:
        cursor.close()
    return [DatasetLookup(**row) for row in rows]


def list_field_maps(conn, dataset_id: int, *, active_only: bool = True) -> List[FieldMap]:
    cursor = conn.cursor()
    try:
        sql = (
            "SELECT field_map_id, dataset_id, ordinal, target_column, source_expression, "
            "data_type, rule_name, rule_params, nullable, default_value, is_active "
            "FROM meta.field_map WHERE dataset_id = ?"
        )
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY ordinal"
        cursor.execute(sql, dataset_id)
        rows = _rows(cursor)
    finally:
        cursor.close()
    return [
        FieldMap(
            field_map_id=row["field_map_id"],
            dataset_id=row["dataset_id"],
            ordinal=row["ordinal"],
            target_column=row["target_column"],
            source_expression=row["source_expression"],
            data_type=row["data_type"],
            rule_name=row["rule_name"],
            rule_params=_parse_rule_params(row["rule_params"]),
            nullable=bool(row["nullable"]),
            default_value=row["default_value"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def get_dataset_by_name(conn, feed_id: int, dataset_name: str) -> Optional[Dataset]:
    for dataset in list_datasets(conn, feed_id, active_only=False):
        if dataset.dataset_name == dataset_name:
            return dataset
    return None
