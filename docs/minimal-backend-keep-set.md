# Minimal backend keep-set for SSRS to Power BI (local PBIP)

**Purpose.** Define the smallest slice of a source SSRS-to-Power-BI conversion backend needed to
convert SSRS `.rdl` reports into Power BI, both **SSRS to Interactive PBIP** and **SSRS to
Paginated**, producing **local PBIP/RDL files** (no Fabric, no Power BI Service, no AI). This is the
report to hand to an agentic coding tool such as Devin: what to keep, what to delete, on a
dedicated branch, plus the exact commands to run.

**Scope (confirmed).** SSRS to Interactive PBIP + SSRS to Paginated, output = local files, no
Fabric, no cloud publish, no AI, CLI-only (no web app, no API, no auth, no database).

**Out of scope (excluded on purpose).** SSIS lineage/estate, Report Studio (prompt to report),
Fabric publish, the FastAPI backend, the React frontend, auth, DB, cost/governance,
complexity/landscape, agentic refinement, and all AI providers.

---

## TL;DR

The two conversion paths are **cleanly decoupled** from the rest of the product in the source
backend this was derived from. Verified `TESTED`:

- The conversion keep-set (`pipeline`, `parser/*`, `generator/*`, `translator/*`, `pbip/*`,
  `paginated/*`) has **zero imports** of `lineage`, `report_studio`, `complexity`, `cost`,
  `fabric`, `preview`, `ai`, `mcp`, or any legacy source-server client (grep across those modules
  finds no matches).
- The CLI entrypoint (`cli.py`, `config.py`, `models/job.py`) has **no** imports of `app.db`,
  `app.auth`, `sqlalchemy`, or `app.api`.

So a minimal build is a **pure-Python CLI** that runs `ConversionPipeline` (interactive) and
`run_paginated` (paginated). Everything else is deletable without touching the conversion code.
Both paths are already **deterministic and zero-network** (`use_ai_dax=False`).

---

## Minimal component set: KEEP

| Component | Path | Why |
|---|---|---|
| Pipeline orchestrator | `backend/app/core/pipeline.py` | Runs Steps A-F for interactive PBIP |
| Source/RDL parser | `backend/app/core/parser/` (`rdl_parser`, `source_parser`, `sql_tables`, `connect_string`) | `.rdl` to `IRWorkbook`; native-SQL M-query |
| Translator | `backend/app/core/translator/` (`calc_translator`, `visual_mapper`, `ai_dax_converter`*) | RDL/VB expr to DAX (rule-based); visual mapping |
| Generators | `backend/app/core/generator/` (`data_model_builder`, `tmdl_generator`, `semantic_model`, `report`, `theme`, `packager`, `column_validator`, `sample_data_generator`, `naming`) | Steps B-F: model, PBIR, theme, package |
| PBIP fixer | `backend/app/core/pbip/` (`fixer`, `pbir_fixes`) | Feb 2026 PBIP schema fixes |
| Paginated | `backend/app/core/paginated/` (`compatibility`, `transformer`, `pipeline`) | SSRS to paginated RDL re-point + report |
| IR model | `shared/ir_models.py` | The `IRWorkbook` lingua franca |
| CLI | `backend/app/cli.py` (trimmed, see below) | The only entry point a minimal build needs |
| Config | `backend/app/config.py` | Settings; run with `ENVIRONMENT=development` |
| Job state | `backend/app/models/job.py` | `JobState` used by the CLI + pipeline |
| Logging | `backend/app/utils/logging.py` | `JobLogger` |
| Base theme | `templates/base_themes/CY25SU12.json` | `packager` copies it into every report |

\* `ai_dax_converter` stays but is **dormant**: the keep-set has no static import of `core/ai`, so
it resolves providers lazily and is skipped when `use_ai_dax=False`. Keeping `translator/` whole
avoids editing `pipeline.py`'s imports.

**CLI trim.** Keep the `report-studio from-rdl` (interactive + `--paginated`), `fix-pbip`, and
`compare-baseline` subcommands. Remove `report-studio generate` (needs the Report Studio engines)
and `report-studio publish-fabric` (needs `core/fabric`), both from `cli.py`'s `main()` and their
`_cmd_*` handlers.

---

## What to REMOVE

All cleanly separable (see the coupling evidence in TL;DR). Group and delete:

| Remove | Path | Reason |
|---|---|---|
| Web API | `backend/app/main.py`, `backend/app/api/` | FastAPI app + all routers, not used by the CLI |
| Auth | `backend/app/auth/` | JWT/Argon2, no server, no users |
| Database | `backend/app/db/`, `backend/alembic/`, `alembic.ini` | SQLAlchemy/migrations, no persistence needed |
| Report Studio | `backend/app/core/report_studio/` | prompt to report + all engines (out of scope) |
| Fabric | `backend/app/core/fabric/` | cloud publish (local-only target) |
| Lineage | `backend/app/core/lineage/`, `shared/lineage_models.py` (see note below) | SSIS estate (out of scope) |
| Complexity | `backend/app/core/complexity/` | portfolio/landscape analysis |
| Cost/governance | `backend/app/core/cost/` | AI spend guardrails |
| AI providers | `backend/app/core/ai/` | no AI |
| Preview | `backend/app/core/preview/` | Report Studio preview (verify not imported) |
| Refinement | `backend/app/core/refine_agent.py` (see note below), `backend/app/mcp/` | agentic PBIP refinement loop |
| Frontend | `frontend/` | React/MUI UI |
| Prompts | `prompts/` | design + refine prompts (no AI) |
| Heavy utils | `backend/app/utils/` `crypto`, `cloud_storage`, `keyvault`, `audit` | cloud/secret plumbing |
| Legacy source-server client | any legacy Tableau/Server client module + lazy imports of it in kept endpoints | dead source path |

Note: `shared/lineage_models.py` and `core/refine_agent.py`, confirm they aren't imported by the
keep-set before deleting (the pipeline imports IR types from `shared/ir_models.py`, not
`lineage_models`).

**Trimmed dependencies.** Derive a lean `requirements.txt` from the keep-set's import closure:
drop `fastapi`, `uvicorn`, `starlette`, `sqlalchemy`, `alembic`, `argon2-cffi`, `python-jose`,
`authlib`, `azure-*`, `anthropic`, `openai`, `google-*`, `langchain*`, `mcp`, `fastmcp*`, `msal*`,
`keyring`, `slowapi`. Likely-needed core: `pydantic`, `pydantic-settings`, `python-dotenv`,
`pandas`, `numpy`, `openpyxl`, `sqlglot`, `Pillow`. Verify by importing `app.cli` in the trimmed
venv and running the tests below.

**Tests to keep** (drop the rest): `test_rdl_parser`, `test_rdl_roundtrip`, `test_paginated`,
`test_report_binding`, `test_pbir_ms_validation_fixes`, `test_tmdl_measures`, `test_column_validator`,
`test_sample_data_generator`, plus a small set of `.rdl` test fixtures.

---

## Branch strategy (recommended, not yet executed)

Keep the full product intact; give the agentic coding tool a focused branch.

```bash
# Branch from the commit that already carries the PBIR generator fixes
# (the generator/report.py, theme.py, packager.py changes + test_pbir_ms_validation_fixes.py).
git checkout -b minimal-backend        # from that branch/commit, NOT a stale main
# ... apply the REMOVE list above as deletions on this branch ...
git commit -m "Minimal SSRS->PBIP CLI subset (strip API/auth/db/report-studio/fabric/...)"
```

- Do the deletions **only** on the dedicated branch; the main branch stays whole.
- Rationale: the agentic tool gets a small, single-purpose repo (faster clone, fewer deps, clear
  surface), and the deletions never risk the full product.
- Keep the repository's own contributor guide, this document plus the companion evidence log
  (see the sibling document below), `.gitignore`, and a trimmed `requirements.txt`.

---

## Runbook

```bash
# 1. Environment (Linux or Windows; prefer Python 3.11/3.12, avoid 3.14)
python -m venv .venv && . .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt           # the trimmed file

# 2. Interactive PBIP (deterministic, no network, no AI)
cd backend
export PYTHONPATH="$(pwd)/..:$(pwd)"; export ENVIRONMENT=development
python -m app.cli report-studio from-rdl --rdl tests/fixtures/sales_by_region.rdl --out /out/build
#   -> /out/build/<name>.zip  (a PBIP: .Report + .SemanticModel)

# 3. Paginated (RDL re-point + compatibility report)
python -m app.cli report-studio from-rdl --rdl <report.rdl> --out /out/pg --paginated
#   -> re-pointed .paginated.rdl + compatibility_report.md

# 4. (Optional) validate the interactive PBIP with Microsoft's cross-platform CLI
npm i -g @microsoft/powerbi-report-authoring-cli@latest
unzip -o /out/build/*.zip -d /out/build/x
powerbi-report-author validate /out/build/x/*/*.pbip --pretty   # expect 0 errors
```

**Windows MAX_PATH caveat** `TESTED`: use a short `--out` (for example `C:\out`). Deep output
paths overflow the 260-character limit and the TMDL date-table file write fails; a short root
avoids it.

---

## Verification (definition of done)

1. `pip install` of the trimmed requirements succeeds and `python -c "import app.cli"` works.
2. The kept tests pass: `pytest backend/tests -q` (only the conversion tests remain).
3. `from-rdl` on `sales_by_region.rdl` emits a PBIP ZIP; `powerbi-report-author validate` reports
   **0 errors** (the generator fixes for `nativeQueryRef`, theme registration, `grid`, and
   `.platform` are already in place, see the evidence log referenced below).
4. `--paginated` on a sample RDL emits a re-pointed `.paginated.rdl` + `compatibility_report.md`.
5. The PBIP opens in Power BI Desktop without repair prompts.

---

## Open items

- Confirm `core/preview`, `shared/lineage_models.py`, and `core/refine_agent.py` are not imported
  by the keep-set before deleting (static import check at strip time).
- Produce the trimmed `requirements.txt` and pin it.
- Decide whether the semantic model should be **TMDL** (default) or **TMSL**, both generators are
  kept; `SEMANTIC_MODEL_FORMAT` selects.

See the sibling document **`docs/devin-windsurf-microsoft-skills-integration.md`** for the
alternative that uses **no source-backend code**, Microsoft's Power BI MCP + authoring skills +
Fabric skills driven by Devin/Windsurf, and a head-to-head comparison.
