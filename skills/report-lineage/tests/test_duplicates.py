"""Tests for reportlineage.duplicates."""
from __future__ import annotations

from reportlineage.duplicates import analyze_duplicates, compare, find_reuse
from reportlineage.fingerprint import fingerprint_rdl


def _fingerprints(fixtures_dir):
    fp_a = fingerprint_rdl(str(fixtures_dir / "sample_report.rdl"), key="a")
    fp_b = fingerprint_rdl(str(fixtures_dir / "sample_report_near_dup.rdl"), key="b")
    fp_c = fingerprint_rdl(str(fixtures_dir / "sample_report_unrelated.rdl"), key="c")
    return fp_a, fp_b, fp_c


def test_compare_scores_near_duplicate_pair_higher_than_unrelated(fixtures_dir):
    fp_a, fp_b, fp_c = _fingerprints(fixtures_dir)

    pair_ab = compare(fp_a, fp_b)
    pair_ac = compare(fp_a, fp_c)

    assert pair_ab is not None
    assert pair_ab.similarity > 0.6
    assert pair_ac is None or pair_ac.similarity < pair_ab.similarity


def test_analyze_duplicates_clusters_similar_reports_and_excludes_unrelated(fixtures_dir):
    fp_a, fp_b, fp_c = _fingerprints(fixtures_dir)

    summary = analyze_duplicates([fp_a, fp_b, fp_c])

    assert len(summary.clusters) == 1
    cluster = summary.clusters[0]
    member_keys = {m.key for m in cluster.members}
    assert member_keys == {"a", "b"}
    assert cluster.keeper in member_keys
    assert cluster.action in ("retire_duplicates", "merge_into_one", "review")
    assert "c" in summary.unmatched


def test_analyze_duplicates_with_fewer_than_two_usable_reports_is_a_noop():
    fp = fingerprint_rdl("does_not_exist.rdl", key="only")
    summary = analyze_duplicates([fp])
    assert summary.clusters == []
    assert summary.pairs == []


def test_find_reuse_ranks_matching_report_above_unrelated(fixtures_dir):
    fp_a, _fp_b, fp_c = _fingerprints(fixtures_dir)

    matches = find_reuse([fp_a, fp_c], "I need a sales report broken down by region")
    by_key = {m.key: m for m in matches}

    assert by_key["a"].score > by_key["c"].score


def test_find_reuse_with_no_searchable_terms_still_returns_a_list(fixtures_dir):
    fp_a, _fp_b, _fp_c = _fingerprints(fixtures_dir)
    matches = find_reuse([fp_a], "")
    assert isinstance(matches, list)
