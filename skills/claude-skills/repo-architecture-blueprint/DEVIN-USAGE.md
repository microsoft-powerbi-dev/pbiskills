# Using this skill in Devin

A human setup-and-run guide for `repo-architecture-blueprint` on Devin Chat,
Devin Desktop, and Devin CLI. `SKILL.md` is written for the agent; this file
is written for you.

**Tag legend** (same convention as
`docs/devin-windsurf-microsoft-skills-integration.md`): `TESTED` = run on this
machine, `FILE` = read from files in this repository, `DOCS` = read in
Devin's published documentation, `VERIFY` = plausible but must be confirmed on
your actual Devin seat before you rely on it.

---

## What you get out of it

Point Devin at any repository — this one, or a completely unrelated one it
has access to — and ask it to run this skill. You get back one file:

```
<RepoName>_Architecture_Blueprint.md
```

containing the detected stack and architectural pattern, Mermaid architecture
diagrams, Mermaid data-flow diagrams, per-component documentation, layering
and cross-cutting-concern notes, and a "blueprint for new development"
section — plus a generation footer naming the commit it was run against.

Diagrams and at least one data-flow diagram are **mandatory** output of this
skill — if Devin produces a blueprint with no diagrams, it did not follow the
skill; see [Troubleshooting](#troubleshooting).

---

## Install

### Option A — repo-vendored skill (recommended, works for Chat, Desktop and CLI)

Devin has no skill marketplace, so skills are vendored into the repository at
the path Devin scans. `DOCS` This repo already carries the copy:

```
.agents/skills/repo-architecture-blueprint/SKILL.md
```

Commit that path to the branch Devin is working on and it is discovered
automatically. This skill needs **no MCP server** and **no secrets** — it
only reads files and writes one Markdown document.

The canonical copy lives at
`skills/claude-skills/repo-architecture-blueprint/SKILL.md`. The
`.agents/skills/` copy is the discovery stub.

**The constraint that matters:** Devin's skill loader reads **`SKILL.md`
only**. `DOCS`/`VERIFY` Because this skill is intentionally self-contained
(no `references/` or `scripts/` beside it), that constraint doesn't cost you
anything extra here — unlike skills in this repo that split content into
`references/`.

### Option B — run it against a repo Devin doesn't have vendored into

Because this skill is stack-agnostic and repo-agnostic by design, you don't
need to vendor it into *every* target repository. Two ways to use it against
a repo that doesn't carry the skill file itself:

1. **Paste `SKILL.md` directly into the session/chat** and tell Devin which
   repository (path, or already-open workspace) to run it against.
2. **Keep it vendored in one "toolbox" repo** Devin has access to, and ask it
   to "use the repo-architecture-blueprint skill from `<toolbox-repo>` against
   `<target-repo>`." `VERIFY` this cross-repo reference against your actual
   Devin seat/workspace configuration — behavior may depend on how multiple
   repos are attached to one session.

### Option C — Devin Desktop beta skill

`VERIFY` — confirm the exact menu path against your Desktop build.

1. Open Devin Desktop → Settings → Skills (beta).
2. Add a skill, paste the contents of
   `skills/claude-skills/repo-architecture-blueprint/SKILL.md`.
3. Keep the YAML front matter intact — `name`, `description`, and `triggers`
   are what let Devin auto-select the skill instead of you invoking it by
   name every time.

### Option D — Devin CLI

`.agents/skills/` is on the CLI's discovery path, as is `.windsurf/skills/`.
`DOCS` Option A covers it; no extra step.

---

## Invoking it

| Surface | How |
| --- | --- |
| Devin Chat / Desktop | `@skills:repo-architecture-blueprint`, or describe the task — the `description`/`triggers` front matter is written for auto-selection |
| Devin CLI | `/repo-architecture-blueprint` |
| Any | "analyze this repo and document its architecture", "generate an architecture blueprint", "give me architecture and data-flow diagrams for `<repo>`" |

**Say which repository up front**, even when it seems obvious:

> Run the repo-architecture-blueprint skill against `<path or repo name>`.
> Detail level: Detailed. Include code examples and decision records.
> Save the output to `<path>`.

That answers Step 0 in one message instead of a back-and-forth.

---

## Expect to be asked things

Per `SKILL.md` Step 0, before analysis starts Devin should confirm:

1. **Target repository** — which one, if it isn't unambiguous.
2. **Output location** — default is the target repo's root.
3. **Detail level** — High-level / Detailed / Comprehensive / Implementation-Ready.
4. **Diagram notation** — C4 / UML / Flowchart (all rendered as Mermaid regardless).
5. **Include code examples / decision records?**
6. **Sensitive-data posture** — if the target repo is private/internal, confirm
   before any part of the blueprint leaves the workspace (pasted elsewhere,
   sent to an external tool, etc.).

Pre-answering these in your opening message (as in the example above) cuts
the round-trip to zero.

---

## A session, end to end

1. **Point Devin at the repo** and answer the Step 0 questions.
2. **Devin detects stack + pattern** from manifests, entry points, folder
   shape, and dependency direction — not from a template.
3. **Devin produces the diagrams first**, before writing prose sections — the
   skill treats diagrams as Step 3, ahead of per-component documentation.
4. **Devin writes the full blueprint** and saves
   `<RepoName>_Architecture_Blueprint.md`.
5. **You review it** — see [Reviewing what Devin produced](#reviewing-what-devin-produced).
6. **Re-run later** to refresh it; the generation footer's commit SHA is what
   tells you how stale the copy you're reading is.

---

## Reviewing what Devin produced

In order of how much time each check saves you:

1. **Are there diagrams at all**, and is there at least one data-flow
   diagram? If not, the skill wasn't followed — see Troubleshooting.
2. **Spot-check one diagram edge against the actual code.** Pick a
   component-to-component arrow and confirm the import/call really exists.
3. **Follow one real request or message through the data-flow diagram and the
   source side by side.** This is the check most likely to catch an invented
   flow.
4. **Check the generation footer** — date, commit SHA, detail level. Absent
   footer, or a SHA that doesn't match the branch you're on, means treat the
   document as unverified.
5. **Look for invented content** — a cross-cutting concern or pattern
   described that you don't recognize in the actual codebase.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| No diagrams in the output | The skill wasn't loaded, or Devin treated diagrams as optional | Confirm `.agents/skills/repo-architecture-blueprint/SKILL.md` is on the branch Devin is working on; re-ask explicitly for "Mermaid architecture and data-flow diagrams, which are mandatory per the skill" |
| Diagram doesn't match the code | Devin drew the textbook shape of the named pattern instead of the actual dependency graph | Ask it to re-derive the diagram from actual imports/dependency edges, and to call out any deviation from the pattern's ideal shape as a finding |
| Devin asks about a "Pro IO"/external diagram tool integration that doesn't exist | Step 7 of the skill is a documented, not pre-wired, hand-off point | Confirm no such integration is configured; either name the real target tool so it can be wired up as a separate task, or tell Devin to leave the Mermaid source as the deliverable |
| Blueprint has no generation footer | Section 15 was skipped | Ask Devin to add it: date, commit SHA, detail level, regeneration note |
| Devin analyzed the wrong repo | Step 0 scope wasn't confirmed | Restate the target repo path explicitly in your next message |

---

## Sensitive/internal repositories: read this before you share the output

This skill only **reads** the target repository and **writes** one Markdown
file; it does not query a database, call an external service, or transmit
anything by itself. The residual risk is entirely in what a human does with
the resulting document next:

- If the target repo is private/internal, the blueprint (including any
  pulled code examples) is only as safe as wherever you send it next —
  pasting it into an external chat, ticket system, or a hosted diagramming
  tool publishes it there. Confirm that's intended before doing it (see
  Step 0's sensitive-data posture question).
- Code examples pulled into the blueprint are illustrative snippets from the
  repo, never invented — but they are still verbatim source. Treat them with
  the same handling rules as the rest of the codebase.
- Nothing about this skill grants it (or Devin) write access to anything
  beyond the one blueprint file it produces.
