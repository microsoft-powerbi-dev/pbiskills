"""
Report Studio MCP Server — the Windsurf-facing tool surface.

Exposes the source backend's deterministic report generation as MCP tools that an
agentic IDE (Windsurf's Cascade, Claude Code, etc.) drives over stdio. The
division of labour is the Report Studio philosophy — *the LLM proposes, a
deterministic oracle disposes*: the agent's own model designs the report (an
``IRWorkbook``) and this server schema-validates and renders it locally. The
server itself makes **no cloud call and needs no API key**; the only model in
the loop is the host IDE's, which is that customer's AI-governance boundary.

Tools:
  * ``list_grounding``   — inventory an SSRS estate (tables/columns/measures +
                           native-SQL M-queries) so the agent grounds its design.
  * ``get_ir_schema``    — the IRWorkbook JSON contract the agent must emit.
  * ``validate_ir``      — cheap pre-check: normalize + validate, no render.
  * ``render_report``    — render an agent-designed IRWorkbook to a PBIP.
  * ``generate_from_rdl``— deterministic, zero-LLM SSRS .rdl -> PBIP.
  * ``batch_generate``   — render many reports from a manifest, fail-soft.
  * ``fix_pbip``         — apply the Power BI Feb 2026 schema-compat fixes.

Every tool is a plain Python function callable in-process; when ``fastmcp`` is
installed they are also registered as MCP tools and ``main()`` serves them over
stdio (``python -m app.mcp.report_studio_server``).

Direct usage (in-process, no MCP):
    from app.mcp.report_studio_server import render_report, list_grounding
    result = render_report(ir_json, "out/report1")
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastmcp import FastMCP

    _mcp: Optional[Any] = FastMCP("report-studio")
except ImportError:
    _mcp = None


def _tool(fn):
    """Register with FastMCP when available; otherwise return the function unchanged."""
    if _mcp is not None:
        return _mcp.tool()(fn)
    return fn


# ---------------------------------------------------------------------------
# Offline / no-egress enforcement (defense-in-depth for air-gapped installs)
# ---------------------------------------------------------------------------

_CLOUD_AI_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_FOUNDRY_API_KEY",
)


def offline_violations() -> List[str]:
    """Return cloud-AI settings that are configured (empty when the env is clean).

    Used by the no-egress guard. The render/convert tools never call these, but
    an air-gapped operator can assert the process was launched with a clean env.
    """
    found = [name for name in _CLOUD_AI_ENV if (os.environ.get(name) or "").strip()]
    provider = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if provider and provider != "none":
        found.append(f"AI_PROVIDER={provider}")
    return found


def _enforce_offline_if_requested() -> None:
    """Refuse to start when ENFORCE_NO_EGRESS is set and a key is present."""
    flag = (os.environ.get("ENFORCE_NO_EGRESS") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    violations = offline_violations()
    if violations:
        raise SystemExit(
            "ENFORCE_NO_EGRESS is set but cloud AI configuration was "
            f"detected: {', '.join(violations)}. Unset these before starting the "
            "server, or clear the enforcement flag."
        )


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


@_tool
def list_grounding(rdl_paths: List[str]) -> Dict[str, Any]:
    """Inventory an SSRS estate to ground report design on real names.

    Parses each SSRS ``.rdl`` and returns the authoritative tables, columns
    (with types), measures, and the native-SQL M-queries that reuse the source
    connection. Feed this back into the model before it designs an IRWorkbook so
    it targets real ``table``/``column`` names instead of inventing them.

    Args:
        rdl_paths: paths to existing SSRS .rdl reports.

    Returns:
        {tables: [{name, columns: [{name, data_type}], measures: [name]}],
         m_queries: {normalized_table_name: native_sql_m_query},
         errors: [str]}
    """
    from app.core.report_studio.grounding import GroundingSources
    from app.core.report_studio.pipeline import _grounding_m_queries
    from app.core.report_studio.schema import build_grounded_schema

    errors: List[str] = []
    existing = []
    for p in rdl_paths or []:
        if Path(p).exists():
            existing.append(p)
        else:
            errors.append(f"RDL not found: {p}")

    sources = GroundingSources(rdl_paths=existing)
    schema = build_grounded_schema(sources)
    tables = [
        {
            "name": t.name,
            "columns": [{"name": c.name, "data_type": c.data_type} for c in t.columns],
            "measures": [m.name for m in t.measures],
        }
        for t in schema.tables
    ]
    return {
        "tables": tables,
        "m_queries": _grounding_m_queries(existing),
        "errors": errors,
    }


def _resolve_ir_schema_doc() -> str:
    """Return the IRWorkbook contract markdown (repo prompts/sophia-ir.md)."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "prompts" / "sophia-ir.md",   # repo root /prompts
        here.parents[2] / "prompts" / "sophia-ir.md",   # backend/prompts
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    # Fall back to the canonical loader (returns a minimal contract if absent).
    from app.core.parser.prompt_to_ir import _load_template

    return _load_template()


@_tool
def get_ir_schema() -> str:
    """Return the IRWorkbook JSON contract the agent must emit for render_report.

    This is the exact schema (field names, enum values, required shape) that
    ``render_report``/``validate_ir`` accept. Emit one JSON object matching it.
    """
    return _resolve_ir_schema_doc()


@_tool
def validate_ir(ir_json: str) -> Dict[str, Any]:
    """Validate an IRWorkbook design without rendering (a cheap pre-check loop).

    Args:
        ir_json: the IRWorkbook as JSON text (a fenced ```json block is accepted).

    Returns:
        {valid: bool, error: str|None, name: str|None,
         datasource_count: int, visual_count: int}
    """
    from app.core.report_studio.render import coerce_ir

    try:
        wb = coerce_ir(ir_json)
    except ValueError as exc:
        return {"valid": False, "error": str(exc)}
    return {
        "valid": True,
        "error": None,
        "name": wb.name,
        "datasource_count": len(wb.datasources),
        "visual_count": len(wb.worksheets),
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


@_tool
def render_report(
    ir_json: str, out_dir: str, project_name: Optional[str] = None
) -> Dict[str, Any]:
    """Render an agent-designed IRWorkbook to a packaged Power BI project (PBIP).

    The agent designs the report as IRWorkbook JSON (see ``get_ir_schema``); this
    validates it, reconciles it, and renders it through the deterministic
    pipeline. No LLM/cloud call is made here.

    Args:
        ir_json: the IRWorkbook design as JSON text.
        out_dir: directory to write the PBIP build tree and ZIP into.
        project_name: optional project-name override.

    Returns:
        A result dict: {success, status, output_zip, build_dir, project_name,
        error, warnings, logs}.
    """
    from app.core.report_studio.render import render_ir_to_pbip

    return render_ir_to_pbip(ir_json, out_dir, project_name=project_name).to_dict()


@_tool
def generate_from_rdl(
    rdl_path: str, out_dir: str, paginated: bool = False
) -> Dict[str, Any]:
    """Convert an existing SSRS ``.rdl`` to a PBIP — fully deterministic, zero-LLM.

    The near-term self-service win: reuse or re-point an existing report with no
    model involved. Set ``paginated`` for an SSRS->Paginated re-point instead of
    interactive PBIP.

    Args:
        rdl_path: path to the existing SSRS .rdl.
        out_dir: directory to write output into.
        paginated: re-point to paginated RDL instead of interactive PBIP.

    Returns:
        A result dict (same shape as ``render_report``).
    """
    from app.core.report_studio.render import render_rdl_to_pbip

    return render_rdl_to_pbip(rdl_path, out_dir, paginated=paginated).to_dict()


@_tool
def batch_generate(manifest_json: str, out_dir: str) -> Dict[str, Any]:
    """Render many reports from a manifest in one offline run, fail-soft per item.

    Args:
        manifest_json: JSON text — a list of items, each an object with
            ``kind`` = ``"rdl"`` (``{kind, rdl, [paginated], [out]}``) or
            ``"ir"`` (``{kind, ir | ir_file, [project_name], [out]}``).
        out_dir: root directory; each item renders into ``out_dir/<slug>/``.

    Returns:
        {total, succeeded, failed, out_dir, report_path, results: [...]}. One bad
        item is captured as a failed result; the run continues.
    """
    from app.core.report_studio.batch import run_batch

    try:
        manifest = json.loads(manifest_json)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"total": 0, "succeeded": 0, "failed": 0, "error": f"Invalid manifest JSON: {exc}"}
    try:
        return run_batch(manifest, out_dir).to_dict()
    except ValueError as exc:
        return {"total": 0, "succeeded": 0, "failed": 0, "error": str(exc)}


@_tool
def fix_pbip(pbip_dir: str, report_md: Optional[str] = None) -> Dict[str, Any]:
    """Apply Power BI Desktop Feb 2026 schema-compatibility fixes to a PBIP in place.

    Args:
        pbip_dir: the PBIP directory (contains *.pbip, *.Report/, *.SemanticModel/).
        report_md: optional path to write a compatibility report markdown.

    Returns:
        {ok, files_changed: [str], fixes: [str], error: str|None}.
    """
    from app.core.pbip.fixer import PBIPFixer

    in_dir = Path(pbip_dir)
    if not in_dir.exists():
        return {"ok": False, "files_changed": [], "fixes": [], "error": f"PBIP directory not found: {pbip_dir}"}
    try:
        fixer = PBIPFixer(
            input_dir=in_dir,
            output_dir=in_dir,
            report_path=Path(report_md) if report_md else None,
        )
        report = fixer.run()
    except Exception as exc:  # noqa: BLE001 - surface as a tool error, never crash the server
        return {"ok": False, "files_changed": [], "fixes": [], "error": str(exc)}
    return {
        "ok": True,
        "files_changed": [str(f) for f in report.files_changed],
        "fixes": [str(e) for e in report.entries],
        "error": None,
    }


def main() -> None:
    """Serve the tools over stdio for an MCP client (Windsurf, Claude Code, ...)."""
    _enforce_offline_if_requested()
    if _mcp is None:
        raise SystemExit(
            "fastmcp is not installed. Install it (pip install fastmcp) to run the "
            "Report Studio MCP server, or import the tool functions directly."
        )
    _mcp.run()


if __name__ == "__main__":
    main()
