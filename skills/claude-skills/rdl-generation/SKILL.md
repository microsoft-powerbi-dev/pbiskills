---
name: RDL File Generation
description: >
  Use this skill when generating, writing, or emitting SSRS Report Definition
  Language (.rdl) XML from scratch or from a specification: as opposed to
  reading or migrating an existing report. Covers the RDL 2016 document skeleton,
  namespace and schema-version handling, DataSources and DataSets, Fields and
  rd:TypeName mapping, ReportParameters, Tablix construction (flat table, grouped
  matrix, totals), Chart construction, Subreports, page headers and footers,
  measurement units, expression syntax that round-trips cleanly, and a
  pre-flight validation checklist. Includes a dependency-free Python builder
  (scripts/rdl_builder.py), a validator (scripts/validate_rdl.py), an MCP
  server exposing both as tools (scripts/mcp_server.py), and five working
  .rdl examples. Example requests:
  "generate an RDL for this SELECT statement", "write a grouped matrix RDL with
  subtotals", "emit RDL XML for a parameterised stored-procedure report",
  "why does Report Builder reject my generated RDL".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
triggers:
  - generate rdl
  - create rdl
  - write rdl
  - emit rdl
  - rdl xml
  - build an rdl
  - rdl file
  - report definition language
  - rdl schema
  - rdl namespace
  - tablix xml
  - tablix hierarchy
  - reportparameter
  - dataset commandtext
  - rd:TypeName
  - rdl template
  - ssrs report xml
  - paginated report definition
  - rdl generator
  - rdl round trip
  - rdl mcp server
  - mcp for rdl
---

# RDL File Generation

Generate valid, tool-openable SSRS / Power BI Paginated `.rdl` documents.

This skill is about the **output** side. If you are reading an existing report,
scoring it, or tracing where its data comes from, use `skills/report-lineage/`
instead. If you are standing up a report end to end including the datasource,
deployment and subscription, start from the `ssrs-report-creation` skill and
come back here for the XML.

## When to use

- A specification, SQL query, or IR object needs to become an `.rdl` file.
- Generated RDL is rejected by Report Builder, Visual Studio, or the report
  server, and the cause is in the XML.
- You need to hand-write a fragment (a Tablix, a chart series, a parameter)
  and get the element order right the first time.

## The five rules that prevent most failures

1. **Element order is significant.** RDL is validated against an XSD that uses
   `xsd:sequence`, not `xsd:all`. `<DataSourceName>` must precede
   `<CommandType>`, which must precede `<CommandText>`. Getting the set of
   elements right but the order wrong produces an "unexpected element" error
   that names the *following* element, not the misplaced one.
2. **Read namespace-agnostically, write namespace-explicitly.** When parsing,
   navigate by local tag name so 2008 / 2010 / 2016 documents all work. When
   emitting, register the default namespace and the `rd` prefix before
   serialising, or every element comes out as `ns0:`.
3. **Every measurement carries a unit.** `<Width>3in</Width>`, not
   `<Width>3</Width>`. A unit-less value is not valid RDL; some tools tolerate
   it, the server does not.
4. **Emit expressions in the canonical forms.** `=Fields!Name.Value` and
   `=Sum(Fields!Amount.Value)`. These are the exact forms the repository's
   parser reads back (`rdl_parser._FIELD_RE`, `_AGG_RE`), so anything you emit
   this way survives a generate → parse round trip. Creative equivalents do not.
5. **`PageHeader` and `PageFooter` live inside `<Page>`.** In RDL 2016 they are
   children of `ReportSection/Page`, not of `Report`. This is the single most
   common structural error in hand-written 2016 RDL.

## Workflow

1. **Establish the target schema version.** Default to RDL 2016
   (`.../reporting/2016/01/reportdefinition`) unless the estate is pinned to an
   older server. See `references/namespaces-versions.md`.
2. **Resolve the data first.** You cannot emit a correct `<Fields>` block
   without knowing the column names and types the query returns. Run the query,
   read the DDL, or read an existing dataset: do not guess. Guessed field
   names produce a report that opens and renders `#Error` in every cell.
   `skills/claude-skills/sql-server-schema/` is how to satisfy this against a
   live on-premises database: it reads the real column names and types out of
   the catalog, and can generate a per-database reference pack so you do not
   have to reconnect every time.
3. **Build in order:** `DataSources` → `DataSets` → `ReportParameters` →
   `ReportSections/ReportSection/Body/ReportItems` → `Page`.
4. **Emit.** Either use `scripts/rdl_builder.py` (no third-party dependencies)
   or hand-write from `examples/`.
5. **Validate.** Run `scripts/validate_rdl.py <file>` and work through
   `references/validation-checklist.md` before handing the file over.

## Bundled assets

### `scripts/`

| Script | Use |
| --- | --- |
| `rdl_builder.py` | Dependency-free builder. `RdlBuilder(...).add_datasource(...).add_dataset(...).add_table(...).save(path)`. Also runnable as a CLI over a JSON spec. |
| `validate_rdl.py` | Structural validation: well-formedness, namespace, element order, dataset/field references, expression field references, unit-less measurements. Exit code 0 or 1. |
| `mcp_server.py` | An MCP server exposing `rdl_builder.py` and `validate_rdl.py` as tools (`build_rdl`, `validate_rdl_file`, `field_expression`, plus the reference docs and examples), for an agentic IDE to drive over stdio. See below. |

All three are standalone: copy any of them into any repository and they work
on a stock Python 3.9+ install.

### MCP server

`scripts/mcp_server.py` has no dependency on this repository's backend, only
on its two sibling scripts. It works with or without `fastmcp` installed:
without it, every tool is still a plain, directly callable Python function;
with it (`pip install fastmcp`), `python scripts/mcp_server.py` serves the
same tools over stdio for any MCP client.

Point an MCP client's config at the file directly, for example:

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

Tools: `get_json_spec_schema`, `build_rdl`, `validate_rdl_file`,
`field_expression`, `list_reference_topics`, `get_reference`,
`list_examples`, `get_example`. `build_rdl` takes the same JSON spec
`rdl_builder.py`'s CLI accepts and returns the write path plus validation
findings in one call, so an agent can generate and check an RDL file in a
single tool round trip instead of shelling out to two separate scripts.

### `references/`

| File | Covers |
| --- | --- |
| `rdl-structure.md` | The document skeleton, element order, required vs optional |
| `datasets-parameters.md` | DataSources, DataSets, Fields, rd:TypeName, QueryParameters, ReportParameters |
| `tablix-and-charts.md` | Tablix body/hierarchy model, grouping, totals, chart anatomy |
| `expressions.md` | VB.NET expression syntax, aggregates, scope, formatting, the round-trip-safe forms |
| `layout-units.md` | Measurement units, coordinate model, page setup, pagination |
| `namespaces-versions.md` | Schema versions, when to target which, migration between them |
| `validation-checklist.md` | Pre-flight checks derived from the repository's own compatibility rules |

### `examples/`

Five complete, validating `.rdl` files:

| File | Demonstrates |
| --- | --- |
| `01-minimal-table.rdl` | Smallest correct RDL 2016 document |
| `02-grouped-matrix.rdl` | Row + column groups, subtotals, grand total |
| `03-chart-report.rdl` | Column chart with category and series hierarchies |
| `04-parameterised-proc.rdl` | Stored procedure, multi-value parameter, cascading parameter |
| `05-header-footer-subreport.rdl` | Page header/footer, page numbers, subreport |

## Common failures and their causes

| Symptom | Cause |
| --- | --- |
| "The element 'Query' has invalid child element 'CommandText'" | `CommandType` emitted after `CommandText`, or `DataSourceName` missing |
| Every cell renders `#Error` | Field name in the expression does not match a `<Field Name="...">` |
| Report opens blank | `Body/Height` is `0in`, or the Tablix `Top` exceeds `Body/Height` |
| Elements serialise as `ns0:Report` | Default namespace not registered before writing |
| Server accepts, Report Builder rejects | 2016-only element in a document declaring the 2008 namespace |
| Blank page between every page | `Body/Width` + left margin + right margin exceeds `PageWidth` |
| `ElementTree.ParseError: not well-formed` on a server-downloaded file | Trailing `\x00` padding; strip it before parsing |

## Related material in this repository

- `backend/app/core/generator/rdl_generator.py`: the production IR → RDL 2016
  generator. The reference implementation for anything this skill describes.
- `backend/app/core/parser/rdl_parser.py` + `rdl_xml.py`: the reader side, and
  the definition of what "round-trips" means here.
- `backend/tests/fixtures/*.rdl`: three small, known-good fixtures.
- `.claude/skills/paginated-reports/examples/rdl-templates.md`: larger,
  production-shaped RDL examples including page-break-per-group invoices.
- `backend/app/core/paginated/validate.py`: the deeper validator this skill's
  checklist is distilled from.
