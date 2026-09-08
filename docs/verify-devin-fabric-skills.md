# Verification: Devin.ai + `microsoft/skills-for-fabric` Power BI authoring

**What this is.** A research finding plus an implementation plan, answering one question against
live sources (not training data): *Can Devin.ai be driven to generate a Power BI report
(PBIP/PBIR + TMDL) using Microsoft's `skills-for-fabric` Power BI authoring skills, and how does
that fit a dedicated SSRS-to-Power-BI backend?* Where a claim could be tested on this machine, it
was, those rows are tagged `TESTED`. The original executable spike playbook that seeded this work
is preserved in git history; this document supersedes it with the actual answers.

---

## Verdict

> **GO-WITH-CAVEATS.**
>
> The file-generation half of the toolchain is real, cross-platform, and works with **no Power BI
> Desktop**, verified end to end on this machine: the Microsoft PBIR CLI installed cleanly, and
> Microsoft's own `powerbi-report-author validate` ran against a PBIP that a dedicated backend
> generated, producing structured diagnostics with zero Desktop involvement. Devin *can* run the
> skills, but **only by vendoring the `SKILL.md` folders** (Devin has no marketplace) **and
> separately wiring the MCP servers + Fabric secrets that Devin's skill loader ignores.** The
> single fact that decides the caveat: **the verification/screenshot loop needs Power BI Desktop,
> which is Windows-only and absent from a Devin Linux workspace.**
>
> For a dedicated backend specifically the recommendation is narrower, see
> [9](#9-recommendation). It is not necessary for Devin to run the Microsoft skills; what is
> needed is the Microsoft **PBIR validation CLI** as a post-generation gate, because it already
> found five real defect classes in the tested output.

---

## Metadata and evidence tags

| | |
|---|---|
| Verified on | 2026-07-23 |
| Host platform | Windows 11 (`MINGW64_NT-10.0-26200`), **not** the Linux that Devin cloud uses |
| Node / npm | `v22.14.0` / `11.14.1` |
| Python | `3.13.2` (system; the source repository prefers 3.11/3.12, see [Blockers](#8-blockers)) |
| Azure CLI | `2.86.0` present |
| `skills-for-fabric` (live GitHub) | **v0.3.9**, `main` @ `d79f3393cab658d0b12d7215b7df1a069a2463a5` (2026-07-23) |
| `skills-for-fabric` (installed here) | **v0.3.8**, plugin commit `16903b068f9a7e0180d701f158465f53cd2110ba` |
| Repo under test | a dedicated SSRS-to-Power-BI backend and CLI (name withheld from this public copy) |

**Tag legend**, every factual row below carries one:
`DOCS` read in live documentation, `FILE` read from disk in this session, `TESTED` run and
observed the result, `INFERRED` reasoned, not directly confirmed (with what would confirm it).

---

## 1. Devin's skill mechanism

Devin ships **two products with different on-disk conventions**, do not conflate them.

**Devin cloud** (`https://docs.devin.ai/product-guides/skills`)
- `DOCS` Discovery is repo-vendored, scanning eight locations of the form
  `<dir>/skills/<name>/SKILL.md`: `.agents/skills/` (recommended), `.devin/`, `.github/`,
  `.claude/`, `.cursor/`, `.codex/`, `.cognition/`, `.windsurf/`.
- `DOCS` **No marketplace / plugin installer, vendoring is the only route.** Devin follows the
  open Agent Skills standard so the same files work across tools.
- `DOCS` Frontmatter parsed: `name` (defaults to dir name), `description` (routing text),
  `allowed-tools`, `argument-hint`, `triggers` (for example `[user]` disables auto-activation).
- `DOCS` Invocation: automatic (by `description` relevance) **or** explicit `@skills:name` (with
  `$ARGUMENTS`/`$0` substitution).

**Devin for Terminal / CLI** (`https://docs.devin.ai/cli/extensibility/skills/overview`, the
redirect target of `cli.devin.ai`)
- `DOCS` Adds **global** locations to the project ones: `~/.config/devin/skills/<name>/SKILL.md`,
  `~/.codeium/<channel>/skills/...`; on Windows `%APPDATA%\devin\skills\<name>\SKILL.md`.
- `DOCS` Invocation: explicit `/name` slash command, plus autonomous use (on by default).

**Agent Skills spec** (`https://agentskills.io/specification`)
- `DOCS` `skill-name/SKILL.md` + optional `scripts/`, `references/`, `assets/`. Required
  frontmatter: `name`, `description`. **There is no top-level `version` field**, version lives
  under `metadata` (which is exactly how Microsoft encodes it, see [2](#2-the-microsoft-skills-inventory)).

**The load-bearing gap.** `DOCS`+`FILE` Devin's skill loader reads *only* `SKILL.md`. It does
**not** read Claude Code's `plugin.json` / `.mcp.json` / `marketplace.json`. So the MCP servers
those Microsoft plugins declare, and the `FABRIC_*` / token secrets they need, must be configured
through Devin's **separate** MCP and secrets features. `UNCERTAIN` The Devin skills docs do not
cover MCP/secrets; those pages were not confirmed in this pass.

---

## 2. The Microsoft skills, inventory

`DOCS` The live repo has **both** a top-level `skills/` tree and a `plugins/<plugin>/skills/`
tree; plugins are `fabric-skills` (complete), `fabric-authoring`, `fabric-consumption`,
`fabric-operations`, `powerbi-authoring`. `FILE` In this session both trees are real directories
(not symlinks) under a cloned marketplace.

`FILE` **What is actually enabled in this Claude Code session** (`installed_plugins.json`): only
`fabric-authoring`, `fabric-consumption`, `fabric-skills` (all v0.3.8). The `powerbi-authoring`
plugin is present on disk but **not activated**, so `semantic-model-authoring` and `fabriciq` are
live here, but the four `powerbi-report-*` skills are on-disk-only.

`FILE` The Power BI authoring skills (all `metadata.version: 0.1.0`):

| Skill | Role | Writes PBIR? |
|---|---|---|
| `powerbi-report-planning` | requirements to spec to approve to build to validate to publish orchestration | no, routes |
| `powerbi-report-design` | visual design guidance; produces a `Design Brief:` | no |
| `powerbi-report-authoring` | owns all PBIR JSON (`definition.pbir`, `report.json`, `pages.json`, `page.json`, `visual.json`, `version.json`); validate; Desktop reload/screenshot | **yes** |
| `powerbi-report-management` | report item CRUD + PBIR transport to/from Fabric via `az rest` | no, transport |
| `semantic-model-authoring` | TMDL/PBIP model create/edit/deploy; Tier-1 tool `powerbi-modeling-mcp` | model side |

`FILE` **Routing DAG:** planning to design to authoring (files) to management (transport);
authoring delegates model edits to semantic-model-authoring / `powerbi-modeling-mcp`.
`FILE` **Documented hard stop:** if neither the Modeling MCP nor a PBIP is available, "the agent
cannot author the model."

`FILE` **External tooling the skills shell out to** (package name is not the binary name):

| Binary | npm package | Latest | os/cpu | Role |
|---|---|---|---|---|
| `powerbi-report-author` | `@microsoft/powerbi-report-authoring-cli` | `0.1.4` | none (cross-platform) | catalog/formatting metadata, expr codec, **validate** |
| `powerbi-modeling-mcp` | `@microsoft/powerbi-modeling-mcp` | `0.5.0-beta.11` | `win32/darwin/linux`, `x64/arm64` | TMDL authoring MCP (file mode) |
| `powerbi-desktop` | `@microsoft/powerbi-desktop-bridge-cli` | `0.1.2` | none declared, **but drives Power BI Desktop (Windows-only)** | reload + screenshot verification |
| (bundled) | `@microsoft/powerbi-core-visual-schema` | -- | n/a | authoritative visual/formatting metadata provider |

`FILE` MCP servers the skills declare: `powerbi-modeling-mcp` (stdio, `npx -y ... --start`) and a
`FabricIQ` HTTP MCP (`https://api.fabric.microsoft.com/v1/mcp/...`, bearer via
`${FABRIC_MCP_TOKEN}` from `az account get-access-token --resource
https://analysis.windows.net/powerbi/api`).

---

## 3. Feasibility matrix

Windows cells are `TESTED` on this host. Linux cells are `INFERRED` from npm `os`/`cpu` metadata
(`DOCS`), **not** run on Linux in this pass; a Devin Linux container run would confirm them.

| Component | Windows (this host) | Linux (Devin cloud) | Evidence |
|---|---|---|---|
| PBIR file authoring + validation CLI | **works** | should work | `TESTED` install + `doctor ok`; `DOCS` no os/cpu constraint |
| Authoring metadata / enum lookup | **works** (57 visual types, 15 VCOs) | should work | `TESTED` `doctor`, `catalog list` |
| TMDL read/write via Modeling MCP (**file mode**) | launches | should launch | `TESTED` `powerbi-modeling-mcp --version 0.5.0-beta.11`; `DOCS` `os: linux` |
| Modeling MCP attached to live Desktop | Windows-only | **no** | `INFERRED` (Desktop is Windows-only) |
| Desktop reload + screenshot loop | Windows + Desktop only | **no** | `DOCS`/`INFERRED` `powerbi-desktop` drives Desktop |
| Fabric publish via `az rest` | `az 2.86.0` present | yes (az is cross-platform) | `TESTED` `az version`; `DOCS` |

**Key implication.** The generate-and-validate path is genuinely cross-platform; only the
*visual verification* half is Windows-bound. A Devin Linux workspace can produce and statically
validate a PBIP, but cannot do the reload/screenshot check the `powerbi-report-authoring` skill
describes.

---

## 4. What actually ran (TESTED)

All commands run on this Windows host, 2026-07-23. Reproduction commands are in the
[Appendix](#appendix-reproduction).

**4.1 Install**, `npm i -g @microsoft/powerbi-report-authoring-cli@latest
@microsoft/powerbi-modeling-mcp@latest` produced *added 11 packages in 14s*, exit 0. Both binaries
resolve on PATH. `powerbi-report-author --version` returns `0.1.4`; `powerbi-modeling-mcp
--version` returns `0.5.0-beta.11`.

**4.2 Self-check**, `powerbi-report-author doctor` returns `ok: true`; node 22.14.0 >= 20; ajv ok;
metadata-provider **57 visual types, 15 VCOs**, source `npm:@microsoft/powerbi-core-visual-schema`.
This answers a standing open question: the CLI gets PBIR enums/property names from a **bundled
schema package**, not the model's memory. `catalog list` returns the full visual list and flags
deprecations (`filledMap`/`map` becomes `azureMap`, `qnaVisual` unsupported).

**4.3 Generation (no Desktop)**, a dedicated backend's own deterministic, zero-LLM path:
`python -m app.cli report-studio from-rdl --rdl tests/fixtures/sales_by_region.rdl --out C:\rsb`.
Result: parsed 1 datasource + 2 visuals (Tablix becomes `tableEx`, Chart becomes
`clusteredColumnChart`) + 1 page; generated TMDL (`model`, `database`, `cultures/en-US`,
`SalesData`, `_Measures`, `DateTableTemplate`), PBIR, theme; packaged `sales_by_region.zip`. One
warning: *"Datasource 'SalesData': No file mapped and no connection info. Semantic model table
will be empty"*, expected for the no-data path.

**4.4 Cross-tool validation, the core result.** `powerbi-report-author validate` run against the
tested backend-generated PBIP (no Desktop): **result `failed`, 9 errors, 6 warnings.**

| Diagnostic | Sev | x | What it means |
|---|---|---|---|
| `PBIR_PROJECTION_MISSING_NATIVE_QUERY_REF` | error | 4 | The generator emits `queryRef` but **not** `nativeQueryRef`; the MS reference requires it on every projection. **Real gap.** |
| `PBIR_THEME_NAME_MISSING_JSON_EXT` | error | 1 | `customTheme.name` lacked the `.json` suffix; must match the extension the service expects or theming applies wrong. **Real bug.** |
| `PBIR_THEME_RR_PACKAGE_MISSING` | error | 1 | Theme registered but no `RegisteredResources` package declared in `report.json` `resourcePackages`. **Real bug.** |
| `PBIR_FORMATTING_OBJECT_UNKNOWN` | error | 2 | A formatting object name emitted on `tableEx` was not a known object name. **Real bug.** |
| `PBIR_PLATFORM_MISSING` | error | 1 | No per-item `.platform` file. Matters for Desktop-open and the MS transport skill. |
| `PBIR_SCHEMA_UNREACHABLE` | warning | 6 | Remote JSON schemas (`developer.microsoft.com`, report schema **2.0.0**) not fetched offline. Not a defect; degrades gracefully. |

This is the payoff: **five distinct real defect classes in the generator's own PBIR output, found
with zero Power BI Desktop, entirely from a Linux-capable CLI.** It also corrects an earlier
inference that `nativeQueryRef` was only a documentation artifact, it is a genuinely required
field.

**4.5 Windows path caveat** `TESTED`, the first generation attempt failed with
`FileNotFoundError` writing `DateTableTemplate_<guid>.tmdl` under a deep temp dir: the full path
exceeded Windows' 260-character `MAX_PATH`. Re-running with a short `--out` (`C:\rsb`) succeeded.
Relevant to Devin-for-Terminal on Windows and to any deep workspace path.

**4.6 Fixes implemented and re-verified** `TESTED`, all five defect classes above were fixed in
the generator's report, theme, and packager modules. Re-running the same generate to validate loop
now reports **0 errors** (`result: succeededWithWarnings`); the only remaining diagnostics are the
7 `PBIR_SCHEMA_UNREACHABLE` warnings (offline remote-schema fetch, not a defect). The fixes are
locked in by a dedicated regression test module (4 tests) and the full backend test suite stays
green (413 passed). The `.platform` file is now emitted for both the Report and SemanticModel
items with a deterministic `logicalId`.

---

## 5. Backend integration analysis

**5.1 The Devin drop-in seam already exists.** `FILE` The source backend ships a settings-driven
"local agent CLI" engine whose module docstring names Devin as the target. Its contract:

| Setting | Default | Purpose |
|---|---|---|
| `REPORT_STUDIO_AGENT_CLI_CMD` | `""` | binary; empty = engine unavailable |
| `REPORT_STUDIO_AGENT_CLI_ARGS` | `-p` | argv appended after the binary |
| `REPORT_STUDIO_AGENT_CLI_PROMPT_MODE` | `stdin` | `stdin` \| `arg` |
| `REPORT_STUDIO_AGENT_CLI_OUTPUT_MODE` | `text` | `text` \| `json_envelope:<field>` |
| `REPORT_STUDIO_AGENT_CLI_TIMEOUT_SECONDS` | `300` | subprocess timeout |

`FILE` The engine runs `subprocess.run(argv, input=prompt_or_None, capture_output=True, text=True,
timeout=...)`, then extracts the result text (stdout as-is for `text`, or a JSON field for
`json_envelope:<field>`). The result must ultimately contain an `IRWorkbook` JSON object, which
the deterministic pipeline renders to PBIP. **Devin fits iff its CLI (a) runs non-interactively,
(b) accepts a prompt on stdin or as an arg, (c) prints the answer to stdout (plain or
JSON-enveloped).** If Devin's CLI is interactive-only, this seam is a NO-GO, the remaining open
question ([10](#10-open-questions)).

**5.2 The Fabric publish path is a Python re-implementation of the equivalent skill's `az rest`
flow, not a wrapper.** `FILE` The backend's Fabric client calls Fabric/Power BI REST directly with
`requests` + `azure-identity`: create-item vs update-item-definition, 202 to LRO poll, inline
base64 parts. Publish is create-if-absent / update-in-place. So this backend and the
`powerbi-report-management` skill implement the *same* REST semantics two ways. Divergences to
keep aligned: the skill has an explicit report-to-model **binding-verification** step; the tested
backend's path only gates on the LRO reaching `succeeded`.

**5.3 A local Power BI Modeling MCP refinement engine already exists in the source backend.**
`FILE`+`DOCS` It launches `npx -y @microsoft/powerbi-modeling-mcp@latest --start` over stdio
against the PBIP `definition/` folder, "no Power BI Desktop, Fabric connection, or sign-in
needed." `docs/powerbi-mcp-engine.md` records live testing at MCP **v0.5.0-beta.11**. This is the
same server the Microsoft skills use; it is a working precedent inside the source backend.

**5.4 Divergences from the old spike text (corrected here).** `FILE`
- A previously claimed "vendored PBIR constants" module **does not exist** in the tested backend.
  The equivalent constants are scattered across a couple of generator modules. There is nothing on
  disk to diff against a Microsoft version.
- Previously claimed prompt-template paths **do not exist** at those paths; the real templates
  live at the repository root under `prompts/`, none carrying an external-version note.
- **Aggregation enum:** the tested backend's code uses `Sum=0, Avg=1, Min=2, Max=3, Count=4,
  CountDistinct=5, Median=6`. The old spike text asserted `Count=2, Min=3, Max=4`. `TESTED` The MS
  validator did **not** flag the aggregation values (it flagged the missing `nativeQueryRef`
  instead), so the spike text's numbers appear to be the erroneous ones, but `INFERRED`:
  definitive confirmation needs a data-mapped run or a read of the bundled
  `powerbi-core-visual-schema`.
- The `REPORT_STUDIO_AGENT_CLI_*` settings are defined in config and documented separately but are
  **absent from the example env file**, add them when wiring Devin.

---

## 6. Implementation plan

Ordered, each step independently verifiable. Steps 1-2 deliver value without Devin at all.

**Step 1, adopt the MS PBIR validator as a CI/pipeline gate (highest value, lowest cost).**
Add an optional post-package validation step that shells to `powerbi-report-author validate
<pbip> --out <json>` and surfaces the diagnostics. Wire it behind a setting (for example
`ENABLE_PBIR_VALIDATION`, default off) so it degrades cleanly when the CLI is absent, mirroring
the availability-probe pattern already used for the local Modeling MCP engine. This immediately
catches the five defect classes in [4.4](#4-what-actually-ran-tested) on every run.

**Step 2, fix the five generator defects the validator found. (DONE, re-validated to 0 errors,
see [4.6](#4-what-actually-ran-tested).)** Each was a small, targeted change with the validator as
its regression check:
1. `nativeQueryRef` on projections, add a fixer that backfills `nativeQueryRef` from `queryRef`.
2. Theme name `.json` suffix, fix theme registration so `customTheme.name` matches the
   `resourcePackages` entry.
3. RegisteredResources package, ensure `report.json` `resourcePackages` declares the theme already
   written to `resources/RegisteredResources/`.
4. Unknown formatting object, replace it with the object name the CLI's
   `formatting list-objects tableEx` reports as valid.
5. `.platform` file, emit a per-item `.platform` (logicalId/type/displayName).

**Step 3, vendor the Microsoft skills for Devin (only if a Devin seat is in play).** Copy the
`SKILL.md` folders (`powerbi-report-planning/-design/-authoring/-management`,
`semantic-model-authoring`) into `.agents/skills/<name>/SKILL.md` at the repo root, the one
location both Devin cloud and Devin CLI scan. Pin the source commit (currently `d79f339`, v0.3.9).
Vendoring, not a marketplace install, is the only supported route ([1](#1-devins-skill-mechanism)).

**Step 4, wire the MCP servers + secrets in Devin separately.** Devin ignores the plugins'
`.mcp.json`, so register `powerbi-modeling-mcp` (stdio) and, if used, the FabricIQ HTTP MCP
through Devin's own MCP config, and expose `FABRIC_TENANT_ID/CLIENT_ID/CLIENT_SECRET` (and any
`FABRIC_MCP_TOKEN`) via Devin's secrets. `UNCERTAIN`, the exact Devin mechanism is unconfirmed and
must be validated on a real seat.

**Step 5, wire Devin's CLI into the existing seam (alternative to Steps 3-4).** If Devin ships a
non-interactive CLI, set the engine to the local agent CLI mode with Devin as the command and its
`_ARGS/_PROMPT_MODE/_OUTPUT_MODE` matching its I/O contract ([5.1](#5-backend-integration-analysis));
add these to the example env file. This reuses the deterministic renderer and sidesteps the
Desktop dependency entirely, because rendering is a Python pipeline, not the skills' Desktop loop.

---

## 7. Verification (how to test the changes end to end)

```powershell
# Prereqs (once)
npm i -g @microsoft/powerbi-report-authoring-cli@latest @microsoft/powerbi-modeling-mcp@latest
powerbi-report-author doctor            # expect ok:true

# Generate a PBIP deterministically (short --out avoids Windows MAX_PATH)
cd backend
$env:PYTHONPATH = "$(Get-Location)\..;$(Get-Location)"; $env:ENVIRONMENT = "development"
..\.venv\Scripts\python.exe -m app.cli report-studio from-rdl `
  --rdl tests/fixtures/sales_by_region.rdl --out C:\rsb

# Cross-validate with Microsoft's CLI (the regression gate for Step 2)
Expand-Archive C:\rsb\sales_by_region.zip -DestinationPath C:\rsb\extracted -Force
powerbi-report-author validate C:\rsb\extracted\sales_by_region\sales_by_region.pbip --pretty
#   Baseline before fixes: failed, 9 errors, 6 warnings. Target after Step 2: 0 errors
#   (PBIR_SCHEMA_UNREACHABLE warnings persist offline and are acceptable).
```

- **Step 1** passes when the pipeline surfaces the validator's diagnostics and still succeeds when
  the CLI is absent.
- **Step 2** passes when `validate` drops from 9 errors to 0 (warnings from offline schema fetch
  excepted) and existing PBIP tests stay green.
- **Step 5** passes when a real Devin CLI returns a parseable `IRWorkbook` through the local agent
  CLI seam and the pipeline renders a PBIP from it.

---

## 8. Blockers

| # | Blocker | Severity | What unblocks it |
|---|---|---|---|
| B1 | Desktop reload/screenshot loop is Windows-only; absent on Devin Linux | High (for the full MS flow) | Use file-mode validation only, or run Devin-for-Terminal on Windows with Desktop |
| B2 | Devin ignores plugin `.mcp.json`/secrets; MCP + `FABRIC_*` must be wired separately | Medium | Confirm and configure Devin's own MCP/secrets features (needs a seat) |
| B3 | Unknown whether Devin's CLI is non-interactive with stdin/stdout I/O | Medium (gates Step 5) | Run `devin --help`/a stdin smoke test on a real CLI |
| B4 | `powerbi-modeling-mcp` is **beta** (`0.5.0-beta.11`) | Low-Med | Pin the version; re-verify on upgrade |
| B5 | Python 3.13 only on this host (the source repository prefers 3.11/3.12) | Low | Deps installed fine here; use 3.12 in CI to match guidance |
| B6 | Windows `MAX_PATH` truncates deep PBIP output paths | Low | Short output roots, or enable Win32 long paths |

---

## 9. Recommendation

**Steps 1-2 need no Devin and no Fabric. Step 2 is now done** (the five defect classes are fixed
and re-validated to 0 errors, [4.6](#4-what-actually-ran-tested)); **Step 1 remains**, wire the
Microsoft PBIR validator in as an optional, default-off pipeline/CI gate so future regressions are
caught automatically. The validator is a cross-platform, Desktop-free quality gate, and running it
on every build keeps a generator aligned with the moving Microsoft reference (which advanced
v0.3.7 to v0.3.9 during this study).

**Treat full Devin+skills orchestration as opportunistic, not foundational.** A dedicated backend
already owns the two hard parts the skills provide, deterministic PBIP rendering and a Python
Fabric publish path, so the marginal value of driving the skills through Devin is modest and gated
on B1-B3. If a Devin seat exists, prefer **Step 5** (Devin as an IR provider on the existing local
agent CLI seam) over Steps 3-4 (vendoring the full skill set), because it reuses the renderer and
avoids the Desktop dependency.

---

## 10. Open questions

- **Devin CLI I/O contract**, non-interactive? stdin vs arg? plain text vs JSON envelope?
  (Decides Step 5; needs a real CLI, B3.)
- **Devin MCP + secrets mechanism**, how are `powerbi-modeling-mcp` and `FABRIC_*` actually
  registered? (Not in the skills docs, B2.)
- **Aggregation enum ground truth**, confirm `Min=2/Max=3/Count=4` against the bundled
  `@microsoft/powerbi-core-visual-schema` or a data-mapped generation run ([5.4](#5-backend-integration-analysis)).
- **Linux parity**, re-run [4](#4-what-actually-ran-tested) in a Linux container to convert the
  `INFERRED` matrix cells to `TESTED`.
- **`.platform` necessity for a direct REST publish path**, does it need it, or only Desktop-open?
  (Affects Step 2.5 priority.)

---

## Appendix, reproduction

```bash
# Environment
node --version && npm --version && python --version && az version && uname -s -r

# npm metadata (os/cpu/version)
npm view @microsoft/powerbi-modeling-mcp version os cpu engines
npm view @microsoft/powerbi-report-authoring-cli version os cpu engines
npm view @microsoft/powerbi-desktop-bridge-cli version os cpu engines

# Install + self-check
npm i -g @microsoft/powerbi-report-authoring-cli@latest @microsoft/powerbi-modeling-mcp@latest
powerbi-report-author doctor --pretty
powerbi-report-author catalog list

# Generate + validate (see 7 for the PowerShell form)
python -m app.cli report-studio from-rdl --rdl tests/fixtures/sales_by_region.rdl --out C:\rsb
powerbi-report-author validate C:\rsb\extracted\sales_by_region\sales_by_region.pbip --pretty
```

### Primary sources
- `https://docs.devin.ai/product-guides/skills`
- `https://docs.devin.ai/cli/extensibility/skills/overview`
- `https://agentskills.io/specification`
- `https://github.com/microsoft/skills-for-fabric` @ `d79f3393cab658d0b12d7215b7df1a069a2463a5`
- npm: `@microsoft/powerbi-report-authoring-cli` 0.1.4, `@microsoft/powerbi-modeling-mcp`
  0.5.0-beta.11, `@microsoft/powerbi-desktop-bridge-cli` 0.1.2
- On disk: a locally cloned skills-for-fabric marketplace, `installed_plugins.json`
- Backend code referenced (paths internal to the source repository, not included in this public
  copy): the Report Studio generator/local-agent-CLI/Modeling-MCP/Fabric-MCP modules, the Fabric
  publish/workspace-client modules, the generator report/tmdl/packager modules, the CLI entry
  point, and this repository's own `docs/powerbi-mcp-engine.md`.
