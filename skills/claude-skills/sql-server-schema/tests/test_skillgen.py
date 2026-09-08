"""Tests for the skill-pack generator.

Includes the two properties that matter most: the generator is deterministic
(so a pack is diffable as a schema-drift detector), and it refuses to write
anywhere git would pick the pack up (so an internal schema cannot reach a
public repository by accident).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze  # noqa: E402
import guardrails  # noqa: E402
import skillgen  # noqa: E402

from test_analyze import _star  # noqa: E402

EXAMPLE_DIGEST = (
    Path(__file__).resolve().parent.parent / "examples" / "schema-digest.example.json"
)


@pytest.fixture
def digest():
    tables, foreign_keys = _star()
    return analyze.build_digest(
        database="SalesDW",
        tables=tables,
        foreign_keys=foreign_keys,
        generated_at="2026-01-01T00:00:00Z",
        server_alias="demo-sql",
    )


def test_generates_the_expected_file_set(digest, tmp_path):
    result = skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    assert result["ok"] is True
    names = {Path(f["path"]).name for f in result["files"]}
    assert {"SKILL.md", "REDACTIONS.md", "schema_digest.json", ".gitignore"} <= names
    assert "00-index.md" in names
    assert "01-join-graph.md" in names
    assert "02-conventions.md" in names
    assert "03-query-recipes.md" in names
    assert any(name.startswith("schema-dbo") for name in names)


def test_pack_carries_its_own_gitignore(digest, tmp_path):
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    ignore = (tmp_path / "pack" / ".gitignore").read_text(encoding="utf-8")
    assert ignore.strip().endswith("*")
    assert "Never commit" in ignore


def test_skill_md_warns_against_committing(digest, tmp_path):
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    text = (tmp_path / "pack" / "SKILL.md").read_text(encoding="utf-8")
    assert "do not commit" in text.lower()
    assert "do_not_commit: true" in text


def test_skill_md_frontmatter_is_quoted_and_read_only(digest, tmp_path):
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    text = (tmp_path / "pack" / "SKILL.md").read_text(encoding="utf-8")
    # 'Schema: X' must be quoted or YAML parses the name as a mapping.
    assert 'name: "Schema: SalesDW"' in text
    # A reference pack must not be able to modify anything.
    assert "  - Read" in text
    assert "  - Write" not in text
    assert "  - Bash" not in text


def test_skill_md_frontmatter_parses_as_yaml(digest, tmp_path):
    yaml = pytest.importorskip("yaml")
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    text = (tmp_path / "pack" / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, _ = text.split("---", 2)
    parsed = yaml.safe_load(frontmatter)
    assert parsed["name"] == "Schema: SalesDW"
    assert parsed["allowed-tools"] == ["Read", "Glob", "Grep"]
    assert parsed["metadata"]["do_not_commit"] is True
    assert isinstance(parsed["triggers"], list) and parsed["triggers"]


def test_index_locates_every_table(digest, tmp_path):
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    index = (tmp_path / "pack" / "references" / "00-index.md").read_text(encoding="utf-8")
    for table in digest["tables"]:
        assert "{}.{}".format(table["schema"], table["name"]) in index


def test_generation_is_deterministic(digest, tmp_path):
    first = skillgen.generate_skill_pack(digest, str(tmp_path / "a"))
    second = skillgen.generate_skill_pack(digest, str(tmp_path / "b"))
    assert first["ok"] and second["ok"]
    for one, two in zip(sorted(first["files"], key=lambda f: f["path"]),
                        sorted(second["files"], key=lambda f: f["path"])):
        assert Path(one["path"]).name == Path(two["path"]).name
        assert (
            Path(one["path"]).read_text(encoding="utf-8")
            == Path(two["path"]).read_text(encoding="utf-8")
        )


def test_dry_run_writes_nothing(digest, tmp_path):
    target = tmp_path / "pack"
    result = skillgen.generate_skill_pack(digest, str(target), dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["files"], "dry run should still report what it would write"
    assert not target.exists()


def test_sensitive_columns_are_marked_and_never_sampled(tmp_path):
    tables, foreign_keys = _star()
    tables[0]["columns"].append(
        {"name": "MemberSSN", "data_type": "char", "type": "char(11)",
         "nullable": True, "is_identity": False, "is_computed": False,
         "default": None, "description": None}
    )
    digest = analyze.build_digest(
        database="SalesDW", tables=tables, foreign_keys=foreign_keys,
        generated_at="fixed", server_alias="demo-sql",
    )
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    schema_doc = next((tmp_path / "pack" / "references").glob("schema-dbo*.md"))
    text = schema_doc.read_text(encoding="utf-8")
    assert "withheld: sensitive (ssn)" in text
    redactions = (tmp_path / "pack" / "REDACTIONS.md").read_text(encoding="utf-8")
    assert "MemberSSN" in redactions
    assert "hard_deny" in redactions


def test_refuses_to_write_into_the_repository(digest):
    """The mechanical guarantee behind the public-repo decision."""
    repo_path = Path(__file__).resolve().parents[3] / "generated-pack"
    with pytest.raises(PermissionError):
        skillgen.generate_skill_pack(digest, str(repo_path))


def test_rejects_an_invalid_digest(tmp_path):
    result = skillgen.generate_skill_pack({"tables": []}, str(tmp_path / "pack"))
    assert result["ok"] is False
    assert result["errors"]


def test_query_recipes_include_the_soft_delete_filter(digest, tmp_path):
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    recipes = (tmp_path / "pack" / "references" / "03-query-recipes.md").read_text(
        encoding="utf-8"
    )
    assert "FactOrder" in recipes
    assert "IsDeleted = 0" in recipes
    assert "soft delete convention detected" in recipes
    # Half-open date ranges, and no SELECT *.
    assert "half-open range" in recipes
    assert "SELECT *" not in recipes


def test_join_graph_marks_inferred_edges(tmp_path):
    tables, _ = _star()
    digest = analyze.build_digest(
        database="SalesDW", tables=tables, foreign_keys=[], generated_at="fixed"
    )
    skillgen.generate_skill_pack(digest, str(tmp_path / "pack"))
    graph_doc = (tmp_path / "pack" / "references" / "01-join-graph.md").read_text(
        encoding="utf-8"
    )
    assert "inferred" in graph_doc
    assert "verify" in graph_doc


def test_large_schemas_are_chunked(tmp_path):
    many = [
        {
            "schema": "dbo",
            "name": "Table{:03d}".format(index),
            "columns": [{"name": "Id", "data_type": "int", "type": "int", "nullable": False}],
            "primary_key": {"name": "PK", "columns": ["Id"]},
            "row_count": index,
            "object_type": "USER_TABLE",
        }
        for index in range(95)
    ]
    digest = analyze.build_digest(
        database="Big", tables=many, foreign_keys=[], generated_at="fixed"
    )
    result = skillgen.generate_skill_pack(
        digest, str(tmp_path / "pack"), max_tables_per_file=40
    )
    names = sorted(Path(f["path"]).name for f in result["files"])
    assert "schema-dbo-1.md" in names
    assert "schema-dbo-3.md" in names
    index = (tmp_path / "pack" / "references" / "00-index.md").read_text(encoding="utf-8")
    # Every part must be reachable from the index, or the chunking hid tables.
    assert "schema-dbo-3.md" in index


def test_column_lists_are_capped(tmp_path):
    wide = [{
        "schema": "dbo", "name": "Wide",
        "columns": [
            {"name": "C{:03d}".format(i), "data_type": "int", "type": "int", "nullable": True}
            for i in range(120)
        ],
        "primary_key": None, "row_count": 10, "object_type": "USER_TABLE",
    }]
    digest = analyze.build_digest(
        database="W", tables=wide, foreign_keys=[], generated_at="fixed"
    )
    skillgen.generate_skill_pack(
        digest, str(tmp_path / "pack"), max_columns_per_table=60
    )
    text = next((tmp_path / "pack" / "references").glob("schema-dbo*.md")).read_text(
        encoding="utf-8"
    )
    assert "60 further columns" in text
    assert "schema_digest.json" in text


# ---------------------------------------------------------------------------
# The committed example, which is safe because its schema is invented
# ---------------------------------------------------------------------------


def test_example_digest_is_valid_and_fictional():
    assert EXAMPLE_DIGEST.exists(), "run scripts/cli.py example to regenerate"
    data = json.loads(EXAMPLE_DIGEST.read_text(encoding="utf-8"))
    assert analyze.validate_digest(data) == []
    assert data["source"]["database"] == "SalesDW"
    assert data["source"]["server_alias"] == "demo-sql"


def test_example_digest_generates_cleanly(tmp_path):
    data = json.loads(EXAMPLE_DIGEST.read_text(encoding="utf-8"))
    result = skillgen.generate_skill_pack(data, str(tmp_path / "pack"))
    assert result["ok"] is True
    assert len(result["files"]) >= 8
