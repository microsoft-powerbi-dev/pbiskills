"""Tests for reportlineage.scanners.filesystem."""
from __future__ import annotations

from reportlineage.scanners.filesystem import scan_filesystem


def test_scan_filesystem_classifies_files_by_extension(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "report.rdl").write_text("<Report/>", encoding="utf-8")
    (tmp_path / "sub" / "package.dtsx").write_text("<pkg/>", encoding="utf-8")
    (tmp_path / "view.sql").write_text("CREATE VIEW dbo.V AS SELECT 1", encoding="utf-8")
    (tmp_path / "conn.conmgr").write_text("<conn/>", encoding="utf-8")
    (tmp_path / "dataset.rsd").write_text("<rsd/>", encoding="utf-8")
    (tmp_path / "source.rds").write_text("<rds/>", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")

    result = scan_filesystem(str(tmp_path))

    assert any(p.endswith("report.rdl") for p in result.rdl_paths)
    assert any(p.endswith("package.dtsx") for p in result.dtsx_paths)
    assert any(p.endswith("view.sql") for p in result.sql_paths)
    assert any(p.endswith("conn.conmgr") for p in result.conmgr_paths)
    assert any(p.endswith("dataset.rsd") for p in result.rsd_paths)
    assert any(p.endswith("source.rds") for p in result.rsd_paths)
    assert not any(p.endswith("notes.txt") for p in result.rdl_paths)
    assert result.skipped == []


def test_scan_filesystem_nonexistent_root_does_not_raise(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = scan_filesystem(str(missing))
    assert result.rdl_paths == []
    assert result.dtsx_paths == []
    assert result.skipped
