# SSRS to Power BI Skills and MCP Toolkit

A standalone folder of reusable skills and MCP server reference material for
SSRS-to-Power-BI work, copied out of a source repository so it can be
used, shared, or vendored into another project on its own. The source
repository is unchanged; everything here is a copy.

## What is in this folder

```
skills/     Reusable skills: RDL generation, SSRS report creation, report-estate
            lineage, IDE-specific reference variants, and four vendored
            Power BI report skills from Microsoft. Fully documented in
            skills/ASSESSMENT.md.
mcp/        Three MCP servers. See mcp/README.md: one (RDL generation) runs
            standalone as-is; the other two are reference copies that need
            backend code not included here to actually run.
docs/       Background reading: two different ways to drive an SSRS-to-Power-BI
            migration with an agentic coding tool, plus how to wire skills up
            with Devin, Windsurf, or Microsoft's own Fabric skills.
```

## Quick start

**Using a skill in Claude Code or Cowork**: point the tool at a `SKILL.md`
file under `skills/claude-skills/<name>/` or `skills/vendor/microsoft-fabric/<name>/`.
Each one is self-contained: read its `SKILL.md` first, then its `references/`
and `examples/` as needed.

**Using a skill in Devin, Windsurf, Cursor, or Copilot**: these tools don't
read the Anthropic SKILL.md format. Use `skills/ide-references/` instead:

- `skills/ide-references/vendor-neutral/`: plain markdown playbooks, usable
  anywhere.
- `skills/ide-references/devin/`: Devin knowledge-base entries.
- `skills/ide-references/cursor/.cursorrules`
- `skills/ide-references/copilot/copilot-instructions.md`

For Devin specifically, `docs/devin-windsurf-microsoft-skills-integration.md`
also documents how Devin vendors a `SKILL.md` (copy the whole skill folder
into `.agents/skills/<name>/` at the target repo's root).

**Understanding an SSRS estate (lineage, duplicates, search)**: this is a
runnable Python package, not just documentation. See
`skills/report-lineage/README.md`:

```bash
cd skills/report-lineage
pip install -e .
python -m reportlineage scan /path/to/an/estate --out ./out
```

**Generating or reviewing RDL XML by hand**: `skills/claude-skills/rdl-generation/`
ships a dependency-free Python builder and validator alongside the reference
docs; both run with a stock Python 3.9+ install, no install step needed:

```bash
python skills/claude-skills/rdl-generation/scripts/validate_rdl.py your-report.rdl
```

The same folder also ships an MCP server (`scripts/mcp_server.py`) over
those two scripts, runnable as-is; see the MCP servers entry below.

**Designing, authoring, or publishing a Power BI (.pbip) report**: once a
report is or is becoming a `.pbip`/PBIR project, use the four vendored
Microsoft skills at `skills/vendor/microsoft-fabric/`. They need external
tooling (Node.js CLIs, `az` CLI) documented in their own `README.md`, not
included here since they are third-party installable tools, not files.

**MCP servers**: read `mcp/README.md` first. Of the three servers there,
`rdl_generation_server.py` runs standalone as-is (`python mcp/rdl_generation_server.py`,
`pip install fastmcp` optional). The other two,
`report_studio_server.py` and `pbi_refine_server.py`, are reference copies:
they need backend code not included here to actually run. Either work from
the source repository directly for those two, or follow
`docs/minimal-backend-keep-set.md`'s keep-set to bring over the backend
code they depend on.

## Where each piece came from

Everything under `skills/` was built directly in this folder's source
repository except `skills/vendor/microsoft-fabric/`, which is a verbatim,
MIT-licensed copy from Microsoft's public `microsoft/skills-for-fabric`
repository (see that folder's own `README.md` for the exact provenance and
license).

`mcp/report_studio_server.py` and `mcp/pbi_refine_server.py` are reference
copies from the source repository's own MCP server folder, with a couple of
identifier names generalized for this public copy (see `mcp/README.md`).
`mcp/rdl_generation_server.py` is a thin launcher written for this folder;
see `skills/claude-skills/rdl-generation/SKILL.md` for the real server it
wraps. The files under `docs/` are generalized from the source repository's
own background reading, with names and product-specific references
stripped out so they stand on their own.

For the full capability assessment behind all of this, including what parts
of the source repository were reused, extracted, or left alone, read
`skills/ASSESSMENT.md`.

## Keeping this in sync

This folder is a snapshot, not a live link. If the source repository's
`skills/` folder changes, re-copy it here to pick up the changes; nothing
here auto-updates.
