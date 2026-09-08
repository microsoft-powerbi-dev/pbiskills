"""
mcp_server.py - an MCP server exposing this skill's RDL builder and validator
as tools, for an agentic IDE (Claude Code, Devin, Windsurf, Cursor, or any
other MCP client) to drive over stdio.

Unlike the two MCP servers under this repository's ``backend/app/mcp/``
(``report_studio_server.py``, ``pbi_refine_server.py``), this one has no
dependency on the backend at all. It only imports its two sibling scripts,
``rdl_builder.py`` and ``validate_rdl.py``, both of which are themselves
dependency-free (Python 3.9+ standard library only). Copy this whole
``scripts/`` folder into any project and it runs as-is.

Tools
-----
  * ``get_json_spec_schema``   -- the JSON spec ``build_rdl`` accepts, so the
                                  agent emits a spec in the exact shape.
  * ``build_rdl``              -- build and save an .rdl file from a JSON spec,
                                  then validate the result.
  * ``validate_rdl_file``      -- validate an existing .rdl file on disk.
  * ``field_expression``       -- the exact round-trip-safe VB.NET expression
                                  for a field reference or an aggregate.
  * ``list_reference_topics``  -- the reference docs this skill ships.
  * ``get_reference``          -- the content of one reference doc, by topic.
  * ``list_examples``          -- the five bundled working .rdl examples.
  * ``get_example``             -- the content of one example, by name.

Every tool is a plain, directly callable Python function; when the optional
``fastmcp`` package is installed they are also registered as MCP tools and
``main()`` serves them over stdio.

Direct usage (in-process, no MCP)::

    from mcp_server import build_rdl, validate_rdl_file
    result = build_rdl(spec_json, "out/SalesByRegion.rdl")

Served over stdio::

    pip install fastmcp   # optional; only needed for the stdio server
    python mcp_server.py

Point an MCP client's config at this file, for example (Claude Code /
Claude Desktop ``.mcp.json``-style config)::

    {
      "mcpServers": {
        "rdl-generation": {
          "command": "python",
          "args": ["/absolute/path/to/scripts/mcp_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make this script's own directory importable regardless of the caller's
# working directory or sys.path, so ``rdl_builder`` and ``validate_rdl``
# resolve as plain sibling-module imports (matching how rdl_builder.py and
# validate_rdl.py are designed to be copied and run standalone).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from rdl_builder import build_from_spec, field_ref, aggregate_ref  # noqa: E402
from validate_rdl import validate as _validate_rdl_file  # noqa: E402

_SKILL_ROOT = _HERE.parent
_REFERENCES_DIR = _SKILL_ROOT / "references"
_EXAMPLES_DIR = _SKILL_ROOT / "examples"

try:
    from fastmcp import FastMCP

    _mcp: Optional[Any] = FastMCP("rdl-generation")
except ImportError:
    _mcp = None


def _tool(fn):
    """Register with FastMCP when available; otherwise return the function unchanged."""
    if _mcp is not None:
        return _mcp.tool()(fn)
    return fn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@_tool
def get_json_spec_schema() -> str:
    """Return the JSON spec schema that ``build_rdl`` accepts.

    This is the exact shape (top-level keys, required vs optional, per-column
    tuple order) documented in ``rdl_builder.py``'s module docstring. Emit a
    JSON object matching it, then pass it as ``spec_json`` to ``build_rdl``.
    """
    import rdl_builder

    doc = rdl_builder.__doc__ or ""
    marker = "JSON spec schema"
    idx = doc.find(marker)
    return doc[idx:] if idx != -1 else doc


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@_tool
def build_rdl(spec_json: str, out_path: str, validate: bool = True) -> Dict[str, Any]:
    """Build and save an ``.rdl`` file from a JSON spec, then validate it.

    Args:
        spec_json: the report spec as JSON text (see ``get_json_spec_schema``).
        out_path: where to write the ``.rdl`` file (parent directories are
            created as needed).
        validate: when true (the default), immediately run the structural
            validator on the file just written and include its findings.

    Returns:
        {ok: bool, path: str, error: str|None,
         findings: [{severity, code, message}], report_name: str}

        ``ok`` is true when the file was written successfully AND (if
        ``validate`` is true) no error-severity finding was raised. A
        malformed spec never raises; it comes back as ``ok: false`` with
        ``error`` set.
    """
    try:
        spec = json.loads(spec_json)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "path": out_path, "error": f"Invalid JSON: {exc}", "findings": [], "report_name": None}

    out = Path(out_path)
    try:
        builder = build_from_spec(spec, default_report_name=out.stem)
        saved_path = builder.save(out)
    except (KeyError, ValueError, TypeError) as exc:
        return {"ok": False, "path": out_path, "error": f"Could not build RDL from spec: {exc}", "findings": [], "report_name": None}

    findings: List[Dict[str, str]] = []
    has_error = False
    if validate:
        for finding in _validate_rdl_file(str(saved_path)):
            findings.append({"severity": finding.severity, "code": finding.code, "message": finding.message})
            if finding.severity == "error":
                has_error = True

    return {
        "ok": not has_error,
        "path": str(saved_path),
        "error": None,
        "findings": findings,
        "report_name": spec.get("report_name") or out.stem,
    }


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@_tool
def validate_rdl_file(path: str, strict: bool = False) -> Dict[str, Any]:
    """Validate an existing ``.rdl`` file for structural correctness.

    Runs the same checks as ``validate_rdl.py``: well-formed XML, a
    recognised RDL namespace, ``Query`` child element order, every
    ``Fields!X.Value`` reference resolving to a declared ``Field``, every
    ``DataSetName`` resolving to a declared ``DataSet``, every measurement
    carrying a unit, ``PageHeader``/``PageFooter`` nested inside ``Page``,
    and a nonzero ``Body`` height.

    Args:
        path: path to the ``.rdl`` file to validate.
        strict: when true, treat any warning-severity finding as a failure
            too (``ok`` becomes false).

    Returns:
        {ok: bool, findings: [{severity, code, message}],
         error_count: int, warning_count: int}
    """
    findings = _validate_rdl_file(path)
    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    ok = error_count == 0 and (not strict or warning_count == 0)
    return {
        "ok": ok,
        "findings": [{"severity": f.severity, "code": f.code, "message": f.message} for f in findings],
        "error_count": error_count,
        "warning_count": warning_count,
    }


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@_tool
def field_expression(field_name: str, aggregate: Optional[str] = None) -> str:
    """Return the round-trip-safe RDL expression for a field or an aggregate.

    Without ``aggregate``: ``=Fields!Name.Value``. With ``aggregate`` (one of
    ``Sum``, ``Avg``, ``Min``, ``Max``, ``Count``, ``CountDistinct``,
    ``First``, ``Last``): ``=Sum(Fields!Name.Value)``. These are the exact
    forms this repository's RDL parser reads back; use this instead of
    hand-composing the string.

    Args:
        field_name: the dataset field name.
        aggregate: an optional aggregate function name.

    Returns:
        The expression string, e.g. ``"=Fields!Amount.Value"`` or
        ``"=Sum(Fields!Amount.Value)"``.
    """
    if aggregate:
        return aggregate_ref(aggregate, field_name)
    return field_ref(field_name)


# ---------------------------------------------------------------------------
# Reference docs and examples
# ---------------------------------------------------------------------------


@_tool
def list_reference_topics() -> List[str]:
    """Return the reference-doc topics this skill ships (without the ``.md``).

    Pass one of these to ``get_reference`` for the full content: the exact
    document skeleton and element order, DataSource/DataSet/Field/Parameter
    XML, the Tablix and Chart object models, expression syntax, layout units,
    schema versions, or the pre-flight validation checklist.
    """
    if not _REFERENCES_DIR.is_dir():
        return []
    return sorted(p.stem for p in _REFERENCES_DIR.glob("*.md"))


@_tool
def get_reference(topic: str) -> Dict[str, Any]:
    """Return the full content of one reference doc.

    Args:
        topic: a name from ``list_reference_topics`` (with or without ``.md``).

    Returns:
        {ok: bool, topic: str, content: str|None, error: str|None}
    """
    name = topic if topic.endswith(".md") else f"{topic}.md"
    path = _REFERENCES_DIR / name
    if not path.is_file():
        return {"ok": False, "topic": topic, "content": None, "error": f"No reference doc named {name!r}."}
    return {"ok": True, "topic": topic, "content": path.read_text(encoding="utf-8"), "error": None}


@_tool
def list_examples() -> List[str]:
    """Return the names of the five bundled, validating example ``.rdl`` files."""
    if not _EXAMPLES_DIR.is_dir():
        return []
    return sorted(p.name for p in _EXAMPLES_DIR.glob("*.rdl"))


@_tool
def get_example(name: str) -> Dict[str, Any]:
    """Return the full XML content of one bundled example ``.rdl`` file.

    Args:
        name: a name from ``list_examples`` (with or without ``.rdl``).

    Returns:
        {ok: bool, name: str, content: str|None, error: str|None}
    """
    filename = name if name.endswith(".rdl") else f"{name}.rdl"
    path = _EXAMPLES_DIR / filename
    if not path.is_file():
        return {"ok": False, "name": name, "content": None, "error": f"No example named {filename!r}."}
    return {"ok": True, "name": name, "content": path.read_text(encoding="utf-8"), "error": None}


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Serve the tools over stdio for an MCP client (Claude Code, Devin, Windsurf, ...)."""
    if _mcp is None:
        raise SystemExit(
            "fastmcp is not installed. Install it (pip install fastmcp) to run this "
            "as an MCP server, or import the tool functions directly from mcp_server.py."
        )
    _mcp.run()


if __name__ == "__main__":
    main()
