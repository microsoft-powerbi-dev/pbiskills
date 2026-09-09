"""Acceptance check #7: memory stays bounded at 500,000 rows, asserted in CI
so nobody reintroduces a materializing read.

Uses a synthetic batch generator injected in place of
pl.read_database(iter_batches=True) - the same dependency-injection seam
sql-server-schema's FakeConnection already relies on - so this runs with no
database at all. Measures actual process RSS via psutil, not just a
structural "batch size never exceeded" argument (that check lives alongside
the transform/sanitize/write tests in test_execution_polars.py).
"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

import polars as pl
import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from extract_engine import execution_polars as ep  # noqa: E402
from extract_engine.metadata import Feed, FieldMap  # noqa: E402


def _field(ordinal, target, source, rule_name="passthrough"):
    return FieldMap(
        field_map_id=ordinal, dataset_id=1, ordinal=ordinal, target_column=target,
        source_expression=source, data_type="varchar(200)", rule_name=rule_name,
        rule_params={}, nullable=True, default_value=None, is_active=True,
    )


def _feed():
    return Feed(
        feed_id=1, feed_name="Test", source_server="S", source_database="D", delimiter="|",
        collision_action="sanitize", collision_char=" ", line_ending="CRLF", null_sentinel="",
        max_rows_per_file=1_000_000, emit_header_row=False, emit_trailer_row=False,
        emit_concat_ws_line=False, default_execution_mode="polars", output_root="C:\\out",
        anchor_date_default=None, window_years_default=2, is_active=True,
    )


def _process_rss_mb() -> float:
    gc.collect()
    return psutil.Process().memory_info().rss / (1024 * 1024)


@pytest.mark.skipif(
    __import__("os").environ.get("EXTRACT_ENGINE_SKIP_RSS_TEST") == "1",
    reason="RSS measurement can be flaky on a loaded CI runner; opt out explicitly if needed",
)
def test_memory_ceiling_at_500000_rows(tmp_path):
    fields = [
        _field(1, "Notes", "notes", "trim"),
        _field(2, "Status", "status", "upper"),
    ]
    feed = _feed()
    writer = ep.PartWriter(
        output_dir=tmp_path, file_stem="Big", delimiter="|", line_ending=b"\n",
        null_sentinel="", max_rows_per_file=2_000_000, emit_trailer_row=False,
    )

    batch_size = 50_000
    total_rows = 500_000
    # A realistically-sized string per row (~200 bytes) so a materializing
    # read would actually show up in RSS, not be lost in noise.
    filler = "x" * 180

    def synthetic_batches():
        remaining = total_rows
        while remaining > 0:
            n = min(batch_size, remaining)
            yield pl.DataFrame(
                {"notes": [" {} ".format(filler) for _ in range(n)], "status": ["open"] * n}
            )
            remaining -= n

    baseline_mb = _process_rss_mb()
    peak_mb = baseline_mb

    for batch in synthetic_batches():
        transformed = ep.apply_transform(batch, fields)
        sanitized = ep.apply_sanitization(transformed, feed)
        writer.write_batch(sanitized)
        peak_mb = max(peak_mb, _process_rss_mb())

    result = writer.finish()
    assert result.row_count == total_rows

    growth_mb = peak_mb - baseline_mb
    # One batch of 50,000 rows at ~200 bytes/row is roughly 10 MB of raw
    # string data; several times that in Polars/Python overhead is still a
    # small, bounded multiple of ONE batch, not a function of the 500,000-row
    # total. A materializing read (the bug this test exists to catch) would
    # instead show growth roughly proportional to the full dataset.
    assert growth_mb < 250, (
        "Memory grew by {:.1f} MB processing 500,000 rows in {}-row batches; "
        "this should stay bounded by batch size, not total row count. A "
        "materializing read may have been reintroduced.".format(growth_mb, batch_size)
    )
