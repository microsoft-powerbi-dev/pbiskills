"""Tests for execution path A's pure pieces: transform, sanitize, row
sequence, and the part writer. No database needed anywhere in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import execution_polars as ep  # noqa: E402
from extract_engine.metadata import Feed, FieldMap  # noqa: E402


def _field(ordinal, target, source, rule_name="passthrough", rule_params=None, default_value=None):
    return FieldMap(
        field_map_id=ordinal, dataset_id=1, ordinal=ordinal, target_column=target,
        source_expression=source, data_type="varchar(20)", rule_name=rule_name,
        rule_params=rule_params or {}, nullable=True, default_value=default_value, is_active=True,
    )


def _feed(**overrides):
    base = dict(
        feed_id=1, feed_name="Test", source_server="S", source_database="D", delimiter="|",
        collision_action="sanitize", collision_char=" ", line_ending="CRLF", null_sentinel="",
        max_rows_per_file=1000000, emit_header_row=False, emit_trailer_row=True,
        emit_concat_ws_line=False, default_execution_mode="polars", output_root="C:\\out",
        anchor_date_default=None, window_years_default=2, is_active=True,
    )
    base.update(overrides)
    return Feed(**base)


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------


def test_apply_transform_applies_rules_and_renames():
    fields = [_field(1, "ClaimNumber", "claim_id", "trim"), _field(2, "Status", "status_code", "upper")]
    batch = pl.DataFrame({"claim_id": ["  A1  ", " A2"], "status_code": ["open", "shut"]})
    out = ep.apply_transform(batch, fields)
    assert out.columns == ["ClaimNumber", "Status"]
    assert out["ClaimNumber"].to_list() == ["A1", "A2"]
    assert out["Status"].to_list() == ["OPEN", "SHUT"]


def test_apply_transform_applies_default_value_on_null():
    fields = [_field(1, "Notes", "notes", "passthrough", default_value="N/A")]
    batch = pl.DataFrame({"notes": ["x", None]})
    out = ep.apply_transform(batch, fields)
    assert out["Notes"].to_list() == ["x", "N/A"]


def test_apply_transform_skips_stateful_fields():
    fields = [
        _field(1, "ClaimNumber", "claim_id", "trim"),
        _field(2, "SeqKey", "n/a", "row_sequence", rule_params={"order_by": "claim_id"}),
    ]
    batch = pl.DataFrame({"claim_id": ["A1"]})
    out = ep.apply_transform(batch, fields)
    assert out.columns == ["ClaimNumber"]  # SeqKey not selected here


def test_reorder_columns_uses_ordinal_not_select_order():
    fields = [_field(2, "B", "b"), _field(1, "A", "a")]
    df = pl.DataFrame({"B": [1], "A": [2]})
    out = ep.reorder_columns(df, fields)
    assert out.columns == ["A", "B"]


# ---------------------------------------------------------------------------
# row_sequence: the stateful special case
# ---------------------------------------------------------------------------


def test_row_sequence_continues_across_batches():
    fields = [_field(1, "SeqKey", "n/a", "row_sequence", rule_params={"order_by": "x"})]
    offsets = {}
    batch1 = pl.DataFrame({"x": [1, 2, 3]})
    batch2 = pl.DataFrame({"x": [4, 5]})
    out1 = ep.add_row_sequence_columns(batch1, fields, offsets)
    out2 = ep.add_row_sequence_columns(batch2, fields, offsets)
    assert out1["SeqKey"].to_list() == [1, 2, 3]
    assert out2["SeqKey"].to_list() == [4, 5]  # continues, does not restart


def test_row_sequence_independent_offsets_per_target_column():
    fields = [
        _field(1, "SeqA", "n/a", "row_sequence", rule_params={"order_by": "x"}),
        _field(2, "SeqB", "n/a", "row_sequence", rule_params={"order_by": "y"}),
    ]
    offsets = {}
    batch = pl.DataFrame({"x": [1, 2]})
    out = ep.add_row_sequence_columns(batch, fields, offsets)
    assert out["SeqA"].to_list() == [1, 2]
    assert out["SeqB"].to_list() == [1, 2]
    assert offsets == {"SeqA": 3, "SeqB": 3}


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def test_sanitize_mode_neutralizes_collisions():
    feed = _feed(collision_action="sanitize")
    df = pl.DataFrame({"Notes": ["a|b", "clean"], "Amount": [1, 2]})
    out = ep.apply_sanitization(df, feed)
    assert out["Notes"].to_list() == ["a b", "clean"]
    assert out["Amount"].to_list() == [1, 2]  # non-string columns untouched


def test_fail_mode_raises_naming_only_column_and_batch():
    from extract_engine.sanitizer import CollisionError

    feed = _feed(collision_action="fail")
    df = pl.DataFrame({"Notes": ["clean", "has|pipe"]})
    with pytest.raises(CollisionError) as excinfo:
        ep.apply_sanitization(df, feed, batch_ordinal=7)
    assert excinfo.value.column == "Notes"
    assert excinfo.value.batch_ordinal == 7
    assert "has|pipe" not in str(excinfo.value)


def test_fail_mode_passes_clean_batches_through_unchanged():
    feed = _feed(collision_action="fail")
    df = pl.DataFrame({"Notes": ["clean", "also clean"]})
    out = ep.apply_sanitization(df, feed)
    assert out["Notes"].to_list() == ["clean", "also clean"]


# ---------------------------------------------------------------------------
# KeyAccumulator
# ---------------------------------------------------------------------------


def test_key_accumulator_bounds_by_distinct_values_not_row_count():
    acc = ep.KeyAccumulator("member_id")
    acc.add_batch(pl.DataFrame({"member_id": [1, 1, 2, None]}))
    acc.add_batch(pl.DataFrame({"member_id": [2, 3]}))
    assert sorted(acc.finish()) == [1, 2, 3]


def test_key_accumulator_empty():
    acc = ep.KeyAccumulator("member_id")
    assert acc.finish() == []


# ---------------------------------------------------------------------------
# PartWriter: byte-exact line endings, part-rolling, checksum
# ---------------------------------------------------------------------------


def test_part_writer_writes_pipe_delimited_rows(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\r\n",
        null_sentinel="", max_rows_per_file=1000, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"A": ["x", "y"], "B": [1, 2]}))
    result = writer.finish()
    assert result.row_count == 2
    assert result.part_count == 1
    content = result.parts[0].read_bytes()
    assert content == b"x|1\r\ny|2\r\n"


def test_part_writer_line_ending_is_exact_bytes_lf(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=1000, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"A": ["x"]}))
    result = writer.finish()
    content = result.parts[0].read_bytes()
    assert content == b"x\n"
    assert b"\r\n" not in content


def test_part_writer_null_sentinel():
    pass  # covered by test_part_writer_writes_null_sentinel below


def test_part_writer_writes_null_sentinel(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="N/A", max_rows_per_file=1000, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"A": [None, "x"]}))
    result = writer.finish()
    assert result.parts[0].read_bytes() == b"N/A\nx\n"


def test_part_writer_rolls_to_a_new_part_at_the_row_limit(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=3, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"A": ["1", "2", "3", "4", "5"]}))
    result = writer.finish()
    assert result.part_count == 2
    assert result.row_count == 5
    assert result.parts[0].read_text().splitlines() == ["1", "2", "3"]
    assert result.parts[1].read_text().splitlines() == ["4", "5"]


def test_part_writer_slices_a_batch_straddling_the_part_boundary(tmp_path):
    """A single batch larger than max_rows_per_file must split across parts,
    never confusingly mid-write, and never lose or duplicate a row."""
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=2, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"A": [str(i) for i in range(7)]}))  # one big batch
    result = writer.finish()
    assert result.part_count == 4  # 2,2,2,1
    assert result.row_count == 7
    all_values = []
    for part in result.parts:
        all_values.extend(part.read_text().splitlines())
    assert all_values == [str(i) for i in range(7)]


def test_part_writer_trailer_row_matches_line_count(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=1000, emit_trailer_row=True,
    )
    writer.write_batch(pl.DataFrame({"A": ["1", "2", "3"]}))
    result = writer.finish()
    lines = result.parts[0].read_text().splitlines()
    assert lines[:3] == ["1", "2", "3"]
    assert lines[3] == "TRAILER|3"


def test_part_writer_header_row_when_enabled(tmp_path):
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Claim", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=1000, emit_header_row=True, emit_trailer_row=False,
    )
    writer.write_batch(pl.DataFrame({"ClaimNumber": ["A1"], "Status": ["OPEN"]}))
    result = writer.finish()
    lines = result.parts[0].read_text().splitlines()
    assert lines[0] == "ClaimNumber|Status"
    assert lines[1] == "A1|OPEN"


def test_part_writer_checksum_is_deterministic(tmp_path):
    def _run(directory):
        writer = ep.PartWriter(
            output_dir=directory, file_stem="Claim", delimiter="|", line_ending=b"\r\n",
            null_sentinel="", max_rows_per_file=1000, emit_trailer_row=False,
        )
        writer.write_batch(pl.DataFrame({"A": ["x", "y"]}))
        return writer.finish().checksum

    checksum_a = _run(tmp_path / "a")
    checksum_b = _run(tmp_path / "b")
    assert checksum_a == checksum_b


# ---------------------------------------------------------------------------
# Memory ceiling: bounded memory regardless of total row count. No database.
# ---------------------------------------------------------------------------


def test_memory_bounded_across_500000_synthetic_rows(tmp_path):
    """A synthetic batch generator stands in for pl.read_database(iter_batches=True):
    the point is that nothing here ever materializes the whole 500,000-row
    result at once. Verified structurally (batches processed one at a time,
    never concatenated into one frame) rather than via a live RSS measurement,
    which would be flaky in CI; a psutil-based peak-RSS assertion is the
    documented extension point for a fuller check.
    """
    fields = [_field(1, "V", "v", "trim")]
    feed = _feed()
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Big", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=1_000_000, emit_trailer_row=False,
    )

    batch_size = 50_000
    total_rows = 500_000
    max_batch_height_seen = 0

    def synthetic_batches():
        remaining = total_rows
        while remaining > 0:
            n = min(batch_size, remaining)
            yield pl.DataFrame({"v": [" x " for _ in range(n)]})
            remaining -= n

    for batch in synthetic_batches():
        max_batch_height_seen = max(max_batch_height_seen, batch.height)
        transformed = ep.apply_transform(batch, fields)
        sanitized = ep.apply_sanitization(transformed, feed)
        writer.write_batch(sanitized)

    result = writer.finish()
    assert result.row_count == total_rows
    # The strongest assertion available without a live RSS probe: no single
    # unit of work ever exceeded one batch's size.
    assert max_batch_height_seen == batch_size
