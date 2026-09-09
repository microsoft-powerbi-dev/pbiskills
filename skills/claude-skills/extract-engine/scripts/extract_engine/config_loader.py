"""
config_loader.py - Excel workbook -> validated meta.* upsert.

Resolved decision 1: "Excel authors, metadata executes." The workbook stays
the customer-facing authoring format; this module is the only bridge into the
meta.* tables the execution paths actually read. Neither execution path ever
reads Excel directly.

Sheet-to-table mapping:

    Feed          -> meta.feed             (one feed per workbook)
    Datasets      -> meta.dataset
    Lookups       -> meta.dataset_lookup   ("calculator table" joins)
    FieldMap      -> meta.field_map
    KeyGeneration -> folds into FieldMap rows using rule_name in
                     {row_sequence, ...}; kept as a separate authoring sheet
                     for clarity, merged here.
    ExtractParameters -> feed.anchor_date_default / window_years_default
    OutputLayout      -> feed.emit_header_row / emit_trailer_row /
                         emit_concat_ws_line only (column order is already
                         FieldMap.ordinal)

Validation runs entirely before any database write, and needs a connection
only for the one check that genuinely requires one (a calculator/lookup table
actually existing in the source database) - everything else is structural and
checked against the Python rule registry and the workbook's own internal
consistency.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import connection as conn_mod
from . import rules as rules_mod

REQUIRED_SHEETS = ("Feed", "Datasets", "FieldMap")
OPTIONAL_SHEETS = ("Lookups", "KeyGeneration", "ExtractParameters", "OutputLayout")

_DATA_TYPE_PATTERN = re.compile(
    r"^(int|bigint|bit|date|datetime|datetime2|varchar\(\d+\)|nvarchar\(\d+|MAX\)|"
    r"char\(\d+\)|nchar\(\d+\)|decimal\(\d+,\s*\d+\))$",
    re.IGNORECASE,
)


@dataclass
class ValidationIssue:
    sheet: str
    row: Optional[int]
    column: Optional[str]
    message: str

    def __str__(self) -> str:
        location = self.sheet
        if self.row is not None:
            location += ":row {}".format(self.row)
        if self.column is not None:
            location += ":{}".format(self.column)
        return "{}: {}".format(location, self.message)


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, sheet: str, message: str, row: Optional[int] = None, column: Optional[str] = None) -> None:
        self.issues.append(ValidationIssue(sheet, row, column, message))

    def __str__(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)


@dataclass
class WorkbookConfig:
    """The parsed workbook, before validation or database contact."""

    feed: Dict[str, Any]
    datasets: List[Dict[str, Any]]
    lookups: List[Dict[str, Any]]
    field_maps: List[Dict[str, Any]]
    workbook_path: Path
    workbook_sha256: str
    sheet_summary: Dict[str, int]


def _sheet_to_dicts(ws) -> List[Dict[str, Any]]:
    """A worksheet's rows as dicts, keyed by its header row."""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    result = []
    for row in rows[1:]:
        if all(v is None for v in row):
            continue
        result.append({headers[i]: row[i] for i in range(len(headers)) if headers[i]})
    return result


def load_workbook(path: str) -> WorkbookConfig:
    """Parse the Excel workbook into a WorkbookConfig. No validation yet."""
    import openpyxl

    path = Path(path)
    raw_bytes = path.read_bytes()
    workbook_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet_summary: Dict[str, int] = {}

    def _rows(name: str) -> List[Dict[str, Any]]:
        if name not in wb.sheetnames:
            return []
        rows = _sheet_to_dicts(wb[name])
        sheet_summary[name] = len(rows)
        return rows

    feed_rows = _rows("Feed")
    datasets = _rows("Datasets")
    lookups = _rows("Lookups")
    field_maps = _rows("FieldMap")
    key_generation = _rows("KeyGeneration")
    extract_params = _rows("ExtractParameters")
    output_layout = _rows("OutputLayout")

    if not feed_rows:
        raise ValueError("Workbook has no 'Feed' sheet row; cannot determine the feed name.")
    feed = dict(feed_rows[0])

    if extract_params:
        feed.setdefault("anchor_date_default", extract_params[0].get("anchor_date_default"))
        feed.setdefault("window_years_default", extract_params[0].get("window_years_default"))
    if output_layout:
        layout = output_layout[0]
        feed.setdefault("emit_header_row", layout.get("emit_header_row"))
        feed.setdefault("emit_trailer_row", layout.get("emit_trailer_row"))
        feed.setdefault("emit_concat_ws_line", layout.get("emit_concat_ws_line"))

    # KeyGeneration rows are additional FieldMap rows authored on a separate
    # sheet for clarity; merge them in, ordinal and all.
    field_maps = list(field_maps) + list(key_generation)

    return WorkbookConfig(
        feed=feed,
        datasets=datasets,
        lookups=lookups,
        field_maps=field_maps,
        workbook_path=path,
        workbook_sha256=workbook_sha256,
        sheet_summary=sheet_summary,
    )


def validate(config: WorkbookConfig, *, check_lookup_tables_exist=None) -> ValidationReport:
    """Validate a parsed workbook before any database write.

    ``check_lookup_tables_exist``, if given, is a callable
    ``(schema, table) -> bool`` (typically backed by a read-only connection's
    catalog check) - the one validation that genuinely needs a live database.
    Every other check here is structural and needs nothing but the workbook
    itself and the Python rule registry.
    """
    report = ValidationReport()

    if not config.feed.get("feed_name"):
        report.add("Feed", "feed_name is required")
    if not config.feed.get("source_server"):
        report.add("Feed", "source_server is required")
    if not config.feed.get("source_database"):
        report.add("Feed", "source_database is required")
    if not config.feed.get("output_root"):
        report.add("Feed", "output_root is required")
    delimiter = config.feed.get("delimiter", "|")
    collision_char = config.feed.get("collision_char", " ")
    if delimiter == collision_char:
        report.add("Feed", "collision_char must not equal delimiter")

    dataset_names = {d.get("dataset_name") for d in config.datasets if d.get("dataset_name")}
    if len(dataset_names) != len([d for d in config.datasets if d.get("dataset_name")]):
        report.add("Datasets", "duplicate dataset_name values found")

    dataset_by_name = {d.get("dataset_name"): d for d in config.datasets}

    for i, dataset in enumerate(config.datasets, start=2):  # row 1 is the header
        name = dataset.get("dataset_name")
        if not name:
            report.add("Datasets", "dataset_name is required", row=i)
            continue
        mode = dataset.get("dataset_mode")
        if mode not in ("primary", "dependent", "reference"):
            report.add("Datasets", "dataset_mode must be primary/dependent/reference, got {!r}".format(mode),
                        row=i, column="dataset_mode")
            continue
        if not dataset.get("source_schema") or not dataset.get("source_table"):
            report.add("Datasets", "source_schema and source_table are required", row=i)
        if not dataset.get("primary_key_columns"):
            report.add("Datasets", "primary_key_columns is required", row=i)

        if mode == "primary" and not dataset.get("window_date_column"):
            report.add("Datasets", "mode 'primary' requires window_date_column", row=i,
                        column="window_date_column")

        if mode == "dependent":
            depends_on = dataset.get("depends_on_dataset")
            if not depends_on:
                report.add("Datasets", "mode 'dependent' requires depends_on_dataset", row=i,
                            column="depends_on_dataset")
            elif depends_on not in dataset_by_name:
                report.add(
                    "Datasets",
                    "depends_on_dataset {!r} does not match any dataset_name in this workbook".format(depends_on),
                    row=i, column="depends_on_dataset",
                )
            elif depends_on == name:
                report.add("Datasets", "a dataset cannot depend on itself", row=i, column="depends_on_dataset")
            if not dataset.get("dependency_source_column") or not dataset.get("dependency_target_column"):
                report.add(
                    "Datasets",
                    "mode 'dependent' requires dependency_source_column and dependency_target_column",
                    row=i,
                )
            if dataset.get("include_own_window") and not dataset.get("window_date_column"):
                report.add("Datasets", "include_own_window requires window_date_column", row=i)

    _check_dependency_cycles(config.datasets, report)

    for i, lookup in enumerate(config.lookups, start=2):
        alias = lookup.get("lookup_alias")
        if not alias:
            report.add("Lookups", "lookup_alias is required", row=i)
        if not lookup.get("lookup_schema") or not lookup.get("lookup_table"):
            report.add("Lookups", "lookup_schema and lookup_table are required", row=i)
        elif check_lookup_tables_exist is not None:
            if not check_lookup_tables_exist(lookup["lookup_schema"], lookup["lookup_table"]):
                report.add(
                    "Lookups",
                    "table {}.{} does not exist in the source database".format(
                        lookup["lookup_schema"], lookup["lookup_table"]
                    ),
                    row=i,
                )

    lookup_aliases_by_dataset: Dict[Any, set] = {}
    for lookup in config.lookups:
        lookup_aliases_by_dataset.setdefault(lookup.get("dataset_name"), set()).add(lookup.get("lookup_alias"))

    registry = rules_mod.registry()
    seen_ordinals: Dict[Any, set] = {}
    seen_targets: Dict[Any, set] = {}

    for i, fm in enumerate(config.field_maps, start=2):
        dataset_name = fm.get("dataset_name")
        if dataset_name not in dataset_by_name:
            report.add("FieldMap", "dataset_name {!r} does not match any Datasets row".format(dataset_name), row=i)
            continue

        ordinal = fm.get("ordinal")
        target = fm.get("target_column")
        if ordinal is None:
            report.add("FieldMap", "ordinal is required", row=i)
        else:
            bucket = seen_ordinals.setdefault(dataset_name, set())
            if ordinal in bucket:
                report.add("FieldMap", "duplicate ordinal {} for dataset {!r}".format(ordinal, dataset_name), row=i)
            bucket.add(ordinal)

        if not target:
            report.add("FieldMap", "target_column is required", row=i)
        else:
            tbucket = seen_targets.setdefault(dataset_name, set())
            if target in tbucket:
                report.add("FieldMap", "duplicate target_column {!r} for dataset {!r}".format(target, dataset_name), row=i)
            tbucket.add(target)
            if any(c in target for c in ("|", "\r", "\n", "\t")):
                report.add("FieldMap", "target_column {!r} must not contain a delimiter or newline".format(target), row=i)

        source_expr = fm.get("source_expression") or ""
        if source_expr.startswith("lookup:"):
            try:
                alias = source_expr[len("lookup:"):].split(".", 1)[0]
            except IndexError:
                alias = None
            if not alias or alias not in lookup_aliases_by_dataset.get(dataset_name, set()):
                report.add(
                    "FieldMap",
                    "source_expression {!r} references an undefined lookup alias".format(source_expr),
                    row=i,
                )
        elif not source_expr:
            report.add("FieldMap", "source_expression is required", row=i)

        data_type = fm.get("data_type")
        if not data_type or not _DATA_TYPE_PATTERN.match(str(data_type)):
            report.add("FieldMap", "data_type {!r} is not a recognized token".format(data_type), row=i)

        rule_name = fm.get("rule_name") or "passthrough"
        rule_obj = registry.get(rule_name)
        if rule_obj is None:
            report.add(
                "FieldMap",
                "rule_name {!r} is not registered. Available: {}".format(rule_name, sorted(registry)),
                row=i, column="rule_name",
            )
        else:
            raw_params = fm.get("rule_params")
            try:
                params = rules_mod.parse_rule_params(raw_params if isinstance(raw_params, str) else
                                                      (json.dumps(raw_params) if raw_params else None))
                rules_mod.validate_params(rule_obj, params)
            except (ValueError, json.JSONDecodeError) as exc:
                report.add("FieldMap", "rule_params invalid for rule {!r}: {}".format(rule_name, exc), row=i)

    return report


def _check_dependency_cycles(datasets: List[Dict[str, Any]], report: ValidationReport) -> None:
    graph = {d.get("dataset_name"): d.get("depends_on_dataset") for d in datasets if d.get("dataset_mode") == "dependent"}
    for start in graph:
        seen = set()
        current = start
        while current in graph and graph[current]:
            if current in seen:
                report.add("Datasets", "dependency cycle detected starting at {!r}".format(start))
                break
            seen.add(current)
            current = graph[current]


def upsert(config: WorkbookConfig, conn, *, loaded_by: str) -> int:
    """Transactional upsert into meta.*. Caller must have already validated.

    Returns the new feed_config_version_id. Idempotent: reloading a workbook
    whose bytes are unchanged (same SHA-256) for the same feed is a no-op
    that returns the existing version id rather than duplicating it.
    """
    feed_name = config.feed["feed_name"]
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT feed_config_version_id FROM meta.feed_config_version "
            "JOIN meta.feed ON meta.feed.feed_id = meta.feed_config_version.feed_id "
            "WHERE meta.feed.feed_name = ? AND workbook_sha256 = ?",
            feed_name, config.workbook_sha256,
        )
        existing = cursor.fetchone()
        if existing:
            return int(existing[0])

        cursor.execute("SELECT feed_id FROM meta.feed WHERE feed_name = ?", feed_name)
        row = cursor.fetchone()
        if row:
            feed_id = int(row[0])
            cursor.execute(
                "UPDATE meta.feed SET source_server=?, source_database=?, delimiter=?, "
                "collision_action=?, collision_char=?, output_root=?, updated_at=SYSUTCDATETIME() "
                "WHERE feed_id=?",
                config.feed.get("source_server"), config.feed.get("source_database"),
                config.feed.get("delimiter", "|"), config.feed.get("collision_action", "sanitize"),
                config.feed.get("collision_char", " "), config.feed.get("output_root"), feed_id,
            )
        else:
            feed_id = conn_mod.execute_insert_return_identity(
                cursor,
                "INSERT INTO meta.feed (feed_name, source_server, source_database, delimiter, "
                "collision_action, collision_char, output_root) VALUES (?, ?, ?, ?, ?, ?, ?)",
                feed_name, config.feed.get("source_server"), config.feed.get("source_database"),
                config.feed.get("delimiter", "|"), config.feed.get("collision_action", "sanitize"),
                config.feed.get("collision_char", " "), config.feed.get("output_root"),
            )

        dataset_ids: Dict[str, int] = {}
        for dataset in config.datasets:
            name = dataset["dataset_name"]
            cursor.execute(
                "SELECT dataset_id FROM meta.dataset WHERE feed_id = ? AND dataset_name = ?", feed_id, name
            )
            existing_row = cursor.fetchone()
            if existing_row:
                dataset_id = int(existing_row[0])
                dataset_ids[name] = dataset_id
                # A dataset row already exists: update its core fields too, not
                # just resolve its id. Without this, reloading a workbook whose
                # window_date_column/source_table/etc changed would silently
                # keep serving the OLD values forever - directly defeating
                # goal #1 ("a mapping change is a metadata update and a
                # rerun. No code edit, no deploy."). Found via the manual
                # end-to-end walkthrough: editing the sample workbook's
                # window_date_column and reloading kept querying the old name.
                cursor.execute(
                    "UPDATE meta.dataset SET source_schema=?, source_table=?, dataset_mode=?, "
                    "primary_key_columns=?, window_date_column=?, output_file_stem=?, run_ordinal=? "
                    "WHERE dataset_id=?",
                    dataset["source_schema"], dataset["source_table"], dataset["dataset_mode"],
                    dataset["primary_key_columns"], dataset.get("window_date_column"),
                    dataset.get("output_file_stem", name), dataset.get("run_ordinal", 1),
                    dataset_id,
                )
            else:
                dataset_ids[name] = conn_mod.execute_insert_return_identity(
                    cursor,
                    "INSERT INTO meta.dataset (feed_id, dataset_name, source_schema, source_table, "
                    "dataset_mode, primary_key_columns, window_date_column, output_file_stem, run_ordinal) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    feed_id, name, dataset["source_schema"], dataset["source_table"], dataset["dataset_mode"],
                    dataset["primary_key_columns"], dataset.get("window_date_column"),
                    dataset.get("output_file_stem", name), dataset.get("run_ordinal", 1),
                )

        for dataset in config.datasets:
            if dataset.get("dataset_mode") != "dependent":
                continue
            cursor.execute(
                "UPDATE meta.dataset SET depends_on_dataset_id=?, dependency_source_column=?, "
                "dependency_target_column=?, include_own_window=? WHERE dataset_id=?",
                dataset_ids.get(dataset.get("depends_on_dataset")),
                dataset.get("dependency_source_column"),
                dataset.get("dependency_target_column"),
                bool(dataset.get("include_own_window", 0)),
                dataset_ids[dataset["dataset_name"]],
            )

        for dataset_id in dataset_ids.values():
            # Scoped reinsert, never a bare DELETE - matches
            # guardrails.classify_write_statement's allow-list exactly.
            cursor.execute("DELETE FROM meta.field_map WHERE dataset_id = ?", dataset_id)
            cursor.execute("DELETE FROM meta.dataset_lookup WHERE dataset_id = ?", dataset_id)

        for lookup in config.lookups:
            dataset_id = dataset_ids.get(lookup.get("dataset_name"))
            if dataset_id is None:
                continue
            cursor.execute(
                "INSERT INTO meta.dataset_lookup (dataset_id, lookup_alias, lookup_schema, lookup_table, "
                "join_source_column, join_lookup_column, join_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                dataset_id, lookup["lookup_alias"], lookup["lookup_schema"], lookup["lookup_table"],
                lookup["join_source_column"], lookup["join_lookup_column"], lookup.get("join_type", "left"),
            )

        for fm in config.field_maps:
            dataset_id = dataset_ids.get(fm.get("dataset_name"))
            if dataset_id is None:
                continue
            raw_params = fm.get("rule_params")
            params_json = raw_params if isinstance(raw_params, str) else (json.dumps(raw_params) if raw_params else None)
            cursor.execute(
                "INSERT INTO meta.field_map (dataset_id, ordinal, target_column, source_expression, "
                "data_type, rule_name, rule_params, nullable, default_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                dataset_id, fm["ordinal"], fm["target_column"], fm["source_expression"], fm["data_type"],
                fm.get("rule_name", "passthrough"), params_json, bool(fm.get("nullable", 1)),
                fm.get("default_value"),
            )

        sheet_summary_json = json.dumps(config.sheet_summary, sort_keys=True)
        new_version_id = conn_mod.execute_insert_return_identity(
            cursor,
            "INSERT INTO meta.feed_config_version (feed_id, workbook_filename, workbook_sha256, "
            "loaded_by, sheet_summary_json) VALUES (?, ?, ?, ?, ?)",
            feed_id, config.workbook_path.name, config.workbook_sha256, loaded_by, sheet_summary_json,
        )
        cursor.execute(
            "UPDATE meta.feed_config_version SET is_current = 0 WHERE feed_id = ? AND workbook_sha256 <> ?",
            feed_id, config.workbook_sha256,
        )
        return new_version_id
    finally:
        cursor.close()
