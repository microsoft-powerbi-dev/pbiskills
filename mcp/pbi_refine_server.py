"""
PBI Refinement MCP Server — 7 tools for inspecting and patching a PBIP folder.

All tools are plain Python functions that can be called directly.  When the
optional ``fastmcp`` package is present they are also registered as MCP tools
and the server can be mounted at ``/mcp/refine`` via SSE transport.

Direct usage (in-process):
    from app.mcp.pbi_refine_server import structural_compare, apply_patch, ...
    result = structural_compare("/path/to/build/MyProject")
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastmcp import FastMCP
    _mcp: Optional[Any] = FastMCP("pbi-refine")
except ImportError:
    _mcp = None


def _tool(fn):
    """Register with FastMCP when available; otherwise return the function unchanged."""
    if _mcp is not None:
        return _mcp.tool()(fn)
    return fn


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_report_dir(build_dir: Path) -> Optional[Path]:
    """Return the first *.Report directory found directly inside build_dir."""
    if not build_dir.exists():
        return None
    for candidate in build_dir.iterdir():
        if candidate.is_dir() and candidate.name.endswith(".Report"):
            return candidate
    return None


def _find_model_bim(build_dir: Path) -> Optional[Path]:
    """Return the first model.bim found anywhere under build_dir."""
    for p in build_dir.rglob("model.bim"):
        return p
    return None


def _find_tmdl_database(build_dir: Path) -> Optional[Path]:
    """Return the database.tmdl file if the model is in TMDL format."""
    for p in build_dir.rglob("database.tmdl"):
        return p
    return None


def _tmdl_table_count(build_dir: Path) -> int:
    """Count tables defined as .tmdl files under a SemanticModel definition folder."""
    count = 0
    for p in build_dir.rglob("*.SemanticModel"):
        tables_dir = p / "definition" / "tables"
        if tables_dir.exists():
            count = sum(1 for f in tables_dir.iterdir() if f.suffix == ".tmdl")
    return count


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tool 1: validate_dax
# ---------------------------------------------------------------------------

@_tool
def validate_dax(build_dir: str) -> Dict:
    """
    Parse the semantic model in *build_dir* (TMSL model.bim or TMDL) and return
    a schema summary with table/column/measure names, flagging TODO stub measures.
    """
    bd = Path(build_dir)
    bim_path = _find_model_bim(bd)
    if bim_path is not None:
        return _validate_dax_bim(bim_path)
    tmdl_db = _find_tmdl_database(bd)
    if tmdl_db is not None:
        return _validate_dax_tmdl(tmdl_db.parent)
    return {"tables": [], "errors": ["No semantic model found (model.bim or database.tmdl)"]}


def _validate_dax_bim(bim_path: Path) -> Dict:
    errors: List[str] = []
    try:
        raw = _load_json(bim_path)
    except Exception as exc:
        return {"tables": [], "errors": [f"Failed to parse model.bim: {exc}"]}

    tables = []
    for tbl in raw.get("model", {}).get("tables", []):
        name = tbl.get("name", "")
        columns = [
            {"name": c.get("name", ""), "dataType": c.get("dataType", "unknown")}
            for c in tbl.get("columns", [])
        ]
        measures = []
        for m in tbl.get("measures", []):
            expr = m.get("expression", "") or ""
            has_stub = "// TODO" in expr or expr.strip().startswith("/*")
            measures.append({"name": m.get("name", ""), "expression": expr, "has_stub": has_stub})
            if has_stub:
                errors.append(f"Stub measure in '{name}'.[{m.get('name', '')}]: needs manual DAX")
        tables.append({"name": name, "columns": columns, "measures": measures})
    return {"tables": tables, "errors": errors}


def _validate_dax_tmdl(definition_dir: Path) -> Dict:
    import re
    errors: List[str] = []
    tables = []
    tables_dir = definition_dir / "tables"
    if not tables_dir.is_dir():
        return {"tables": [], "errors": ["TMDL tables/ directory not found"]}

    tbl_re = re.compile(r"^table\s+(?:'([^']+)'|(\S.*?))\s*$")
    col_re = re.compile(r"^\tcolumn\s+(?:'([^']+)'|(\S+))")
    meas_re = re.compile(r"^\tmeasure\s+(?:'([^']+)'|([^=]+?))\s*=\s*(.*)")

    for tmdl in sorted(tables_dir.glob("*.tmdl")):
        current_table = ""
        columns: List[Dict] = []
        measures: List[Dict] = []
        in_measure_expr = False
        measure_lines: List[str] = []
        measure_name = ""

        def _flush_measure() -> None:
            nonlocal in_measure_expr, measure_lines, measure_name
            if measure_name:
                expr = "\n".join(measure_lines).strip()
                has_stub = "// TODO" in expr or expr.strip().startswith("/*")
                measures.append({"name": measure_name, "expression": expr, "has_stub": has_stub})
                if has_stub:
                    errors.append(f"Stub measure in '{current_table}'.[{measure_name}]: needs manual DAX")
            in_measure_expr = False
            measure_lines = []
            measure_name = ""

        for raw in tmdl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = tbl_re.match(raw)
            if m:
                current_table = (m.group(1) or m.group(2) or "").strip()
                continue
            m = col_re.match(raw)
            if m:
                _flush_measure()
                columns.append({"name": (m.group(1) or m.group(2) or "").strip(), "dataType": "unknown"})
                continue
            m = meas_re.match(raw)
            if m:
                _flush_measure()
                measure_name = (m.group(1) or m.group(2) or "").strip()
                first_line = (m.group(3) or "").strip()
                if first_line:
                    measure_lines.append(first_line)
                in_measure_expr = True
                continue
            if in_measure_expr:
                stripped = raw.strip()
                if stripped.startswith("///") or (not raw.startswith("\t\t") and raw.strip() and not raw.startswith("\t\t")):
                    _flush_measure()
                else:
                    measure_lines.append(stripped)

        _flush_measure()
        if current_table:
            tables.append({"name": current_table, "columns": columns, "measures": measures})
    return {"tables": tables, "errors": errors}


# ---------------------------------------------------------------------------
# Tool 2: get_schema
# ---------------------------------------------------------------------------

@_tool
def get_schema(build_dir: str) -> Dict:
    """
    Return a lightweight schema summary (table/column names + relationships)
    from the semantic model (TMSL model.bim or TMDL).
    """
    bd = Path(build_dir)
    bim_path = _find_model_bim(bd)
    if bim_path is not None:
        try:
            raw = _load_json(bim_path)
        except Exception as exc:
            return {"tables": [], "relationships": [], "error": str(exc)}
        model = raw.get("model", {})
        tables = [
            {"name": t.get("name", ""), "column_count": len(t.get("columns", [])),
             "measure_count": len(t.get("measures", []))}
            for t in model.get("tables", [])
        ]
        relationships = [
            {"from": f"{r.get('fromTable', '')}[{r.get('fromColumn', '')}]",
             "to": f"{r.get('toTable', '')}[{r.get('toColumn', '')}]",
             "cross_filter": r.get("crossFilteringBehavior", "oneDirection")}
            for r in model.get("relationships", [])
        ]
        return {"tables": tables, "relationships": relationships}

    tmdl_db = _find_tmdl_database(bd)
    if tmdl_db is None:
        return {"tables": [], "relationships": [], "error": "No semantic model found"}

    dax_result = _validate_dax_tmdl(tmdl_db.parent)
    tables = [
        {"name": t["name"], "column_count": len(t.get("columns", [])),
         "measure_count": len(t.get("measures", []))}
        for t in dax_result.get("tables", [])
    ]
    from app.core.validation.structural import relationship_inventory
    model_dir = None
    for p in bd.glob("*.SemanticModel"):
        if p.is_dir():
            model_dir = p
            break
    rels_raw = relationship_inventory(model_dir) if model_dir else []
    relationships = [
        {"from": r.get("from", ""), "to": r.get("to", ""),
         "cross_filter": "oneDirection"}
        for r in rels_raw
    ]
    return {"tables": tables, "relationships": relationships}


# ---------------------------------------------------------------------------
# Tool 3: get_visual_config
# ---------------------------------------------------------------------------

@_tool
def get_visual_config(build_dir: str, page_name: str, visual_name: str) -> Dict:
    """
    Read and return the PBIR visual.json for *visual_name* on *page_name*.

    Path resolved as:
        <build_dir>/<ProjectName>.Report/definition/pages/<page_name>/visuals/<visual_name>/visual.json
    """
    report_dir = _find_report_dir(Path(build_dir))
    if report_dir is None:
        return {"error": "No .Report directory found in build_dir"}

    visual_path = (
        report_dir / "definition" / "pages" / page_name / "visuals" / visual_name / "visual.json"
    )
    if not visual_path.exists():
        # Try glob for case-insensitive or partial matches
        candidates = list((report_dir / "definition" / "pages").glob(
            f"*{page_name}*/visuals/*{visual_name}*/visual.json"
        ))
        if not candidates:
            return {"error": f"visual.json not found for page={page_name!r} visual={visual_name!r}"}
        visual_path = candidates[0]

    try:
        data = _load_json(visual_path)
        return {"page": page_name, "visual": visual_name, "config": data, "path": str(visual_path)}
    except Exception as exc:
        return {"error": f"Failed to read visual.json: {exc}"}


# ---------------------------------------------------------------------------
# Tool 4: apply_patch
# ---------------------------------------------------------------------------

@_tool
def apply_patch(build_dir: str, file_rel_path: str, patch: List[Dict]) -> Dict:
    """
    Apply RFC 6902 JSON Patch *patch* to the file at *build_dir*/*file_rel_path*.

    On success: writes the patched file and returns {"status": "ok", "path": ...}.
    On failure: restores original content and returns {"status": "error", "detail": ...}.
    """
    if not file_rel_path:
        return {"status": "error", "detail": "file_rel_path is empty"}

    target = Path(build_dir) / file_rel_path
    if not target.exists():
        return {"status": "error", "detail": f"File not found: {file_rel_path}"}

    original_bytes = target.read_bytes()
    try:
        try:
            import jsonpatch as _jp
        except ImportError:
            return {
                "status": "error",
                "detail": "jsonpatch library not installed; run: pip install jsonpatch",
            }

        doc = json.loads(original_bytes.decode("utf-8"))
        patched = _jp.apply_patch(doc, patch)
        target.write_text(json.dumps(patched, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"status": "ok", "path": file_rel_path}

    except Exception as exc:
        # Rollback
        target.write_bytes(original_bytes)
        return {"status": "error", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Tool 5: export_visual_png (stub)
# ---------------------------------------------------------------------------

@_tool
def export_page_png(build_dir: str, page_name: str, out_path: str = "") -> Dict:
    """
    Render a schematic PNG of a page's PBIR layout (positions, sizes, titles,
    colours) using the in-process Pillow renderer.  This is a layout render, not a
    Power BI engine render, but is sufficient for position/size/colour comparison
    against a source Tableau screenshot.

    Returns {"status": "ok", "path": <png>} or {"status": "not_available"/"error"}.
    """
    from app.core.preview.layout_preview import find_report_dir, render_page_preview

    report_dir = find_report_dir(Path(build_dir))
    if report_dir is None:
        return {"status": "error", "detail": "No .Report directory found in build_dir"}

    if out_path:
        target = Path(out_path)
    else:
        target = Path(build_dir) / "_preview" / f"{page_name}.png"

    png = render_page_preview(report_dir, page_name, target)
    if png is None:
        return {
            "status": "not_available",
            "reason": "Pillow not installed or page.json missing",
        }
    return {"status": "ok", "path": str(png)}


@_tool
def export_visual_png(build_dir: str, page_name: str = "", visual_name: str = "") -> Dict:
    """
    Export a page (or its containing page for a single visual) as a schematic PNG.

    Delegates to :func:`export_page_png`; ``visual_name`` is accepted for API
    compatibility but the whole page is rendered (single-visual cropping is a
    future refinement).
    """
    if not page_name:
        return {"status": "error", "detail": "page_name is required"}
    return export_page_png(build_dir, page_name)


# ---------------------------------------------------------------------------
# Tool 6: compare_visuals
# ---------------------------------------------------------------------------

@_tool
def compare_visuals(build_dir: str, baseline_dir: str) -> Dict:
    """
    Structural JSON diff of all visual.json files between *build_dir* and *baseline_dir*.

    Returns per-visual diffs on ``visualType`` and top-level config keys.
    (Pixel-level diff deferred until export_visual_png is available.)
    """
    diffs: List[Dict] = []

    def _collect_visuals(root: Path) -> Dict[str, Path]:
        """Return {relative_key: visual_path} for every visual.json under root."""
        result: Dict[str, Path] = {}
        for vp in root.rglob("visual.json"):
            try:
                parts = vp.relative_to(root).parts
                key = "/".join(parts)
                result[key] = vp
            except ValueError:
                pass
        return result

    base_visuals = _collect_visuals(Path(build_dir))
    ref_visuals = _collect_visuals(Path(baseline_dir)) if baseline_dir else {}

    for key, vpath in base_visuals.items():
        try:
            base_cfg = _load_json(vpath)
        except Exception:
            continue

        ref_cfg = None
        if key in ref_visuals:
            try:
                ref_cfg = _load_json(ref_visuals[key])
            except Exception:
                pass

        base_type = (base_cfg.get("visual") or {}).get("visualType", "unknown")
        ref_type = (ref_cfg.get("visual") or {}).get("visualType", "unknown") if ref_cfg else None

        entry: Dict[str, Any] = {"path": key, "visual_type": base_type}
        if ref_type and ref_type != base_type:
            entry["type_mismatch"] = {"build": base_type, "baseline": ref_type}
            diffs.append(entry)

    return {"diffs": diffs, "mode": "structural", "visual_count": len(base_visuals)}


# ---------------------------------------------------------------------------
# Tool 7: structural_compare
# ---------------------------------------------------------------------------

@_tool
def structural_compare(build_dir: str) -> Dict:
    """
    L1 structural inspection of the PBIP folder at *build_dir*.

    Returns page count, per-page visual count, table/measure counts from model.bim,
    and a list of any structural issues found.
    """
    bd = Path(build_dir)
    issues: List[str] = []

    report_dir = _find_report_dir(bd)
    if report_dir is None:
        return {
            "page_count": 0,
            "pages": [],
            "table_count": 0,
            "measure_count": 0,
            "issues": ["No .Report directory found — packager may not have run"],
        }

    pages_dir = report_dir / "definition" / "pages"
    pages = []
    if pages_dir.exists():
        for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
            visuals_dir = page_dir / "visuals"
            visual_names: List[str] = []
            if visuals_dir.exists():
                visual_names = [v.name for v in visuals_dir.iterdir() if v.is_dir()]
                for vn in visual_names:
                    vjson = visuals_dir / vn / "visual.json"
                    if not vjson.exists():
                        issues.append(f"Missing visual.json for visual '{vn}' on page '{page_dir.name}'")
                    else:
                        try:
                            cfg = _load_json(vjson)
                            if not cfg.get("name"):
                                issues.append(f"visual.json missing 'name' for '{vn}'")
                            vtype = (cfg.get("visual") or {}).get("visualType")
                            if not vtype:
                                issues.append(f"visual.json missing visualType for '{vn}'")
                        except Exception as exc:
                            issues.append(f"Invalid JSON in visual.json for '{vn}': {exc}")
            pages.append({"name": page_dir.name, "visual_count": len(visual_names)})
    else:
        issues.append("pages/ directory missing inside definition/")

    # Check semantic model (supports both TMSL model.bim and TMDL format)
    table_count = 0
    measure_count = 0
    semantic_format = "none"
    bim_path = _find_model_bim(bd)
    if bim_path:
        semantic_format = "tmsl"
        try:
            raw = _load_json(bim_path)
            tbls = raw.get("model", {}).get("tables", [])
            table_count = len(tbls)
            measure_count = sum(len(t.get("measures", [])) for t in tbls)
        except Exception as exc:
            issues.append(f"Failed to parse model.bim: {exc}")
    else:
        tmdl_path = _find_tmdl_database(bd)
        if tmdl_path:
            semantic_format = "tmdl"
            table_count = _tmdl_table_count(bd)
            # Measure count from TMDL requires text parsing — not implemented yet
            measure_count = 0
        else:
            issues.append("No semantic model found (model.bim or database.tmdl)")

    return {
        "page_count": len(pages),
        "pages": pages,
        "table_count": table_count,
        "measure_count": measure_count,
        "semantic_format": semantic_format,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# FastMCP app accessor (for SSE mounting in FastAPI)
# ---------------------------------------------------------------------------

def get_mcp_app():
    """Return the FastMCP ASGI app, or None if fastmcp is not installed."""
    if _mcp is None:
        return None
    try:
        return _mcp.get_asgi_app()
    except AttributeError:
        # Older FastMCP API
        return _mcp
