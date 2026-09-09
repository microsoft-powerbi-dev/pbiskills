# Component guide

Full-repository documentation index. The top-level `README.md`, `mcp/README.md`,
`docs/README.md` and `skills/ASSESSMENT.md` already give a good overview and
capability assessment; the files under `docs/components/` linked below go one
level deeper, module by module and function by function, verified directly
against source rather than paraphrased from existing docs.

Read this file to find which doc covers a given file or folder. Each linked
doc is self-contained; you don't need to read this index's summaries first.

## MCP servers

| Doc | Covers |
| --- | --- |
| [`components/mcp-servers.md`](components/mcp-servers.md) | All four files under `mcp/`: `rdl_generation_server.py`, `sqlserver_schema_server.py` (both standalone, runnable as-is), `report_studio_server.py`, `pbi_refine_server.py` (reference copies, only partially runnable — the doc lists the exact missing backend imports per tool). Full parameter/return tables for every tool, verified against the real servers they launch (`skills/claude-skills/rdl-generation/scripts/mcp_server.py`, `skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py`), install/env-var steps, worked examples using this repo's actual `.mcp.json`, and the guardrail/no-egress mechanisms each enforces. |

## `skills/claude-skills/` (built in this repo)

| Skill | Doc | Covers |
| --- | --- | --- |
| `extract-engine` | Its own new [`SKILL.md`](../skills/claude-skills/extract-engine/SKILL.md) + [`references/`](../skills/claude-skills/extract-engine/references/) (5 files) | This skill had **no documentation at all** before this pass — code, tests, and an example workbook only. Now covers the 6 real CLI commands, the Excel-to-config loading flow, the two execution backends (Polars vs. generated SQL Server views), write guardrails, and checkpoint/resume. **Flags two gaps between the design doc and the implementation:** the `extract benchmark` command and an `extract_engine_authoring_mcp.py` MCP server, both referenced in `cli.py`'s own docstring and the design doc, do not exist in the codebase — this skill is CLI-only today. |
| `rdl-generation` | [`components/rdl-generation.md`](components/rdl-generation.md) | Deep dive beyond the skill's own `SKILL.md`: full API reference for `rdl_builder.py` and `validate_rdl.py` (all 10 validation checks), the reconstructed JSON spec schema with a worked example, and per-file summaries of all 6 reference docs and all 5 example `.rdl` files. |
| `sql-server-schema` | [`components/sql-server-schema.md`](components/sql-server-schema.md) | Deep dive: the digest-as-seam architecture, full CLI reference (5 subcommands), all 14 MCP tools with exact signatures, the four-layer read-only guardrail mechanism, the schema digest JSON shape, and the skill-pack generator's classification heuristics. **Flags a real discrepancy:** `mssql_list_relationships(infer=True)` does not actually perform inference — it silently ignores the flag and returns a note pointing at `mssql_build_schema_digest` instead. |
| `ssrs-report-creation` | [`components/ssrs-report-creation.md`](components/ssrs-report-creation.md) | This skill is pure workflow/process guidance (no scripts). Covers the 11-step authoring workflow, how it divides responsibility with `rdl-generation` (process vs. mechanical build/validate), and per-file summaries of all reference docs and example templates. |

## `skills/report-lineage/` (standalone Python package)

| Doc | Covers |
| --- | --- |
| [`components/report-lineage.md`](components/report-lineage.md) | Module-by-module deep dive on the `reportlineage` package: the `EstateGraph` data model, the `table_signature()` join mechanism, `builder.py`'s per-source extractors, every parser (RDL/DTSX/conmgr/SQL) and scanner (filesystem/git/report-server), the duplicate-detection weights and clustering algorithm, the inverted-index search, every export format, and every CLI subcommand. **Flags two things the source-repository version has that this extracted package does not:** the `fabric_readiness`/`wave_plan`/`duplicates`/`report_fingerprints` projection layer described in `skills/ASSESSMENT.md` §2.4 was not carried over, and the Report Server REST scanner has no SSRF/URL guard (the source backend's `url_guard.py` was not ported). |

## Vendored and IDE-integration material

| Doc | Covers |
| --- | --- |
| [`components/vendor-and-ide-references.md`](components/vendor-and-ide-references.md) | Two directories in one file. **Part 1**: the four vendored Microsoft `skills/vendor/microsoft-fabric/` skills (planning → design → authoring → management pipeline), provenance/license, and exact external tooling each needs (Node CLIs, `az`/`jq`). **Part 2**: `skills/ide-references/` — confirms the vendor-neutral playbooks are genuine restatements of the `claude-skills/*/SKILL.md` files, and explains how the Devin `.knowledge.md` files, `.cursorrules`, and `copilot-instructions.md` differ and get wired into their respective tools. Notes that no doc in this repo actually specifies how Devin's `.knowledge.md` files get loaded (distinct from the `.agents/skills/<name>/SKILL.md` vendoring mechanism, which only applies to Part 1). |

## Not re-documented here

`docs/minimal-backend-keep-set.md`, `docs/devin-windsurf-microsoft-skills-integration.md`,
`docs/verify-devin-fabric-skills.md`, `docs/powerbi-mcp-engine.md`, and
`docs/extract-engine-mvp-prompt-v2-polars.md` are background/design reading, not
component reference material — see `docs/README.md` for what each covers.
`skills/ASSESSMENT.md` remains the authoritative capability assessment against
the source repository this content was extracted from.

## Findings worth acting on

Three real discrepancies surfaced while writing this documentation (all
verified against source, none fixed as part of this pass):

1. **`mssql_list_relationships(infer=True)` is a no-op for inference** — see
   `components/sql-server-schema.md`. Callers wanting inferred (non-FK)
   relationships must use `mssql_build_schema_digest` instead.
2. **`extract-engine`'s CLI docstring and design doc describe two things that
   don't exist**: an `extract benchmark` command and an
   `extract_engine_authoring_mcp.py` MCP server. Neither is implemented.
3. **The `report-lineage` Report Server scanner has no SSRF guard**, unlike
   the source repository's `report_server/url_guard.py`. Anyone pointing
   `reportlineage.scanners.report_server` at a user-suppliable URL should add
   one before treating it as safe against SSRF the way the source backend is.
