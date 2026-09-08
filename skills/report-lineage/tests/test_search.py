"""Tests for reportlineage.search."""
from __future__ import annotations

from reportlineage.fingerprint import fingerprint_rdl
from reportlineage.search import SearchIndex


def _build_index(fixtures_dir):
    fp_a = fingerprint_rdl(str(fixtures_dir / "sample_report.rdl"), key="a")
    fp_b = fingerprint_rdl(str(fixtures_dir / "sample_report_near_dup.rdl"), key="b")
    fp_c = fingerprint_rdl(str(fixtures_dir / "sample_report_unrelated.rdl"), key="c")
    return SearchIndex([fp_a, fp_b, fp_c]), fp_a, fp_b, fp_c


def test_search_ranks_sales_reports_over_unrelated_employee_report(fixtures_dir):
    index, fp_a, fp_b, fp_c = _build_index(fixtures_dir)

    results = index.search("sales region report", limit=5)
    assert results

    scores_by_key = {fp.key: score for fp, score in results}
    assert scores_by_key.get(fp_a.key, 0.0) >= scores_by_key.get(fp_c.key, 0.0)


def test_search_respects_limit(fixtures_dir):
    index, *_ = _build_index(fixtures_dir)
    results = index.search("sales", limit=1)
    assert len(results) <= 1


def test_by_table_returns_every_report_referencing_the_signature(fixtures_dir):
    index, fp_a, fp_b, fp_c = _build_index(fixtures_dir)

    shared_table = next(iter(fp_a.tables & fp_b.tables))
    hits = index.by_table(shared_table)
    hit_keys = {fp.key for fp in hits}

    assert fp_a.key in hit_keys
    assert fp_b.key in hit_keys
    assert fp_c.key not in hit_keys


def test_by_table_returns_empty_list_for_unknown_signature(fixtures_dir):
    index, *_ = _build_index(fixtures_dir)
    assert index.by_table("nope::dbo.nothing") == []
