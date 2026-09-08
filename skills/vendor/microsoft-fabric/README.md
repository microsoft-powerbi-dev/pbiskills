# Vendored: Power BI report skills from microsoft/skills-for-fabric

This folder is a verbatim copy of four skills from Microsoft's public
`skills-for-fabric` repository, plus the small shared library one of them
depends on. Nothing in this folder was written by this repository's own
authors; it is vendored, unmodified, third-party content.

Source: https://github.com/microsoft/skills-for-fabric
Path vendored: `skills/powerbi-report-authoring`, `skills/powerbi-report-design`,
`skills/powerbi-report-management`, `skills/powerbi-report-planning`, plus
`common/COMMON-CLI.md` and `common/COMMON-CORE.md` (only the two files the
management skill actually links to, not the full `common/` library, which
also covers Spark, Eventhouse, Dataflows, and SQL DB/DW topics unrelated to
Power BI reports).
Commit pulled from: `main`, fetched 2026-08-27.
License: MIT (see `LICENSE` in this folder, copied from the upstream repo).

## Why these four, and not the other twenty

`skills-for-fabric` ships about two dozen skills covering the whole Fabric
surface: Spark, Eventhouse/KQL, Eventstreams, Dataflows Gen2, SQL DB/DW,
migration playbooks, and more. This repository is about SSRS-to-Power-BI
migration, so only the four skills that own the Power BI *report* surface
were pulled in:

| Skill | Owns |
| --- | --- |
| `powerbi-report-planning` | The guided requirements-to-implementation workflow: audience, scope, page plan, approval gate, build sequencing |
| `powerbi-report-design` | Visual design decisions before any file is written: tone, archetype, chart selection, layout, color, typography, accessibility |
| `powerbi-report-authoring` | PBIR/PBIP file mechanics: pages, visuals, filters, slicers, themes, formatting, validation, Desktop reload and screenshots |
| `powerbi-report-management` | Transporting a report to and from a Fabric workspace over the REST API: create, get, update, delete |

These four already form a closed loop (planning routes to design, design
hands off to authoring, management transports the result), so pulling all
four together, rather than a subset, keeps every cross-reference inside the
skill text resolvable.

## Content preserved exactly as published

Every file under `powerbi-report-authoring/`, `powerbi-report-design/`,
`powerbi-report-management/`, `powerbi-report-planning/`, and `common/` is
byte-for-byte what the upstream repository publishes: same wording, same
punctuation (including any em dashes in the original prose), same code
samples. This repository's own house style (for example, avoiding em dashes
in newly authored documents) applies to content this repository's authors
write. It does not apply here: editing vendored, licensed, third-party text
for a house style would misrepresent what was actually published upstream,
and would silently diverge from the source the moment Microsoft updates it.

If you need to know whether a specific line came from Microsoft or from this
repository's own skills, the answer is simple: everything under
`skills/vendor/microsoft-fabric/` is Microsoft's; everything under
`skills/claude-skills/`, `skills/ide-references/`, and `skills/report-lineage/`
is this repository's own.

## One adaptation: relative link depth

Upstream, every skill lives at `skills/<name>/` and the shared library at
`common/`, so `powerbi-report-management/SKILL.md` links to
`../../common/COMMON-CLI.md` (up through `skills/`, then into `common/`).

This vendored copy drops the redundant `skills/` wrapper (there is no reason
to write `skills/vendor/microsoft-fabric/skills/powerbi-report-management/`),
so `common/` sits one level up from each skill folder instead of two.
`powerbi-report-management/SKILL.md` was adjusted to link to
`../common/COMMON-CLI.md` and `../common/COMMON-CORE.md` to match. This is
the only edit made to any vendored file; everything else is untouched.

## Layout

```
skills/vendor/microsoft-fabric/
  LICENSE                                MIT, copied from upstream
  README.md                              this file
  common/
    COMMON-CLI.md                        az CLI patterns: auth, workspace/item resolution, LRO polling
    COMMON-CORE.md                       shared authentication model and token audiences
  powerbi-report-planning/
    SKILL.md
  powerbi-report-design/
    SKILL.md
    assets/base.json                     starter Power BI theme JSON
    references/                          14 files: tone, signatures, chart selection, layout, color, typography,
                                          accessibility, anti-patterns, brownfield, interactivity, design-brief,
                                          pre-flight-checklist, archetype-composition
    references/archetypes/               5 named page archetypes (executive summary, operational monitor,
                                          analytical canvas, narrative story, comparative benchmark)
  powerbi-report-authoring/
    SKILL.md
    references/                          23 files: PBIR structure, every visual family, theming, filters,
                                          slicers, expressions, formatting cascade, Desktop CLI, validation
  powerbi-report-management/
    SKILL.md                              Fabric REST API CRUD for report items (create/get/update/delete,
                                           publish a local .pbip, LRO polling)
```

## How this relates to the rest of `skills/`

This repository's own skills stop at the RDL/paginated-report boundary:
`skills/claude-skills/rdl-generation/` and
`skills/claude-skills/ssrs-report-creation/` cover writing and deploying
`.rdl` files, and `skills/report-lineage/` covers understanding an existing
SSRS/SSIS estate. None of that overlaps with these four vendored skills,
which start on the other side of a migration: once a report becomes a Power
BI `.pbip`/PBIR project (whether by hand, by this repository's own
`core/generator/rdl_generator.py` inverse path, or by the platform's
Report Studio), these skills cover designing, authoring, and publishing it.

A typical SSRS-to-Fabric flow that uses both halves: fingerprint and
understand the existing RDL estate with `skills/report-lineage/`, decide
per-report whether to keep it paginated or convert it to an interactive
report (guidance in
`skills/claude-skills/ssrs-report-creation/references/migration-to-fabric.md`),
then for the reports being converted, use `powerbi-report-planning` through
`powerbi-report-management` here to build and publish the Power BI version.

## Prerequisites these skills assume

These skills assume tooling this repository does not install or manage:

- Node.js 20+ and the `@microsoft/powerbi-report-authoring-cli` and
  `@microsoft/powerbi-desktop-bridge-cli` npm packages, for
  `powerbi-report-authoring`.
- The Azure CLI (`az`), `jq`, and `az login` access to a Fabric-enabled
  Azure AD tenant, for `powerbi-report-management`.
- Power BI Desktop (Windows) for the reload/screenshot verification loop in
  `powerbi-report-authoring`.
- A semantic-model authoring skill or MCP server (not included here) for the
  model-inspection and model-publish steps that `powerbi-report-planning`
  and `powerbi-report-management` delegate to.

None of this is required to read the skills as reference material; it is
required only to actually run the workflows they describe end to end.

## Keeping this up to date

Each vendored `SKILL.md` carries its own `metadata: version:` and an
"Update Check" note pointing at a `check-updates` skill from the upstream
marketplace, which this vendored copy does not include (it depends on the
live plugin/marketplace machinery, not applicable to a static copy). To
refresh this folder against a newer upstream release, re-fetch the same file
list from `https://raw.githubusercontent.com/microsoft/skills-for-fabric/main/`
(see the path list above) and diff before overwriting, since the one
adaptation noted above (the `common/` relative link depth) needs to be
re-applied to `powerbi-report-management/SKILL.md` after any refresh.
