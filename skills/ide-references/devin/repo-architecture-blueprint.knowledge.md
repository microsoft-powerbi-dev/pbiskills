# Devin knowledge: repository architecture blueprint generation

## Trigger

Apply this knowledge whenever asked to analyze a repository — this one, or
any other repo attached to the session — and produce architecture
documentation for it: a stack/pattern summary, architecture diagrams, data
flow diagrams, per-component documentation, or a "how to extend this
codebase" section. Also apply it when a request names an architecture
blueprint, a data-flow diagram, or a component diagram without naming a
generation method. The full skill is at
`skills/claude-skills/repo-architecture-blueprint/SKILL.md`; setup and usage
notes for Devin specifically are in that folder's `DEVIN-USAGE.md`.

## Content

Work the steps in order and do not collapse them: fix the scope, detect the
stack and pattern from evidence, map the repo to components, produce the
diagrams, document each component plus layering and cross-cutting concerns,
write the extension/blueprint section, then assemble and save
`<RepoName>_Architecture_Blueprint.md`.

**Diagrams are mandatory, not a configurable option.** The skill this
knowledge entry points to is adapted from a community prompt
(`architecture-blueprint-generator` in `github/awesome-copilot`) that allows a
"no diagrams" mode; this repository's version removes that option
deliberately. Every blueprint must include a system/context diagram, a
component diagram, and at least one data-flow diagram that traces a real
request or message through the actual code, all rendered as Mermaid so they
render inline without a plugin. A blueprint produced without at least one
data-flow diagram did not follow the skill.

**Detect, never template.** Stack and architectural pattern come from
manifests, entry points, folder shape, and actual dependency direction — never
from assuming the pattern a folder name implies. If the evidence conflicts
(folder names say one pattern, imports show another), report the conflict as
a finding rather than picking the more flattering label. This applies with
equal force to a diagram: draw the dependency graph that is actually there,
including a cycle or a layering violation if one exists, rather than the
clean shape the named pattern would predict.

**This skill is repo-agnostic by construction**, which is the point of it:
the same procedure runs unmodified against a second or third repository. Fix
the target repository, the output location, the detail level
(High-level/Detailed/Comprehensive/Implementation-Ready), the diagram
notation (C4/UML/Flowchart — all rendered as Mermaid regardless), and whether
to include code examples and decision records, in one batch of questions
before analysis starts, the same way other skills in this family front-load
their clarifying questions rather than trickling them out.

**The generated document always ends with a footer** — generation date, the
commit SHA analyzed, the detail level used, and how to regenerate it. A
blueprint without that footer looks perpetually current and is the one most
likely to be trusted past the point the code has moved past it.

**External diagram-tool hand-off is a documented seam, not a live
integration.** Some teams want the Mermaid diagrams pushed into a separate
hosted diagramming or documentation platform. No such integration exists or
is configured in this repository today. The skill keeps every diagram's
Mermaid source in its own fenced block specifically so it can be lifted out
and re-rendered elsewhere, but do not name or wire up a specific external
tool (a "Pro.io"-style diagram service, Structurizr, PlantUML server,
Lucidchart/diagrams.net, or otherwise) unless the user names the actual
target and confirms access — mark it `TODO: confirm <tool>` in the blueprint
and move on rather than guessing an integration's shape.

**Only reads, only writes one file.** This skill has no database access, no
MCP server, and no secrets requirement; it reads the target repository's
files and writes one Markdown document. The residual risk is entirely in what
a human does with the output afterward — if the target repo is
private/internal, confirm before any part of the blueprint (including pulled
code examples, which are verbatim source) is pasted into an external chat,
ticket, or hosted tool. That confirmation is one of the Step 0 questions the
skill asks up front, not an afterthought.
