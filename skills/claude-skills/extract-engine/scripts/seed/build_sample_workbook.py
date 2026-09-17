"""
build_sample_workbook.py - regenerate the committed example workbook.

Writes examples/sample-workbook.xlsx: a small, entirely fictional three-
dataset feed (Claim/Member/StatusLookup) exercising all three dataset modes,
a calculator/lookup table join, and most of the rule catalog. Committed
because it is invented data - safe to commit, unlike anything loaded from a
real customer's Excel file.

Run: python scripts/seed/build_sample_workbook.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

_SKILL_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = _SKILL_ROOT / "examples" / "sample-workbook.xlsx"


def _write_sheet(wb, name, headers, rows):
    ws = wb.create_sheet(name)
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])


def build() -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # drop the default empty sheet

    _write_sheet(
        wb, "Feed",
        ["feed_name", "source_server", "source_database", "delimiter", "collision_action",
         "collision_char", "output_root", "default_execution_mode"],
        [{
            "feed_name": "ClaimsExtract", "source_server": "SQLPROD01", "source_database": "ClaimsDW",
            "delimiter": "|", "collision_action": "sanitize", "collision_char": " ",
            "output_root": "D:\\Extracts\\ClaimsExtract", "default_execution_mode": "polars",
        }],
    )

    _write_sheet(
        wb, "ExtractParameters",
        ["anchor_date_default", "window_years_default"],
        [{"anchor_date_default": "", "window_years_default": 2}],
    )

    # line_ending and max_rows_per_file are read from this sheet too, and every
    # column here is genuinely persisted to meta.feed now (they used to be
    # parsed and then dropped). The values match seed_schema.sql's own
    # defaults, so the sample feed behaves exactly as before - they are spelled
    # out to show an author where the knobs actually live.
    _write_sheet(
        wb, "OutputLayout",
        ["emit_header_row", "emit_trailer_row", "emit_concat_ws_line",
         "line_ending", "max_rows_per_file"],
        [{"emit_header_row": 0, "emit_trailer_row": 1, "emit_concat_ws_line": 0,
          "line_ending": "CRLF", "max_rows_per_file": 1000000}],
    )

    _write_sheet(
        wb, "Datasets",
        ["dataset_name", "source_schema", "source_table", "dataset_mode", "primary_key_columns",
         "window_date_column", "depends_on_dataset", "dependency_source_column",
         "dependency_target_column", "include_own_window", "output_file_stem", "run_ordinal"],
        [
            {"dataset_name": "Claim", "source_schema": "src", "source_table": "claim",
             "dataset_mode": "primary", "primary_key_columns": "ClaimId",
             "window_date_column": "service_date", "output_file_stem": "Claim", "run_ordinal": 1},
            {"dataset_name": "Member", "source_schema": "src", "source_table": "member",
             "dataset_mode": "dependent", "primary_key_columns": "MemberId",
             "depends_on_dataset": "Claim", "dependency_source_column": "member_id",
             "dependency_target_column": "member_id", "include_own_window": 0,
             "output_file_stem": "Member", "run_ordinal": 2},
            {"dataset_name": "StatusLookup", "source_schema": "src", "source_table": "status_lookup",
             "dataset_mode": "reference", "primary_key_columns": "StatusCode",
             "output_file_stem": "StatusLookup", "run_ordinal": 3},
        ],
    )

    _write_sheet(
        wb, "Lookups",
        ["dataset_name", "lookup_alias", "lookup_schema", "lookup_table",
         "join_source_column", "join_lookup_column", "join_type"],
        [{"dataset_name": "Claim", "lookup_alias": "calc", "lookup_schema": "src",
          "lookup_table": "claim_calc", "join_source_column": "claim_id",
          "join_lookup_column": "claim_id", "join_type": "left"}],
    )

    field_map_rows = [
        {"dataset_name": "Claim", "ordinal": 1, "target_column": "ClaimNumber",
         "source_expression": "claim_id", "data_type": "varchar(20)", "rule_name": "trim"},
        {"dataset_name": "Claim", "ordinal": 2, "target_column": "MemberId",
         "source_expression": "member_id", "data_type": "int", "rule_name": "passthrough"},
        {"dataset_name": "Claim", "ordinal": 3, "target_column": "ServiceDate",
         "source_expression": "service_date", "data_type": "date", "rule_name": "date_format",
         "rule_params": json.dumps({"style": 23, "polars_format": "%Y-%m-%d"})},
        {"dataset_name": "Claim", "ordinal": 4, "target_column": "PaidAmount",
         "source_expression": "lookup:calc.amount", "data_type": "decimal(18,2)",
         "rule_name": "decimal_format", "rule_params": json.dumps({"scale": 2, "mask": "N2"})},
        {"dataset_name": "Claim", "ordinal": 5, "target_column": "StatusCode",
         "source_expression": "status_code", "data_type": "varchar(10)", "rule_name": "upper"},
        {"dataset_name": "Claim", "ordinal": 6, "target_column": "Notes",
         "source_expression": "notes", "data_type": "varchar(200)", "rule_name": "default_if_null",
         "rule_params": json.dumps({"value": "N/A"})},
        {"dataset_name": "Member", "ordinal": 1, "target_column": "MemberId",
         "source_expression": "member_id", "data_type": "int", "rule_name": "passthrough"},
        {"dataset_name": "Member", "ordinal": 2, "target_column": "MemberName",
         "source_expression": "member_name", "data_type": "varchar(100)", "rule_name": "trim"},
        {"dataset_name": "StatusLookup", "ordinal": 1, "target_column": "StatusCode",
         "source_expression": "status_code", "data_type": "varchar(10)", "rule_name": "passthrough"},
        {"dataset_name": "StatusLookup", "ordinal": 2, "target_column": "Description",
         "source_expression": "description", "data_type": "varchar(50)", "rule_name": "trim"},
    ]
    _write_sheet(
        wb, "FieldMap",
        ["dataset_name", "ordinal", "target_column", "source_expression", "data_type",
         "rule_name", "rule_params", "nullable", "default_value"],
        field_map_rows,
    )

    _write_sheet(
        wb, "KeyGeneration",
        ["dataset_name", "ordinal", "target_column", "source_expression", "data_type",
         "rule_name", "rule_params"],
        [{"dataset_name": "Claim", "ordinal": 7, "target_column": "SurrogateKey",
          "source_expression": "n/a", "data_type": "bigint", "rule_name": "row_sequence",
          "rule_params": json.dumps({"order_by": "claim_id"})}],
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build()
    print("wrote", path)
