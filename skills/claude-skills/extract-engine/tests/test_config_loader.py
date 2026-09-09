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
