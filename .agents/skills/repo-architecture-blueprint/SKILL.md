---
name: repo-architecture-blueprint
description: >
  Use this skill when asked to analyze an arbitrary repository — this one or
  any other repo an agent is pointed at — and produce a standing architecture
  document for it: a `<Repo>_Architecture_Blueprint.md` that auto-detects the
  technology stack and architectural pattern, documents components, layers,
  cross-cutting concerns and extension points, and always includes Mermaid
  architecture diagrams and data-flow diagrams (these are mandatory output,
  not optional). It is deliberately stack-agnostic and repo-agnostic so the
  same skill runs unmodified against a second, third, or Nth repository. Based
  on the community "Architecture Blueprint Generator" prompt
  (github/awesome-copilot), restructured into this repository's skill format
  and hardened with an explicit step order, a mandatory-diagrams rule, and a
  documented (not wired) extension point for pushing diagrams to an external
  diagramming/documentation service. Example requests: "analyze this repo and
  document its architecture", "generate an architecture blueprint for
  <repo>", "produce architecture and data-flow diagrams for this codebase",
  "run the architecture blueprint skill against the repo at <path>".
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - Edit
  - AskUserQuestion
triggers:
  - architecture blueprint
  - architecture documentation
  - document the architecture
  - analyze this repo
  - analyze this repository
  - repo analysis
  - codebase analysis
  - architecture diagram
  - data flow diagram
  - dataflow diagram
  - component diagram
  - C4 diagram
  - sequence diagram
  - technology stack detection
  - architectural pattern detection
  - blueprint for new development
  - architectural decision record
---

# Repository Architecture Blueprint

Analyze **any** repository — not just this one — and produce a single durable
document, `<RepoName>_Architecture_Blueprint.md`, that explains how the
codebase is actually built: its stack, its architectural pattern, its
components and data flows, and how to extend it without breaking its own
conventions. This skill is the reusable, repeatable procedure; the blueprint
document is the artifact a team keeps and re-generates as the codebase
evolves.

**Source and lineage.** This skill is adapted from the community
[`architecture-blueprint-generator`](https://github.com/github/awesome-copilot/blob/main/skills/architecture-blueprint-generator/SKILL.md)
prompt in `github/awesome-copilot`. That prompt is a single parametrized
generation instruction; this version keeps its analysis scope and section set
but restructures it into an ordered procedure, makes diagrams and data-flow
documentation **mandatory** rather than a configurable option, and adds an
explicit (documented, not pre-wired) hook for exporting diagrams to an
external diagramming/documentation platform.

**Reusability contract.** Every step below must work without repo-specific
assumptions:

- The **target repository** is an input (a path, or "this repository" when
  run in place) — never hard-coded.
- **Stack and pattern are detected from evidence** (files, imports, configs,
  folder shape), never assumed from a template.
- Nothing in this skill may reference one repository's own specific paths,
  tables, or components as if they were universal. Where an example is
  useful, mark it explicitly as an example.
- Running this skill twice against the same repo, or once each against two
  unrelated repos, must produce the same section structure and the same
  quality bar.

## What you get out of it

One Markdown file, `<RepoName>_Architecture_Blueprint.md`, containing:

1. A stack and architectural-pattern summary, with the evidence for each claim.
2. **Mermaid architecture diagrams** (mandatory) at more than one level of
   abstraction.
3. **Mermaid data-flow diagrams** (mandatory) showing how information actually
   moves through the system.
4. Per-component documentation: purpose, internal structure, interaction
   patterns, extension points.
5. Layering, cross-cutting concerns (auth, error handling, logging,
   validation, configuration), and service-communication patterns.
6. A "blueprint for new development" section — where a new feature starts,
   what template to follow, and what pitfalls to avoid.
7. A generation footer: when it was produced, from what commit, and how to
   regenerate it.

Plus, up front, the questions needed to fix the analysis scope (see
[Step 0](#step-0-fix-the-scope-ask-do-not-assume)) — expect those before the
document, not after.

## The order of work — do not skip or reorder

0. **Fix the scope.** Confirm the target repo/path, and the configuration
   knobs below, before analyzing anything.
1. **Detect the stack and architectural pattern** from evidence in the repo.
2. **Map the repo to components** — folders/modules/services and their
   boundaries.
3. **Produce the diagrams** — architecture diagrams and data-flow diagrams are
   both required output of this step, not later add-ons.
4. **Document each component**, the layering, and cross-cutting concerns.
5. **Write the extension/blueprint section** for new development.
6. **Assemble and save** `<RepoName>_Architecture_Blueprint.md`, with the
   generation footer.
7. **Offer the optional external-diagramming hook** (see
   [Step 7](#step-7-optional-external-diagram-tool-hand-off)) — never wire it
   without the user naming the target tool and confirming access.

## Step 0: fix the scope — ask, do not assume

Before reading a single file, confirm:

- **Target repository**: a path already open in the workspace, a path to
  clone/attach, or "this repository." Never infer a different repo than the
  one named.
- **Output location**: default is the target repo's root as
  `<RepoName>_Architecture_Blueprint.md`; confirm if the user wants it
  elsewhere (a docs folder, a separate wiki repo, a scratch path).
- **Detail level**: `High-level` | `Detailed` | `Comprehensive` |
  `Implementation-Ready`. Default to `Detailed` if unstated.
- **Diagram notation**: `C4` | `UML` | `Flowchart` — all rendered as Mermaid
  code blocks so they show inline on GitHub/GitLab/most Devin surfaces without
  a plugin. There is no "no diagrams" option; see
  [Diagrams are mandatory](#diagrams-and-data-flows-are-mandatory).
- **Include code examples?** (illustrative snippets pulled from the repo,
  not invented) and **include architectural decision records?** — both
  default to yes unless the user says otherwise.
- **Sensitive-data posture**: if the repo is private/internal, confirm before
  the blueprint (or its code examples) leaves the workspace — e.g. before
  pasting sections into an external chat, ticket, or diagramming service. This
  skill does not itself restrict where the output goes; the person invoking
  it is accountable for that call, per whatever data-handling policy applies
  to the repo in question.

Batch these into one round of questions, the way other skills in this family
do — do not trickle them out one at a time.

## Step 1: detect the stack and architectural pattern

Read, don't assume. Evidence sources, roughly in order of reliability:

- **Manifests and lockfiles**: `package.json`, `*.csproj`/`*.sln`, `pom.xml`,
  `build.gradle`, `requirements.txt`/`pyproject.toml`, `go.mod`, `Gemfile`,
  `pubspec.yaml`. These name the stack directly.
- **Entry points and framework fingerprints**: `Program.cs`/`Startup.cs`,
  `manage.py`, `main.go`, `index.js`/`server.js`, `App.tsx`, Spring
  `@SpringBootApplication`, Django `settings.py`.
- **Folder shape and naming**: layered folders (`Controllers/`,
  `Services/`, `Repositories/`), feature-folder layout, a `packages/`
  monorepo layout, `apps/`+`libs/` (Nx-style), domain-driven folders
  (`domain/`, `application/`, `infrastructure/`).
  This repository's own `skills/`, `mcp/`, `docs/` split is one example of a
  folder-shape signal — treat it as an example of the technique, not as a
  pattern to expect elsewhere.
- **Dependency direction**: which layer imports which. A layer that is
  imported by everything and imports nothing is almost always the domain
  core; reversing that expectation is itself a finding worth documenting.
- **Build/deploy config**: Dockerfiles, CI workflows, IaC (Bicep/Terraform/CDK),
  `docker-compose.yml` service graphs — these reveal deployment topology and
  often the true service boundaries better than the source tree does.

From this, state:

- The **primary stack(s)** — a repo can be polyglot; list each with its role
  (frontend, backend, infra scripts, etc.), not just the dominant one.
- The **architectural pattern(s)** — Clean Architecture, Layered, MVC, MVVM,
  Hexagonal, Microservices, Event-Driven, Serverless, Monolithic, or a hybrid.
  Name the hybrid explicitly rather than forcing a single label; most real
  codebases are one.
- The **guiding principles actually enforced** — e.g. "no controller may
  reference a repository directly, only a service interface" — evidenced by
  actual imports, not the pattern's textbook definition.

If the evidence is ambiguous or conflicting (e.g. the folder names say
"Clean Architecture" but a controller imports a repository directly), report
the conflict as a finding rather than picking the more flattering label.

## Step 2: map the repo to components

Enumerate the top-level components (services, modules, packages, layers) and
for each record its **boundary** — what is inside vs. outside it — from
actual dependency edges, not from the name of its folder. A `Services/`
folder that is imported by nothing is not a component boundary; a package
that half the repo imports without an interface in front of it is a de facto
shared-kernel, whatever it's named.

Where a boundary is enforced by tooling (a lint rule, a build-time dependency
graph check, a separate compiled assembly/package), say so — that is stronger
evidence than a naming convention.

## Step 3: produce the diagrams

### Diagrams and data flows are mandatory

Every blueprint this skill produces includes, at minimum:

1. **A system/context diagram** — the repo's boundary, its external
   dependencies (databases, third-party APIs, message brokers, other
   services), and the actors that use it.
2. **A component/container diagram** — the components from Step 2 and the
   relationships between them (calls, imports, publishes/subscribes to).
3. **At least one data-flow diagram** — how a representative unit of data (a
   request, a message, a batch record) actually moves from entry to
   persistence/exit, through which components, transformed how, at each hop.
   For a repo with more than one materially different flow (e.g. a
   synchronous API path and an async ingestion path), diagram each
   separately rather than merging them into one diagram that represents
   neither faithfully.

There is no `DIAGRAM_TYPE = "None"` option in this skill — the source prompt
allows omitting diagrams; this variant deliberately does not, because a
blueprint without a diagram of the actual data flow is the part most likely
to go stale unnoticed and least likely to be independently checked.

### Notation

Render every diagram as a fenced ` ```mermaid ` code block so it renders
inline wherever the blueprint is viewed, with no external renderer required:

- `graph TD`/`graph LR` for component and context diagrams.
- `sequenceDiagram` for a request/response or message-driven data flow.
- `flowchart` for a multi-step batch/ETL-style data flow with branching.
- `classDiagram` only where the detail level is `Implementation-Ready` and a
  domain model is genuinely central to the architecture.

Diagrams must reflect **actual implementation**, traced from real
dependencies and call sites — not the idealized shape the pattern's name
implies. If the real dependency graph has a cycle or a layering violation,
draw it that way and call it out as a finding; smoothing it into a clean
diagram hides the thing a blueprint exists to surface.

## Step 4: document each component, layering, and cross-cutting concerns

For every component from Step 2:

- **Purpose and responsibility** — what it does, what business or technical
  concern it owns, and its explicit non-goals.
- **Internal structure** — key classes/modules, the design patterns actually
  used (not patterns that could theoretically apply).
- **Interaction patterns** — how it talks to others: direct calls, DI'd
  interfaces, events published/consumed, message queues.
- **Extension points** — how the component is meant to grow (plugin
  registration, configuration-driven behavior, an abstract base a new
  variant subclasses).

Then, across the whole repo:

- **Layering and dependency rules** — the allowed direction of dependency
  between layers, and any violation found.
- **Data architecture** — domain model shape, entity relationships, data
  access pattern (repository, active record, raw query), caching, validation.
- **Cross-cutting concerns**: authentication/authorization, error handling
  and resilience (retries, circuit breakers, fallbacks), logging/observability,
  input and business-rule validation, configuration and secrets management.
  Document each as it is actually implemented in this repo, with a file
  reference, not as a generic checklist description.
- **Service communication patterns** (if more than one service/process is
  involved): protocols, sync vs. async, versioning, discovery, resilience.
- **Testing architecture**: test boundaries (unit/integration/system), test
  doubles, fixtures/test data strategy.
- **Deployment architecture**: topology, environment-specific config,
  containerization/orchestration, cloud service integration — derived from
  the build/deploy config found in Step 1, not guessed.

## Step 5: write the blueprint for new development

This is the section a developer opens before writing new code. Include:

- **Development workflow**: for a new feature of type X, where does work
  start, in what order are components touched, what tests are expected.
- **Implementation templates**: the base class/interface a new component of
  each kind should follow, referencing a real example already in the repo.
- **Common pitfalls**: architecture violations seen elsewhere in the repo
  that a new contributor is likely to repeat, and how to avoid them.

Include architectural decision records here too, when requested in Step 0:
for each significant decision evidenced in the codebase, the context that
forced it, the alternatives the code's own history/comments suggest were
considered, and the resulting trade-off.

## Step 6: assemble and save

Write `<RepoName>_Architecture_Blueprint.md` at the confirmed output
location, with this section order:

```
1. Overview & Detected Stack
2. Architectural Pattern
3. Architecture Diagrams          (Mermaid, mandatory)
4. Data Flow Diagrams             (Mermaid, mandatory)
5. Components
6. Architectural Layers & Dependencies
7. Data Architecture
8. Cross-Cutting Concerns
9. Service Communication Patterns  (if applicable)
10. Testing Architecture
11. Deployment Architecture
12. Extension & Evolution Patterns
13. Architectural Decision Records  (if requested)
14. Blueprint for New Development
15. Generation Footer
```

The **generation footer** is not optional: date generated, the commit SHA the
analysis was run against, the detail level used, and a one-line note on how
to regenerate it (re-run this skill against the same repo). A blueprint
without a footer looks current forever and is the thing most likely to be
trusted past its expiry.

## Step 7: optional external diagram-tool hand-off

Some teams want the Mermaid diagrams this skill produces pushed into a
separate diagramming or documentation platform (a hosted diagram tool,
a wiki, an internal portal — referred to by some teams by product names such
as "Pro.io"-style diagram services, Structurizr, PlantUML server, or
Lucidchart/diagrams.net). **This skill does not integrate with any specific
one of these today** — no such integration has been confirmed or configured
in this repository — but the hand-off point is designed to be a clean one:

- The Mermaid source for every diagram is kept in its own fenced block in the
  blueprint, so it can be lifted out and re-rendered by another tool without
  re-deriving it.
- If a specific target platform is named, treat wiring it up as a separate,
  explicit step: confirm the tool, its API or import format, and any
  credentials needed, before sending anything to it — the same rule this
  family of skills applies to any external hand-off. Do not guess an
  integration's name or API shape; ask, or mark the section `TODO: confirm
  <tool>` in the generated blueprint and move on.

## Reviewing what was produced

Before treating the blueprint as done:

1. **Every diagram traces to a real file or dependency edge**, not to the
   pattern's textbook shape. Spot-check two or three edges against the code.
2. **The data-flow diagram(s) match an actual code path** — pick one request
   or message and follow it through the diagram and the source side by side.
3. **The footer is present** and the commit SHA matches what was analyzed.
4. **Nothing in the document was invented** — a component, a pattern, or a
   cross-cutting concern that isn't actually there is worse than an omission,
   because it will be trusted.

## Known limits

- **This is a reading of the code, not a certification of it.** Detecting a
  pattern by evidence is still an inference; state confidence where the
  evidence is thin instead of asserting a pattern the repo only partially
  follows.
- **Diagrams are Mermaid, not a specific enterprise diagramming standard.**
  Step 7 is the documented seam for anyone who needs a different renderer;
  it is deliberately not pre-wired to one.
- **Large monorepos** may need the scope narrowed (Step 0) to a
  sub-tree/package rather than the whole tree, or the component list and
  diagrams become too coarse to be useful.

## Related material in this repository

- `skills/claude-skills/repo-architecture-blueprint/DEVIN-USAGE.md` — the
  human setup-and-run guide for this skill on Devin Chat, Desktop, and CLI.
- `skills/ide-references/devin/repo-architecture-blueprint.knowledge.md` —
  the condensed Devin knowledge entry for this skill.
- `.agents/skills/repo-architecture-blueprint/SKILL.md` — the discovery stub
  Devin's skill loader actually scans; keep it in sync with this file.
