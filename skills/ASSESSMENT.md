# Repository capability assessment: SSRS / RDL generation and report-estate lineage

Date: 2026-08-24
Scope: the SSRS/SSIS-to-Power-BI backend this content was extracted from
Purpose: establish what already exists, what is reusable, and what the new
`skills/` folder adds on top. Nothing outside `skills/` was read-modified; this
document is an inventory, not a change log.

---

## 1. Executive summary

The repository is substantially further along than a first read of `CLAUDE.md`
suggests. Three things that the brief assumed were missing are in fact present
and tested:

| Assumed missing | Actually present |
| --- | --- |
| RDL generation | `backend/app/core/generator/rdl_generator.py`: 949 lines, `IRWorkbook -> RDL 2016`, round-trip tested against the parser (`backend/tests/test_rdl_roundtrip.py`) |
| Estate lineage | `backend/app/core/lineage/`: `EstateGraph` builder over RDL + DTSX + CONMGR + RSD + BIM + SQL, joined on a `database::schema.table` signature |
| Duplicate detection | `backend/app/core/complexity/duplicates.py` + `duplicate_fingerprint.py`: 8-dimension weighted similarity, inverted-index blocking, union-find clustering, keeper selection, reuse search |

There is also an existing Claude skill at `.claude/skills/paginated-reports/`
covering paginated-report authoring and RDL reference material, and two agent
definitions at `.claude/agents/` (`rdl-reviewer.md`,
`paginated-performance-advisor.md`) that are loaded **at runtime by Python
code**, not only by an IDE agent.

The real gap is therefore not capability but **packaging**. Everything above is
welded into a FastAPI application: it imports `app.*`, depends on
`shared.ir_models`, assumes a `JobState`, a DB session, and a repo-root
`PYTHONPATH`. None of it can be lifted into another repository as-is.

The `skills/` folder addresses exactly that gap in two ways:

1. **Skills and IDE references** that let a developer (or Devin, Copilot,
   Cursor, Claude Code) author correct RDL and SSRS reports without reading the
   backend source.
2. **`skills/report-lineage/`**, a self-contained, zero-backend-import Python
   package that re-implements the estate-scanning, lineage, duplicate-detection
   and search capability so it can be moved to another repository by copying
   one directory.

---

## 2. What exists today, by capability

### 2.1 RDL reading: `backend/app/core/parser/`

| File | Lines | Role |
| --- | --- | --- |
| `rdl_parser.py` | 755 | `parse_rdl(path, logger) -> IRWorkbook`. The single entry point. |
| `rdl_xml.py` | 129 | Namespace-agnostic ElementTree helpers + RDL size/type conversions. |
| `connect_string.py` | 36 | `parse_connect_string(connect) -> (server, database)`. |
| `sql_tables.py` | 369 | `extract_table_refs(sql, database) -> SqlRefs`. sqlglot-first, regex fallback. |
| `m_query.py` | 88 | Reads the M expression back apart. |
| `dtsx_parser.py` | 405 | `parse_dtsx(path, connections) -> SsisPackage`. |
| `bim_parser.py` | 542 | SSAS `.bim` / TMSL. |
| `sql_file_parser.py`, `sql_surface.py` | 156 / 230 | `.sql` DDL classification and construct detection. |

**The pattern that matters**: RDL 2008, 2010 and 2016 use different schema
namespaces, so the parser never uses namespaces at all. It navigates by *local
tag name*:

```python
def local_name(tag): return tag.split("}", 1)[1] if "}" in tag else tag
def child(elem, name): ...        # first direct child, case-insensitive
def descendants(elem, name): ...  # any depth
```

One consequence: `_report_section()` transparently handles the 2008 `Report/Body`
shape and the 2016 `Report/ReportSections/ReportSection/Body` shape with the same
code path.

RDL elements covered: `DataSources/ConnectionProperties/ConnectString`,
`DataSets/DataSet/Query` (`CommandText`, `CommandType`, `DataSourceName`,
`QueryParameters`), `DataSet/Fields/Field` (`rd:TypeName`, `Value` = calculated),
`ReportParameters/ReportParameter` (`DataType`, `DefaultValue`, `ValidValues`),
and the body `ReportItems` walked recursively: `Rectangle` (flattened),
`Textbox`, `Image`, plus `Tablix`, `Chart`, `Gauge`, `Map`, `Subreport`.

`sanitize_rdl_bytes()` strips the trailing `\x00` padding that SSRS Report
Server adds to definitions it serves. Without it, `ElementTree.parse` fails on
roughly every definition downloaded from a real server. This is one of those
details that costs an afternoon to rediscover.

### 2.2 RDL writing: `backend/app/core/generator/rdl_generator.py`

`generate_rdl(workbook: IRWorkbook, out_path: Path, logger=None, sample_data=False) -> Path`

Approach: **programmatic `xml.etree.ElementTree`, no templating engine.** There
is no Jinja anywhere in the repository. Namespaces are registered once at module
import:

```python
_NS = "http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition"
_RD = "http://schemas.microsoft.com/SQLServer/reporting/reportdesigner"
register_namespace("", _NS)
register_namespace("rd", _RD)
```

Emits `Report/ReportSections/ReportSection/{Body, Width, Page}`, with
`PageHeader` / `PageFooter` inside `<Page>` (a common authoring error is putting
them at `Report` level, where SSRS 2016 rejects them).

**The design constraint worth preserving:** the generator emits exactly the
expression forms the parser's `_FIELD_RE` and `_AGG_RE` read back
(`=Fields!X.Value`, `=Sum(Fields!Y.Value)`). Round-trip is therefore a property
of the pair, and `test_rdl_roundtrip.py` asserts it. Any new RDL emitter should
honour the same contract or the analysis side silently loses fidelity.

Supporting: `core/translator/dax_to_rdl.py` (135 lines,
`translate_dax_to_rdl`), `core/generator/sample_data_generator.py`,
`sql_sample_data.py`.

### 2.3 RDL-to-RDL transform: `backend/app/core/paginated/`

| File | Lines | Role |
| --- | --- | --- |
| `transformer.py` | 197 | `transform_paginated(rdl_path, out_path, target, ...)` |
| `compatibility.py` | 274 | `detect(report_el) -> List[Finding]` |
| `enrichers.py` | 276 | Additive, idempotent RDL improvements |
| `validate.py` | 445 | `validate_rdl(path) -> RdlValidation` for SSRS + Power BI targets |
| `pipeline.py` | 61 | Orchestration |

The module docstring states the governing decision explicitly: *paginated
reports **are** RDL, so conversion mutates the original tree in place rather
than round-tripping through the IR, which would be lossy.* The IR is the
analysis engine; it is not on the paginated output path.

**Namespace-preserving writes**: the counterpart to namespace-agnostic reads,
and directly reusable:

```python
for event, (prefix, uri) in ET.iterparse(sanitize_rdl_bytes(path), events=["start-ns"]):
    nsmap.setdefault(prefix, uri)
for prefix, uri in nsmap.items():
    ET.register_namespace(prefix, uri)
...
tree.write(out_path, encoding="utf-8", xml_declaration=True)
```

New elements inherit the namespace from their parent's tag rather than from a
hard-coded constant, so a 2008 input stays a 2008 output.

`compatibility.detect` also carries static-analysis rules lifted out of the
`.claude/agents` review agents: `select_star`, `leading_wildcard`, `cross_join`,
`nested_iif`, `chained_replace`, `integrated_security`, `many_tablix` (>5),
`many_chart` (>5). Those rules are worth reusing as an authoring checklist,
which is what `skills/claude-skills/rdl-generation/references/validation-checklist.md`
does.

### 2.4 Lineage: `backend/app/core/lineage/` + `shared/lineage_models.py`

`EstateGraph` is a Pydantic model with `meta`, `layers`, `nodes`, `edges`,
`diagnostics`, `inventory_by_kind`, `confidence`, `provenance`, `totals`, `kpis`.

Six layers: `0 source · 1 etl · 2 staging · 3 dw · 4 semantic · 5 report`.

Enums: `Confidence{high,medium,low,inferred}`,
`Provenance{parser,referenced,llm,sidecar}`,
`EdgeKind{derives_from,contains,references}`,
`Severity{blocker,warn,info,error}`. `NodeKind` is deliberately an *open* set of
string constants, not an enum, so new source types do not require a schema change.

**The whole linking mechanism is one function** (`signature.py`, 19 lines):

```python
def table_signature(database, schema, table) -> str:
    # "database::schema.table", case-folded, schema defaults to dbo
```

Because a table node's id derives from its signature, an SSIS destination table
and an RDL dataset source table collapse to the same node automatically, and
`source -> package -> table -> dataset -> report` forms with no explicit join
step. Schema defaulting to `dbo` is what makes an unqualified name on one side
match a `dbo.`-qualified name on the other.

`builder.py` (850 lines) exposes one extractor per source type, each returning
the same shape (`nodes, edges, diagnostics, root_id, table_ids`), so adding a
source type is additive. `nodes_from_rdl` optionally accepts a `ProcBodyLookup`
callable, which lets lineage trace *through* a stored procedure instead of
stopping at the `EXEC` frontier.

`runner.py` attaches four projections to the graph: `fabric_readiness`,
`wave_plan`, `duplicates`, `report_fingerprints`: each individually
`try`/`except`'d so a failing projection cannot abort a scan.

### 2.5 Duplicate detection: `backend/app/core/complexity/`

| File | Lines | Role |
| --- | --- | --- |
| `duplicate_fingerprint.py` | 242 | `ReportFingerprint`: 8 frozenset dimensions |
| `duplicates.py` | 660 | `compare`, `analyze_duplicates`, `find_reuse`, clustering |
| `duplicate_models.py` | 189 | Frozen, `extra="forbid"` result models |
| `duplicate_weights.py` + `data/duplicate_weights.json` | 100 | Judgments-as-data |
| `landscape.py` | 356 | Attribute-level redundancy, Jaccard subject areas |

Dimension weights: `sql .28, tables .24, columns .24, measures .10,
parameters .06, visuals .05, name .03`. Verdict thresholds:
`identical .95, near_duplicate .82, overlapping .62, related .45`.

Two design decisions worth carrying forward:

- **Renormalise over populated dimensions only.** A report whose SQL could not
  be resolved would otherwise score artificially low against an identical twin.
- **Inverted-index blocking.** Pairs are nominated only if they share a table, a
  measure, or `min_shared_columns` (3) columns, which keeps a whole-estate sweep
  near-linear rather than O(n²).

`find_reuse` answers "does the report I am about to build already exist?" from a
natural-language request, weighted `terms .45, tables .30, columns .25`.

### 2.6 Report Server connectivity: `backend/app/core/report_server/`

`rest_api.py` (210 lines) targets `/reports/api/v2.0`: `GET /System` to probe,
`GET /CatalogItems` fetched flat and filtered locally, definition download via
`GET /CatalogItems({id})/Content/$value` with a fallback to the base64 `Content`
property. `soap_api.py` (212 lines) covers `ReportService2010.asmx` for older
servers. `url_guard.py` (190 lines) is an SSRF guard on the configured base URL.

The comment on `list_catalog` explains why the catalog is fetched flat: OData
`$filter` support drifts between SSRS 2017 / 2019 / 2022 and PBIRS, whereas the
flat collection is stable everywhere, and an estate is small enough that one
round-trip beats N.

### 2.7 Report study / analysis

- `core/complexity/`: parse-only sweep scoring migration effort. Weights live
  in `data/complexity_weights.json` with ordered thresholds mapping a score to
  Low / Medium / High / Very High, and `effort.py` maps a level to a day range.
- `core/analysis.py::analyze_ir_dict`: `complexity`, `overview`, `data_volume`,
  `data_sources`.
- `core/report_studio/`: prompt-to-report. `grounding.py` (559 lines) builds a
  bounded markdown context per source; `schema.py` (418 lines) builds a
  `GroundedSchema` oracle and reconciles LLM output against it with `difflib`,
  which is what stops a model inventing column names.
- `core/report_studio/rdl_review.py`: loads `.claude/agents/rdl-reviewer.md`
  from disk as the system instruction. Markdown-on-disk as a prompt asset is an
  established pattern here, which is why the new skills follow it.

### 2.8 Existing skill and agent assets

- `.claude/skills/paginated-reports/`: `SKILL.md` (YAML frontmatter: `name`,
  `description`, `allowed-tools`, ~40 `triggers`), `examples/` (4 files
  including `rdl-templates.md`, the closest thing to a template library in the
  repo), `references/` (8 files).
- `.claude/agents/rdl-reviewer.md`, `.claude/agents/paginated-performance-advisor.md`.
- `prompts/`: 6 markdown prompt templates with `{{PLACEHOLDER}}` tokens.
  `sophia-ir.md` explicitly notes that its JSON output feeds *both* the PBIP
  generator and the RDL generator.

### 2.9 Test and fixture assets

`backend/tests/` holds 68 test files. Directly relevant: `test_rdl_parser.py`,
`test_rdl_roundtrip.py`, `test_rdl_validate.py`, `test_paginated*.py` (3),
`test_lineage.py`, `test_duplicates.py`, `test_sql_tables.py`,
`test_dtsx_parser.py`.

Fixtures: `backend/tests/fixtures/{sales_by_region,city_staging_report,report_with_subreport}.rdl`.
Wider samples: `samples/` (8 `.rdl`, `DailyETLMain.dtsx`, 2 `.conmgr`,
`PolicyDW.rds`), `docs/samples/report-studio-rdl/`,
`tools/ssrs-lab/reports/WWI-New/`.

---

## 3. Reusability analysis

### 3.1 Directly extractable: pure functions, few or no dependencies

| Asset | Dependency | Extraction cost |
| --- | --- | --- |
| `parser/connect_string.py` | none | copy verbatim |
| `lineage/signature.py` | `sql_tables.TableRef` | copy with its one dependency |
| `parser/rdl_xml.py` helpers | `shared.ir_models.DataType` for one function | copy, replace the enum |
| `parser/sql_tables.py` | optional `sqlglot` | copy verbatim |
| `complexity/duplicate_fingerprint.py` | `sql_tables`, IR dict shape | copy, re-target the input shape |
| `complexity/duplicates.py` scoring | fingerprint + weights JSON | copy, drop the `app.utils.logging` import |
| `data/duplicate_weights.json` | none | copy verbatim |
| `lineage/inventory.py` | `lineage_models` | copy |
| `shared/lineage_models.py` | pydantic | copy |

### 3.2 Extractable with rework

| Asset | Obstacle |
| --- | --- |
| `parser/rdl_parser.py` | Produces `IRWorkbook`, a 456-line Pydantic model built for PBIP generation. For lineage, only datasets / connections / parameters / visual counts are needed, so a narrower parse is cheaper than dragging the IR along. |
| `parser/dtsx_parser.py` | Self-contained apart from `connect_string` and `sql_tables`; the `SsisDiagnostic` type couples loosely to the job logger. |
| `lineage/builder.py` | 850 lines, imports six parsers and the shared models. Straightforward but bulky. |
| `report_server/rest_api.py` | Takes an injected `session`, so transport is already decoupled; needs `models.CatalogItem` and the auth/url-guard modules for a working client. |

### 3.3 Not extractable, and should not be

The FastAPI routers, `JobState` / `AnalysisJobState`, the DB layer, Fernet
settings encryption, the React frontend, the PBIP generation chain, and the LLM
engines. These are the product, not the library.

### 3.4 What was actually done

Per the agreed approach (**extract copies, zero backend imports**), the new
`skills/report-lineage/` package re-implements the §3.1 and §3.2 assets as a
standalone library. It duplicates logic deliberately. The tradeoff is explicit:
portability is bought with the risk of drift, and `PORTING.md` records which
backend file each module was derived from so the two can be diffed later.

---

## 4. Gap analysis: what the new folder adds

| Gap | Addressed by |
| --- | --- |
| No authoring guidance for writing RDL from scratch outside Claude Code | `skills/claude-skills/rdl-generation/`: SKILL.md, 7 reference docs, 5 working `.rdl` examples, a dependency-free builder script |
| No end-to-end SSRS report-creation workflow (datasource -> dataset -> layout -> deploy) | `skills/claude-skills/ssrs-report-creation/` |
| Existing knowledge is Claude-Code-shaped only | `skills/ide-references/`: vendor-neutral playbooks, Devin knowledge entries, `.cursorrules`, `copilot-instructions.md` |
| Lineage / duplicates / search cannot leave this repo | `skills/report-lineage/`: installable package, CLI, tests, porting guide |
| No SSRS Report Server *discovery* path that works standalone | `reportlineage/scanners/report_server.py`: REST v2.0, catalog listing plus definition download |
| No searchable index over an estate | `reportlineage/search.py`: inverted index with term / table / column scoring |
| No way to learn the shape of the databases the RDL datasets actually query (added 2026-09-08) | `skills/claude-skills/sql-server-schema/`: Windows-auth connection over pyodbc, the `sys.*` catalog reads, a portable schema digest, and a deterministic skill-pack generator, exposed as an MCP server and a CLI |
| Lineage stops at the `EXEC` frontier for stored-procedure-backed datasets (added 2026-09-08) | The same skill's `mssql_get_definition` supplies the `proc_body_lookup` that `reportlineage.builder.build_estate` has always accepted and nothing ever provided |

---

## 5. Risks and known limitations

1. **Duplication drift.** `skills/report-lineage/` copies logic from the
   backend. If `duplicate_weights.json` or `sql_tables.py` changes in
   `backend/`, the copy does not follow. `PORTING.md` lists the provenance of
   every module; treat it as the diff manifest.
2. **T-SQL extraction is heuristic.** `sqlglot` improves it materially but is an
   optional dependency; without it the regex path is used. Dynamic SQL
   (`EXEC(@sql)`, `sp_executesql`) is flagged, never guessed.
3. **REST v2.0 only.** Per the agreed scope, the portable scanner targets SSRS
   2017+ and PBIRS. SSRS 2008 R2 through 2016 need the SOAP endpoint; the
   scanner raises a clear error rather than failing obscurely, and
   `backend/app/core/report_server/soap_api.py` remains the reference
   implementation if SOAP support is added later.
4. **Duplicate thresholds are calibrated for SSRS estates.** The weights are
   judgments, versioned as data. Recalibrate on a labelled sample before
   trusting `retire` recommendations in a new estate.
5. **No credentials are stored.** The scanner reads them from environment
   variables or explicit arguments only.
6. **Generated schema packs are customer data** (added 2026-09-08). A pack
   produced by `sql-server-schema` names internal servers, databases, tables
   and columns. It is written outside version control by default, the generator
   refuses to write into `skills/`, `mcp/` or `docs/` at all and refuses any
   other in-repository path that `git check-ignore` does not confirm, and every
   pack carries a self-ignoring `.gitignore` plus a do-not-commit header. The
   residual risk is a human copying one in by hand.
7. **Schema classification is heuristic** (added 2026-09-08). Fact, dimension,
   bridge and lookup roles, and the importance score behind the tiering, are
   name, key and cardinality rules rather than semantics.
   `references/schema-digest-format.md` documents every rule and each
   classified table carries the reasons that fired, so a wrong call is visible
   rather than silent. Recalibrate `max_tier1_tables` per estate.
8. **Windows Integrated Auth constrains where the server can run** (added
   2026-09-08). It authenticates as whoever launched it, so it needs a Windows
   session holding a Kerberos ticket for the domain account. A Linux-hosted or
   service-account-hosted agent cannot use it against an on-premises server.

---

## 6. Vendored Power BI report skills

`skills/vendor/microsoft-fabric/` holds four skills copied verbatim (MIT
license) from Microsoft's public `microsoft/skills-for-fabric` repository:
`powerbi-report-planning`, `powerbi-report-design`, `powerbi-report-authoring`,
and `powerbi-report-management`. These start where this repository's own
skills stop: once a report is (or is becoming) a Power BI `.pbip`/PBIR
project, these cover designing, authoring, and publishing it. See
`skills/vendor/microsoft-fabric/README.md` for the full provenance note,
scope rationale, and the one relative-link adaptation made to fit the
vendored layout.

## 6a. SQL Server schema analysis (added 2026-09-08)

`skills/claude-skills/sql-server-schema/` closes the one gap every other skill
here shares: everything else works on files, and nothing could reach the
databases those files describe.

Design decisions worth recording:

- **The digest is the seam.** The live server writes a `schema_digest.json`;
  the generator reads one and never opens a connection. That makes the digest
  reviewable before any documentation is produced, makes the generator
  testable against a fixture, makes a pack regenerable on a machine with no
  ODBC driver, and makes two digests of the same database diff cleanly as a
  schema-drift detector.
- **`skillgen.py`, `analyze.py` and `guardrails.py` are standard library only**
  and import no driver. Only `connection.py` and `catalog.py` need `pyodbc`.
- **No LLM call anywhere.** The generator is deterministic templating over
  sorted collections, consistent with the `ENFORCE_NO_EGRESS` posture of
  `mcp/report_studio_server.py`. Given the same digest, byte-identical output.
- **The join key is reused, not reinvented.** `analyze.table_signature`
  produces the same `database::schema.table` string as
  `reportlineage.sql_refs.TableRef.signature()`, and a test imports the real
  class to assert they agree, so lineage and schema knowledge join with no
  mapping layer.
- **Read-only is structural, not configured.** There is no write tool to
  disable and no flag to leave off.
- **The build-versus-adopt reasoning is written down** in that skill's
  `references/existing-tools-and-alternatives.md`, covering Microsoft's Data
  API builder based SQL MCP Server and the community MSSQL MCP servers, so the
  next person does not repeat the evaluation.

## 7. Where to start reading

- New to the estate problem: `skills/report-lineage/README.md`, then run
  `python -m reportlineage scan <folder>`.
- Understanding the database behind a report:
  `skills/claude-skills/sql-server-schema/SKILL.md`, then run
  `scripts/cli.py test-connection`.
- Seeing what a generated schema pack looks like without touching a real
  database: `skills/claude-skills/sql-server-schema/examples/generated-pack/`,
  built from the fictional digest beside it.
- Authoring an RDL by hand or with an agent:
  `skills/claude-skills/rdl-generation/SKILL.md`.
- Standing up a new SSRS report end to end:
  `skills/claude-skills/ssrs-report-creation/SKILL.md`.
- Moving lineage into another repository: `skills/report-lineage/PORTING.md`.
- Designing, authoring, or publishing a Power BI report once a migration
  target is a `.pbip`: `skills/vendor/microsoft-fabric/README.md`.
