# SkillsMCP: Agent Skills and MCP Server Library

**A reusable library of Agent Skills, MCP servers, and vendor-neutral playbooks
for Power BI, SSRS, SQL Server, and Python data-engineering work.**

Everything in here is designed to be *imported*, not read once and forgotten.
Point an agentic coding tool (Devin, Claude Code, Cowork, Windsurf, Cursor,
GitHub Copilot) at a folder in this repository and it gains a specific,
documented capability: generate RDL XML, interrogate a SQL Server schema,
turn a mapping spreadsheet into a working extract pipeline, plan and author a
Power BI report, or document a repository's architecture.

This repository is the **library**. The consuming project is yours.

---

## Table of contents

1. [What you get](#1-what-you-get)
2. [Repository map](#2-repository-map)
3. [Skill catalog](#3-skill-catalog)
4. [MCP server catalog](#4-mcp-server-catalog)
5. [Runnable Python tooling](#5-runnable-python-tooling)
6. [Step-by-step: import into Devin](#6-step-by-step-import-into-devin)
7. [Step-by-step: import into other hosts](#7-step-by-step-import-into-other-hosts)
8. [Step-by-step: wire up an MCP server](#8-step-by-step-wire-up-an-mcp-server)
9. [Prerequisite matrix](#9-prerequisite-matrix)
10. ["I want to..." quick starts](#10-i-want-to-quick-starts)
11. [Documentation index](#11-documentation-index)
12. [Data-safety rules](#12-data-safety-rules)
13. [Adding a skill to this library](#13-adding-a-skill-to-this-library)
14. [Provenance and licensing](#14-provenance-and-licensing)

---

## 1. What you get

| Count | What | Where |
| --- | --- | --- |
| **7** | First-party Agent Skills (Anthropic `SKILL.md` format) | [skills/claude-skills/](skills/claude-skills/) |
| **4** | Vendored Microsoft Power BI skills (MIT) | [skills/vendor/microsoft-fabric/](skills/vendor/microsoft-fabric/) |
| **4** | MCP servers (2 run standalone, 2 are reference copies) | [mcp/](mcp/) |
| **1** | Standalone `pip install`-able Python package | [skills/report-lineage/](skills/report-lineage/) |
| **11** | Vendor-neutral / Devin / Cursor / Copilot reference variants | [skills/ide-references/](skills/ide-references/) |
| **13** | Background and component-reference documents | [docs/](docs/) |

Domains covered: **Power BI** (PBIP/PBIR authoring, design, planning,
workspace management), **SSRS / paginated reporting** (RDL generation,
report creation, estate lineage), **SQL Server** (read-only schema
interrogation, metadata-driven extracts, T-SQL view generation), **Python**
(extract pipelines, OpenAPI and JSON-schema readers, lineage graphs), and
**general engineering** (architecture blueprints, API-to-MongoDB mapping).

---

## 2. Repository map

```
skills/
  claude-skills/        7 first-party skills, each a self-contained folder:
                        SKILL.md + references/ + examples/ + scripts/ + tests/
  vendor/
    microsoft-fabric/   4 verbatim MIT skills from microsoft/skills-for-fabric
  ide-references/       The same knowledge reshaped for tools that do not read
                        SKILL.md: vendor-neutral/, devin/, cursor/, copilot/
  report-lineage/       A standalone pip package (not a skill): SSRS/SSIS
                        estate lineage, duplicate detection, NL search
  ASSESSMENT.md         The capability assessment behind all of the above

mcp/                    4 MCP server entry points + mcp/README.md
.agents/skills/         Devin/Windsurf discovery stubs (see section 6)
.mcp.json               Claude Code MCP registration for this repo
.vscode/mcp.json        VS Code MCP registration, with prompted inputs
docs/                   Component guide + background reading
SkillsMCP_Architecture_Blueprint.md
                        Architecture, diagrams, ADRs for this repository
```

---

## 3. Skill catalog

Every first-party skill folder follows the same shape, so once you have read
one you can navigate all of them:

```
<skill-name>/
  SKILL.md          The agent-facing contract: frontmatter (name, description,
                    allowed-tools, triggers) plus the procedure. Read first.
  references/*.md   Deep reference docs, loaded on demand by the agent
  examples/         Working inputs and expected outputs
  scripts/          Runnable Python (CLI and/or MCP server)
  tests/            pytest, runnable without a database where possible
  DEVIN-USAGE.md    A human setup-and-run guide, where one exists
```

### 3.1 Power BI and SSRS reporting

| Skill | What it does | Entry point | Devin-ready |
| --- | --- | --- | --- |
| **rdl-generation** | Writes SSRS RDL 2016 XML from a spec: skeleton, namespaces, DataSets, Fields, ReportParameters, Tablix (flat/grouped/matrix), Charts, subreports, headers/footers, plus a pre-flight validator. Ships a dependency-free builder, validator, MCP server, and 5 validating example `.rdl` files. | [SKILL.md](skills/claude-skills/rdl-generation/SKILL.md) · [scripts/](skills/claude-skills/rdl-generation/scripts/) · [examples/](skills/claude-skills/rdl-generation/examples/) | [KB entry](skills/ide-references/devin/rdl-generation.knowledge.md) |
| **ssrs-report-creation** | End-to-end paginated report delivery: requirements, query design, dataset and parameter modelling, layout patterns, drillthrough, export constraints, deployment via REST v2.0 / `rs.exe`, permissions, performance tuning, and when to move to Power BI instead. Documentation-only, no scripts. | [SKILL.md](skills/claude-skills/ssrs-report-creation/SKILL.md) · [references/](skills/claude-skills/ssrs-report-creation/references/) | [KB entry](skills/ide-references/devin/ssrs-report-creation.knowledge.md) |
| **powerbi-report-planning** *(vendored)* | Guided Define → Inspect → Spec → Approve → Build → Validate → Publish workflow for a new report or dashboard. | [SKILL.md](skills/vendor/microsoft-fabric/powerbi-report-planning/SKILL.md) | Vendor SKILL.md |
| **powerbi-report-design** *(vendored)* | Design decisions before any file is written: tone, page archetypes, chart selection, layout, color, typography, accessibility. Produces a design contract, never PBIR. | [SKILL.md](skills/vendor/microsoft-fabric/powerbi-report-design/SKILL.md) | Vendor SKILL.md |
| **powerbi-report-authoring** *(vendored)* | PBIR/PBIP file mechanics: pages, visuals, filters, slicers, bookmarks, themes, formatting, validation, Desktop reload and screenshots. | [SKILL.md](skills/vendor/microsoft-fabric/powerbi-report-authoring/SKILL.md) | Vendor SKILL.md |
| **powerbi-report-management** *(vendored)* | Report item CRUD against the Fabric REST API via `az rest`: create from PBIR, get/download definitions, update, list, delete. | [SKILL.md](skills/vendor/microsoft-fabric/powerbi-report-management/SKILL.md) | Vendor SKILL.md |

The four vendored skills form a closed loop: planning routes to design, design
hands off to authoring, management transports the result. They need external
tooling (Node.js CLIs, `az` CLI). See
[skills/vendor/microsoft-fabric/README.md](skills/vendor/microsoft-fabric/README.md).

### 3.2 SQL Server and data engineering

| Skill | What it does | Entry point | Devin-ready |
| --- | --- | --- | --- |
| **sql-server-schema** | The one piece that reaches a live database. Connects to on-premises SQL Server with Windows Integrated Auth over pyodbc, reads schema from `sys.*` catalog views, classifies facts and dimensions, builds a declared-plus-inferred join graph, traces an RDL dataset back to its source tables, and emits a portable digest plus a generated per-database skill pack. **Read-only by construction**: no write tool, no flag that adds one. | [SKILL.md](skills/claude-skills/sql-server-schema/SKILL.md) · [scripts/cli.py](skills/claude-skills/sql-server-schema/scripts/cli.py) · [MCP server](skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py) | [KB entry](skills/ide-references/devin/sql-server-schema.knowledge.md) |
| **extract-engine** | A metadata-driven SQL Server extract engine: reads feeds, datasets and field mappings from `meta.*` tables (authored in Excel) and produces pipe-delimited flat files via two interchangeable backends (in-process Polars, or a generated SQL Server view) required to produce byte-identical output. Includes checkpoint/resume, a write-statement allow-list, and a rule catalog that renders each transform as both a Polars expression and a T-SQL fragment. | [SKILL.md](skills/claude-skills/extract-engine/SKILL.md) · [RUNBOOK.md](skills/claude-skills/extract-engine/RUNBOOK.md) · [DEVELOPER.md](skills/claude-skills/extract-engine/DEVELOPER.md) | No KB entry yet |
| **mapping-driven-loader-pipeline** | Turns a mapping spreadsheet into a working extract pipeline: reads the workbook, writes a reviewable transformation-mapper Markdown artifact, then generates generator-style Python (one directory per output file) that extracts from SQL Server, applies the mapped transforms, writes a pipe-delimited flat file, and splits output by record count and/or byte size. Reuses `extract-engine` patterns rather than inventing new ones. | [SKILL.md](skills/claude-skills/mapping-driven-loader-pipeline/SKILL.md) · [DEVIN-USAGE.md](skills/claude-skills/mapping-driven-loader-pipeline/DEVIN-USAGE.md) | Yes: [stub](.agents/skills/mapping-driven-loader-pipeline/SKILL.md) + [KB](skills/ide-references/devin/mapping-driven-loader-pipeline.knowledge.md) |

### 3.3 API, Python, and general engineering

| Skill | What it does | Entry point | Devin-ready |
| --- | --- | --- | --- |
| **api-mongodb-mapping** | Documents an API's fields against a MongoDB target field by field, producing **one reviewable mapping document and nothing else**, no code. Handles internal APIs (scanned from repo routes and models, or a committed OpenAPI file) and external ones (spec, Postman export, or sample payloads). Reads the Mongo side from a `$jsonSchema` validator, schema-defining code (Mongoose, Beanie, Spring Data, C# driver), or inferred samples with stated confidence. Never invents a field, never silently resolves a mismatch. | [SKILL.md](skills/claude-skills/api-mongodb-mapping/SKILL.md) · [DEVIN-USAGE.md](skills/claude-skills/api-mongodb-mapping/DEVIN-USAGE.md) | Yes: [stub](.agents/skills/api-mongodb-mapping/SKILL.md) + [KB](skills/ide-references/devin/api-mongodb-mapping.knowledge.md) |
| **repo-architecture-blueprint** | Analyzes any repository and produces a standing `<Repo>_Architecture_Blueprint.md`: auto-detected stack and architectural pattern, components, layers, cross-cutting concerns, extension points, ADRs, and **mandatory** Mermaid architecture and data-flow diagrams. Stack-agnostic and repo-agnostic, so it runs unmodified against the next repository. | [SKILL.md](skills/claude-skills/repo-architecture-blueprint/SKILL.md) · [DEVIN-USAGE.md](skills/claude-skills/repo-architecture-blueprint/DEVIN-USAGE.md) · [example output](SkillsMCP_Architecture_Blueprint.md) | Yes: [stub](.agents/skills/repo-architecture-blueprint/SKILL.md) + [KB](skills/ide-references/devin/repo-architecture-blueprint.knowledge.md) |

---

## 4. MCP server catalog

Start with [mcp/README.md](mcp/README.md) for the full tool-by-tool reference.

| Server | Runs standalone? | Tools | Needs |
| --- | --- | --- | --- |
| [rdl_generation_server.py](mcp/rdl_generation_server.py) | **Yes** | `get_json_spec_schema`, `build_rdl`, `validate_rdl_file`, `field_expression`, `list_reference_topics` / `get_reference`, `list_examples` / `get_example` | Nothing. Python 3.9+ stdlib; `fastmcp` only to serve over stdio. Thin launcher for [rdl-generation/scripts/mcp_server.py](skills/claude-skills/rdl-generation/scripts/mcp_server.py) |
| [sqlserver_schema_server.py](mcp/sqlserver_schema_server.py) | **Yes**, with a driver | 15 read-only tools: `mssql_list_odbc_drivers`, `mssql_test_connection`, `mssql_list_databases` / `_schemas` / `_tables`, `mssql_describe_table`, `mssql_list_relationships` / `_indexes`, `mssql_table_stats`, `mssql_list_programmability`, `mssql_get_definition`, `mssql_run_query`, `mssql_build_schema_digest`, `mssql_generate_skill`, `mssql_get_reference` | `pip install pyodbc fastmcp sqlglot`, a Microsoft ODBC driver, and Windows Integrated Auth to the target. Thin launcher for [sqlserver_schema_mcp.py](skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py) |
| [report_studio_server.py](mcp/report_studio_server.py) | **No, reference only** | `list_grounding`, `get_ir_schema`, `validate_ir`, `render_report`, `generate_from_rdl`, `batch_generate`, `fix_pbip` | A backend `app.core.*` tree not included here. See [docs/minimal-backend-keep-set.md](docs/minimal-backend-keep-set.md) |
| [pbi_refine_server.py](mcp/pbi_refine_server.py) | **No, reference only** | `validate_dax`, `get_schema`, `get_visual_config`, `apply_patch`, `export_page_png`, `export_visual_png`, `compare_visuals`, `structural_compare` | The same backend tree |

> **The one trap worth knowing up front.** `fastmcp` depends on a PyPI package
> called `mcp`, and this repository's folder is *also* called `mcp`. Whenever
> the repository root is on `sys.path`, `import mcp` resolves to the folder and
> fastmcp fails from inside its own internals. **Launch servers by path**
> (`python mcp/sqlserver_schema_server.py`), never `python -m mcp.<name>` from
> the repository root, and never put the repository root on `PYTHONPATH`.
> `sqlserver_schema_server.py` carries a startup check that detects the
> collision and names the fix.

Every MCP tool is also a plain Python function, callable with no MCP client at
all:

```python
from sqlserver_schema_mcp import mssql_describe_table
result = mssql_describe_table("dbo.Claim", database="OrdersDW")
```

---

## 5. Runnable Python tooling

### report-lineage (a pip package, not a skill)

Scans an SSRS/SSIS estate, builds a lineage graph from source tables through
ETL packages to reports, finds duplicate or overlapping reports, and answers
"does a report like this already exist?" from a natural-language description.
Zero dependency on any host application: copy it into another repository and
it works. See [skills/report-lineage/README.md](skills/report-lineage/README.md)
and [PORTING.md](skills/report-lineage/PORTING.md).

```bash
cd skills/report-lineage
pip install -e .                 # or: pip install -e ".[all]"

python -m reportlineage scan ./my-estate --out ./out --mermaid --html --csv
python -m reportlineage scan-git https://github.com/org/repo.git --branch main
python -m reportlineage scan-server https://myserver/reports \
    --user 'domain\svc-account' --password-env REPORTLINEAGE_PASSWORD
python -m reportlineage duplicates ./my-estate --out duplicates.json
python -m reportlineage search ./my-estate "monthly sales by region"
```

Prefer `--password-env` over `--password`: an environment variable does not
land in shell history or a process list.

### Per-skill CLIs

```bash
# rdl-generation: no install step, stdlib only
python skills/claude-skills/rdl-generation/scripts/validate_rdl.py your-report.rdl

# sql-server-schema: pip install pyodbc fastmcp sqlglot
python skills/claude-skills/sql-server-schema/scripts/cli.py drivers
python skills/claude-skills/sql-server-schema/scripts/cli.py test-connection --server YOURSERVER
python skills/claude-skills/sql-server-schema/scripts/cli.py digest --database YOURDB
python skills/claude-skills/sql-server-schema/scripts/cli.py generate <digest.json> --dry-run
python skills/claude-skills/sql-server-schema/scripts/cli.py example

# extract-engine: pip install polars pyodbc openpyxl click
extract load-config     --workbook <path> --feed <name> [--dry-run] [--allow-config-write]
extract validate-config --feed <name>
extract dry-run         --feed <name>
extract explain         --feed <name> --dataset <name> --mode sql [--out <path>]
extract deploy-views    --feed <name> [--i-understand-this-writes-to-the-database]
extract run             --feed <name> [--mode polars|sql] [--resume --run-id N]

# mapping-driven-loader-pipeline: pip install openpyxl
python skills/claude-skills/mapping-driven-loader-pipeline/scripts/mapping_reader.py \
    report --workbook <mapping.xlsx>

# api-mongodb-mapping: stdlib, plus pyyaml for .yaml specs
python skills/claude-skills/api-mongodb-mapping/scripts/openapi_reader.py report --spec <spec.yaml>
python skills/claude-skills/api-mongodb-mapping/scripts/json_sample_schema_reader.py report --samples <docs.json>
```

`extract-engine` also ships seed scripts that stand up a three-dataset sample
feed against LocalDB in one command. See
[extract-engine/SKILL.md](skills/claude-skills/extract-engine/SKILL.md)
("Quickstart: seed scripts + the sample workbook").

Three test suites run with no database at all:

```bash
python -m pytest skills/claude-skills/sql-server-schema/tests -q
python -m pytest skills/claude-skills/extract-engine/tests -q
python -m pytest skills/report-lineage/tests -q
```

---

## 6. Step-by-step: import into Devin

Devin has **no skill marketplace**. Skills are *vendored into the repository
Devin is working on*, at the one path both Devin cloud and Devin CLI scan:
`.agents/skills/<name>/SKILL.md`.

### 6.1 The single most important constraint

**Devin's skill loader reads `SKILL.md` only.** It does not automatically pull
in `references/*.md`, `examples/`, or `scripts/`. `SKILL.md` names those files
by path and Devin reads them with its normal file tools *when they are in its
workspace*. Two consequences:

1. Copy the **whole skill folder**, not just `SKILL.md`, whenever the skill has
   `references/` or `scripts/` it depends on, which is every skill here except
   `repo-architecture-blueprint`.
2. Vendor into the repository whose code is being worked on. If the API you
   want mapped lives in repo B, the skill folder goes into repo B.

### 6.2 Import a skill, step by step

**Step 1: pick the skill** from [section 3](#3-skill-catalog).

**Step 2: copy the full folder into your target repository.**

```bash
# from the root of the repo Devin will work on
mkdir -p .agents/skills

# a first-party skill
cp -r /path/to/SkillsMCP/skills/claude-skills/rdl-generation .agents/skills/rdl-generation

# a vendored Microsoft skill: these link to ../../common/, so bring that too
cp -r /path/to/SkillsMCP/skills/vendor/microsoft-fabric/powerbi-report-planning \
      .agents/skills/powerbi-report-planning
cp -r /path/to/SkillsMCP/skills/vendor/microsoft-fabric/common .agents/skills/common
```

PowerShell equivalent:

```powershell
New-Item -ItemType Directory -Force .agents\skills
Copy-Item -Recurse C:\path\to\SkillsMCP\skills\claude-skills\rdl-generation .agents\skills\rdl-generation
```

**Step 3: check the YAML frontmatter survived the copy.** `name`,
`description`, and `triggers` are what make Devin auto-select the skill. A
`SKILL.md` with mangled frontmatter is ignored or mis-selected.

```bash
head -n 5 .agents/skills/rdl-generation/SKILL.md
```

**Step 4: commit the path to the branch Devin is on.** Discovery happens from
the checked-out branch, so an uncommitted skill folder is invisible to Devin
cloud.

```bash
git add .agents/skills
git commit -m "Vendor rdl-generation skill for Devin"
git push
```

**Step 5: install the skill's prerequisites in Devin's environment.** See the
[prerequisite matrix](#9-prerequisite-matrix). For example:

```bash
pip install openpyxl pyodbc     # mapping-driven-loader-pipeline
pip install pyyaml              # api-mongodb-mapping, YAML specs only
npm i -g @microsoft/powerbi-report-authoring-cli@latest \
         @microsoft/powerbi-modeling-mcp@latest    # vendored Power BI skills, Node >= 20
```

**Step 6: invoke it.**

| Surface | How |
| --- | --- |
| Devin Chat / Desktop | `@skills:<skill-name>` |
| Devin CLI | `/<skill-name>` |
| Any | Describe the task; `description` and `triggers` are written for auto-selection |

**Step 7: verify the skill actually loaded.** Each skill has a behavioural
tell. `api-mongodb-mapping` must ask about the identity/key field and about
null-vs-missing before producing a document; if it doesn't, the skill wasn't
loaded. `repo-architecture-blueprint` must emit Mermaid diagrams. Where a skill
ships a `DEVIN-USAGE.md`, its "Expect to be asked things" section is the
checklist.

### 6.3 Shortcut: the three stubs already in this repo

[.agents/skills/](.agents/skills/) already carries committed discovery stubs
for three skills, so if Devin is working on **this** repository they are live
already:

- [api-mongodb-mapping](.agents/skills/api-mongodb-mapping/SKILL.md)
- [mapping-driven-loader-pipeline](.agents/skills/mapping-driven-loader-pipeline/SKILL.md)
- [repo-architecture-blueprint](.agents/skills/repo-architecture-blueprint/SKILL.md)

These are stubs. The canonical copy, with all `references/`, `scripts/`, and
`examples/`, stays under [skills/claude-skills/](skills/claude-skills/).

### 6.4 Option B: Devin Desktop beta skills

Confirm the menu path against your Desktop build.

1. Devin Desktop → **Settings → Skills (beta)**.
2. Add a skill, paste the contents of the chosen `SKILL.md`.
3. **Keep the YAML frontmatter intact.**
4. Because a pasted skill has no `references/` beside it, *also* point Desktop
   at this repository, and at the repository being worked on, if different. A
   pasted `SKILL.md` alone produces plausible-looking output that skips the
   skill's discovery rules and stop conditions.

### 6.5 Devin knowledge-base entries: a lighter alternative

If you want the guidance without vendoring a folder, six skills have condensed
Devin knowledge-base entries (trigger plus content, one concern per entry) in
[skills/ide-references/devin/](skills/ide-references/devin/):
[api-mongodb-mapping](skills/ide-references/devin/api-mongodb-mapping.knowledge.md),
[mapping-driven-loader-pipeline](skills/ide-references/devin/mapping-driven-loader-pipeline.knowledge.md),
[rdl-generation](skills/ide-references/devin/rdl-generation.knowledge.md),
[repo-architecture-blueprint](skills/ide-references/devin/repo-architecture-blueprint.knowledge.md),
[sql-server-schema](skills/ide-references/devin/sql-server-schema.knowledge.md),
[ssrs-report-creation](skills/ide-references/devin/ssrs-report-creation.knowledge.md).
Paste them into Devin's knowledge base. They are **derived, condensed views**:
no scripts, no examples, no reference docs. Use them for advice; vendor the
folder when you need the tooling.

### 6.6 Register an MCP server with Devin

Devin's skill loader **ignores** a plugin's `.mcp.json`. MCP servers are
registered separately, through Devin's own MCP configuration:

| Server | stdio command |
| --- | --- |
| rdl-generation | `python skills/claude-skills/rdl-generation/scripts/mcp_server.py` |
| sql-server-schema | `python skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py` |
| powerbi-modeling-mcp (Microsoft) | `npx -y @microsoft/powerbi-modeling-mcp@latest --start` |

For a local-only Power BI target, no `FABRIC_*` secrets are needed. Confirm the
exact configuration surface on your own Devin seat.

### 6.7 Devin caveats that will bite you

- **Devin runs on Linux, and Windows Integrated Auth does not exist there.**
  `sql-server-schema` and `extract-engine` both assume Windows auth to an
  on-premises SQL Server. Devin can *generate* a pipeline perfectly well, but
  *running* it against an on-prem server with Kerberos has to happen on a
  Windows host: yours, not Devin's. Decide this before you tell Devin to "run
  it", or you get a connection failure that looks like a code defect.
- **Skip the Power BI Desktop bridge** on headless Linux.
- **Pin the source commit** when you vendor, so you know exactly what you
  copied.
- **Uploading a spec, a workbook, or sample documents to a Devin session puts
  them on Devin's machine and in that session's history.** Clear that with
  whoever owns the data first. See [section 12](#12-data-safety-rules).

Fuller treatment, including a head-to-head comparison with the
custom-backend approach:
[docs/devin-windsurf-microsoft-skills-integration.md](docs/devin-windsurf-microsoft-skills-integration.md),
with the evidence log in
[docs/verify-devin-fabric-skills.md](docs/verify-devin-fabric-skills.md).

---

## 7. Step-by-step: import into other hosts

### Claude Code / Cowork

These read `SKILL.md` natively.

**Option A: point at this repository in place.** Open a session with this
repository in the workspace and name the skill; `skills/claude-skills/<name>/`
and `skills/vendor/microsoft-fabric/<name>/` are read directly.

**Option B: install into a project.** Copy the whole skill folder into the
consuming project:

```bash
mkdir -p .claude/skills
cp -r /path/to/SkillsMCP/skills/claude-skills/sql-server-schema .claude/skills/
```

**Option C: install for your user, across all projects.**

```bash
cp -r /path/to/SkillsMCP/skills/claude-skills/rdl-generation ~/.claude/skills/
```

MCP servers come from [.mcp.json](.mcp.json) when this repository is the
workspace. See [section 8](#8-step-by-step-wire-up-an-mcp-server).

### Windsurf / Cascade

Windsurf follows the same open Agent Skills standard.

1. Vendor into `.windsurf/skills/<name>/` **or** `.agents/skills/<name>/`;
   both are recognized.
2. Install the skill's prerequisites, exactly as in section 6, step 5.
3. Register MCP servers via Windsurf's MCP config (commonly
   `~/.codeium/windsurf/mcp_config.json`). Confirm the path and format against
   the Windsurf version in use.
4. Invoke by name in Cascade, or let it auto-select on the description.

### Cursor

Cursor does not read `SKILL.md`. Use the condensed rules file:

```bash
cp /path/to/SkillsMCP/skills/ide-references/cursor/.cursorrules ./
```

If you already have a `.cursorrules`, append rather than overwrite.

### GitHub Copilot (VS Code)

```bash
mkdir -p .github
cp /path/to/SkillsMCP/skills/ide-references/copilot/copilot-instructions.md \
   .github/copilot-instructions.md
```

For MCP in VS Code, copy [.vscode/mcp.json](.vscode/mcp.json); it prompts for
the server and database rather than hardcoding them.

### Any other tool, or a human reader

[skills/ide-references/vendor-neutral/](skills/ide-references/vendor-neutral/)
holds plain-markdown playbooks with no frontmatter:
[rdl-generation](skills/ide-references/vendor-neutral/rdl-generation-playbook.md),
[sql-server-schema](skills/ide-references/vendor-neutral/sql-server-schema-playbook.md),
[ssrs-report-creation](skills/ide-references/vendor-neutral/ssrs-report-creation-playbook.md).

> **These variants are derived views, not a second source of truth.** The full
> detail (every reference doc, working example, builder, validator, and MCP
> server) lives only under `skills/claude-skills/`. See
> [skills/ide-references/README.md](skills/ide-references/README.md).

---

## 8. Step-by-step: wire up an MCP server

### RDL generation: zero configuration

**Step 1: optional dependency.** `pip install fastmcp`, needed only to serve
over stdio; the tools are importable Python functions without it.

**Step 2: register it.** In your MCP client's config, point at the real server
by absolute path:

```json
{
  "mcpServers": {
    "rdl-generation": {
      "command": "python",
      "args": ["/absolute/path/to/skills/claude-skills/rdl-generation/scripts/mcp_server.py"]
    }
  }
}
```

**Step 3: smoke-test it.** `python mcp/rdl_generation_server.py` should start
without error. Then ask the agent for `get_json_spec_schema()`.

### SQL Server schema: needs a driver and a Kerberos ticket

**Step 1: install prerequisites.**

```bash
pip install pyodbc fastmcp sqlglot
# plus a Microsoft ODBC Driver for SQL Server (17 or 18)
```

**Step 2: prove the connection before involving any agent.**

```bash
python skills/claude-skills/sql-server-schema/scripts/cli.py drivers
python skills/claude-skills/sql-server-schema/scripts/cli.py test-connection --server YOURSERVER
```

An `auth_scheme` of `KERBEROS` or `NTLM` confirms Windows Integrated Auth
reached the server.

**Step 3: register it.**

```json
{
  "mcpServers": {
    "sql-server-schema": {
      "command": "python",
      "args": ["/absolute/path/to/skills/claude-skills/sql-server-schema/scripts/sqlserver_schema_mcp.py"],
      "env": {
        "SQLSERVER_MCP_SERVER": "YOURSERVER",
        "SQLSERVER_MCP_DATABASE": "YOURDB",
        "SQLSERVER_MCP_MAX_ROWS": "100"
      }
    }
  }
}
```

Environment variables: `SQLSERVER_MCP_SERVER`, `SQLSERVER_MCP_DATABASE`
(default `master`), `SQLSERVER_MCP_MAX_ROWS`, and `SQLSERVER_MCP_ENCRYPT=auto`
for the self-signed-certificate case ODBC Driver 18 rejects by default.

**The server authenticates as whoever launched it**, so it must run in your own
session. There is no password parameter anywhere in the API, and a build-time
assertion (`assert_no_credentials`) fails if one is ever added.

**Step 4, for VS Code:** [.vscode/mcp.json](.vscode/mcp.json) already does
this with `promptString` inputs, so the server and database are asked for at
startup instead of committed.

**Step 5, for this repository in Claude Code:** [.mcp.json](.mcp.json)
registers both standalone servers already; set `SQLSERVER_MCP_SERVER` in your
environment and restart the session.

Full client-setup reference:
[sql-server-schema/references/mcp-client-setup.md](skills/claude-skills/sql-server-schema/references/mcp-client-setup.md).

### The two reference-only servers

`report_studio_server.py` and `pbi_refine_server.py` import from a backend
`app.core.*` tree that is not in this repository. To make them run, follow the
keep-set in [docs/minimal-backend-keep-set.md](docs/minimal-backend-keep-set.md)
, the smallest slice of backend needed for the deterministic SSRS-to-PBIP
pipeline. Until then, treat them as documentation of the tool surface and the
design pattern.

---

## 9. Prerequisite matrix

| Component | Python | Pip packages | Other |
| --- | --- | --- | --- |
| rdl-generation | 3.9+ | none (`fastmcp` for stdio MCP only) | n/a |
| ssrs-report-creation | n/a | none | Documentation only |
| sql-server-schema | 3.9+ | `pyodbc`, `sqlglot`, `fastmcp` | Microsoft ODBC Driver 17/18; Windows Integrated Auth to the target |
| extract-engine | 3.10+ | `polars`, `pyodbc`, `openpyxl`, `click` | Reachable SQL Server (LocalDB or Docker); ODBC Driver 18; `EXTRACT_ENGINE_SERVER` / `EXTRACT_ENGINE_DATABASE` |
| mapping-driven-loader-pipeline | 3.10+ | `openpyxl` to generate; `pyodbc` to run | ODBC Driver 18, only to run the generated pipeline |
| api-mongodb-mapping | 3.9+ | `pyyaml`, only for `.yaml` / `.yml` specs | No database connection, no HTTP call |
| repo-architecture-blueprint | n/a | none | Reads files, writes one Markdown document |
| report-lineage | 3.9+ | `pip install -e .`; extras `sqlparse`, `server` | `requests` only for `scan-server` |
| Vendored Power BI skills | n/a | n/a | Node >= 20; `@microsoft/powerbi-report-authoring-cli`, `@microsoft/powerbi-modeling-mcp`; `az` CLI for the management skill |

`extract-engine` deliberately uses its own `EXTRACT_ENGINE_*` namespace,
separate from `sql-server-schema`'s `SQLSERVER_MCP_*`, so the two never bleed
into each other's configuration.

---

## 10. "I want to..." quick starts

| Goal | Start here |
| --- | --- |
| Generate an `.rdl` from a SELECT or a spec | [rdl-generation/SKILL.md](skills/claude-skills/rdl-generation/SKILL.md), then `scripts/validate_rdl.py` |
| Find out why Report Builder rejects my RDL | [validation-checklist.md](skills/claude-skills/rdl-generation/references/validation-checklist.md) |
| Get RDL expression syntax that round-trips cleanly | [expressions.md](skills/claude-skills/rdl-generation/references/expressions.md) |
| Build or deploy a paginated report end to end | [ssrs-report-creation/SKILL.md](skills/claude-skills/ssrs-report-creation/SKILL.md) |
| Decide paginated vs. Power BI | [migration-to-fabric.md](skills/claude-skills/ssrs-report-creation/references/migration-to-fabric.md) |
| Understand a SQL Server database I did not design | [sql-server-schema/SKILL.md](skills/claude-skills/sql-server-schema/SKILL.md), then `cli.py test-connection` then `digest` |
| Turn a database into a reusable per-database skill | `cli.py digest` then `cli.py generate --dry-run`; format in [skill-pack-format.md](skills/claude-skills/sql-server-schema/references/skill-pack-format.md) |
| Work out which tables an RDL actually reads | [rdl-to-tables.md](skills/claude-skills/sql-server-schema/references/rdl-to-tables.md) |
| Map a whole SSRS/SSIS estate and find duplicates | [report-lineage/README.md](skills/report-lineage/README.md) |
| Build a metadata-driven flat-file extract | [extract-engine/RUNBOOK.md](skills/claude-skills/extract-engine/RUNBOOK.md) |
| Resume an extract that died overnight | [checkpoint-and-resume.md](skills/claude-skills/extract-engine/references/checkpoint-and-resume.md) |
| Prove the Polars and SQL backends agree | [rule-catalog-and-conformance.md](skills/claude-skills/extract-engine/references/rule-catalog-and-conformance.md) |
| Show a DBA the view DDL before deploying it | `extract explain --mode sql --out <path>` |
| Turn a mapping spreadsheet into pipeline code | [mapping-driven-loader-pipeline/SKILL.md](skills/claude-skills/mapping-driven-loader-pipeline/SKILL.md) |
| Document an API against a MongoDB collection | [api-mongodb-mapping/SKILL.md](skills/claude-skills/api-mongodb-mapping/SKILL.md) |
| Migrate SSRS to Power BI with no custom backend | [docs/devin-windsurf-microsoft-skills-integration.md](docs/devin-windsurf-microsoft-skills-integration.md) |
| Migrate SSRS to Power BI deterministically, at scale | [docs/minimal-backend-keep-set.md](docs/minimal-backend-keep-set.md) |
| Document any repository's architecture | [repo-architecture-blueprint/SKILL.md](skills/claude-skills/repo-architecture-blueprint/SKILL.md) |
| Look up an exact function signature or CLI flag | [docs/COMPONENT-GUIDE.md](docs/COMPONENT-GUIDE.md) |

For a Power BI report end to end, walk the four vendored skills in order:
[planning](skills/vendor/microsoft-fabric/powerbi-report-planning/SKILL.md),
[design](skills/vendor/microsoft-fabric/powerbi-report-design/SKILL.md),
[authoring](skills/vendor/microsoft-fabric/powerbi-report-authoring/SKILL.md),
[management](skills/vendor/microsoft-fabric/powerbi-report-management/SKILL.md).

---

## 11. Documentation index

### Reference

| Document | Read it for |
| --- | --- |
| [docs/COMPONENT-GUIDE.md](docs/COMPONENT-GUIDE.md) | The detailed per-component index: every MCP server, every skill, the lineage package, and the vendored/IDE material, documented module by module and function by function against the actual source, with real discrepancies flagged. Go here for exact signatures, CLI flags, and file formats. |
| [docs/components/](docs/components/) | One file per component group: [mcp-servers](docs/components/mcp-servers.md), [rdl-generation](docs/components/rdl-generation.md), [sql-server-schema](docs/components/sql-server-schema.md), [ssrs-report-creation](docs/components/ssrs-report-creation.md), [report-lineage](docs/components/report-lineage.md), [vendor-and-ide-references](docs/components/vendor-and-ide-references.md) |
| [SkillsMCP_Architecture_Blueprint.md](SkillsMCP_Architecture_Blueprint.md) | This repository's own architecture: detected stack, pattern, Mermaid diagrams, layers, cross-cutting concerns, ADRs, and the blueprint for new development. Also the worked example of what `repo-architecture-blueprint` produces. |
| [skills/ASSESSMENT.md](skills/ASSESSMENT.md) | The capability assessment behind everything here: what existed, what was extracted, what was left alone, and the known limitations. |
| [mcp/README.md](mcp/README.md) | Tool-by-tool MCP reference, the folder-name collision, and the safety posture. |
| [skills/ide-references/README.md](skills/ide-references/README.md) | What each IDE variant is for, and the rule for keeping them in sync. |

### Background reading

| Document | Read it for |
| --- | --- |
| [docs/minimal-backend-keep-set.md](docs/minimal-backend-keep-set.md) | The smallest backend slice that runs deterministic SSRS-to-PBIP conversion on its own: what to keep, what to delete, and the exact commands. |
| [docs/devin-windsurf-microsoft-skills-integration.md](docs/devin-windsurf-microsoft-skills-integration.md) | The same migration with no custom backend at all: Devin or Windsurf plus Microsoft's Power BI/Fabric skills, and a head-to-head comparison of the two approaches. |
| [docs/verify-devin-fabric-skills.md](docs/verify-devin-fabric-skills.md) | The evidence log: what was actually tested, what is documented-but-unverified, and what still needs a real seat. |
| [docs/powerbi-mcp-engine.md](docs/powerbi-mcp-engine.md) | Standing up a Report Studio engine that uses a local Power BI Modeling MCP server for refinement. |
| [docs/extract-engine-mvp-prompt-v2-polars.md](docs/extract-engine-mvp-prompt-v2-polars.md) | The original design prompt behind `extract-engine`. Compare with its `SKILL.md`: two described features, a `benchmark` command and an MCP server, were never built. |

Read the first two together. They are two answers to the same question, and the
right choice depends on whether deterministic, repeatable conversion at scale
matters more than having zero custom code to maintain.

---

## 12. Data-safety rules

These are load-bearing, not boilerplate. This repository is shared.

**Never commit generated artifacts that name real systems.** Schema digests and
generated skill packs name internal servers, databases, tables, and columns.
The generator writes them **outside the repository** by default
(`%LOCALAPPDATA%`), and `resolve_artifact_path()` refuses `skills/`, `mcp/`,
and `docs/` outright, plus any in-repo path `git check-ignore` does not
confirm. That is a backstop, not a policy. See
[pii-and-public-repo-safety.md](skills/claude-skills/sql-server-schema/references/pii-and-public-repo-safety.md).

**Every committed example here is fictional.** The six-table `SalesDW` digest
and its [generated-pack/](skills/claude-skills/sql-server-schema/examples/generated-pack/)
are invented, kept as the reference output. So are the API and Mongo fixtures
and the sample mapping workbook.

**Sample documents from a real collection are real data by definition.** Read
field *definitions* from them; never carry a live value into a mapping
document.

**Never derive an IDE reference variant from a generated schema pack.** Those
variants describe the *static* skills only.

**Read-only is structural, not advisory.** `sql-server-schema` has no write
tool and no flag that unlocks one: every connection is `autocommit=False` and
unconditionally rolled back, every ad-hoc statement is classified first, and
only `mssql_run_query` returns rows, with sensitivity-matched columns masked to
a shape descriptor. In `extract-engine`, a write-statement allow-list is the
only thing permitted to touch the database, and `deploy-views` requires
`--i-understand-this-writes-to-the-database`.

**No cloud AI calls.** `report_studio_server.py` ships an explicit no-egress
guard (`ENFORCE_NO_EGRESS`) that refuses to start if a cloud AI API key is
present in the environment, for air-gapped installs. The skill-pack generator
is deterministic templating with no model call at all.

---

## 13. Adding a skill to this library

1. **Create the folder** at `skills/claude-skills/<kebab-case-name>/` with a
   `SKILL.md`, plus `references/`, `examples/`, `scripts/`, and `tests/` as
   needed.
2. **Write the frontmatter**: `name`, a `description` written for
   auto-selection (state when to use it *and when not to*, and name the
   neighbouring skill it should defer to), `allowed-tools`, and `triggers`.
   Copy the shape from an existing skill.
3. **Keep script module names globally unique.** Both MCP launchers insert a
   skill's `scripts/` directory onto `sys.path` and import by module name, so
   no two skills may ship a `mcp_server.py`: they would collide in
   `sys.modules` and one launcher would silently re-export the other's tools.
   That is why the SQL Server server is called `sqlserver_schema_mcp.py`.
4. **Use a distinct environment-variable namespace** if the skill needs
   configuration, the way `EXTRACT_ENGINE_*` and `SQLSERVER_MCP_*` stay apart.
5. **Add tests that run without a database**, using an injected connection and
   a fake, the way `tests/fakes.py` does in the two skills that have one.
6. **Add a `DEVIN-USAGE.md`** if setup is non-obvious, following the `TESTED` /
   `FILE` / `DOCS` / `VERIFY` tag convention.
7. **Add the discovery stub** at `.agents/skills/<name>/SKILL.md`.
8. **Add the condensed IDE variants** under `skills/ide-references/` if the
   skill should be usable outside a SKILL.md host. These are generated views:
   treat drift as a documentation bug and re-derive them when the source
   changes.
9. **Register it here**, in [section 3](#3-skill-catalog), and in
   [docs/COMPONENT-GUIDE.md](docs/COMPONENT-GUIDE.md).

---

## 14. Provenance and licensing

Everything under `skills/` was built in this repository **except**
[skills/vendor/microsoft-fabric/](skills/vendor/microsoft-fabric/), which is a
verbatim, MIT-licensed copy of four skills plus two shared files from
Microsoft's public
[microsoft/skills-for-fabric](https://github.com/microsoft/skills-for-fabric)
repository, fetched 2026-08-27 from `main`. The vendored text is byte-for-byte
what upstream publishes, same wording and same samples, with one adaptation:
relative link depth, since the folder layout here differs. License in
[skills/vendor/microsoft-fabric/LICENSE](skills/vendor/microsoft-fabric/LICENSE).

`repo-architecture-blueprint` is based on the community "Architecture Blueprint
Generator" prompt from `github/awesome-copilot`, restructured into this
repository's skill format and hardened with an explicit step order and a
mandatory-diagrams rule.

`mcp/report_studio_server.py` and `mcp/pbi_refine_server.py` are reference
copies from a source repository's own MCP folder, with a couple of identifier
names generalized. `mcp/rdl_generation_server.py` is a launcher written here.
`skills/claude-skills/sql-server-schema/` and its launcher were written here
and depend on no source-repository code. The files under `docs/` are
generalized from the source repository's background reading.

**Keeping vendored content in sync:** this is a snapshot, not a live link. Pin
the source commit when you vendor, and re-copy to pick up upstream changes.
Nothing here auto-updates.
