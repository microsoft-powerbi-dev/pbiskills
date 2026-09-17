# Component: `skills/vendor/microsoft-fabric/` and `skills/ide-references/`

Two directories that share a theme — neither one is original guidance written
for Claude Code's SKILL.md format, and both exist so the repository's
knowledge is usable outside that format. `skills/vendor/microsoft-fabric/` is
unmodified third-party content (Microsoft's own Power BI report skills, MIT
licensed). `skills/ide-references/` is this repository's own content,
reshaped into formats that Devin, Cursor, and GitHub Copilot can actually
read. This document covers both.

---

## Part 1 — `skills/vendor/microsoft-fabric/`

### 1.1 Provenance

| Fact | Value |
| --- | --- |
| Source | `https://github.com/microsoft/skills-for-fabric` |
| Paths vendored | `skills/powerbi-report-planning`, `skills/powerbi-report-design`, `skills/powerbi-report-authoring`, `skills/powerbi-report-management`, plus `common/COMMON-CLI.md` and `common/COMMON-CORE.md` |
| Commit | `main`, fetched 2026-08-27 |
| License | MIT, copyright Microsoft Corporation 2026 (`LICENSE` in this folder, copied from upstream) |
| Fidelity | Byte-for-byte copy of upstream, including em dashes and other prose choices this repository's own style guide would normally avoid — house style applies to content this repository's authors write, not to vendored, licensed, third-party text |
| Adaptation | Exactly one: `powerbi-report-management/SKILL.md` was edited to link to `../common/COMMON-CLI.md` and `../common/COMMON-CORE.md` instead of `../../common/...`, because this vendored copy drops the redundant `skills/` wrapper that upstream uses. Nothing else was touched. |

`skills-for-fabric` ships roughly two dozen skills covering the whole Fabric
surface (Spark, Eventhouse/KQL, Eventstreams, Dataflows Gen2, SQL DB/DW,
migration playbooks). Only the four that own the Power BI *report* surface
were vendored, because this repository is about SSRS-to-Power-BI migration
and these four already form a closed loop — planning routes to design, design
hands off to authoring, management transports the result — so pulling all
four keeps every cross-reference inside the vendored text resolvable. `common/`
was trimmed to the two files the management skill actually links to, not the
full shared library (which also covers Spark/Eventhouse/Dataflows/SQL DB-DW
topics unrelated to Power BI reports).

### 1.2 Where this sits relative to the rest of the repository

This repository's own skills (`skills/claude-skills/rdl-generation/`,
`skills/claude-skills/ssrs-report-creation/`) stop at the RDL/paginated-report
boundary, and `skills/report-lineage/` covers understanding an existing
SSRS/SSIS estate. None of that overlaps with these four vendored skills, which
start on the other side of a migration: once a report becomes a Power BI
`.pbip`/PBIR project — by hand, by this repository's inverse
`rdl_generator.py` path, or by Report Studio — these skills cover designing,
authoring, and publishing it. A typical end-to-end flow: fingerprint the
existing RDL estate with `skills/report-lineage/`, decide per-report whether
to keep it paginated or convert it (guidance in
`skills/claude-skills/ssrs-report-creation/references/migration-to-fabric.md`),
then use `powerbi-report-planning` through `powerbi-report-management` here
for the reports being converted.

### 1.3 The four skills as a pipeline

| Skill | Owns | Has no `references/` folder? |
| --- | --- | --- |
| `powerbi-report-planning` | The guided requirements-to-implementation workflow: audience, scope, page plan, approval gate, build sequencing | Correct — self-contained in `SKILL.md` |
| `powerbi-report-design` | Visual design decisions before any file is written: tone, archetype, chart selection, layout, color, typography, accessibility | Has 14 reference files + 5 archetype files + `assets/base.json` |
| `powerbi-report-authoring` | PBIR/PBIP file mechanics: pages, visuals, filters, slicers, themes, formatting, validation, Desktop reload and screenshots | Has 23 reference files |
| `powerbi-report-management` | Transporting a report to and from a Fabric workspace over the REST API: create, get, update, delete | Correct — self-contained in `SKILL.md` |

Routing is strict and mutually reinforcing: planning invokes design during its
Round 3–4 (page/archetype and identity decisions) and hands the approved
result to authoring; design refuses to write PBIR and hands off to authoring;
authoring refuses to do Fabric transport and hands off to management;
management refuses to construct PBIR JSON from memory and insists everything
go through authoring first. Each skill's `SKILL.md` states this boundary
explicitly under a `Must/Prefer/Avoid` section.

### 1.4 `powerbi-report-planning`

**Purpose.** Orchestrates the full lifecycle for a new report:
`Define -> Inspect -> Spec -> Approve -> Build -> Validate -> Publish`. It
runs up to five clarification rounds (Round 0 dependency check, Round 1
audience/job, Round 2 model inventory and scope, Round 3 narrative/page plan,
Round 4 design identity/accessibility/delivery), then locks one file,
`./_brief/report-spec.md`, containing both Markdown sections for human
approval and a fenced YAML `Design Brief:` block that is the canonical
implementation contract. Nothing is built until the user approves that spec.

**When to use vs. the others.** Use for broad "build me a dashboard"-style
requests that need requirements gathering, dependency checks, approval, and
build sequencing. Do not use it for a small surgical edit to an existing PBIR
page (use `powerbi-report-authoring` directly), or for a one-off "redesign
this" / "what should this look like?" ask with no build workflow attached (use
`powerbi-report-design` directly — planning *uses* design during Rounds 3–4,
it does not replace it).

**External tooling.** No CLI of its own. The dependency checklist it walks in
Round 0 lists: Power BI Desktop (local preview), a PBIP/PBIR project, a TMDL
semantic model, a `powerbi-modeling-mcp` server (live model authoring),
the `powerbi-report-authoring` skill (validation/reload/screenshot), the
`powerbi-report-management` skill (Fabric publish, optional), and Node.js
(recommended for generator-based PBIR authoring). Any dependency that is
unavailable gets marked blocked/manual rather than assumed present.

**Reference material.** None — everything (round structure, the design
contract gate, the `report-spec.md` template, the acceptance checklist before
approval, the 16-step implementation sequence, Fabric publish rules,
validation standards, anti-patterns) lives directly in `SKILL.md`.

### 1.5 `powerbi-report-design`

**Purpose.** Decides *what* a report should look like and *why*, before any
PBIR file exists: commits a design identity (tone + signature), routes each
*page* (not each report) to one of five archetypes, picks a layout variant,
selects chart types, configures per-visual rules, adapts the theme, and emits
a structured `Design Brief:` YAML contract. It explicitly does not create
pages, visuals, filters, or theme files — that is `powerbi-report-authoring`'s
job.

**When to use vs. the others.** Use for open-ended visual design: choosing
tone/archetype/chart type/layout/color, redesigning or restyling an existing
report, applying a brand, or critiquing chart/layout choices. `SKILL.md`
frames its own 8-step workflow (Data-First Investigation → Design Identity →
Archetype Router → Chart Selection → Visual Configuration → Theme → Canonical
Design Contract → Review and Handoff), each step pointing at a specific
reference file to read on demand rather than loading the whole catalog.

**External tooling.** None beyond semantic-model inspection (a Semantic Model
MCP server if available, or direct `.tmdl` file reads). No CLI is defined by
this skill.

**Reference material by topic** (`references/`, 14 files, plus 5 archetype
files under `references/archetypes/` and `assets/base.json`):

| Topic | File(s) | Content |
| --- | --- | --- |
| Design identity | `tone-catalog.md`, `signatures.md` | Named tone entries and their downstream palette/type implications; signature visual moves |
| Page archetypes | `archetypes/executive-summary.md`, `operational-monitor.md`, `analytical-canvas.md`, `narrative-story.md`, `comparative-benchmark.md` | Each ships: a Job-to-be-Done table, Core Principles, 2–3 layout variants (A/B/C) with an explicit data-signal selection table, ASCII layout diagrams with pixel zone heights, chart-type "Use" / "Do NOT Use" tables, color+typography specs, interaction design rules, a PBI formatting-property reference, and a shippable decision checklist |
| Multi-page composition | `archetype-composition.md` | Common multi-page compositions (Executive + Drill, Ops + Detail, Story + Evidence, Multi-domain) and cross-page variant rotation rules |
| Chart selection | `chart-selection.md` | Purpose-to-chart decision matrix (comparison/composition/distribution/relationship/trend/ranking/deviation/flow/single-KPI/geospatial), a native-PBI-visual crosswalk, cardinality limits per visual type, and the Cleveland–McGill encoding-accuracy hierarchy |
| Visual configuration | `visual-cookbook.md` | Per-visual-type sort/color/label/axis/conditional-formatting rules |
| Layout | `layout.md` | 8px base grid, 12-column FHD grid arithmetic, F-pattern vs. Z-pattern reading order, slicer-placement rules (inline for 1–3 slicers, vertical rail for 4+), space-allocation rules (max empty-cell %, hero-region caps) |
| Color | `color.md` | Sequential/diverging/categorical palette tables with CVD-safety flags, semantic color rules (green/red/amber never flipped without instruction), PBI theme-key grounding (`dataColors`, `good`/`neutral`/`bad`, etc.), a same-measure-same-color assignment strategy with a worked 4-card example |
| Typography | `typography.md` | Font-size/weight conventions and pairings |
| Theme | `assets/base.json` | Starter Power BI theme JSON; callouts document the textbox/card/table per-type safeguards that must be preserved when adapting it |
| Interaction, brownfield | `interactivity.md`, `brownfield.md` | Drill-through/bookmark/cross-filter rules; redesign/restyle/theme-swap workflow, current-vs-target tone capture |
| Accessibility | `accessibility.md` | WCAG 2.1/2.2 checklist mapped to dashboard implications, four alt-text templates, full keyboard-navigation reference table, the WCAG contrast ratio formula with worked hex-pair examples, CVD prevalence/safe-palette notes, and a 10-item pre-publish accessibility test checklist |
| Quality gates | `anti-patterns.md`, `pre-flight-checklist.md`, `design-brief.md` | Common failure catalog; final review checklist; the full `Design Brief:` YAML schema (page-level `layout_contract`, `grid.regions`, `placements`, `space_audit`) and its validation rules |

`SKILL.md` itself also documents a set of non-obvious "Gotchas" (tone declared
but never propagated, page background left white/borderless, textbox
scrollbar height formula, monochrome bars from single-series charts, raw
database field names left on axes) that recur across archetypes.

### 1.6 `powerbi-report-authoring`

**Purpose.** Owns all PBIR/PBIP file mechanics once there is a concrete spec
or edit to make: page/visual JSON, bindings, filters, slicers, themes,
formatting, navigation, bookmarks, validation, and Desktop-verified rendering.

**When to use vs. the others.** Use once there is an approved
`Design Brief:`/`report-spec.md` (from planning/design) or a concrete PBIR
edit to implement. Route open-ended design uncertainty back to
`powerbi-report-design` and full requirements/build sequencing back to
`powerbi-report-planning` rather than deciding those things here.

**External tooling — exact requirements cited in `SKILL.md`:**

- **Node.js 20 or later** (`node --version`).
- Two npm packages installed globally:
  `npm install -g @microsoft/powerbi-report-authoring-cli @microsoft/powerbi-desktop-bridge-cli`,
  confirmed on `PATH` via `powerbi-report-author --version` and
  `powerbi-desktop --version`.
- **Power BI Desktop (Windows)** for the reload/screenshot verification loop
  — the `powerbi-desktop` CLI drives a running Desktop instance via a bridge
  pipe; there is no headless equivalent for visual verification.

**Reference material by topic** (`references/`, 23 files):

| Topic | File(s) | Content |
| --- | --- | --- |
| Core mechanics | `authoring.md` | ID generation rules (visual/page/filter naming), format-version constants, add-a-page / add-a-visual walkthroughs, three copy-paste layout templates, theme-change mechanics with the GUID cache-busting rule |
| CLI reference | `powerbi-report-author-cli.md` | Full command catalog: `catalog list/describe`, `formatting list-objects/describe-object/describe-property/search/list-vcos/effective-properties`, `expr encode/decode`, `theme encode/shade-color`, `validate`, `preview-visuals/pages/filters/themes`, `doctor` |
| Desktop verification | `powerbi-desktop.md` | `open/status/manifest/reload/screenshot/screenshot-all` commands, PID-selection rules (never `--report`, always select by PID from `status`), a full error-outcome table (`AMBIGUOUS_DESKTOP_INSTANCE`, `METHOD_NOT_AVAILABLE`, `Timeout`, `Cancelled`, `REPORT_DIR_REQUIRED`) |
| Version control | `version-control.md` | Pre-flight git-repo check, mandatory branch-before-editing workflow (`copilot/<intent>` branches), validate-before-commit gate, never-auto-commit rule, revert patterns (uncommitted / last commit / to baseline / single file) |
| Formatting cascade | `formatting-overview.md`, `formatting.md`, `color-strategy.md`, `conditional-formatting.md`, `page-formatting.md`, `filter-pane.md`, `theming.md`, `re-theming.md` | Selector/VCO encoding mechanics; `dataColors` vs. `dataPoint.defaultColor` vs. `dataPoint.fill`; FillRule/rules/icon-set/data-bar conditional formatting; page background vs. plot-area image routing; filter-pane chrome; theme JSON authoring; the color-mapping-then-bulk-sweep re-theming workflow (including a dark-mode checklist) |
| Data binding | `expressions.md`, `filters.md`, `slicers.md` | Field/measure/aggregation/hierarchy expression construction; filter JSON; slicer selection state (`expansionStates` + `objects.general.filter`) |
| Visual families | `cartesian.md`, `map.md`, `card.md`, `table.md`, `image.md`, `shape.md`, `textbox.md` | Per-visual-type templates, roles, and known rendering pitfalls |
| Output review | `screenshot-review.md` | Rendered-output review checklist after Desktop screenshot capture |

`SKILL.md` also carries a roughly 40-row anti-patterns table covering, among
others: filter `Where` clauses needing `"Source"` not `"Entity"`; legacy
visual types (`card`, `table`, `matrix`, `map`/`filledMap`) that must never be
created in favor of `cardVisual`/`tableEx`/`pivotTable`/`azureMap`; `dataPoint.fill`
needing a `metadata` selector or bars render invisible; `ThemeDataColor`
resolving to white/black unpredictably inside `FillRule`; and PowerShell's
`ConvertTo-Json` truncating nested JSON at its default `-Depth 2`.

### 1.7 `powerbi-report-management`

**Purpose.** CRUD for Power BI report items and their PBIR definitions in a
Fabric workspace, using `az rest` against the Fabric REST API — list, get,
create, update, delete, and the download/upload of PBIR definition parts.
Explicitly out of scope: any PBIR content authoring (that is exclusively
`powerbi-report-authoring`'s job — the `Must` section states this is "the
single most important rule" and forbids constructing even `definition.pbir`
or `version.json` from memory).

**When to use vs. the others.** The entry point when a user has a local
`.pbip` (or an already-downloaded `.Report` folder) and wants to publish,
upload, get, update, or delete a report in Fabric. It delegates semantic-model
deployment to a semantic-model authoring skill (not vendored in this
repository) rather than authoring TMDL itself.

**External tooling.**

| Tool | Role |
| --- | --- |
| `az` CLI | Primary: `az rest` for every Fabric REST call, `az login` for auth |
| `jq` | Parse/construct JSON payloads |
| `base64` (or PowerShell `[Convert]::ToBase64String`/`FromBase64String`) | Encode/decode PBIR file content for definition payloads |

**Reference material.** None — everything lives in `SKILL.md`: the companion-
skill partition table, authentication (Fabric API audience only), workspace/
report resolution (delegated to `COMMON-CLI.md`), CRUD examples for every
operation (list, get properties, `getDefinition?format=PBIR` with its
PBIR-Legacy rejection rule, create with definition, `updateDefinition`
replace-everything semantics, patch properties, soft/hard delete), an LRO
section with a hard rule never to retry a create POST after a `202`, the
`definition.pbir` `byConnection` schema, and three full agentic workflows:
**Publishing a local `.pbip`** (10 steps — detect local source, confirm
workspace once, prompt publish-model-vs-connect-existing, resolve
`semanticModelId`, verify TMDL bindings, rebind `byPath` → `byConnection`,
create-or-update, encode/upload, clean up), **Modifying an existing report in
Fabric** (7 steps), and **Creating a new report in Fabric** (7 steps), plus a
troubleshooting table covering `MissingDefinitionParts` (Windows backslash
paths breaking the Fabric API), duplicate reports from retried creates, and
stale `byConnection` model IDs.

### 1.8 The shared `common/` library

Two files, referenced by `powerbi-report-management` (and, transitively, by
the other three skills' delegated Fabric operations):

- **`COMMON-CLI.md`** — concrete, copy-pasteable `az`/`curl`/`jq`/`sqlcmd`
  recipes: authentication (`az login` variants including
  `--allow-no-subscriptions` and device-code/service-principal/managed-identity
  flows), the `az rest --resource "https://api.fabric.microsoft.com"`
  requirement (omitting `--resource` is called out as "the single most common
  mistake"), workspace/item resolution by `displayName` via JMESPath, a
  reusable pagination loop, a reusable `fabric_lro` bash polling helper,
  OneLake `curl` recipes (create→append→flush upload), `sqlcmd -G` for
  TDS/SQL access, job execution (`RunNotebook`/`Pipeline`/`Refresh` — never
  `DefaultJob`), job scheduling, capacity management, three composite
  end-to-end recipes, and a CLI-specific gotchas table.
- **`COMMON-CORE.md`** — the language-agnostic REST specification behind the
  CLI file: Fabric topology (tenant → capacity → workspace → item → OneLake),
  environment URLs for every workload, the token-audience table (Fabric API,
  Power BI/XMLA, OneLake/storage, SQL/TDS, KQL, ARM — each a distinct audience
  and the most common cause of `401`), the core control-plane REST APIs
  (catalog search, workspace/item CRUD, pagination, LRO), OneLake data access,
  job execution, capacity management, and a general troubleshooting/best-
  practices table.

---

## Part 2 — `skills/ide-references/`

### 2.1 Purpose

The primary skills at `skills/claude-skills/{rdl-generation,
ssrs-report-creation, sql-server-schema}/` use the Anthropic SKILL.md format
(YAML frontmatter, `triggers:`, bundled `references/`/`scripts/`/`examples/`).
Not every tool reads that format. `skills/ide-references/` holds the same
knowledge reshaped for four other consumption paths, per its own `README.md`:

| Folder | For | Format |
| --- | --- | --- |
| `vendor-neutral/` | Any human reader, or any tool that just ingests markdown | Plain playbook, no frontmatter |
| `devin/` | Devin AI's knowledge base | Trigger + content, one concern per entry |
| `cursor/` | Cursor | `.cursorrules` |
| `copilot/` | GitHub Copilot (VS Code) | `copilot-instructions.md` |

These are declared **generated views, not a second source of truth**: if a
SKILL.md or its references change materially, the condensed copies here need
updating too, and drift here is a documentation bug, not a functional one.
One hard rule specific to this folder: the SQL Server variants describe only
the static `sql-server-schema` skill (no customer data); nobody may derive an
IDE reference from a *generated* schema pack, because that would name real
internal servers/databases/tables in a folder meant for a public repository.

### 2.2 `vendor-neutral/` — three playbooks

Three files, one per source skill: `rdl-generation-playbook.md`,
`sql-server-schema-playbook.md`, `ssrs-report-creation-playbook.md`. Each is
confirmed, by comparing headings and rule content against the corresponding
`skills/claude-skills/<name>/SKILL.md`, to be a plain-markdown restatement of
that skill with the frontmatter, `triggers:` list, and `allowed-tools:`
stripped out — the substance (the numbered "rules that prevent most
failures," the workflow steps, the pointers into that skill's `references/`
and `scripts/`) survives, condensed to what fits one page:

- **`rdl-generation-playbook.md`** — the five rules that prevent most RDL XML
  failures (element order under `xsd:sequence`, namespace-agnostic reads vs.
  namespace-explicit writes, mandatory unit suffixes on every measurement,
  canonical `=Fields!Name.Value`/`=Sum(...)` expression forms, `PageHeader`/
  `PageFooter` as children of `ReportSection/Page` not `Report`), the minimum
  viable document skeleton, a four-step workflow, and a symptom-to-cause
  failure table. Points back to `skills/claude-skills/rdl-generation/` for the
  seven reference docs, the Python builder/validator scripts, and five worked
  examples.
- **`sql-server-schema-playbook.md`** — the five rules for read-only schema
  analysis (structurally read-only via autocommit-off + unconditional
  rollback; breadth before depth; `sys.*` never `INFORMATION_SCHEMA`; row
  counts from `sys.dm_db_partition_stats` never `COUNT(*)`; a missing foreign
  key is not a missing relationship), a catalog-view-per-question table,
  connection-string gotchas (the ODBC Driver 18 `TrustServerCertificate`
  trap, named-instance vs. port), the fact/dimension/bridge/lookup
  classification rules, soft-delete and Type-2-SCD detection, and the PII
  column-name denylist. Points back to
  `skills/claude-skills/sql-server-schema/`.
- **`ssrs-report-creation-playbook.md`** — the paginated-vs.-interactive
  decision, a seven-stage workflow (requirements → data → parameters →
  layout → formatting → deploy → operate), the REST v2.0 deployment calls,
  named design patterns, and an anti-patterns list. Points back to
  `skills/claude-skills/ssrs-report-creation/`.

### 2.3 `devin/` — three knowledge-base entries

Three files, `rdl-generation.knowledge.md`, `sql-server-schema.knowledge.md`,
`ssrs-report-creation.knowledge.md`, each following a fixed two-section shape:
a `## Trigger` paragraph stating exactly when the entry applies, then
`## Content` — dense prose (no bullet-heavy structure, no headers inside)
restating the same rules as the vendor-neutral playbook for that skill, and
closing with a `## Reference material in this repository` pointer back to the
full `skills/claude-skills/<name>/` skill.

**How this differs from the vendor-neutral playbooks**, concretely:

- **Shape.** The playbooks are structured documents with headers, tables, and
  a workflow section a human can skim. The knowledge files are written as
  continuous prose paragraphs — this matches a knowledge-base entry meant to
  be retrieved and read as one block of guidance in response to a trigger,
  not browsed section by section.
- **Granularity of the trigger.** Each knowledge file states its applicability
  condition as an explicit `## Trigger` sentence ("Apply this knowledge when
  the task involves..."), which the playbooks do not — a playbook's "Scope"
  section describes what it covers, but nothing in it is phrased as a
  retrieval condition.
- **Density.** The knowledge files spell out more of the *why* inline (for
  example, the SQL Server entry explains why `ODBC Driver 18`'s default
  `Encrypt=yes` breaks against a self-signed certificate, in the same
  sentence as the fix), because there is no sibling reference file to defer
  to — the entry has to be self-sufficient at the point Devin retrieves it.

### 2.4 `cursor/.cursorrules` and `copilot/copilot-instructions.md`

Both files fold **all three** source skills' rules into one flat document
(unlike the per-skill split in `vendor-neutral/` and `devin/`), because both
target formats are a single project-wide rules file, not a per-topic
knowledge base.

- **`.cursorrules`** — sectioned by file-type/task ("RDL structure",
  "Expressions", "Datasets and parameters", "Anti-patterns to flag in
  review", "Before finishing any RDL change", "SQL Server schema work"),
  opening with a scope line: "These rules apply whenever a change touches an
  `.rdl` file, generates one, concerns SSRS / Power BI Paginated report
  creation, or connects to a SQL Server to read its schema." Closes with a
  `## Full reference` section pointing at all three `skills/claude-skills/`
  directories.
- **`copilot-instructions.md`** — same underlying rules, sectioned instead by
  Copilot-relevant moments ("When generating RDL XML", "When designing or
  reviewing a report", "When deploying", "When exploring a SQL Server
  database", "Reference paths"), because Copilot's completion/chat surface is
  triggered by what the developer is currently doing rather than by a file
  type alone.

**How a developer wires these in:**

- **Cursor** reads a `.cursorrules` file at the project root automatically —
  no explicit registration step; a developer places (or symlinks) the file at
  `skills/ide-references/cursor/.cursorrules` into the target repository's
  root as `.cursorrules` and Cursor applies it to every chat/completion in
  that workspace.
- **GitHub Copilot (VS Code)** reads `.github/copilot-instructions.md` (or a
  path configured via VS Code's `github.copilot.chat.codeGeneration.instructions`
  setting) the same way — a developer copies
  `skills/ide-references/copilot/copilot-instructions.md` into
  `.github/copilot-instructions.md` in the target repository.

### 2.5 Devin: two distinct mechanisms, not one

The repository documents two different ways Devin can pick up this
repository's knowledge, and they map to two different artifacts — do not
conflate them:

1. **The vendored Microsoft SKILL.md-format skills** (Part 1 of this
   document). `docs/devin-windsurf-microsoft-skills-integration.md` states
   the mechanism explicitly: "Devin has no marketplace; copy each `SKILL.md`
   folder into `.agents/skills/<name>/SKILL.md` at the repo root (the one
   path both Devin cloud and Devin CLI scan)." That doc also notes Devin's
   skill loader reads only `SKILL.md` and ignores a plugin's `.mcp.json`, so
   any MCP server a vendored skill depends on (for example
   `powerbi-modeling-mcp`) must be registered separately through Devin's own
   MCP configuration.
2. **The `devin/*.knowledge.md` files** (this Part). These are not SKILL.md
   folders and the `.agents/skills/` mechanism above does not apply to them.
   Per `skills/ide-references/README.md`, they are shaped for "Devin AI's
   knowledge base" — a trigger-plus-content entry format. No document in this
   repository specifies the exact UI/API step for loading a `.knowledge.md`
   file's trigger and content into that knowledge base; the two `.knowledge.md`
   section headers (`## Trigger`, `## Content`) are written to match the
   shape such an entry takes once created, not to describe a file Devin scans
   from a path the way it scans `.agents/skills/`.

In short: for the four Microsoft Power BI skills, the integration path is
"copy the folder to a path Devin scans." For the three RDL/SSRS/SQL-Server
skills' Devin variant, the integration path is "paste trigger + content into
Devin's knowledge base" — a manual/API step this repository's docs describe
the *shape* of, not the *mechanics* of.
