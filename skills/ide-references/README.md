# IDE and agent reference variants

The primary skills in `skills/claude-skills/` use the Anthropic SKILL.md format
(YAML frontmatter, triggers, bundled references and scripts). Not every tool
reads that format. This folder holds the same guidance reshaped for other
environments, so the same knowledge is usable in VS Code, Devin, Cursor, and
GitHub Copilot without a Claude-specific harness.

Every file here is a derived, condensed view. The full detail (all reference
docs, working examples, the builder and validator scripts, the MCP servers)
lives only in `skills/claude-skills/rdl-generation/`,
`skills/claude-skills/ssrs-report-creation/`, and
`skills/claude-skills/sql-server-schema/`. When a variant below references a
path like `references/expressions.md`, it means the file at
`skills/claude-skills/rdl-generation/references/expressions.md`.

| Folder | For | Format |
| --- | --- | --- |
| `vendor-neutral/` | Any human reader, or any tool that just ingests markdown | Plain playbook, no frontmatter |
| `devin/` | Devin AI's knowledge base | Trigger plus content, one concern per entry |
| `cursor/` | Cursor | `.cursorrules` |
| `copilot/` | GitHub Copilot (VS Code) | `copilot-instructions.md` |

## Keeping these in sync

These are generated views, not a second source of truth. If the underlying
SKILL.md or its references change materially (a new rule, a corrected
namespace, a changed API call), update this folder's condensed copies too.
Treat drift here as a documentation bug, not a functional one: the worst case
is stale advice, not a broken build.

One hard rule for the SQL Server variants: they describe the **static**
`sql-server-schema` skill only, which contains no customer data. Never derive
an IDE reference from a *generated* schema pack. Those name internal servers,
databases, tables and columns, and this folder is committed to a public
repository.
