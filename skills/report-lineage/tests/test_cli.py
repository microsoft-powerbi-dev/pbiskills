"""Tests for reportlineage.cli. No network access; scan-git is not exercised here."""
from __future__ import annotations

import json

from reportlineage import __version__
from reportlineage.cli import main


def test_cli_version_prints_the_package_version(capsys):
    rc = main(["--version"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == __version__


def test_cli_no_subcommand_prints_help_and_returns_zero(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


def test_cli_scan_writes_estate_json_and_exports(fixtures_dir, tmp_path):
    out_dir = tmp_path / "out"
    rc = main(["scan", str(fixtures_dir), "--out", str(out_dir), "--mermaid", "--html", "--csv"])
    assert rc == 0

    estate_path = out_dir / "estate.json"
    assert estate_path.exists()
    data = json.loads(estate_path.read_text(encoding="utf-8"))
    assert "nodes" in data

    assert (out_dir / "estate.mmd").exists()
    assert (out_dir / "estate.mmd").read_text(encoding="utf-8").strip()
    assert (out_dir / "estate.html").exists()
    assert (out_dir / "nodes.csv").exists()
    assert (out_dir / "edges.csv").exists()


def test_cli_scan_with_no_matching_files_returns_zero_not_an_exception(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    rc = main(["scan", str(empty_dir), "--out", str(tmp_path / "out2")])
    assert rc == 0


def test_cli_duplicates_writes_json_and_prints_clusters(fixtures_dir, tmp_path, capsys):
    out_file = tmp_path / "dupes.json"
    rc = main(["duplicates", str(fixtures_dir), "--out", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert "clusters" in payload
    capsys.readouterr()  # drain, not asserted on content


def test_cli_duplicates_with_no_rdl_files_returns_zero(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    rc = main(["duplicates", str(empty_dir)])
    assert rc == 0


def test_cli_search_prints_ranked_results(fixtures_dir, capsys):
    rc = main(["search", str(fixtures_dir), "sales region"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip()


def test_cli_search_with_no_rdl_files_returns_zero(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    rc = main(["search", str(empty_dir), "sales"])
    assert rc == 0
