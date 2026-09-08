# SSRS to Power BI with Devin/Windsurf + Microsoft skills (no source backend)

**Purpose.** The alternative to
[`minimal-backend-keep-set.md`](./minimal-backend-keep-set.md): build Power BI reports using
**only** Microsoft's Power BI MCP + authoring skills + Fabric skills, driven by **Devin** or
**Windsurf** CLI, **zero source-backend code, nothing to maintain**. Target stays local PBIP
files. Evidence for every external fact is in
[`verify-devin-fabric-skills.md`](./verify-devin-fabric-skills.md); this document is the setup
and workflow.

**Tag legend:** `DOCS` = read in live docs, `FILE` = read from the installed skills on disk,
`TESTED` = run on this machine, `VERIFY` = plausible but must be confirmed on a real Devin/Windsurf
seat before relying on it.

---

## The one thing to decide first (honest tradeoff)

Microsoft's skills are **report and model authoring** skills. **None of them parses SSRS RDL.**
`FILE` The four Power BI skills (`powerbi-report-planning/-design/-authoring/-management`) plus
`semantic-model-authoring` create/modify PBIR + TMDL, they have no `.rdl` reader, no `.dtsx`
reader, and no paginated-RDL re-point.

Consequence:

- **Interactive PBIP:** feasible, but the RDL-to-report *translation* becomes the **agent's
  reasoning task**, Devin/Windsurf reads the `.rdl` XML, infers datasets/parameters/tablix/chart,
  and then authors the model + visuals with the skills. That is **non-deterministic** (LLM
  interpretation), unlike a deterministic RDL parser in a dedicated backend.
- **Paginated (RDL to paginated):** **no equivalent skill exists.** `DOCS`/`FILE` There is
  nothing in skills-for-fabric that re-points a paginated `.rdl`. Devin would hand-edit the RDL,
  or this path stays on a dedicated backend. **Treat paginated as a gap in the no-backend
  approach.**

So this approach primarily delivers the **interactive** path. Choose it when zero custom-code
maintenance and prompt-driven authoring matter more than deterministic, repeatable RDL conversion
at scale.

---

## Components (all external, nothing custom)

**npm binaries** `TESTED` (installed and run on this machine; versions live as of 2026-07-23):

| Package | Binary | Version | Platform | Role |
|---|---|---|---|---|
| `@microsoft/powerbi-modeling-mcp` | `powerbi-modeling-mcp` | `0.5.0-beta.11` | win/mac/linux `DOCS` | TMDL/semantic-model authoring MCP (file mode, no Desktop) |
| `@microsoft/powerbi-report-authoring-cli` | `powerbi-report-author` | `0.1.4` | cross-platform `DOCS` | PBIR catalog/formatting metadata, expr codec, **validate** |
| `@microsoft/powerbi-desktop-bridge-cli` | `powerbi-desktop` | `0.1.2` | **Windows + Desktop only** | reload/screenshot verification, **skip for local/headless** |

**Skills to vendor** `FILE` (all `metadata.version: 0.1.0`; local marketplace clone is
skills-for-fabric v0.3.8, live GitHub is v0.3.9 @ `d79f339`):

| Skill | Keep for a local, deterministic-free migration? |
|---|---|
| `powerbi-report-planning` | yes, requirements to spec to build orchestration |
| `powerbi-report-design` | yes, design brief |
| `powerbi-report-authoring` | yes, owns all PBIR JSON |
| `semantic-model-authoring` | yes, TMDL model via the Modeling MCP |
| `powerbi-report-management` | optional, Fabric transport (only if publishing, out of scope here) |
| Fabric skills (`fabric-authoring`/`-consumption`) | optional, include per request, but idle for local-only output |

**MCP servers:** `powerbi-modeling-mcp` (stdio) is required. The FabricIQ HTTP MCP and Fabric REST
are only needed if publishing to Fabric, not for a local-only target.

---

## Devin setup

1. **Vendor the skills** `DOCS`, Devin has no marketplace; copy each `SKILL.md` folder into
   `.agents/skills/<name>/SKILL.md` at the repo root (the one path both Devin cloud and Devin CLI
   scan). Pin the source commit (`d79f339`, v0.3.9).
2. **Install the npm binaries** in Devin's environment: `npm i -g
   @microsoft/powerbi-report-authoring-cli@latest @microsoft/powerbi-modeling-mcp@latest`
   (Node >= 20). Skip the Desktop bridge for headless Linux.
3. **Wire the MCP server + secrets separately** `DOCS`/`VERIFY`, Devin's skill loader reads only
   `SKILL.md`; it ignores the plugins' `.mcp.json`. Register `powerbi-modeling-mcp` (stdio,
   `npx -y @microsoft/powerbi-modeling-mcp@latest --start`) through Devin's own MCP configuration.
   For a local-only target no `FABRIC_*` secrets are needed. `VERIFY` on a real Devin seat.
4. **Invoke:** `@skills:powerbi-report-planning` (cloud) or `/powerbi-report-planning` (CLI), or
   let Devin auto-select on the skill `description`.

## Windsurf CLI setup

`VERIFY`, Windsurf follows the same open Agent Skills standard, and Devin CLI already lists
`.windsurf/skills/` as a discovery path (`DOCS`). Confirm current Windsurf docs before relying on
specifics.

1. **Vendor the skills** into `.windsurf/skills/<name>/SKILL.md` (or `.agents/skills/`, both are
   recognized by the shared standard).
2. **Install the same npm binaries.**
3. **Wire the Modeling MCP** via Windsurf/Cascade MCP config (commonly
   `~/.codeium/windsurf/mcp_config.json`) with the stdio `powerbi-modeling-mcp` entry. `VERIFY`
   the exact path/format against the Windsurf version in use.
4. **Invoke** the skill by name in Cascade, or let it auto-select on the description.

---

## Workflow: SSRS `.rdl` to interactive PBIP (no source backend)

Give the agent the `.rdl` file and this sequence:

1. **Plan**, `powerbi-report-planning` reads the `.rdl` and produces a spec: datasets (for
   example `SELECT Region, Product, Amount FROM dbo.Sales`), parameters (for example `Year`), and
   the visuals implied by each `Tablix`/`Chart`.
2. **Design**, `powerbi-report-design` emits a design brief (layout, visual choices) from the
   spec. It never writes PBIR.
3. **Model**, `semantic-model-authoring` + `powerbi-modeling-mcp` author the TMDL: a table per
   RDL dataset, and measures for RDL aggregates (`=Sum(Fields!Amount.Value)` becomes a `Sum`
   measure). The MCP operates on the local PBIP `definition/` folder, **no Desktop**.
4. **Author**, `powerbi-report-authoring` writes the PBIR (pages, visuals mapped from the
   tablix/chart). It uses `powerbi-report-author catalog`/`formatting` for **authoritative** enum
   and property names (`TESTED`: 57 visual types / 15 VCOs from the bundled schema, the skill is
   forbidden from guessing) and `powerbi-report-author validate` to check the output.
5. **Result**, a local PBIP folder; open it in Power BI Desktop. No Fabric, no publish.

**Verification signal you already have** `TESTED`, `powerbi-report-author validate` runs fully
offline/no-Desktop and returns structured diagnostics. Use it as the acceptance gate for whatever
the skills produce.

**Paginated:** not covered, see the tradeoff section. Keep paginated on a dedicated deterministic
backend (see the sibling document) or hand-edit RDL.

---

## Head to head

| Dimension | A: minimal dedicated backend | B: Devin/Windsurf + MS skills |
|---|---|---|
| RDL parsing | **Deterministic** (a dedicated `rdl_parser`) | Agent interprets RDL (**non-deterministic**) |
| Interactive PBIP | Yes, repeatable | Yes, per-run variance |
| Paginated (RDL re-point) | **Yes** (a dedicated paginated module) | **No equivalent** (gap) |
| Custom code to maintain | The stripped CLI subset | **None** (all external skills/CLIs) |
| Runs headless on Linux | Yes (pure Python) | Yes for authoring; Desktop verify is Windows-only |
| Setup cost | venv + trimmed deps | vendor skills + npm + MCP/secrets wiring |
| Determinism at scale (many reports) | **Strong** | Weaker (LLM in the loop each report) |
| Zero-vendor-lock | Custom code | Microsoft-native tooling |
| Cross-tool validation | `powerbi-report-author validate` | `powerbi-report-author validate` |

---

## Recommendation

- **Bulk SSRS migration (many reports, both interactive + paginated):** prefer **A**. The
  deterministic RDL parser and the built-in paginated path are the differentiators; the CLI subset
  is small and gives an agentic tool a repeatable, reviewable conversion.
- **Net-new / prompt-driven reports, or a strict no-custom-code mandate:** use **B**. Accept that
  RDL interpretation is the agent's job (validate every output) and that paginated is out.
- **Pragmatic hybrid:** A for the deterministic RDL-to-PBIP + paginated conversion, then let
  Devin/Windsurf + the Microsoft skills **refine/enhance** the generated PBIP (extra visuals,
  polish) using `powerbi-modeling-mcp` file mode. This is the same shape a `powerbi_mcp`
  refinement engine already uses in the source backend this repository draws from, best of both,
  still local, still no Fabric.

---

## Verification (prove approach B before committing to it)

1. Install the two cross-platform npm binaries; `powerbi-report-author doctor` returns `ok`.
2. Vendor the four skills; confirm the agent lists/loads them (`@skills:...` / `/...`).
3. Run the workflow above on a sample `.rdl` fixture; `powerbi-report-author validate` the output,
   0 errors.
4. Diff the skill-authored PBIP against a deterministic-backend baseline (approach A's output),
   every difference is either a skill gap or a backend gap. This is the honest apples-to-apples
   check.
5. `VERIFY` the Devin MCP/secrets wiring and the Windsurf MCP config path on the actual seats,
   these are the two items not confirmable ahead of time.
