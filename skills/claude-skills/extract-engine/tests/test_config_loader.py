"""Tests for the Excel-to-metadata loader's validation, and the upsert's
transactional shape against a FakeConnection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_engine import config_loader as cl  # noqa: E402
from fakes import FakeConnection  # noqa: E402


def _valid_config(**overrides):
    feed = {
        "feed_name": "ClaimsExtract", "source_server": "SQLPROD01", "source_database": "ClaimsDW",
        "output_root": "D:\\Extracts\\ClaimsExtract", "delimiter": "|", "collision_char": " ",
    }
    datasets = [
        {"dataset_name": "Claim", "source_schema": "src", "source_table": "claim", "dataset_mode": "primary",
         "primary_key_columns": "ClaimId", "window_date_column": "ServiceDate"},
        {"dataset_name": "Member", "source_schema": "src", "source_table": "member", "dataset_mode": "dependent",
         "primary_key_columns": "MemberId", "depends_on_dataset": "Claim",
         "dependency_source_column": "MemberId", "dependency_target_column": "member_id"},
    ]
    lookups = [
        {"dataset_name": "Claim", "lookup_alias": "calc", "lookup_schema": "src", "lookup_table": "claim_calc",
         "join_source_column": "claim_id", "join_lookup_column": "claim_id", "join_type": "left"},
    ]
    field_maps = [
        {"dataset_name": "Claim", "ordinal": 1, "target_column": "ClaimNumber", "source_expression": "claim_id",
         "data_type": "varchar(20)", "rule_name": "trim"},
        {"dataset_name": "Claim", "ordinal": 2, "target_column": "PaidAmount",
         "source_expression": "lookup:calc.amount", "data_type": "decimal(18,2)",
         "rule_name": "decimal_format", "rule_params": {"scale": 2, "mask": "N2"}},
        {"dataset_name": "Member", "ordinal": 1, "target_column": "MemberName", "source_expression": "name",
         "data_type": "varchar(100)", "rule_name": "trim"},
    ]
    config = cl.WorkbookConfig(
        feed=feed, datasets=datasets, lookups=lookups, field_maps=field_maps,
        workbook_path=Path("test.xlsx"), workbook_sha256="a" * 64, sheet_summary={},
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_valid_config_has_no_issues():
    report = cl.validate(_valid_config())
    assert report.ok, str(report)


def test_missing_feed_fields():
    config = _valid_config()
    config.feed = {}
    report = cl.validate(config)
    messages = str(report)
    assert "feed_name" in messages
    assert "source_server" in messages
    assert "output_root" in messages


def test_collision_char_equal_to_delimiter_is_rejected():
    config = _valid_config()
    config.feed["collision_char"] = config.feed["delimiter"]
    report = cl.validate(config)
    assert not report.ok
    assert "collision_char" in str(report)


def test_primary_mode_requires_window_date_column():
    config = _valid_config()
    config.datasets[0]["window_date_column"] = None
    report = cl.validate(config)
    assert any("window_date_column" in str(i) for i in report.issues)


def test_dependent_mode_requires_depends_on_dataset():
    config = _valid_config()
    del config.datasets[1]["depends_on_dataset"]
    report = cl.validate(config)
    assert any("depends_on_dataset" in str(i) for i in report.issues)


def test_dependent_mode_depends_on_dataset_must_resolve():
    config = _valid_config()
    config.datasets[1]["depends_on_dataset"] = "NoSuchDataset"
    report = cl.validate(config)
    assert any("does not match any dataset_name" in str(i) for i in report.issues)


def test_dependent_mode_cannot_depend_on_itself():
    config = _valid_config()
    config.datasets[1]["depends_on_dataset"] = "Member"
    report = cl.validate(config)
    assert any("cannot depend on itself" in str(i) for i in report.issues)


def test_dependency_cycle_is_detected():
    config = _valid_config()
    config.datasets[0]["dataset_mode"] = "dependent"
    config.datasets[0]["depends_on_dataset"] = "Member"
    config.datasets[0]["dependency_source_column"] = "x"
    config.datasets[0]["dependency_target_column"] = "y"
    config.datasets[1]["depends_on_dataset"] = "Claim"
    report = cl.validate(config)
    assert any("cycle" in str(i) for i in report.issues)


def test_unknown_rule_name_is_rejected():
    config = _valid_config()
    config.field_maps[0]["rule_name"] = "does_not_exist"
    report = cl.validate(config)
    assert any("not registered" in str(i) for i in report.issues)


def test_missing_required_rule_params_is_rejected():
    config = _valid_config()
    config.field_maps[0]["rule_name"] = "pad_left"
    config.field_maps[0]["rule_params"] = {"char": "0"}  # missing 'width'
    report = cl.validate(config)
    assert any("rule_params invalid" in str(i) for i in report.issues)


def test_undefined_lookup_alias_is_rejected():
    config = _valid_config()
    config.field_maps[1]["source_expression"] = "lookup:nope.amount"
    report = cl.validate(config)
    assert any("undefined lookup alias" in str(i) for i in report.issues)


def test_target_column_with_embedded_delimiter_is_rejected():
    config = _valid_config()
    config.field_maps[0]["target_column"] = "Bad|Name"
    report = cl.validate(config)
    assert any("delimiter or newline" in str(i) for i in report.issues)


def test_duplicate_ordinal_is_rejected():
    config = _valid_config()
    config.field_maps[1]["dataset_name"] = "Claim"
    config.field_maps[1]["ordinal"] = 1  # collides with field_maps[0]
    report = cl.validate(config)
    assert any("duplicate ordinal" in str(i) for i in report.issues)


def test_duplicate_target_column_is_rejected():
    config = _valid_config()
    config.field_maps[1]["target_column"] = "ClaimNumber"
    report = cl.validate(config)
    assert any("duplicate target_column" in str(i) for i in report.issues)


def test_unrecognized_data_type_is_rejected():
    config = _valid_config()
    config.field_maps[0]["data_type"] = "geography"
    report = cl.validate(config)
    assert any("not a recognized token" in str(i) for i in report.issues)


def test_check_lookup_tables_exist_hook_is_used():
    config = _valid_config()
    report = cl.validate(config, check_lookup_tables_exist=lambda schema, table: False)
    assert any("does not exist" in str(i) for i in report.issues)


# ---------------------------------------------------------------------------
# upsert: transactional shape, idempotency, scoped delete-and-reinsert
# ---------------------------------------------------------------------------


def test_upsert_is_idempotent_on_unchanged_hash():
    config = _valid_config()
    conn = FakeConnection(
        {r"FROM meta\.feed_config_version": (["feed_config_version_id"], [(99,)])}
    )
    version_id = cl.upsert(config, conn, loaded_by="DOMAIN\\svc")
    assert version_id == 99
    assert conn.writes == []  # a no-op reload writes nothing


def test_the_committed_sample_workbook_loads_and_validates():
    """A real .xlsx round trip, not just an in-memory WorkbookConfig - this
    is what caught openpyxl-specific issues during development that a purely
    synthetic WorkbookConfig object could not have."""
    path = Path(__file__).resolve().parents[1] / "examples" / "sample-workbook.xlsx"
    assert path.exists(), "run scripts/seed/build_sample_workbook.py to regenerate"
    config = cl.load_workbook(str(path))
    assert config.feed["feed_name"] == "ClaimsExtract"
    assert len(config.datasets) == 3
    assert len(config.field_maps) == 11  # 10 FieldMap + 1 KeyGeneration row
    report = cl.validate(config)
    assert report.ok, str(report)


def test_upsert_updates_an_existing_datasets_fields_on_reload():
    """Found via the manual end-to-end walkthrough: the first cut of upsert()
    only looked up an existing dataset's id and never updated its other
    columns, so reloading a workbook whose window_date_column changed kept
    querying the OLD column name forever. This is the regression test."""
    config = _valid_config()
    conn = FakeConnection({
        r"FROM meta\.feed_config_version": (["feed_config_version_id"], []),
        r"SELECT feed_id FROM meta\.feed": (["feed_id"], [(1,)]),
        # Both datasets already exist (Claim=10, Member=20).
        r"SELECT dataset_id FROM meta\.dataset WHERE feed_id = \? AND dataset_name = \?":
            (["dataset_id"], [(10,)]),  # FakeConnection replays this same row for every
                                          # matching call, which is fine: the test only
                                          # needs to see the UPDATE fire with the right SQL.
        r"SCOPE_IDENTITY": (["id"], [(1,)]),
    })
    cl.upsert(config, conn, loaded_by="DOMAIN\\svc")
    update_dataset_sql = [sql for sql, _ in conn.writes if sql.strip().upper().startswith("UPDATE META.DATASET SET SOURCE_SCHEMA")]
    assert update_dataset_sql, "existing dataset rows must be updated, not just looked up"
    # The new window_date_column value from the reloaded workbook must be in
    # the UPDATE's params, not silently dropped.
    sql, params = next(w for w in conn.writes if w[0] in update_dataset_sql)
    assert "ServiceDate" in params or "service_date" in [p for p in params if isinstance(p, str)]


def test_upsert_never_sends_a_bare_delete():
    """Every DELETE this loader issues must be scoped, per
    guardrails.classify_write_statement's allow-list."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from extract_engine import guardrails

    config = _valid_config()
    conn = FakeConnection({
        r"FROM meta\.feed_config_version": (["feed_config_version_id"], []),
        r"SELECT feed_id FROM meta\.feed": (["feed_id"], []),
        r"SELECT dataset_id FROM meta\.dataset": (["dataset_id"], []),
        r"SCOPE_IDENTITY": (["id"], [(1,)]),
    })
    cl.upsert(config, conn, loaded_by="DOMAIN\\svc")
    for sql, _params in conn.writes:
        verdict = guardrails.classify_write_statement(sql)
        assert verdict.allowed, "{!r} should be on the write allow-list: {}".format(sql, verdict.reason)


# ---------------------------------------------------------------------------
# data_type tokens: the validator and the Polars dtype mapping must agree.
# Two live defects sat here - the validator rejected every nvarchar(n) and
# every MAX length, and accepted nchar(n) which the mapping then rejected at
# read-plan time, after the config write had already committed.
# ---------------------------------------------------------------------------

_MAPPABLE_TOKENS = (
    "int", "bigint", "bit", "date", "datetime", "datetime2",
    "varchar(20)", "varchar(MAX)", "nvarchar(100)", "nvarchar(MAX)",
    "char(5)", "nchar(5)", "decimal(18,2)", "decimal(18, 2)",
)


def test_every_accepted_data_type_token_is_also_mappable_to_a_polars_dtype():
    """The lockstep property. A token the validator admits but
    data_type_to_polars cannot map is a config write that commits and then
    blows up when the read plan is built."""
    from extract_engine.query_builder import data_type_to_polars

    for token in _MAPPABLE_TOKENS:
        assert cl._DATA_TYPE_PATTERN.match(token), "validator rejects {!r}".format(token)
        assert data_type_to_polars(token) is not None, "no dtype for {!r}".format(token)


def test_nvarchar_and_max_lengths_are_accepted():
    for token in ("nvarchar(100)", "nvarchar(MAX)", "varchar(MAX)", "nchar(5)"):
        config = _valid_config()
        config.field_maps[0]["data_type"] = token
        report = cl.validate(config)
        assert report.ok, "{} rejected: {}".format(token, report)


def test_still_rejects_a_token_with_no_dtype_mapping():
    for token in ("float", "geography", "varchar", "nvarchar(50", "MAX)"):
        config = _valid_config()
        config.field_maps[0]["data_type"] = token
        assert not cl.validate(config).ok, "{} should be rejected".format(token)


# ---------------------------------------------------------------------------
# The optional meta.feed settings. These were parsed, validated, then dropped
# on the floor by upsert(), so every feed silently ran on the DDL defaults.
# ---------------------------------------------------------------------------


def _upsert_feed_sql(config, *, existing_feed_id=None):
    """Run upsert far enough to capture the meta.feed INSERT or UPDATE."""
    script = {r"SCOPE_IDENTITY": (["id"], [(7,)])}
    if existing_feed_id is not None:
        script[r"SELECT feed_id FROM meta\.feed"] = (["feed_id"], [(existing_feed_id,)])
    conn = FakeConnection(script)
    cl.upsert(config, conn, loaded_by="DOMAIN\\svc")
    for sql, params in conn.writes:
        if "meta.feed " in sql or "meta.feed(" in sql or sql.strip().upper().startswith(
            ("INSERT INTO META.FEED ", "UPDATE META.FEED")
        ):
            return sql, params
    raise AssertionError("no meta.feed write captured: {}".format(conn.write_texts()))


def test_upsert_persists_the_optional_feed_settings_on_insert():
    config = _valid_config()
    config.feed.update({
        "line_ending": "LF", "null_sentinel": "NULL", "max_rows_per_file": 250000,
        "emit_header_row": 1, "emit_trailer_row": 0, "emit_concat_ws_line": 1,
        "default_execution_mode": "sql", "anchor_date_default": "2026-06-30",
        "window_years_default": 5, "collision_action": "fail",
    })
    assert cl.validate(config).ok, str(cl.validate(config))
    sql, params = _upsert_feed_sql(config)
    import datetime

    for column in ("line_ending", "null_sentinel", "max_rows_per_file", "emit_header_row",
                    "emit_trailer_row", "emit_concat_ws_line", "default_execution_mode",
                    "anchor_date_default", "window_years_default", "collision_action"):
        assert column in sql, "{} missing from {}".format(column, sql)
    assert "LF" in params and "sql" in params and "fail" in params
    assert 250000 in params and 5 in params
    assert datetime.date(2026, 6, 30) in params
    assert sql.count("?") == len(params)


def test_upsert_persists_the_optional_feed_settings_on_reload_of_an_existing_feed():
    config = _valid_config()
    config.feed["default_execution_mode"] = "sql"
    config.feed["max_rows_per_file"] = 99
    sql, params = _upsert_feed_sql(config, existing_feed_id=3)
    assert sql.strip().upper().startswith("UPDATE META.FEED SET")
    assert "default_execution_mode=?" in sql and "max_rows_per_file=?" in sql
    assert "sql" in params and 99 in params
    assert sql.count("?") == len(params)


def test_an_omitted_optional_setting_is_not_written_at_all():
    """A blank or absent cell must mean 'keep what the database has', never
    'overwrite it with NULL' - otherwise dropping an optional sheet silently
    resets settings an operator configured on purpose."""
    config = _valid_config()
    config.feed["anchor_date_default"] = ""        # blank cell, as openpyxl reads it
    sql, _ = _upsert_feed_sql(config)
    for absent in ("line_ending", "max_rows_per_file", "emit_header_row",
                    "default_execution_mode", "anchor_date_default", "window_years_default"):
        assert absent not in sql, "{} should not be written: {}".format(absent, sql)


def test_unusable_feed_settings_are_validation_issues_not_constraint_violations():
    cases = {
        "emit_header_row": "maybe",
        "default_execution_mode": "duckdb",
        "line_ending": "CR",
        "max_rows_per_file": 0,
        "window_years_default": -1,
        "anchor_date_default": "30/06/2026",
        "collision_action": "ignore",
    }
    for column, bad_value in cases.items():
        config = _valid_config()
        config.feed[column] = bad_value
        report = cl.validate(config)
        assert not report.ok, "{}={!r} should be rejected".format(column, bad_value)
        assert column in str(report), str(report)


def test_feed_setting_issues_are_reported_against_their_authoring_sheet():
    config = _valid_config()
    config.feed["emit_header_row"] = "maybe"
    config.feed["window_years_default"] = "soon"
    messages = str(cl.validate(config))
    assert "OutputLayout" in messages
    assert "ExtractParameters" in messages


def test_feed_settings_are_normalised_to_what_the_db_check_constraints_accept():
    settings, issues = cl.coerce_feed_settings({
        "line_ending": "lf", "default_execution_mode": "SQL", "collision_action": "Fail",
        "emit_trailer_row": "yes", "max_rows_per_file": "1000",
    })
    assert issues == []
    assert settings["line_ending"] == "LF"
    assert settings["default_execution_mode"] == "sql"
    assert settings["collision_action"] == "fail"
    assert settings["emit_trailer_row"] is True
    assert settings["max_rows_per_file"] == 1000


def test_the_sample_workbook_supplies_the_output_layout_settings():
    """load_workbook must fold OutputLayout's file-format columns into the feed
    dict, or the sheet is decoration again."""
    workbook = Path(__file__).resolve().parent.parent / "examples" / "sample-workbook.xlsx"
    if not workbook.exists():
        return
    config = cl.load_workbook(str(workbook))
    settings, issues = cl.coerce_feed_settings(config.feed)
    assert issues == []
    assert settings["line_ending"] == "CRLF"
    assert settings["max_rows_per_file"] == 1000000
    assert settings["default_execution_mode"] == "polars"
    assert settings["window_years_default"] == 2
    assert settings["emit_trailer_row"] is True
    # anchor_date_default is deliberately blank in the sample workbook
    assert "anchor_date_default" not in settings
