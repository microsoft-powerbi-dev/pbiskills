# docs/

## Component reference

`COMPONENT-GUIDE.md` is the detailed, per-component documentation index for
the whole repository: MCP servers, every `skills/claude-skills/` skill, the
`skills/report-lineage/` package, and the vendored/IDE-integration material.
It links out to `components/*.md`, one file per component group, each
documented module by module and function by function against the actual
source. Start there for exact function signatures, CLI flags, or file
formats; this page's table below is background reading instead.

## Background reading

Copied and generalized from the source repository this folder was extracted
from. These describe two different ways to drive an SSRS-to-Power-BI migration with an agentic
coding tool.

| File | Read this for |
| --- | --- |
| `minimal-backend-keep-set.md` | The smallest slice of a dedicated backend that can run the deterministic SSRS-to-PBIP conversion on its own: what to keep, what to delete, and the exact commands to run it. This is the keep-set to follow if you want `../mcp/` actually runnable beyond the standalone RDL generation server. |
| `devin-windsurf-microsoft-skills-integration.md` | The alternative to the above: doing the same migration with **no dedicated backend code at all**, driven entirely by Devin or Windsurf plus Microsoft's own Power BI/Fabric skills (the same four skills vendored at `../skills/vendor/microsoft-fabric/`). Includes a head-to-head comparison of the two approaches. |
| `verify-devin-fabric-skills.md` | The evidence log behind the claims in the Devin/Windsurf document: what was actually tested, what is documented-but-unverified, and what still needs confirming on a real seat. |
| `powerbi-mcp-engine.md` | Setting up a `powerbi_mcp`-style Report Studio engine: how a generation pipeline can use a local Power BI Modeling MCP server for refinement, the same pattern `../mcp/report_studio_server.py` and the "pragmatic hybrid" option in the Devin/Windsurf doc both describe. |
| `extract-engine-mvp-prompt-v2-polars.md` | The original design/requirements prompt behind `../skills/claude-skills/extract-engine/`. Compare against `../skills/claude-skills/extract-engine/SKILL.md` for what was actually implemented — two described features (a `benchmark` CLI command and an MCP server) were not. |

Read `minimal-backend-keep-set.md` and `devin-windsurf-microsoft-skills-integration.md`
together first: they are two answers to the same question (how do you get from SSRS `.rdl`
files to Power BI reports), and the right choice depends on whether deterministic, repeatable
conversion at scale matters more than having zero custom code to maintain.
