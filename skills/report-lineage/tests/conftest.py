"""Shared pytest fixtures for the reportlineage test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Directory containing the synthetic RDL/DTSX/conmgr/SQL fixture files."""
    return FIXTURES_DIR


@pytest.fixture
def sample_rdl_path(fixtures_dir: Path) -> str:
    return str(fixtures_dir / "sample_report.rdl")


@pytest.fixture
def sample_dtsx_path(fixtures_dir: Path) -> str:
    return str(fixtures_dir / "sample_package.dtsx")


@pytest.fixture
def sample_conmgr_path(fixtures_dir: Path) -> str:
    return str(fixtures_dir / "sample_connection.conmgr")


@pytest.fixture
def sample_sql_path(fixtures_dir: Path) -> str:
    return str(fixtures_dir / "sample_view.sql")
